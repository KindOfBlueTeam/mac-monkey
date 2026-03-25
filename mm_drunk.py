#!/usr/bin/env python3
"""
mm_drunk.py

MacMonkey "Drunken Monkey" mode:
- Runs a local-only web server with the normal MacMonkey UI
- Serves mock payloads that simulate realistic failure cascades ("playlists")
- Provides a /playlist endpoint to view/set the active playlist
- Does NOT execute remediation commands (safe for demos)

Defaults:
- Uses port 8766 by default (to avoid colliding with mm_main.py default 8765)
- If the port is in use, auto-increments (8767, 8768, ...) until free.

Endpoints:
  /              Web UI
  /help          Help HTML
  /payload       Mock JSON payload (same schema as mm_main web mode)
  /playlist      Playlist info and switching (GET; can set via ?name=...)
  /playlist/next Advance one step
  /playlist/reset Reset playlist

Usage:
  python3 mm_drunk.py --list-playlists
  python3 mm_drunk.py --playlist flakey_nas
  python3 mm_drunk.py --host 127.0.0.1 --port 8766 --interval 2 --playlist bad_day
"""

from __future__ import annotations

import argparse
import json
import os
import random
import time
from collections import deque
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Deque, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, urlparse

from mm_payload import Payload, Section, Check


BANNER = r"""░▒▓███████▓▒░░▒▓███████▓▒░░▒▓█▓▒░░▒▓█▓▒░▒▓███████▓▒░░▒▓█▓▒░░▒▓█▓▒░      ░▒▓██████████████▓▒░ ░▒▓██████▓▒░░▒▓███████▓▒░░▒▓█▓▒░░▒▓█▓▒░▒▓████████▓▒░▒▓█▓▒░░▒▓█▓▒░ 
░▒▓█▓▒░░▒▓█▓▒░▒▓█▓▒░░▒▓█▓▒░▒▓█▓▒░░▒▓█▓▒░▒▓█▓▒░░▒▓█▓▒░▒▓█▓▒░░▒▓█▓▒░      ░▒▓█▓▒░░▒▓█▓▒░░▒▓█▓▒░▒▓█▓▒░░▒▓█▓▒░▒▓█▓▒░░▒▓█▓▒░▒▓█▓▒░░▒▓█▓▒░▒▓█▓▒░      ░▒▓█▓▒░░▒▓█▓▒░ 
░▒▓█▓▒░░▒▓█▓▒░▒▓█▓▒░░▒▓█▓▒░▒▓█▓▒░░▒▓█▓▒░▒▓█▓▒░░▒▓█▓▒░▒▓█▓▒░░▒▓█▓▒░      ░▒▓█▓▒░░▒▓█▓▒░░▒▓█▓▒░▒▓█▓▒░░▒▓█▓▒░▒▓█▓▒░░▒▓█▓▒░▒▓█▓▒░░▒▓█▓▒░▒▓█▓▒░      ░▒▓█▓▒░░▒▓█▓▒░ 
░▒▓█▓▒░░▒▓█▓▒░▒▓███████▓▒░░▒▓█▓▒░░▒▓█▓▒░▒▓█▓▒░░▒▓█▓▒░▒▓███████▓▒░       ░▒▓█▓▒░░▒▓█▓▒░░▒▓█▓▒░▒▓█▓▒░░▒▓█▓▒░▒▓█▓▒░░▒▓█▓▒░▒▓███████▓▒░░▒▓██████▓▒░  ░▒▓██████▓▒░  
░▒▓█▓▒░░▒▓█▓▒░▒▓█▓▒░░▒▓█▓▒░▒▓█▓▒░░▒▓█▓▒░▒▓█▓▒░░▒▓█▓▒░▒▓█▓▒░░▒▓█▓▒░      ░▒▓█▓▒░░▒▓█▓▒░░▒▓█▓▒░▒▓█▓▒░░▒▓█▓▒░▒▓█▓▒░░▒▓█▓▒░▒▓█▓▒░░▒▓█▓▒░▒▓█▓▒░         ░▒▓█▓▒░     
░▒▓█▓▒░░▒▓█▓▒░▒▓█▓▒░░▒▓█▓▒░▒▓█▓▒░░▒▓█▓▒░▒▓█▓▒░░▒▓█▓▒░▒▓█▓▒░░▒▓█▓▒░      ░▒▓█▓▒░░▒▓█▓▒░░▒▓█▓▒░▒▓█▓▒░░▒▓█▓▒░▒▓█▓▒░░▒▓█▓▒░▒▓█▓▒░░▒▓█▓▒░▒▓█▓▒░         ░▒▓█▓▒░     
░▒▓███████▓▒░░▒▓█▓▒░░▒▓█▓▒░░▒▓██████▓▒░░▒▓█▓▒░░▒▓█▓▒░▒▓█▓▒░░▒▓█▓▒░      ░▒▓█▓▒░░▒▓█▓▒░░▒▓█▓▒░░▒▓██████▓▒░░▒▓█▓▒░░▒▓█▓▒░▒▓█▓▒░░▒▓█▓▒░▒▓████████▓▒░  ░▒▓█▓▒░     
"""

