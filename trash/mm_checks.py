"""
mm_checks.py

Core system checks for MacMonkey.

Includes:
- Host config loading with built-in defaults + per-host override file
- Mount checks (required mount points)
- Disk free space with 20-segment bar (5% per segment)
- High CPU processes (basename only, not full path)
- USB storage detection + required USB volumes + USB free space bars
- Time Machine local snapshots: COUNT + parsed timestamps (no size/purgeable)
- Network monitoring (default route, active IPv4 interfaces, egress, DNS)
- Sparkline support: sets Check.metric + Check.metric_unit for web UI trends

Notes:
- This module is read-only and does NOT execute privileged remediation.
"""

from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import time
from typing import Any, Dict, List, Optional, Tuple

from mm_payload import Check, Section, Payload

ANSI_RED = "\x1b[31m"
ANSI_GREEN = "\x1b[32m"
ANSI_RESET = "\x1b[0m"


DEFAULTS: Dict[str, Any] = {
    "checks": {
        "mounts": {"enabled": True, "required_mount_points": []},
        "disk": {"enabled": True, "free_gb_warn": 25, "free_gb_bad": 10},
        "processes": {"enabled": True, "high_cpu_threshold": 50, "top_n": 8},
        "usb": {"enabled": True, "required_volume_names": [], "free_gb_warn": 50, "free_gb_bad": 20},
        "timemachine": {
            "enabled": True,
            "local_snapshots_warn": 10,
            "local_snapshots_bad": 25,
            "show_latest_n": 10,  # newest-first
        },
        "network": {
            "enabled": True,
            "egress_host": "1.1.1.1",
            "egress_port": 443,
            "egress_timeout_sec": 1.5,
            "dns_test_host": "apple.com",
        },
    }
}


# ----------------------------
# Config / utility helpers
# ----------------------------

