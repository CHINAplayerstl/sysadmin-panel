# -*- coding: utf-8 -*-
"""给"会话内助手"用的内部接口（不是给浏览器用的）。

    GET  /internal/agent/poll        助手来问：现在要不要帧 / 剪贴板
    POST /internal/agent/frame       助手推一帧 JPEG 上来
    POST /internal/agent/clipboard   助手推剪贴板文本上来

安全把关
--------
这几个接口【不走】前端的令牌校验（助手没有面板令牌），所以自己守两道门：
    1. 只接受来自回环地址的连接 —— 局域网上的人碰不到
    2. 必须带 X-Agent-Secret 头，且与 config.json 里的 agent_secret 一致（常数时间比较）
"""

from __future__ import annotations

import hmac

from flask import Blueprint, current_app, jsonify, request

from . import live_state as state
from .wts import active_session_id, current_session_id

bp = Blueprint("agent", __name__, url_prefix="/internal/agent")

LOOPBACK = {"127.0.0.1", "::1"}


def _authorized() -> bool:
    if (request.remote_addr or "") not in LOOPBACK:
        return False

    expected = str(current_app.config.get("PANEL", {}).get("agent_secret") or "")
    if not expected:
        return False

    provided = request.headers.get("X-Agent-Secret") or ""
    return hmac.compare_digest(provided, expected)


@bp.before_request
def _guard():  # type: ignore[unused-ignore]
    if not _authorized():
        return jsonify({"ok": False, "error": "助手接口仅限本机回环，且需要正确的 agent_secret"}), 403
    return None


@bp.get("/poll")
def poll():
    """助手每次轮询都会走这里，同时顺便上报自己还活着。"""
    try:
        raw_session = request.headers.get("X-Agent-Session")
        try:
            session = int(raw_session) if raw_session is not None else None
        except ValueError:
            session = None

        state.mark_agent_seen(session)

        need_frame = state.want_frame()
        need_clipboard = state.want_clipboard()

        cfg = current_app.config.get("PANEL", {})
        return jsonify(
            {
                "ok": True,
                "need_frame": need_frame,
                "need_clipboard": need_clipboard,
                # 有人看就按 500ms 抓（支撑 2 帧/秒），没人看就拉长间隔空转
                "interval_ms": 500 if need_frame else 1500,
                "quality": int(cfg.get("screenshot_quality", 70)),
                "server_session": current_session_id(),
                "active_session": active_session_id(),
            }
        )
    except Exception as exc:
        return jsonify({"ok": False, "error": f"{type(exc).__name__}: {exc}"}), 500


@bp.post("/frame")
def frame():
    """接收一帧 JPEG（原始字节，不做 base64，省一次编解码）。"""
    try:
        payload = request.get_data(cache=False)
        if not payload:
            return jsonify({"ok": False, "error": "空帧"}), 400

        try:
            width = int(request.headers.get("X-Frame-Width") or 0)
            height = int(request.headers.get("X-Frame-Height") or 0)
        except ValueError:
            width = height = 0

        raw_session = request.headers.get("X-Agent-Session")
        try:
            session = int(raw_session) if raw_session is not None else None
        except ValueError:
            session = None

        state.put_frame(payload, width, height, session)

        # 同时记下助手的 PID，便于排查
        info = state.agent_info()
        if not info.get("pid"):
            state.set_agent(0, session)

        return jsonify({"ok": True, "bytes": len(payload), "width": width, "height": height})
    except Exception as exc:
        return jsonify({"ok": False, "error": f"{type(exc).__name__}: {exc}"}), 500


@bp.post("/clipboard")
def clipboard():
    """接收剪贴板文本。"""
    try:
        data = request.get_json(silent=True) or {}
        text = str(data.get("text") or "")
        state.put_clipboard(text)
        return jsonify({"ok": True, "length": len(text)})
    except Exception as exc:
        return jsonify({"ok": False, "error": f"{type(exc).__name__}: {exc}"}), 500


@bp.post("/log")
def log():
    """助手回传异常（例如"屏幕已锁定抓不到"），让网页上能显示原因。"""
    try:
        data = request.get_json(silent=True) or {}
        message = str(data.get("message") or "").strip()
        if message:
            state.set_agent_report(message)
        return jsonify({"ok": True})
    except Exception as exc:
        return jsonify({"ok": False, "error": f"{type(exc).__name__}: {exc}"}), 500