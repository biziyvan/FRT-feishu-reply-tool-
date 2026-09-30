"""
页面实况诊断
============
连接当前正在运行的浏览器，dump 出：
  1. 页面 URL / 可见性状态
  2. DOM 里所有 .js-message-item（id / 方向 / 文本前 80 字）
  3. 程序会如何排序、选中哪条
  4. 滚动位置 / 消息列表容器情况

运行（程序正在跑的时候另开一个终端）：
  .venv\\Scripts\\python.exe diag_page.py
"""

import sys
import time
import logging
from pathlib import Path

import yaml
from playwright.sync_api import sync_playwright

BASE_DIR = Path(__file__).parent
sys.path.insert(0, str(BASE_DIR))
from settings import load_config
CFG = load_config()
FS = CFG["feishu"]
BR = CFG["browser"]

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s",
                    datefmt="%H:%M:%S")
log = logging.getLogger("diag")

import step2_auto_reply as S
from denoise import sort_by_time, clean_text, find_pending_question, question_fingerprint


def main():
    log.info("连接浏览器（复用已有窗口）...")
    with sync_playwright() as p:
        # 直接连已运行的 persistent context
        ctx = p.chromium.launch_persistent_context(
            user_data_dir=str(BASE_DIR / BR["user_data_dir"]),
            headless=False,
            channel=BR.get("channel"),
            args=["--no-sandbox"],
        )
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        log.info(f"页面 URL: {page.url}")

        vis = page.evaluate("() => ({s: document.visibilityState, h: document.hidden})")
        log.info(f"可见性: state={vis['s']}, hidden={vis['h']}")

        # 滚动容器信息
        try:
            info = page.evaluate(
                """() => {
                    const out = {};
                    out.bodyLen = document.body.innerText.length;
                    out.itemCount = document.querySelectorAll('.js-message-item').length;
                    // 找滚动容器
                    for (const sel of ['.messageList', '.chatMessages', '.messageContainer', '[class*="messageList"]']) {
                        const el = document.querySelector(sel);
                        if (el) {
                            out.scrollSel = sel;
                            out.scrollTop = el.scrollTop;
                            out.scrollHeight = el.scrollHeight;
                            out.clientHeight = el.clientHeight;
                            out.atBottom = (el.scrollHeight - el.scrollTop - el.clientHeight) < 30;
                            break;
                        }
                    }
                    return out;
                }"""
            )
            log.info(f"滚动容器: {info.get('scrollSel')}  "
                     f"scrollTop={info.get('scrollTop')} / "
                     f"scrollHeight={info.get('scrollHeight')} / "
                     f"clientHeight={info.get('clientHeight')}")
            log.info(f"是否在底部: {info.get('atBottom')}   DOM 消息条数={info.get('itemCount')}")
        except Exception as e:
            log.warning(f"读取滚动信息失败: {e}")

        # dump 所有 DOM 消息
        msgs = S.snapshot_visible(page)
        log.info(f"\n程序读到 {len(msgs)} 条消息（按程序认定的时间序）:")
        for m in msgs:
            who = "机器人" if m["is_from_other"] else "  我  "
            t = clean_text(m["text"])[:70].replace("\n", " / ")
            log.info(f"  {who} | id=...{m['id'][-8:]} | {t}")

        # 程序会选中哪条
        trig, qn, body = find_pending_question(msgs, set(), latest_only=True)
        log.info("\n程序判定:")
        if trig:
            log.info(f"  应答: 第{qn}题 | {(body or '')[:60]}")
            log.info(f"  来源消息: {clean_text(trig['text'])[:80]}")
        else:
            log.info("  无待答题")

        ctx.close()
        log.info("诊断完成")


if __name__ == "__main__":
    main()
