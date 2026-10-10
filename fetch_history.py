"""
抓取并预处理历史对话
====================
用途：
  偶尔手动运行一次，把「AI教学平台」的完整对话历史抓下来，
  去噪后保存为 history.json，供主程序 step2 作为上下文使用。
  这样主程序日常运行**无需反复滚动翻页**。

运行：
  .venv\\Scripts\\python.exe fetch_history.py

参数：
  --scroll 30   向上滚动的轮数（默认 25，越大抓得越多）
"""

import time
import json
import argparse
from pathlib import Path

import yaml
from playwright.sync_api import sync_playwright

from denoise import clean_text, classify, denoise, sort_by_time
from runtime import DATA_DIR


BASE_DIR = DATA_DIR
HISTORY_FILE = BASE_DIR / "history.json"
RAW_FILE = BASE_DIR / "history_raw.json"


def log(*a):
    print(*a, flush=True)


from settings import load_config  # noqa: E402

CFG = load_config()
FS = CFG["feishu"]
BR = CFG["browser"]


def snapshot(page):
    """抓当前 DOM 里的全部消息。"""
    out = {}
    try:
        items = page.locator(".js-message-item")
        for i in range(items.count()):
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
                continue
    except Exception:
        pass
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scroll", type=int, default=25, help="向上滚动轮数")
    args = ap.parse_args()

    log("=" * 62)
    log("  抓取历史对话（去噪后供主程序使用）")
    log("=" * 62)

    with sync_playwright() as p:
        kwargs = dict(
            user_data_dir=str(BASE_DIR / BR["user_data_dir"]),
            headless=BR["headless"],
            slow_mo=BR["slow_mo"],
            viewport={"width": BR["viewport_width"], "height": BR["viewport_height"]},
            # Edge 155+ 必须带"跳过兼容层重启"，否则启动即秒退（TargetClosedError）
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox",
                  "--edge-skip-compat-layer-relaunch"],
        )
        if BR.get("channel"):
            kwargs["channel"] = BR["channel"]
        ctx = p.chromium.launch_persistent_context(**kwargs)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()

        page.goto(FS["base_url"], wait_until="domcontentloaded", timeout=60000)
        for _ in range(30):
            time.sleep(2)
            try:
                loc = page.locator(".list_items")
                if loc.count() > 0 and len(loc.first.inner_text()) > 20:
                    break
            except Exception:
                pass
        log("会话列表已加载")

        page.locator(f'.list_items >> text="{FS["target_chat_name"]}"').first.click(timeout=8000)
        time.sleep(4)
        log("已进入会话")

        acc = {}
        acc.update(snapshot(page))
        log(f"当前可见 {len(acc)} 条")

        try:
            page.mouse.move(BR["viewport_width"] // 2, BR["viewport_height"] // 2)
        except Exception:
            pass

        stable = 0
        for i in range(args.scroll):
            try:
                page.evaluate("""() => {
                    const el = document.querySelector('.messageList') ||
                               document.querySelector('.chatMessages') ||
                               document.querySelector('.messageContainer');
                    if (el) el.scrollTop = 0;
                }""")
                page.mouse.wheel(0, -2500)
            except Exception:
                pass
            time.sleep(0.8)
            before = len(acc)
            acc.update(snapshot(page))
            if len(acc) == before:
                stable += 1
                if stable >= 3:
                    log(f"  第 {i+1} 轮无新增，停止")
                    break
            else:
                stable = 0
                log(f"  第 {i+1} 轮: 累计 {len(acc)} 条")

        # 滚回底部
        try:
            page.evaluate("""() => {
                const el = document.querySelector('.messageList') ||
                           document.querySelector('.chatMessages') ||
                           document.querySelector('.messageContainer');
                if (el) el.scrollTop = el.scrollHeight;
            }""")
            time.sleep(1)
        except Exception:
            pass

        ctx.close()

    msgs = sorted(acc.values(), key=lambda m: int(m["id"]) if m["id"].isdigit() else 0)
    log(f"\n共抓取 {len(msgs)} 条原始消息")

    # 保存原始
    RAW_FILE.write_text(json.dumps(msgs, ensure_ascii=False, indent=2), encoding="utf-8")

    # 去噪
    cleaned = denoise(msgs, drop_reminders=False)  # 历史全量保留，但已清洗文本
    # 统计类型
    from collections import Counter
    types = Counter(m["type"] for m in cleaned)
    log("消息类型统计:")
    for t, c in types.most_common():
        log(f"  {t}: {c}")

    HISTORY_FILE.write_text(json.dumps(cleaned, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"\n去噪后 {len(cleaned)} 条，已保存到 {HISTORY_FILE.name}")
    log(f"原始数据保存在 {RAW_FILE.name}")

    # 展示最近几条
    log("\n最近 8 条（去噪后）:")
    for m in cleaned[-8:]:
        who = "机器人" if m["is_from_other"] else "我"
        log(f"  [{who}][{m['type']}] {m['text'][:80].replace(chr(10), ' / ')}")

    log("\n完成。现在可以运行 step2_auto_reply.py")


if __name__ == "__main__":
    main()
