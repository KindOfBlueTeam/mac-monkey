MacMonkey

MacMonkey is a local, read-only macOS monitoring tool designed for power users. It exposes system health via both a terminal view and a local web dashboard.

Safety: Nothing is sent off the machine. No changes are made unless you explicitly run commands yourself.

Quick Start

Setup

Clone the repo: git clone https://github.com/KindOfBlueTeam/MacMonkey.git && cd MacMonkey/mm
Create a Python virtual environment: python3 -m venv .venv
Activate it: source .venv/bin/activate
Install dependencies: pip install -r requirements.txt
First Run

python3 mm_main.py --mode web
Open your browser to http://127.0.0.1:8765

Running MacMonkey

Web mode (default)

python3 mm_main.py --mode web
Starts a local web server on http://127.0.0.1:8765 (by default).

Web mode options:

--host <IP> — Bind address (default: 127.0.0.1)
--port <num> — Port number (default: 8765)
--interval <sec> — UI refresh interval in seconds (default: 10)
--debug — Enable debug logging
Example: Custom host and port

python3 mm_main.py --mode web --host 0.0.0.0 --port 3000 --interval 5
Web endpoints:

/ — Main web UI dashboard
/payload — Full JSON payload (for external tools)
/help — This help page
POST /action/networkquality/run — Force re-run network quality check (returns JSON)
CLI mode

python3 mm_main.py --mode cli
Prints the full JSON payload to stdout. ANSI color is included where applicable (for terminals).

Test mode

python3 mm_main.py --mode test
Runs internal checks without starting a server. Useful for debugging and automation.

Sections Explained

Web UI Features

Overall Status — Color-coded summary (OK / WARN / BAD)
Sparklines — Trend charts for metrics (disk free, USB free, CPU, network quality, etc.)
Auto-refresh — Dashboard updates every interval seconds (configurable)
Metric bars — Visual representation of capacity usage
ANSI color support — Messages may include terminal color codes
Processes

Shows processes exceeding CPU threshold (50% by default, configurable)
Displays top N processes by CPU usage
Only shows process names (not full command paths)
Trend sparklines in web UI
Network

Default route detection — Checks if a gateway is reachable
Active IPv4 interfaces — Count of active network adapters
Gateway ping — Verifies default gateway responds
Egress test — TCP connectivity to 1.1.1.1:443 (Cloudflare DNS)
DNS resolution — Tests actual name resolution
Network quality — Runs /usr/bin/networkQuality (Apple's built-in tool)
Measures downlink/uplink capacity (Mbps)
Responsiveness score
Idle latency
Results cached for 30 minutes (auto-refresh) or manually via web UI
Disk

Total and free space
Status bar showing usage (20 segments)
Configurable WARN/BAD thresholds (GB)
Sparkline trend history in web UI (last 60 measurements)
Mounts

Checks required mount points
Useful for NAS / network shares
USB Storage

Detects external USB volumes
Optional required-volume enforcement
Free space bars and trends
Time Machine

Total local snapshot count
Snapshot timestamps (newest / oldest)
No automatic cleanup or deletion
Configuration

MacMonkey is designed to work without configuration (using built-in defaults), but you can customize behavior per host.

Configuration file

File naming convention:

mm_config.<hostname>.json
Example: mm_config.mymac.json on hostname "mymac"

Interactive setup

To create or modify a config interactively:

python3 mm_setup.py
The setup wizard (SAFE MODE, read-only) lets you configure:

Mount checks — Which NAS/network mounts are required
Disk thresholds — WARN and BAD free space limits (GB)
Process checks — High CPU threshold %, number of top processes to show
USB storage checks — Which USB volumes are required, free space thresholds
Time Machine checks — Snapshot count WARN/BAD thresholds
If no config file exists, built-in defaults are used.

Drunken Monkey Mode 🥃

Drunken Monkey mode is a simulation for demos and testing.
It feeds mock data into the MacMonkey UI to demonstrate realistic failure cascades and recovery patterns.

python3 mm_drunk.py
Important: This mode only simulates data. It never touches real system state. Perfect for:

Live demos to stakeholders
Training and screenshots
Testing monitoring integrations
UI/UX feedback
Available playlists

List all presets:

python3 mm_drunk.py --list-playlists
Playlists include: bad_day, flakey_nas, heavy_workload, no_gateway, tm_silent_fail, usb_full, usb_tm_failure

Default port

Listens on http://127.0.0.1:8766 to avoid collisions with regular MacMonkey (8765). If that port is busy, auto-increments (8767, 8768, …).

Drunk mode endpoints

Main UI: /
JSON payload: /payload
Playlist control: /playlist
Advance step: /playlist/next
Reset playlist: /playlist/reset
Chaos Mode (Alternative Mock Server)

Similar to Drunken Monkey, but with a different UI and configuration options.

python3 mm_chaos.py --list-playlists
python3 mm_chaos.py --playlist flakey_nas --step-seconds 10 --loop
Listens on http://127.0.0.1:8766 by default (same as Drunken Monkey, auto-increments if busy).

JSON Payload & Integration

The JSON payload is the backbone of MacMonkey. All formats use the same schema:

{
  "now": "2026-03-25 12:34:56",
  "overall": "OK",
  "about": "Description of this payload",
  "sections": [
    {
      "title": "Section Name",
      "checks": [
        {
          "status": "OK|WARN|BAD",
          "title": "Check Title",
          "message": "Human-readable message",
          "metric": 123.4,
          "metric_unit": "GB",
          "trend": [120.5, 121.2, 123.4]
        }
      ]
    }
  ]
}
Access the payload from:

Web mode: curl http://127.0.0.1:8765/payload
CLI mode: python3 mm_main.py --mode cli
Programmatically: from mm_checks import build_payload
Troubleshooting

Port already in use

Specify a different port:

python3 mm_main.py --mode web --port 9999
networkQuality not available

The network quality check uses macOS's built-in /usr/bin/networkQuality (available on macOS 12+). On older systems, this check will show as unavailable but won't block other checks.

Check mm_log.txt

The web server logs errors to mm_log.txt in the mm directory.

Permissions

MacMonkey uses read-only system calls. If a check fails, it will report the failure gracefully. No sudo/admin access is required.

For more info: GitHub Repository
