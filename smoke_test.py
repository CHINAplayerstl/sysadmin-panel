# -*- coding: utf-8 -*-
"""接口自检脚本（冒烟测试）。

用法：
    python smoke_test.py                                   # 默认打 http://127.0.0.1:8788
    python smoke_test.py http://127.0.0.1:8787 <令牌>       # 指定地址与令牌

只做只读检查，唯一有副作用的是"结束进程"这一项 —— 它会故意用非法 PID 和
系统关键进程试探拦截逻辑，不会真的杀掉任何东西。
"""

from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8788"
TOKEN = sys.argv[2] if len(sys.argv) > 2 else ""

PASS, FAIL = "PASS", "FAIL"
results: list[tuple[str, str, str]] = []


def call(method: str, path: str, body=None, token: str | None = None, raw: bool = False):
    """发一个请求，返回 (状态码, 头, 载荷)。不抛异常。"""
    url = BASE + path
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if data:
        req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("X-Auth-Token", token)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            content = resp.read()
            if raw:
                return resp.status, resp.headers, content
            try:
                return resp.status, resp.headers, json.loads(content.decode("utf-8"))
            except Exception:
                return resp.status, resp.headers, content[:150]
    except urllib.error.HTTPError as exc:
        content = exc.read()
        try:
            payload = json.loads(content.decode("utf-8"))
        except Exception:
            payload = content[:200]
        return exc.code, exc.headers, payload
    except Exception as exc:
        return 0, {}, f"{type(exc).__name__}: {exc}"


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((PASS if ok else FAIL, name, detail))
    print(f"  [{'OK ' if ok else 'FAIL'}] {name}" + (f"  ->  {detail}" if detail else ""))


