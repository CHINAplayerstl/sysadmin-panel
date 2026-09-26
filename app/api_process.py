# -*- coding: utf-8 -*-
"""进程管理接口。

    GET  /api/processes        列表（支持搜索 / 排序 / 限制条数）
    POST /api/processes/kill   批量结束

CPU 占用口径说明：
    psutil 的 process.cpu_percent() 在多核机器上可能超过 100（100 = 占满一个核）。
    这里除以逻辑核心数，换算成和任务管理器一致的口径（0-100）。
    另外该值是"距上次调用以来的平均占用"，也就是"自上次刷新以来的占用"。
"""

from __future__ import annotations

import psutil
from flask import Blueprint, jsonify, request

from .utils import classify_process, human_bytes

bp = Blueprint("process", __name__, url_prefix="/api")

# 支持排序的字段
_SORT_FIELDS = ("cpu", "mem", "name", "pid")

# 进程 CPU 计数的预热标记
_primed = False


def prime_process_cpu() -> None:
    """预热所有进程的 CPU 计数器。

    第一次调用 cpu_percent 必然返回 0，所以应用启动时先整体调一遍，
    之后每次请求拿到的才是"距上次刷新"的真实占用。
    """
    global _primed
    if _primed:
        return
    for proc in psutil.process_iter(["pid"]):
        try:
            proc.cpu_percent(interval=None)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    _primed = True


def collect_processes() -> list[dict]:
    """抓取当前所有进程的概要信息。"""
    cpu_count = psutil.cpu_count(logical=True) or 1
    rows: list[dict] = []

    attrs = ["pid", "name", "username", "status", "memory_info", "num_threads", "create_time"]
    for proc in psutil.process_iter(attrs):
        try:
            info = proc.info
            pid = info.get("pid")
            if pid is None:
                continue

            name = info.get("name") or "?"

            mem_info = info.get("memory_info")
            rss = int(getattr(mem_info, "rss", 0) or 0)

            # 除以核心数，口径对齐任务管理器
            try:
                raw_cpu = float(proc.cpu_percent(interval=None))
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                raw_cpu = 0.0
            cpu = round(raw_cpu / cpu_count, 1)

            rows.append(
                {
                    "pid": pid,
                    "name": name,
                    "cpu": cpu,
                    "mem": rss,
                    "mem_human": human_bytes(rss),
                    "status": info.get("status") or "unknown",
                    "username": info.get("username") or "",
                    "threads": int(info.get("num_threads") or 0),
                    "create_time": float(info.get("create_time") or 0.0),
                    "risk": classify_process(name, pid),
                }
            )
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            # 进程在遍历过程中退出了，跳过即可
            continue

    return rows


@bp.get("/processes")
def list_processes():
    """进程列表：搜索 + 排序。"""
    try:
        prime_process_cpu()

        search = (request.args.get("search") or "").strip().lower()
        sort_key = request.args.get("sort") or "cpu"
        order = (request.args.get("order") or "desc").lower()
        limit = request.args.get("limit", type=int) or 0

        if sort_key not in _SORT_FIELDS:
            sort_key = "cpu"

        all_rows = collect_processes()
        total = len(all_rows)
        total_mem = sum(r["mem"] for r in all_rows)
        total_cpu = round(sum(r["cpu"] for r in all_rows), 1)

        rows = all_rows

        # 模糊过滤：进程名或 PID 命中即可
        if search:
            rows = [
                r
                for r in rows
                if search in r["name"].lower() or search in str(r["pid"])
            ]

        reverse = order != "asc"
        if sort_key == "name":
            rows.sort(key=lambda r: r["name"].lower(), reverse=reverse)
        else:
            rows.sort(key=lambda r: r[sort_key], reverse=reverse)

        if limit > 0:
            rows = rows[:limit]

        return jsonify(
            {
                "ok": True,
                "total": total,
                "returned": len(rows),
                "cpu_count": psutil.cpu_count(logical=True) or 1,
                "total_mem": total_mem,
                "total_mem_human": human_bytes(total_mem),
                "total_cpu": total_cpu,
                "items": rows,
            }
        )
    except Exception as exc:
        return jsonify({"ok": False, "error": f"{type(exc).__name__}: {exc}"}), 500


@bp.post("/processes/kill")
def kill_processes():
    """批量结束进程。

    请求体：{"pids": [123, 456], "force": false}
    force=false 时拒绝 fatal 级进程；force=true 才允许尝试（能不能成另说）。
    """
    try:
        payload = request.get_json(silent=True) or {}
        pids = payload.get("pids") or []
        force = bool(payload.get("force"))

        if not isinstance(pids, list) or not pids:
            return jsonify({"ok": False, "error": "没有收到要结束的 PID"}), 400

        results = []
        for raw_pid in pids:
            try:
                pid = int(raw_pid)
            except (TypeError, ValueError):
                results.append({"pid": raw_pid, "ok": False, "error": "PID 非法"})
                continue

            # 先拿到名字，用于风险判断和结果回显
            name = "?"
            try:
                proc = psutil.Process(pid)
                name = proc.name()
            except psutil.NoSuchProcess:
                results.append({"pid": pid, "name": name, "ok": False, "error": "进程不存在"})
                continue
            except psutil.AccessDenied:
                results.append({"pid": pid, "name": name, "ok": False, "error": "权限不足，无法读取该进程"})
                continue

            risk = classify_process(name, pid)
            if risk == "fatal" and not force:
                results.append(
                    {
                        "pid": pid,
                        "name": name,
                        "risk": risk,
                        "ok": False,
                        "error": "系统关键进程，已拒绝结束（如确需强制，前端会二次确认并带上 force）",
                    }
                )
                continue

            try:
                proc.terminate()  # 先温柔地请求退出
                try:
                    proc.wait(timeout=3)
                    results.append({"pid": pid, "name": name, "risk": risk, "ok": True, "method": "terminate"})
                    continue
                except psutil.TimeoutExpired:
                    pass

                proc.kill()  # 3 秒还不退，就强杀
                try:
                    proc.wait(timeout=3)
                except psutil.TimeoutExpired:
                    pass
                results.append({"pid": pid, "name": name, "risk": risk, "ok": True, "method": "kill"})
            except psutil.AccessDenied:
                results.append(
                    {
                        "pid": pid,
                        "name": name,
                        "risk": risk,
                        "ok": False,
                        "error": "权限不足（系统进程需要管理员权限才能结束）",
                    }
                )
            except psutil.NoSuchProcess:
                results.append({"pid": pid, "name": name, "risk": risk, "ok": True, "method": "already-gone"})
            except Exception as exc:
                results.append({"pid": pid, "name": name, "risk": risk, "ok": False, "error": str(exc)})

        succeeded = sum(1 for r in results if r.get("ok"))
        return jsonify({"ok": True, "succeeded": succeeded, "failed": len(results) - succeeded, "results": results})
    except Exception as exc:
        return jsonify({"ok": False, "error": f"{type(exc).__name__}: {exc}"}), 500
