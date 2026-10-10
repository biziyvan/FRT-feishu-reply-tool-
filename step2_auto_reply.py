"""
飞书 AI 教学平台 · 自动回复主程序
==================================
设计原则（解决翻页/卡死/费token问题）：

  【不反复滚动】
   - 启动时：从本地 history.json 加载历史上下文（由 fetch_history.py 预先抓好）
   - 运行中：只读取当前可见的最新消息（不滚动），据此判断有无新消息
   - 需要更新历史时，手动跑 fetch_history.py 即可

  【不再卡死】
   - 所有浏览器操作加超时保护
   - 单轮执行时间有上限

  【省 token】
   - 用 denoise.py 去噪、压缩上下文

运行：
  .venv\\Scripts\\python.exe step2_auto_reply.py                # 一直运行
  .venv\\Scripts\\python.exe step2_auto_reply.py --max-replies 10  # 跑10次停
  .venv\\Scripts\\python.exe step2_auto_reply.py --dry-run --max-replies 5  # 只生成不发送
停止：Ctrl+C
"""

import sys
import json
import time
import os
import io
import random
import logging
import signal
import argparse
import subprocess
from pathlib import Path

import yaml
import requests
from playwright.sync_api import sync_playwright

from denoise import (
    denoise,
    build_context,
    classify,
    clean_text,
    extract_current_course,
    sort_by_time,
    find_pending_question,
    question_fingerprint,
    extract_pending_question,
    fp_to_str,
)


from runtime import DATA_DIR

BASE_DIR = DATA_DIR
STATE_FILE = BASE_DIR / "answered.json"
HISTORY_FILE = BASE_DIR / "history.json"
LOG_FILE = BASE_DIR / "auto_reply.log"
STATS_FILE = BASE_DIR / "reply_stats.json"
SHOT_DIR = BASE_DIR / "debug_shots"
SHOT_DIR.mkdir(exist_ok=True)

_running = True


def _stop(sig, frame):
    global _running
    _running = False


signal.signal(signal.SIGINT, _stop)


def parse_args():
    ap = argparse.ArgumentParser(description="飞书自动回复")
    ap.add_argument("--max-replies", type=int, default=0, help="最多回复次数（0=不限）")
    ap.add_argument("--dry-run", action="store_true", help="只生成答案不发送")
    return ap.parse_args()


ARGS = parse_args()


# ---------------- 日志 ----------------
logger = logging.getLogger("auto")
logger.setLevel(logging.DEBUG)
_fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", "%H:%M:%S")
# 只在没有 handler 时添加，避免被 import 两次导致日志重复
if not any(isinstance(h, logging.FileHandler) for h in logger.handlers):
    _fh = logging.FileHandler(LOG_FILE, mode="a", encoding="utf-8")
    _fh.setFormatter(_fmt)
    logger.addHandler(_fh)

# 打包运行时，GUI 通过「实时日志文件」读取输出（窗口程序无 stdout）
_LIVE_LOG = os.environ.get("FEISHU_LIVE_LOG")
if _LIVE_LOG and not any(
    isinstance(h, logging.FileHandler) and getattr(h, "baseFilename", "") == _LIVE_LOG
    for h in logger.handlers
):
    try:
        _lh = logging.FileHandler(_LIVE_LOG, mode="a", encoding="utf-8")
        _lh.setFormatter(_fmt)
        logger.addHandler(_lh)
    except Exception:
        pass

if not any(isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler)
           for h in logger.handlers):
    # 关键：打包成窗口程序后，stdout 可能是 None，或是 GBK 编码的控制台代理。
    # 日志里含 Emoji（📚✅💡），用 GBK 编码会直接抛 UnicodeEncodeError，
    # 因此统一包一层 UTF-8 + 容错的输出流。
    try:
        _raw_out = sys.stdout if sys.stdout is not None else open(os.devnull, "w")
    except Exception:
        _raw_out = open(os.devnull, "w")

    try:
        _safe_out = io.TextIOWrapper(
            _raw_out.buffer, encoding="utf-8", errors="replace", line_buffering=True
        )
    except Exception:
        # 没有 .buffer（如已被包装过 / 特殊流）→ 退化为容错包装
        class _SafeStream:
            def __init__(self, s):
                self._s = s

            def write(self, data):
                try:
                    self._s.write(data)
                except Exception:
                    try:
                        self._s.write(data.encode("utf-8", "replace").decode("utf-8", "replace"))
                    except Exception:
                        pass

            def flush(self):
                try:
                    self._s.flush()
                except Exception:
                    pass

            def isatty(self):
                return False

        _safe_out = _SafeStream(_raw_out)

    try:
        _sh = logging.StreamHandler(_safe_out)
        _sh.setFormatter(_fmt)
        logger.addHandler(_sh)
    except Exception:
        pass
logger.propagate = False


