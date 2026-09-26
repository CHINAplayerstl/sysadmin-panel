# -*- coding: utf-8 -*-
"""管理"会话内助手"的部署与生命周期。

这里做两件事：
    1. 把助手部署到【所有用户都能访问】的位置，并设好权限
    2. 需要时把助手拉进当前活动会话

为什么必须部署到 C:\\ProgramData（而不是就地运行项目里的 agent.py）
------------------------------------------------------------------
助手是以【当前登录者本人】的身份运行的。项目在 C:\\Users\\CHNpl\\...，
那里的 ACL 只给 Firefly / SYSTEM / Administrators ——
换成 study 登录时，study 连这个目录都进不去，
CreateProcessAsUser 连解释器都打不开，前端就永远卡在"正在拉起会话内助手"。

所以助手需要的三样东西都要放到公共位置：
    - agent.py / screen_grab.py    代码（Users 只读+执行）
    - agent-config.json            只放端口和 agent_secret
    - logs\\                        助手写日志（Users 可写）
另外 Python 解释器本身也在 Firefly 的用户目录下，需要给 Users 读+执行的权限。

⚠️ 面板的 config.json 绝不复制过去：里面有 access_token，
   那等于 SYSTEM 级命令执行的通行证，不能让普通用户读到。

部署是幂等的，并且在主服务启动后第一次需要助手时自动完成，
所以改了 agent.py 不需要重跑安装脚本。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import threading
import time

from flask import current_app

from . import live_state as state
from .wts import active_session_id, has_logged_on_user, in_service_session, spawn_in_active_session

# 两次启动尝试之间的最小间隔（秒），防止前端狂刷导致疯狂拉起进程
MIN_RESPAWN_INTERVAL = 15.0

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 公共部署目录
STAGE_DIR = os.path.join(os.environ.get("ProgramData", r"C:\ProgramData"), "sysadmin-panel")
STAGE_AGENT = os.path.join(STAGE_DIR, "agent.py")
STAGE_GRAB = os.path.join(STAGE_DIR, "screen_grab.py")
STAGE_CONFIG = os.path.join(STAGE_DIR, "agent-config.json")
STAGE_LOGS = os.path.join(STAGE_DIR, "logs")

CREATE_NO_WINDOW = 0x08000000

_stage_lock = threading.Lock()
_staged_once = False
_stage_error = ""


def pythonw_path() -> str:
    """找一个"没有控制台窗口"的 Python 解释器。"""
    exe = sys.executable or ""
    if exe.lower().endswith("pythonw.exe") and os.path.exists(exe):
        return exe

    candidate = os.path.join(os.path.dirname(exe), "pythonw.exe")
    if exe and os.path.exists(candidate):
        return candidate

    return exe or "pythonw.exe"


def python_home() -> str:
    """解释器所在目录（要被授予 Users 读+执行权限）。"""
    return os.path.dirname(os.path.abspath(pythonw_path()))


# ---------------------------------------------------------------- 部署
def _icacls(args: list[str]) -> bool:
    try:
        subprocess.run(
            ["icacls"] + args,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=180,
            creationflags=CREATE_NO_WINDOW,
        )
        return True
    except Exception:
        return False


def _copy_if_changed(src: str, dst: str) -> bool:
    """内容不同才复制，避免每次启动都敲盘。"""
    try:
        with open(src, "rb") as handle:
            data = handle.read()
    except OSError:
        return False

    try:
        with open(dst, "rb") as handle:
            if handle.read() == data:
                return True
    except OSError:
        pass

    try:
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        with open(dst, "wb") as handle:
            handle.write(data)
        return True
    except OSError:
        return False


def stage_files(force: bool = False) -> tuple[bool, str]:
    """把助手部署到公共目录并设好权限。幂等。"""
    global _staged_once, _stage_error

    with _stage_lock:
        if _staged_once and not force:
            return (not _stage_error), _stage_error

        try:
            os.makedirs(STAGE_DIR, exist_ok=True)
            os.makedirs(STAGE_LOGS, exist_ok=True)

            # 1) 代码
            ok_agent = _copy_if_changed(os.path.join(PROJECT_DIR, "agent.py"), STAGE_AGENT)
            ok_grab = _copy_if_changed(os.path.join(PROJECT_DIR, "screen_grab.py"), STAGE_GRAB)

            # 2) 只放助手需要的配置（端口 + agent_secret，绝不放 access_token）
            cfg = current_app.config.get("PANEL", {})
            import json

            payload = {
                "port": int(cfg.get("port", 8787)),
                "agent_secret": str(cfg.get("agent_secret") or ""),
                "log_dir": STAGE_LOGS,
                "screenshot_quality": int(cfg.get("screenshot_quality", 70)),
            }
            with open(STAGE_CONFIG, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)

            # 3) 权限：代码只读，日志可写，解释器可执行
            _icacls([STAGE_DIR, "/grant", "BUILTIN\\Users:(OI)(CI)RX", "/T", "/C", "/Q"])
            _icacls([STAGE_LOGS, "/grant", "BUILTIN\\Users:(OI)(CI)M", "/T", "/C", "/Q"])
            _icacls([python_home(), "/grant", "BUILTIN\\Users:(OI)(CI)RX", "/T", "/C", "/Q"])

            if not (ok_agent and ok_grab):
                _stage_error = "复制 agent.py / screen_grab.py 失败"
                return False, _stage_error

            _stage_error = ""
            _staged_once = True
            return True, ""
        except Exception as exc:
            _stage_error = f"{type(exc).__name__}: {exc}"
            return False, _stage_error


# ---------------------------------------------------------------- 生命周期
def agent_required() -> bool:
    """当前是否必须依赖助手（也就是主服务跑在会话 0）。"""
    return in_service_session()


def ensure_agent(force: bool = False) -> tuple[bool, str]:
    """确保活动会话里有一个活着的助手。返回 (是否可用, 说明)。"""
    if state.agent_alive() and not force:
        return True, "助手在线"

    if not agent_required():
        return False, "无需助手（主服务已在交互式会话中）"

    cfg = current_app.config.get("PANEL", {})
    if not str(cfg.get("agent_secret") or ""):
        return False, "未配置 agent_secret，无法启动助手"

    # 停在登录界面 / 已注销：不是错误，如实说明即可
    if not has_logged_on_user():
        return False, "当前没有用户登录（停在登录界面或已注销）"

    ok_stage, stage_msg = stage_files()
    if not ok_stage:
        return False, f"助手部署失败：{stage_msg}"

    session = active_session_id()
    if session is None:
        return False, "当前没有活动的交互式会话"

    info = state.agent_info()
    if not force and (time.time() - float(info.get("spawned_at") or 0)) < MIN_RESPAWN_INTERVAL:
        return False, f"刚刚尝试过启动助手，{int(MIN_RESPAWN_INTERVAL)} 秒内不重复尝试"

    ok, message, pid = spawn_in_active_session(
        pythonw_path(), f'"{STAGE_AGENT}"', STAGE_DIR
    )
    state.set_agent(pid if ok else 0, session, "" if ok else message)

    if ok:
        return True, f"已把助手送进会话 {session}（PID {pid}），等它推第一帧"
    return False, message


def agent_status() -> dict:
    """给前端看的助手状态摘要。"""
    info = state.agent_info()
    return {
        "required": agent_required(),
        "alive": bool(info.get("alive")),
        "pid": info.get("pid") or 0,
        "session": info.get("session"),
        "active_session": active_session_id(),
        "logged_on": has_logged_on_user(),
        "stage_dir": STAGE_DIR,
        "last_error": (info.get("last_error") or "") or _stage_error,
    }