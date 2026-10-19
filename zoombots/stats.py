"""Mesure des ressources par bot en lisant /proc (Linux).

Chaque Chromium est lancé avec PULSE_SOURCE=zb_<id>_mic : tous ses process
(navigateur, renderers, GPU) héritent de cette variable, ce qui permet de les retrouver."""

import os
from pathlib import Path

PAGE = os.sysconf("SC_PAGE_SIZE")
TICK = os.sysconf("SC_CLK_TCK")


def bot_processes(source: str) -> list[int]:
    needle = f"PULSE_SOURCE={source}".encode()
    pids = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            if needle in (entry / "environ").read_bytes().split(b"\0"):
                pids.append(int(entry.name))
        except OSError:
            continue
    return pids


def process_usage(pids: list[int]) -> dict:
    rss = cpu = 0
    alive = 0
    for pid in pids:
        try:
            fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
            cpu += int(fields[11]) + int(fields[12])  # utime + stime
            rss += int(Path(f"/proc/{pid}/statm").read_text().split()[1]) * PAGE
            alive += 1
        except (OSError, IndexError, ValueError):
            continue
    return {"processes": alive, "rss_mb": round(rss / 2**20, 1), "cpu_seconds": round(cpu / TICK, 1)}


def system_usage() -> dict:
    meminfo = {}
    for line in Path("/proc/meminfo").read_text().splitlines():
        key, value = line.split(":", 1)
        meminfo[key] = int(value.split()[0]) // 1024  # MiB
    return {
        "mem_total_mb": meminfo.get("MemTotal", 0),
        "mem_available_mb": meminfo.get("MemAvailable", 0),
        "load_avg": os.getloadavg(),
        "cpu_count": os.cpu_count(),
    }
