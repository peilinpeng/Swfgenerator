#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
01_station_capability_matrix_v4.py

Purpose:
    Build a station capability report for the CH2025 -> EPW workflow.

Capability levels:
    L1 Core support:
        Official CH2025 daily target variables exist for tas/hurs/rsds/tasmax/tasmin,
        reference-state daily variables exist for tas/hurs/rsds, and the hourly
        observation backbone contains tas/hurs/rsds/sfcWind.

    L2 Scenario fallback:
        Hourly backbone is available, but one or more CH2025 scenario variables
        are missing. A nearest-station or gridded proxy would be needed.

    L3 Hourly donor fallback:
        CH2025 scenario variables are available, but one or more required hourly
        observation variables are missing. A donor/patching workflow would be needed.

    L4 Unsupported:
        Missing critical CH2025 and hourly information; no EPW should be generated
        without explicit manual intervention.

Notes:
    This script does not download data. It checks already parsed CH2025 daily files
    and one standardized MeteoSwiss hourly observation file.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List, Tuple

import pandas as pd


def log(msg: str) -> None:
    print(msg, flush=True)


def read_table(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(path)
    if path.suffix.lower() == ".parquet":
        return pd.read_parquet(path)
    return pd.read_csv(path)


def first_existing(paths: List[Path]) -> Path | None:
    for p in paths:
        if p.exists():
            return p
    return None


def ch2025_candidates(root: Path, station: str, variable: str, state: str) -> List[Path]:
    station = station.lower()
    return [
        root / f"ch2025_daily_{station}_{variable}_{state}.parquet",
        root / f"ch2025_daily_{station}_{variable}_{state}.csv",
    ]


def check_ch2025_file(path: Path | None, expected_variable: str) -> Dict[str, Any]:
    if path is None:
        return {"available": False, "path": None, "row_count": 0, "model_chain_count": 0, "warnings": ["file_not_found"]}
    warnings: List[str] = []
    try:
        df = read_table(path)
        required = {"station_id", "variable_id", "state", "model_chain", "month", "day", "doy", "value"}
        missing = sorted(required - set(df.columns))
        if missing:
            warnings.append(f"missing_columns:{missing}")
        variables = sorted(df.get("variable_id", pd.Series(dtype=str)).dropna().astype(str).unique().tolist())
        if variables and variables != [expected_variable]:
            warnings.append(f"variable_mismatch:{variables}")
        value_na = float(pd.to_numeric(df.get("value", pd.Series(dtype=float)), errors="coerce").isna().mean()) if "value" in df.columns else 1.0
        if value_na > 0.05:
            warnings.append(f"high_na_fraction:{value_na:.3f}")
        return {
            "available": len(missing) == 0,
            "path": str(path),
            "row_count": int(len(df)),
            "model_chain_count": int(df["model_chain"].nunique()) if "model_chain" in df.columns else 0,
            "na_fraction_value": value_na,
            "warnings": warnings,
        }
    except Exception as exc:
        return {"available": False, "path": str(path), "row_count": 0, "model_chain_count": 0, "warnings": [f"read_error:{exc}"]}


def check_hourly_file(path: Path, required_vars: List[str], recommended_vars: List[str]) -> Dict[str, Any]:
    if not path.exists():
        return {"available": False, "path": str(path), "row_count": 0, "variables": {}, "warnings": ["file_not_found"]}
    try:
        df = read_table(path)
    except Exception as exc:
        return {"available": False, "path": str(path), "row_count": 0, "variables": {}, "warnings": [f"read_error:{exc}"]}

    variables: Dict[str, Any] = {}
    warnings: List[str] = []
    for var in required_vars + recommended_vars:
        present = var in df.columns
        na_fraction = None
        if present:
            na_fraction = float(pd.to_numeric(df[var], errors="coerce").isna().mean())
        variables[var] = {"present": present, "na_fraction": na_fraction, "required": var in required_vars}
        if var in required_vars and (not present or (na_fraction is not None and na_fraction > 0.2)):
            warnings.append(f"required_hourly_problem:{var}")

    years = sorted(df["year"].dropna().astype(int).unique().tolist()) if "year" in df.columns else []
    return {
        "available": all(variables[v]["present"] and (variables[v]["na_fraction"] is not None and variables[v]["na_fraction"] <= 0.2) for v in required_vars),
        "path": str(path),
        "row_count": int(len(df)),
        "year_min": min(years) if years else None,
        "year_max": max(years) if years else None,
        "variables": variables,
        "warnings": warnings,
    }


def decide_level(ch2025_target_ok: bool, ch2025_ref_ok: bool, hourly_ok: bool) -> Tuple[str, str, str]:
    """Return capability level, technical handling, and missing-data category.

    v4.1 distinguishes missing target-state scenario variables from missing
    reference-state baseline variables because the latter prevents direct
    delta/factor construction.
    """
    if ch2025_target_ok and ch2025_ref_ok and hourly_ok:
        return "L1 Core support", "Main workflow allowed for core FRY/XMY generation; EPW auxiliary fields may still use documented fallback.", "none"
    if hourly_ok and (not ch2025_target_ok) and ch2025_ref_ok:
        return "L2 Scenario fallback", "Target-state CH2025 proxy required for missing scenario variables.", "target_scenario_missing"
    if hourly_ok and ch2025_target_ok and (not ch2025_ref_ok):
        return "L2b Baseline fallback", "Reference-state CH2025 proxy required before delta/factor signals can be computed.", "reference_baseline_missing"
    if hourly_ok and (not ch2025_target_ok) and (not ch2025_ref_ok):
        return "L2 Critical scenario fallback", "Both target and reference CH2025 variables are incomplete; station should not enter the main workflow without explicit proxy assumptions.", "target_and_reference_missing"
    if ch2025_target_ok and ch2025_ref_ok and (not hourly_ok):
        return "L3 Hourly donor fallback", "Hourly donor/patching workflow required.", "hourly_missing"
    return "L4 Unsupported", "Do not generate EPW without manual data intervention.", "multiple_critical_missing"


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Build station capability matrix for CH2025 EPW workflow.")
    p.add_argument("--station", default="sma")
    p.add_argument("--ref-state", default="ref91-20")
    p.add_argument("--target-state", default="gwl2.0")
    p.add_argument("--ch2025-dir", default="./data_processed/ch2025_daily")
    p.add_argument("--hourly-obs", default="./data_processed/hourly_obs/hourly_obs_sma_v4.csv")
    p.add_argument("--required-target-vars", default="tas,hurs,rsds,tasmax,tasmin")
    p.add_argument("--required-ref-vars", default="tas,hurs,rsds")
    p.add_argument("--required-hourly-vars", default="tas,hurs,rsds,sfcWind")
    p.add_argument("--recommended-hourly-vars", default="pres,windDir,horizIR,cloudcover,total_sky_cover_tenths,opaque_sky_cover_tenths")
    p.add_argument("--output-dir", default="./data_processed/station_capability")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    station = args.station.lower()
    ch2025_dir = Path(args.ch2025_dir)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    target_vars = [x.strip() for x in args.required_target_vars.split(",") if x.strip()]
    ref_vars = [x.strip() for x in args.required_ref_vars.split(",") if x.strip()]
    hourly_req = [x.strip() for x in args.required_hourly_vars.split(",") if x.strip()]
    hourly_rec = [x.strip() for x in args.recommended_hourly_vars.split(",") if x.strip()]

    target_checks = {}
    for var in target_vars:
        target_checks[var] = check_ch2025_file(first_existing(ch2025_candidates(ch2025_dir, station, var, args.target_state)), var)
    ref_checks = {}
    for var in ref_vars:
        ref_checks[var] = check_ch2025_file(first_existing(ch2025_candidates(ch2025_dir, station, var, args.ref_state)), var)
    hourly_check = check_hourly_file(Path(args.hourly_obs), hourly_req, hourly_rec)

    target_ok = all(v["available"] for v in target_checks.values())
    ref_ok = all(v["available"] for v in ref_checks.values())
    hourly_ok = bool(hourly_check["available"])
    level, handling, missing_category = decide_level(target_ok, ref_ok, hourly_ok)

    rows = []
    for group, checks in [("ch2025_target", target_checks), ("ch2025_ref", ref_checks)]:
        for var, info in checks.items():
            rows.append({
                "station_id": station,
                "capability_level": level,
                "group": group,
                "variable": var,
                "available": info["available"],
                "path": info["path"],
                "row_count": info.get("row_count"),
                "model_chain_count": info.get("model_chain_count"),
                "warnings": ";".join(info.get("warnings", [])),
            })
    for var, info in hourly_check.get("variables", {}).items():
        rows.append({
            "station_id": station,
            "capability_level": level,
            "group": "hourly_obs",
            "variable": var,
            "available": info["present"] and (info["na_fraction"] is not None and info["na_fraction"] <= 0.2),
            "path": hourly_check["path"],
            "row_count": hourly_check.get("row_count"),
            "model_chain_count": None,
            "warnings": "" if info["present"] else "missing_column",
        })
    matrix = pd.DataFrame(rows)
    base = f"station_capability_{station}_{args.ref_state}_to_{args.target_state}_v4"
    csv_path = output_dir / f"{base}.csv"
    json_path = output_dir / f"{base}.json"
    matrix.to_csv(csv_path, index=False)
    payload = {
        "station_id": station,
        "ref_state": args.ref_state,
        "target_state": args.target_state,
        "capability_level": level,
        "technical_handling": handling,
        "missing_category": missing_category,
        "target_ok": target_ok,
        "reference_ok": ref_ok,
        "hourly_ok": hourly_ok,
        "checks": {"ch2025_target": target_checks, "ch2025_ref": ref_checks, "hourly_obs": hourly_check},
    }
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    log(f"Capability: {level} — {handling}")
    log(f"CSV : {csv_path}")
    log(f"JSON: {json_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