def log(msg, level="info"):
    getattr(logger, level)(msg)


# ---------------- 配置 ----------------
from settings import load_config  # noqa: E402


CFG = load_config()
FS = CFG["feishu"]
BR = CFG["browser"]
POLL = CFG["polling"]
LLM = CFG["llm"]
REPLY = CFG["reply"]
CTX = CFG.get("context", {})
RSTRAT = CFG.get("reply_strategy", {})


# ---------------- 状态 ----------------
def load_state():
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"answered": []}


def save_state(state):
    state["answered"] = state["answered"][-800:]
    if "answered_fps" in state:
        state["answered_fps"] = state["answered_fps"][-800:]
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def load_history():
    """加载本地历史（由 fetch_history.py 生成）。"""
    if HISTORY_FILE.exists():
        try:
            data = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
            return data
        except Exception as e:
            log(f"读取 history.json 失败: {e}", "warning")
    return []


# ---------------- 大模型 ----------------
def ask_llm(context_str):
    """把已构造好的精简上下文发给大模型。"""
    api_key = LLM.get("api_key", "").strip()
    if not api_key:
        log("未配置 API Key", "error")
        return ""

    messages = [
        {"role": "system", "content": LLM["system_prompt"].strip()},
        {"role": "user", "content": context_str},
    ]
    payload = {
        "model": LLM["model"],
        "messages": messages,
        "max_tokens": LLM.get("max_tokens", 500),
        "temperature": LLM.get("temperature", 0.3),
    }
    if LLM.get("enable_thinking") is False:
        payload["enable_thinking"] = False

    url = LLM["base_url"].rstrip("/") + "/chat/completions"
    timeout = LLM.get("timeout", 45)
    max_retries = LLM.get("max_retries", 3)
    last_err = ""

    for attempt in range(1, max_retries + 1):
        try:
            r = requests.post(
                url,
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json=payload,
                timeout=timeout,
            )
            r.raise_for_status()
            return r.json()["choices"][0]["message"]["content"].strip()
        except requests.HTTPError as e:
            last_err = f"HTTP {e} | {getattr(e.response, 'text', '')[:150]}"
            code = getattr(e.response, "status_code", 0)
            if 400 <= code < 500 and code != 429:
                log(f"  请求被拒: {last_err}", "error")
                return ""
        except Exception as e:
            last_err = str(e)[:150]

        if attempt < max_retries:
            wait = attempt * 2
            log(f"  第 {attempt} 次失败({last_err[:50]})，{wait}s 后重试", "warning")
            time.sleep(wait)

    log(f"  大模型最终失败: {last_err}", "error")
    return ""


# ---------------- 浏览器 ----------------
# 反"后台冻结"启动参数：
#   Chromium 在窗口被遮挡/最小化/失去焦点时，会把标签页降级为后台状态，
#   停止渲染、停止定时器、降低 CPU —— 导致页面 DOM 不再更新（读不到新消息），
#   CDP 通信也可能超时。下面这些开关强制浏览器始终保持"前台活跃"语义。
ANTI_THROTTLE_ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--no-sandbox",
    # 关闭后台标签页节流（最关键）
    "--disable-background-timer-throttling",
    "--disable-backgrounding-occluded-windows",
    "--disable-renderer-backgrounding",
    # 关闭页面可见性降级 / 焦点抢占限制
    "--disable-features=CalculateNativeWinOcclusion,IntensiveWakeUpThrottling",
    "--disable-ipc-flooding-protection",
    # 允许窗口在失焦时仍正常渲染
    "--disable-backgrounding-occluded-windows",
    "--disable-hang-monitor",
    # 不做窗口遮挡检测
    "--disable-window-occlusion-tracking",
]

# Edge 155+ 引入了"兼容层重启"：以自动化参数启动时，Edge 会先把自己重启一遍
# 再接管，Playwright 刚连上就发现原进程已退出 → TargetClosedError
# （表现为"启动即秒退"，退出码 0、无任何报错输出）。
# 该开关让 Edge 跳过这次重启。仅 Edge 内核需要，其余 Chromium 会忽略未知开关。
EDGE_COMPAT_ARGS = ["--edge-skip-compat-layer-relaunch"]


_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# 让页面认为自己始终"可见"（拦截 Page Visibility API）
VISIBILITY_SCRIPT = """
Object.defineProperty(document, 'visibilityState', {get: () => 'visible', configurable: true});
Object.defineProperty(document, 'hidden', {get: () => false, configurable: true});
document.addEventListener('visibilitychange', e => e.stopImmediatePropagation(), true);
window.addEventListener('blur', e => {}, true);
window.addEventListener('focus', e => {}, true);
"""


def profile_dir():
    """浏览器用户数据目录（绝对路径）。"""
    return (BASE_DIR / BR["user_data_dir"]).resolve()


