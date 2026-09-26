# -*- coding: utf-8 -*-
"""杂项接口：剪贴板、启动项、命令执行。

    GET  /api/clipboard      读取剪贴板文本
    GET  /api/startup        列出开机启动项
    GET  /api/exec/presets   返回命令白名单（前端做快捷按钮）
    POST /api/exec           执行命令并回显

剪贴板为什么走 PowerShell：
    Python 标准库没有剪贴板 API，装 pywin32 / pyperclip 又要多一个依赖。
    Windows 10 自带的 PowerShell 5.1 就有 Get-Clipboard，零成本。
    注意统一把 [Console]::OutputEncoding 设成 UTF-8，否则中文会变成乱码。

命令执行为什么默认走白名单：
    这个接口是整套面板里最危险的一环 —— 网页上一个输入框直连 cmd.exe。
    默认只放行 ipconfig / ping / netstat 这类查询命令，
    并禁止管道、重定向、命令串联（& | > < ^），避免 "ipconfig & format c:" 这种绕过。
    真要放开任意命令，把 config.json 的 allow_arbitrary_commands 改成 true。
"""

from __future__ import annotations

import os
import time

from flask import Blueprint, current_app, jsonify, request

from .utils import run_powershell, run_process

bp = Blueprint("misc", __name__, url_prefix="/api")

# 输出上限（字符），防止一条命令把响应撑爆
MAX_OUTPUT = 200_000

# 剪贴板内容上限
MAX_CLIPBOARD = 200_000

# 白名单模式下禁止出现的 shell 元字符
SHELL_METACHARS = "&|><^`"


# ---------------------------------------------------------------- 剪贴板
@bp.get("/clipboard")
def clipboard():
    """读取当前剪贴板中的文本。

    剪贴板是【会话级】资源：
      - 面板以 SYSTEM 跑在会话 0 时，本地读到的是会话 0 的空剪贴板，毫无意义，
        这时必须用"会话内助手"从用户会话读上来的内容；
      - 面板自己就在交互式会话里时，直接本地读即可。
    """
    try:
        from . import live_state as state
        from .agent_control import agent_required, agent_status, ensure_agent

        request_start = time.time()
        state.touch_clipboard()

        # --- A. 助手推上来的剪贴板 ---
        # 必须比本次请求更新：否则用户刚复制的新内容还没被助手采到，
        # 我们会把"上一轮"的旧样本当成结果返回（实测踩过：刚复制的文字读不到）。
        snapshot = state.get_clipboard()
        if snapshot["fresh"] and snapshot["ts"] >= request_start:
            text = snapshot["text"] or ""
            return jsonify(
                {
                    "ok": True,
                    "text": text[:MAX_CLIPBOARD],
                    "length": len(text),
                    "lines": text.count("\n") + (1 if text else 0),
                    "truncated": len(text) > MAX_CLIPBOARD,
                    "source": "agent",
                    "read_at": snapshot["ts"],
                }
            )

        # --- B. 需要助手但还没就绪：先催它，并给它一点时间把剪贴板推上来 ---
        if agent_required():
            ensure_agent()

            if state.agent_alive():
                # 冷启动场景：刚打开页面时助手可能在空转，等一小会儿别让用户看到空内容
                deadline = time.time() + 1.6
                while time.time() < deadline:
                    time.sleep(0.15)
                    snapshot = state.get_clipboard()
                    if snapshot["fresh"] and snapshot["ts"] >= request_start:
                        text = snapshot["text"] or ""
                        return jsonify(
                            {
                                "ok": True,
                                "text": text[:MAX_CLIPBOARD],
                                "length": len(text),
                                "lines": text.count("\n") + (1 if text else 0),
                                "truncated": len(text) > MAX_CLIPBOARD,
                                "source": "agent",
                                "read_at": snapshot["ts"],
                            }
                        )

            info = agent_status()
            note = (
                "当前没有交互式会话（没人登录），读不到剪贴板"
                if info["active_session"] is None
                else (info.get("last_error") or "会话内助手未就绪")
            )
            return jsonify({"ok": True, "text": "", "length": 0, "source": "agent", "note": note})

        # --- C. 本地读（面板自己就在交互式会话里） ---
        code, out, err = run_powershell(
            "Get-Clipboard -Raw -Format Text -ErrorAction Stop",
            timeout=15,
        )

        if code != 0 and not out:
            # 剪贴板里没有文本时，Get-Clipboard 可能报错或返回空，这属于正常情况
            return jsonify(
                {
                    "ok": True,
                    "text": "",
                    "length": 0,
                    "note": (err or "").strip() or "剪贴板为空，或内容不是文本",
                }
            )

        text = out or ""
        truncated = len(text) > MAX_CLIPBOARD
        if truncated:
            text = text[:MAX_CLIPBOARD]

        return jsonify(
            {
                "ok": True,
                "text": text,
                "length": len(text),
                "lines": text.count("\n") + (1 if text else 0),
                "truncated": truncated,
                "read_at": time.time(),
            }
        )
    except Exception as exc:
        return jsonify({"ok": False, "error": f"{type(exc).__name__}: {exc}"}), 500


