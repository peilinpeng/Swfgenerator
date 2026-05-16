#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
05_build_hourly_future_candidates_v4.py

Purpose:
    Apply CH2025 daily climate-change signals to historical hourly observations,
    producing a wide/enriched future hourly candidate archive.

Core variables transformed:
    tas   -> additive delta
    hurs  -> multiplicative factor
    rsds  -> multiplicative factor

Auxiliary variables retained when available:
    sfcWind, windDir, pres, horizIR, cloudcover, pre-interpolated sky-cover fields, and any user-requested passthrough columns.

Important design:
    This is NOT the final EPW completion layer. It only carries useful variables forward.
    Final DHI/DNI, dew point, pressure normalization, sky-cover scaling and EPW formatting
    happen after FRY/XMY selection.
"""
from __future__ import annotations

import argparse
import json
import gc
from pathlib import Path
from typing import Any, Dict, List

import pandas as pd


def log(msg: str) -> None:
    print(msg, flush=True)


def read_table(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"Input file not found: {path}")
    if path.suffix.lower() == ".parquet":
        return pd.read_parquet(path)
    return pd.read_csv(path)


def month_day_to_noleap_doy(month: int, day: int) -> int:
    month_lengths = [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    if month < 1 or month > 12:
        raise ValueError(f"Invalid month: {month}")
    max_day = month_lengths[month - 1]
    if day < 1 or day > max_day:
        raise ValueError(f"Invalid day {day} for month {month}")
    return sum(month_lengths[: month - 1]) + day


def load_and_prepare_hourly_obs(path: Path, baseline_start_year: int, baseline_end_year: int) -> Dict[str, Any]:
    df = read_table(path)
    required = {"station_id", "datetime", "year", "month", "day", "hour", "tas", "hurs", "rsds", "sfcWind"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"Hourly obs missing required columns: {sorted(missing)}")
    df = df.copy()
    if "datetime_utc" in df.columns:
        df["datetime_utc"] = pd.to_datetime(df["datetime_utc"], errors="coerce", utc=True)
    if "datetime_local_std" in df.columns:
        df["datetime_local_std"] = pd.to_datetime(df["datetime_local_std"], errors="coerce")
    df["datetime"] = pd.to_datetime(df["datetime"], errors="coerce")
    df = df.dropna(subset=["datetime"]).copy()
    rows_before = len(df)
    df = df[(df["year"] >= baseline_start_year) & (df["year"] <= baseline_end_year)].copy()
    rows_after_year = len(df)
    leap_mask = (df["month"] == 2) & (df["day"] == 29)
    df = df[~leap_mask].copy()
    rows_after_leap = len(df)
    df["doy_noleap"] = [month_day_to_noleap_doy(int(m), int(d)) for m, d in zip(df["month"], df["day"])]
    sort_col = "datetime_utc" if "datetime_utc" in df.columns else "datetime"
    df = df.sort_values(sort_col, kind="stable").reset_index(drop=True)
    station_ids = sorted(df["station_id"].dropna().astype(str).unique().tolist())
    if len(station_ids) != 1:
        raise ValueError(f"Hourly obs station mismatch: {station_ids}")
    return {"df": df, "station_id": station_ids[0], "rows_before": rows_before, "rows_after_year": rows_after_year, "rows_after_leap": rows_after_leap}


def load_signal_table(path: Path, expected_variable: str) -> pd.DataFrame:
    df = read_table(path)
    required = {"station_id", "variable_id", "model_chain", "month", "day", "ref_state", "target_state", "signal_kind", "signal_value"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"Signal file {path} missing required columns: {sorted(missing)}")
    variables = sorted(df["variable_id"].dropna().astype(str).unique().tolist())
    if variables != [expected_variable]:
        raise ValueError(f"Signal file {path} variable mismatch: expected {expected_variable}, got {variables}")
    return df.copy()


def load_common_chains(path: Path) -> pd.DataFrame:
    df = read_table(path)
    required = {"station_id", "ref_state", "target_state", "model_chain"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"Common-chain file missing required columns: {sorted(missing)}")
    return df.copy()


def signal_map(df_signal: pd.DataFrame, out_col: str) -> pd.DataFrame:
    return df_signal[["model_chain", "month", "day", "signal_value"]].rename(columns={"signal_value": out_col})


def normalize_passthrough_list(text: str) -> List[str]:
    return [x.strip() for x in text.split(",") if x.strip()]


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Build enriched future hourly candidate pool v4.")
    p.add_argument("--hourly-obs", default="./data_processed/hourly_obs/hourly_obs_sma_v4.csv")
    p.add_argument("--tas-signal", default="./data_processed/daily_signal/daily_signal_sma_tas_ref91-20_to_gwl2.0.csv")
    p.add_argument("--hurs-signal", default="./data_processed/daily_signal/daily_signal_sma_hurs_ref91-20_to_gwl2.0.csv")
    p.add_argument("--rsds-signal", default="./data_processed/daily_signal/daily_signal_sma_rsds_ref91-20_to_gwl2.0.csv")
    p.add_argument("--common-chains", default="./data_processed/common_model_chains/common_model_chains_sma_ref91-20_to_gwl2.0_v4.csv")
    p.add_argument("--output", default="./data_processed/hourly_future_candidates/hourly_future_candidates_sma_ref91-20_to_gwl2.0_v4.csv")
    p.add_argument("--baseline-start-year", type=int, default=1991)
    p.add_argument("--baseline-end-year", type=int, default=2020)
    p.add_argument("--passthrough-cols", default="datetime_utc,datetime_local_std,pres,windDir,horizIR,cloudcover,cloudcover_interpolated,total_sky_cover_tenths,opaque_sky_cover_tenths,skycover_source,skycover_interpolation_method")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    try:
        hourly_obs_path = Path(args.hourly_obs)
        tas_signal_path = Path(args.tas_signal)
        hurs_signal_path = Path(args.hurs_signal)
        rsds_signal_path = Path(args.rsds_signal)
        common_chains_path = Path(args.common_chains)
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        passthrough_requested = normalize_passthrough_list(args.passthrough_cols)

        log("[1/6] Loading hourly observations ...")
        obs_info = load_and_prepare_hourly_obs(hourly_obs_path, args.baseline_start_year, args.baseline_end_year)
        obs = obs_info["df"]
        station_id = obs_info["station_id"]

        log("[2/6] Loading daily signals and common chains ...")
        tas_signal = load_signal_table(tas_signal_path, "tas")
        hurs_signal = load_signal_table(hurs_signal_path, "hurs")
        rsds_signal = load_signal_table(rsds_signal_path, "rsds")
        common_df = load_common_chains(common_chains_path)
        chains = sorted(common_df["model_chain"].dropna().astype(str).unique().tolist())
        if not chains:
            raise ValueError("No common model chains found.")
        ref_state = sorted(common_df["ref_state"].dropna().astype(str).unique().tolist())[0]
        target_state = sorted(common_df["target_state"].dropna().astype(str).unique().tolist())[0]

        log("[3/6] Preparing signal lookup tables ...")
        tas_map = signal_map(tas_signal, "delta_tas")
        hurs_map = signal_map(hurs_signal, "factor_hurs")
        rsds_map = signal_map(rsds_signal, "factor_rsds")
        tas_map = tas_map[tas_map["model_chain"].isin(chains)]
        hurs_map = hurs_map[hurs_map["model_chain"].isin(chains)]
        rsds_map = rsds_map[rsds_map["model_chain"].isin(chains)]

        passthrough_cols = [c for c in passthrough_requested if c in obs.columns]
        log(f"[4/6] Building candidate pool for {len(chains)} chains ...")
        first = True
        total_rows = 0
        per_chain_rows = None
        for idx, chain in enumerate(chains, start=1):
            if idx == 1 or idx % 5 == 0 or idx == len(chains):
                log(f"      chain {idx}/{len(chains)}: {chain}")
            df_chain = obs.copy()
            df_chain["model_chain"] = chain
            df_chain = (
                df_chain.merge(tas_map[tas_map["model_chain"] == chain], on=["model_chain", "month", "day"], how="left")
                .merge(hurs_map[hurs_map["model_chain"] == chain], on=["model_chain", "month", "day"], how="left")
                .merge(rsds_map[rsds_map["model_chain"] == chain], on=["model_chain", "month", "day"], how="left")
            )
            df_chain["tas_future"] = df_chain["tas"] + df_chain["delta_tas"]
            df_chain["hurs_future"] = (df_chain["hurs"] * df_chain["factor_hurs"]).clip(0, 100)
            df_chain["rsds_future"] = (df_chain["rsds"] * df_chain["factor_rsds"]).clip(lower=0)
            # Retained auxiliary variables. Keep both explicit retained names and legacy *_future aliases for compatibility.
            df_chain["sfcWind_retained"] = df_chain["sfcWind"].clip(lower=0)
            df_chain["sfcWind_future"] = df_chain["sfcWind_retained"]
            for c in passthrough_cols:
                if c in {"datetime_utc", "datetime_local_std"}:
                    continue
                df_chain[f"{c}_retained"] = df_chain[c]
                if c in {"pres", "windDir", "horizIR", "cloudcover", "cloudcover_interpolated", "total_sky_cover_tenths", "opaque_sky_cover_tenths"}:
                    df_chain[f"{c}_future"] = df_chain[c]
            df_chain["ref_state"] = ref_state
            df_chain["target_state"] = target_state
            base_cols = [
                "station_id", "model_chain", "ref_state", "target_state",
                "datetime", "year", "month", "day", "hour", "doy", "doy_noleap",
                "tas", "hurs", "rsds", "sfcWind",
                "delta_tas", "factor_hurs", "factor_rsds",
                "tas_future", "hurs_future", "rsds_future", "sfcWind_retained", "sfcWind_future",
            ]
            optional = [c for c in passthrough_cols if c in df_chain.columns]
            retained = [c for c in df_chain.columns if c.endswith("_retained") and c not in base_cols]
            legacy_future = [c for c in df_chain.columns if c.endswith("_future") and c not in base_cols and c not in ["tas_future", "hurs_future", "rsds_future", "sfcWind_future"]]
            out = df_chain[base_cols + optional + retained + legacy_future]
            if per_chain_rows is None:
                per_chain_rows = len(out)
            total_rows += len(out)
            out.to_csv(output_path, index=False, mode="w" if first else "a", header=first)
            first = False
            del out, df_chain
            if idx % 5 == 0:
                gc.collect()

        log("[5/6] Writing metadata ...")
        meta = {
            "inputs": {
                "hourly_obs": str(hourly_obs_path),
                "tas_signal": str(tas_signal_path),
                "hurs_signal": str(hurs_signal_path),
                "rsds_signal": str(rsds_signal_path),
                "common_chains": str(common_chains_path),
            },
            "summary_metadata": {
                "station_id": station_id,
                "ref_state": ref_state,
                "target_state": target_state,
                "chain_count": len(chains),
                "baseline_start_year": args.baseline_start_year,
                "baseline_end_year": args.baseline_end_year,
                "obs_rows_before_filter": obs_info["rows_before"],
                "obs_rows_after_year_filter": obs_info["rows_after_year"],
                "obs_rows_after_leap_drop": obs_info["rows_after_leap"],
                "per_chain_row_count": per_chain_rows,
                "total_rows_written": total_rows,
                "passthrough_columns_found": passthrough_cols,
            },
            "policies": {
                "core_transform": "tas additive delta; hurs and rsds multiplicative factors",
                "auxiliary_variables": "retained from MeteoSwiss hourly observations when available; final EPW completion occurs after selection",
                "leap_day_policy": "Feb 29 removed to match CH2025 365_day calendar",
            },
        }
        with open(output_path.with_suffix(output_path.suffix + ".meta.json"), "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)
        log("[6/6] Done.")
        log(f"Output: {output_path}")
        return 0
    except Exception as exc:
        log(f"[ERROR] {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
