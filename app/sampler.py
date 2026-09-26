# -*- coding: utf-8 -*-
"""后台指标采样线程。

为什么要独立线程：
    前端每 1 秒拉一次图表数据，如果每次都现场调用 psutil.cpu_percent(interval=1)，
    会把 Flask 的工作线程阻塞住，页面反而更卡。
    这里用一个后台线程按固定间隔采样，接口只是把内存里的历史读出来，恒定 O(1)。
"""

from __future__ import annotations

import collections
import os
import threading
import time

import psutil


class MetricsSampler:
    """周期性采集 CPU / 内存 / 磁盘 / 网络，并保留固定长度的历史。"""

    def __init__(self, interval: float = 1.0, points: int = 120) -> None:
        self.interval = float(interval)
        self.points = int(points)

        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

        self._prev_net = None
        self._prev_ts: float | None = None

        # 系统盘（一般是 C:）
        self._system_drive = os.environ.get("SystemDrive", "C:") + "\\"

        # 历史序列，deque(maxlen) 自动丢弃最旧的点
        self.history: dict[str, collections.deque] = {
            "t": collections.deque(maxlen=self.points),
            "cpu": collections.deque(maxlen=self.points),
            "mem": collections.deque(maxlen=self.points),
            "net_up": collections.deque(maxlen=self.points),
            "net_down": collections.deque(maxlen=self.points),
        }

        self.latest: dict = {
            "cpu": 0.0,
            "mem": 0.0,
            "mem_used": 0,
            "mem_total": 0,
            "disk": 0.0,
            "disk_used": 0,
            "disk_total": 0,
            "net_up": 0.0,
            "net_down": 0.0,
            "uptime": 0.0,
            "cpu_count": psutil.cpu_count(logical=True) or 1,
            "ts": time.time(),
        }

    # ------------------------------------------------------------ 内部
    def _prime(self) -> None:
        """预热：cpu_percent 第一次调用永远返回 0，先把基线打上。"""
        try:
            psutil.cpu_percent(interval=None)
        except Exception:
            pass
        try:
            self._prev_net = psutil.net_io_counters()
        except Exception:
            self._prev_net = None
        self._prev_ts = time.time()

    def _sample_once(self) -> None:
        now = time.time()

        # CPU：interval=None 表示"距上次调用以来的平均值"，不阻塞
        try:
            cpu = float(psutil.cpu_percent(interval=None))
        except Exception:
            cpu = 0.0

        # 内存
        try:
            vm = psutil.virtual_memory()
            mem = float(vm.percent)
            mem_used, mem_total = int(vm.used), int(vm.total)
        except Exception:
            mem, mem_used, mem_total = 0.0, 0, 0

        # 系统盘占用
        try:
            du = psutil.disk_usage(self._system_drive)
            disk = float(du.percent)
            disk_used, disk_total = int(du.used), int(du.total)
        except Exception:
            disk, disk_used, disk_total = 0.0, 0, 0

        # 网络：用两次采样的字节差除以时间差，得到瞬时速率
        net_up = net_down = 0.0
        try:
            counters = psutil.net_io_counters()
            if self._prev_net is not None and self._prev_ts is not None:
                span = max(1e-6, now - self._prev_ts)
                net_up = max(0.0, (counters.bytes_sent - self._prev_net.bytes_sent) / span)
                net_down = max(0.0, (counters.bytes_recv - self._prev_net.bytes_recv) / span)
            self._prev_net = counters
            self._prev_ts = now
        except Exception:
            pass

        # 开机时长
        try:
            uptime = max(0.0, now - psutil.boot_time())
        except Exception:
            uptime = 0.0

        with self._lock:
            self.latest = {
                "cpu": round(cpu, 1),
                "mem": round(mem, 1),
                "mem_used": mem_used,
                "mem_total": mem_total,
                "disk": round(disk, 1),
                "disk_used": disk_used,
                "disk_total": disk_total,
                "net_up": round(net_up, 1),
                "net_down": round(net_down, 1),
                "uptime": uptime,
                "cpu_count": psutil.cpu_count(logical=True) or 1,
                "ts": now,
            }
            self.history["t"].append(now)
            self.history["cpu"].append(round(cpu, 1))
            self.history["mem"].append(round(mem, 1))
            self.history["net_up"].append(round(net_up, 1))
            self.history["net_down"].append(round(net_down, 1))

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self._sample_once()
            except Exception:
                # 采样线程绝不能因为一次异常就死掉
                pass
            self._stop.wait(self.interval)

    # ------------------------------------------------------------ 对外
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._prime()
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="metrics-sampler", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def snapshot(self) -> dict:
        """返回最新值 + 历史序列（供前端画折线图）。"""
        with self._lock:
            return {
                "interval": self.interval,
                "latest": dict(self.latest),
                "history": {key: list(values) for key, values in self.history.items()},
            }


# 模块级单例：create_app 时初始化
_sampler: MetricsSampler | None = None


def init_sampler(app) -> None:
    """在应用工厂里调用，启动后台采样。"""
    global _sampler

    cfg = app.config.get("PANEL", {})
    _sampler = MetricsSampler(
        interval=float(cfg.get("sample_interval", 1.0)),
        points=int(cfg.get("history_points", 120)),
    )
    _sampler.start()
    app.config["SAMPLER"] = _sampler


def get_sampler() -> MetricsSampler:
    if _sampler is None:
        raise RuntimeError("采样器尚未初始化")
    return _sampler
