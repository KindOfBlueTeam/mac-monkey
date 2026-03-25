#!/usr/bin/env python3
"""
mm_main.py — entry point for MacMonkey (mm)

Modes:
  --mode web   : start local-only web UI server (default)
  --mode cli   : print payload JSON to stdout
  --mode test  : simple self-test / debug output

Back-compat aliases:
  --web, --cli, --test  (set mode accordingly)

Notes:
  - Web server binds to host/port you specify (default 127.0.0.1:8765).
  - Payload generation lives in mm_checks.build_payload().
"""

from __future__ import annotations

import argparse
import json
import sys

import mm_checks
import mm_web


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="MacMonkey (mm)")

    # Canonical mode flag
    p.add_argument("--mode", choices=["web", "cli", "test"], default="web",
                   help="Run mode (default: web)")

    # Back-compat convenience flags (override --mode if present)
    p.add_argument("--web", action="store_const", const="web", dest="mode",
                   help="Alias for --mode web")
    p.add_argument("--cli", action="store_const", const="cli", dest="mode",
                   help="Alias for --mode cli")
    p.add_argument("--test", action="store_const", const="test", dest="mode",
                   help="Alias for --mode test")

    # Web options
    p.add_argument("--host", default="127.0.0.1",
                   help="Web bind host (default: 127.0.0.1)")
    p.add_argument("--port", type=int, default=8765,
                   help="Web bind port (default: 8765)")
    p.add_argument("--interval", type=int, default=10,
                   help="UI refresh interval seconds (default: 10)")

    # Debug toggle (applies mainly to web server logging / extra output)
    p.add_argument("--debug", action="store_true", help="Enable debug mode")

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.mode == "cli":
        payload = mm_checks.build_payload()
        print(json.dumps(payload.to_dict(), indent=2))
        return 0

    if args.mode == "test":
        # Minimal self-test: build payload and confirm it serializes.
        payload = mm_checks.build_payload()
        d = payload.to_dict()
        # Print a compact summary so you can quickly see it worked.
        print("OK: payload built and serialized")
        print(f"overall={d.get('overall')} sections={len(d.get('sections', []))}")
        # Also print JSON (pretty) because it's often helpful in test mode.
        print(json.dumps(d, indent=2))
        return 0

    # Default: web
    debug = bool(args.debug)
    mm_web.serve(
        host=args.host,
        port=args.port,
        interval=args.interval,
        debug=debug,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
