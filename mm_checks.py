import os
import re
import json
import time
import shutil
import socket
import subprocess
from dataclasses import dataclass
from typing import List, Optional, Dict, Any


# -------------------------------------------------------------------
# Models
# -------------------------------------------------------------------

@dataclass
class Check:
    status: str
    title: str
    message: str
    metric: Optional[float] = None
    metric_unit: Optional[str] = None


@dataclass
class Section:
    title: str
    checks: List[Check]


# -------------------------------------------------------------------
# Utilities
# -------------------------------------------------------------------

def _run(cmd: List[str], timeout: int = 10) -> str:
    try:
        cp = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return cp.stdout.strip()
    except Exception:
        return ""


def _age_str(seconds: float) -> str:
    if seconds < 60:
        return f"{int(seconds)}s"
    if seconds < 3600:
        return f"{int(seconds // 60)}m"
    return f"{int(seconds // 3600)}h"


# -------------------------------------------------------------------
# networkQuality
# -------------------------------------------------------------------

def _parse_networkquality(stdout: str) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for line in stdout.splitlines():
        line = line.strip()

        if line.lower().startswith("uplink capacity:"):
            m = re.search(r"([\d.]+)\s*Mbps", line, re.IGNORECASE)
            if m:
                out["uplink_mbps"] = float(m.group(1))

        elif line.lower().startswith("downlink capacity:"):
            m = re.search(r"([\d.]+)\s*Mbps", line, re.IGNORECASE)
            if m:
                out["downlink_mbps"] = float(m.group(1))

        elif line.lower().startswith("responsiveness:"):
            m = re.match(r"Responsiveness:\s*([A-Za-z]+)", line)
            if m:
                out["responsiveness"] = m.group(1)

            m2 = re.search(r"\(([\d.]+)\s*milliseconds", line, re.IGNORECASE)
            if m2:
                out["responsiveness_ms"] = float(m2.group(1))

        elif line.lower().startswith("idle latency:"):
            m = re.search(r"([\d.]+)\s*milliseconds", line, re.IGNORECASE)
            if m:
                out["idle_latency_ms"] = float(m.group(1))

    return out


def run_networkquality(force: bool,
                       cache_path: str,
                       interval_minutes: int,
                       timeout_sec: int) -> Dict[str, Any]:

    cache_path = os.path.expanduser(cache_path)
    os.makedirs(os.path.dirname(cache_path), exist_ok=True)

    now = int(time.time())

    # Load cache
    cached = {}
    if os.path.exists(cache_path):
        try:
            with open(cache_path, "r") as f:
                cached = json.load(f)
        except Exception:
            cached = {}

    ts = int(cached.get("ts", 0))
    age_sec = now - ts if ts else 999999
    stale = age_sec >= interval_minutes * 60

    if not force and ts and not stale:
        return {
            "ok": cached.get("ok", False),
            "ran": False,
            "ts": ts,
            "age_sec": age_sec,
            "parsed": cached.get("parsed", {}),
            "error": cached.get("error"),
        }

    try:
        cp = subprocess.run(
            ["/usr/bin/networkQuality"],
            capture_output=True,
            text=True,
            timeout=timeout_sec,
        )

        stdout = cp.stdout or ""
        parsed = _parse_networkquality(stdout)

        record = {
            "ok": cp.returncode == 0,
            "ts": now,
            "parsed": parsed,
            "error": None,
        }

        with open(cache_path, "w") as f:
            json.dump(record, f, indent=2)

        return {
            "ok": record["ok"],
            "ran": True,
            "ts": now,
            "age_sec": 0,
            "parsed": parsed,
            "error": None,
        }

    except Exception as e:
        return {
            "ok": False,
            "ran": True,
            "ts": now,
            "age_sec": 0,
            "parsed": {},
            "error": str(e),
        }


# -------------------------------------------------------------------
# Checks
# -------------------------------------------------------------------

def check_processes() -> Section:
    checks = []

    out = _run(["ps", "-Ao", "%cpu,comm"], timeout=5)
    high = []
    for line in out.splitlines()[1:]:
        try:
            cpu, cmd = line.strip().split(None, 1)
            cpu = float(cpu)
            if cpu >= 50:
                high.append((cpu, os.path.basename(cmd)))
        except Exception:
            continue

    if high:
        msg = " | ".join(f"{name} ({cpu:.0f}%)" for cpu, name in high)
        checks.append(Check("WARN", "High CPU", msg))
    else:
        checks.append(Check("OK", "High CPU", "None >= 50%"))

    return Section("Processes", checks)


def check_network() -> Section:
    checks = []

    # Gateway
    gw = _run(["route", "-n", "get", "default"])
    if "gateway:" in gw:
        checks.append(Check("OK", "Gateway", "Default route present"))
    else:
        checks.append(Check("WARN", "Gateway", "No default route"))

    # networkQuality (cached)
    nq = run_networkquality(
        force=False,
        cache_path="~/.cache/mm/networkquality.json",
        interval_minutes=30,
        timeout_sec=120,
    )

    parsed = nq.get("parsed", {})
    age = _age_str(float(nq.get("age_sec", 0)))

    down = parsed.get("downlink_mbps")
    up = parsed.get("uplink_mbps")
    resp = parsed.get("responsiveness")
    idle = parsed.get("idle_latency_ms")

    if nq.get("ok") and down:
        msg = (
            f"Downlink: {down:.1f} Mbps\n"
            f"Uplink: {up:.1f} Mbps\n"
            f"Responsiveness: {resp}\n"
            f"Idle latency: {idle:.1f} ms\n"
            f"Age: {age} (auto every 30m)"
        )
        checks.append(Check("OK", "networkQuality", msg, metric=down, metric_unit="Mbps"))
    else:
        checks.append(Check("WARN", "networkQuality", "Unavailable"))

    return Section("Network", checks)


def check_disk() -> Section:
    checks = []

    total, used, free = shutil.disk_usage("/")
    free_gb = free / (1024 ** 3)

    checks.append(Check("OK", "Disk free (/)", f"{free_gb:.1f} GB free"))

    return Section("Disk", checks)


def check_mounts() -> Section:
    checks = []

    if os.path.ismount("/Volumes/NAS"):
        checks.append(Check("OK", "Mount /Volumes/NAS", "Mounted"))
    else:
        checks.append(Check("WARN", "Mount /Volumes/NAS", "Not mounted"))

    return Section("Mounts", checks)


def check_usb() -> Section:
    return Section("USB Storage", [])


def check_timemachine() -> Section:
    checks = []

    out = _run(["tmutil", "listlocalsnapshots", "/"])
    snaps = [l for l in out.splitlines() if l.strip()]

    checks.append(Check("OK", "Local snapshots", f"{len(snaps)} snapshots"))

    return Section("Time Machine", checks)


# -------------------------------------------------------------------
# Payload Builder
# -------------------------------------------------------------------

def build_payload() -> Dict[str, Any]:
    sections = [
        check_processes(),
        check_network(),
        check_disk(),
        check_mounts(),
        check_usb(),
        check_timemachine(),
    ]

    return {
        "now": time.strftime("%Y-%m-%d %H:%M:%S"),
        "sections": [s.__dict__ for s in sections],
        "overall": "OK",
        "about": "MacMonkey modular baseline",
    }