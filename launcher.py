"""
飞书自动回复助手 · 图形启动器
==============================
面向不熟悉命令行的使用者：
  - 首次运行：弹出配置向导，填写飞书地址 / 会话名 / API Key
  - 之后运行：一个主控窗口，点按钮即可启动、停止监控，查看日志

打包后作为 exe 入口（见 build.spec）。
"""
import json
import os
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
import traceback
import urllib.request
from pathlib import Path
from tkinter import filedialog, messagebox, scrolledtext, ttk

import yaml

from runtime import APP_DIR, DATA_DIR, IS_FROZEN, config_path

APP_TITLE = "飞书自动回复助手"
CONFIG_FILE = DATA_DIR / "config.yaml"
EXAMPLE_FILE = APP_DIR / "config.example.yaml"
PID_FILE = DATA_DIR / ".launcher.pid"
LIVE_LOG = DATA_DIR / "live.log"   # 打包模式下子进程的实时输出
ERROR_LOG = DATA_DIR / "launcher_error.log"

# ============================================================
# 内嵌默认配置（最终兜底）
# ------------------------------------------------------------
# 首次配置时需要一份"完整"的配置基底（含 system_prompt、轮询、
# 上下文等全部默认项），否则写出的 config.yaml 会缺键，主程序行为
# 异常。正常情况下读外部的 config.example.yaml；一旦它丢失
# （例如打包时被漏掉），这里保证仍能写出完整可用的配置。
# 修改默认值时，请与 config.example.yaml 保持同步。
# ============================================================
EMBEDDED_EXAMPLE = """
feishu:
  base_url: "https://YOUR_TENANT.feishu.cn/next/messenger/"
  target_chat_name: "AI教学平台"

browser:
  channel: "msedge"
  user_data_dir: "./.browser_data"
  headless: false
  slow_mo: 200
  viewport_width: 1440
  viewport_height: 900

polling:
  interval: 5
  jitter: 2

context:
  context_size: 30
  max_chars: 3000

reply_strategy:
  latest_only: true

llm:
  provider: "siliconflow"
  base_url: "https://api.siliconflow.cn/v1"
  api_key: "sk-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
  model: "Qwen/Qwen3.5-35B-A3B"
  enable_thinking: false
  system_prompt: |
    你是答题助手，替学生回复「AI教学平台」教学机器人。

    我会把我和机器人的最近对话粘贴给你。请根据对话，判断学生下一条应该发送什么。

    【判断规则】
    - 对话中最后一条是机器人消息。要根据它判断该回什么。
    - 「AI 自适应问答」+ 题目（可能带 A/B/C/D 选项）→ 这是正式题目，给出答案。
    - 「学习反馈」+「答对了」→ 说明答对了，按其中的新要求作答（如"说说...""解释..."）。
    - 「学习反馈」+「答错了」+ 追问 → 针对追问给出正确答案，不要重复老答案。
    - 「学习提醒」+「你已经XX小时没有答题」→ 这是催答题的提醒，
      里面的"直接回复课程名（如智能合约）"只是示例，**不要回复课程名**，
      应回复当前正在学习的课程名以继续答题（如正在学习智能合约技术就回"智能合约技术"）。

    【代码题】（题目要求"编写代码""写一段代码""实现某逻辑"等）
    - 目的只是过评分，不追求写出可运行、完整、规范的工程代码。
    - 代码里一行注释都不要写。
    - 省略一切非必要的东西：不要异常处理、不要参数校验、不要日志、
      不要拆辅助函数、不要设计模式、不要 main 示例；变量名简短直白，
      能看懂就行。写得像学生随手交的作业，而不是正式项目代码或教程。
    - 语言用题目或上下文里出现的那门语言，不要引入题目没提到的库。
    - 代码块后面必须紧跟一句话，说明关键几行的作用
      （机器人明确要求"说明关键行的作用"，缺了会被判无效作答）；
      不要逐行讲解，不要写成小作文。
    - 篇幅不设硬限制，以"刚好把题目要求交代清楚"为准，能短不长。
      格式示例（仅示范精简程度，代码内容不要照搬）：
      ```solidity
      function transfer(address to, uint amount) public {
          require(balances[msg.sender] >= amount);
          balances[msg.sender] -= amount;
          balances[to] += amount;
      }
      ```
      require 校验余额是否够，后两行分别扣减付款方、增加收款方。

    【输出格式】
    - 选择题：只输出选项字母（如 C）
    - 判断题：输出「正确」或「错误」
    - 简答题：用一两句话准确作答
    - 代码题：按上面【代码题】规则，极简代码块 + 一句话说明
    - 只输出要发送的内容本身，不要解释、不要客套、不要复述题目。
  max_tokens: 1000
  temperature: 0.3
  timeout: 45
  max_retries: 3

reply:
  send_delay_min: 2
  send_delay_max: 6
  dedup: true
"""


