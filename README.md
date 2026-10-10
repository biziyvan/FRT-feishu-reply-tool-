# 飞书教学机器人 · 自动回复助手

用 Playwright 驱动真实浏览器，自动监控飞书（Lark）「AI 教学平台」类教学机器人的提问，
调用大模型生成答案并自动回复。

> **适用场景**：老师在群里发放的第三方教学机器人，会定时推送题目要求作答。
> 由于无法获得该机器人的 API 或事件订阅权限，因此采用**浏览器自动化**方案。

---

## ✨ 特性

- **真实浏览器驱动**：基于 Playwright + Edge，有头模式运行，规避无头风控
- **登录态复用**：登录一次长期有效，无需反复扫码
- **抗浏览器遮挡**：窗口被盖住 / 最小化后仍能正常读消息、发消息
- **时序正确**：用雪花 ID 还原消息真实时间序，避免"答非所问"
- **智能选题**：自动识别「提问卡片」与「反馈卡片夹带的下一题」
- **去噪省 token**：过滤催答提醒和重复推送，上下文压缩约 85%
- **严格只发一次**：基于消息计数校验，杜绝重复发送
- **失联自愈**：页面异常时自动重载 / 重建会话
- **内置自检工具**：发送链路、后台能力、失联恢复均可一键验证
- **绿色免安装版**：打包好的 exe + 图形配置向导，不装 Python 也能用（Windows）
- **作答风格可调**：代码题默认「极简、不写注释、一句说明」，改提示词即可调整

---

## 📦 环境准备

需要 Python 3.10+（当前依赖 playwright 1.63，它要求 `Requires-Python: >=3.10`）。

```bash
# 1. 创建虚拟环境
python -m venv .venv

# 2. 安装依赖（国内建议用清华镜像）
.venv\Scripts\python.exe -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple

# Linux / macOS
.venv/bin/python -m pip install -r requirements.txt
```

> 使用 Edge / Chrome 通道**无需**执行 `playwright install chromium`。
> 若想用 Playwright 自带内核，把 `config.yaml` 里 `browser.channel` 留空，
> 再执行 `.venv\Scripts\python.exe -m playwright install chromium`。

---

## 🚀 快速开始

