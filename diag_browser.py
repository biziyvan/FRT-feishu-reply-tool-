# -*- coding: utf-8 -*-
"""浏览器启动诊断工具。

用途：程序报 `TargetClosedError: Target page, context or browser has been closed`
（或"浏览器启动失败"）时，用来定位到底卡在哪一层。

用法：
    .venv\\Scripts\\python.exe diag_browser.py            # 用默认配置目录
    .venv\\Scripts\\python.exe diag_browser.py <配置目录>  # 指定目录

它会依次检查：
  1. 运行环境（Python / playwright 版本、Edge 版本）
  2. 配置目录状态（是否存在、有无残留锁文件）
  3. 是否有进程占用该配置目录
  4. 用「全新临时目录」启动一次 → 判断 Edge 本身是否正常
  5. 用「实际配置目录」启动一次 → 判断是否是该目录的问题
"""
import io
import os
import sys
import tempfile
import traceback
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

ARGS = [
    "--disable-background-timer-throttling",
    "--disable-backgrounding-occluded-windows",
    "--disable-renderer-backgrounding",
    # Edge 155+ 必须带"跳过兼容层重启"，否则启动即秒退（TargetClosedError）
    "--edge-skip-compat-layer-relaunch",
]
DEFAULT_PROFILE = Path(
    r"D:\bzy\飞书自动回复\飞书自动回复助手-绿色版\飞书自动回复助手\.browser_data"
)


def hr(title):
    print()
    print("=" * 62)
    print(title)
    print("=" * 62)


def check_env():
    hr("1. 运行环境")
    print(f"Python     : {sys.version.split()[0]}  frozen={getattr(sys, 'frozen', False)}")
    try:
        from importlib.metadata import version
        print(f"playwright : {version('playwright')}")
    except Exception as e:
        print(f"playwright 版本读取失败: {e}")
    try:
        import playwright
        print(f"包路径     : {playwright.__file__}")
    except Exception as e:
        print(f"导入 playwright 失败: {e}")
    edge = Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")
    print(f"Edge 存在  : {edge.exists()}  ({edge})")


def check_profile(ud):
    hr("2. 配置目录状态")
    print(f"目录: {ud}")
    if not ud.exists():
        print("  -> 不存在（首次运行会被自动创建，属正常）")
        return
    items = list(ud.glob("*"))
    print(f"  条目数: {len(items)}")
    if not items:
        print("  -> **目录为空：说明浏览器从未成功写入过，登录态不存在**")
    locks = [n for n in ("SingletonLock", "SingletonCookie", "SingletonSocket", "lockfile")
             if list(ud.glob(n))]
    print(f"  残留锁文件: {locks if locks else '无'}")
    for k in ("Last Version", "Local State"):
        p = ud / k
        if p.exists():
            print(f"  {k}: 存在")


def check_processes(ud):
    hr("3. 是否有进程占用该配置目录")
    key = str(ud).lower()
    try:
        import subprocess
        ps = (
            "$k='" + key.replace("'", "''") + "';"
            "Get-CimInstance Win32_Process -Filter \"Name='msedge.exe' or Name='chrome.exe'\" "
            "-ErrorAction SilentlyContinue | "
            "Where-Object { $_.CommandLine -and $_.CommandLine.ToLower().Contains($k) } | "
            "Select-Object -ExpandProperty ProcessId"
        )
        out = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                             capture_output=True, timeout=60)
        txt = out.stdout.decode("utf-8", "replace").strip()
        print(f"  占用该目录的浏览器进程 PID: {txt or '无'}")
        if txt:
            print("  -> **有占用！这就是 TargetClosedError 的直接原因**")
    except Exception as e:
        print(f"  检查失败: {e}")


def try_launch(label, ud):
    from playwright.sync_api import sync_playwright
    print(f"  [{label}] 目录={ud}")
    try:
        with sync_playwright() as p:
            ctx = p.chromium.launch_persistent_context(
                str(ud), channel="msedge", headless=False, args=ARGS,
                viewport={"width": 1440, "height": 900},
            )
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            page.goto("about:blank")
            print(f"     -> 成功  页面数={len(ctx.pages)}  Edge={ctx.browser.version if ctx.browser else 'n/a'}")
            ctx.close()
            return True
    except Exception as e:
        print(f"     -> 失败  {type(e).__name__}: {str(e)[:250]}")
        return False


def main():
    ud = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else DEFAULT_PROFILE

    check_env()
    check_profile(ud)
    check_processes(ud)

    hr("4/5. 启动测试")
    ok_new = try_launch("全新临时目录（测 Edge 本身）", tempfile.mkdtemp(prefix="diag_"))
    ok_real = try_launch("实际配置目录（测你的环境）", ud)

    hr("结论")
    if ok_new and ok_real:
        print("两者都成功 -> 现在一切正常，直接重试程序即可。")
        print("若刚才失败过，通常是被上一个浏览器实例短暂占用，或 Edge 正在自动更新。")
    elif ok_new and not ok_real:
        print("新目录成功、实际目录失败 -> **问题在该配置目录**：")
        print("  1) 确认程序没有在别处运行（一个配置目录只能被一个浏览器占用）")
        print("  2) 结束所有 msedge 进程后重试")
        print("  3) 仍失败可把 .browser_data 改名备份（会需要重新扫码登录）")
    elif not ok_new:
        print("全新目录也失败 -> **问题在 Edge 本身**：")
        print("  · 手动打开一次 Edge 看能否启动（可能正在自动更新/需重启）")
        print("  · 重启电脑后再试")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
