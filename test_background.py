"""
后台/遮挡环境下持续读取验证
============================
验证加了抗节流参数后，浏览器窗口即使不在前台，页面仍能：
  1. 持续刷新（定时器不被节流）
  2. 正常读取 DOM 消息
  3. 正常写入输入框内容

运行：
  .venv\\Scripts\\python.exe test_background.py
"""

import sys
import time
import logging
from pathlib import Path

import yaml
from playwright.sync_api import sync_playwright

BASE_DIR = Path(__file__).parent
from settings import load_config
CFG = load_config()
FS = CFG["feishu"]

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s",
                    datefmt="%H:%M:%S")
log = logging.getLogger("bgtest")

import step2_auto_reply as S


def main():
    log.info("=" * 60)
    log.info("  后台/遮挡环境持续读取验证")
    log.info("=" * 60)

    with sync_playwright() as p:
        ctx = S.launch_browser(p)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(FS["base_url"], wait_until="domcontentloaded", timeout=60000)

        if not S.wait_for_chat_list(page):
            log.error("会话列表加载失败")
            ctx.close()
            sys.exit(1)
        S.open_target_chat(page, FS["target_chat_name"])
        time.sleep(3)

        # --- 验证 1: visibilityState 被伪装成 visible ---
        log.info("[1] 检查页面可见性伪装 ...")
        vis = page.evaluate("() => ({state: document.visibilityState, hidden: document.hidden})")
        log.info(f"    visibilityState = {vis['state']}, hidden = {vis['hidden']}")
        if vis["state"] == "visible" and vis["hidden"] is False:
            log.info("    ✓ 已伪装为可见，页面不会被浏览器降级")
        else:
            log.warning("    ✗ 可见性伪装未生效")

        # --- 验证 2: 定时器不被节流 ---
        log.info("[2] 检查定时器节流情况（后台标签页定时器通常被限到 1次/分钟）...")
        interval_ms = page.evaluate(
            """() => new Promise(resolve => {
                let count = 0;
                const t0 = performance.now();
                const id = setInterval(() => { count++; }, 100);
                setTimeout(() => { clearInterval(id); resolve({count, elapsed: performance.now() - t0}); }, 3000);
            })"""
        )
        expected = 30
        got = interval_ms["count"]
        log.info(f"    3秒内 setInterval(100ms) 触发 {got} 次（正常约 {expected} 次）")
        if got >= expected * 0.7:
            log.info("    ✓ 定时器未被节流")
        else:
            log.warning(f"    ✗ 定时器疑似被节流（只触发 {got} 次）")

        # --- 验证 3: 连续 N 轮读取消息 ---
        log.info("[3] 连续 5 轮读取消息（每轮间隔 3s）...")
        for i in range(5):
            msgs = S.snapshot_visible(page)
            alive = S.page_alive(page)
            log.info(f"    第{i+1}轮: alive={alive}, 消息数={len(msgs)}")
            time.sleep(3)

        # --- 验证 4: 输入框可写入（不真的发送）---
        log.info("[4] 验证输入框写入能力（写入后清除，不发送）...")
        try:
            wrote = page.evaluate(
                """() => {
                    const boxes = document.querySelectorAll('[contenteditable="true"]');
                    const el = boxes[boxes.length - 1];
                    if (!el) return 'NO_BOX';
                    el.focus();
                    const sel = window.getSelection();
                    sel.removeAllRanges();
                    const r = document.createRange();
                    r.selectNodeContents(el);
                    sel.addRange(r);
                    document.execCommand('insertText', false, '【自检】__TEST__');
                    return el.innerText.trim();
                }"""
            )
            log.info(f"    写入结果: {wrote[:40]}")
            # 清空
            page.evaluate(
                """() => {
                    const boxes = document.querySelectorAll('[contenteditable="true"]');
                    const el = boxes[boxes.length - 1];
                    if (!el) return;
                    el.focus();
                    const sel = window.getSelection();
                    sel.removeAllRanges();
                    const r = document.createRange();
                    r.selectNodeContents(el);
                    sel.addRange(r);
                    document.execCommand('delete', false);
                }"""
            )
            if "__TEST__" in wrote:
                log.info("    ✓ 可在后台环境写入输入框")
            else:
                log.warning(f"    ✗ 写入异常: {wrote}")
        except Exception as e:
            log.error(f"    写入失败: {str(e)[:80]}")

        log.info("=" * 60)
        log.info("验证完成（未发送任何消息）")
        ctx.close()


if __name__ == "__main__":
    main()