PLAYLIST_PRESETS: Dict[str, List[str]] = {
    "bad_day":         ["all_ok", "cpu_hot", "mounts_missing", "usb_low_space", "tm_warn", "disk_warn", "mixed", "all_ok"],
    "flakey_nas":      ["all_ok", "mounts_missing", "cpu_hot", "tm_warn", "disk_warn", "mixed", "all_ok"],
    "heavy_workload":  ["all_ok", "cpu_hot", "disk_warn", "tm_warn", "cpu_hot", "mixed", "all_ok"],
    "no_gateway":      ["all_ok", "no_gateway", "cpu_hot", "mixed", "all_ok"],
    "tm_silent_fail":  ["all_ok", "tm_warn", "tm_bad", "disk_warn", "disk_bad", "tm_bad", "mixed", "all_ok"],
    "usb_full":        ["all_ok", "usb_low_space", "usb_low_space", "disk_warn", "mixed", "all_ok"],
    "usb_tm_failure":  ["all_ok", "usb_missing", "tm_warn", "disk_warn", "tm_bad", "disk_bad", "mixed", "all_ok"],
}

DEFAULT_UI_FALLBACK = """<!doctype html><html><body><pre>mm_ui.html missing</pre></body></html>"""
DEFAULT_HELP_FALLBACK = """<!doctype html><html><body><pre>mm_help.html missing</pre></body></html>"""


def main() -> int:
    p = argparse.ArgumentParser(description="MacMonkey Drunken Monkey mode (mock web UI)")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument(
        "--port",
        type=int,
        default=8766,
        help="Listen port (default: 8766 to avoid mm_main default 8765). If busy, auto-increments.",
    )
    p.add_argument("--interval", type=int, default=2, help="Seconds between automatic step advances")
    p.add_argument("--playlist", default="bad_day", help="Playlist preset name")
    p.add_argument("--list-playlists", action="store_true")
    p.add_argument("--no-auto-advance", action="store_true", help="Do not automatically advance steps")
    p.add_argument("--seed", type=int, default=None, help="Random seed for jitter")
    args = p.parse_args()

    if args.list_playlists:
        print("Playlist presets:")
        for name, seq in PLAYLIST_PRESETS.items():
            print(f"  {name:<13} " + ",".join(seq))
        return 0

    if args.playlist not in PLAYLIST_PRESETS:
        raise SystemExit(f"Unknown playlist: {args.playlist}. Use --list-playlists")

    if args.seed is not None:
        random.seed(args.seed)

    host = args.host
    port = int(args.port)

    server = _bind_with_port_bump(host, port)

    server.host = host
    server.port = server.server_address[1]
    server.interval = max(1, int(args.interval))
    server.auto_advance = not args.no_auto_advance
    server.playlist_name = args.playlist
    server.playlist = list(PLAYLIST_PRESETS[args.playlist])
    server.step_index = 0
    server.started_at = time.time()

    server.ui_html = _read_file("mm_ui.html", DEFAULT_UI_FALLBACK)
    server.help_html = _read_file("mm_help.html", DEFAULT_HELP_FALLBACK)

    server.ui_html = _inject_banner(server.ui_html)

    server.trends = {}
    server.history_len = 60

    print("MacMonkey Drunken Monkey mode")
    print(f"Web UI: http://{server.host}:{server.port}")
    print(f"Help:   http://{server.host}:{server.port}/help")
    print(f"JSON:   http://{server.host}:{server.port}/payload")
    print(f"Ctrl:   http://{server.host}:{server.port}/playlist")
    print(f"Playlist: {args.playlist}  Steps: {','.join(server.playlist)}")
    if server.port != port:
        print(f"Note: requested port {port} was busy; using {server.port} instead.")
    print("Press Ctrl+C to stop.")

    if server.auto_advance:
        _start_advancer(server)

    try:
        server.serve_forever()
    finally:
        server.server_close()

    return 0


