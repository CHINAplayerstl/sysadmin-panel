# -*- coding: utf-8 -*-
"""抓屏并编码成 JPEG 的共享实现。

被两处使用：
    app/api_screen.py —— 面板自己就跑在交互式会话里时的本地抓屏（开发/单用户场景）
    agent.py          —— 面板跑在 SYSTEM 会话 0 时，由"会话内助手"负责抓屏

优先用 mss（实测约 45 ms/帧），装不上就退回 PIL.ImageGrab（实测约 530 ms/帧）。
"""

from __future__ import annotations

import io

# mss 可用性只探测一次
_mss_state: dict = {"checked": False, "ok": False}


def _mss_available() -> bool:
    if not _mss_state["checked"]:
        try:
            import mss  # noqa: F401

            _mss_state["ok"] = True
        except Exception:
            _mss_state["ok"] = False
        _mss_state["checked"] = True
    return bool(_mss_state["ok"])


def _grab_with_mss():
    """用 mss 抓整块虚拟桌面（多显示器也一次抓全），转成 PIL Image。"""
    import mss
    from PIL import Image

    with mss.mss() as sct:
        # monitors[0] 是所有显示器的并集，等价于"整块虚拟桌面"
        monitor = sct.monitors[0]
        shot = sct.grab(monitor)
        return Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")


def _grab_with_pil():
    """用 PIL.ImageGrab 抓屏，最省事的兜底方案。"""
    from PIL import ImageGrab

    image = ImageGrab.grab(all_screens=True)
    if image.mode != "RGB":
        image = image.convert("RGB")
    return image


def grab_jpeg(quality: int = 70) -> tuple[bytes, int, int]:
    """抓屏并编码成 JPEG，返回 (字节流, 宽, 高)。"""
    image = None

    if _mss_available():
        try:
            image = _grab_with_mss()
        except Exception:
            # mss 在个别多屏 / 远程桌面场景会失败，降级即可
            image = None

    if image is None:
        image = _grab_with_pil()

    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=int(quality), optimize=False)
    width, height = image.size
    return buffer.getvalue(), width, height
