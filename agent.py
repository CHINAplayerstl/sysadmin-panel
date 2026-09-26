# -*- coding: utf-8 -*-
r"""会话内小助手：在【当前登录用户的桌面会话】里截屏、读剪贴板，推给主服务。

为什么需要它
------------
主服务以 SYSTEM 身份跑在会话 0，这样"没人登录时面板也能访问"。
但截屏和剪贴板是**会话级资源** —— 会话 0 里抓不到用户桌面。

为什么代码要放到 C:\ProgramData 而不是项目目录
----------------------------------------------
助手是以【当前登录者本人】的身份运行的。项目位于 C:\Users\<你的用户名>\...，
那里的 ACL 只给 Firefly / SYSTEM / Administrators —— 换成 study 登录时，
study 连这个目录都进不去，CreateProcessAsUser 连解释器都打不开，
表现就是前端一直卡在"正在拉起会话内助手"。

所以本文件与 screen_grab.py 会被主服务复制到
    C:\ProgramData\sysadmin-panel\
并授予 BUILTIN\Users 只读+执行权限。ProgramData 对普通用户默认可读，
正是放"公共可执行内容"的地方。

⚠️ 这里【只】放助手需要的端口与 agent_secret。
   面板的 config.json（含 access_token）绝不复制过去 ——
   那个令牌能让持有者以 SYSTEM 执行命令，不能让普通用户读到。

工作方式
--------
    循环： GET  /internal/agent/poll        问"现在要不要帧 / 剪贴板"
           POST /internal/agent/frame       推一张 JPEG
           POST /internal/agent/clipboard   推剪贴板文本
           POST /internal/agent/log         汇报异常（例如屏幕已锁定）

没人看屏幕时服务端会说 need_frame=false，助手就空转不抓屏，几乎不占 CPU。
连续多次联系不上主服务就自我了断，避免变成孤儿进程。
"""

from __future__ import annotations

import ctypes
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

from screen_grab import grab_jpeg  # noqa: E402  （必须在 sys.path 设置之后导入）

# 优先读同级目录的 agent-config.json（ProgramData 那份），
# 回退到项目目录的 config.json（方便在本机直接调试）。
STAGED_CONFIG = os.path.join(BASE_DIR, "agent-config.json")
PROJECT_CONFIG = os.path.join(BASE_DIR, "config.json")

CREATE_NO_WINDOW = 0x08000000
MAX_FAILURES = 15

_LOG_FILE = None
_LAST_REPORT = 0.0


# ---------------------------------------------------------------- 基础设施
def session_id():
    """本进程所在的会话号。"""
    sid = ctypes.c_ulong(0xFFFFFFFF)
    try:
        ctypes.WinDLL("kernel32").ProcessIdToSessionId(os.getpid(), ctypes.byref(sid))
    except Exception:
        pass
    return int(sid.value)


def load_config():
    for path in (STAGED_CONFIG, PROJECT_CONFIG):
        try:
            with open(path, "r", encoding="utf-8") as handle:
                return json.load(handle)
        except Exception:
            continue
    return {}


def log_file(cfg):
    """挑一个当前用户确实能写的位置放日志。"""
    global _LOG_FILE
    if _LOG_FILE:
        return _LOG_FILE

    candidates = []
    if cfg.get("log_dir"):
        candidates.append(cfg["log_dir"])
    candidates.append(BASE_DIR)
    candidates.append(tempfile.gettempdir())

    for folder in candidates:
        try:
            os.makedirs(folder, exist_ok=True)
            probe = os.path.join(folder, ".write-probe-%d" % os.getpid())
            with open(probe, "w", encoding="utf-8") as handle:
                handle.write("ok")
            os.remove(probe)
            _LOG_FILE = os.path.join(folder, "agent.log")
            return _LOG_FILE
        except Exception:
            continue

    _LOG_FILE = os.path.join(tempfile.gettempdir(), "sysadmin-panel-agent.log")
    return _LOG_FILE


def log(cfg, message):
    try:
        with open(log_file(cfg), "a", encoding="utf-8") as handle:
            handle.write("[%s] [pid %d] %s\n" % (time.strftime('%Y-%m-%d %H:%M:%S'), os.getpid(), message))
    except Exception:
        pass


