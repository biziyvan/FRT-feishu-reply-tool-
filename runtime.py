"""
运行环境适配模块
================
统一处理"源码运行"与"打包成 exe 运行"两种模式下的路径差异。

核心问题：
  PyInstaller 打包后，`__file__` 指向的是临时解压目录（_MEIPASS），
  程序一退出就被清空 —— 如果把它当数据目录，配置、登录态、日志、
  已答记录全都会丢失。

解决方案：
  - 代码资源（.py、config.example.yaml）→ 可能在 _MEIPASS 里
  - 数据文件（config.yaml、.browser_data、日志、history.json 等）
    → 必须放在 exe 所在目录（或用户指定目录），保证持久化

对外提供：
  APP_DIR    代码/资源所在目录（源码运行=项目根目录；frozen=_MEIPASS）
  DATA_DIR   数据写入目录（frozen=exe 同级；源码运行=项目根目录）
  IS_FROZEN  是否运行在 PyInstaller 打包环境中
"""

import os
import sys
from pathlib import Path


def _resolve_app_dir():
    """代码 / 只读资源所在目录。"""
    if getattr(sys, "frozen", False):
        # PyInstaller：资源被解压到 _MEIPASS
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parent


def _resolve_data_dir():
    """
    可写数据目录。

    优先级：
      1. 环境变量 FEISHU_DATA_DIR（用户显式指定，便于多实例/自定义位置）
      2. frozen 模式 → exe 所在目录（绿色版，随包走）
      3. 源码模式 → 项目根目录
    """
    env = os.environ.get("FEISHU_DATA_DIR")
    if env:
        p = Path(env).expanduser()
        p.mkdir(parents=True, exist_ok=True)
        return p

    if getattr(sys, "frozen", False):
        # 注意：用 sys.executable 的父目录，而不是 _MEIPASS
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


APP_DIR = _resolve_app_dir()
DATA_DIR = _resolve_data_dir()
IS_FROZEN = bool(getattr(sys, "frozen", False))


def resource_path(*parts):
    """获取只读资源路径（优先看数据目录，其次看打包资源目录）。"""
    for base in (DATA_DIR, APP_DIR):
        p = base.joinpath(*parts)
        if p.exists():
            return p
    return DATA_DIR.joinpath(*parts)


def data_path(*parts):
    """获取数据文件路径（始终在可写的数据目录下）。"""
    return DATA_DIR.joinpath(*parts)


def config_path():
    """
    返回应使用的配置文件路径。
    优先 config.yaml，不存在则回退 config.example.yaml。
    """
    user_cfg = data_path("config.yaml")
    if user_cfg.exists():
        return user_cfg
    example = resource_path("config.example.yaml")
    if example.exists():
        return example
    return user_cfg
