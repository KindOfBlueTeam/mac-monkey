#!/usr/bin/env python3
"""
mm_setup.py — MacMonkey interactive setup wizard

• SAFE MODE: read-only scans only
• Writes a local JSON config file
• No automatic remediation or system changes
"""

from __future__ import annotations

import json
import platform
import plistlib
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


CMD_TIMEOUT = 10


# -------------------------
# Control flow
# -------------------------

class SetupCancelled(Exception):
    """Raised when the user cancels setup (Ctrl+C)."""


def _input(prompt: str) -> str:
    try:
        return input(prompt)
    except KeyboardInterrupt:
        raise SetupCancelled()


def yn(prompt: str, default: bool = True) -> bool:
    suffix = " [Y/n] " if default else " [y/N] "
    while True:
        s = _input(prompt + suffix).strip().lower()
        if not s:
            return default
        if s in ("y", "yes"):
            return True
        if s in ("n", "no"):
            return False
        print("Please enter y or n.")


def ask_int(prompt: str, default: int, min_val: Optional[int] = None) -> int:
    while True:
        raw = _input(f"{prompt} [{default}]: ").strip()
        if not raw:
            return default
        if not raw.isdigit():
            print("Enter a whole number.")
            continue
        val = int(raw)
        if min_val is not None and val < min_val:
            print(f"Must be >= {min_val}")
            continue
        return val


def ask_float(prompt: str, default: float, min_val: Optional[float] = None) -> float:
    while True:
        raw = _input(f"{prompt} [{default}]: ").strip()
        if not raw:
            return default
        try:
            val = float(raw)
        except ValueError:
            print("Enter a number.")
            continue
        if min_val is not None and val < min_val:
            print(f"Must be >= {min_val}")
            continue
        return val


def choose_many(prompt: str, options: List[str]) -> List[str]:
    if not options:
        print(prompt)
        print("  (no options detected)\n")
        return []

    print(prompt)
    for i, opt in enumerate(options, start=1):
        print(f"  {i}. {opt}")

    raw = _input("Choose one or more (comma-separated), or blank for none: ").strip()
    if not raw:
        return []

    picked: List[str] = []
    for part in raw.split(","):
        part = part.strip()
        if part.isdigit():
            idx = int(part)
            if 1 <= idx <= len(options):
                picked.append(options[idx - 1])

    # de-dupe
    seen = set()
    out: List[str] = []
    for p in picked:
        if p not in seen:
            seen.add(p)
            out.append(p)
    return out


# -------------------------
# Utilities
# -------------------------

def run_cmd(cmd: List[str], timeout: int = CMD_TIMEOUT) -> Tuple[int, str, str]:
    try:
        p = subprocess.run(cmd, text=True, capture_output=True, timeout=timeout, check=False)
        return p.returncode, (p.stdout or "").strip(), (p.stderr or "").strip()
    except Exception as e:
        return 1, "", f"{type(e).__name__}: {e}"


def run_plist(cmd: List[str], timeout: int = CMD_TIMEOUT) -> Optional[Dict[str, Any]]:
    rc, out, _ = run_cmd(cmd, timeout=timeout)
    if rc != 0 or not out:
        return None
    try:
        return plistlib.loads(out.encode("utf-8", errors="ignore"))
    except Exception:
        return None


def safe_host_name() -> str:
    host = platform.node() or "unknown-host"
    return "".join(c if c.isalnum() or c in "-_." else "_" for c in host)


def default_config_path() -> Path:
    return Path(__file__).resolve().parent / f"mm_config.{safe_host_name()}.json"


def write_json_atomic(path: Path, data: Dict[str, Any]) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


# -------------------------
# Scans
# -------------------------

_MOUNT_RE = re.compile(r"^(?P<dev>.*?) on (?P<mp>.*?) \((?P<fstype>.*?)[,)]")
_USB_MOUNT_RE = re.compile(r"^(?P<dev>/dev/disk\S+)\s+on\s+(?P<mp>/Volumes/\S+)\s+\((?P<fstype>[^,)\s]+)")
_NET_FSTYPES = {"smbfs", "nfs", "webdav", "cifs", "afpfs"}


def scan_mounts() -> Dict[str, Any]:
    rc, out, _ = run_cmd(["mount"])
    network_mounts = []

    if rc == 0:
        for line in out.splitlines():
            m = _MOUNT_RE.match(line.strip())
            if not m:
                continue
            dev = m.group("dev")
            mp = m.group("mp")
            fstype = m.group("fstype").split(",")[0].strip()
            if fstype in _NET_FSTYPES or dev.startswith("//"):
                network_mounts.append({
                    "remote": dev,
                    "mount_point": mp,
                    "fs_type": fstype,
                })

    return {"network_mounts": network_mounts}


def scan_usb_volumes() -> List[Dict[str, Any]]:
    rc, out, _ = run_cmd(["mount"])
    vols = []

    if rc != 0:
        return vols

    for line in out.splitlines():
        m = _USB_MOUNT_RE.match(line.strip())
        if not m:
            continue

        mp = m.group("mp")
        fstype = m.group("fstype").lower()
        if fstype in _NET_FSTYPES:
            continue

        info = run_plist(["diskutil", "info", "-plist", mp])
        if not info:
            continue

        internal = bool(info.get("Internal", True))
        bus = (info.get("BusProtocol") or "").upper()
        if internal and bus not in ("USB", "THUNDERBOLT", "FIREWIRE"):
            continue

        vols.append({
            "volume_name": info.get("VolumeName") or Path(mp).name,
            "mount_point": mp,
            "bus": bus or "external",
        })

    return vols


