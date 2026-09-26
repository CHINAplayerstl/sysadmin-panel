# -*- coding: utf-8 -*-
"""访问令牌校验 + 失败限速。

设计取舍：
    这个面板能结束进程、执行命令、看屏幕、下载任意文件。
    一旦监听在 0.0.0.0，令牌就是唯一防线，所以除了比对令牌，还必须防爆破 ——
    否则一个弱令牌在局域网里几分钟就能被跑出来。

令牌来源（按优先级）：
    1. 请求头  X-Auth-Token: <token>
    2. 查询串  ?token=<token>   （<img> 标签不能带自定义头，图片接口用这种）
    3. Cookie  panel_token=<token>

限速策略：
    同一 IP 在 5 分钟窗口内连续失败 10 次 → 封禁 5 分钟，返回 429。
    成功一次立刻清零。封禁只针对来源 IP，不影响本机调试。
"""

from __future__ import annotations

import hmac
import threading
import time
from functools import wraps

from flask import current_app, jsonify, request

TOKEN_HEADER = "X-Auth-Token"
TOKEN_COOKIE = "panel_token"

# ---------------------------------------------------------------- 限速参数
MAX_FAILURES = 10      # 连续失败多少次触发封禁
FAILURE_WINDOW = 300   # 统计窗口（秒）
BLOCK_SECONDS = 300    # 封禁时长（秒）
MAX_TRACKED_IPS = 512  # 记录上限，防止字典无限增长

_lock = threading.Lock()
_failures: dict[str, dict] = {}


def _expected_token() -> str:
    return current_app.config.get("PANEL", {}).get("access_token", "") or ""


def _provided_token() -> str:
    return (
        request.headers.get(TOKEN_HEADER)
        or request.args.get("token")
        or request.cookies.get(TOKEN_COOKIE)
        or ""
    )


def token_ok(provided: str, expected: str) -> bool:
    """常数时间比较，避免时序侧信道。"""
    if not expected:
        return True  # 未配置令牌 = 不校验
    return hmac.compare_digest(provided.strip(), expected.strip())


# ---------------------------------------------------------------- 限速实现
def _client_ip() -> str:
    return request.remote_addr or "?"


def blocked_seconds(ip: str) -> int:
    """该 IP 还被封着多少秒；0 表示没被封。"""
    now = time.time()
    with _lock:
        record = _failures.get(ip)
        if not record:
            return 0
        until = record.get("until", 0)
        return int(until - now) if until > now else 0


def record_failure(ip: str) -> None:
    now = time.time()
    with _lock:
        record = _failures.get(ip)
        # 超出窗口就重新计数
        if not record or now - record.get("first", 0) > FAILURE_WINDOW:
            record = {"count": 0, "first": now, "until": 0}
            _failures[ip] = record

        record["count"] += 1
        if record["count"] >= MAX_FAILURES:
            record["until"] = now + BLOCK_SECONDS
            record["count"] = 0
            record["first"] = now

        # 顺手清理陈旧记录
        if len(_failures) > MAX_TRACKED_IPS:
            stale = [k for k, v in _failures.items() if now - v.get("first", 0) > FAILURE_WINDOW * 2]
            for key in stale:
                _failures.pop(key, None)


def record_success(ip: str) -> None:
    with _lock:
        _failures.pop(ip, None)


def stats() -> dict:
    """给 /api/ping 回显用：当前有多少 IP 处于封禁中。"""
    now = time.time()
    with _lock:
        active = [k for k, v in _failures.items() if v.get("until", 0) > now]
    return {"blocked_ips": len(active), "max_failures": MAX_FAILURES, "block_seconds": BLOCK_SECONDS}


# ---------------------------------------------------------------- 钩子
def init_auth(app) -> None:
    """给 app 挂上全局鉴权钩子。"""

    @app.before_request
    def _guard():  # type: ignore[unused-ignore]
        # 静态资源和首页放行，否则前端连登录界面都加载不出来
        if not request.path.startswith("/api/"):
            return None

        ip = _client_ip()

        remaining = blocked_seconds(ip)
        if remaining > 0:
            return (
                jsonify(
                    {
                        "ok": False,
                        "error": f"认证失败次数过多，请 {remaining} 秒后再试",
                        "blocked_seconds": remaining,
                        "need_token": False,
                    }
                ),
                429,
            )

        if token_ok(_provided_token(), _expected_token()):
            record_success(ip)
            return None

        record_failure(ip)
        left = MAX_FAILURES - _failures.get(ip, {}).get("count", 0)
        return (
            jsonify(
                {
                    "ok": False,
                    "error": "未授权：缺少或错误的访问令牌",
                    "need_token": True,
                    "attempts_left": max(0, left),
                }
            ),
            401,
        )


def require_token(func):
    """需要单独加校验的视图可用这个装饰器（当前全局钩子已覆盖）。"""

    @wraps(func)
    def wrapper(*args, **kwargs):
        if not token_ok(_provided_token(), _expected_token()):
            return jsonify({"ok": False, "error": "未授权"}), 401
        return func(*args, **kwargs)

    return wrapper
