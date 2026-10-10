"""
第 1 步：登录验证 + 会话定位 + 消息读取
========================================
用真实的飞书 DOM 选择器（已实测验证）：
  - 会话列表项   : .list_items 内的文本
  - 单条消息     : .js-message-item （带 id 属性，可作为消息唯一键）
  - 消息方向     : class 含 "message-not-self" = 对方发的
  - 输入框       : .zone-container / [contenteditable="true"]

运行：
  .venv\\Scripts\\python.exe step1_check_login.py

说明：
  - 第一次运行需要在弹出的浏览器里扫码登录
  - 登录态保存在 ./.browser_data，之后无需重复登录
  - 输出同时打印到屏幕并写入 step1.log
"""

import sys
import time
import logging
from pathlib import Path

import yaml
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout


BASE_DIR = DATA_DIR
LOG_FILE = BASE_DIR / "step1.log"
SHOT_DIR = BASE_DIR / "debug_shots"
SHOT_DIR.mkdir(exist_ok=True)


# ---------------- 日志 ----------------
logger = logging.getLogger("feishu")
logger.setLevel(logging.DEBUG)
_fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", "%H:%M:%S")
_fh = logging.FileHandler(LOG_FILE, mode="w", encoding="utf-8")
_fh.setFormatter(_fmt)
_sh = logging.StreamHandler(sys.stdout)
_sh.setFormatter(_fmt)
logger.addHandler(_fh)
logger.addHandler(_sh)


def log(msg, level="info"):
    getattr(logger, level)(msg)


# ---------------- 配置 ----------------
from settings import load_config  # noqa: E402
from runtime import DATA_DIR  # noqa: E402

CFG = load_config()
FS = CFG["feishu"]
BR = CFG["browser"]


def launch_browser(p):
    """启动持久化浏览器上下文。"""
    kwargs = dict(
        user_data_dir=str(BASE_DIR / BR["user_data_dir"]),
        headless=BR["headless"],
        slow_mo=BR["slow_mo"],
        viewport={
            "width": BR["viewport_width"],
            "height": BR["viewport_height"],
        },
        args=[
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",
            # Edge 155+ 必须带"跳过兼容层重启"，否则启动即秒退（TargetClosedError）
            "--edge-skip-compat-layer-relaunch",
        ],
    )
    if BR.get("channel"):
        kwargs["channel"] = BR["channel"]
    return p.chromium.launch_persistent_context(**kwargs)


def wait_for_login(page, timeout=180):
    """等待页面离开登录页，返回是否登录成功。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        url = page.url.lower()
        if "accounts.feishu" not in url and "login" not in url:
            time.sleep(2)
            if "accounts.feishu" not in page.url.lower() and "login" not in page.url.lower():
                return True
        time.sleep(1.5)
    return False


def wait_for_chat_list(page, timeout=60):
    """等待左侧会话列表渲染出内容。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if page.locator(".list_items").count() > 0:
                # 再确认里面有真实文字
                if len(page.locator(".list_items").first.inner_text()) > 20:
                    return True
        except Exception:
            pass
        time.sleep(2)
    return False


def click_chat(page, name):
    """点击指定会话。"""
    # 已实测可用：.list_items 内按文本定位
    try:
        loc = page.locator(f'.list_items >> text="{name}"')
        if loc.count() > 0:
            loc.first.click(timeout=8000)
            return True
    except Exception as e:
        log(f"  .list_items 定位失败: {str(e)[:100]}", "debug")

    # 兜底方案
    for sel in [f'text="{name}"', f'[title="{name}"]']:
        try:
            loc = page.locator(sel)
            if loc.count() > 0:
                loc.first.click(timeout=8000)
                return True
        except Exception:
            continue
    return False


def read_messages(page, limit=15):
    """
    读取当前会话最近的消息。
    返回: [{"id": 消息ID, "is_from_other": 是否对方发的, "text": 文本}]
    """
    msgs = []
    try:
        items = page.locator(".js-message-item")
        count = items.count()
        if count == 0:
            return []
        start = max(0, count - limit)
        for i in range(start, count):
            el = items.nth(i)
            try:
                mid = el.get_attribute("id") or ""
                cls = el.get_attribute("class") or ""
                is_other = "message-not-self" in cls
                text = el.inner_text().strip()
                if text:
                    msgs.append({"id": mid, "is_from_other": is_other, "text": text})
            except Exception:
                continue
    except Exception as e:
        log(f"  读取消息出错: {e}", "warning")
    return msgs


def main():
    log("=" * 62)
    log("  飞书自动回复 · 第 1 步：登录 + 会话定位 + 消息读取")
    log("=" * 62)
    log(f"  目标会话 : {FS['target_chat_name']}")
    log(f"  浏览器   : {BR.get('channel')}  无头={BR['headless']}")
    log("=" * 62)

    with sync_playwright() as p:
        log("启动浏览器 ...")
        ctx = launch_browser(p)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()

        log("[1/4] 打开飞书 ...")
        page.goto(FS["base_url"], wait_until="domcontentloaded", timeout=60000)
        time.sleep(4)
        log(f"  URL: {page.url}")

        # 登录判断
        if "accounts.feishu" in page.url or "login" in page.url.lower():
            log("[2/4] 需要登录 —— 请在弹出的浏览器窗口扫码登录（最多等 3 分钟）")
            if not wait_for_login(page):
                log("  登录超时", "error")
                ctx.close()
                sys.exit(1)
            log("  登录成功")
            if "messenger" not in page.url:
                page.goto(FS["base_url"], wait_until="domcontentloaded", timeout=60000)
        else:
            log("[2/4] 已登录")

        # 等待列表
        log("[3/4] 等待会话列表加载 ...")
        if not wait_for_chat_list(page):
            log("  列表加载超时", "error")
            page.screenshot(path=str(SHOT_DIR / "list_timeout.png"))
            ctx.close()
            sys.exit(1)
        log("  列表已加载")

        target = FS["target_chat_name"]
        log(f"  点击会话「{target}」...")
        if not click_chat(page, target):
            log(f"  未找到会话「{target}」", "error")
            page.screenshot(path=str(SHOT_DIR / "chat_not_found.png"))
            ctx.close()
            sys.exit(1)
        time.sleep(4)
        log("  已进入会话")

        # 读取消息
        log("[4/4] 读取最近消息 ...")
        msgs = read_messages(page, limit=15)
        if not msgs:
            log("  没有读到消息", "warning")
        else:
            log(f"  共读到 {len(msgs)} 条：")
            log("  " + "-" * 58)
            for m in msgs:
                who = "机器人→我" if m["is_from_other"] else "我→机器人"
                preview = m["text"].replace("\n", " / ")
                if len(preview) > 90:
                    preview = preview[:90] + " ..."
                log(f"  [{who}] id={m['id'][-8:] if m['id'] else 'N/A'}")
                log(f"           {preview}")
            log("  " + "-" * 58)

        log("\n第 1 步验证完成。")
        log(f"  日志: {LOG_FILE}")
        log("  浏览器 15 秒后自动关闭 ...")
        time.sleep(15)
        ctx.close()
        log("结束。")


if __name__ == "__main__":
    main()
