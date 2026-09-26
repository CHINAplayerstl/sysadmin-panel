# -*- coding: utf-8 -*-
"""全局配置：读写项目根目录下的 config.json。

首次运行会生成 config.json，并自动分配一个访问令牌（access_token）。
想改端口 / 绑定地址 / 放开命令执行，直接改 config.json 即可。
"""

from __future__ import annotations

import json
import os
import secrets

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")

# ---------------------------------------------------------------- 默认配置
DEFAULTS: dict = {
    # 监听地址：
    #   127.0.0.1  = 只有本机能访问（最安全，默认）
    #   0.0.0.0    = 局域网可访问（必须配置 access_token，否则启动时会被强制拦住）
    "host": "127.0.0.1",

    # 监听端口。用户指定 8787。
    # （3080 是 DSH 本体的 Web GUI，与本项目无关，千万别去抢。）
    # 若端口被占用，run.py 会指出占用者并给出建议。
    "port": 8787,

    # 访问令牌。前端首屏需要输入它。
    # 留空字符串 = 关闭校验（危险，等同于把整台机器交出去）。
    "access_token": "",

    # 会话内助手的共享密钥（/internal/agent/* 接口用它鉴权）。
    # 由程序自动生成并写回本文件，一般不需要动。
    "agent_secret": "",

    # 命令执行：False = 只允许下面白名单里的命令（默认，安全）
    #           True  = 允许任意 cmd 命令（危险，自己看着办）
    "allow_arbitrary_commands": False,

    # 命令白名单（仅取命令名，不含参数）
    "command_whitelist": [
        "ipconfig", "ping", "tracert", "nslookup", "netstat", "arp", "route",
        "tasklist", "systeminfo", "whoami", "hostname", "ver", "set",
        "dir", "type", "tree", "find", "findstr", "where", "chcp",
        "driverquery", "net", "vol", "date", "time", "echo", "path",
    ],

    # 文件浏览器允许访问的根目录列表。留空 = 自动使用所有存在的盘符。
    # 例：["C:\\Users\\CHNpl", "D:\\"]
    "file_roots": [],

    # 单条命令超时（秒）
    "command_timeout": 30,

    # 截屏 JPEG 质量（1-95）
    "screenshot_quality": 70,

    # 后台指标采样间隔（秒）
    "sample_interval": 1.0,

    # 图表保留的历史采样点数
    "history_points": 120,

    # Flask 调试模式（会开启自动重载，生产别开）
    "debug": False,
}


def load_config() -> dict:
    """读取 config.json；不存在则按 DEFAULTS 生成，并补一个随机访问令牌。"""
    cfg = dict(DEFAULTS)

    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                saved = json.load(f)
            if isinstance(saved, dict):
                cfg.update(saved)
        except (OSError, ValueError) as exc:  # 配置坏了不要拦着服务启动
            print(f"[warn] 读取 config.json 失败，使用默认配置：{exc}")
    else:
        # 首次运行：自动生成一个足够强的访问令牌
        cfg["access_token"] = secrets.token_urlsafe(24)
        try:
            save_config(cfg)
            print(f"[init] 已生成配置文件：{CONFIG_PATH}")
            print(f"[init] 访问令牌：{cfg['access_token']}")
        except OSError as exc:
            print(f"[warn] 写入 config.json 失败：{exc}")

    # 类型兜底，避免手改配置写错类型导致运行期崩
    cfg["port"] = int(cfg.get("port") or 3080)
    cfg["host"] = str(cfg.get("host") or "127.0.0.1")
    cfg["access_token"] = str(cfg.get("access_token") or "")

    # 会话内助手的密钥：老配置文件没有这一项时自动补上并落盘
    if not cfg.get("agent_secret"):
        cfg["agent_secret"] = secrets.token_urlsafe(24)
        try:
            save_config({"agent_secret": cfg["agent_secret"]})
            print("[init] 已为会话内助手生成 agent_secret 并写入 config.json")
        except OSError as exc:
            print(f"[warn] 写入 agent_secret 失败：{exc}")
    cfg["agent_secret"] = str(cfg["agent_secret"])
    cfg["history_points"] = max(10, int(cfg.get("history_points") or 120))
    cfg["sample_interval"] = max(0.2, float(cfg.get("sample_interval") or 1.0))
    cfg["command_timeout"] = max(1, int(cfg.get("command_timeout") or 30))
    cfg["screenshot_quality"] = min(95, max(1, int(cfg.get("screenshot_quality") or 70)))
    if not isinstance(cfg.get("command_whitelist"), list):
        cfg["command_whitelist"] = list(DEFAULTS["command_whitelist"])
    if not isinstance(cfg.get("file_roots"), list):
        cfg["file_roots"] = []

    return cfg


def save_config(cfg: dict) -> None:
    """把配置写回 config.json（保留原有的额外字段）。"""
    data = {}
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                data = json.load(f) or {}
        except (OSError, ValueError):
            data = {}
    data.update(cfg)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