# ============================================================
# 全局异常兜底
# ------------------------------------------------------------
# 窗口程序（console=False）没有可用的 stderr，Tk 回调里抛出的异常
# 会被完全吞掉 —— 用户看到的现象就是"点了按钮毫无反应"。
# 这里接管回调异常：写入 launcher_error.log 并弹窗提示。
# ============================================================
def install_exception_hook(root):
    def _hook(exc, val, tb):
        text = "".join(traceback.format_exception(exc, val, tb))
        try:
            with open(ERROR_LOG, "a", encoding="utf-8") as f:
                f.write(f"\n{'=' * 60}\n[{time.strftime('%Y-%m-%d %H:%M:%S')}]\n{text}")
        except Exception:
            pass
        try:
            messagebox.showerror(
                "程序内部错误",
                f"操作未能完成，发生了一个内部错误：\n\n{type(val).__name__}: {val}\n\n"
                f"详细信息已写入 launcher_error.log。",
                parent=root,
            )
        except Exception:
            pass

    root.report_callback_exception = _hook


def load_base_config():
    """读取配置模板作为基底。

    顺序：APP_DIR → DATA_DIR 的 config.example.yaml → 内嵌兜底。
    任何一步出错都静默降级，绝不抛异常（这是首次配置能否成功的关键）。
    返回 dict（可能为空，由调用方补齐）。
    """
    for p in (EXAMPLE_FILE, APP_DIR / "config.example.yaml", DATA_DIR / "config.example.yaml"):
        try:
            if p.exists():
                data = yaml.safe_load(p.read_text(encoding="utf-8"))
                if isinstance(data, dict) and data:
                    return data
        except Exception:
            continue

    # 最终兜底：内嵌配置
    try:
        data = yaml.safe_load(EMBEDDED_EXAMPLE)
        if isinstance(data, dict) and data:
            return data
    except Exception:
        pass
    return {}


# ============================================================
# 工具函数
# ============================================================
def _base_python():
    """返回用于启动子进程的 Python 可执行文件与参数前缀。"""
    if IS_FROZEN:
        # 打包后：直接用自身 exe 的 --run-sub 模式跑子进程
        return [sys.executable]
    return [sys.executable]


def _script_path(name):
    """定位脚本文件（打包后资源在 APP_DIR，源码运行也在 APP_DIR）。"""
    p = APP_DIR / name
    if p.exists():
        return p
    p2 = DATA_DIR / name
    if p2.exists():
        return p2
    return p


def _profile_dir():
    """浏览器用户数据目录（与 config.yaml 的 browser.user_data_dir 保持一致）。"""
    rel = "./.browser_data"
    try:
        if CONFIG_FILE.exists():
            cfg = yaml.safe_load(CONFIG_FILE.read_text(encoding="utf-8")) or {}
            rel = (cfg.get("browser") or {}).get("user_data_dir") or rel
    except Exception:
        pass
    try:
        return (DATA_DIR / rel).resolve()
    except Exception:
        return DATA_DIR / ".browser_data"


def _clean_profile_locks():
    """清理 Chromium 残留的单实例锁文件。

    程序被强杀后这些锁会让下次启动的浏览器认为"已有实例在运行"而立即退出，
    playwright 报 TargetClosedError。
    """
    ud = _profile_dir()
    removed = []
    for name in ("SingletonLock", "SingletonCookie", "SingletonSocket", "lockfile"):
        try:
            for f in ud.glob(name):
                try:
                    f.unlink()
                    removed.append(f.name)
                except Exception:
                    pass
        except Exception:
            pass
    return removed


