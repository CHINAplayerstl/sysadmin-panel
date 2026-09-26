# -*- coding: utf-8 -*-
"""应用工厂：装配蓝图、鉴权、后台采样线程与统一错误响应。"""

from __future__ import annotations

import os

from flask import Flask, jsonify, render_template, request

from .auth import init_auth
from .sampler import init_sampler

__version__ = "1.0.0"


def create_app(cfg: dict) -> Flask:
    app = Flask(
        __name__,
        template_folder=os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "templates"),
        static_folder=os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static"),
    )
    app.config["PANEL"] = cfg
    app.json.ensure_ascii = False  # 直接输出中文，别转义成 \uXXXX

    # ---- 蓝图注册 ----
    from .api_system import bp as system_bp
    from .api_process import bp as process_bp
    from .api_power import bp as power_bp
    from .api_screen import bp as screen_bp
    from .api_files import bp as files_bp
    from .api_misc import bp as misc_bp
    from .api_agent import bp as agent_bp

    for blueprint in (system_bp, process_bp, power_bp, screen_bp, files_bp, misc_bp, agent_bp):
        app.register_blueprint(blueprint)

    init_auth(app)
    init_sampler(app)

    # ---- 前端入口 ----
    @app.get("/")
    def index():
        return render_template("index.html", version=__version__)

    @app.get("/api/ping")
    def ping():
        """前端用它探测令牌是否正确；鉴权钩子已放行并单独处理。"""
        return jsonify({"ok": True, "version": __version__})

    @app.post("/api/service/restart")
    def service_restart():
        """重启服务：本进程干净退出，交给计划任务的看门狗重新拉起。

        为什么需要它：面板以 SYSTEM 常驻，改完代码或 config.json 之后，
        重启它本来需要管理员权限（Stop-ScheduledTask / Start-ScheduledTask）。
        有了这个接口，在网页上点一下就行，不用反复走 UAC。

        为什么【不】用 os.execv 原地替换（这里是踩过坑的记录）：
            Windows 上的 execv 实际是"另起一个进程 + 结束自己"，PID 会变。
            更糟的是 Flask 的监听套接字设了 SO_REUSEADDR，新进程能重复绑定同一个端口，
            于是两个套接字抢同一个端口 —— 表现为端口"在监听"但所有连接超时，
            而且旧套接字还会挂在已死进程名下变成孤儿。所以改成彻底退出。

        代价：恢复需要等看门狗，约 1 分钟（任务里配的是每分钟检查一次）。
        """
        import sys as _sys
        import threading as _threading

        def _exit_cleanly() -> None:
            try:
                if _sys.stdout:
                    _sys.stdout.flush()
                if _sys.stderr:
                    _sys.stderr.flush()
            except Exception:
                pass
            os._exit(0)

        _threading.Timer(0.6, _exit_cleanly).start()
        return jsonify(
            {
                "ok": True,
                "message": "服务将在 0.6 秒后退出，计划任务会在 1 分钟内自动拉起（PID 会变）",
            }
        )

    # ---- 统一错误响应：/api 下返回 JSON，其它返回首页 ----
    def _wants_json() -> bool:
        return request.path.startswith("/api/")

    @app.errorhandler(404)
    def _not_found(_err):  # type: ignore[unused-ignore]
        if _wants_json():
            return jsonify({"ok": False, "error": "接口不存在"}), 404
        return render_template("index.html", version=__version__), 404

    @app.errorhandler(500)
    def _server_error(err):  # type: ignore[unused-ignore]
        app.logger.exception("内部错误: %s", err)
        if _wants_json():
            return jsonify({"ok": False, "error": f"服务器内部错误：{err}"}), 500
        return "服务器内部错误", 500

    @app.errorhandler(Exception)
    def _any_error(err):  # type: ignore[unused-ignore]
        # 兜底：任何未捕获异常都别把 HTML 错误页甩给 fetch
        from werkzeug.exceptions import HTTPException

        if isinstance(err, HTTPException):
            return err
        app.logger.exception("未处理异常: %s", err)
        return jsonify({"ok": False, "error": f"{type(err).__name__}: {err}"}), 500

    # ---- 响应头 ----
    @app.after_request
    def _headers(resp):  # type: ignore[unused-ignore]
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["X-Frame-Options"] = "SAMEORIGIN"
        resp.headers["Referrer-Policy"] = "no-referrer"
        if request.path.startswith("/api/"):
            # 监控数据必须每次现取，禁止任何缓存
            resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
            resp.headers["Pragma"] = "no-cache"
        return resp

    return app
