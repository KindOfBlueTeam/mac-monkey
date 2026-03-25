# mac-monkey

A local-only macOS system health monitor for power users. Scans your machine for issues across processes, network, disk, mounts, USB storage, and Time Machine — then surfaces them via a terminal-style web UI or JSON CLI output.

No data leaves your machine. No automatic changes are made. Read-only, local-only by design.

---

## What it monitors

| Category | What's checked |
|---|---|
| **Processes** | High CPU processes (≥ 50% threshold) |
| **Network** | Default route, active interfaces, downlink/uplink speed, latency |
| **Disk** | Free space on root (`/`) with configurable warn/bad thresholds |
| **Mounts** | Required network mount presence and status |
| **USB Storage** | External volume detection, required volumes, free space |
| **Time Machine** | Local snapshot count and age |

Each check reports one of three states: `OK`, `WARN`, or `BAD`.

---

## Quick start

**1. Run setup (first time only)**

```bash
python3 mm_setup.py
```

The interactive wizard detects your hostname, discovers your mounts and volumes, and writes a host-specific config file (`mm_config.<hostname>.json`).

**2. Start the web UI**

```bash
python3 mm_main.py --mode web
```

Opens a local server at `http://127.0.0.1:8001`. The terminal-style dashboard auto-refreshes and shows sparkline trends for key metrics.

**3. Or get JSON output**

```bash
python3 mm_main.py --mode cli
```

Prints the full payload as JSON to stdout — useful for piping into other tools or scripts.

---

## All run modes

```bash
# Web dashboard (default port 8001)
python3 mm_main.py --mode web --host 127.0.0.1 --port 8001

# JSON to stdout
python3 mm_main.py --mode cli

# Self-test (verify checks run without errors)
python3 mm_main.py --mode test

# Demo server with scripted failure scenarios ("Drunken Monkey")
python3 mm_drunk.py --playlist flakey_nas --port 8002
python3 mm_drunk.py --list-playlists

# Interactive chaos/fuzz testing ("ChaosMonkey")
python3 mm_chaos.py --playlist bad_day --step-seconds 10 --loop
python3 mm_chaos.py --list-playlists
```

---

## Configuration

Config lives in `mm_config.<hostname>.json` (created by `mm_setup.py`). You can edit it directly to adjust:

- Warn/bad thresholds for disk, USB, CPU, Time Machine
- Enable/disable individual checks
- Required mount points and USB volumes to track

---

## JSON payload schema

The `/payload` endpoint (and `--mode cli`) returns:

```json
{
  "now": "<ISO timestamp>",
  "overall": "OK | WARN | BAD",
  "about": "<descriptive string>",
  "sections": [
    {
      "title": "Section Name",
      "checks": [
        {
          "status": "OK | WARN | BAD",
          "title": "Check title",
          "message": "Human-readable detail",
          "metric": 42.0,
          "metric_unit": "GB | % | ms | count",
          "trend": [40.0, 41.0, 42.0]
        }
      ]
    }
  ]
}
```

The `trend` array holds recent historical values used to render sparklines in the web UI.

---

## Safety guarantees

- **Read-only** — no remediation runs automatically
- **Local-only** — server binds to `127.0.0.1` by default
- **No telemetry** — nothing leaves the machine
- **No side effects** — safe to run repeatedly in the background

---

## Requirements

- macOS (uses `networkQuality`, `diskutil`, `tmutil`, and other native tools)
- Python 3.9+
- `pytest` (for tests only)

```bash
pip install -r requirements.txt
```

---

## Testing

```bash
pytest
```

Tests cover argument parser compatibility (`test_main_args.py`) and payload serialization.

---

## Web UI endpoints

| Endpoint | Description |
|---|---|
| `/` | Terminal-style dashboard |
| `/payload` | Raw JSON health data |
| `/help` | Full help and documentation |
