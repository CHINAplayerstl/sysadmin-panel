# -*- coding: utf-8 -*-
"""启动入口。

用法：
    python run.py                 # 使用 config.json 里的 host/port
    python run.py --port 3081     # 临时覆盖端口
    python run.py --host 0.0.0.0  # 临时开放到局域网（必须已配置 access_token）

为什么要有 --port：本机 3080 已被 DSH 的 Web GUI（node ... dsh/lib/bin.js web）占用，
直接绑 3080 会 WinError 10048。这里会在启动前把占用者查出来告诉你。
"""

from __future__ import annotations

import argparse
import os
import socket
import sys
import time

from config import CONFIG_PATH, load_config

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def redirect_output_to_log() -> tuple[object, str]:
    """把标准输出/错误导向日志文件，返回 (流对象, 日志路径)。

    为什么必须做这件事：
        计划任务用 pythonw.exe 启动时【没有控制台】，此时 sys.stdout / sys.stderr
        是 None，任何 print() 都会抛 AttributeError 把服务直接搞挂。
        werkzeug 的日志处理器又是在导入时绑定 sys.stderr 的，
        所以这个函数必须在导入 Flask 之前调用。

    附带做了 5 MB 的简单轮转，只留一份历史，避免日志无限增长。
    """
    log_dir = os.path.join(BASE_DIR, "logs")
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir, "server.log")

    try:
        if os.path.exists(log_path) and os.path.getsize(log_path) > 5 * 1024 * 1024:
            backup = log_path + ".1"
            if os.path.exists(backup):
                os.remove(backup)
            os.replace(log_path, backup)
    except OSError:
        pass

    stream = open(log_path, "a", encoding="utf-8", buffering=1)
    if sys.stdout is None:
        sys.stdout = stream
    if sys.stderr is None:
        sys.stderr = stream
    return stream, log_path


def find_port_owner(port: int) -> tuple[int, str] | None:
    """返回占用指定端口的 (pid, 名字)；查不到返回 None。"""
    try:
        import psutil
    except ImportError:
        return None

    try:
        for conn in psutil.net_connections(kind="inet"):
            if conn.status == psutil.CONN_LISTEN and conn.laddr and conn.laddr.port == port:
                try:
                    proc = psutil.Process(conn.pid)
                    return conn.pid, proc.name()
                except Exception:
                    return conn.pid, "<未知>"
    except Exception:
        return None
    return None


def describe_port_owner(port: int) -> str:
    """给出一段人话，说明端口被谁占了、该怎么办。"""
    owner = find_port_owner(port)
    if not owner:
        return f"    端口 {port} 被占用，但查不到占用进程。用 netstat -ano | findstr :{port} 自行排查。"

    pid, name = owner
    lines = [f"    占用者：PID={pid}  进程={name}"]

    # 特意把 DSH 认出来——它占着 3080，而用户此刻正在通过它跟我们对话
    cmdline = ""
    try:
        import psutil

        cmdline = " ".join(psutil.Process(pid).cmdline())
    except Exception:
        pass

    # 若占用者恰好是 DSH 本体（用户此刻正在使用的那个界面），额外提醒一句：别去关它
    if "dsh" in cmdline.lower() or "bin.js" in cmdline.lower():
        lines += [
            "",
            "  [!] 占用者是 DeepSeek Harness 本体的 Web 界面，也就是你此刻正在用的页面。",
            "      不要结束它，换端口跑这个面板即可。",
        ]

    lines += [
        "",
        f"    换端口：python run.py --port {port + 1}",
        f"    或结束它：taskkill /PID {pid} /F",
    ]
    return "\n".join(lines)


def port_is_free(host: str, port: int) -> bool:
    """实际尝试绑定一次，比只查 netstat 可靠。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host, port))
            return True
        except OSError:
            return False


def main() -> int:
    # 必须在导入 Flask / 打印任何东西之前做掉，否则 pythonw 下会 NoneType 崩溃
    _stream, log_path = redirect_output_to_log()
    print("")
    print("=" * 64)
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] sysadmin-panel 启动  PID={os.getpid()}")

    cfg = load_config()

    parser = argparse.ArgumentParser(description="本机系统管理面板")
    parser.add_argument("--host", default=cfg["host"], help="监听地址，默认取 config.json")
    parser.add_argument("--port", type=int, default=cfg["port"], help="监听端口，默认取 config.json")
    args = parser.parse_args()

    host, port = args.host, args.port

    # 安全闸门：监听非本机地址时必须要有访问令牌
    if host not in ("127.0.0.1", "localhost", "::1") and not cfg["access_token"]:
        print("[拒绝启动] 你把服务绑到了非本机地址，却没有配置 access_token。")
        print("           这个面板能结束进程、执行命令、看屏幕，裸奔等于把这台机器交出去。")
        print(f"           请编辑 {CONFIG_PATH}，给 access_token 填一个长随机串，然后重来。")
        return 2

    if not port_is_free(host, port):
        print(f"[拒绝启动] {host}:{port} 已被占用（WinError 10048 的前兆）。")
        print(describe_port_owner(port))
        print("[提示] 计划任务的看门狗会定期重试，端口一空出来就会自动接管。")
        print(f"[日志] {log_path}")
        return 3

    # 延迟导入，让依赖缺失时给出更友好的提示
    try:
        from app import create_app
    except ModuleNotFoundError as exc:
        print(f"[拒绝启动] 缺少依赖：{exc.name}")
        print("           先装依赖：python -m pip install -r requirements.txt")
        return 4

    app = create_app(cfg)

    token = cfg["access_token"]
    print("=" * 64)
    print("  本机系统管理面板")
    print("=" * 64)
    print(f"  地址      : http://{'127.0.0.1' if host in ('0.0.0.0', '::') else host}:{port}")
    if host in ("0.0.0.0", "::"):
        print("  绑定      : 局域网可访问 —— 同一网络下任何人都能尝试连接")
    else:
        print("  绑定      : 仅本机")
    print(f"  访问令牌  : {token if token else '<空 —— 未启用校验！>'}")
    print(f"  命令执行  : {'任意命令（危险）' if cfg['allow_arbitrary_commands'] else '仅白名单'}")
    print(f"  配置文件  : {CONFIG_PATH}")
    print(f"  运行日志  : {log_path}")
    print("=" * 64)

    if not token:
        print("  [!] 未配置 access_token，接口不做任何校验。")

    try:
        app.run(host=host, port=port, debug=bool(cfg["debug"]), threaded=True, use_reloader=False)
    except KeyboardInterrupt:
        print("\n已停止。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
