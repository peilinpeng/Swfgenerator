#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
07_select_xmy_profiles_from_candidate_pool_v4.py

Purpose:
    Select fit-for-purpose XMY files from the future hourly candidate archive.

User-facing thesis profiles:
    1. seasonal_warm  -> cumulative seasonal heat burden
    2. peak_event     -> short peak heat stress
    3. sustained_heat -> prolonged heat accumulation / heatwave continuity
    4. nocturnal_heat -> night-time recovery failure / tropical nights

Design choice:
    XMY profiles are selected as full-year candidates (`model_chain + source_year`)
    rather than month composites, so heatwave and nocturnal sequences remain temporally continuous.

The script also writes a literature mapping table showing how the four thesis profiles
relate to wider XMY/DSY/ERY/HWE/RSWY/EBEY literature families.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List, Tuple

import numpy as np
import pandas as pd


def log(msg: str) -> None:
    print(msg, flush=True)


def read_table(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"Input file not found: {path}")
    if path.suffix.lower() == ".parquet":
        return pd.read_parquet(path)
    return pd.read_csv(path)


def save_csv(df: pd.DataFrame, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False)
    return path


def write_json(path: Path, payload: Dict[str, Any]) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def longest_consecutive_true(values: pd.Series) -> int:
    best = 0
    cur = 0
    for v in values.astype(bool).tolist():
        if v:
            cur += 1
            best = max(best, cur)
        else:
            cur = 0
    return int(best)


def load_candidate_pool(path: Path) -> pd.DataFrame:
    df = read_table(path)
    required = {"station_id", "model_chain", "ref_state", "target_state", "datetime", "year", "month", "day", "hour", "tas_future", "hurs_future", "rsds_future"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"Candidate pool missing required columns: {sorted(missing)}")
    if "sfcWind_future" not in df.columns and "sfcWind_retained" not in df.columns:
        raise ValueError("Candidate pool must include sfcWind_future or sfcWind_retained")
    df = df.copy()
    df["datetime"] = pd.to_datetime(df["datetime"], errors="coerce")
    return df


def build_daily_stats(pool: pd.DataFrame) -> pd.DataFrame:
    group = ["station_id", "model_chain", "ref_state", "target_state", "year", "month", "day"]
    return pool.groupby(group, sort=False).agg(
        tas_daily_mean=("tas_future", "mean"),
        hurs_daily_mean=("hurs_future", "mean"),
        rsds_daily_sum=("rsds_future", "sum"),
        tmax_daily=("tas_future", "max"),
        tmin_daily=("tas_future", "min"),
    ).reset_index()


