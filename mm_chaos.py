#!/usr/bin/env python3
"""
mm_chaos.py — "ChaosMonkey" interactive payload fuzzer for MacMonkey

Now supports:
  --playlist <preset_name>   e.g. --playlist flakey_nas
  --playlist a,b,c           explicit comma-list still works
  --list-playlists           show presets

See /help once running.
"""

from __future__ import annotations

import argparse
import json
import random
import time
import traceback
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Dict, List, Optional, Tuple


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8002


# ----------------------------
# Built-in playlist presets
# ----------------------------

# These are intentionally short, “workstation realistic”, and map to existing scenarios.
PLAYLIST_PRESETS: Dict[str, List[str]] = {
    # NAS mount drops intermittently -> retries spike CPU -> TM snapshots climb -> disk pressure.
    "flakey_nas": ["all_ok", "mounts_missing", "cpu_hot", "tm_warn", "disk_warn", "mixed", "all_ok"],

    # Default route/gateway disappears (VPN drop / wifi hiccup). Visible symptom for mm today is
    # often: mounts missing + services retrying.
    "no_gateway": ["all_ok", "mounts_missing", "cpu_hot", "mixed", "all_ok"],

    # USB Time Machine / external backup drive disconnects -> local snapshots balloon -> disk goes BAD.
    "usb_tm_failure": ["all_ok", "usb_missing", "tm_warn", "disk_warn", "tm_bad", "disk_bad", "mixed", "all_ok"],

    # Heavy creative workload (renders/exports) -> CPU hot -> temp/cache grows -> disk warn -> TM warn.
    "heavy_workload": ["all_ok", "cpu_hot", "disk_warn", "tm_warn", "cpu_hot", "mixed", "all_ok"],

    # Silent Time Machine destination unreachable -> snapshots climb invisibly -> disk BAD.
    "tm_silent_fail": ["all_ok", "tm_warn", "tm_bad", "disk_warn", "disk_bad", "tm_bad", "mixed", "all_ok"],

    # External drive present but nearly full -> WARN/BAD; user frees space -> recovery.
    "usb_full": ["all_ok", "usb_low_space", "usb_low_space", "disk_warn", "mixed", "all_ok"],

    # More “chaotic” but plausible mixed day
    "bad_day": ["all_ok", "cpu_hot", "mounts_missing", "usb_low_space", "tm_warn", "disk_warn", "mixed", "all_ok"],
}


# ----------------------------
# Minimal terminal UI (served at /)
# ----------------------------

