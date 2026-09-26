# -*- coding: utf-8 -*-
"""屏幕监控接口。

    GET /api/screen/frame      一张 JPEG 帧（前端按刷新频率轮询）
    GET /api/screen/snapshot   手动截图，带时间戳文件名，用于下载
    GET /api/screen/info       分辨率 + 助手状态

取帧有两条路，自动选择：

    A. 主服务跑在会话 0（SYSTEM 常驻，为了"没人登录也能访问"）
       -> 帧来自"会话内助手"（agent.py）从用户桌面推上来的数据。
          这时本地抓屏毫无意义：会话 0 只有黑屏。

    B. 主服务自己就在交互式会话里（手动 python run.py 调试）
       -> 直接用 mss / PIL 本地抓屏，并带一个短缓存。

两者都拿不到时返回一张**占位图**，而不是让浏览器显示裂图 ——
这样屏幕卡片始终有东西可看，也看得懂"为什么没画面"。
"""

from __future__ import annotations

import io
import threading
import time

from flask import Blueprint, Response, current_app, jsonify, request, send_file

from . import live_state as state
from .agent_control import agent_required, agent_status, ensure_agent
from .wts import has_logged_on_user

bp = Blueprint("screen", __name__, url_prefix="/api")

# 本地抓屏的帧缓存（只在路径 B 用到）
_lock = threading.Lock()
_cache: dict = {"buf": None, "ts": 0.0, "width": 0, "height": 0}
MIN_CACHE_TTL = 0.25


def _quality() -> int:
    return int(current_app.config.get("PANEL", {}).get("screenshot_quality", 70))


# ---------------------------------------------------------------- 路径 B：本地抓屏
def _local_frame() -> tuple[bytes, int, int, float]:
    """本地抓屏（带短缓存），返回 (字节, 宽, 高, 时间戳)。"""
    from screen_grab import grab_jpeg

    now = time.time()
    with _lock:
        if _cache["buf"] and (now - _cache["ts"]) < MIN_CACHE_TTL:
            return _cache["buf"], _cache["width"], _cache["height"], _cache["ts"]

    buf, width, height = grab_jpeg(_quality())
    with _lock:
        _cache.update({"buf": buf, "ts": now, "width": width, "height": height})
    return buf, width, height, now


# ---------------------------------------------------------------- 占位图
_placeholder_cache: dict = {}


