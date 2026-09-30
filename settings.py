"""
配置加载模块
============
统一入口，供所有脚本使用。

优先级：环境变量 > config.yaml > config.example.yaml

首次使用：
  1. 复制 config.example.yaml 为 config.yaml
  2. 填入你的飞书地址、会话名、API Key
  （或改用环境变量，避免密钥落盘 —— 见 README）

支持的环境变量：
  FEISHU_BASE_URL      飞书网页版地址
  FEISHU_TARGET_CHAT   目标会话名
  LLM_API_KEY          大模型 API Key
  LLM_BASE_URL         大模型接口地址
  LLM_MODEL            模型名
"""

import os
from pathlib import Path

import yaml

BASE_DIR = Path(__file__).parent


def load_config():
    path = BASE_DIR / "config.yaml"
    if not path.exists():
        path = BASE_DIR / "config.example.yaml"
    with open(path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}

    fs = cfg.setdefault("feishu", {})
    llm = cfg.setdefault("llm", {})

    if os.environ.get("FEISHU_BASE_URL"):
        fs["base_url"] = os.environ["FEISHU_BASE_URL"]
    if os.environ.get("FEISHU_TARGET_CHAT"):
        fs["target_chat_name"] = os.environ["FEISHU_TARGET_CHAT"]
    if os.environ.get("LLM_API_KEY"):
        llm["api_key"] = os.environ["LLM_API_KEY"]
    if os.environ.get("LLM_BASE_URL"):
        llm["base_url"] = os.environ["LLM_BASE_URL"]
    if os.environ.get("LLM_MODEL"):
        llm["model"] = os.environ["LLM_MODEL"]

    return cfg
