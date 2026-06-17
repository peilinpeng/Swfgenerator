"""Config loading and validation for the swfgenerator workflow.

The config is the single source of truth for station, scenario, data-source mode,
cache policy, reference files, output root, and the EPW/design-condition policies.
Core code is station-agnostic: there are no Zurich/SMA defaults baked in here —
the station must be supplied by the config (Zurich/SMA is only an *example* config).

Fields that belong to the intended design but are not yet wired into the pipeline
are accepted and surfaced under ``Config.reserved`` so callers can see them without
the loader pretending they are implemented.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from .paths import project_root


class ConfigError(ValueError):
    pass


# Keys that are part of the intended schema but may not be fully wired yet.
# Loaded and echoed back, never silently assumed complete.
RESERVED_HINTS = {
    "data_source.local_overrides": "optional explicit local input files (debug/override)",
    "epw_writer.morphed_fields": "passed through to the EPW writer when wired per-stage",
}


@dataclass
class StationConfig:
    code: str
    name: str
    latitude: float
    longitude: float
    elevation_m: float
    timezone_utc_offset: float
    source_ids: Dict[str, Any] = field(default_factory=dict)
    reference_epw: Optional[Path] = None
    reference_ddy: Optional[Path] = None


@dataclass
class Config:
    path: Path
    station: StationConfig
    ref_state: str
    gwls: List[str]
    baseline_start_year: int
    baseline_end_year: int
    profiles: List[str]
    data_source_mode: str           # "api" | "local"
    cache_enabled: bool
    cache_dir: Path
    cache_refresh: bool
    output_root: Path
    morphed_fields: List[str]
    require_reference_epw: bool
    db_bin_width_c: float
    allow_reference_fallback: bool
    timestamp_semantics: str
    raw: Dict[str, Any] = field(default_factory=dict)
    reserved: Dict[str, Any] = field(default_factory=dict)


def _require(d: Dict[str, Any], key: str, ctx: str) -> Any:
    if key not in d or d[key] is None:
        raise ConfigError(f"Missing required config key: {ctx}.{key}")
    return d[key]


def _resolve(path_like: Optional[str], root: Path) -> Optional[Path]:
    if path_like is None:
        return None
    p = Path(path_like)
    return p if p.is_absolute() else (root / p)


def load_config(path: str | Path) -> Config:
    cfg_path = Path(path)
    if not cfg_path.is_file():
        raise ConfigError(f"Config file not found: {cfg_path}")
    with cfg_path.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    if not isinstance(raw, dict):
        raise ConfigError(f"Config root must be a mapping: {cfg_path}")

    root = project_root()

    st = _require(raw, "station", "config")
    station = StationConfig(
        code=str(_require(st, "code", "station")),
        name=str(_require(st, "name", "station")),
        latitude=float(_require(st, "latitude", "station")),
        longitude=float(_require(st, "longitude", "station")),
        elevation_m=float(_require(st, "elevation_m", "station")),
        timezone_utc_offset=float(_require(st, "timezone_utc_offset", "station")),
        source_ids=dict(st.get("source_ids") or {}),
        reference_epw=_resolve(st.get("reference_epw"), root),
        reference_ddy=_resolve(st.get("reference_ddy"), root),
    )

    scenario = _require(raw, "scenario", "config")
    data_source = raw.get("data_source") or {}
    cache = data_source.get("cache") or {}
    output = raw.get("output") or {}
    epw_writer = raw.get("epw_writer") or {}
    design = raw.get("design_conditions") or {}
    fallback = (design.get("reference_fallback") or {})

    mode = str(data_source.get("mode", "api")).lower()
    if mode not in ("api", "local"):
        raise ConfigError(f"data_source.mode must be 'api' or 'local', got '{mode}'")

    output_root = _resolve(str(output.get("root", "outputs")), root)
    if output_root is not None and output_root.name == "test_outputs":
        raise ConfigError(
            "output.root must not be 'test_outputs' (reserved for tests/dev examples)."
        )

    reserved = {k: v for k, v in RESERVED_HINTS.items()}

    return Config(
        path=cfg_path,
        station=station,
        ref_state=str(scenario.get("ref_state", "ref91-20")),
        gwls=list(_require(scenario, "gwls", "scenario")),
        baseline_start_year=int(scenario.get("baseline_start_year", 1991)),
        baseline_end_year=int(scenario.get("baseline_end_year", 2020)),
        profiles=list(raw.get("profiles") or
                      ["seasonal_warm", "peak_event", "sustained_heat", "nocturnal_heat"]),
        data_source_mode=mode,
        cache_enabled=bool(cache.get("enabled", True)),
        cache_dir=_resolve(str(cache.get("dir", "cache")), root),
        cache_refresh=bool(cache.get("refresh", False)),
        output_root=output_root,
        morphed_fields=list(epw_writer.get("morphed_fields") or
                            ["dry_bulb_temperature", "relative_humidity",
                             "global_horizontal_radiation"]),
        require_reference_epw=bool(epw_writer.get("require_reference_epw", True)),
        db_bin_width_c=float(design.get("db_bin_width_c", 0.5)),
        allow_reference_fallback=bool(fallback.get("allow", False)),
        timestamp_semantics=str(design.get("timestamp_semantics",
                                            "hour_ending_local_standard_time")),
        raw=raw,
        reserved=reserved,
    )
