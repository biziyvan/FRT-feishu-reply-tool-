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

---

## 📦 环境准备

需要 Python 3.9+。

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

## 🧰 内置工具

| 脚本 | 用途 |
|---|---|
| `step1_check_login.py` | 环境验证：登录 → 定位会话 → 读消息 |
| `test_send.py` | 发送链路自检（不发送真实消息） |
| `test_background.py` | 后台 / 遮挡环境读写能力自检 |
| `test_occlusion.py` | 页面失联后的自动恢复自检 |
| `diag_dom.py` | 对比 DOM 顺序与真实时间顺序，排查时序问题 |
| `diag_page.py` | 打印页面实况（URL / 可见性 / 消息列表） |

---

## 🏗️ 架构

```
step2_auto_reply.py   主程序（轮询 → 选题 → 生成 → 发送 → 状态管理）
  ├── settings.py       配置加载（环境变量 > config.yaml > example）
  ├── denoise.py        去噪 / 选题 / 上下文压缩（核心逻辑）
  └── fetch_history.py  历史抓取（滚动累积）
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

---

## 📁 目录结构

```
feishu-auto-reply/
├── LICENSE               ← MIT 开源协议
├── config.example.yaml   ← 配置模板（复制为 config.yaml 使用）
├── requirements.txt
├── settings.py           ← 配置加载（环境变量 > config.yaml > example）
├── denoise.py            ← 去噪 / 选题 / 上下文压缩
├── step2_auto_reply.py   ← 主程序
├── fetch_history.py      ← 历史抓取
├── step1_check_login.py  ← 环境验证
├── test_send.py          ← 发送链路自检
├── test_background.py    ← 后台能力自检
├── test_occlusion.py     ← 失联恢复自检
├── diag_dom.py           ← 时序诊断
├── diag_page.py          ← 页面实况诊断
└── README.md

运行时自动生成（已 gitignore）：
├── config.yaml           ← 你的真实配置
├── .browser_data/        ← 登录态（勿提交）
├── history.json          ← 会话历史（勿提交）
├── answered.json         ← 已答记录
├── reply_stats.json      ← 问答记录
└── *.log
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
