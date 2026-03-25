"""
mm_web.py

Local-only web server for MacMonkey.

Serves:
- /            : web UI (mm_ui.html)
- /help        : help page (mm_help.html)
- /payload     : JSON payload (monitoring data)
- POST /action/networkquality/run : force-run networkQuality now (cached), returns JSON

Notes:
- Local-only server (bind to 127.0.0.1 by default in mm_main.py)
- Does NOT execute privileged remediation commands.
- Writes errors to mm_log.txt for debugging.
"""

from __future__ import annotations

import dataclasses
import json
import os
import time
import traceback
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Deque, Dict, Tuple
from urllib.parse import urlparse

import mm_checks

HISTORY_LEN_DEFAULT = 60
LOG_PATH_DEFAULT = "mm_log.txt"


def serve(host: str, port: int, interval: int, debug: bool = False) -> None:
    server = _MacMonkeyServer((host, port), _Handler)
    server.interval = max(2, int(interval))
    server.debug = bool(debug)

    server.ui_html = _read_file_local("mm_ui.html", default=_DEFAULT_UI_HTML)
    server.help_html = _read_file_local("mm_help.html", default=_DEFAULT_HELP_HTML)

    server.history_len = HISTORY_LEN_DEFAULT
    server.trends = {}  # (section_title, check_title) -> deque[float]
    server.log_path = LOG_PATH_DEFAULT

    print(f"MacMonkey web UI: http://{host}:{port}")
    print(f"Help: http://{host}:{port}/help")
    print("Press Ctrl+C to stop.")

    try:
        server.serve_forever()
    finally:
        server.server_close()


class _MacMonkeyServer(ThreadingHTTPServer):
    interval: int
    debug: bool
    ui_html: str
    help_html: str
    history_len: int
    trends: Dict[Tuple[str, str], Deque[float]]
    log_path: str


def _jsonable(obj: Any) -> Any:
    """
    Convert payload structures into JSON-serializable Python types.

    Handles:
    - dict / list / tuple / primitives
    - dataclasses (asdict)
    - objects with to_dict()
    - objects with __dict__
    """
    if obj is None:
        return None
    if isinstance(obj, (str, int, float, bool)):
        return obj
    if isinstance(obj, dict):
        return {str(k): _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_jsonable(v) for v in obj]

    # Prefer explicit to_dict()
    td = getattr(obj, "to_dict", None)
    if callable(td):
        return _jsonable(td())

    # Dataclass
    if dataclasses.is_dataclass(obj):
        return _jsonable(dataclasses.asdict(obj))

    # Fallback: __dict__
    d = getattr(obj, "__dict__", None)
    if isinstance(d, dict):
        return _jsonable(d)

    # Last resort: stringify
    return str(obj)


class _Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, body: bytes, content_type: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_text(self, code: int, text: str, content_type: str) -> None:
        self._send(code, text.encode("utf-8", errors="replace"), content_type)

    def _send_json(self, code: int, obj: Any) -> None:
        self._send_text(code, json.dumps(obj, indent=2), "application/json; charset=utf-8")

    def _read_request_body(self) -> bytes:
        try:
            n = int(self.headers.get("Content-Length", "0") or "0")
        except Exception:
            n = 0
        if n <= 0:
            return b""
        return self.rfile.read(n)

    def log_message(self, fmt: str, *args: Any) -> None:
        srv: _MacMonkeyServer = self.server  # type: ignore[assignment]
        if getattr(srv, "debug", False):
            super().log_message(fmt, *args)

    def _log_exc(self, prefix: str, exc: BaseException) -> None:
        srv: _MacMonkeyServer = self.server  # type: ignore[assignment]
        try:
            ts = time.strftime("%Y-%m-%d %H:%M:%S")
            blob = (
                f"[{ts}] {prefix}: {exc}\n"
                f"{traceback.format_exc()}\n"
                f"---\n"
            )
            with open(srv.log_path, "a", encoding="utf-8") as f:
                f.write(blob)
        except Exception:
            pass

    def do_GET(self) -> None:
        srv: _MacMonkeyServer = self.server  # type: ignore[assignment]
        path = urlparse(self.path).path

        if path in ("/", "/index.html"):
            self._send_text(200, srv.ui_html, "text/html; charset=utf-8")
            return

        if path == "/help":
            self._send_text(200, srv.help_html, "text/html; charset=utf-8")
            return

        if path == "/payload":
            try:
                payload_obj = mm_checks.build_payload()
                data = _jsonable(payload_obj)

                if not isinstance(data, dict):
                    raise TypeError(f"build_payload() normalized to non-dict: {type(data).__name__}")

                _attach_trends(srv, data)
                self._send_json(200, data)
            except Exception as e:
                self._log_exc("GET /payload failed", e)
                self._send_json(500, {"error": str(e), "hint": "Check mm_log.txt for traceback"})
            return

        self._send_text(404, "Not found\n", "text/plain; charset=utf-8")

    def do_POST(self) -> None:
        path = urlparse(self.path).path

        if path == "/action/networkquality/run":
            try:
                _ = self._read_request_body()

                cfg = mm_checks.load_config() if hasattr(mm_checks, "load_config") else {}
                checks_cfg = (cfg.get("checks") or {}) if isinstance(cfg, dict) else {}
                net_cfg = (checks_cfg.get("network") or {}) if isinstance(checks_cfg, dict) else {}

                cache_path = str(net_cfg.get("networkquality_cache_path", "~/.cache/mm/networkquality.json"))
                interval_m = int(net_cfg.get("networkquality_interval_minutes", 30))
                timeout_s = int(net_cfg.get("networkquality_timeout_sec", 120))

                if not hasattr(mm_checks, "run_networkquality"):
                    raise RuntimeError("mm_checks.run_networkquality() is not implemented")

                result = mm_checks.run_networkquality(
                    force=True,
                    cache_path=cache_path,
                    interval_minutes=interval_m,
                    timeout_sec=timeout_s,
                )
                self._send_json(200, _jsonable(result))
            except Exception as e:
                self._log_exc("POST /action/networkquality/run failed", e)
                self._send_json(500, {"error": str(e), "hint": "Check mm_log.txt for traceback"})
            return

        self._send_text(404, "Not found\n", "text/plain; charset=utf-8")


def _attach_trends(server: _MacMonkeyServer, payload_dict: Dict[str, Any]) -> None:
    """
    Add a `trend` list to each check that includes numeric `metric`.
    History is stored in server.trends in-memory.

    Expects payload_dict to already be JSONable (dicts/lists), which _jsonable() ensures.
    """
    sections = payload_dict.get("sections") or []
    for sec in sections:
        if not isinstance(sec, dict):
            continue
        sec_title = sec.get("title", "")
        checks = sec.get("checks") or []
        for chk in checks:
            if not isinstance(chk, dict):
                continue
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


def _read_file_local(path: str, default: str) -> str:
    try:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return f.read()
    except Exception:
        pass
    return default


_DEFAULT_UI_HTML = """<!doctype html><html><body><pre>mm_ui.html missing</pre></body></html>"""
_DEFAULT_HELP_HTML = """<!doctype html><html><body><pre>mm_help.html missing</pre></body></html>"""