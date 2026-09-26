# -*- coding: utf-8 -*-
"""把进程送进"当前登录用户的交互式会话"。

为什么需要这个模块
------------------
为了让面板在【没人登录 Firefly 时也能访问】，主服务必须以 SYSTEM 身份常驻。
但 SYSTEM 跑在会话 0，而**截屏和剪贴板都是会话级资源** ——
会话 0 里抓不到用户桌面，只会得到黑屏 / 空剪贴板。

于是要有个小助手（agent.py）进入用户正在使用的那个会话里干活。
怎么把进程送进去？Windows 官方的路径是这四步：

    1. WTSGetActiveConsoleSessionId()  取当前活动的控制台会话号
    2. WTSQueryUserToken(session)      取该会话的用户令牌（需要 SYSTEM + SeTcbPrivilege）
    3. DuplicateTokenEx(... TokenPrimary ...)  复制成可用来创建进程的主令牌
    4. CreateProcessAsUserW(...)       用它启动进程 —— 令牌里带着会话号，进程自然落在那个会话

本模块只做这一件事，而且【失败不影响主服务】：
拿不到令牌就返回带错误码的说明，接口层会告诉前端"当前没有可用的交互式会话"。
"""

from __future__ import annotations

import ctypes
import os
from ctypes import wintypes

INVALID_SESSION_ID = 0xFFFFFFFF

# ---- 常量 ----
MAXIMUM_ALLOWED = 0x02000000
SECURITY_IMPERSONATION = 2
TOKEN_PRIMARY = 1
CREATE_UNICODE_ENVIRONMENT = 0x00000400
CREATE_NO_WINDOW = 0x08000000

_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_wtsapi32 = ctypes.WinDLL("wtsapi32", use_last_error=True)
_advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
_userenv = ctypes.WinDLL("userenv", use_last_error=True)

_kernel32.WTSGetActiveConsoleSessionId.argtypes = []
_kernel32.WTSGetActiveConsoleSessionId.restype = wintypes.DWORD

