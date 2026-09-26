# -*- coding: utf-8 -*-
"""系统信息与实时指标接口。

    GET /api/system/info  静态信息（CPU 型号、内存总量、系统版本、盘符…），前端只取一次
    GET /api/metrics      实时指标 + 历史序列，前端每秒轮询
"""

from __future__ import annotations

import getpass
import os
import platform
import socket
import sys
import time

import psutil
from flask import Blueprint, jsonify

from .sampler import get_sampler
from .utils import existing_drives, human_bytes

bp = Blueprint("system", __name__, url_prefix="/api")


def cpu_model_name() -> str:
    """取 CPU 型号。

    platform.processor() 在 Windows 上经常返回空串，所以优先读注册表里的
    ProcessorNameString —— 这是任务管理器显示的那个名字。
    家庭版没有组策略限制，读取 HKLM 的这个键不需要管理员权限。
    """
    try:
        import winreg

        key_path = r"HARDWARE\DESCRIPTION\System\CentralProcessor\0"
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key_path) as key:
            name, _ = winreg.QueryValueEx(key, "ProcessorNameString")
            if name:
                return str(name).strip()
    except Exception:
        pass

    name = platform.processor() or platform.uname().processor or ""
    return name.strip() or "未知 CPU"


@bp.get("/system/info")
def system_info():
    """一次性返回不常变的系统信息。"""
    try:
        vm = psutil.virtual_memory()
        sm = psutil.swap_memory()
        boot = psutil.boot_time()

        # 各盘符容量
        disks = []
        for drive in existing_drives():
            try:
                usage = psutil.disk_usage(drive)
                disks.append(
                    {
                        "device": drive,
                        "total": usage.total,
                        "used": usage.used,
                        "free": usage.free,
                        "percent": round(usage.percent, 1),
                        "total_human": human_bytes(usage.total),
                        "used_human": human_bytes(usage.used),
                        "free_human": human_bytes(usage.free),
                    }
                )
            except OSError:
                continue

        # 物理核心 / 逻辑核心
        physical = psutil.cpu_count(logical=False) or 0
        logical = psutil.cpu_count(logical=True) or 0
        try:
            freq = psutil.cpu_freq()
            freq_current = round(freq.current) if freq and freq.current else None
        except Exception:
            freq_current = None

        # 操作系统版本
        os_caption = f"{platform.system()} {platform.release()}"
        try:
            import winreg

            with winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                r"SOFTWARE\Microsoft\Windows NT\CurrentVersion",
            ) as key:
                product, _ = winreg.QueryValueEx(key, "ProductName")
                display_version, _ = winreg.QueryValueEx(key, "DisplayVersion")
                build, _ = winreg.QueryValueEx(key, "CurrentBuildNumber")
                os_caption = f"{product} {display_version} (Build {build})"
        except Exception:
            os_caption = f"{os_caption} {platform.version()}"

        return jsonify(
            {
                "ok": True,
                "data": {
                    "hostname": socket.gethostname(),
                    "user": getpass.getuser(),
                    "platform": os_caption,
                    "architecture": platform.machine(),
                    "cpu_model": cpu_model_name(),
                    "cpu_physical": physical,
                    "cpu_logical": logical,
                    "cpu_freq": freq_current,
                    "mem_total": vm.total,
                    "mem_total_human": human_bytes(vm.total),
                    "mem_used_human": human_bytes(vm.used),
                    "swap_total": sm.total,
                    "swap_total_human": human_bytes(sm.total),
                    "disks": disks,
                    "boot_time": boot,
                    "boot_time_text": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(boot)),
                    "uptime": max(0.0, time.time() - boot),
                    "python": sys.version.split()[0],
                    "pid": os.getpid(),
                },
            }
        )
    except Exception as exc:  # 任何一处失败都返回 JSON，避免前端拿到 HTML
        return jsonify({"ok": False, "error": f"{type(exc).__name__}: {exc}"}), 500


@bp.get("/metrics")
def metrics():
    """实时指标 + 历史，前端每秒轮询一次。"""
    try:
        return jsonify({"ok": True, **get_sampler().snapshot()})
    except Exception as exc:
        return jsonify({"ok": False, "error": f"{type(exc).__name__}: {exc}"}), 500
