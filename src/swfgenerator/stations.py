"""Station registry — Switzerland-wide station handling driven by config/catalog.

There is no hardcoded Zurich/SMA default here. The active station comes from the
loaded config. The registry can additionally enrich/validate a station against the
CH2025 station catalog (``frontend/data/stations_catalog.json``, produced by the
``00_discover_ch2025_stations`` API stage) when present, and resolves a reference
DDY from the config or a bundled package resource.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional

from .config import StationConfig
from .paths import bundled_reference_ddy, project_root


def load_station_catalog() -> Dict[str, Any]:
    """Return the CH2025 station catalog if available, else an empty dict."""
    path = project_root() / "frontend" / "data" / "stations_catalog.json"
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def resolve_reference_ddy(station: StationConfig) -> Optional[Path]:
    """Resolve a reference DDY for the station.

    Priority: explicit config path -> bundled resource keyed by station code.
    Returns None if neither exists (the design-conditions stage then enforces the
    no-silent-fallback policy itself).
    """
    if station.reference_ddy is not None and Path(station.reference_ddy).is_file():
        return Path(station.reference_ddy)
    return bundled_reference_ddy(station.code)


def resolve_reference_epw(station: StationConfig) -> Optional[Path]:
    if station.reference_epw is not None and Path(station.reference_epw).is_file():
        return Path(station.reference_epw)
    return None


def station_summary(station: StationConfig) -> Dict[str, Any]:
    return {
        "code": station.code,
        "name": station.name,
        "latitude": station.latitude,
        "longitude": station.longitude,
        "elevation_m": station.elevation_m,
        "timezone_utc_offset": station.timezone_utc_offset,
        "source_ids": station.source_ids,
        "reference_ddy": str(resolve_reference_ddy(station)) if resolve_reference_ddy(station) else None,
        "reference_epw": str(resolve_reference_epw(station)) if resolve_reference_epw(station) else None,
    }