# ---------------------------------------------------------------- 启动项
def _read_run_key(hive, subkey: str, view: str, location: str) -> list[dict]:
    """读一个 Run/RunOnce 注册表键。"""
    import winreg

    items: list[dict] = []
    flags = winreg.KEY_READ
    if view == "64":
        flags |= winreg.KEY_WOW64_64KEY
    elif view == "32":
        flags |= winreg.KEY_WOW64_32KEY

    try:
        with winreg.OpenKey(hive, subkey, 0, flags) as key:
            index = 0
            while True:
                try:
                    name, value, _type = winreg.EnumValue(key, index)
                except OSError:
                    break
                index += 1
                items.append(
                    {
                        "name": name or "(默认)",
                        "command": str(value),
                        "location": location,
                        "source": "注册表",
                        "kind": "Run" if "RunOnce" not in subkey else "RunOnce",
                    }
                )
    except FileNotFoundError:
        pass
    except OSError:
        pass

    return items


def _read_startup_folder(folder: str, location: str) -> list[dict]:
    """读启动文件夹里的快捷方式。"""
    items: list[dict] = []
    if not folder or not os.path.isdir(folder):
        return items

    try:
        for entry in os.scandir(folder):
            try:
                if entry.name.lower() == "desktop.ini":
                    continue
                items.append(
                    {
                        "name": os.path.splitext(entry.name)[0],
                        "command": entry.path,
                        "location": location,
                        "source": "启动文件夹",
                        "kind": "Folder",
                    }
                )
            except OSError:
                continue
    except OSError:
        pass

    return items


@bp.get("/startup")
def startup():
    """列出开机启动项：注册表 Run/RunOnce（32/64 位视图）+ 两个启动文件夹。"""
    try:
        import winreg

        items: list[dict] = []

        run_subkeys = [
            (r"Software\Microsoft\Windows\CurrentVersion\Run", "Run"),
            (r"Software\Microsoft\Windows\CurrentVersion\RunOnce", "RunOnce"),
        ]

        # 当前用户
        for subkey, kind in run_subkeys:
            items += _read_run_key(winreg.HKEY_CURRENT_USER, subkey, "64", f"HKCU\\{subkey}")
            items += _read_run_key(winreg.HKEY_CURRENT_USER, subkey, "32", f"HKCU\\{subkey} (32 位视图)")

        # 本机
        for subkey, kind in run_subkeys:
            items += _read_run_key(winreg.HKEY_LOCAL_MACHINE, subkey, "64", f"HKLM\\{subkey}")
            items += _read_run_key(winreg.HKEY_LOCAL_MACHINE, subkey, "32", f"HKLM\\{subkey} (32 位视图)")
            items += _read_run_key(
                winreg.HKEY_LOCAL_MACHINE,
                r"Software\Wow6432Node\Microsoft\Windows\CurrentVersion\\" + kind,
                "64",
                f"HKLM\\Wow6432Node\\...\\{kind}",
            )

        # 启动文件夹
        appdata = os.environ.get("APPDATA", "")
        programdata = os.environ.get("ProgramData", "")
        if appdata:
            folder = os.path.join(appdata, r"Microsoft\Windows\Start Menu\Programs\Startup")
            items += _read_startup_folder(folder, folder)
        if programdata:
            folder = os.path.join(programdata, r"Microsoft\Windows\Start Menu\Programs\Startup")
            items += _read_startup_folder(folder, folder)

        # 去重（32/64 位视图可能读到同一项）
        seen: set[tuple] = set()
        unique: list[dict] = []
        for item in items:
            key = (item["name"].lower(), item["command"].lower())
            if key in seen:
                continue
            seen.add(key)
            unique.append(item)

        return jsonify({"ok": True, "count": len(unique), "items": unique})
    except Exception as exc:
        return jsonify({"ok": False, "error": f"{type(exc).__name__}: {exc}"}), 500