def read_clipboard():
    """读剪贴板文本。走 PowerShell 是为了不加 pywin32 依赖。"""
    try:
        completed = subprocess.run(
            [
                "powershell.exe", "-NoProfile", "-NonInteractive",
                "-ExecutionPolicy", "Bypass", "-Command",
                "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8; Get-Clipboard -Raw -Format Text -ErrorAction Stop",
            ],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=15, creationflags=CREATE_NO_WINDOW,
        )
        return completed.stdout.decode("utf-8", errors="replace")
    except Exception as exc:
        log({}, "读剪贴板失败: %s" % exc)
        return ""


# ---------------------------------------------------------------- HTTP
def request_json(url, headers, timeout=10):
    request = urllib.request.Request(url, headers=headers, method="GET")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def post_bytes(url, payload, headers, timeout=20):
    request = urllib.request.Request(url, data=payload, headers=headers, method="POST")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        response.read()


def report_to_server(cfg, base, headers, message):
    """把异常回传主服务，好让网页上能显示"为什么没有画面"。同一条消息 10 秒内只发一次。"""
    global _LAST_REPORT
    now = time.time()
    if now - _LAST_REPORT < 10:
        return
    _LAST_REPORT = now
    try:
        post_bytes(
            base + "/internal/agent/log",
            json.dumps({"message": message}).encode("utf-8"),
            dict(headers, **{"Content-Type": "application/json"}),
            timeout=8,
        )
    except Exception:
        pass


# ---------------------------------------------------------------- 主循环
def main():
    sid = session_id()
    if sid == 0:
        log({}, "检测到自己在会话 0（服务会话），助手不应该跑在这里，退出")
        return 1

    cfg = load_config()
    port = int(cfg.get("port") or 8787)
    secret = str(cfg.get("agent_secret") or "")
    if not secret:
        log(cfg, "配置里没有 agent_secret，无法与主服务通信，退出")
        return 1

    base = "http://127.0.0.1:%d" % port
    headers = {"X-Agent-Secret": secret, "X-Agent-Session": str(sid)}

    log(cfg, "助手启动：会话=%d 端口=%d 代码目录=%s" % (sid, port, BASE_DIR))
    failures = 0
    grab_failures = 0

    while True:
        try:
            poll = request_json(base + "/internal/agent/poll", headers)
            failures = 0

            need_frame = bool(poll.get("need_frame"))
            need_clipboard = bool(poll.get("need_clipboard"))
            quality = int(poll.get("quality") or 70)
            interval_ms = int(poll.get("interval_ms") or 500)

            if need_frame:
                try:
                    buf, width, height = grab_jpeg(quality)
                    post_bytes(
                        base + "/internal/agent/frame", buf,
                        dict(headers, **{"Content-Type": "image/jpeg",
                                         "X-Frame-Width": str(width), "X-Frame-Height": str(height)}),
                    )
                    grab_failures = 0
                except Exception as exc:
                    grab_failures += 1
                    # 最常见的原因：屏幕已锁定 / 处于安全桌面，Windows 不允许此时截图
                    if grab_failures in (1, 5, 30):
                        message = ("抓屏失败：屏幕可能已锁定或停在登录界面，"
                                   "Windows 不允许在安全桌面上截图。解锁后会自动恢复。"
                                   if grab_failures >= 5 else "抓屏失败：%s" % exc)
                        log(cfg, message)
                        report_to_server(cfg, base, headers, message)

            if need_clipboard:
                try:
                    post_bytes(
                        base + "/internal/agent/clipboard",
                        json.dumps({"text": read_clipboard()}).encode("utf-8"),
                        dict(headers, **{"Content-Type": "application/json"}),
                    )
                except Exception as exc:
                    log(cfg, "推剪贴板失败: %s" % exc)

            time.sleep((interval_ms / 1000.0) if (need_frame or need_clipboard) else 0.8)

        except Exception as exc:
            failures += 1
            if failures == 1:
                log(cfg, "联系主服务失败（第 1 次）: %s" % exc)
            if failures >= MAX_FAILURES:
                log(cfg, "连续 %d 次联系不上主服务，助手退出" % MAX_FAILURES)
                return 0
            time.sleep(2)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(0)