def build_candidate_year_metrics(pool: pd.DataFrame, daily: pd.DataFrame, warm_months: List[int], cdh_base: float, hot_day_threshold: float, tropical_night_threshold: float) -> pd.DataFrame:
    rows: List[Dict[str, Any]] = []
    warm_pool = pool[pool["month"].isin(warm_months)].copy()
    warm_daily = daily[daily["month"].isin(warm_months)].copy()
    for (chain, year), grp_h in warm_pool.groupby(["model_chain", "year"], sort=False):
        grp_d = warm_daily[(warm_daily["model_chain"].astype(str) == str(chain)) & (warm_daily["year"] == year)].copy()
        if grp_h.empty or grp_d.empty:
            continue
        grp_d = grp_d.sort_values(["month", "day"], kind="stable").reset_index(drop=True)
        hourly_tas = pd.to_numeric(grp_h["tas_future"], errors="coerce")
        cdh = np.maximum(hourly_tas - cdh_base, 0).sum()
        hot_day = grp_d["tmax_daily"] >= hot_day_threshold
        tropical_night = grp_d["tmin_daily"] >= tropical_night_threshold
        heat_exceed = np.maximum(grp_d["tmax_daily"] - hot_day_threshold, 0)
        night_exceed = np.maximum(grp_d["tmin_daily"] - tropical_night_threshold, 0)
        rows.append({
            "model_chain": str(chain),
            "source_year": int(year),
            "summer_cdh": float(cdh),
            "summer_mean_tas": float(grp_d["tas_daily_mean"].mean()),
            "summer_mean_tmax": float(grp_d["tmax_daily"].mean()),
            "summer_mean_tmin": float(grp_d["tmin_daily"].mean()),
            "max_hourly_tas": float(hourly_tas.max()),
            "max_daily_tmax": float(grp_d["tmax_daily"].max()),
            "max_3day_tmax_mean": float(grp_d["tmax_daily"].rolling(3, min_periods=3).mean().max()),
            "hot_day_count": int(hot_day.sum()),
            "longest_hot_spell_days": longest_consecutive_true(hot_day),
            "heatwave_severity_degree_days": float(heat_exceed.sum()),
            "tropical_night_count": int(tropical_night.sum()),
            "longest_tropical_night_spell": longest_consecutive_true(tropical_night),
            "night_heat_degree_days": float(night_exceed.sum()),
            "summer_rsds_total": float(grp_d["rsds_daily_sum"].sum()),
        })
    out = pd.DataFrame(rows)
    if out.empty:
        raise ValueError("No candidate-year metrics were built. Check warm months and candidate pool.")
    return out


def profile_registry() -> Dict[str, Dict[str, Any]]:
    return {
        "seasonal_warm": {
            "test_perspective": "seasonal heat burden",
            "literature_families": ["DSY", "pDSY", "HSY", "SRY"],
            "stress_function": "maximize summer Cooling Degree Hours",
            "sort_by": [("summer_cdh", False), ("summer_mean_tas", False), ("summer_mean_tmax", False)],
            "required_variables": ["tas_future"],
        },
        "peak_event": {
            "test_perspective": "short peak heat stress",
            "literature_families": ["temperature-led extremes", "DSY2-like short intense spell"],
            "stress_function": "maximize daily/hourly peak outdoor temperature",
            "sort_by": [("max_daily_tmax", False), ("max_hourly_tas", False), ("max_3day_tmax_mean", False)],
            "required_variables": ["tas_future"],
        },
        "sustained_heat": {
            "test_perspective": "prolonged heat accumulation",
            "literature_families": ["HWE", "HWY", "heatwave event files"],
            "stress_function": "maximize longest hot spell, then accumulated heatwave severity",
            "sort_by": [("longest_hot_spell_days", False), ("heatwave_severity_degree_days", False), ("hot_day_count", False)],
            "required_variables": ["tas_future"],
        },
        "nocturnal_heat": {
            "test_perspective": "night-time recovery failure",
            "literature_families": ["RSWY", "day/night heat-stress files", "tropical-night indicators"],
            "stress_function": "maximize tropical nights and night heat burden",
            "sort_by": [("tropical_night_count", False), ("longest_tropical_night_spell", False), ("night_heat_degree_days", False)],
            "required_variables": ["tas_future"],
        },
    }


def rank_for_profile(metrics: pd.DataFrame, profile_name: str, spec: Dict[str, Any]) -> Tuple[pd.DataFrame, pd.Series]:
    rank = metrics.copy()
    rank["profile"] = profile_name
    rank["test_perspective"] = spec["test_perspective"]
    rank["stress_function"] = spec["stress_function"]
    by = [x[0] for x in spec["sort_by"]] + ["model_chain", "source_year"]
    ascending = [x[1] for x in spec["sort_by"]] + [True, True]
    rank = rank.sort_values(by=by, ascending=ascending, kind="stable").reset_index(drop=True)
    rank["rank_within_profile"] = np.arange(1, len(rank) + 1)
    selected = rank.iloc[0].copy()
    rank["selected_flag"] = rank["rank_within_profile"] == 1
    return rank, selected