def _deep_merge(a: Dict[str, Any], b: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(a)
    for k, v in (b or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def get_host() -> str:
    return socket.gethostname()


def load_config() -> Dict[str, Any]:
    """
    Load per-host config file if present:
      mm_config.<hostname>.json

    Adds:
      _config_path, _config_notice
    """
    host = get_host()
    cfg = dict(DEFAULTS)
    path = f"mm_config.{host}.json"

    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                user_cfg = json.load(f)
            cfg = _deep_merge(cfg, user_cfg)
            cfg["_config_path"] = path
            cfg["_config_notice"] = None
            return cfg
        except Exception as e:
            cfg["_config_path"] = None
            cfg["_config_notice"] = f"Failed to load {path}: {e}"
            return cfg

    cfg["_config_path"] = None
    cfg["_config_notice"] = (
        f"No host config found for '{host}'. Using built-in defaults. "
        f"Run: python3 mm_setup.py to create {path}"
    )
    return cfg


def _run(cmd: List[str], timeout: int = 8, input_text: Optional[str] = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd,
        input=input_text,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=timeout,
        check=False,
    )


def _gb(b: int) -> float:
    return b / (1024**3)


def _fmt_bytes(b: int) -> str:
    gb = b / (1024**3)
    if gb < 1024:
        return f"{gb:.0f}G"
    tb = b / (1024**4)
    return f"{tb:.1f}T" if tb < 10 else f"{tb:.0f}T"


def _statvfs(path: str) -> Optional[Tuple[int, int]]:
    try:
        st = os.statvfs(path)
        total = int(st.f_frsize * st.f_blocks)
        free = int(st.f_frsize * st.f_bavail)
        return total, free
    except Exception:
        return None


def _proc_name(cmd: str) -> str:
    return os.path.basename(cmd or "")


# ----------------------------
# Storage bar helpers (20 segs)
# ----------------------------

def _space_segments(total: int, free: int) -> Tuple[int, int]:
    segments = 20
    if total <= 0:
        return 0, segments

    free = max(0, free)
    free_ratio = free / total

    free_segs = int(round(free_ratio * segments))
    free_segs = max(0, min(segments, free_segs))
    used_segs = segments - free_segs
    return free_segs, used_segs


def _ascii_bar(free_segs: int, used_segs: int) -> str:
    return "[" + ("|" * free_segs) + ("|" * used_segs) + "]"


def _ansi_bar(free_segs: int, used_segs: int) -> str:
    free_bar = "|" * free_segs
    used_bar = "|" * used_segs
    return "[" + ANSI_GREEN + free_bar + ANSI_RESET + ANSI_RED + used_bar + ANSI_RESET + "]"


def _space_lines(total: int, free: int) -> str:
    pct = int(round((free / total) * 100)) if total > 0 else 0
    free_segs, used_segs = _space_segments(total, free)
    return f"Total: {_fmt_bytes(total)}\nFree: {_fmt_bytes(free)} ({pct}%)\n{_ascii_bar(free_segs, used_segs)}"


def _space_lines_ansi(total: int, free: int) -> str:
    pct = int(round((free / total) * 100)) if total > 0 else 0
    free_segs, used_segs = _space_segments(total, free)
    return f"Total: {_fmt_bytes(total)}\nFree: {_fmt_bytes(free)} ({pct}%)\n{_ansi_bar(free_segs, used_segs)}"


# ----------------------------
# Mount checks
# ----------------------------

def check_mounts(required: List[str]) -> Section:
    checks: List[Check] = []
    for mp in required:
        ok = os.path.ismount(mp)
        checks.append(Check("OK" if ok else "BAD", f"Mount {mp}", "Mounted" if ok else "Not mounted"))
    if not checks:
        checks.append(Check("OK", "Mount checks", "None configured"))
    return Section("Mounts", checks)


# ----------------------------
# Disk checks
# ----------------------------

def check_disk(path: str, warn: float, bad: float) -> Section:
    sp = _statvfs(path)
    if not sp:
        return Section("Disk", [Check("WARN", f"Disk free ({path})", "Unavailable")])

    total, free = sp
    free_gb = _gb(free)

    status = "OK" if free_gb > warn else "WARN" if free_gb > bad else "BAD"
    free_segs, used_segs = _space_segments(total, free)

    return Section(
        "Disk",
        [
            Check(
                status=status,
                title=f"Disk free ({path})",
                message=_space_lines(total, free),
                message_ansi=_space_lines_ansi(total, free),
                bar_free_segments=free_segs,
                bar_used_segments=used_segs,
                metric=round(free_gb, 2),
                metric_unit="GB",
            )
        ],
    )


# ----------------------------
# Process checks
# ----------------------------

def check_processes(threshold: float, top_n: int) -> Section:
    cp = _run(["/bin/ps", "-axo", "%cpu,comm"], timeout=8)
    lines = cp.stdout.splitlines()[1:] if cp.stdout else []

    offenders: List[Tuple[str, float]] = []
    max_cpu = 0.0

    for ln in lines:
        try:
            cpu_s, cmd = ln.strip().split(None, 1)
            cpu = float(cpu_s)
            if cpu > max_cpu:
                max_cpu = cpu
            if cpu >= threshold:
                offenders.append((_proc_name(cmd), cpu))
        except Exception:
            continue

    if not offenders:
        return Section(
            "Processes",
            [Check("OK", "High CPU", f"None >= {int(threshold)}%", metric=round(max_cpu, 2), metric_unit="%")],
        )

    offenders = offenders[: max(1, int(top_n))]
    msg = " | ".join(f"{name}({cpu:.0f}%)" for name, cpu in offenders)

    return Section(
        "Processes",
        [Check("WARN", "High CPU", msg, metric=round(max_cpu, 2), metric_unit="%")],
    )


# ----------------------------
# USB checks
# ----------------------------

def _mount_fstype_map() -> Dict[str, str]:
    cp = _run(["/sbin/mount"], timeout=5)
    mp: Dict[str, str] = {}
    for line in (cp.stdout or "").splitlines():
        m = re.search(r" on (/[^ ]+) \(([^,]+),", line)
        if m:
            mp[m.group(1)] = m.group(2).lower()
    return mp


def _diskutil_plist_value(volume: str, key: str) -> Optional[str]:
    try:
        cp = _run(["/usr/sbin/diskutil", "info", "-plist", volume], timeout=20)
        if not cp.stdout:
            return None
        cp2 = _run(
            ["/usr/bin/plutil", "-extract", key, "raw", "-o", "-", "-"],
            timeout=20,
            input_text=cp.stdout,
        )
        if cp2.returncode != 0:
            return None
        v = (cp2.stdout or "").strip()
        return v if v else None
    except Exception:
        return None


def _list_usb_volumes() -> List[Dict[str, str]]:
    vols: List[Dict[str, str]] = []
    fstype = _mount_fstype_map()
    network = {"smbfs", "nfs", "afpfs", "cifs", "webdav"}

    try:
        entries = os.listdir("/Volumes")
    except Exception:
        return []

    for name in sorted(entries):
        mp = f"/Volumes/{name}"
        if not os.path.ismount(mp):
            continue
        if fstype.get(mp) in network:
            continue

        bus = _diskutil_plist_value(mp, "BusProtocol")
        internal = _diskutil_plist_value(mp, "Internal")

        if str(internal).strip().lower() == "true":
            continue

        if bus and bus.strip().upper() == "USB":
            vols.append({"name": name, "mount": mp, "bus": "USB"})

    return vols


def check_usb(required: List[str], warn: float, bad: float) -> Section:
    vols = _list_usb_volumes()
    checks: List[Check] = []

    if vols:
        for v in vols:
            checks.append(Check("OK", "USB storage", f"{v['name']} @ {v['mount']} (USB)"))
    else:
        checks.append(Check("WARN", "USB storage", "No USB volumes detected"))

    if required:
        missing = [r for r in required if r not in {v["name"] for v in vols}]
        checks.append(
            Check(
                "BAD" if missing else "OK",
                "USB required volumes",
                "Missing: " + ", ".join(missing) if missing else "All required USB volumes are mounted",
            )
        )
    else:
        checks.append(Check("OK", "USB required volumes", "None configured"))

    if vols:
        for v in vols:
            sp = _statvfs(v["mount"])
            if not sp:
                checks.append(Check("WARN", f"USB free space ({v['name']})", "Unavailable"))
                continue

            total, free = sp
            free_gb = _gb(free)
            status = "OK" if free_gb > warn else "WARN" if free_gb > bad else "BAD"
            free_segs, used_segs = _space_segments(total, free)

            checks.append(
                Check(
                    status=status,
                    title=f"USB free space ({v['name']})",
                    message=_space_lines(total, free),
                    message_ansi=_space_lines_ansi(total, free),
                    bar_free_segments=free_segs,
                    bar_used_segments=used_segs,
                    metric=round(free_gb, 2),
                    metric_unit="GB",
                )
            )
    else:
        checks.append(Check("OK", "USB free space", "No mounted USB volumes"))

    return Section("USB Storage", checks)


# ----------------------------
# Network checks (lightweight)
# ----------------------------

def _default_route() -> Tuple[Optional[str], Optional[str]]:
    try:
        cp = _run(["/sbin/route", "-n", "get", "default"], timeout=4)
        if cp.returncode != 0:
            return None, None
        iface = None
        gw = None
        for line in (cp.stdout or "").splitlines():
            line = line.strip()
            if line.startswith("interface:"):
                iface = line.split("interface:", 1)[1].strip()
            elif line.startswith("gateway:"):
                gw = line.split("gateway:", 1)[1].strip()
        return iface, gw
    except Exception:
        return None, None


def _list_ifaces() -> List[str]:
    cp = _run(["/sbin/ifconfig", "-l"], timeout=4)
    if cp.returncode != 0:
        return []
    return [x for x in (cp.stdout or "").strip().split() if x]


def _iface_ipv4(iface: str) -> Optional[str]:
    cp = _run(["/usr/sbin/ipconfig", "getifaddr", iface], timeout=2)
    if cp.returncode != 0:
        return None
    ip = (cp.stdout or "").strip()
    return ip if ip else None


def _tcp_connect_ms(host: str, port: int, timeout_sec: float) -> Optional[float]:
    t0 = time.time()
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout_sec)
    try:
        s.connect((host, int(port)))
        return (time.time() - t0) * 1000.0
    except Exception:
        return None
    finally:
        try:
            s.close()
        except Exception:
            pass


def _dns_resolve_ok(name: str, timeout_sec: float = 1.5) -> bool:
    old = socket.getdefaulttimeout()
    try:
        socket.setdefaulttimeout(timeout_sec)
        socket.getaddrinfo(name, 443)
        return True
    except Exception:
        return False
    finally:
        socket.setdefaulttimeout(old)


def check_network(egress_host: str, egress_port: int, egress_timeout_sec: float, dns_test_host: str) -> Section:
    checks: List[Check] = []

    iface, gw = _default_route()
    if iface:
        msg = f"Default: {iface}"
        if gw:
            msg += f" via {gw}"
        checks.append(Check("OK", "Default route", msg))
    else:
        checks.append(Check("BAD", "Default route", "No default route found"))

    active: List[Tuple[str, str]] = []
    for i in _list_ifaces():
        ip = _iface_ipv4(i)
        if ip:
            active.append((i, ip))

    if active:
        msg = "\n".join(f"{i}: {ip}" for i, ip in active)
        checks.append(Check("OK", "Active IPv4 interfaces", msg, metric=float(len(active)), metric_unit="count"))
    else:
        checks.append(Check("WARN", "Active IPv4 interfaces", "None detected", metric=0.0, metric_unit="count"))

    if gw and re.match(r"^\d+\.\d+\.\d+\.\d+$", gw):
        cp = _run(["/sbin/ping", "-c", "1", "-W", "1000", gw], timeout=3)
        ok = (cp.returncode == 0)
        checks.append(Check("OK" if ok else "WARN", "Gateway ping", "Reachable" if ok else "No reply"))
    else:
        checks.append(Check("OK", "Gateway ping", "No IPv4 gateway found (skipped)"))

    ms = _tcp_connect_ms(egress_host, int(egress_port), float(egress_timeout_sec))
    if ms is None:
        checks.append(Check("BAD", "Egress test", f"Unable to connect to {egress_host}:{egress_port}"))
    else:
        checks.append(Check("OK", "Egress test", f"TCP {egress_host}:{egress_port} in {ms:.0f} ms",
                            metric=round(ms, 2), metric_unit="ms"))

    dns_ok = _dns_resolve_ok(dns_test_host, timeout_sec=float(egress_timeout_sec))
    checks.append(Check("OK" if dns_ok else "WARN", "DNS resolution", f"{dns_test_host}: {'OK' if dns_ok else 'FAIL'}"))

    return Section("Network", checks)


# ----------------------------
# Time Machine (count + timestamps; no manual cleanup)
# ----------------------------

def _tmutil_available() -> bool:
    return os.path.exists("/usr/bin/tmutil")


def _tm_list_local_snapshots_raw() -> Optional[List[str]]:
    if not _tmutil_available():
        return None

    cp = _run(["/usr/bin/tmutil", "listlocalsnapshots", "/"], timeout=15)
    if cp.returncode != 0:
        return None

    snaps: List[str] = []
    for line in (cp.stdout or "").splitlines():
        line = line.strip()
        if not line:
            continue
        if "com.apple.TimeMachine" in line:
            snaps.append(line)
    return snaps


def _parse_tm_snapshot_timestamp(name: str) -> Optional[str]:
    m = re.search(r"\.(\d{4}-\d{2}-\d{2})-(\d{6})\.", name)
    if not m:
        return None
    date_part = m.group(1)
    t = m.group(2)
    hh, mm, ss = t[0:2], t[2:4], t[4:6]
    return f"{date_part} {hh}:{mm}:{ss}"


def check_timemachine(local_warn: int, local_bad: int, show_latest_n: int = 10) -> Section:
    checks: List[Check] = []

    if not _tmutil_available():
        checks.append(Check("WARN", "Time Machine", "tmutil not available"))
        return Section("Time Machine", checks)

    snaps = _tm_list_local_snapshots_raw()
    if snaps is None:
        checks.append(Check("WARN", "Local snapshots", "Unable to query local snapshots"))
        return Section("Time Machine", checks)

    n = len(snaps)
    status = "OK" if n < int(local_warn) else "WARN" if n < int(local_bad) else "BAD"

    checks.append(Check(status, "Local snapshots", f"{n} snapshots", metric=float(n), metric_unit="count"))

    stamps: List[str] = []
    unknown = 0
    for s in snaps:
        ts = _parse_tm_snapshot_timestamp(s)
        if ts:
            stamps.append(ts)
        else:
            unknown += 1

    stamps.sort(reverse=True)

    if stamps:
        newest = stamps[0]
        oldest = stamps[-1]
        latest_list = stamps[: max(1, int(show_latest_n))]

        msg = (
            f"Newest: {newest}\n"
            f"Oldest: {oldest}\n"
            f"Showing latest {len(latest_list)}:\n  " + "\n  ".join(latest_list)
        )
        if unknown:
            msg += f"\n\nNote: {unknown} snapshot(s) had unknown naming format."
        checks.append(Check("OK", "Snapshot timestamps", msg))
    else:
        checks.append(Check("WARN", "Snapshot timestamps", "No parseable snapshot timestamps found"))

    return Section("Time Machine", checks)


# ----------------------------
# Payload assembly (ORDERED)
# ----------------------------

def build_payload() -> Payload:
    cfg = load_config()
    c = cfg["checks"]
    sections: List[Section] = []

    # 1: Processes
    if c["processes"]["enabled"]:
        sections.append(check_processes(c["processes"]["high_cpu_threshold"], c["processes"]["top_n"]))

    # 2: Network
    if c["network"]["enabled"]:
        net = c["network"]
        sections.append(
            check_network(
                egress_host=str(net.get("egress_host", "1.1.1.1")),
                egress_port=int(net.get("egress_port", 443)),
                egress_timeout_sec=float(net.get("egress_timeout_sec", 1.5)),
                dns_test_host=str(net.get("dns_test_host", "apple.com")),
            )
        )

    # 3: Disk
    if c["disk"]["enabled"]:
        sections.append(check_disk("/", c["disk"]["free_gb_warn"], c["disk"]["free_gb_bad"]))

    # 4: Mounts
    if c["mounts"]["enabled"]:
        sections.append(check_mounts(c["mounts"]["required_mount_points"]))

    # 5: USB Storage
    if c["usb"]["enabled"]:
        sections.append(
            check_usb(
                c["usb"]["required_volume_names"],
                c["usb"]["free_gb_warn"],
                c["usb"]["free_gb_bad"],
            )
        )

    # 6: Time Machine
    if c["timemachine"]["enabled"]:
        sections.append(
            check_timemachine(
                c["timemachine"]["local_snapshots_warn"],
                c["timemachine"]["local_snapshots_bad"],
                c["timemachine"].get("show_latest_n", 10),
            )
        )

    overall = "OK"
    for s in sections:
        for chk in s.checks:
            if chk.status == "BAD":
                overall = "BAD"
                break
            if chk.status == "WARN" and overall != "BAD":
                overall = "WARN"

    about = f"Config: {config_name}" if config_name else "Config: default"
    cfg_path = cfg.get("_config_path")
    if cfg_path:
        about = f"Config: {os.path.basename(str(cfg_path))}"
else:
    about = "Config: built-in defaults"
    return Payload(
        now=time.strftime("%Y-%m-%d %H:%M:%S"),
        sections=sections,
        overall=overall,
        about=about,
    )
