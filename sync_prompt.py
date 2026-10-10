"""把最新版 system_prompt 同步到各处的配置文件

背景
----
程序运行时读的是**自己目录下**的 config.yaml（首次配置向导生成的那份）。
在项目根改了 config.yaml 里的 system_prompt 后，已经生成的那些副本
不会自动跟着变，需要用本脚本同步。

处理方式分两类：
  - config.example.yaml（模板，带大量注释）→ 整文件复制，保持与源一致
  - 用户已生成的 config.yaml（扁平无注释）  → 只替换 llm.system_prompt，
    用户自己填的 api_key / 课程名等设置全部保留

用法
----
  python sync_prompt.py              # 只预览，不写盘
  python sync_prompt.py --apply      # 实际写入（自动生成 .bak 备份）
  python sync_prompt.py --apply D:/某处/config.yaml   # 额外指定文件
"""

import io
import shutil
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import yaml

ROOT = Path(__file__).resolve().parent
SRC_CONFIG = ROOT / "config.yaml"
SRC_EXAMPLE = ROOT / "config.example.yaml"

GREEN = ROOT / "飞书自动回复助手-绿色版"

# 模板文件：整文件复制
EXAMPLE_TARGETS = [
    ROOT / "config.example.yaml",
    ROOT / "dist" / "飞书自动回复助手" / "config.example.yaml",
    GREEN / "config.example.yaml",
]

# 用户实跑配置：只换 system_prompt
CONFIG_TARGETS = [
    GREEN / "config.yaml",
]


def load_prompt(path):
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return data["llm"]["system_prompt"].strip()


def sync_example(src, dst, apply):
    if not dst.parent.exists():
        print(f"  [跳过] 目录不存在: {dst.parent}")
        return False
    if dst.exists() and dst.read_text(encoding="utf-8") == src.read_text(encoding="utf-8"):
        print(f"  [已是最新] {dst}")
        return False
    print(f"  [替换] {dst}")
    if apply:
        if dst.exists():
            shutil.copy2(dst, str(dst) + ".bak")
        shutil.copy2(src, dst)
    return True


def sync_config(prompt, dst, apply):
    if not dst.exists():
        print(f"  [跳过] 文件不存在: {dst}")
        return False
    data = yaml.safe_load(dst.read_text(encoding="utf-8")) or {}
    old = str((data.get("llm") or {}).get("system_prompt", "")).strip()
    if old == prompt:
        print(f"  [已是最新] {dst}")
        return False
    print(f"  [更新 system_prompt] {dst}  ({len(old)} -> {len(prompt)} 字符)")
    if apply:
        shutil.copy2(dst, str(dst) + ".bak")
        data.setdefault("llm", {})["system_prompt"] = prompt
        dst.write_text(
            yaml.safe_dump(data, allow_unicode=True, sort_keys=False, default_flow_style=False),
            encoding="utf-8",
        )
    return True


def main():
    apply = "--apply" in sys.argv
    extra = [Path(a) for a in sys.argv[1:] if not a.startswith("--")]

    print("=" * 68)
    print("  system_prompt 同步工具" + ("" if apply else "   [预览模式，未写盘]"))
    print("=" * 68)

    prompt = load_prompt(SRC_CONFIG)
    print(f"源: {SRC_CONFIG}  (system_prompt {len(prompt)} 字符)")

    print("\n--- 模板文件（整文件复制）---")
    for dst in EXAMPLE_TARGETS:
        if dst == SRC_EXAMPLE:
            print(f"  [源] {dst}")
            continue
        sync_example(SRC_EXAMPLE, dst, apply)

    print("\n--- 用户实际运行配置（只换 prompt）---")
    for dst in list(CONFIG_TARGETS) + extra:
        sync_config(prompt, dst, apply)

    print("=" * 68)
    if not apply:
        print("预览完成。加 --apply 才会真正写入（会自动 .bak 备份）。")


if __name__ == "__main__":
    main()
