# -*- coding: utf-8 -*-
"""电源控制接口。

    POST /api/power/shutdown    关机（可延时，单位秒）
    POST /api/power/reboot      重启（可延时）
    POST /api/power/cancel      撤销已下达的关机/重启
    POST /api/power/lock        锁屏
    POST /api/power/hibernate   休眠
    POST /api/power/sleep       睡眠（顺带提供）
    GET  /api/power/status      查询是否有待执行的电源操作

为什么全走系统命令：
    这些操作在 Windows 上都有现成的、经过签名的入口，比自己去调
    SetSuspendState / InitiateSystemShutdownEx 的 P/Invoke 稳得多：

        shutdown.exe /s /t <秒>      关机，系统自带倒计时
        shutdown.exe /r /t <秒>      重启
        shutdown.exe /a              撤销
        rundll32 user32.dll,LockWorkStation           锁屏
        shutdown.exe /h              休眠（未启用休眠时会报错，错误原样回显）
        rundll32 powrprof.dll,SetSuspendState 0,1,0   睡眠

待执行状态：
    Windows 没有"查询是否有待执行关机"的干净接口，所以这里自己记一份，
    同时 /cancel 会调用 shutdown /a —— 若系统本来就没有待执行任务，会返回错误提示。
"""

from __future__ import annotations

import threading
import time

from flask import Blueprint, jsonify, request

from .utils import run_process

bp = Blueprint("power", __name__, url_prefix="/api")

# 自己维护的"待执行电源操作"状态
_lock = threading.Lock()
_state: dict = {"action": None, "at": None, "delay": 0, "due_at": None}

# 允许的最大延时（秒）：24 小时
MAX_DELAY = 24 * 3600


def _set_state(action: str | None, delay: int = 0) -> None:
    with _lock:
        now = time.time()
        _state.update(
            {
                "action": action,
                "at": now if action else None,
                "delay": delay,
                "due_at": (now + delay) if action else None,
            }
        )


def _run(args: list[str], timeout: int = 15) -> tuple[int, str, str]:
    code, out, err = run_process(args, timeout=timeout)
    return code, out.strip(), err.strip()


def _parse_delay(payload: dict) -> int:
    try:
        delay = int(payload.get("delay") or 0)
    except (TypeError, ValueError):
        delay = 0
    return max(0, min(MAX_DELAY, delay))


@bp.post("/power/shutdown")
def shutdown():
    """延时关机。delay=0 表示立刻关机。"""
    try:
        payload = request.get_json(silent=True) or {}
        delay = _parse_delay(payload)

        code, out, err = _run(["shutdown.exe", "/s", "/t", str(delay)])
        if code != 0:
            return jsonify({"ok": False, "error": err or out or f"shutdown 返回码 {code}"}), 500

        _set_state("shutdown", delay)
        return jsonify({"ok": True, "action": "shutdown", "delay": delay, "message": f"已下达关机命令，{delay} 秒后执行"})
    except Exception as exc:
        return jsonify({"ok": False, "error": f"{type(exc).__name__}: {exc}"}), 500


@bp.post("/power/reboot")
def reboot():
    """延时重启。"""
    try:
        payload = request.get_json(silent=True) or {}
        delay = _parse_delay(payload)

        code, out, err = _run(["shutdown.exe", "/r", "/t", str(delay)])
        if code != 0:
            return jsonify({"ok": False, "error": err or out or f"shutdown 返回码 {code}"}), 500

        _set_state("reboot", delay)
        return jsonify({"ok": True, "action": "reboot", "delay": delay, "message": f"已下达重启命令，{delay} 秒后执行"})
    except Exception as exc:
        return jsonify({"ok": False, "error": f"{type(exc).__name__}: {exc}"}), 500


@bp.post("/power/cancel")
def cancel():
    """撤销待执行的关机/重启。"""
    try:
        code, out, err = _run(["shutdown.exe", "/a"])
        had_state = _state.get("action") is not None
        _set_state(None)

        if code != 0:
            # 系统里本来就没有待执行任务时，这里会报错，属于正常情况
            return jsonify(
                {
                    "ok": False,
                    "error": (err or out or "没有待执行的关机/重启任务").strip(),
                    "had_pending": had_state,
                }
            ), 400

        return jsonify({"ok": True, "message": "已撤销待执行的关机/重启"})
    except Exception as exc:
        return jsonify({"ok": False, "error": f"{type(exc).__name__}: {exc}"}), 500


@bp.post("/power/lock")
def lock():
    """锁屏。"""
    try:
        code, out, err = _run(["rundll32.exe", "user32.dll,LockWorkStation"])
        # LockWorkStation 成功时不返回任何输出，返回码也可能非 0，所以不以此判失败
        return jsonify({"ok": True, "message": "已发出锁屏指令", "code": code, "detail": (err or out).strip()})
    except Exception as exc:
        return jsonify({"ok": False, "error": f"{type(exc).__name__}: {exc}"}), 500


@bp.post("/power/hibernate")
def hibernate():
    """休眠。系统未启用休眠时，把系统错误原样回显。"""
    try:
        code, out, err = _run(["shutdown.exe", "/h"])
        if code != 0:
            return jsonify(
                {
                    "ok": False,
                    "error": (err or out).strip() or f"shutdown /h 返回码 {code}",
                    "hint": "若提示未启用休眠，可用管理员权限执行：powercfg /hibernate on",
                }
            ), 500

        _set_state("hibernate")
        return jsonify({"ok": True, "message": "已发出休眠指令"})
    except Exception as exc:
        return jsonify({"ok": False, "error": f"{type(exc).__name__}: {exc}"}), 500


@bp.post("/power/sleep")
def sleep():
    """睡眠。powrprof 的分支依赖系统休眠设置，不保证一定进睡眠。"""
    try:
        _run(["rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"])
        return jsonify({"ok": True, "message": "已发出睡眠指令"})
    except Exception as exc:
        return jsonify({"ok": False, "error": f"{type(exc).__name__}: {exc}"}), 500


@bp.get("/power/status")
def status():
    """返回自己记录的待执行状态。"""
    with _lock:
        snapshot = dict(_state)

    remaining = None
    if snapshot.get("due_at"):
        remaining = max(0, int(snapshot["due_at"] - time.time()))

    return jsonify({"ok": True, "pending": snapshot, "remaining": remaining})
