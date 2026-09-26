# -*- coding: utf-8 -*-
"""文件浏览器接口。

    GET /api/files/drives     列出盘符及容量
    GET /api/files/list       列目录内容
    GET /api/files/download   下载文件

安全模型：
    所有路径必须落在 config.json 的 file_roots 之内。
    file_roots 留空时会自动放开"本机所有存在的盘符"，也就是全盘可读 ——
    这是需求里要的"浏览磁盘目录、下载文件"，但请清楚它意味着什么：
    拿到令牌的人能下载你机器上的任何文件。所以令牌 + 仅本机监听是必须的。
"""

from __future__ import annotations

import os
import time

from flask import Blueprint, current_app, jsonify, request, send_file

from .utils import existing_drives, human_bytes

bp = Blueprint("files", __name__, url_prefix="/api")

# 目录列出的最大条目数，防止超大目录把响应撑爆
MAX_ENTRIES = 3000


def allowed_roots() -> list[str]:
    """允许访问的根目录列表（绝对路径）。"""
    cfg = current_app.config.get("PANEL", {})
    roots = cfg.get("file_roots") or []
    if not roots:
        roots = existing_drives()
    result = []
    for root in roots:
        try:
            result.append(os.path.abspath(root))
        except (OSError, ValueError):
            continue
    return result


def resolve_path(raw: str) -> str:
    """把前端传来的路径解析成绝对路径，并确保它在允许范围内。

    越界时抛 PermissionError，由调用方转成 403。
    """
    if not raw:
        raise ValueError("缺少 path 参数")

    path = os.path.abspath(os.path.expandvars(os.path.expanduser(raw)))
    roots = allowed_roots()

    norm_path = os.path.normcase(path)
    for root in roots:
        norm_root = os.path.normcase(root)
        # 用 commonpath 判断父子关系，避免 "C:\foo" 被 "C:\foobar" 骗过
        try:
            common = os.path.commonpath([norm_path, norm_root])
        except ValueError:
            continue  # 不同盘符，commonpath 直接抛 ValueError
        if common == norm_root:
            return path

    raise PermissionError(f"路径不在允许范围内：{path}")


@bp.get("/files/drives")
def drives():
    """列出盘符。"""
    try:
        import psutil

        items = []
        for root in existing_drives():
            entry = {"path": root, "name": root}
            try:
                usage = psutil.disk_usage(root)
                entry.update(
                    {
                        "total": usage.total,
                        "used": usage.used,
                        "free": usage.free,
                        "percent": round(usage.percent, 1),
                        "total_human": human_bytes(usage.total),
                        "free_human": human_bytes(usage.free),
                    }
                )
            except OSError:
                pass
            items.append(entry)

        return jsonify({"ok": True, "roots": allowed_roots(), "items": items})
    except Exception as exc:
        return jsonify({"ok": False, "error": f"{type(exc).__name__}: {exc}"}), 500


@bp.get("/files/list")
def list_dir():
    """列出目录内容。"""
    try:
        raw = request.args.get("path") or ""
        if not raw:
            # 没传路径就给一个默认起点：第一个允许的根
            roots = allowed_roots()
            if not roots:
                return jsonify({"ok": False, "error": "没有可用的根目录"}), 400
            raw = roots[0]

        path = resolve_path(raw)

        if not os.path.exists(path):
            return jsonify({"ok": False, "error": f"路径不存在：{path}"}), 404
        if not os.path.isdir(path):
            return jsonify({"ok": False, "error": f"不是目录：{path}"}), 400

        entries = []
        truncated = False
        try:
            with os.scandir(path) as it:
                for index, item in enumerate(it):
                    if index >= MAX_ENTRIES:
                        truncated = True
                        break
                    try:
                        is_dir = item.is_dir(follow_symlinks=False)
                        stat = item.stat(follow_symlinks=False)
                        entries.append(
                            {
                                "name": item.name,
                                "path": item.path,
                                "is_dir": is_dir,
                                "size": 0 if is_dir else int(stat.st_size),
                                "size_human": "-" if is_dir else human_bytes(stat.st_size),
                                "mtime": float(stat.st_mtime),
                                "mtime_text": time.strftime("%Y-%m-%d %H:%M", time.localtime(stat.st_mtime)),
                            }
                        )
                    except OSError:
                        # 单个条目读不到（权限/损坏链接）就跳过，不影响整个列表
                        continue
        except PermissionError:
            return jsonify({"ok": False, "error": f"没有权限读取：{path}"}), 403
        except OSError as exc:
            return jsonify({"ok": False, "error": f"读取目录失败：{exc}"}), 500

        # 目录在前，然后按名字排序
        entries.sort(key=lambda e: (not e["is_dir"], e["name"].lower()))

        parent = os.path.dirname(path.rstrip("\\/")) or None
        if parent and os.path.normcase(parent) == os.path.normcase(path):
            parent = None

        return jsonify(
            {
                "ok": True,
                "path": path,
                "parent": parent,
                "roots": allowed_roots(),
                "count": len(entries),
                "truncated": truncated,
                "items": entries,
            }
        )
    except PermissionError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 403
    except (ValueError, OSError) as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    except Exception as exc:
        return jsonify({"ok": False, "error": f"{type(exc).__name__}: {exc}"}), 500


@bp.get("/files/download")
def download():
    """下载文件。"""
    try:
        path = resolve_path(request.args.get("path") or "")

        if not os.path.exists(path):
            return jsonify({"ok": False, "error": f"文件不存在：{path}"}), 404
        if os.path.isdir(path):
            return jsonify({"ok": False, "error": "这是一个目录，不能下载"}), 400

        # conditional=True 支持 Range 断点续传；文件用流式发送，不会一次性吃满内存
        return send_file(path, as_attachment=True, conditional=True)
    except PermissionError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 403
    except (ValueError, OSError) as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    except Exception as exc:
        return jsonify({"ok": False, "error": f"{type(exc).__name__}: {exc}"}), 500