TERMINAL_UI_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>MacMonkey Chaos</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
:root {
  --bg: #000;
  --fg: #33ff33;
  --fg-dim: #1fbf1f;
  --warn: #ffd84d;
  --bad: #ff4d4d;
  --border: #0a2a0a;
  --panel: #001000;
}
html, body { background: var(--bg); color: var(--fg);
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  font-size: 14px; margin: 0; padding: 12px;
}
a{ color: var(--warn); text-decoration: none; } a:hover{ text-decoration: underline; }
header{
  border: 1px solid var(--border); border-radius: 8px;
  padding: 10px 12px; background: var(--panel);
  display:flex; justify-content: space-between; align-items: center; gap: 12px;
}
h1{ margin:0; font-size: 16px; }
.meta{ color: var(--fg-dim); font-size: 12px; }
button{
  background:#001800; color: var(--fg);
  border: 1px solid var(--border); border-radius: 6px; padding: 4px 10px;
  cursor:pointer;
}
button:hover{ background:#002400; }
.overall{ padding:2px 10px; border-radius:999px; font-weight:bold; }
.overall.OK{ color: var(--fg); }
.overall.WARN{ color: var(--warn); }
.overall.BAD{ color: var(--bad); }

details{ border: 1px solid var(--border); border-radius: 8px; margin-top: 10px; background:#000; }
summary{ cursor:pointer; padding: 8px 12px; font-weight:bold; background:#001000; user-select:none; }
summary::-webkit-details-marker{ display:none; }
.check{
  display:grid; grid-template-columns: 80px 1fr; gap: 12px;
  padding: 6px 12px; border-top: 1px dashed var(--border);
}
.status.OK{ color: var(--fg); }
.status.WARN{ color: var(--warn); }
.status.BAD{ color: var(--bad); font-weight: bold; }
.footer{ margin-top: 12px; color: var(--fg-dim); font-size: 12px; }
code{ background:#001000; border:1px solid var(--border); padding:1px 6px; border-radius: 7px; }
</style>
</head>
<body>
<header>
  <div>
    <h1>MacMonkey Chaos</h1>
    <div id="meta" class="meta">Loading…</div>
  </div>
  <div style="display:flex; gap:10px; align-items:center;">
    <span id="overall" class="overall OK">OK</span>
    <button onclick="refresh()">Refresh</button>
  </div>
</header>

<div class="footer">
  Data: <code>/payload</code> · Help: <a href="/help">/help</a>
</div>

<div id="sections"></div>

<script>
const POLL_SECONDS = Number("{{MM_POLL_SECONDS}}") || 3;
const STATE_KEY = "mm_chaos_section_state_v1";

function loadState(){ try { return JSON.parse(localStorage.getItem(STATE_KEY) || "{}"); } catch { return {}; } }
function saveState(s){ try { localStorage.setItem(STATE_KEY, JSON.stringify(s)); } catch {} }

function worst(checks){
  const order = { OK:0, WARN:1, BAD:2 };
  let w = "OK";
  for (const c of (checks||[])) if (order[c.status] > order[w]) w = c.status;
  return w;
}

async function refresh(){
  try{
    const res = await fetch("/payload", { cache: "no-store" });
    const payload = await res.json();
    render(payload);
  }catch(e){
    document.getElementById("meta").textContent = "Error loading payload";
  }
}

function render(p){
  document.getElementById("meta").textContent = `${p.now} · ${p.about || ""}`;
  const overall = document.getElementById("overall");
  overall.textContent = p.overall;
  overall.className = "overall " + p.overall;

  const container = document.getElementById("sections");
  container.innerHTML = "";
  const state = loadState();

  for(const sec of (p.sections||[])){
    const d = document.createElement("details");
    const w = worst(sec.checks);

    d.open = (state[sec.title] !== undefined) ? state[sec.title] : (w !== "OK");
    d.addEventListener("toggle", () => { state[sec.title] = d.open; saveState(state); });

    const s = document.createElement("summary");
    s.textContent = `${sec.title} [${w}]`;
    d.appendChild(s);

    for(const chk of (sec.checks||[])){
      const row = document.createElement("div"); row.className = "check";
      const st = document.createElement("div"); st.className = "status " + chk.status; st.textContent = chk.status;
      const body = document.createElement("div");
      body.innerHTML = `<strong>${chk.title}</strong><br>${(chk.message || "").replaceAll("\\n","<br>")}`;
      row.appendChild(st); row.appendChild(body);
      d.appendChild(row);
    }
    container.appendChild(d);
  }
}

refresh();
setInterval(refresh, Math.max(2, POLL_SECONDS) * 1000);
</script>
</body>
</html>
"""


HELP_HTML = """<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>MacMonkey Chaos Help</title>
<style>
body{ background:#000; color:#33ff33; font-family: ui-monospace, Menlo, Monaco, Consolas, monospace;
      margin:16px; font-size:14px; line-height:1.45; }
a{ color:#ffd84d; text-decoration:none } a:hover{ text-decoration:underline }
code,pre{ background:#001000; border:1px solid #0a2a0a; padding:8px 10px; border-radius:10px; overflow-x:auto; }
</style>
</head>
<body>
<h2>MacMonkey Chaos</h2>
<p>Mock-only sandbox for exercising the MacMonkey UI and payload consumers.</p>

<p><strong>Endpoints</strong></p>
<ul>
  <li><code>/</code> — terminal UI</li>
  <li><code>/payload</code> — payload JSON</li>
  <li><code>/help</code> — this page</li>
</ul>

<p><strong>Playlist presets</strong></p>
<pre>python3 mm_chaos.py --playlist flakey_nas --step-seconds 10 --loop</pre>

<p><strong>Manual playlist</strong></p>
<pre>python3 mm_chaos.py --playlist all_ok,mounts_missing,cpu_hot,tm_warn,disk_warn,mixed --step-seconds 10</pre>

<p>Run <code>python3 mm_chaos.py --list-playlists</code> to see presets.</p>
<p><a href="/">Back</a></p>
</body></html>
"""


# ----------------------------
# Scenarios
# ----------------------------

SCENARIOS = [
    "mixed",
    "all_ok",
    "disk_warn",
    "disk_bad",
    "mounts_missing",
    "cpu_hot",
    "usb_missing",
    "usb_low_space",
    "tm_warn",
    "tm_bad",
]


@dataclass
class ChaosConfig:
    interval: int
    scenario: str
    glitch: float
    seed: Optional[int]
    playlist: List[str]
    step_seconds: int
    loop: bool
    shuffle: bool
    playlist_name: Optional[str]


def worst_status(sections: List[Dict[str, Any]]) -> str:
    order = {"OK": 0, "WARN": 1, "BAD": 2}
    worst = "OK"
    for sec in sections:
        for chk in sec.get("checks", []) or []:
            s = (chk.get("status") or "OK")
            if order.get(s, 0) > order[worst]:
                worst = s
    return worst


def pick_weighted(rng: random.Random, items: List[Tuple[Any, int]]) -> Any:
    total = sum(w for _, w in items)
    x = rng.randint(1, total)
    upto = 0
    for item, w in items:
        upto += w
        if x <= upto:
            return item
    return items[-1][0]


def scenario_bias(scenario: str) -> Dict[str, int]:
    return {
        "all_ok": {"bad": 0, "warn": 0},
        "disk_warn": {"bad": 0, "warn": 6},
        "disk_bad": {"bad": 7, "warn": 2},
        "mounts_missing": {"bad": 7, "warn": 1},
        "cpu_hot": {"bad": 0, "warn": 7},
        "usb_missing": {"bad": 7, "warn": 2},
        "usb_low_space": {"bad": 5, "warn": 5},
        "tm_warn": {"bad": 0, "warn": 7},
        "tm_bad": {"bad": 7, "warn": 3},
        "mixed": {"bad": 3, "warn": 3},
    }.get(scenario, {"bad": 3, "warn": 3})


def mock_payload(cfg: ChaosConfig, rng: random.Random, scenario: str, stage_info: str) -> Dict[str, Any]:
    now = time.strftime("%Y-%m-%d %H:%M:%S")

    playlist_tag = ""
    if cfg.playlist_name:
        playlist_tag = f"playlist={cfg.playlist_name} | "

    about = (
        f"MacMonkey Chaos | {playlist_tag}{stage_info} | scenario={scenario} | "
        f"seed={cfg.seed if cfg.seed is not None else 'random'}"
    )

    bias = scenario_bias(scenario)

    def ok_warn_bad() -> str:
        roll = rng.random()
        p_bad = min(0.60, 0.03 + (bias["bad"] * 0.06))
        p_warn = min(0.80, 0.06 + (bias["warn"] * 0.07))
        if roll < p_bad:
            return "BAD"
        if roll < p_bad + p_warn:
            return "WARN"
        return "OK"

    # Mounts
    mounts_status = "OK"
    mounts_msg = "Mounted"
    if scenario == "mounts_missing":
        mounts_status, mounts_msg = "BAD", "Not mounted"
    else:
        if ok_warn_bad() == "BAD":
            mounts_status, mounts_msg = "BAD", "Not mounted"
    mounts_section = {"title": "Mounts", "checks": [{"status": mounts_status, "title": "Mount /Volumes/NAS", "message": mounts_msg}]}

    # Disk
    if scenario == "disk_warn":
        disk_state = "WARN"
    elif scenario == "disk_bad":
        disk_state = "BAD"
    elif scenario == "all_ok":
        disk_state = "OK"
    else:
        disk_state = ok_warn_bad()
        if disk_state == "BAD" and rng.random() < 0.6:
            disk_state = "WARN"
    disk_free = {"OK": 220.4, "WARN": 18.2, "BAD": 6.7}[disk_state]
    disk_section = {"title": "Disk", "checks": [{"status": disk_state, "title": "Disk free (/)", "message": f"{disk_free:.1f} GB free"}]}

    # Processes (clean names)
    if scenario == "cpu_hot":
        cpu_state = "WARN"
    elif scenario == "all_ok":
        cpu_state = "OK"
    else:
        cpu_state = pick_weighted(rng, [("OK", 7), ("WARN", 3), ("BAD", 0)])
    if cpu_state == "OK":
        cpu_msg = "None >= 50%"
    else:
        offenders = ["AppleMusic", "kernel_task", "WindowServer", "python3", "backupd", "mds_stores"]
        rng.shuffle(offenders)
        cpu_msg = ", ".join([f"{offenders[0]} (62%)", f"{offenders[1]} (51%)"])
    proc_section = {"title": "Processes", "checks": [{"status": cpu_state, "title": "High CPU", "message": cpu_msg}]}

    # USB
    if scenario == "usb_missing":
        usb_present = False
    elif scenario == "all_ok":
        usb_present = True
    else:
        usb_present = rng.random() > 0.15
    usb_free_state = "OK"
    if scenario == "usb_low_space":
        usb_free_state = pick_weighted(rng, [("WARN", 5), ("BAD", 4), ("OK", 1)])
    usb_vol = "USBDISK"
    usb_mp = "/Volumes/USBDISK"
    usb_proto = "USB"
    usb_free_gb = {"OK": 1653.8, "WARN": 18.5, "BAD": 7.9}[usb_free_state]

    usb_checks: List[Dict[str, Any]] = []
    if usb_present:
        usb_checks.append({"status": "OK", "title": "USB storage", "message": f"{usb_vol} @ {usb_mp} ({usb_proto})"})
        usb_checks.append({"status": "OK", "title": "USB required volumes", "message": "All required USB volumes are mounted"})
        usb_checks.append({"status": usb_free_state, "title": "USB free space", "message": f"{usb_vol}: {usb_free_gb:.1f} GB ({usb_free_state})"})
    else:
        usb_checks.append({"status": "WARN", "title": "USB storage", "message": "No mounted external/USB volumes detected"})
        usb_checks.append({"status": "OK", "title": "USB required volumes", "message": "None configured"})
        usb_checks.append({"status": "OK", "title": "USB free space", "message": "No mounted USB volumes"})
    usb_section = {"title": "USB Storage", "checks": usb_checks}

    # Time Machine
    if scenario == "tm_warn":
        tm_state = "WARN"
    elif scenario == "tm_bad":
        tm_state = "BAD"
    elif scenario == "all_ok":
        tm_state = "OK"
    else:
        tm_state = pick_weighted(rng, [("OK", 5), ("WARN", 4), ("BAD", 1)])
    snap_count = {"OK": 2, "WARN": 14, "BAD": 31}[tm_state]

    tm_checks = [
        {"status": "OK", "title": "Destination", "message": "Configured"},
        {"status": tm_state, "title": "Local snapshots", "message": f"{snap_count} snapshots"},
    ]
    if tm_state in ("WARN", "BAD"):
        tm_checks.append({
            "status": "OK",
            "title": "Remediation",
            "message": "Suggested remediation (manual):\n"
                       "  tmutil listlocalsnapshots /\n"
                       "  sudo tmutil thinlocalsnapshots / 20000000000 4\n"
                       "  # or delete a specific snapshot:\n"
                       "  # sudo tmutil deletelocalsnapshots <snapshot-id>"
        })
    tm_section = {"title": "Time Machine", "checks": tm_checks}

    sections = [mounts_section, disk_section, proc_section, usb_section, tm_section]
    overall = worst_status(sections)

    payload: Dict[str, Any] = {"now": now, "sections": sections, "overall": overall, "about": about}

    # Optional glitches
    if cfg.glitch > 0 and rng.random() < cfg.glitch:
        glitch_type = rng.choice(["drop_message", "unknown_status", "drop_section_checks", "empty_sections"])
        if glitch_type == "drop_message":
            sec = rng.choice(payload["sections"])
            chk = rng.choice(sec.get("checks", []))
            chk.pop("message", None)
            payload["about"] += " | glitch=drop_message"
        elif glitch_type == "unknown_status":
            sec = rng.choice(payload["sections"])
            chk = rng.choice(sec.get("checks", []))
            chk["status"] = rng.choice(["WAT", "???", "BROKEN"])
            payload["about"] += " | glitch=unknown_status"
        elif glitch_type == "drop_section_checks":
            sec = rng.choice(payload["sections"])
            sec.pop("checks", None)
            payload["about"] += " | glitch=drop_section_checks"
        elif glitch_type == "empty_sections":
            payload["sections"] = []
            payload["overall"] = "OK"
            payload["about"] += " | glitch=empty_sections"

    return payload


def html_with_interval(html: str, interval: int) -> str:
    return html.replace("{{MM_POLL_SECONDS}}", str(int(interval)))


def resolve_playlist_arg(raw: str) -> Tuple[Optional[str], List[str]]:
    """
    Accept:
      --playlist flakey_nas          -> preset
      --playlist all_ok,mixed,...    -> explicit list

    Returns: (playlist_name, playlist_items)
    """
    s = (raw or "").strip()
    if not s:
        return None, []

    # preset name?
    if s in PLAYLIST_PRESETS:
        return s, list(PLAYLIST_PRESETS[s])

    # explicit list
    items = [x.strip() for x in s.split(",") if x.strip()]
    return None, items


class ChaosServer:
    def __init__(self, host: str, port: int, cfg: ChaosConfig):
        self.host = host
        self.port = port
        self.cfg = cfg
        self.rng = random.Random(cfg.seed) if cfg.seed is not None else random.Random()
        self.started_at = time.time()

        self.playlist: List[str] = [s for s in (cfg.playlist or []) if s in SCENARIOS]

        if self.playlist and cfg.shuffle:
            self.rng.shuffle(self.playlist)

    def current_stage(self) -> Tuple[str, str]:
        if not self.playlist:
            return self.cfg.scenario, "stage=single"

        n = len(self.playlist)
        step = max(1, int(self.cfg.step_seconds))
        elapsed = max(0.0, time.time() - self.started_at)

        idx = int(elapsed // step)
        if idx >= n:
            if self.cfg.loop:
                idx = idx % n
            else:
                idx = n - 1

        scenario = self.playlist[idx]
        next_in = step - (elapsed % step)

        if not self.cfg.loop and idx == n - 1:
            stage_info = f"stage={idx+1}/{n} (final)"
        else:
            stage_info = f"stage={idx+1}/{n} next_in={int(next_in)}s"

        return scenario, stage_info

    def make_handler(self):
        server = self

        class Handler(BaseHTTPRequestHandler):
            server_version = "MacMonkeyChaos/0.3"

            def log_message(self, fmt: str, *args) -> None:
                return

            def _send(self, status: int, body: bytes, content_type: str) -> None:
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _text(self, status: int, text: str, ctype: str) -> None:
                self._send(status, text.encode("utf-8"), ctype)

            def _json(self, obj: Any) -> None:
                data = json.dumps(obj, indent=2).encode("utf-8")
                self._send(200, data, "application/json; charset=utf-8")

            def do_GET(self) -> None:
                try:
                    path = (self.path or "/").split("?", 1)[0]

                    if path == "/":
                        html = html_with_interval(TERMINAL_UI_HTML, server.cfg.interval)
                        return self._text(200, html, "text/html; charset=utf-8")

                    if path == "/help":
                        return self._text(200, HELP_HTML, "text/html; charset=utf-8")

                    if path == "/payload":
                        scenario, stage_info = server.current_stage()
                        p = mock_payload(server.cfg, server.rng, scenario, stage_info)
                        return self._json(p)

                    if path == "/health":
                        return self._text(200, "OK\n", "text/plain; charset=utf-8")

                    return self._text(404, "Not Found\n", "text/plain; charset=utf-8")

                except Exception as e:
                    tb = traceback.format_exc()
                    return self._text(500, f"Internal Server Error: {e}\n\n{tb}\n", "text/plain; charset=utf-8")

        return Handler

    def serve(self) -> None:
        httpd = HTTPServer((self.host, self.port), self.make_handler())
        print(f"MacMonkey Chaos UI: http://{self.host}:{self.port}")
        print(f"Help: http://{self.host}:{self.port}/help")

        if self.playlist:
            name = self.cfg.playlist_name or "<custom>"
            print(f"Playlist: {name} = {','.join(self.playlist)}")
            print(f"Step seconds: {max(1, int(self.cfg.step_seconds))}  Loop: {self.cfg.loop}  Shuffle: {self.cfg.shuffle}")
        else:
            print(f"Scenario: {self.cfg.scenario}")

        print("Press Ctrl+C to stop.")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            httpd.server_close()


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="MacMonkey Chaos (mock payload server)")

    p.add_argument("--host", default=DEFAULT_HOST, help="Bind host (default: 127.0.0.1)")
    p.add_argument("--port", type=int, default=DEFAULT_PORT, help="Bind port (default: 8766)")
    p.add_argument("--interval", type=int, default=3, help="UI poll interval seconds (default: 3)")
    p.add_argument("--seed", type=int, default=None, help="Random seed for reproducible runs")

    p.add_argument(
        "--scenario",
        default="mixed",
        choices=SCENARIOS,
        help="Scenario bias (default: mixed). Ignored if --playlist is provided.",
    )

    p.add_argument(
        "--playlist",
        default="",
        help="Either a preset name (e.g. flakey_nas) or a comma-list of scenarios.",
    )
    p.add_argument("--list-playlists", action="store_true", help="List playlist presets and exit")
    p.add_argument("--step-seconds", type=int, default=10, help="Advance playlist stage every N seconds (default: 10)")
    p.add_argument("--loop", action="store_true", help="Loop playlist when it ends")
    p.add_argument("--shuffle", action="store_true", help="Shuffle playlist order once at startup (seeded)")

    p.add_argument("--glitch", type=float, default=0.0, help="Chance [0..1] to inject mild schema glitches (default: 0)")
    return p


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)

    if args.list_playlists:
        print("Playlist presets:")
        for name in sorted(PLAYLIST_PRESETS.keys()):
            seq = ",".join(PLAYLIST_PRESETS[name])
            print(f"  {name:14}  {seq}")
        return 0

    glitch = max(0.0, min(1.0, float(args.glitch)))

    playlist_name, playlist_items = resolve_playlist_arg(args.playlist)

    cfg = ChaosConfig(
        interval=int(args.interval),
        scenario=str(args.scenario),
        glitch=glitch,
        seed=args.seed,
        playlist=playlist_items,
        step_seconds=max(1, int(args.step_seconds)),
        loop=bool(args.loop),
        shuffle=bool(args.shuffle),
        playlist_name=playlist_name,
    )

    ChaosServer(args.host, int(args.port), cfg).serve()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
