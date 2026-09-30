"""Slice-06: honest resource telemetry (measure first, gate on it later).

Sources: psutil (RAM/CPU/process, cross-platform) + nvidia-smi (GPU/VRAM/
temp, NVIDIA-only). What is NOT here: CPU temperature (no reliable Windows
source without third-party drivers — GPU temp covers thermals), per-tab
browser attribution (process-level only), any prediction.

Degradation contract: if GPU telemetry is unavailable, gpu_available=False
and GPU fields are None — callers MUST skip VRAM gates, never treat unknown
as zero or as infinite. Samples are cached for SAMPLE_TTL_S so admission
checks stay cheap (cpu_percent would otherwise block on every call).
"""

from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass, field

SAMPLE_TTL_S = 5.0


@dataclass
class SystemSnapshot:
    ts: float
    ram_total_mb: int
    ram_available_mb: int
    cpu_percent: float
    lakra_rss_mb: float
    browser_rss_mb: float
    gpu_available: bool
    gpu_util_percent: float | None = None
    vram_total_mb: int | None = None
    vram_free_mb: int | None = None
    gpu_temp_c: float | None = None


def _nvidia_smi() -> dict | None:
    try:
        proc = subprocess.run(
            ["nvidia-smi",
             "--query-gpu=utilization.gpu,memory.total,memory.free,"
             "temperature.gpu",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=10)
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    try:
        util, total, free, temp = proc.stdout.strip().split(",")
        return {"util": float(util), "total": int(total),
                "free": int(free), "temp": float(temp)}
    except (ValueError, IndexError):
        return None  # driver output drift: report unknown, never garbage


class ResourceMonitor:
    def __init__(self, ttl_s: float = SAMPLE_TTL_S) -> None:
        import psutil  # local import: psutil is a slice-scoped dependency
        self._psutil = psutil
        self._proc = psutil.Process()
        self.ttl_s = ttl_s
        self._cached: SystemSnapshot | None = None

    def sample(self, force: bool = False) -> SystemSnapshot:
        now = time.time()
        if (not force and self._cached is not None
                and now - self._cached.ts < self.ttl_s):
            return self._cached
        vm = self._psutil.virtual_memory()
        cpu = self._psutil.cpu_percent(interval=0.1)
        own = self._proc.memory_info().rss / (1024 * 1024)
        browser = 0.0
        for p in self._psutil.process_iter(["name", "memory_info"]):
            try:
                if (p.info["name"] or "").lower() in (
                        "headless_shell.exe", "chrome.exe"):
                    browser += p.info["memory_info"].rss / (1024 * 1024)
            except Exception:
                continue  # races with process exit; skip, don't fail
        gpu = _nvidia_smi()
        snap = SystemSnapshot(
            ts=now,
            ram_total_mb=int(vm.total / (1024 * 1024)),
            ram_available_mb=int(vm.available / (1024 * 1024)),
            cpu_percent=cpu,
            lakra_rss_mb=round(own, 1),
            browser_rss_mb=round(browser, 1),
            gpu_available=gpu is not None,
            gpu_util_percent=gpu["util"] if gpu else None,
            vram_total_mb=gpu["total"] if gpu else None,
            vram_free_mb=gpu["free"] if gpu else None,
            gpu_temp_c=gpu["temp"] if gpu else None,
        )
        self._cached = snap
        return snap

    def under_pressure(self, *, ram_floor_mb: int = 2048,
                       vram_floor_mb: int = 512,
                       cpu_ceiling: float = 90.0) -> tuple[bool, str]:
        """Conservative pressure check. Unknown GPU => cannot judge VRAM:
        that dimension is skipped (documented), never assumed safe."""
        snap = self.sample()
        if snap.ram_available_mb < ram_floor_mb:
            return True, f"low RAM: {snap.ram_available_mb} MB available"
        if snap.cpu_percent > cpu_ceiling:
            return True, f"high CPU: {snap.cpu_percent}%"
        if snap.gpu_available and (snap.vram_free_mb or 0) < vram_floor_mb:
            return True, f"low VRAM: {snap.vram_free_mb} MB free"
        return False, "ok"