def clean_profile_locks(ud=None):
    """删除 Chromium 的单实例锁文件。

    程序被强杀（任务管理器结束进程 / 断电 / 崩溃）后，user_data_dir 里会残留
    SingletonLock 等锁文件。下次启动时浏览器会误判"已有实例在运行"，把命令
    转交给那个不存在的实例后**立即自行退出**，playwright 于是报
    TargetClosedError: Target page, context or browser has been closed。
    """
    ud = Path(ud or profile_dir())
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


def find_browser_pids_using(ud=None):
    """找出命令行中使用了指定配置目录的 Edge/Chrome 进程 PID。

    仅匹配**我们自己的配置目录**，不会误伤用户正在使用的浏览器。
    """
    ud = Path(ud or profile_dir())
    key = str(ud).lower().replace("'", "''")
    ps = (
        "$k = '" + key + "';"
        "Get-CimInstance Win32_Process -Filter \"Name='msedge.exe' or Name='chrome.exe'\" "
        "-ErrorAction SilentlyContinue | "
        "Where-Object { $_.CommandLine -and $_.CommandLine.ToLower().Contains($k) } | "
        "Select-Object -ExpandProperty ProcessId"
    )
    pids = []
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
            capture_output=True, timeout=25, creationflags=_NO_WINDOW,
        )
        for line in out.stdout.decode("utf-8", "replace").splitlines():
            line = line.strip()
            if line.isdigit():
                pids.append(int(line))
    except Exception:
        pass
    return pids


def kill_pids(pids):
    """结束进程树（/T 连带子进程），避免残留浏览器锁住配置目录。"""
    pids = [p for p in pids if isinstance(p, int) and p > 0]
    if not pids:
        return
    cmd = ["taskkill", "/F", "/T"]
    for p in pids:
        cmd += ["/PID", str(p)]
    try:
        subprocess.run(cmd, capture_output=True, timeout=40, creationflags=_NO_WINDOW)
    except Exception:
        # 批量失败则逐个来
        for p in pids:
            try:
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(p)],
                               capture_output=True, timeout=20, creationflags=_NO_WINDOW)
            except Exception:
                pass


def recover_profile(ud=None):
    """清锁 + 结束仍占用该配置目录的残留浏览器进程。"""
    ud = Path(ud or profile_dir())
    locks = clean_profile_locks(ud)
    if locks:
        log(f"  已清理残留锁文件: {', '.join(locks)}")
    pids = find_browser_pids_using(ud)
    if pids:
        log(f"  发现 {len(pids)} 个残留浏览器进程占用该配置，正在结束 …")
        kill_pids(pids)
        time.sleep(2)
    return bool(locks or pids)


def _attach_visibility_script(ctx):
    try:
        ctx.add_init_script(VISIBILITY_SCRIPT)
    except Exception:
        pass


def launch_browser(p):
    """启动浏览器。带"自愈重试"：

    实测飞书/Edge 环境下浏览器启动失败（TargetClosedError）最常见的原因，
    是配置目录被上一次残留的浏览器进程占用（程序被强杀、点了停止又有残留、
    或 Edge 正在后台自动更新）。这类故障是**瞬时**的，清掉锁 + 结束残留进程
    后重试即可成功，不该让用户看到崩溃。

    依次尝试：① 抗节流参数 → ② 最小参数 → ③ 完全默认参数
    """
    ud = profile_dir()
    try:
        ud.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass

    base = dict(
        user_data_dir=str(ud),
        headless=BR["headless"],
        slow_mo=BR["slow_mo"],
        viewport={"width": BR["viewport_width"], "height": BR["viewport_height"]},
    )
    if BR.get("channel"):
        base["channel"] = BR["channel"]

    # (说明, 附加参数, 本尝试前是否先自愈, 重试前等待秒数)
    # 注意：自愈成功后要**先用原来的抗节流参数重试**，只有确实起不来才降级，
    # 否则会白白丢掉"抗浏览器遮挡"这个关键能力。
    # 中间穿插的等待，是为了扛住 Edge 后台自动更新之类的瞬时故障。
    # Edge 155+ 必须带"跳过兼容层重启"开关，否则每种参数组合都会秒退，
    # 因此把它并进**每一条**尝试路径（含降级的最小/默认参数），不能只在抗节流里。
    _chan = str(BR.get("channel") or "")
    compat = list(EDGE_COMPAT_ARGS) if _chan.startswith("msedge") else []

    attempts = [
        ("抗节流参数", ANTI_THROTTLE_ARGS + compat, False, 0),
        ("抗节流参数", ANTI_THROTTLE_ARGS + compat, True, 2),
        ("抗节流参数", ANTI_THROTTLE_ARGS + compat, True, 10),
        ("最小参数", ["--disable-blink-features=AutomationControlled"] + compat, False, 2),
        ("默认参数", compat or None, False, 10),
    ]

    # 启动前的信息性体检：只在发现"上次浏览器可能没退干净"的迹象时才做
    # 昂贵的进程扫描（否则每次启动白白多花几秒）。
    try:
        if any(ud.glob("Singleton*")) or (ud / "lockfile").exists():
            leftovers = find_browser_pids_using(ud)
            if leftovers:
                log(f"  提示：检测到 {len(leftovers)} 个残留浏览器进程占用该配置，"
                    f"若启动失败将自动清理", "warning")
    except Exception:
        pass

    last_err = None
    t_start = time.time()
    for idx, (label, extra_args, do_recover, delay) in enumerate(attempts, 1):
        if delay:
            log(f"  等待 {delay}s 后重试（{idx}/{len(attempts)}）…", "warning")
            time.sleep(delay)
        if do_recover:
            log(f"  正在自愈后重试（{idx}/{len(attempts)}）…", "warning")
            recover_profile(ud)

        kw = dict(base)
        if extra_args:
            kw["args"] = extra_args

        try:
            ctx = p.chromium.launch_persistent_context(**kw)
            if idx > 1:
                log(f"  浏览器已启动（第 {idx} 次尝试，{label}）")
            else:
                log(f"  浏览器已就绪（{time.time() - t_start:.1f}s）")
            _attach_visibility_script(ctx)
            return ctx
        except Exception as e:
            msg = str(e).splitlines()[0] if str(e) else type(e).__name__
            last_err = e
            if not is_lost_error(e):
                # 不是"启动即退出"类的问题（如配置写错），直接抛
                raise
            log(f"  浏览器启动失败（{label}）：{msg[:110]}", "warning")

    raise RuntimeError(f"浏览器多次启动失败：{str(last_err).splitlines()[0][:200]}")