def main() -> int:
    print(f"目标: {BASE}")
    print(f"令牌: {(TOKEN[:8] + '…') if TOKEN else '<未提供，认证相关用例会失败>'}")
    print("-" * 78)

    # ---------------------------------------------------------- 认证
    print("【认证】")
    code, _, _ = call("GET", "/api/ping")
    check("无令牌访问 /api/ping 应被拒绝 (401)", code == 401, f"实际 {code}")

    code, _, payload = call("GET", "/api/ping", token=TOKEN)
    check("带令牌访问 /api/ping 应通过 (200)", code == 200, f"实际 {code} {payload}")

    code, _, _ = call("GET", "/api/ping", token="wrong-token-xxx")
    check("错误令牌应被拒绝 (401)", code == 401, f"实际 {code}")

    # ---------------------------------------------------------- 页面
    print("【页面与静态资源】")
    code, _, body = call("GET", "/", raw=True)
    is_html = isinstance(body, bytes) and b"<!DOCTYPE html>" in body[:200]
    check("首页返回 HTML", code == 200 and is_html, f"状态 {code}")

    for asset in ("/static/css/style.css", "/static/js/api.js", "/static/js/modules.js",
                  "/static/vendor/echarts.min.js"):
        code, _, body = call("GET", asset, raw=True)
        size = len(body) if isinstance(body, bytes) else 0
        check(f"静态资源 {asset}", code == 200 and size > 200, f"{size} 字节")

    # ---------------------------------------------------------- 系统信息
    print("【系统监控】")
    code, _, payload = call("GET", "/api/system/info", token=TOKEN)
    ok = code == 200 and isinstance(payload, dict) and payload.get("ok")
    data = payload.get("data", {}) if isinstance(payload, dict) else {}
    check("系统信息", ok, f"CPU={data.get('cpu_model', '?')[:34]} 内存={data.get('mem_total_human')}")
    check("  磁盘列表非空", len(data.get("disks", [])) > 0, f"{len(data.get('disks', []))} 个盘符")

    code, _, m1 = call("GET", "/api/metrics", token=TOKEN)
    ok = code == 200 and isinstance(m1, dict) and m1.get("ok")
    n1 = len(m1.get("history", {}).get("cpu", [])) if ok else 0
    check("指标接口", ok, f"CPU={m1.get('latest', {}).get('cpu')}% 内存={m1.get('latest', {}).get('mem')}% 历史 {n1} 点")

    time.sleep(2.2)
    code, _, m2 = call("GET", "/api/metrics", token=TOKEN)
    n2 = len(m2.get("history", {}).get("cpu", [])) if isinstance(m2, dict) else 0
    check("  历史序列在增长（后台采样线程活着）", n2 > n1, f"{n1} -> {n2} 点")
    check("  网络速率字段存在", "net_up" in m2.get("latest", {}) and "net_down" in m2.get("latest", {}),
          f"上行={m2.get('latest', {}).get('net_up')} B/s 下行={m2.get('latest', {}).get('net_down')} B/s")

    # ---------------------------------------------------------- 进程
    print("【进程管理】")
    code, _, payload = call("GET", "/api/processes?sort=cpu&order=desc", token=TOKEN)
    items = payload.get("items", []) if isinstance(payload, dict) else []
    check("进程列表", code == 200 and len(items) > 0, f"{payload.get('total')} 个进程，返回 {len(items)}")
    if items:
        top = items[0]
        check("  字段完整", all(k in top for k in ("pid", "name", "cpu", "mem", "status", "risk")),
              f"最高 CPU: {top['name']} {top['cpu']}%")

    code, _, payload = call("GET", "/api/processes?search=svchost", token=TOKEN)
    hit = payload.get("items", []) if isinstance(payload, dict) else []
    check("模糊搜索 search=svchost", code == 200 and all("svchost" in i["name"].lower() for i in hit),
          f"命中 {len(hit)} 个")

    code, _, payload = call("GET", "/api/processes?sort=mem&order=desc", token=TOKEN)
    mem_items = payload.get("items", []) if isinstance(payload, dict) else []
    sorted_ok = all(mem_items[i]["mem"] >= mem_items[i + 1]["mem"] for i in range(min(20, len(mem_items) - 1)))
    check("按内存降序排序正确", sorted_ok, f"最大 {mem_items[0]['mem_human'] if mem_items else '?'}")

    code, _, payload = call("POST", "/api/processes/kill", {"pids": []}, token=TOKEN)
    check("空 PID 列表被拒绝 (400)", code == 400, f"实际 {code}")

    code, _, payload = call("POST", "/api/processes/kill", {"pids": ["abc"]}, token=TOKEN)
    results_list = payload.get("results", []) if isinstance(payload, dict) else []
    check("非法 PID 被拦下", code == 200 and results_list and not results_list[0]["ok"], "已拒绝")

    # PID 4 = System，属于 fatal 级；不带 force 必须被拒绝
    code, _, payload = call("POST", "/api/processes/kill", {"pids": [4]}, token=TOKEN)
    results_list = payload.get("results", []) if isinstance(payload, dict) else []
    blocked = bool(results_list) and not results_list[0]["ok"] and "关键进程" in str(results_list[0].get("error", ""))
    check("系统关键进程 (PID 4) 未带 force 时被拒绝", blocked,
          results_list[0].get("error", "")[:48] if results_list else "无返回")

    # ---------------------------------------------------------- 电源
    print("【电源控制】")
    code, _, payload = call("GET", "/api/power/status", token=TOKEN)
    check("电源状态查询", code == 200 and payload.get("ok"), f"pending={payload.get('pending')}")
    # 故意不测真正的关机/重启 —— 那是破坏性操作，交给人工在页面上点

    # ---------------------------------------------------------- 屏幕
    print("【屏幕监控】")
    t0 = time.time()
    code, headers, body = call("GET", f"/api/screen/frame?token={TOKEN}", raw=True)
    cost = time.time() - t0
    size = len(body) if isinstance(body, bytes) else 0
    check("截屏帧返回 JPEG", code == 200 and headers.get("Content-Type") == "image/jpeg" and size > 1000,
          f"{size / 1024:.0f} KB，耗时 {cost * 1000:.0f} ms")

    t0 = time.time()
    code, _, body2 = call("GET", f"/api/screen/frame?token={TOKEN}", raw=True)
    cost2 = time.time() - t0
    check("  第二次命中帧缓存（更快）", code == 200 and cost2 <= cost + 0.05,
          f"{cost2 * 1000:.0f} ms（首次 {cost * 1000:.0f} ms）")

    code, _, payload = call("GET", "/api/screen/info", token=TOKEN)
    check("屏幕信息", code == 200 and payload.get("ok"),
          f"{payload.get('width')}×{payload.get('height')}")

    # ---------------------------------------------------------- 文件
    print("【文件浏览器】")
    code, _, payload = call("GET", "/api/files/drives", token=TOKEN)
    check("盘符列表", code == 200 and len(payload.get("items", [])) > 0,
          f"{[d['path'] for d in payload.get('items', [])]}")

    code, _, payload = call("GET", "/api/files/list?path=C%3A%5C", token=TOKEN)
    check("列目录 C:\\", code == 200 and payload.get("count", 0) > 0, f"{payload.get('count')} 项")

    code, _, payload = call("GET", "/api/files/list?path=C%3A%5C__definitely_not_here__", token=TOKEN)
    check("允许范围内但不存在的路径返回 404", code == 404, f"实际 {code}")

    code, _, payload = call("GET", "/api/files/list?path=Z%3A%5C", token=TOKEN)
    check("允许范围之外的盘符被拦截 (403)", code == 403, f"实际 {code}")

    # ---------------------------------------------------------- 剪贴板 / 启动项
    print("【剪贴板与启动项】")
    code, _, payload = call("GET", "/api/clipboard", token=TOKEN)
    check("剪贴板读取", code == 200 and payload.get("ok"),
          f"{payload.get('length')} 字符" + (f"（{payload.get('note')}）" if payload.get("note") else ""))

    code, _, payload = call("GET", "/api/startup", token=TOKEN)
    check("启动项列表", code == 200 and payload.get("ok"), f"{payload.get('count')} 项")

    # ---------------------------------------------------------- 命令执行
    print("【命令执行】")
    code, _, payload = call("GET", "/api/exec/presets", token=TOKEN)
    check("白名单接口", code == 200 and payload.get("ok"),
          f"模式={'任意' if payload.get('allow_arbitrary') else '白名单'}，{len(payload.get('whitelist', []))} 条")

    code, _, payload = call("POST", "/api/exec", {"command": "whoami"}, token=TOKEN)
    check("执行白名单命令 whoami", code == 200 and payload.get("ok") and payload.get("stdout", "").strip(),
          f"输出={payload.get('stdout', '').strip()!r} 耗时={payload.get('duration')}s")

    code, _, payload = call("POST", "/api/exec", {"command": "ipconfig | findstr IPv4"}, token=TOKEN)
    check("白名单模式拦截管道符 | (403)", code == 403, f"实际 {code} - {str(payload.get('error'))[:40]}")

    code, _, payload = call("POST", "/api/exec", {"command": "format C: /y"}, token=TOKEN)
    check("非白名单命令 format 被拦截 (403)", code == 403, f"实际 {code} - {str(payload.get('error'))[:40]}")

    # ---------------------------------------------------------- 汇总
    print("-" * 78)
    failed = [r for r in results if r[0] == FAIL]
    print(f"合计 {len(results)} 项，通过 {len(results) - len(failed)} 项，失败 {len(failed)} 项")
    if failed:
        print("失败清单：")
        for _, name, detail in failed:
            print(f"  - {name}  ({detail})")
        return 1
    # 注意：这里不要用 emoji —— 中文控制台是 GBK，编码不了的字符会让脚本自己崩掉
    print("全部通过。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