def choose_col(df: pd.DataFrame, candidates: List[str]) -> str | None:
    for c in candidates:
        if c in df.columns:
            return c
    return None


def extract_full_year_xmy(pool: pd.DataFrame, selected: pd.Series, profile_name: str, synthetic_year: int) -> pd.DataFrame:
    chain = str(selected["model_chain"])
    year = int(selected["source_year"])
    block = pool[(pool["model_chain"].astype(str) == chain) & (pool["year"] == year)].copy()
    if block.empty:
        raise ValueError(f"No hourly data for selected profile={profile_name}, chain={chain}, year={year}")
    block = block.sort_values(["month", "day", "hour"], kind="stable").reset_index(drop=True)
    if len(block) != 8760:
        raise ValueError(f"Selected full-year XMY has {len(block)} rows; expected 8760 after leap-day removal.")
    dt = pd.to_datetime({"year": synthetic_year, "month": block["month"].astype(int), "day": block["day"].astype(int), "hour": block["hour"].astype(int)}, errors="raise")
    out = pd.DataFrame({
        "station_id": block["station_id"],
        "ref_state": block["ref_state"],
        "target_state": block["target_state"],
        "file_type": "XMY",
        "profile": profile_name,
        "source_model_chain": chain,
        "source_year": year,
        "datetime": dt,
        "month": block["month"],
        "day": block["day"],
        "hour": block["hour"],
        "tas": block["tas_future"],
        "hurs": block["hurs_future"],
        "rsds": block["rsds_future"],
    })
    wind_col = choose_col(block, ["sfcWind_future", "sfcWind_retained", "sfcWind"])
    if wind_col:
        out["sfcWind"] = block[wind_col]
    for out_name, candidates in {
        "pres": ["pres_retained", "pres_future", "pres"],
        "windDir": ["windDir_retained", "windDir_future", "windDir"],
        "horizIR": ["horizIR_retained", "horizIR_future", "horizIR"],
        "cloudcover": ["cloudcover_retained", "cloudcover_future", "cloudcover"],
        "dhi_obs": ["dhi_retained", "dhi_future", "dhi"],
    }.items():
        col = choose_col(block, candidates)
        if col:
            out[out_name] = block[col]
    return out


