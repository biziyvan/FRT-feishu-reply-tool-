"""
最小化窗口下的运行能力验证
==========================
之前的 test_background.py 只验证了「窗口被遮挡」，
本脚本**真正调用 Windows API 把浏览器窗口最小化（SW_MINIMIZE）**，
然后逐个验证关键能力是否退化：

  1. 页面可见性伪装是否仍生效
  2. 定时器是否被节流（后台标签页通常被限到 1 次/分钟）
  3. CDP 是否仍能读取 DOM 消息
  4. 连续轮询是否稳定（模拟主程序的轮询循环）

运行（复用用户实跑目录的登录态）：
  FEISHU_DATA_DIR=".../飞书自动回复助手-绿色版" \
      .venv/Scripts/python.exe test_minimize.py
"""

import sys
import time
import ctypes
from ctypes import wintypes
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

sys.path.insert(0, str(Path(__file__).parent))

from playwright.sync_api import sync_playwright  # noqa: E402

from settings import load_config  # noqa: E402
import step2_auto_reply as S  # noqa: E402

CFG = load_config()
FS = CFG["feishu"]

REPORT = []


def log(msg):
    print(msg, flush=True)
    REPORT.append(msg)


# ---------------- Windows 窗口操作 ----------------
u = ctypes.windll.user32
k = ctypes.windll.kernel32
WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

SW_MINIMIZE = 6
SW_RESTORE = 9


def _proc_path(pid):
    h = k.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return ""
    buf = ctypes.create_unicode_buffer(1024)
    size = wintypes.DWORD(1024)
    ok = k.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size))
    k.CloseHandle(h)
    return buf.value if ok else ""


def find_browser_windows(only_msedge=True):
    """枚举可见顶层窗口。默认只取 Edge（避免误碰用户自己的 Chrome 窗口）。"""
    out = []

    def cb(hwnd, lparam):
        if not u.IsWindowVisible(hwnd):
            return True
        cls = ctypes.create_unicode_buffer(256)
        u.GetClassNameW(hwnd, cls, 256)
        if not cls.value.startswith("Chrome_WidgetWin"):
            return True
        n = u.GetWindowTextLengthW(hwnd)
        title = ""
        if n:
            b = ctypes.create_unicode_buffer(n + 1)
            u.GetWindowTextW(hwnd, b, n + 1)
            title = b.value
        pid = wintypes.DWORD()
        u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        pp = _proc_path(pid.value).lower()
        is_edge = "msedge" in pp
        is_chrome = "chrome.exe" in pp
        if (only_msedge and is_edge) or (not only_msedge and (is_edge or is_chrome)):
            out.append({"hwnd": hwnd, "title": title, "cls": cls.value,
                        "pid": pid.value, "exe": pp,
                        "own_visible_rect": u.IsIconic(hwnd)})
        return True

    u.EnumWindows(WNDENUMPROC(cb), 0)
    return out