def _placeholder_jpeg(text: str, width: int = 960, height: int = 540) -> bytes:
    """生成一张深色占位图，避免 <img> 显示裂图。"""
    key = f"{text}|{width}x{height}"
    if key in _placeholder_cache:
        return _placeholder_cache[key]

    from PIL import Image, ImageDraw, ImageFont

    image = Image.new("RGB", (width, height), (10, 15, 28))
    draw = ImageDraw.Draw(image)
    draw.rectangle([12, 12, width - 13, height - 13], outline=(30, 42, 68), width=2)

    font = None
    for candidate in (
        r"C:\Windows\Fonts\msyh.ttc",
        r"C:\Windows\Fonts\msyhbd.ttc",
        r"C:\Windows\Fonts\simhei.ttf",
    ):
        try:
            font = ImageFont.truetype(candidate, 26)
            break
        except Exception:
            continue
    if font is None:
        font = ImageFont.load_default()

    lines = text.split("\n")
    line_height = 42
    y = (height - line_height * len(lines)) // 2
    for line in lines:
        try:
            box = draw.textbbox((0, 0), line, font=font)
            text_width = box[2] - box[0]
        except Exception:
            text_width = len(line) * 13
        draw.text(((width - text_width) // 2, y), line, fill=(142, 160, 191), font=font)
        y += line_height

    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=80)
    data = buffer.getvalue()
    _placeholder_cache[key] = data
    return data


def _jpeg_response(buf: bytes, width: int, height: int, source: str, ts: float) -> Response:
    response = Response(buf, mimetype="image/jpeg")
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
    response.headers["X-Frame-Width"] = str(width or 0)
    response.headers["X-Frame-Height"] = str(height or 0)
    response.headers["X-Frame-Time"] = f"{ts:.3f}"
    response.headers["X-Frame-Source"] = source
    return response


# ---------------------------------------------------------------- 接口
@bp.get("/screen/frame")
def frame():
    """取一帧。顺序：助手帧 -> 本地抓屏 -> 占位图。"""
    try:
        state.touch_screen()

        # --- A. 助手推上来的帧 ---
        snapshot = state.get_frame()
        if snapshot["fresh"]:
            return _jpeg_response(
                snapshot["buf"], snapshot["width"], snapshot["height"], "agent", snapshot["ts"]
            )

        # --- B. 主服务自己在交互式会话里 ---
        if not agent_required():
            buf, width, height, ts = _local_frame()
            return _jpeg_response(buf, width, height, "local", ts)

        # --- C. 需要助手：先催它，再给它一点时间把帧推上来 ---
        _ok, message = ensure_agent()

        if state.agent_alive():
            # 助手在线但当前没有新鲜帧，常见于两种情况：
            #   1. 刚打开页面，助手此前在空转（没人看就不抓屏）
            #   2. 助手刚被拉起，还在初始化
            # 这里最多等 1.6 秒，避免前端看到一闪而过的占位图。
            deadline = time.time() + 1.6
            while time.time() < deadline:
                time.sleep(0.15)
                snapshot = state.get_frame()
                if snapshot["fresh"]:
                    return _jpeg_response(
                        snapshot["buf"], snapshot["width"], snapshot["height"], "agent", snapshot["ts"]
                    )

        info = agent_status()

        report = state.get_agent_report()
        if report.get("fresh"):
            # 助手在线但抓不到屏幕 —— 最常见的是屏幕已锁定/停在安全桌面
            text = "抓不到屏幕\n" + report["message"]
        elif not has_logged_on_user():
            text = "当前没有用户登录\n（停在登录界面或已注销）\n有人登录后画面会自动出现"
        elif state.agent_alive():
            text = "助手在线，但暂时没有画面\n会话 " + str(info["active_session"]) + "\n" + message
        else:
            text = "正在拉起会话内助手…\n会话 " + str(info["active_session"]) + "\n" + message

        return _jpeg_response(_placeholder_jpeg(text), 960, 540, "placeholder", time.time())
    except Exception as exc:
        return jsonify({"ok": False, "error": f"{type(exc).__name__}: {exc}"}), 500


@bp.get("/screen/snapshot")
def snapshot():
    """手动截图：等一帧新鲜的再返回，用于下载。"""
    try:
        state.touch_screen()

        if agent_required():
            ensure_agent()
            deadline = time.time() + 4.0
            while time.time() < deadline:
                snap = state.get_frame()
                if snap["fresh"]:
                    filename = time.strftime("screenshot_%Y%m%d_%H%M%S.jpg")
                    return send_file(
                        io.BytesIO(snap["buf"]), mimetype="image/jpeg",
                        as_attachment=True, download_name=filename, max_age=0,
                    )
                time.sleep(0.2)

            info = agent_status()
            text = (
                "当前没有交互式会话\n没法截图"
                if info["active_session"] is None
                else "会话内助手未就绪\n" + (info.get("last_error") or "请稍后重试")
            )
            return send_file(
                io.BytesIO(_placeholder_jpeg(text)), mimetype="image/jpeg",
                as_attachment=True, download_name="no_session.jpg", max_age=0,
            )

        buf, _w, _h, _ts = _local_frame()
        filename = time.strftime("screenshot_%Y%m%d_%H%M%S.jpg")
        return send_file(
            io.BytesIO(buf), mimetype="image/jpeg",
            as_attachment=True, download_name=filename, max_age=0,
        )
    except Exception as exc:
        return jsonify({"ok": False, "error": f"{type(exc).__name__}: {exc}"}), 500


@bp.get("/screen/info")
def info():
    """屏幕与助手状态。前端用它解释"为什么没有画面"。"""
    try:
        snapshot = state.get_frame()
        agent = agent_status()

        fresh = bool(snapshot.get("fresh"))
        return jsonify(
            {
                "ok": True,
                "width": snapshot["width"] if fresh else 0,
                "height": snapshot["height"] if fresh else 0,
                "source": "agent" if fresh else ("local" if not agent["required"] else "none"),
                "frame_age": round(time.time() - snapshot["ts"], 1) if snapshot["ts"] else None,
                "cache_ttl": MIN_CACHE_TTL,
                "agent": agent,
                "note": "" if fresh else (
                    "当前没有交互式会话（没人登录）"
                    if agent["active_session"] is None
                    else (agent.get("last_error") or "会话内助手未就绪")
                ),
            }
        )
    except Exception as exc:
        return jsonify({"ok": False, "error": f"{type(exc).__name__}: {exc}"}), 500