# ---------------------------------------------------------------- 命令执行
@bp.get("/exec/presets")
def exec_presets():
    """返回白名单，前端据此渲染快捷命令按钮。"""
    cfg = current_app.config.get("PANEL", {})
    return jsonify(
        {
            "ok": True,
            "allow_arbitrary": bool(cfg.get("allow_arbitrary_commands")),
            "whitelist": cfg.get("command_whitelist", []),
            "timeout": cfg.get("command_timeout", 30),
        }
    )


@bp.post("/exec")
def exec_command():
    """执行命令并回显。默认只允许白名单命令。"""
    try:
        cfg = current_app.config.get("PANEL", {})
        payload = request.get_json(silent=True) or {}
        command = str(payload.get("command") or "").strip()

        if not command:
            return jsonify({"ok": False, "error": "命令为空"}), 400
        if len(command) > 2000:
            return jsonify({"ok": False, "error": "命令过长（上限 2000 字符）"}), 400

        allow_arbitrary = bool(cfg.get("allow_arbitrary_commands"))

        if not allow_arbitrary:
            # 取第一个词作为命令名，剥掉路径和 .exe 后缀
            first = command.split()[0]
            base = os.path.basename(first).lower()
            if base.endswith(".exe"):
                base = base[:-4]

            whitelist = {str(c).lower() for c in cfg.get("command_whitelist", [])}
            if base not in whitelist:
                return (
                    jsonify(
                        {
                            "ok": False,
                            "error": f"'{base}' 不在白名单内（当前为白名单模式）",
                            "hint": "如需执行任意命令，请把 config.json 的 allow_arbitrary_commands 改为 true 并重启服务",
                            "whitelist": sorted(whitelist),
                        }
                    ),
                    403,
                )

            # 白名单模式禁止 shell 元字符，防止 "ipconfig & 别的什么"
            for ch in SHELL_METACHARS:
                if ch in command:
                    return (
                        jsonify(
                            {
                                "ok": False,
                                "error": f"白名单模式下禁止使用 shell 控制符：{ch}",
                                "hint": "只允许单条查询命令",
                            }
                        ),
                        403,
                    )

        timeout = int(cfg.get("command_timeout", 30))

        started = time.time()
        code, out, err = run_process(["cmd.exe", "/d", "/c", command], timeout=timeout)
        duration = time.time() - started

        stdout = out or ""
        stderr = err or ""
        truncated = False
        if len(stdout) > MAX_OUTPUT:
            stdout = stdout[:MAX_OUTPUT]
            truncated = True
        if len(stderr) > MAX_OUTPUT:
            stderr = stderr[:MAX_OUTPUT]
            truncated = True

        return jsonify(
            {
                "ok": code == 0,
                "command": command,
                "code": code,
                "stdout": stdout,
                "stderr": stderr,
                "duration": round(duration, 3),
                "truncated": truncated,
                "mode": "arbitrary" if allow_arbitrary else "whitelist",
            }
        )
    except Exception as exc:
        return jsonify({"ok": False, "error": f"{type(exc).__name__}: {exc}"}), 500