def build_feasibility_table(registry: Dict[str, Dict[str, Any]], pool_columns: List[str]) -> pd.DataFrame:
    rows = []
    cols = set(pool_columns)
    for name, spec in registry.items():
        missing = [v for v in spec["required_variables"] if v not in cols]
        rows.append({
            "profile": name,
            "test_perspective": spec["test_perspective"],
            "literature_families": "; ".join(spec["literature_families"]),
            "stress_function": spec["stress_function"],
            "required_variables": "; ".join(spec["required_variables"]),
            "missing_required_variables": "; ".join(missing),
            "implemented": len(missing) == 0,
            "selection_unit": "full_year_candidate:model_chain+source_year",
        })
    return pd.DataFrame(rows)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Select fit-for-purpose XMY profiles from candidate pool v4.")
    p.add_argument("--candidate-pool", default="./data_processed/hourly_future_candidates/hourly_future_candidates_sma_ref91-20_to_gwl2.0_v4.csv")
    p.add_argument("--output-dir", default="./data_processed/xmy")
    p.add_argument("--synthetic-year", type=int, default=2001)
    p.add_argument("--warm-month-start", type=int, default=6)
    p.add_argument("--warm-month-end", type=int, default=8)
    p.add_argument("--cdh-base-temperature-c", type=float, default=26.0)
    p.add_argument("--hot-day-threshold-c", type=float, default=30.0)
    p.add_argument("--tropical-night-threshold-c", type=float, default=20.0)
    p.add_argument("--profiles", default="seasonal_warm,peak_event,sustained_heat,nocturnal_heat")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    try:
        output_dir = Path(args.output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        pool_path = Path(args.candidate_pool)
        pool = load_candidate_pool(pool_path)
        station_id = sorted(pool["station_id"].dropna().astype(str).unique().tolist())[0]
        ref_state = sorted(pool["ref_state"].dropna().astype(str).unique().tolist())[0]
        target_state = sorted(pool["target_state"].dropna().astype(str).unique().tolist())[0]
        warm_months = list(range(args.warm_month_start, args.warm_month_end + 1))
        registry = profile_registry()
        requested = [x.strip() for x in args.profiles.split(",") if x.strip()]
        for prof in requested:
            if prof not in registry:
                raise ValueError(f"Unknown profile '{prof}'. Available: {sorted(registry)}")
        log("[1/5] Building candidate-year metrics ...")
        daily = build_daily_stats(pool)
        metrics = build_candidate_year_metrics(pool, daily, warm_months, args.cdh_base_temperature_c, args.hot_day_threshold_c, args.tropical_night_threshold_c)
        base = f"xmy_profiles_{station_id}_{ref_state}_to_{target_state}_v4"
        metrics_path = output_dir / f"{base}_candidate_year_metrics.csv"
        save_csv(metrics, metrics_path)
        feasibility = build_feasibility_table(registry, list(pool.columns))
        feasibility_path = output_dir / f"{base}_algorithm_feasibility.csv"
        save_csv(feasibility, feasibility_path)
        log("[2/5] Ranking and selecting requested profiles ...")
        all_rankings: List[pd.DataFrame] = []
        selected_rows: List[pd.Series] = []
        xmy_paths: Dict[str, str] = {}
        for prof in requested:
            spec = registry[prof]
            missing = [v for v in spec["required_variables"] if v not in pool.columns]
            if missing:
                log(f"      skipping {prof}: missing {missing}")
                continue
            rank, selected = rank_for_profile(metrics, prof, spec)
            all_rankings.append(rank)
            selected_rows.append(selected)
            xmy = extract_full_year_xmy(pool, selected, prof, args.synthetic_year)
            xmy_path = output_dir / f"xmy_{prof}_{station_id}_{ref_state}_to_{target_state}_v4.csv"
            save_csv(xmy, xmy_path)
            xmy_paths[prof] = str(xmy_path)
        if not selected_rows:
            raise ValueError("No XMY profiles could be selected.")
        selection_table = pd.DataFrame(selected_rows).reset_index(drop=True)
        rankings = pd.concat(all_rankings, ignore_index=True)
        selection_path = output_dir / f"{base}_selections.csv"
        rankings_path = output_dir / f"{base}_rankings.csv"
        save_csv(selection_table, selection_path)
        save_csv(rankings, rankings_path)
        meta = {
            "inputs": {"candidate_pool": str(pool_path)},
            "method": {
                "xmy_design": "profile-based fit-for-purpose XMY",
                "selection_unit": "full-year candidate = model_chain + source_year",
                "profiles": {k: registry[k] for k in requested},
                "warm_months": warm_months,
                "cdh_base_temperature_c": args.cdh_base_temperature_c,
                "hot_day_threshold_c": args.hot_day_threshold_c,
                "tropical_night_threshold_c": args.tropical_night_threshold_c,
            },
            "outputs": {
                "xmy_files": xmy_paths,
                "candidate_year_metrics": str(metrics_path),
                "algorithm_feasibility": str(feasibility_path),
                "selections": str(selection_path),
                "rankings": str(rankings_path),
            },
            "summary_metadata": {
                "station_id": station_id,
                "ref_state": ref_state,
                "target_state": target_state,
                "candidate_pool_rows": int(len(pool)),
                "candidate_year_count": int(len(metrics)),
                "selected_profile_count": len(xmy_paths),
            },
        }
        meta_path = output_dir / f"{base}.json"
        write_json(meta_path, meta)
        log("[5/5] Done.")
        log(f"Selections: {selection_path}")
        return 0
    except Exception as exc:
        log(f"[ERROR] {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
