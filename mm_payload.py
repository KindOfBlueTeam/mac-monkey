"""
mm_payload.py

Payload dataclasses and serialization helpers for MacMonkey.

Design goals:
- Stable JSON schema for /payload (usable by other tools)
- Optional fields for web-only visual enhancements:
  - metric: a numeric value for charting
  - trend: list of recent metric values for sparklines
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict, is_dataclass
from typing import Any, Dict, List, Optional


@dataclass
class Check:
    status: str
    title: str
    message: str
    message_ansi: Optional[str] = None

    # Web UI helpers (optional)
    bar_free_segments: Optional[int] = None
    bar_used_segments: Optional[int] = None

    # Sparkline support (optional)
    metric: Optional[float] = None          # a single numeric value (e.g., free_gb)
    metric_unit: Optional[str] = None       # e.g., "GB", "%", "count"
    trend: Optional[List[float]] = None     # filled by web server (history)

    def to_dict(self) -> Dict[str, Any]:
        return _to_plain_dict(self)


@dataclass
class Section:
    title: str
    checks: List[Check] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return _to_plain_dict(self)


@dataclass
class Payload:
    now: str
    sections: List[Section]
    overall: str
    about: str

    def to_dict(self) -> Dict[str, Any]:
        return _to_plain_dict(self)


def _to_plain_dict(obj: Any) -> Any:
    """
    Convert dataclasses → plain dicts/lists recursively.
    Drops None values to keep JSON clean.
    """
    if is_dataclass(obj):
        raw = asdict(obj)
        return _drop_none(raw)
    if isinstance(obj, dict):
        return _drop_none({k: _to_plain_dict(v) for k, v in obj.items()})
    if isinstance(obj, list):
        return [_to_plain_dict(v) for v in obj]
    return obj


def _drop_none(d: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for k, v in d.items():
        if v is None:
            continue
        if isinstance(v, dict):
            vv = _drop_none(v)
            if vv:
                out[k] = vv
            else:
                out[k] = vv
        else:
            out[k] = v
    return out