def can_write_input(page):
    """往输入框写入并立即清空（不发送），验证最小化下仍能操作输入框。"""
    return page.evaluate(
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
            document.execCommand('insertText', false, '__MIN_TEST__');
            const got = el.innerText || '';
            // 立即清空
            el.focus();
            const s2 = window.getSelection();
            s2.removeAllRanges();
            const r2 = document.createRange();
            r2.selectNodeContents(el);
            s2.addRange(r2);
            document.execCommand('delete', false);
            return got.includes('__MIN_TEST__') ? 'OK' : ('GOT=' + got.slice(0, 30));
        }"""
    )


# ---------------- 页面能力测量 ----------------
def measure_visibility(page):
    return page.evaluate(
        "() => ({state: document.visibilityState, hidden: document.hidden,"
        " hasFocus: document.hasFocus()})"
    )


def measure_timer(page, ms=3000):
    """3 秒内 setInterval(100ms) 实际触发次数。正常约 ms/100 次。"""
    return page.evaluate(
        """(ms) => new Promise(resolve => {
            let c = 0;
            const t0 = performance.now();
            const id = setInterval(() => { c++; }, 100);
            setTimeout(() => { clearInterval(id);
                resolve({count: c, elapsed: performance.now() - t0}); }, ms);
        })""",
        ms,
    )


def measure_timeout(page, ms=1500):
    """setTimeout 调度精度（后台节流会显著拉长）。"""
    return page.evaluate(
        """(ms) => new Promise(resolve => {
            const t0 = performance.now();
            setTimeout(() => resolve(performance.now() - t0), ms);
        })""",
        ms,
    )


def safe(fn, tag):
    try:
        return fn(), None
    except Exception as e:
        return None, f"{tag} 失败: {str(e)[:90]}"


def snapshot(page):
    """一轮完整测量。"""
    d = {}
    vis, err = safe(lambda: measure_visibility(page), "visibility")
    d["visibility"] = vis
    d["visibility_err"] = err

    if not err:
        t, e2 = safe(lambda: measure_timer(page, 3000), "timer")
        d["timer"] = t
        d["timer_err"] = e2

    to, e3 = safe(lambda: measure_timeout(page, 1500), "timeout")
    d["timeout_ms"] = round(to) if to is not None else None
    d["timeout_err"] = e3

    msgs, e4 = safe(lambda: S.snapshot_visible(page), "snapshot_visible")
    d["msg_count"] = len(msgs) if msgs is not None else None
    d["msg_err"] = e4

    d["page_alive"] = S.page_alive(page)
    return d


def fmt(d):
    v = d.get("visibility")
    vis = f"state={v['state']} hidden={v['hidden']}" if v else f"ERR({d.get('visibility_err')})"
    t = d.get("timer")
    tim = f"{t['count']}次/{round(t['elapsed'])}ms" if t else f"ERR({d.get('timer_err')})"
    tm = d.get("timeout_ms")
    tmo = f"{tm}ms(期望1500)" if tm is not None else f"ERR({d.get('timeout_err')})"
    mc = d.get("msg_count")
    msg = str(mc) if mc is not None else f"ERR({d.get('msg_err')})"
    return (f"可见性[{vis}] | 定时器[{tim}] | setTimeout[{tmo}] | "
            f"消息[{msg}条] | alive={d['page_alive']}")


def main():
    log("=" * 68)
    log("  最小化窗口运行能力验证")
    log("=" * 68)

    with sync_playwright() as p:
        ctx = S.launch_browser(p)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(FS["base_url"], wait_until="domcontentloaded", timeout=60000)

        if not S.wait_for_chat_list(page):
            log("!! 会话列表加载失败")
            ctx.close()
            sys.exit(1)
        S.open_target_chat(page, FS["target_chat_name"])
        time.sleep(3)
        log("已进入目标会话\n")

        # ---------- 阶段 A：窗口正常 ----------
        log("---- 阶段 A：窗口正常（前台）----")
        base = snapshot(page)
        log("  " + fmt(base))
        log("")

        # ---------- 最小化（所有 Edge 窗口）----------
        wins = find_browser_windows(only_msedge=True)
        log(f"当前 Edge 窗口 {len(wins)} 个：")
        for w in wins:
            log(f"    hwnd={w['hwnd']} pid={w['pid']} title={w['title'][:40]!r}")
        if not wins:
            log("!! 未找到 Edge 窗口，无法最小化")
            ctx.close()
            sys.exit(1)

        for w in wins:                      # ← 全部最小化，确保页面所在窗口也被最小化
            u.ShowWindow(w["hwnd"], SW_MINIMIZE)
        time.sleep(1.5)
        log("\n>> 已对全部 Edge 窗口执行 SW_MINIMIZE：")
        all_iconic = True
        for w in wins:
            ic = bool(u.IsIconic(w["hwnd"]))
            all_iconic = all_iconic and ic
            log(f"    hwnd={w['hwnd']}  IsIconic={ic}")
        log(f"    => 全部窗口均已最小化: {all_iconic}")
        log("")

        # ---------- 阶段 B：最小化状态 ----------
        log("---- 阶段 B：窗口最小化 ----")
        mini = snapshot(page)
        log("  " + fmt(mini))

        # 输入框写入能力（写完立即清空，不发送）
        wrote, werr = safe(lambda: can_write_input(page), "input")
        log(f"  输入框写入: {wrote if not werr else werr}")
        log("")

        # ---------- 阶段 C：连续轮询（模拟主程序）----------
        log("---- 阶段 C：最小化状态下连续轮询 4 轮（每轮间隔 3s）----")
        polls = []
        for i in range(4):
            d = snapshot(page)
            polls.append(d)
            log(f"  第{i+1}轮: 消息={d.get('msg_count')}条 | alive={d['page_alive']} | "
                f"定时器={ (d.get('timer') or {}).get('count') }")
            time.sleep(3)
        log("")

        # ---------- 恢复窗口 ----------
        for w in wins:
            u.ShowWindow(w["hwnd"], SW_RESTORE)
        time.sleep(1)
        log(">> 已对全部 Edge 窗口执行 SW_RESTORE，IsIconic="
            + str([bool(u.IsIconic(w["hwnd"])) for w in wins]))
        after = snapshot(page)
        log("  " + fmt(after))
        log("")

        # ---------- 结论 ----------
        log("=" * 68)
        log("  结论")
        log("=" * 68)

        def tcount(d):
            return (d.get("timer") or {}).get("count") or 0

        ok_vis = bool((mini.get("visibility") or {}).get("state") == "visible")
        ok_timer = tcount(mini) >= 21          # 期望 30，留 30% 余量
        ok_dom = (mini.get("msg_count") or 0) > 0
        ok_poll = all((d.get("msg_count") or 0) > 0 and d.get("page_alive") for d in polls)
        ok_to = (mini.get("timeout_ms") or 99999) < 3000
        ok_min = all_iconic
        ok_input = (wrote == "OK") and not werr

        log(f"  [{'PASS' if ok_min else 'FAIL'}] 全部 Edge 窗口确已最小化 (IsIconic=True)")
        log(f"  [{'PASS' if ok_vis else 'FAIL'}] 最小化后可见性伪装仍生效")
        log(f"  [{'PASS' if ok_timer else 'FAIL'}] 最小化后定时器未被节流 "
            f"({tcount(mini)}/30 次)")
        log(f"  [{'PASS' if ok_to else 'FAIL'}] 最小化后 setTimeout 调度正常 "
            f"({mini.get('timeout_ms')}ms / 期望 1500ms)")
        log(f"  [{'PASS' if ok_dom else 'FAIL'}] 最小化后仍能读取 DOM 消息 "
            f"({mini.get('msg_count')} 条)")
        log(f"  [{'PASS' if ok_input else 'FAIL'}] 最小化后仍能写入输入框（不发送）")
        log(f"  [{'PASS' if ok_poll else 'FAIL'}] 最小化后连续 4 轮轮询均正常")

        all_ok = all([ok_min, ok_vis, ok_timer, ok_to, ok_dom, ok_input, ok_poll])
        log("")
        log(f"  => {'✅ 最小化状态下可正常运行' if all_ok else '⚠️ 存在退化项，见上'}")
        log("=" * 68)

        ctx.close()

    # 写报告（窗口程序/管道都可能吞输出，落盘更稳）
    out = Path(__file__).parent / "_minimize_report.txt"
    out.write_text("\n".join(REPORT), encoding="utf-8")
    print(f"\n报告已写入: {out}")


if __name__ == "__main__":
    main()