def _bind_with_port_bump(host: str, port: int, max_tries: int = 50) -> "_DrunkServer":
    last_err: Optional[Exception] = None
    for p in range(port, port + max_tries):
        try:
            return _DrunkServer((host, p), _Handler)
        except OSError as e:
            # Address in use
            if getattr(e, "errno", None) == 48:
                last_err = e
                continue
            raise
    raise SystemExit(
        f"Unable to bind to {host}:{port}..{port+max_tries-1}. "
        f"Last error: {last_err}\n"
        f"Try: lsof -nP -iTCP:{port} -sTCP:LISTEN"
    )


class _DrunkServer(ThreadingHTTPServer):
    host: str
    port: int
    interval: int
    auto_advance: bool

    playlist_name: str
    playlist: List[str]
    step_index: int
    started_at: float

    ui_html: str
    help_html: str

    history_len: int
    trends: Dict[Tuple[str, str], Deque[float]]


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args: Any) -> None:
        return

    def _send(self, code: int, body: bytes, content_type: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_text(self, code: int, text: str, content_type: str) -> None:
        self._send(code, text.encode("utf-8", errors="ignore"), content_type)

    def do_GET(self) -> None:
        srv: _DrunkServer = self.server  # type: ignore[assignment]
        parsed = urlparse(self.path)
        path = parsed.path
        qs = parse_qs(parsed.query or "")

        if path in ("/", "/index.html"):
            self._send_text(200, srv.ui_html, "text/html; charset=utf-8")
            return

        if path == "/help":
            self._send_text(200, srv.help_html, "text/html; charset=utf-8")
            return

        if path == "/playlist":
            name = (qs.get("name", [None])[0] or "").strip()
            if name:
                if name not in PLAYLIST_PRESETS:
                    self._send_text(400, f"Unknown playlist: {name}\n", "text/plain; charset=utf-8")
                    return
                srv.playlist_name = name
                srv.playlist = list(PLAYLIST_PRESETS[name])
                srv.step_index = 0

            info = {
                "active": srv.playlist_name,
                "step_index": srv.step_index,
                "steps": srv.playlist,
                "presets": {k: ",".join(v) for k, v in PLAYLIST_PRESETS.items()},
                "set_hint": "Use /playlist?name=<preset> to switch",
                "next_hint": "Use /playlist/next to advance one step",
                "reset_hint": "Use /playlist/reset to restart current playlist",
            }
            self._send_text(200, json.dumps(info, indent=2), "application/json; charset=utf-8")
            return

        if path == "/playlist/next":
            srv.step_index = (srv.step_index + 1) % max(1, len(srv.playlist))
            self._send_text(200, f"OK next step: {srv.playlist[srv.step_index]}\n", "text/plain; charset=utf-8")
            return

        if path == "/playlist/reset":
            srv.step_index = 0
            self._send_text(200, "OK reset\n", "text/plain; charset=utf-8")
            return

        if path == "/payload":
            payload = build_mock_payload(
                playlist_name=srv.playlist_name,
                scene=srv.playlist[srv.step_index],
                step_index=srv.step_index,
                total_steps=len(srv.playlist),
            )
            data = payload.to_dict()
            _attach_trends(srv, data)
            self._send_text(200, json.dumps(data, indent=2), "application/json; charset=utf-8")
            return

        self._send_text(404, "Not found\n", "text/plain; charset=utf-8")


def _start_advancer(server: _DrunkServer) -> None:
    import threading

    def loop() -> None:
        while True:
            time.sleep(server.interval)
            server.step_index = (server.step_index + 1) % max(1, len(server.playlist))

    threading.Thread(target=loop, daemon=True).start()


def _read_file(path: str, default: str) -> str:
    try:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return f.read()
    except Exception:
        pass
    return default


def _inject_banner(html: str) -> str:
    banner_html = (
        '<div style="white-space:pre; color:#ff0000; font-size:10px; line-height:1.0; margin:0 0 10px 0;">'
        + _escape_html(BANNER)
        + "</div>"
    )
    idx = html.lower().find("<body")
    if idx == -1:
        return banner_html + html
    end = html.find(">", idx)
    if end == -1:
        return banner_html + html
    return html[: end + 1] + "\n" + banner_html + "\n" + html[end + 1 :]


def _escape_html(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _attach_trends(server: _DrunkServer, payload_dict: Dict[str, Any]) -> None:
    sections = payload_dict.get("sections") or []
    for sec in sections:
        sec_title = sec.get("title", "")
        checks = sec.get("checks") or []
        for chk in checks:
            title = chk.get("title", "")
            metric = chk.get("metric", None)
            if metric is None:
                continue
            key = (sec_title, title)
            dq = server.trends.get(key)
            if dq is None:
                dq = deque(maxlen=server.history_len)
                server.trends[key] = dq
            try:
                dq.append(float(metric))
            except Exception:
                continue
            chk["trend"] = list(dq)


# -----------------------------
# Mock payload generator
# -----------------------------

def _bar_segments_from_free_pct(free_pct: float) -> Tuple[int, int]:
    free_segs = int(round((max(0.0, min(100.0, free_pct)) / 100.0) * 20))
    free_segs = max(0, min(20, free_segs))
    return free_segs, 20 - free_segs


def build_mock_payload(playlist_name: str, scene: str, step_index: int, total_steps: int) -> Payload:
    """
    Section ORDER matches current MacMonkey:
      1: Processes
      2: Network
      3: Disk
      4: Mounts
      5: USB Storage
      6: Time Machine (no manual cleanup)
    """
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    about = f"MacMonkey Drunken Monkey mode | playlist: {playlist_name} | step {step_index+1}/{total_steps} ({scene})"

    cpu_max = 6.0
    cpu_msg = "None >= 50%"
    cpu_status = "OK"

    net_default_route = ("OK", "Default route", "Default: en0 via 192.168.1.1")
    net_ifaces = ("OK", "Active IPv4 interfaces", "en0: 192.168.1.50")
    net_gateway = ("OK", "Gateway ping", "Reachable")
    net_egress_ms = 22.0
    net_egress = ("OK", "Egress test", f"TCP 1.1.1.1:443 in {net_egress_ms:.0f} ms")
    net_dns = ("OK", "DNS resolution", "apple.com: OK")

    disk_free_gb = 166.0
    disk_free_pct = 36
    disk_status = "OK"

    mounts_required = [("/Volumes/NAS", True)]

    usb_present = True
    usb_name = "USBDISK"
    usb_mount = "/Volumes/USBDISK"
    usb_free_gb = 1600.0
    usb_free_pct = 88
    usb_status = "OK"
    usb_required_missing: List[str] = []

    tm_snaps = 2
    tm_status = "OK"
    tm_stamps = ["2026-02-02 15:30:00", "2026-02-01 09:10:22"]

    if scene == "cpu_hot":
        cpu_status = "WARN"
        cpu_max = 96.0
        cpu_msg = "LogicPro(82%) | kernel_task(14%)"

    if scene == "mounts_missing":
        mounts_required = [("/Volumes/NAS", False)]
        net_egress_ms = 45.0
        net_egress = ("OK", "Egress test", f"TCP 1.1.1.1:443 in {net_egress_ms:.0f} ms")

    if scene == "no_gateway":
        net_default_route = ("BAD", "Default route", "No default route found")
        net_ifaces = ("WARN", "Active IPv4 interfaces", "None detected")
        net_gateway = ("WARN", "Gateway ping", "No IPv4 gateway found (skipped)")
        net_egress_ms = 0.0
        net_egress = ("BAD", "Egress test", "Unable to connect to 1.1.1.1:443")
        net_dns = ("WARN", "DNS resolution", "apple.com: FAIL")

    if scene == "disk_warn":
        disk_status = "WARN"
        disk_free_gb = 22.0
        disk_free_pct = 5

    if scene == "disk_bad":
        disk_status = "BAD"
        disk_free_gb = 7.0
        disk_free_pct = 2

    if scene == "usb_low_space":
        usb_status = "WARN"
        usb_free_gb = 35.0
        usb_free_pct = 2

    if scene == "usb_missing":
        usb_present = False
        usb_required_missing = [usb_name]

    if scene == "tm_warn":
        tm_status = "WARN"
        tm_snaps = 14
        tm_stamps = [f"2026-02-02 14:{m:02d}:00" for m in range(0, 10)] + ["2026-02-01 09:10:22"]

    if scene == "tm_bad":
        tm_status = "BAD"
        tm_snaps = 40
        tm_stamps = [f"2026-02-02 13:{m:02d}:00" for m in range(0, 10)] + ["2026-01-25 08:00:00"]

    if scene == "mixed":
        cpu_status = random.choice(["OK", "WARN"])
        cpu_max = random.choice([8.0, 55.0, 78.0])
        cpu_msg = "None >= 50%" if cpu_max < 50 else "AppleMusic(55%)"

        net_egress_ms = random.choice([18.0, 38.0, 120.0])
        net_egress = ("OK", "Egress test", f"TCP 1.1.1.1:443 in {net_egress_ms:.0f} ms") if net_egress_ms < 150 else ("WARN", "Egress test", "High latency TCP connect")

        disk_free_gb = random.choice([160.0, 40.0, 18.0])
        disk_free_pct = int(round((disk_free_gb / 460.0) * 100))
        disk_status = "OK" if disk_free_gb > 25 else "WARN" if disk_free_gb > 10 else "BAD"

        tm_snaps = random.choice([2, 9, 18, 33])
        tm_status = "OK" if tm_snaps < 10 else "WARN" if tm_snaps < 25 else "BAD"

    overall = "OK"
    for s in [cpu_status, net_default_route[0], net_egress[0], disk_status, tm_status]:
        if s == "BAD":
            overall = "BAD"
            break
        if s == "WARN" and overall != "BAD":
            overall = "WARN"

    # Sections in the same order as real mm
    sec_process = Section(
        title="Processes",
        checks=[Check(status=cpu_status, title="High CPU", message=cpu_msg, metric=float(cpu_max), metric_unit="%")],
    )

    sec_net = Section(
        title="Network",
        checks=[
            Check(net_default_route[0], net_default_route[1], net_default_route[2]),
            Check(net_ifaces[0], net_ifaces[1], net_ifaces[2], metric=float(0 if "None" in net_ifaces[2] else 1), metric_unit="count"),
            Check(net_gateway[0], net_gateway[1], net_gateway[2]),
            Check(net_egress[0], net_egress[1], net_egress[2], metric=float(net_egress_ms), metric_unit="ms") if net_egress_ms else Check(net_egress[0], net_egress[1], net_egress[2]),
            Check(net_dns[0], net_dns[1], net_dns[2]),
        ],
    )

    free_segs, used_segs = _bar_segments_from_free_pct(float(disk_free_pct))
    disk_msg = f"Total: 460G\nFree: {disk_free_gb:.0f}G ({disk_free_pct}%)\n[" + ("|" * 20) + "]"
    sec_disk = Section(
        title="Disk",
        checks=[Check(status=disk_status, title="Disk free (/)", message=disk_msg, bar_free_segments=free_segs, bar_used_segments=used_segs,
                     metric=float(disk_free_gb), metric_unit="GB")],
    )

    mount_checks = [Check("OK" if ok else "BAD", f"Mount {mp}", "Mounted" if ok else "Not mounted") for mp, ok in mounts_required]
    sec_mounts = Section(title="Mounts", checks=mount_checks)

    usb_checks: List[Check] = []
    if usb_present:
        usb_checks.append(Check("OK", "USB storage", f"{usb_name} @ {usb_mount} (USB)"))
        free_segs_u, used_segs_u = _bar_segments_from_free_pct(float(usb_free_pct))
        usb_msg = f"Total: 1.8T\nFree: {usb_free_gb:.0f}G ({usb_free_pct}%)\n[" + ("|" * 20) + "]"
        usb_checks.append(Check("OK", "USB required volumes", "All required USB volumes are mounted" if not usb_required_missing else "Missing: " + ", ".join(usb_required_missing)))
        usb_checks.append(Check(status=usb_status, title=f"USB free space ({usb_name})", message=usb_msg, bar_free_segments=free_segs_u, bar_used_segments=used_segs_u,
                               metric=float(usb_free_gb), metric_unit="GB"))
    else:
        usb_checks.append(Check("WARN", "USB storage", "No USB volumes detected"))
        usb_checks.append(Check("BAD", "USB required volumes", "Missing: " + ", ".join(usb_required_missing) if usb_required_missing else "Missing required volume(s)"))
        usb_checks.append(Check("OK", "USB free space", "No mounted USB volumes"))
    sec_usb = Section(title="USB Storage", checks=usb_checks)

    tm_checks: List[Check] = []
    tm_checks.append(Check(tm_status, "Local snapshots", f"{tm_snaps} snapshots", metric=float(tm_snaps), metric_unit="count"))
    if tm_stamps:
        newest = tm_stamps[0]
        oldest = tm_stamps[-1]
        show = tm_stamps[: min(10, len(tm_stamps))]
        msg = f"Newest: {newest}\nOldest: {oldest}\nShowing latest {len(show)}:\n  " + "\n  ".join(show)
        tm_checks.append(Check("OK", "Snapshot timestamps", msg))
    else:
        tm_checks.append(Check("WARN", "Snapshot timestamps", "No parseable snapshot timestamps found"))
    sec_tm = Section(title="Time Machine", checks=tm_checks)

    return Payload(now=now, sections=[sec_process, sec_net, sec_disk, sec_mounts, sec_usb, sec_tm], overall=overall, about=about)


if __name__ == "__main__":
    raise SystemExit(main())