def scan_system_summary() -> Dict[str, Any]:
    summary: Dict[str, Any] = {}
    summary["host"] = platform.node()
    rc, out, _ = run_cmd(["sysctl", "-n", "hw.model"])
    summary["model"] = out if rc == 0 else "unknown"

    rc, route_out, _ = run_cmd(["route", "-n", "get", "default"])
    m = re.search(r"interface:\s+(\S+)", route_out or "")
    summary["default_interface"] = m.group(1) if m else "unknown"

    rc, ifcfg, _ = run_cmd(["ifconfig"])
    active_ipv4 = 0
    if rc == 0:
        active_ipv4 = sum(1 for ln in ifcfg.splitlines() if ln.strip().startswith("inet "))
    summary["active_ipv4"] = active_ipv4

    summary["tmutil"] = bool(shutil.which("tmutil"))
    return summary


# -------------------------
# Wizard
# -------------------------

def wizard() -> Dict[str, Any]:
    sysinfo = scan_system_summary()
    mounts = scan_mounts()
    usb_vols = scan_usb_volumes()

    print("Detected device:")
    print(f"  Host:  {sysinfo['host']}")
    print(f"  Model: {sysinfo['model']}")
    print(f"  Default interface: {sysinfo['default_interface']}")
    print(f"  Active IPv4 interfaces: {sysinfo['active_ipv4']}")
    print(f"  Network mounts: {len(mounts['network_mounts'])}")
    print(f"  External/USB volumes detected: {len(usb_vols)}")
    print(f"  Time Machine: {'tmutil available' if sysinfo['tmutil'] else 'not available'}")
    print()

    cfg: Dict[str, Any] = {
        "schema_version": 1,
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "host": sysinfo["host"],
        "checks": {},
    }

    # Mount checks
    if yn("Enable mount checks (warn if required mounts missing)?", True):
        opts = [
            f"{m['mount_point']}  <=  {m['remote']} ({m['fs_type']})"
            for m in mounts["network_mounts"]
        ]
        picked = choose_many("Select network mounts that should be REQUIRED on this machine:", opts)
        cfg["checks"]["mounts"] = {
            "enabled": True,
            "required_mount_points": [p.split()[0] for p in picked],
        }
    else:
        cfg["checks"]["mounts"] = {"enabled": False, "required_mount_points": []}

    # Disk
    if yn("Enable disk free checks?", True):
        cfg["checks"]["disk"] = {
            "enabled": True,
            "free_gb_warn": ask_float("Disk free WARN threshold (GB)", 25.0, 0.0),
            "free_gb_bad": ask_float("Disk free BAD threshold (GB)", 10.0, 0.0),
        }
    else:
        cfg["checks"]["disk"] = {"enabled": False}

    # Processes
    if yn("Enable process checks (high CPU + top processes)?", True):
        cfg["checks"]["processes"] = {
            "enabled": True,
            "high_cpu_threshold": ask_float("High CPU threshold (%)", 50.0, 0.0),
            "top_n": ask_int("How many top processes to list", 8, 1),
        }
    else:
        cfg["checks"]["processes"] = {"enabled": False}

    # USB storage
    if yn("Enable USB-attached storage checks?", True):
        opts = [f"{v['volume_name']} @ {v['mount_point']} ({v['bus']})" for v in usb_vols]
        picked = choose_many("Select USB volumes that should be REQUIRED (by volume name):", opts)
        cfg["checks"]["usb_storage"] = {
            "enabled": True,
            "required_volume_names": [p.split(" @ ")[0] for p in picked],
            "free_gb_warn": ask_float("USB free space WARN threshold (GB)", 25.0, 0.0),
            "free_gb_bad": ask_float("USB free space BAD threshold (GB)", 10.0, 0.0),
        }
    else:
        cfg["checks"]["usb_storage"] = {"enabled": False}

    # Time Machine
    if sysinfo["tmutil"] and yn("Enable Time Machine checks?", True):
        cfg["checks"]["timemachine"] = {
            "enabled": True,
            "snapshot_warn": ask_int("Snapshot WARN threshold", 10, 0),
            "snapshot_bad": ask_int("Snapshot BAD threshold", 25, 0),
            "show_remediation": yn("Show suggested remediation commands?", True),
        }
    else:
        cfg["checks"]["timemachine"] = {"enabled": False}

    return cfg


# -------------------------
# Main
# -------------------------

def main() -> int:
    print("\nMacMonkey setup wizard")
    print("---------------------------------")
    print("SAFE MODE:")
    print("• Performs read-only system scans")
    print("• Does NOT modify mounts, Time Machine, or networking")
    print("• Only writes a local config file\n")

    out_path = default_config_path()
    print(f"Target config file: {out_path}\n")

    try:
        cfg = wizard()
        write_json_atomic(out_path, cfg)
        print(f"Wrote {out_path}")
        return 0
    except SetupCancelled:
        print("\nSetup cancelled. No config written.")
        return 130


if __name__ == "__main__":
    sys.exit(main())