> **不想装 Python？** Windows 用户可直接用打包好的绿色免安装版，
> 见下方「[绿色免安装版](#-绿色免安装版windows)」。需要改代码或自行部署时才走下面的源码流程。

### 1. 配置

```bash
# 复制示例配置
cp config.example.yaml config.yaml     # Windows: copy config.example.yaml config.yaml
```

编辑 `config.yaml`，填入：
- `feishu.base_url` —— 你的飞书地址（浏览器地址栏复制）
- `feishu.target_chat_name` —— 目标会话名
- `llm.api_key` —— 大模型 API Key

**不想让密钥落盘？** 改用环境变量（优先级高于配置文件）：

```bash
set FEISHU_BASE_URL=https://你的租户.feishu.cn/next/messenger/
set FEISHU_TARGET_CHAT=AI教学平台
set LLM_API_KEY=sk-xxxxxxxx
```

### 2. 抓取一次历史上下文

```bash
.venv\Scripts\python.exe fetch_history.py
```

首次运行会弹出浏览器，**扫码登录**（登录态会记住）。
该脚本滚动抓取完整对话历史，去噪后存为 `history.json`，供后续作上下文。
换课程或想刷新上下文时再跑一次即可。

### 3. 启动自动回复

```bash
# 持续运行
.venv\Scripts\python.exe step2_auto_reply.py

# 最多回复 10 次后停止
.venv\Scripts\python.exe step2_auto_reply.py --max-replies 10

# 干跑：只生成不发送（联调用）
.venv\Scripts\python.exe step2_auto_reply.py --dry-run --max-replies 5
```

按 `Ctrl+C` 停止。

---

## 🖥️ 绿色免安装版（Windows）

打包好的 `飞书自动回复助手-绿色版.zip`，**解压即用，无需安装 Python**。

1. 解压得到 `飞书自动回复助手/` 目录（路径别太深，避免超长路径问题）
2. 双击 `飞书自动回复助手.exe`
3. 首次启动弹出**配置向导**：填飞书地址、会话名、大模型 API Key
4. 点「保存并开始」，浏览器自动拉起，**扫码登录一次**即可长期使用

**几个容易踩的点**

| 现象 | 说明 |
|---|---|
| 改了配置/Prompt 没反应 | 程序读的是 **exe 同目录**下的 `config.yaml`，改别处的无效；且**改完必须重启程序** |
| 想重新配置 | 删掉同目录 `config.yaml`，重启 exe 会再弹向导 |
| 双击没反应 | 看同目录 `launcher_error.log`（窗口程序没有控制台，错误都写这里） |
| 启动报浏览器相关错误 | 多为配置目录被残留浏览器占用，程序会自动重试；见「常见问题」 |
| 运行日志 | 同目录 `auto_reply.log`（历史）/ `live.log`（实时） |

### 自行重新打包

打包环境请用 **python.org 官方安装版 Python 3.13**（自带 tkinter），
这样 playwright 能跟上最新版本、不会被锁死在旧版：

```bash
pip install playwright pyyaml requests pyinstaller   # 依赖一个都不能少
python -m PyInstaller build.spec --noconfirm --distpath dist --workpath build/wk

# 压缩：必须 cd 到 dist 再压缩，否则 zip 顶层会多一层 dist/
cd dist && zip -r ../飞书自动回复助手-绿色版.zip 飞书自动回复助手
```

> **三个必须记住的坑**
>
> 1. **别用"精简版 / 托管版"Python**：某些发行版编译时裁掉了 Tk（`Lib/tkinter` 是空的、
>    没有 `_tkinter.pyd`），`import tkinter` 直接失败。先用 `py -0p` 枚举机器上所有
>    Python、挨个测 tkinter，挑**带 Tk 且版本最新**的那个 —— 不要因为一个解释器缺 Tk
>    就断定"整个版本都缺"（这个误判会把项目逼回老 Python，进而被老 playwright 锁死）。
> 2. **依赖必须装齐**：`build.spec` 里 `collect_all(pkg)` 对缺失的包是 **`try/except pass`
>    静默跳过**的，漏装不报错，只会让打出的 exe 一启动就 `ModuleNotFoundError`。
> 3. **exe 与 `_internal/` 必须同版本配套**：跨 Python 版本升级时只能**整体替换**整个
>    目录，单独替换 exe 会启动即死（新 exe 找不到自己需要的 `pythonXXX.dll`）。
>
> **为什么强调用新版 Python**：打包会把 playwright **固化**进 exe，而用户机器上的浏览器
> 是独立自动更新的。老 playwright 迟早跟不上新浏览器（例如 Edge 155 引入"兼容层重启"后，
> playwright 1.48 启动即秒退）。用最新 Python ⇒ 用最新 playwright ⇒ 抗浏览器升级能力强。

> **打包后一定要 `ls dist/` 核对产物**：只看日志容易被过滤掉失败信息（退出码仍是 0）。

---

## 🧰 内置工具

| 脚本 | 用途 |
|---|---|
| `step1_check_login.py` | 环境验证：登录 → 定位会话 → 读消息 |
| `test_send.py` | 发送链路自检（不发送真实消息） |
| `test_multiline.py` | 多行 / 代码块输入能力自检（不发送） |
| `test_background.py` | 后台 / 遮挡环境读写能力自检 |
| `test_occlusion.py` | 页面失联后的自动恢复自检 |
| `test_code_style.py` | 代码题作答风格自检（只调模型，不启浏览器，几秒出结果） |
| `sync_prompt.py` | 把最新提示词同步到各处 `config.yaml`（改 prompt 后必用） |
| `diag_dom.py` | 对比 DOM 顺序与真实时间顺序，排查时序问题 |
| `diag_page.py` | 打印页面实况（URL / 可见性 / 消息列表） |
| `diag_browser.py` | 浏览器启动分层诊断（环境 → 配置目录 → 占用进程 → 启动实测，直接给结论） |

---

## 🏗️ 架构

```
launcher.py             图形界面（打包版入口）：配置向导 → 拉起子进程 → 实时日志
  └── runtime.py          路径解析（源码运行 / 打包运行两种形态）

step2_auto_reply.py     主程序（轮询 → 选题 → 生成 → 发送 → 状态管理）
  ├── settings.py         配置加载（环境变量 > config.yaml > example）
  ├── denoise.py          去噪 / 选题 / 上下文压缩（核心逻辑）
  └── fetch_history.py    历史抓取（滚动累积）
```

### 核心机制

**1. 只答最新一题（`reply_strategy.latest_only`）**

程序只在机器人推了新题、且是最新那道题时回复。
答完进入「待命」状态，日志会打印：

```
[待命] 暂无新题，持续监控中… (已轮询 12 轮 / DOM 9 条 / 池 110 条 / 最新: ...)
```

**看到这行说明程序正常在等新题**，不是故障。

**2. 正确识别该答哪道题**

机器人有两种消息形态：
- `AI 自适应问答` 卡片 —— 正式题目
- `学习反馈` 卡片 —— 上一题判定，**尾部会夹带下一题**

程序用「题号 + 题干核心」做**题目指纹**判重，并在上下文里显式标注
`★当前待答题目★`，避免模型串题。

**3. 用雪花 ID 还原时间序**

飞书消息 ID 是 Snowflake ID，高位即时间戳：

```python
def snowflake_ts(mid):
    return int(mid) >> 22
```

这比 DOM 顺序可靠得多 —— 虚拟滚动下 DOM 顺序常与真实时间不一致。

**4. 抗浏览器遮挡**

Chromium 会对后台标签页做三重降级（定时器节流 / 渲染冻结 / CDP 受阻）。
程序通过启动参数关闭这些降级，并伪装页面可见性：

```python
args = [
    "--disable-background-timer-throttling",
    "--disable-backgrounding-occluded-windows",
    "--disable-renderer-backgrounding",
    "--disable-features=CalculateNativeWinOcclusion,IntensiveWakeUpThrottling",
]
ctx.add_init_script("""
    Object.defineProperty(document, 'visibilityState', {get: () => 'visible'});
    Object.defineProperty(document, 'hidden', {get: () => false});
""")
```

> 实测：遮挡状态下 `setInterval(100ms)` 3 秒内仍触发 **30/30 次**
> （默认会降到 0~1 次）。

**5. 严格只发一次**

飞书输入框是 **Lark 富文本编辑器**，直接改 DOM 不会同步编辑器状态。
程序改用 CDP 原生输入层：

```python
cdp.send("Input.insertText", {"text": text})          # 写入
cdp.send("Input.dispatchKeyEvent", {"type": "keyDown", "key": "Enter", ...})  # 发送
```

且**成功判据是"消息条数 +1"，而不是"输入框是否为空"**
（后者会因富文本残留而误判，导致重复发送）。

**6. 多行内容（代码题）**

代码题回复是多行带缩进的文本。飞书输入框是 `white-space: break-spaces`，
所以用同一个 CDP `Input.insertText` 传带 `\n` 的文本即可 ——
换行会变成多个 `<div class="ace-line">` 段落，**缩进完整保留**，一次 Enter 全部发出。

```python
cdp.send("Input.insertText", {"text": "line1\n    indented\nline3"})
```

> 排查提示：Lark 会给每行末尾加一个 `\u200b`（零宽空格）锚点，真人手打多行也一样。
> **比对文本前必须剔除它**，否则会把"完全一致"误判成不一致。

---

## ⚙️ 配置项

| 配置项 | 说明 |
|---|---|
| `feishu.base_url` | 飞书网页版地址 |
| `feishu.target_chat_name` | 目标会话名 |
| `browser.channel` | `msedge` / `chrome` / 留空用自带内核 |
| `browser.headless` | **必须 false**，无头会被风控 |
| `browser.user_data_dir` | 登录态保存位置 |
| `polling.interval` | 轮询间隔（秒） |
| `polling.jitter` | 随机抖动（秒），降低风控概率 |
| `context.context_size` | 上下文消息条数上限 |
| `context.max_chars` | 上下文最大字符数（控 token） |
| `reply_strategy.latest_only` | 只答最新一题（推荐 true） |
| `llm.*` | 模型接口、密钥、模型名、提示词 |
| `llm.enable_thinking` | 思考型模型建议 false（快 50 倍） |
| `reply.send_delay_min/max` | 发送前随机延迟（秒） |

---

## ❓ 常见问题

**Q：程序日志显示"待命"，是不是坏了？**

不是。说明已答完最新题，在等机器人推新题。只有出现 `循环出错` /
`页面失联` / 进程消失才是真故障。

**Q：换了模型，回答质量变差？**

若使用 Qwen3.5 等**思考型模型**，务必设 `enable_thinking: false`，
否则响应慢（几十秒）且容易跑题。

**Q：上下文格式该怎么构造？**

必须用「**单条 user 消息打包**」，不要用 assistant / user 多轮交替 ——
后者会触发接口报错 `No user query found`，且模型容易串题。

**Q：浏览器被其他窗口盖住就不工作了？**

已在代码层面加固（见上文「抗浏览器遮挡」）。但**直接关闭浏览器窗口**
仍会导致页面丢失，程序会尝试自动恢复。

**Q：异常中断后浏览器被锁住？**

```bash
taskkill /F /IM msedge.exe        # Windows
```

**Q：飞书前端升级后不工作了？**

说明 DOM 选择器失效。运行 `diag_dom.py` 查看当前页面结构，对照调整
`step2_auto_reply.py` 里的选择器（关键选择器：`.list_items`、
`.js-message-item`、`.message-not-self`、`[contenteditable="true"]`）。

**Q：想让代码题的回答更简 / 更详细，怎么改？**

改 `system_prompt` 里的 `【代码题】` 段。默认口径是：**代码里一行注释都不写**，
不写异常处理 / 参数校验 / 辅助函数 / `main` 示例，代码块后**只跟一句**说明关键行作用。

改完用离线自检确认效果（只调模型，不启浏览器，几秒出结果）：

```bash
.venv\Scripts\python.exe test_code_style.py
```

> 注意：机器人对代码题明确要求「说明关键行的作用」，**只回代码或只回字母会被判无效作答**，
> 所以那句说明不能省，只能压缩。

**Q：改了提示词，为什么没生效？**

程序首次配置后只会读**自己目录**下的 `config.yaml`，**不再回头读 `config.example.yaml`**；
打包版读的是 **exe 同目录**。用同步工具处理：

```bash
python sync_prompt.py            # 预览要改哪些文件
python sync_prompt.py --apply    # 实际写入（自动 .bak 备份）
```

它对待两类文件不同：模板类整文件复制（保住注释），你已生成的 `config.yaml`
只替换提示词（**保住 api_key 等设置**）。改完**必须重启程序**才生效。

**Q：代码题是多行回复，会被压成一行或丢缩进吗？**

不会。实测 CDP 输入层对 `\n` 换行与缩进 **100% 保留**（输入框 `white-space: break-spaces`），
` ``` ` 也不会触发飞书自动转代码块。可用 `test_multiline.py` 自检（**不会真的发送**）。

---

## 📁 目录结构

```
feishu-auto-reply/
├── README.md
├── LICENSE                     ← MIT 开源协议
├── requirements.txt
├── 使用说明.txt                 ← 面向非技术用户的上手说明
│
├── config.example.yaml         ← 配置模板（复制为 config.yaml 使用）
├── runtime.py                  ← 路径解析（源码运行 / 打包运行两种形态）
├── settings.py                 ← 配置加载（环境变量 > config.yaml > example）
├── denoise.py                  ← 去噪 / 选题 / 上下文压缩
├── step2_auto_reply.py         ← 主程序（轮询 → 选题 → 生成 → 发送）
├── fetch_history.py            ← 历史抓取
├── launcher.py                 ← 图形界面：配置向导 + 启停 + 实时日志
├── build.spec                  ← PyInstaller 打包配置
│
├── step1_check_login.py        ← 环境验证
├── test_send.py                ← 发送链路自检
├── test_multiline.py           ← 多行 / 代码块输入自检
├── test_background.py          ← 后台能力自检
├── test_occlusion.py           ← 失联恢复自检
├── test_code_style.py          ← 代码题作答风格自检
├── sync_prompt.py              ← 提示词同步工具
├── diag_dom.py                 ← 时序诊断
└── diag_page.py                ← 页面实况诊断

运行时自动生成（已 gitignore）：
├── config.yaml                 ← 你的真实配置
├── .browser_data/              ← 登录态（勿提交）
├── history.json                ← 会话历史（勿提交）
├── answered.json               ← 已答记录
├── reply_stats.json            ← 问答记录
├── auto_reply.log / live.log   ← 运行日志
├── launcher_error.log          ← 图形界面错误日志（打包版）
└── .launcher.pid               ← 单实例保护
```

---

## ⚠️ 免责声明

本项目仅供**个人学习与日常答疑辅助**使用。

- 请遵守飞书服务条款及所在机构的规定
- **严禁**用于考试作弊、代考或任何违反学术诚信的场景
- 使用自动化工具与账号相关的风险（如被风控、被限制）由使用者自行承担
- 请勿将含 API Key 的 `config.yaml`、登录态目录 `.browser_data/`
  或聊天记录提交到公开仓库

---

## 📄 License

MIT