def _pid_alive(pid, expect_name=None):
    try:
        out = subprocess.run(["tasklist", "/FI", f"PID eq {int(pid)}"],
                             capture_output=True, timeout=15,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        txt = out.stdout.decode("gbk", "replace")
        if str(int(pid)) not in txt:
            return False
        if expect_name:
            return expect_name.lower() in txt.lower()
        return True
    except Exception:
        return False


def _acquire_single_instance():
    """确保同一时间只开一个程序窗口。

    两个窗口同时点「启动监控」会争抢同一个 .browser_data，
    后启动的那个必然报 TargetClosedError。
    返回已在运行的进程号；没有则返回 None 并写入自己的 PID。
    """
    me = os.getpid()
    try:
        if PID_FILE.exists():
            try:
                old = int(PID_FILE.read_text(encoding="utf-8").strip())
            except Exception:
                old = 0
            if old and old != me and _pid_alive(old, Path(sys.executable).name):
                return old
    except Exception:
        pass
    try:
        PID_FILE.write_text(str(me), encoding="utf-8")
    except Exception:
        pass
    return None


def _release_single_instance():
    try:
        if PID_FILE.exists() and PID_FILE.read_text(encoding="utf-8").strip() == str(os.getpid()):
            PID_FILE.unlink()
    except Exception:
        pass


# ============================================================
# 配置向导（首次运行）
# ============================================================
class ConfigWizard(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"{APP_TITLE} · 首次配置")
        self.geometry("640x620")
        self.resizable(False, False)
        self.result = False
        install_exception_hook(self)
        self._build()
        # 置顶显示，避免对话框被其它窗口盖住
        self.lift()
        self.attributes("-topmost", True)
        self.after(600, lambda: self.attributes("-topmost", False))

    def _build(self):
        pad = {"padx": 18, "pady": 8}

        header = tk.Frame(self, bg="#1f6feb", height=76)
        header.pack(fill="x")
        tk.Label(
            header, text="欢迎使用 · 先完成 3 项配置",
            bg="#1f6feb", fg="white",
            font=("Microsoft YaHei UI", 15, "bold"),
        ).pack(side="left", padx=20, pady=20)
        tk.Label(
            header, text="配置会保存到程序目录，之后可直接双击启动",
            bg="#1f6feb", fg="#cfe2ff",
            font=("Microsoft YaHei UI", 9),
        ).pack(side="left", pady=24)

        body = tk.Frame(self)
        body.pack(fill="both", expand=True, padx=6, pady=6)

        # --- 1. 飞书地址 ---
        tk.Label(body, text="① 飞书网页版地址", font=("Microsoft YaHei UI", 10, "bold")).pack(anchor="w", **pad)
        tk.Label(
            body, text="打开飞书网页版，从浏览器地址栏复制完整地址",
            fg="#666", font=("Microsoft YaHei UI", 9),
        ).pack(anchor="w", padx=18)
        self.e_url = tk.Entry(body, font=("Consolas", 10))
        self.e_url.pack(fill="x", padx=18, pady=(4, 0))
        self.e_url.insert(0, "https://你的租户.feishu.cn/next/messenger/")

        # --- 2. 会话名 ---
        tk.Label(body, text="② 机器人的会话名称", font=("Microsoft YaHei UI", 10, "bold")).pack(anchor="w", **pad)
        tk.Label(
            body, text="飞书左侧会话列表里显示的机器人名字（例如：AI教学平台）",
            fg="#666", font=("Microsoft YaHei UI", 9),
        ).pack(anchor="w", padx=18)
        self.e_chat = tk.Entry(body, font=("Microsoft YaHei UI", 10))
        self.e_chat.pack(fill="x", padx=18, pady=(4, 0))
        self.e_chat.insert(0, "AI教学平台")

        # --- 3. API Key ---
        tk.Label(body, text="③ 大模型 API Key", font=("Microsoft YaHei UI", 10, "bold")).pack(anchor="w", **pad)
        tk.Label(
            body, text="用于生成答案的模型密钥（硅基流动 / DeepSeek 等均兼容）",
            fg="#666", font=("Microsoft YaHei UI", 9),
        ).pack(anchor="w", padx=18)
        self.e_key = tk.Entry(body, font=("Consolas", 10), show="•")
        self.e_key.pack(fill="x", padx=18, pady=(4, 0))

        # 接口与模型（高级，默认折叠）
        adv = tk.Frame(body)
        adv.pack(fill="x", padx=18, pady=(10, 0))
        self.adv_open = False
        self.btn_adv = tk.Button(
            adv, text="▶ 高级设置（接口地址 / 模型名）", relief="flat",
            fg="#1f6feb", cursor="hand2", font=("Microsoft YaHei UI", 9),
            command=self._toggle_adv,
        )
        self.btn_adv.pack(anchor="w")

        self.adv_frame = tk.Frame(body)
        tk.Label(self.adv_frame, text="接口地址", font=("Microsoft YaHei UI", 9)).pack(anchor="w", pady=(6, 0))
        self.e_base = tk.Entry(self.adv_frame, font=("Consolas", 10))
        self.e_base.pack(fill="x")
        self.e_base.insert(0, "https://api.siliconflow.cn/v1")
        tk.Label(self.adv_frame, text="模型名", font=("Microsoft YaHei UI", 9)).pack(anchor="w", pady=(6, 0))
        self.e_model = tk.Entry(self.adv_frame, font=("Consolas", 10))
        self.e_model.pack(fill="x")
        self.e_model.insert(0, "Qwen/Qwen3.5-35B-A3B")

        # --- 底部按钮 ---
        foot = tk.Frame(self)
        foot.pack(fill="x", padx=18, pady=14)

        self.lbl_status = tk.Label(foot, text="", fg="#666", font=("Microsoft YaHei UI", 9))
        self.lbl_status.pack(side="left")

        self.btn_test = tk.Button(foot, text="测试连通性", width=11, command=self._test)
        self.btn_test.pack(side="right", padx=(6, 0))
        self.btn_save = tk.Button(
            foot, text="保存并开始", width=12, bg="#1f6feb", fg="white",
            activebackground="#1a5fd0", activeforeground="white",
            font=("Microsoft YaHei UI", 10, "bold"), command=self._save,
        )
        self.btn_save.pack(side="right")

    def _toggle_adv(self):
        if self.adv_open:
            self.adv_frame.pack_forget()
            self.btn_adv.config(text="▶ 高级设置（接口地址 / 模型名）")
        else:
            self.adv_frame.pack(fill="x", padx=18, pady=(0, 6))
            self.btn_adv.config(text="▼ 高级设置（接口地址 / 模型名）")
        self.adv_open = not self.adv_open

    def _collect(self):
        return {
            "url": self.e_url.get().strip(),
            "chat": self.e_chat.get().strip(),
            "key": self.e_key.get().strip(),
            "base": self.e_base.get().strip(),
            "model": self.e_model.get().strip(),
        }

    def _validate(self, d):
        if not d["url"] or "feishu.cn" not in d["url"]:
            return "飞书地址看起来不对，应形如 https://xxx.feishu.cn/next/messenger/"
        if not d["url"].startswith("http"):
            return "飞书地址需要以 http:// 或 https:// 开头"
        if not d["chat"]:
            return "请填写机器人会话名称"
        if not d["key"] or len(d["key"]) < 12:
            return "请填写有效的大模型 API Key"
        if not d["base"].startswith("http"):
            return "接口地址需要以 http 开头"
        return None

    def _test(self):
        d = self._collect()
        err = self._validate(d)
        if err:
            messagebox.showwarning("请检查", err, parent=self)
            return

        self.btn_test.config(state="disabled", text="测试中…")
        self.lbl_status.config(text="正在连接模型接口…", fg="#666")

        def work():
            msg, color = "❌ 未知错误", "#c0392b"
            try:
                url = d["base"].rstrip("/") + "/models"
                req = urllib.request.Request(
                    url, headers={"Authorization": "Bearer " + d["key"]}
                )
                with urllib.request.urlopen(req, timeout=15) as r:
                    ok = r.status == 200
                msg = "✅ 模型接口连通，密钥有效" if ok else f"⚠️ 返回状态 {r.status}"
                color = "#1a7f37" if ok else "#b54708"
            except Exception as e:
                msg = f"❌ 连接失败：{str(e)[:90]}"
                color = "#c0392b"
            try:
                self.after(0, lambda: self._test_done(msg, color))
            except Exception:
                pass

        threading.Thread(target=work, daemon=True).start()

    def _test_done(self, msg, color):
        try:
            self.lbl_status.config(text=msg, fg=color)
            self.btn_test.config(state="normal", text="测试连通性")
        except Exception:
            pass  # 窗口可能已被关闭

    def _save(self):
        try:
            d = self._collect()
            err = self._validate(d)
            if err:
                messagebox.showwarning("请检查", err, parent=self)
                return

            # 读模板作为基底，保留其它默认项（含 system_prompt 等）
            # 注意：这里必须用 yaml 解析，模板本身就是 YAML 格式。
            cfg = load_base_config()

            # 防御：模板若被破坏成非 dict，退回内嵌默认
            if not isinstance(cfg, dict):
                cfg = {}

            for sect in ("feishu", "browser", "llm", "polling", "context",
                         "reply", "reply_strategy"):
                if not isinstance(cfg.get(sect), dict):
                    cfg[sect] = {}

            cfg["feishu"]["base_url"] = d["url"]
            cfg["feishu"]["target_chat_name"] = d["chat"]
            cfg["llm"]["api_key"] = d["key"]
            cfg["llm"]["base_url"] = d["base"]
            cfg["llm"]["model"] = d["model"]
            cfg["browser"]["channel"] = "msedge"
            cfg["browser"]["headless"] = False
            cfg["browser"]["user_data_dir"] = "./.browser_data"

            try:
                with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                    yaml.safe_dump(cfg, f, allow_unicode=True, sort_keys=False)
            except Exception as e:
                messagebox.showerror(
                    "保存失败",
                    f"无法写入配置文件：\n{CONFIG_FILE}\n\n{e}\n\n"
                    f"请确认程序所在目录可写（不要放在 C:\\Program Files 等受保护目录）。",
                    parent=self,
                )
                return

            self.result = True
            try:
                messagebox.showinfo(
                    "配置完成",
                    "配置已保存！\n\n接下来会打开浏览器，首次使用请扫码登录飞书。",
                    parent=self,
                )
            except Exception:
                pass
            self.destroy()
        except Exception:
            # 任何意外都要让用户看见，绝不静默失败
            tb = traceback.format_exc()
            try:
                with open(ERROR_LOG, "a", encoding="utf-8") as f:
                    f.write(f"\n{'=' * 60}\n[{time.strftime('%Y-%m-%d %H:%M:%S')}] 保存配置失败\n{tb}")
            except Exception:
                pass
            try:
                messagebox.showerror(
                    "保存配置失败",
                    f"保存配置时出现意外错误：\n\n{tb.strip().splitlines()[-1]}\n\n"
                    f"详细信息已写入 launcher_error.log。",
                    parent=self,
                )
            except Exception:
                pass


# ============================================================
# 主控窗口
# ============================================================
class MainWindow(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("820x580")
        self.minsize(720, 500)
        self.proc = None
        self.log_queue = queue.Queue()
        install_exception_hook(self)
        self._build()
        self._load_summary()
        self.after(120, self._drain_log)

    # ---------- UI ----------
    def _build(self):
        # 顶栏
        top = tk.Frame(self, bg="#1f6feb", height=64)
        top.pack(fill="x")
        tk.Label(
            top, text=APP_TITLE, bg="#1f6feb", fg="white",
            font=("Microsoft YaHei UI", 14, "bold"),
        ).pack(side="left", padx=18, pady=16)

        self.lbl_state = tk.Label(
            top, text="● 未运行", bg="#1f6feb", fg="#ffd6d6",
            font=("Microsoft YaHei UI", 10, "bold"),
        )
        self.lbl_state.pack(side="right", padx=18)

        # 配置摘要
        info = tk.LabelFrame(self, text=" 当前配置 ", font=("Microsoft YaHei UI", 9))
        info.pack(fill="x", padx=14, pady=(12, 6))
        self.lbl_cfg = tk.Label(
            info, text="", justify="left", anchor="w",
            font=("Microsoft YaHei UI", 9), fg="#333",
        )
        self.lbl_cfg.pack(fill="x", padx=12, pady=8)

        # 按钮区
        bar = tk.Frame(self)
        bar.pack(fill="x", padx=14, pady=(0, 6))

        self.btn_start = tk.Button(
            bar, text="▶  启动监控", width=13, bg="#1a7f37", fg="white",
            activebackground="#156a2e", activeforeground="white",
            font=("Microsoft YaHei UI", 10, "bold"), command=self.start,
        )
        self.btn_start.pack(side="left")

        self.btn_stop = tk.Button(
            bar, text="■  停止", width=9, state="disabled", command=self.stop,
        )
        self.btn_stop.pack(side="left", padx=6)

        tk.Button(bar, text="⚙  修改配置", width=11, command=self.edit_config).pack(side="left", padx=6)
        tk.Button(bar, text="📂  打开数据目录", width=13, command=self.open_data).pack(side="left", padx=6)
        tk.Button(bar, text="🧹  清屏", width=8, command=self.clear_log).pack(side="right")

        # 日志
        logf = tk.LabelFrame(self, text=" 运行日志 ", font=("Microsoft YaHei UI", 9))
        logf.pack(fill="both", expand=True, padx=14, pady=(0, 12))
        self.txt = scrolledtext.ScrolledText(
            logf, wrap="word", font=("Consolas", 9),
            bg="#0d1117", fg="#c9d1d9", insertbackground="#c9d1d9",
        )
        self.txt.pack(fill="both", expand=True, padx=6, pady=6)
        self.txt.configure(state="disabled")

        self._log("欢迎使用飞书自动回复助手。")
        self._log("点击「启动监控」开始；首次运行会打开浏览器，请扫码登录飞书。")
        self._log("")

    def _load_summary(self):
        """把当前配置摘要显示出来。"""
        try:
            if not CONFIG_FILE.exists():
                self.lbl_cfg.config(text="⚠️ 尚未配置，请点击「修改配置」完成设置", fg="#b54708")
                return
            cfg = yaml.safe_load(CONFIG_FILE.read_text(encoding="utf-8")) or {}
            fs = cfg.get("feishu", {})
            llm = cfg.get("llm", {})
            key = llm.get("api_key", "")
            masked = (key[:8] + "…" + key[-4:]) if len(key) > 14 else "(未填)"
            self.lbl_cfg.config(
                text=(
                    f"飞书地址：{fs.get('base_url','(未填)')}\n"
                    f"会话名称：{fs.get('target_chat_name','(未填)')}\n"
                    f"模型：{llm.get('model','(未填)')}    密钥：{masked}"
                ),
                fg="#333",
            )
        except Exception as e:
            self.lbl_cfg.config(text=f"配置读取失败：{e}", fg="#c0392b")

    # ---------- 日志 ----------
    def _log(self, line):
        self.txt.configure(state="normal")
        self.txt.insert("end", line + "\n")
        self.txt.see("end")
        self.txt.configure(state="disabled")

    def _drain_log(self):
        try:
            while True:
                line = self.log_queue.get_nowait()
                self._log(line.rstrip("\n"))
        except queue.Empty:
            pass
        self.after(120, self._drain_log)

    def clear_log(self):
        self.txt.configure(state="normal")
        self.txt.delete("1.0", "end")
        self.txt.configure(state="disabled")

    # ---------- 启停 ----------
    def _python_cmd(self, script, extra=None):
        """构造启动子进程的命令。

        打包后：exe 带 --run-sub <script> 参数，由入口再执行脚本。
        源码运行：直接用 python <script>。

        注意：打包后的 exe 是窗口程序（没有控制台、没有 stdout），
        因此子进程输出统一写入"实时日志文件"，由 _tail_live 读取。
        """
        if IS_FROZEN:
            cmd = [sys.executable, "--run-sub", str(script)]
        else:
            cmd = [sys.executable, "-u", str(script)]
        if extra:
            cmd += list(extra)
        return cmd

    def start(self):
        if self.proc and self.proc.poll() is None:
            return
        if not CONFIG_FILE.exists():
            if not messagebox.askyesno(
                "尚未配置",
                "还没有配置文件，是否现在填写？",
                parent=self,
            ):
                return
            self.edit_config()
            if not CONFIG_FILE.exists():
                return

        # 清空实时日志（仅本次运行输出）
        try:
            LIVE_LOG.write_text("", encoding="utf-8")
        except Exception:
            pass

        self._log("─" * 60)
        self._log(f"[{time.strftime('%H:%M:%S')}] 启动监控…")

        env = os.environ.copy()
        env["FEISHU_DATA_DIR"] = str(DATA_DIR)
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUNBUFFERED"] = "1"
        env["FEISHU_LIVE_LOG"] = str(LIVE_LOG)

        script = _script_path("step2_auto_reply.py")

        popen_kw = dict(stdin=subprocess.DEVNULL, cwd=str(DATA_DIR), env=env)
        self._live_fh = None
        if IS_FROZEN:
            # 窗口 exe 无 stdout → 重定向到实时日志文件
            try:
                self._live_fh = open(LIVE_LOG, "a", encoding="utf-8", errors="replace")
            except Exception:
                self._live_fh = subprocess.DEVNULL
            popen_kw.update(
                stdout=self._live_fh,
                stderr=subprocess.STDOUT,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        else:
            popen_kw.update(
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
            )

        try:
            self.proc = subprocess.Popen(self._python_cmd(script), **popen_kw)
        except Exception as e:
            messagebox.showerror("启动失败", f"无法启动监控进程：\n{e}", parent=self)
            self._log(f"❌ 启动失败：{e}")
            return

        self.btn_start.config(state="disabled")
        self.btn_stop.config(state="normal")
        self.lbl_state.config(text="● 运行中", fg="#b7f7c2")

        if IS_FROZEN:
            try:
                self._live_pos = LIVE_LOG.stat().st_size
            except Exception:
                self._live_pos = 0
            self.after(700, self._tail_live)
        else:
            threading.Thread(target=self._pump, args=(self.proc,), daemon=True).start()

        self.after(1000, self._watch)

    def _tail_live(self):
        """读取实时日志文件的新增内容（打包模式）。"""
        if not self.proc:
            return
        try:
            if LIVE_LOG.stat().st_size > self._live_pos:
                with open(LIVE_LOG, "r", encoding="utf-8", errors="replace") as f:
                    f.seek(self._live_pos)
                    data = f.read()
                    self._live_pos = f.tell()
                for line in data.splitlines():
                    self.log_queue.put(line + "\n")
        except FileNotFoundError:
            pass
        except Exception:
            pass

        if self.proc.poll() is None:
            self.after(500, self._tail_live)
        else:
            # 收尾：读完剩余内容
            try:
                with open(LIVE_LOG, "r", encoding="utf-8", errors="replace") as f:
                    f.seek(self._live_pos)
                    for line in f.read().splitlines():
                        self.log_queue.put(line + "\n")
            except Exception:
                pass
            self.log_queue.put(f"[{time.strftime('%H:%M:%S')}] 进程已结束。\n")

    def _pump(self, proc):
        """把子进程输出搬进队列（源码模式）。"""
        try:
            for line in proc.stdout:
                self.log_queue.put(line)
        except Exception:
            pass
        finally:
            self.log_queue.put(f"[{time.strftime('%H:%M:%S')}] 进程已结束。\n")

    def _watch(self):
        if self.proc and self.proc.poll() is not None:
            self.btn_start.config(state="normal")
            self.btn_stop.config(state="disabled")
            self.lbl_state.config(text="● 已停止", fg="#ffd6d6")
            return
        if self.proc:
            self.after(1000, self._watch)

    def stop(self):
        if not self.proc:
            return
        self._log(f"[{time.strftime('%H:%M:%S')}] 正在停止…")
        pid = None
        try:
            pid = self.proc.pid
            self.proc.terminate()          # 温和终止
            try:
                self.proc.wait(timeout=6)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                try:
                    self.proc.wait(timeout=6)
                except Exception:
                    pass
            # 兜底：连同子进程树一起结束（playwright 的 node 驱动 + 浏览器）
            # 否则残留的浏览器会锁住 .browser_data，导致下次启动直接失败
            if pid:
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(pid)],
                    capture_output=True, timeout=20,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
        except Exception as e:
            self._log(f"停止时出错：{e}")

        # 清理可能残留的单实例锁（程序被强杀时会留下）
        try:
            removed = _clean_profile_locks()
            if removed:
                self._log(f"已清理残留锁文件：{', '.join(removed)}")
        except Exception:
            pass

        # 关闭实时日志句柄
        try:
            if getattr(self, "_live_fh", None) and self._live_fh is not subprocess.DEVNULL:
                self._live_fh.close()
        except Exception:
            pass
        self._live_fh = None
        self.btn_start.config(state="normal")
        self.btn_stop.config(state="disabled")
        self.lbl_state.config(text="● 已停止", fg="#ffd6d6")

    # ---------- 其它按钮 ----------
    def edit_config(self):
        w = ConfigWizard()
        w.mainloop()
        self._load_summary()
        if w.result:
            self._log("配置已更新。")

    def open_data(self):
        try:
            os.startfile(str(DATA_DIR))  # noqa: S606 (Windows)
        except Exception:
            subprocess.Popen(["explorer", str(DATA_DIR)])

    def on_close(self):
        if self.proc and self.proc.poll() is None:
            if not messagebox.askyesno("退出", "监控仍在运行，确定要退出吗？", parent=self):
                return
            self.stop()
        self.destroy()


# ============================================================
# 入口
# ============================================================
def run_subprocess_mode():
    """
    打包后子进程模式：exe --run-sub <script.py> [args...]
    由本函数执行目标脚本，使 exe 既能当 GUI 又能当脚本运行器。
    """
    args = sys.argv[2:]
    if not args:
        print("用法: app.exe --run-sub <script.py> [args...]", file=sys.stderr)
        return 2

    # 脚本可能以相对名传入（如 step2_auto_reply.py），
    # 需要同时尝试 CWD、打包资源目录（_internal）、数据目录。
    raw = args[0]
    candidates = [Path(raw)]
    if not Path(raw).is_absolute():
        candidates += [APP_DIR / raw, DATA_DIR / raw]
    script = next((c for c in candidates if c.exists()), None)
    if script is None:
        print(f"找不到脚本: {raw}（已尝试: {candidates}）", file=sys.stderr)
        return 2

    sys.argv = [str(script)] + args[1:]
    if str(script.parent) not in sys.path:
        sys.path.insert(0, str(script.parent))
    if str(APP_DIR) not in sys.path:
        sys.path.insert(0, str(APP_DIR))

    code = compile(script.read_text(encoding="utf-8"), str(script), "exec")
    g = {"__name__": "__main__", "__file__": str(script)}
    exec(code, g)
    return 0


def main():
    if "--run-sub" in sys.argv:
        sys.exit(run_subprocess_mode())

    # 单实例保护：两个窗口同抢一个浏览器配置目录必然失败
    other = _acquire_single_instance()
    if other:
        try:
            messagebox.showwarning(
                "程序已在运行",
                f"检测到本程序已经打开（进程 {other}）。\n\n"
                "同时开两个窗口会争抢浏览器配置，导致启动失败。\n"
                "请切换到已经打开的那个窗口；确实要重开，\n"
                "请先关闭它，或到任务管理器结束该进程。",
            )
        except Exception:
            pass
        return

    try:
        # 清理上次被强杀留下的浏览器锁文件（否则首次启动必失败）
        try:
            _clean_profile_locks()
        except Exception:
            pass

        if not CONFIG_FILE.exists():
            wiz = ConfigWizard()
            wiz.mainloop()
            if not wiz.result:
                return

        win = MainWindow()
        win.protocol("WM_DELETE_WINDOW", win.on_close)
        win.mainloop()
    finally:
        _release_single_instance()


if __name__ == "__main__":
    main()
