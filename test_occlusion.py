"""
抗遮挡能力验证脚本
==================
模拟"浏览器被遮挡/冻结"导致页面失联的场景，验证主程序的自动恢复逻辑。

做法：
  1. 启动浏览器并进入目标会话
  2. 人为让页面失联（关闭当前页 / 制造 CDP 断开）
  3. 调用 ensure_page_ready()，看能否自动恢复
  4. 恢复后再读一次消息，确认可用

运行：
  .venv\\Scripts\\python.exe test_occlusion.py
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
log = logging.getLogger("test")

# 复用主程序的关键函数
import step2_auto_reply as S


def main():
    log.info("=" * 60)
    log.info("  抗遮挡恢复能力测试")
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
        time.sleep(2)

        # --- 正常状态 ---
        log.info("[1] 正常状态检测 ...")
        log.info(f"    page_alive = {S.page_alive(page)}")
        msgs = S.snapshot_visible(page)
        log.info(f"    读到 {len(msgs)} 条消息")

        # --- 制造"失联"：模拟遮挡/冻结 ---
        log.info("[2] 模拟浏览器被遮挡/冻结 ...")
        # 真实遮挡下页面不会关闭，只是 CDP 通信受阻、DOM 停止更新。
        # 这里用 CDP 直接让渲染进程"忙等"来模拟（不关闭页面）。
        try:
            cdp = ctx.new_cdp_session(page)
            # 启用生命周期的同时，注入一个长任务让主线程阻塞
            page.evaluate(
                """() => {
                    // 阻塞主线程 3 秒，模拟窗口被遮挡时的调度停滞
                    const t0 = Date.now();
                    while (Date.now() - t0 < 3000) {}
                }"""
            )
            log.info("    已注入主线程阻塞（3s）")
        except Exception as e:
            log.warning(f"    注入失败（不影响后续）: {str(e)[:60]}")

        time.sleep(1)
        log.info(f"    page_alive = {S.page_alive(page)}")

        # --- 尝试恢复 ---
        log.info("[3] 调用 ensure_page_ready 尝试自动恢复 ...")
        t0 = time.time()
        page = S.ensure_page_ready(page, FS["target_chat_name"], ctx)
        cost = time.time() - t0
        log.info(f"    耗时 {cost:.1f}s")

        alive = S.page_alive(page)
        log.info(f"    恢复后 page_alive = {alive}")

        if alive:
            msgs2 = S.snapshot_visible(page)
            log.info(f"    恢复后读到 {len(msgs2)} 条消息")
            if len(msgs2) > 0:
                log.info("    ✓ 恢复成功，可正常读取消息")
            else:
                log.warning("    △ 页面可见但没读到消息，可能没进对会话")
        else:
            log.error("    ✗ 恢复失败")

        log.info("=" * 60)
        ctx.close()
        log.info("测试结束")


if __name__ == "__main__":
    main()
