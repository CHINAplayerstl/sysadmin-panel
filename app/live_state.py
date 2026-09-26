# -*- coding: utf-8 -*-
"""主服务与"会话内助手"之间共享的运行时状态。

为什么单独开一个模块：
    api_agent（助手推数据进来）和 api_screen / api_misc（前端取数据出去）
    需要读写同一份状态。放在任一方的模块里都会造成循环导入，所以抽出来。
"""

from __future__ import annotations

import threading
import time

_lock = threading.Lock()

# 助手推上来的最新一帧
frame: dict = {"buf": None, "ts": 0.0, "width": 0, "height": 0, "session": None}

# 助手推上来的剪贴板
clipboard: dict = {"text": "", "ts": 0.0}

# 助手的存活信息
agent: dict = {"pid": 0, "session": None, "last_seen": 0.0, "spawned_at": 0.0, "last_error": ""}

# 助手主动回传的最近一条异常（例如"屏幕已锁定抓不到"）
agent_report: dict = {"message": "", "ts": 0.0}

# 前端最近一次请求截屏 / 剪贴板的时间 —— 助手据此决定要不要干活（没人看就别抓屏）
last_screen_request: float = 0.0
last_clipboard_request: float = 0.0

# 帧的新鲜度阈值（秒）：超过就认为助手已经不在了
FRAME_TTL = 4.0
CLIPBOARD_TTL = 15.0
AGENT_TTL = 12.0

# 前端停止观看多久之后就让助手歇着
IDLE_AFTER = 8.0


def touch_screen() -> None:
    global last_screen_request
    with _lock:
        last_screen_request = time.time()


def touch_clipboard() -> None:
    global last_clipboard_request
    with _lock:
        last_clipboard_request = time.time()


def want_frame() -> bool:
    with _lock:
        return (time.time() - last_screen_request) < IDLE_AFTER


def want_clipboard() -> bool:
    with _lock:
        return (time.time() - last_clipboard_request) < IDLE_AFTER


def put_frame(buf: bytes, width: int, height: int, session: int | None) -> None:
    with _lock:
        frame.update({"buf": buf, "ts": time.time(), "width": width, "height": height, "session": session})
        agent["last_seen"] = time.time()


def put_clipboard(text: str) -> None:
    with _lock:
        clipboard.update({"text": text, "ts": time.time()})
        agent["last_seen"] = time.time()


def get_frame() -> dict:
    with _lock:
        snapshot = dict(frame)
    snapshot["fresh"] = bool(snapshot["buf"]) and (time.time() - snapshot["ts"]) < FRAME_TTL
    return snapshot


def get_clipboard() -> dict:
    with _lock:
        snapshot = dict(clipboard)
    snapshot["fresh"] = (time.time() - snapshot["ts"]) < CLIPBOARD_TTL
    return snapshot


def mark_agent_seen(session: int | None = None) -> None:
    with _lock:
        agent["last_seen"] = time.time()
        if session is not None:
            agent["session"] = session


def set_agent_report(message: str) -> None:
    """记录助手回传的异常。"""
    with _lock:
        agent_report.update({"message": message, "ts": time.time()})
        agent["last_seen"] = time.time()


def get_agent_report() -> dict:
    with _lock:
        snapshot = dict(agent_report)
    # 超过 30 秒的旧汇报不再展示，免得画面恢复了还挂着旧错误
    snapshot["fresh"] = bool(snapshot["message"]) and (time.time() - snapshot["ts"]) < 30
    return snapshot


def set_agent(pid: int, session: int | None, error: str = "") -> None:
    with _lock:
        agent["pid"] = int(pid or 0)
        agent["session"] = session
        agent["spawned_at"] = time.time()
        agent["last_error"] = error or ""


def agent_alive() -> bool:
    with _lock:
        return (time.time() - agent["last_seen"]) < AGENT_TTL


def agent_info() -> dict:
    with _lock:
        snapshot = dict(agent)
    snapshot["alive"] = (time.time() - snapshot["last_seen"]) < AGENT_TTL
    return snapshot
