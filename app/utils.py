# -*- coding: utf-8 -*-
"""通用工具：进程风险分级、控制台编码、命令执行、单位换算。

这里刻意不引入第三方包（除了 psutil），剪贴板与启动项都走系统自带能力。
"""

from __future__ import annotations

import ctypes
import os
import subprocess
from typing import Iterable

# ---------------------------------------------------------------- 进程风险分级
# 结束这些进程会立刻蓝屏 / 强制重启 / 会话彻底崩掉。
# 注意：其中部分进程（Idle / System / Registry）在 Windows 上本来就不允许结束。
FATAL_PROCESSES: set[str] = {
    "system",
    "system idle process",
    "registry",
    "memory compression",
    "idle",
    "smss.exe",
    "csrss.exe",
    "wininit.exe",
    "winlogon.exe",
    "services.exe",
    "lsass.exe",
    "lsaiso.exe",
    "svchost.exe",  # 视具体实例而定，但整体归为危险
}

# 这两个 PID 是"空闲进程"和"系统进程"，属于内核伪进程，永远结束不掉
FATAL_PIDS: set[int] = {0, 4}

# 结束这些进程不会蓝屏，但会让桌面 / 网络 / 安全防护出问题。
IMPORTANT_PROCESSES: set[str] = {
    "explorer.exe",
    "dwm.exe",
    "fontdrvhost.exe",
    "audiodg.exe",
    "sihost.exe",
    "ctfmon.exe",
    "taskhostw.exe",
    "runtimebroker.exe",
    "shellexperiencehost.exe",
    "startmenuexperiencehost.exe",
    "searchapp.exe",
    "searchindexer.exe",
    "searchhost.exe",
    "spoolsv.exe",
    "mpssvc.exe",  # Windows 防火墙
    "windefend.exe",
    "securityhealthservice.exe",
    "securityhealthsystray.exe",
    "dllhost.exe",
    "conhost.exe",
    "textinputhost.exe",
    "applicationframehost.exe",
}


def classify_process(name: str | None, pid: int | None = None) -> str:
    """把进程分级：fatal / important / normal。

    PID 0（System Idle Process）和 PID 4（System）是内核伪进程，
    按名字可能对不上，所以额外按 PID 判定。
    """
    if pid is not None and pid in FATAL_PIDS:
        return "fatal"
    if not name:
        return "normal"
    low = name.strip().lower()
    if low in FATAL_PROCESSES or (low + ".exe") in FATAL_PROCESSES:
        return "fatal"
    if low in IMPORTANT_PROCESSES:
        return "important"
    return "normal"


# ---------------------------------------------------------------- 控制台编码
def console_encoding() -> str:
    """取控制台 OEM 代码页。中文 Windows 是 cp936(GBK)，不是 UTF-8。"""
    try:
        cp = int(ctypes.windll.kernel32.GetOEMCP())
        if cp > 0:
            return f"cp{cp}"
    except Exception:
        pass
    return "utf-8"


def decode_console(data: bytes | None) -> str:
    """把 cmd 的原始输出解码成文本：先按 OEM 代码页，失败再退 UTF-8 / latin-1。"""
    if not data:
        return ""
    for enc in (console_encoding(), "utf-8", "latin-1"):
        try:
            return data.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return data.decode("utf-8", errors="replace")


# ---------------------------------------------------------------- 子进程执行
# 不弹黑框；被 Flask 的子线程调用时尤其重要。
CREATE_NO_WINDOW = 0x08000000


def run_process(args: list[str], timeout: int = 30) -> tuple[int, str, str]:
    """执行命令，返回 (返回码, stdout, stderr)。不抛异常，异常信息放进 stderr。"""
    try:
        completed = subprocess.run(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            creationflags=CREATE_NO_WINDOW,
            shell=False,
        )
        return (
            completed.returncode,
            decode_console(completed.stdout),
            decode_console(completed.stderr),
        )
    except subprocess.TimeoutExpired as exc:
        return -1, decode_console(exc.stdout), f"执行超时（>{timeout}s），已强制终止"
    except FileNotFoundError:
        return -2, "", f"找不到可执行文件：{args[0]}"
    except OSError as exc:
        return -3, "", f"执行失败：{exc}"


def run_powershell(script: str, timeout: int = 20) -> tuple[int, str, str]:
    """执行一段 PowerShell。统一把输出编码设成 UTF-8，避免中文乱码。"""
    wrapped = "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8;" + script
    return run_process(
        [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy", "Bypass",
            "-Command", wrapped,
        ],
        timeout=timeout,
    )


# ---------------------------------------------------------------- 杂项
def human_bytes(num: float | int | None) -> str:
    """字节数转可读字符串。"""
    if num is None:
        return "-"
    value = float(num)
    for unit in ("B", "KB", "MB", "GB", "TB", "PB"):
        if abs(value) < 1024.0:
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024.0
    return f"{value:.1f} EB"


def format_uptime(seconds: float | None) -> str:
    """把开机时长格式化成 "3 天 04:15:22"。"""
    if seconds is None or seconds < 0:
        return "-"
    total = int(seconds)
    days, rest = divmod(total, 86400)
    hours, rest = divmod(rest, 3600)
    minutes, secs = divmod(rest, 60)
    if days:
        return f"{days} 天 {hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def existing_drives() -> list[str]:
    """列出当前存在的盘符，形如 ["C:\\", "D:\\"]。"""
    drives = []
    for letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
        root = f"{letter}:\\"
        if os.path.exists(root):
            drives.append(root)
    return drives


def dedupe(items: Iterable[str]) -> list[str]:
    """保序去重。"""
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result