def wait_for_chat_list(page, timeout=90):
    """等待会话列表出现。页面失联不在这里处理，只返回 False 由上层恢复。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            loc = page.locator(".list_items")
            if loc.count() > 0 and len(loc.first.inner_text()) > 20:
                return True
        except Exception:
            pass
        time.sleep(2)
    return False


def is_lost_error(e):
    """判断异常是否为"页面/浏览器失联"类（被遮挡冻结时常见）。"""
    msg = str(e)
    return any(k in msg for k in (
        "has been closed", "Target closed", "Target page",
        "Browser has been closed", "Connection closed",
        "Protocol error", "Execution context was destroyed",
        "Target crashed", "Session closed",
    ))


def open_target_chat(page, name):
    try:
        loc = page.locator(f'.list_items >> text="{name}"')
        if loc.count() > 0:
            loc.first.click(timeout=8000)
            time.sleep(2)
            return True
    except Exception as e:
        if is_lost_error(e):
            raise  # 交给上层恢复
        log(f"  打开会话失败: {str(e)[:80]}", "warning")
    return False


def snapshot_visible(page, limit=80):
    """
    只读取当前 DOM 可见的消息（不滚动）。
    返回按【真实时间序】（雪花 ID 时间分量）升序的列表。

    注意：页面失联类错误【不吞掉】，向上抛出让主循环触发自动恢复；
           只有单条消息读取失败才忽略。
    """
    out = {}
    items = page.locator(".js-message-item")
    n = items.count()
    start = max(0, n - limit)
    for i in range(start, n):
        el = items.nth(i)
        try:
            mid = el.get_attribute("id") or ""
            cls = el.get_attribute("class") or ""
            text = el.inner_text().strip()
            if mid and text:
                out[mid] = {
                    "id": mid,
                    "is_from_other": "message-not-self" in cls,
                    "text": text,
                }
        except Exception:
            # 单条消息读取失败（可能正被虚拟滚动回收），忽略
            continue
    # 记录 DOM 实际条数，便于诊断
    snapshot_visible.last_dom_count = n
    return sort_by_time(out.values())


snapshot_visible.last_dom_count = 0


def _count_my_messages(page):
    """统计聊天记录里"我发出的"消息条数，用于发送后校验。"""
    try:
        return page.evaluate(
            """() => document.querySelectorAll(
                '.js-message-item:not(.message-not-self)'
            ).length"""
        )
    except Exception:
        return -1


def _read_input_box(page):
    """读取输入框当前文本。"""
    try:
        return page.evaluate(
            """() => {
                const el = document.querySelectorAll('[contenteditable="true"]');
                const box = el[el.length - 1];
                return box ? box.innerText.trim() : '';
            }"""
        )
    except Exception:
        return ""


def _clear_input_box(page, tries=3):
    """
    清空输入框残留内容。

    飞书是 Lark 富文本编辑器，单次 execCommand('delete') 常常清不干净
    （会残留部分文本 / 空段落节点）。这里循环多次，并用 CDP 键盘事件
    做一次 Ctrl+A + Delete 兜底。
    """
    for i in range(tries):
        try:
            # 方式 1：全选 + 删除
            page.evaluate(
                """() => {
                    const el = document.querySelectorAll('[contenteditable="true"]');
                    const box = el[el.length - 1];
                    if (!box) return;
                    box.focus();
                    const sel = window.getSelection();
                    sel.removeAllRanges();
                    const r = document.createRange();
                    r.selectNodeContents(box);
                    sel.addRange(r);
                    document.execCommand('delete', false);
                    box.dispatchEvent(new InputEvent('input', {bubbles: true}));
                }"""
            )
            time.sleep(0.25)
            if not _read_input_box(page):
                return True
        except Exception:
            pass

        # 方式 2：CDP 派发 Ctrl+A 然后 Delete（更贴近真实操作）
        try:
            cdp = page.context.new_cdp_session(page)
            cdp.send("Input.dispatchKeyEvent", {
                "type": "keyDown", "key": "a", "code": "KeyA",
                "modifiers": 2,  # Ctrl
                "windowsVirtualKeyCode": 65, "nativeVirtualKeyCode": 65,
            })
            cdp.send("Input.dispatchKeyEvent", {
                "type": "keyUp", "key": "a", "code": "KeyA",
                "modifiers": 2,
                "windowsVirtualKeyCode": 65, "nativeVirtualKeyCode": 65,
            })
            time.sleep(0.15)
            cdp.send("Input.dispatchKeyEvent", {
                "type": "keyDown", "key": "Backspace", "code": "Backspace",
                "windowsVirtualKeyCode": 8, "nativeVirtualKeyCode": 8,
            })
            cdp.send("Input.dispatchKeyEvent", {
                "type": "keyUp", "key": "Backspace", "code": "Backspace",
                "windowsVirtualKeyCode": 8, "nativeVirtualKeyCode": 8,
            })
            time.sleep(0.3)
            if not _read_input_box(page):
                return True
        except Exception:
            pass

    return not _read_input_box(page)


def send_reply(page, text):
    """
    发送回复（严格保证只发一次）。

    飞书输入框是 Lark 富文本编辑器（`.zone-container.editor-kit-container`），
    直接改 DOM 不会同步编辑器内部状态，因此用 **CDP Input.insertText**
    （浏览器原生输入层插入），再用 CDP 派发 Enter。

    不依赖系统窗口焦点 → 浏览器被遮挡时也能工作。

    【防重复发送】关键设计：
      - 发送前记录"我发出的消息条数" before
      - 每条发送路径执行后，立即校验消息数是否 +1
      - 只要检测到已发出，就立刻返回成功，绝不再走其他路径
      - 不再依赖"输入框是否为空"来判断（富文本残留会导致误判 → 重复发）
    """
    box = page.locator('[contenteditable="true"]').last
    if box.count() == 0:
        log("  找不到输入框", "error")
        return False

    before = _count_my_messages(page)

    # ---- 路径 1：CDP 原生输入 + Enter（首选，后台可用）----
    try:
        _clear_input_box(page)
        time.sleep(0.2)
        try:
            box.click(timeout=5000)
        except Exception:
            pass
        time.sleep(0.3)

        cdp = page.context.new_cdp_session(page)
        cdp.send("Input.insertText", {"text": text})
        time.sleep(0.4)

        typed = _read_input_box(page)
        if not typed:
            raise RuntimeError("CDP insertText 未写入内容")

        # 派发 Enter
        for ev_type in ("keyDown", "keyUp"):
            cdp.send("Input.dispatchKeyEvent", {
                "type": ev_type,
                "key": "Enter",
                "code": "Enter",
                "windowsVirtualKeyCode": 13,
                "nativeVirtualKeyCode": 13,
            })
        time.sleep(1.2)

        # ★ 校验：消息数是否 +1
        after = _count_my_messages(page)
        if after > before:
            return True
        if after == before == -1:
            # 读不到计数，退而看输入框是否清空
            if not _read_input_box(page):
                return True
            log("  无法确认发送结果，按未发送处理", "warning")
            return False
        # 消息数没变 → 未发出
        log("  CDP 方式未发出，尝试点击发送按钮", "warning")
    except Exception as e:
        log(f"  CDP 方式异常: {str(e)[:70]}", "warning")

    # ---- 路径 2：点击发送按钮（仅在上一步确认未发出时才执行）----
    try:
        after = _count_my_messages(page)
        if after > before:
            return True  # 保险：确实已发出
        btn = page.locator(
            'button[class*="send"]:not([disabled]), [class*="send-button"], [aria-label*="发送"]'
        ).last
        if btn.count() > 0:
            btn.click(timeout=3000)
            time.sleep(1.2)
            after = _count_my_messages(page)
            if after > before:
                return True
            if after == before == -1 and not _read_input_box(page):
                return True
    except Exception as e:
        log(f"  点击发送按钮失败: {str(e)[:70]}", "warning")

    # ---- 路径 3：键盘模拟（最后的兜底，仅确认未发出时执行）----
    try:
        after = _count_my_messages(page)
        if after > before:
            return True
        _clear_input_box(page)
        time.sleep(0.2)
        box.click(timeout=5000)
        time.sleep(0.3)
        page.keyboard.type(text, delay=25)
        time.sleep(0.4)
        page.keyboard.press("Enter")
        time.sleep(1.2)
        after = _count_my_messages(page)
        if after > before:
            return True
        if after == before == -1 and not _read_input_box(page):
            return True
    except Exception as e:
        log(f"  键盘方式失败: {str(e)[:70]}", "error")

    return False


def page_alive(page):
    """检查页面是否仍然可用（未被冻结/关闭）。"""
    try:
        page.evaluate("() => document.readyState")
        return True
    except Exception:
        return False


def ensure_page_ready(page, target_name, ctx=None, retries=3):
    """
    确保页面处于可用状态，尽最大努力恢复：
      1. 页面可用 → 直接返回
      2. 页面对象失效（is_closed）→ 用 context 新建一个页面
      3. 页面活着但 CDP 卡住 → 尝试 goto 重载
    恢复后重新进入目标会话。
    返回可用的 page（可能与传入的不同）。
    """
    for attempt in range(1, retries + 1):
        # --- 情况 A：页面对象已彻底关闭，必须新建 ---
        try:
            closed = page.is_closed()
        except Exception:
            closed = True  # 连 is_closed 都调不了，视作已关闭

        if closed:
            if ctx is None:
                log("  页面已关闭且无 context 可新建，无法恢复", "error")
                return page
            log(f"  页面已关闭，新建页面（第 {attempt} 次）", "warning")
            try:
                page = ctx.new_page()
                page.goto(FS["base_url"], wait_until="domcontentloaded", timeout=40000)
                if wait_for_chat_list(page, timeout=60):
                    try:
                        open_target_chat(page, target_name)
                    except Exception as e:
                        if not is_lost_error(e):
                            raise
                    if page_alive(page):
                        time.sleep(2)
                        return page
            except Exception as e:
                log(f"  新建页面失败: {str(e)[:80]}", "warning")
                time.sleep(3)
                continue

        # --- 情况 B：页面没关但失联（CDP 卡住），尝试重载 ---
        if not page_alive(page):
            log(f"  页面失联，尝试重载（第 {attempt} 次）", "warning")
            try:
                page.goto(FS["base_url"], wait_until="domcontentloaded", timeout=40000)
                if wait_for_chat_list(page, timeout=60):
                    try:
                        open_target_chat(page, target_name)
                    except Exception as e:
                        if not is_lost_error(e):
                            raise
                    if page_alive(page):
                        time.sleep(2)
                        return page
            except Exception as e:
                log(f"  重载失败: {str(e)[:80]}", "warning")
                # 重载也失败，下一轮会走"新建页面"
                try:
                    page.close()
                except Exception:
                    pass
            time.sleep(3)
            continue

        # --- 情况 C：页面活着，确认还在目标会话 ---
        try:
            if page.locator(".js-message-item").count() > 0:
                return page
        except Exception:
            pass
        log(f"  页面无消息区，尝试重进会话（第 {attempt} 次）", "warning")
        try:
            if wait_for_chat_list(page, timeout=45):
                open_target_chat(page, target_name)
                time.sleep(3)
                return page
        except Exception as e:
            log(f"  重进会话失败: {str(e)[:80]}", "warning")
        time.sleep(2)

    return page


def bring_to_front(page):
    """尝试把窗口提到前台（失败也无所谓，只是锦上添花）。"""
    try:
        page.bring_to_front()
    except Exception:
        pass


# ---------------- 主流程 ----------------
def main():
    log("=" * 62)
    log("  飞书 AI 教学平台 · 自动回复程序 启动")
    log("=" * 62)

    if not LLM.get("api_key", "").strip():
        log("请先在 config.yaml 填入 llm.api_key！", "error")
        _shutdown_logging()
        sys.exit(1)

    state = load_state()
    answered = set(state.get("answered", []))
    log(f"模型: {LLM['model']}")
    log(f"已回复记录: {len(answered)} 条")

    # 加载历史上下文
    base_history = load_history()
    if base_history:
        log(f"已加载历史上下文 {len(base_history)} 条（来自 history.json）")
    else:
        log("未找到 history.json，将只基于当前可见消息作答", "warning")
        log("  建议先运行 fetch_history.py 抓取历史", "warning")

    context_size = CTX.get("context_size", 30)
    max_chars = CTX.get("max_chars", 3000)

    with sync_playwright() as p:
        log("启动浏览器 ...")
        try:
            ctx = launch_browser(p)
        except Exception as e:
            log("浏览器启动失败。", "error")
            log(f"  原因: {str(e).splitlines()[0][:180]}", "error")
            log("", "error")
            log("  请按顺序尝试：", "error")
            log("   1) 关闭所有 Edge 窗口，再点「启动监控」重试", "error")
            log("   2) 仍失败 → 删除程序目录下的 .browser_data 文件夹，"
                "再启动（需重新扫码登录）", "error")
            log("   3) 仍失败 → 重启电脑后重试", "error")
            log("", "error")
            log("  （提示：Edge 正在后台自动更新时也会出现此现象，等 1 分钟再试通常就好）",
                "error")
            _shutdown_logging()
            sys.exit(2)

        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(FS["base_url"], wait_until="domcontentloaded", timeout=60000)

        if "accounts.feishu" in page.url or "login" in page.url.lower():
            log("需要登录，请扫码（最多等 3 分钟）...")
            deadline = time.time() + 180
            while time.time() < deadline:
                if "accounts.feishu" not in page.url and "login" not in page.url.lower():
                    break
                time.sleep(2)
            if "messenger" not in page.url:
                page.goto(FS["base_url"], wait_until="domcontentloaded", timeout=60000)

        log("等待会话列表 ...")
        if not wait_for_chat_list(page):
            log("会话列表加载失败", "error")
            ctx.close()
            _shutdown_logging()
            sys.exit(1)

        target = FS["target_chat_name"]
        log(f"进入会话「{target}」...")
        if not open_target_chat(page, target):
            log("无法进入目标会话", "error")
            ctx.close()
            _shutdown_logging()
            sys.exit(1)

        log(f"就绪。轮询 {POLL['interval']}s（不滚动，只读最新消息）")
        if ARGS.max_replies > 0:
            log(f"限制：最多回复 {ARGS.max_replies} 次")
        if ARGS.dry_run:
            log("干跑模式：不发送")
        log("-" * 62)

        interval = POLL["interval"]
        jitter = POLL.get("jitter", 0)
        reply_count = 0
        stats = []
        # 内存中的完整消息池（历史 + 新读到的最新消息），用于构造上下文
        pool = {m["id"]: m for m in base_history if m.get("id")}
        # 已答过的"题目指纹"（而不是消息 id）——避免机器人重复推送同一题时重复作答
        answered_fps = set(state.get("answered_fps", []))
        log(f"已记录已答题指纹: {len(answered_fps)} 个")
        err_streak = 0
        idle_rounds = 0

        while _running:
            try:
                # 每轮先确保页面可用（被遮挡/冻结时自动恢复）
                if not page_alive(page):
                    log("检测到页面失联（可能被遮挡/冻结），正在恢复 ...", "warning")
                    page = ensure_page_ready(page, FS["target_chat_name"], ctx)
                    if not page_alive(page):
                        log("恢复失败，等待下一轮重试", "warning")
                        time.sleep(interval)
                        continue

                # 周期性把窗口提到前台，降低被冻结的概率
                bring_to_front(page)

                visible = snapshot_visible(page)
                err_streak = 0  # 本轮读取成功，清零
                # 合并到池
                for m in visible:
                    pool[m["id"]] = m

                if visible:
                    # 按真实时间序整理池
                    all_msgs = sort_by_time(pool.values())

                    # ★ 关键修复：找出"最新一道尚未回答的题"
                    #   不再用 DOM 顺序，也不再用"消息 id 未记录"，
                    #   而是用【题目指纹】判重 + 【雪花时间】定序
                    latest_only = RSTRAT.get("latest_only", True)
                    trigger, qnum, body = find_pending_question(
                        all_msgs, answered_fps, latest_only=latest_only
                    )

                    if not trigger:
                        # 没有待答题：正常，说明已答完最新题，在等机器人推新题
                        idle_rounds += 1
                        if idle_rounds == 1 or idle_rounds % 12 == 0:
                            newest = all_msgs[-1] if all_msgs else None
                            newest_desc = ""
                            if newest:
                                newest_desc = clean_text(newest["text"])[:50].replace("\n", " / ")
                            log(f"[待命] 暂无新题，持续监控中… "
                                f"(已轮询 {idle_rounds} 轮 / DOM {snapshot_visible.last_dom_count} 条 "
                                f"/ 池 {len(all_msgs)} 条 / 最新: {newest_desc})")
                            # 诊断：把最新的几条 id 打出来，便于对比页面实际内容
                            tail_ids = [f"...{m['id'][-6:]}" for m in all_msgs[-5:]]
                            log(f"       最新5条id: {tail_ids}")
                    else:
                        idle_rounds = 0

                    if trigger:
                        # 上下文窗口：触发消息之前的一段（含触发消息）
                        idx = next(
                            (i for i, x in enumerate(all_msgs) if x["id"] == trigger["id"]),
                            len(all_msgs) - 1,
                        )
                        start = max(0, idx - context_size + 1)
                        window = all_msgs[start:idx + 1]

                        # 去噪（丢弃催答题提醒等干扰）
                        window_clean = denoise(window, drop_reminders=True)
                        # 触发消息本身若未被去噪保留（如被当作提醒），补回
                        if not any(x["id"] == trigger["id"] for x in window_clean):
                            window_clean.append({
                                **trigger,
                                "text": clean_text(trigger["text"]),
                                "type": classify(clean_text(trigger["text"])),
                            })

                        current = {"id": trigger["id"], "text": trigger["text"],
                                   "qnum": qnum, "body": body}
                        context_str = build_context(
                            window_clean, max_chars=max_chars, current=current
                        )
                        log(f"[第 {reply_count + 1} 次] 待答题: 第 {qnum} 题 | "
                            f"{(body or '')[:60]}")
                        log(f"  触发消息: {clean_text(trigger['text'])[:90].replace(chr(10),' / ')}")
                        log(f"  上下文 {len(window_clean)} 条 / {len(context_str)} 字符（已去噪压缩）")
                        answer = ask_llm(context_str)

                        if not answer:
                            log("  生成失败，下轮重试", "warning")
                            time.sleep(interval)
                            continue

                        # 多行回复（如代码题）按行打印，便于在日志里核对格式
                        if "\n" in answer:
                            lines_ = answer.splitlines()
                            log(f"  答案（{len(lines_)} 行，{len(answer)} 字符）:")
                            for ln in lines_[:14]:
                                log(f"    | {ln}")
                            if len(lines_) > 14:
                                log(f"    | ...（共 {len(lines_)} 行）")
                        else:
                            log(f"  答案: {answer[:120]}")

                        fp = question_fingerprint(trigger["text"])
                        fp_s = fp_to_str(fp)
                        if ARGS.dry_run:
                            log("  [干跑] 不发送")
                            if fp_s:
                                answered_fps.add(fp_s)
                                state.setdefault("answered_fps", []).append(fp_s)
                            state["answered"].append(trigger["id"])
                            save_state(state)
                            reply_count += 1
                        else:
                            delay = random.uniform(REPLY["send_delay_min"], REPLY["send_delay_max"])
                            log(f"  等待 {delay:.1f}s 后发送 ...")
                            time.sleep(delay)
                            if send_reply(page, answer):
                                log("  已发送 ✓")
                                if fp_s:
                                    answered_fps.add(fp_s)
                                    state.setdefault("answered_fps", []).append(fp_s)
                                state["answered"].append(trigger["id"])
                                save_state(state)
                                reply_count += 1
                            else:
                                log("  发送失败", "error")

                        stats.append({
                            "qnum": qnum,
                            "question": (body or "")[:200],
                            "trigger": clean_text(trigger["text"])[:200],
                            "answer": answer[:200],
                            # 完整回复（含多行代码），便于事后核对格式
                            "answer_full": answer[:3000],
                            "lines": len(answer.splitlines()),
                        })
                        STATS_FILE.write_text(
                            json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8"
                        )

                        if ARGS.max_replies > 0 and reply_count >= ARGS.max_replies:
                            log("=" * 62)
                            log(f"已达到 {ARGS.max_replies} 次回复上限，停止。")
                            log(f"问答记录: {STATS_FILE.name}")
                            log("=" * 62)
                            break

            except Exception as e:
                if is_lost_error(e):
                    log(f"检测到页面失联: {str(e)[:90]}，尝试自动恢复 ...", "warning")
                    err_streak += 1
                    if err_streak > 10:
                        log("连续恢复失败过多，请检查浏览器窗口是否被关闭", "error")
                    try:
                        page = ensure_page_ready(page, FS["target_chat_name"], ctx)
                    except Exception:
                        pass
                else:
                    log(f"循环出错: {str(e)[:120]}", "error")

            time.sleep(interval + random.uniform(0, jitter))

        log("收到停止信号，关闭 ...")
        try:
            ctx.close()
        except Exception:
            pass
        log("已退出。")
        _shutdown_logging()


def _shutdown_logging():
    """
    退出前刷写并关闭日志 handler。

    Windows 上若不解绑，解释器清理阶段仍可能有日志写入，
    而此时文件流已被关闭 —— 会打印 "--- Logging error ---"。
    """
    try:
        for h in list(logger.handlers):
            try:
                h.flush()
            except Exception:
                pass
            try:
                h.close()
            except Exception:
                pass
            logger.removeHandler(h)

        # 同时清理根 logger 上可能存在的 handler，避免残留写入
        root = logging.getLogger()
        for h in list(root.handlers):
            try:
                h.flush()
                h.close()
            except Exception:
                pass
            root.removeHandler(h)
    except Exception:
        pass


if __name__ == "__main__":
    try:
        main()
    finally:
        _shutdown_logging()
