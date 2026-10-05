# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller 打包配置
====================
构建绿色免安装版：dist/飞书自动回复助手/

关键点：
  1. playwright 包内自带 node 驱动（driver/ 目录），必须整包收集，
     否则运行时报 "Driver not found"。
  2. launcher.py 通过 --run-sub 复用同一个 exe 来跑子脚本，
     因此 step2_auto_reply.py / denoise.py 等必须以 .py 数据文件形式
     打进包内（而非编译进 exe），供运行时读取执行。
  3. tkinter 需要显式收集 tcl/tk 运行库。
"""
import os
import sys

from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_submodules

# ------------------------------------------------------------
# 项目根目录（本 spec 所在目录）
# ------------------------------------------------------------
ROOT = os.path.abspath(os.path.dirname(SPEC))  # noqa: F821 (SPEC 由 PyInstaller 注入)

datas = []
binaries = []
hiddenimports = []

# ---- 1. playwright：驱动 + 子模块 ----
pw_datas, pw_bins, pw_hidden = collect_all("playwright")
datas += pw_datas
binaries += pw_bins
hiddenimports += pw_hidden

# ---- 2. 其余第三方库 ----
for pkg in ("yaml", "requests", "urllib3", "certifi", "charset_normalizer", "idna"):
    try:
        d, b, h = collect_all(pkg)
        datas += d
        binaries += b
        hiddenimports += h
    except Exception:
        pass

# ---- 3. tkinter ----
hiddenimports += ["tkinter", "tkinter.ttk", "tkinter.filedialog",
                  "tkinter.messagebox", "tkinter.scrolledtext"]

# ---- 4. 本项目脚本：作为数据文件打进包，供 --run-sub 读取执行 ----
APP_SCRIPTS = [
    "runtime.py",
    "settings.py",
    "denoise.py",
    "step2_auto_reply.py",
    "fetch_history.py",
    "step1_check_login.py",
    "test_send.py",
    "test_background.py",
    "test_occlusion.py",
    "diag_dom.py",
    "diag_page.py",
]
for name in APP_SCRIPTS:
    p = os.path.join(ROOT, name)
    if os.path.exists(p):
        datas.append((p, "."))

# ---- 5. 配置模板 ----
# 注意：这个文件一旦漏打包，首次配置向导会因找不到模板而失败。
# launcher.py 内已内置 EMBEDDED_EXAMPLE 作为兜底，但这里仍然
# 显式检查，缺失时大声报警，避免"静默漏掉"。
example = os.path.join(ROOT, "config.example.yaml")
if os.path.exists(example):
    datas.append((example, "."))
    print(f"[build.spec] 已包含配置模板: {example}")
else:
    print(f"[build.spec] !!! 警告：找不到配置模板 {example}，"
          f"将依赖 launcher.py 的内嵌兜底配置 !!!")

# ---- 6. 使用说明 ----
for extra in ("使用说明.txt", "LICENSE"):
    p = os.path.join(ROOT, extra)
    if os.path.exists(p):
        datas.append((p, "."))


block_cipher = None

a = Analysis(  # noqa: F821
    [os.path.join(ROOT, "launcher.py")],
    pathex=[ROOT],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "numpy", "pandas", "matplotlib", "scipy", "PIL",
        "pytest", "setuptools", "pip",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)  # noqa: F821

exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="飞书自动回复助手",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,          # GUI 程序：不弹黑窗
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=os.path.join(ROOT, "app.ico") if os.path.exists(os.path.join(ROOT, "app.ico")) else None,
)

coll = COLLECT(  # noqa: F821
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="飞书自动回复助手",
)