_kernel32.ProcessIdToSessionId.argtypes = [wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
_kernel32.ProcessIdToSessionId.restype = wintypes.BOOL

_wtsapi32.WTSQueryUserToken.argtypes = [wintypes.ULONG, ctypes.POINTER(wintypes.HANDLE)]
_wtsapi32.WTSQueryUserToken.restype = wintypes.BOOL

_advapi32.DuplicateTokenEx.argtypes = [
    wintypes.HANDLE,
    wintypes.DWORD,
    ctypes.c_void_p,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.POINTER(wintypes.HANDLE),
]
_advapi32.DuplicateTokenEx.restype = wintypes.BOOL

_advapi32.CreateProcessAsUserW.argtypes = [
    wintypes.HANDLE,
    wintypes.LPCWSTR,
    wintypes.LPWSTR,
    ctypes.c_void_p,
    ctypes.c_void_p,
    wintypes.BOOL,
    wintypes.DWORD,
    ctypes.c_void_p,
    wintypes.LPCWSTR,
    ctypes.c_void_p,
    ctypes.c_void_p,
]
_advapi32.CreateProcessAsUserW.restype = wintypes.BOOL

_userenv.CreateEnvironmentBlock.argtypes = [ctypes.POINTER(ctypes.c_void_p), wintypes.HANDLE, wintypes.BOOL]
_userenv.CreateEnvironmentBlock.restype = wintypes.BOOL

_userenv.DestroyEnvironmentBlock.argtypes = [ctypes.c_void_p]
_userenv.DestroyEnvironmentBlock.restype = wintypes.BOOL


class STARTUPINFOW(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("lpReserved", wintypes.LPWSTR),
        ("lpDesktop", wintypes.LPWSTR),
        ("lpTitle", wintypes.LPWSTR),
        ("dwX", wintypes.DWORD),
        ("dwY", wintypes.DWORD),
        ("dwXSize", wintypes.DWORD),
        ("dwYSize", wintypes.DWORD),
        ("dwXCountChars", wintypes.DWORD),
        ("dwYCountChars", wintypes.DWORD),
        ("dwFillAttribute", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("wShowWindow", wintypes.WORD),
        ("cbReserved2", wintypes.WORD),
        ("lpReserved2", ctypes.POINTER(ctypes.c_byte)),
        ("hStdInput", wintypes.HANDLE),
        ("hStdOutput", wintypes.HANDLE),
        ("hStdError", wintypes.HANDLE),
    ]


class PROCESS_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("hProcess", wintypes.HANDLE),
        ("hThread", wintypes.HANDLE),
        ("dwProcessId", wintypes.DWORD),
        ("dwThreadId", wintypes.DWORD),
    ]


def active_session_id() -> int | None:
    """当前活动的控制台会话号；没人登录时返回 None。

    注意：锁屏 / 注销后的登录界面也会返回一个有效的会话号，
    所以"有活动会话"不等于"有用户登录"。判断后者请用 has_logged_on_user()。
    """
    sid = int(_kernel32.WTSGetActiveConsoleSessionId())
    if sid == INVALID_SESSION_ID:
        return None
    return sid


def current_session_id() -> int | None:
    """本进程所在的会话号。0 表示跑在服务会话（SYSTEM）。"""
    sid = wintypes.DWORD()
    if not _kernel32.ProcessIdToSessionId(os.getpid(), ctypes.byref(sid)):
        return None
    return int(sid.value)


def in_service_session() -> bool:
    """本进程是不是跑在会话 0（也就是必须以 SYSTEM 身份运行的场景）。"""
    return current_session_id() == 0


def spawn_in_active_session(executable: str, arguments: str, workdir: str | None = None) -> tuple[bool, str, int]:
    """把进程送进当前活动会话，返回 (是否成功, 说明, 新进程 PID)。"""
    session_id = active_session_id()
    if session_id is None:
        return False, "当前没有活动的交互式会话（没人登录）", 0

    if session_id == 0:
        return False, "活动会话就是会话 0，不需要跨会话启动", 0

    ctypes.set_last_error(0)
    raw_token = wintypes.HANDLE()
    if not _wtsapi32.WTSQueryUserToken(session_id, ctypes.byref(raw_token)):
        code = ctypes.get_last_error()
        return (
            False,
            f"WTSQueryUserToken 失败（错误码 {code}）。这一般是因为主服务没有以 SYSTEM 身份运行 —— "
            f"只有 SYSTEM 才有权限取别的会话的用户令牌。",
            0,
        )

    dup_token = wintypes.HANDLE()
    env_block = ctypes.c_void_p()
    pi = PROCESS_INFORMATION()

    try:
        if not _advapi32.DuplicateTokenEx(
            raw_token,
            MAXIMUM_ALLOWED,
            None,
            SECURITY_IMPERSONATION,
            TOKEN_PRIMARY,
            ctypes.byref(dup_token),
        ):
            return False, f"DuplicateTokenEx 失败（错误码 {ctypes.get_last_error()}）", 0

        env_ok = bool(_userenv.CreateEnvironmentBlock(ctypes.byref(env_block), dup_token, False))

        si = STARTUPINFOW()
        si.cb = ctypes.sizeof(si)
        # 关键：必须指定 winsta0\default，否则进程拿不到交互式桌面，截屏还是黑的
        si.lpDesktop = "winsta0\\default"

        cmdline = ctypes.create_unicode_buffer(f'"{executable}" {arguments}')

        flags = CREATE_UNICODE_ENVIRONMENT | CREATE_NO_WINDOW
        ok = _advapi32.CreateProcessAsUserW(
            dup_token,
            executable,
            cmdline,
            None,
            None,
            False,
            flags,
            env_block if env_ok else None,
            workdir,
            ctypes.byref(si),
            ctypes.byref(pi),
        )

        if not ok:
            return False, f"CreateProcessAsUserW 失败（错误码 {ctypes.get_last_error()}）", 0

        return True, f"已在会话 {session_id} 中启动（PID {pi.dwProcessId}）", int(pi.dwProcessId)
    finally:
        for handle in (pi.hThread, pi.hProcess, dup_token, raw_token):
            if handle:
                try:
                    _kernel32.CloseHandle(handle)
                except Exception:
                    pass
        if env_block:
            try:
                _userenv.DestroyEnvironmentBlock(env_block)
            except Exception:
                pass



def has_logged_on_user() -> bool:
    """活动会话里到底有没有「已登录的用户」。

    判据：能不能取到该会话的用户令牌。
    停在登录界面（无人登录）时 WTSQueryUserToken 会失败（ERROR_NO_TOKEN=1008），
    这样就区分开了"有人在用"和"只是停在登录/锁屏界面"。
    """
    sid = active_session_id()
    if sid is None or sid == 0:
        return False

    token = wintypes.HANDLE()
    ok = _wtsapi32.WTSQueryUserToken(sid, ctypes.byref(token))
    if ok and token:
        try:
            _kernel32.CloseHandle(token)
        except Exception:
            pass
        return True
    return False