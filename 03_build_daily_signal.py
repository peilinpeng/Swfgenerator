#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
03_build_daily_signal_sma.py

Purpose:
    Build a daily climate-change signal table from two standardized CH2025 tables:
    - reference climate state (e.g. ref91-20)
    - target GWL climate state (e.g. gwl2.0)

Expected inputs:
    Output files from 02_parse_ch2025_sma.py, e.g.
    data_processed/ch2025_daily/ch2025_daily_sma_tas_ref91-20.csv
    data_processed/ch2025_daily/ch2025_daily_sma_tas_gwl2.0.csv

Core logic:
    - For each model_chain and each day-of-year, compute a climatological mean
      over the 30 climate years separately for ref and target.
    - Then derive the signal:
        tas   -> additive delta   = target_mean - ref_mean
        hurs  -> multiplicative factor = target_mean / ref_mean
        rsds  -> multiplicative factor = target_mean / ref_mean

Notes:
    - This first version intentionally supports tas / hurs / rsds only.
    - sfcWind is left for later exploratory handling, consistent with Wehrli-style logic.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Dict, Any, List, Tuple

import numpy as np
import pandas as pd


SUPPORTED_VARIABLES = {"tas", "hurs", "rsds"}


def log(msg: str) -> None:
    print(msg, flush=True)


def infer_output_name(station_id: str, variable_id: str, ref_state: str, target_state: str, fmt: str) -> str:
    suffix = "parquet" if fmt == "parquet" else "csv"
    return f"daily_signal_{station_id}_{variable_id}_{ref_state}_to_{target_state}.{suffix}"


def read_table(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"Input file not found: {path}")

    if path.suffix.lower() == ".parquet":
        return pd.read_parquet(path)

    return pd.read_csv(path)


def basic_checks(df_ref: pd.DataFrame, df_target: pd.DataFrame) -> Tuple[str, str]:
    required_cols = {
        "station_id",
        "variable_id",
        "state",
        "model_chain",
        "climate_year",
        "month",
        "day",
        "doy",
        "value",
    }

    missing_ref = required_cols - set(df_ref.columns)
    missing_target = required_cols - set(df_target.columns)

    if missing_ref:
        raise ValueError(f"Reference table missing columns: {sorted(missing_ref)}")
    if missing_target:
        raise ValueError(f"Target table missing columns: {sorted(missing_target)}")

    station_ref = str(df_ref["station_id"].iloc[0])
    station_target = str(df_target["station_id"].iloc[0])
    if station_ref != station_target:
        raise ValueError(f"Station mismatch: ref={station_ref}, target={station_target}")

    variable_ref = str(df_ref["variable_id"].iloc[0])
    variable_target = str(df_target["variable_id"].iloc[0])
    if variable_ref != variable_target:
        raise ValueError(f"Variable mismatch: ref={variable_ref}, target={variable_target}")

    if variable_ref not in SUPPORTED_VARIABLES:
        raise ValueError(
            f"Variable '{variable_ref}' is not supported in this first version of 03. "
            f"Use one of: {sorted(SUPPORTED_VARIABLES)}"
        )

    return station_ref, variable_ref


def build_daily_climatology(df: pd.DataFrame, value_col_name: str) -> pd.DataFrame:
    """
    Collapse 30 climate years -> 1 climatological value per
    station_id + variable_id + state + model_chain + doy (+ month/day)
    """
    group_cols = [
        "station_id",
        "variable_id",
        "state",
        "model_chain",
        "month",
        "day",
        "doy",
    ]

    out = (
        df.groupby(group_cols, dropna=False)["value"]
        .agg(["mean", "count"])
        .reset_index()
        .rename(columns={"mean": value_col_name, "count": f"{value_col_name}_n"})
    )

    return out


def compute_signal(
    df_ref_clim: pd.DataFrame,
    df_target_clim: pd.DataFrame,
    variable_id: str,
    eps: float = 1e-6,
    max_factor: float = 5.0,
) -> pd.DataFrame:
    merge_cols = ["station_id", "variable_id", "model_chain", "month", "day", "doy"]

    merged = df_ref_clim.merge(
        df_target_clim,
        on=merge_cols,
        how="inner",
        suffixes=("_refmeta", "_targetmeta"),
    )

    # Keep states explicit
    merged["ref_state"] = df_ref_clim["state"].iloc[0]
    merged["target_state"] = df_target_clim["state"].iloc[0]

    ref_vals = pd.to_numeric(merged["ref_mean"], errors="coerce")
    target_vals = pd.to_numeric(merged["target_mean"], errors="coerce")

    if variable_id == "tas":
        merged["signal_kind"] = "delta"
        merged["signal_value"] = target_vals - ref_vals
        merged["signal_note"] = "additive_delta"

    elif variable_id in {"hurs", "rsds"}:
        merged["signal_kind"] = "factor"

        signal_values = np.full(len(merged), np.nan, dtype=float)
        notes: List[str] = []

        for i, (r, t) in enumerate(zip(ref_vals, target_vals)):
            if pd.isna(r) or pd.isna(t):
                notes.append("missing_ref_or_target")
                signal_values[i] = np.nan
            elif abs(r) <= eps and abs(t) <= eps:
                notes.append("both_near_zero_set_to_1")
                signal_values[i] = 1.0
            elif abs(r) <= eps and abs(t) > eps:
                # Avoid propagating NaNs into downstream EPW generation when the
                # reference radiation/humidity climatology is numerically near zero.
                # The capped factor keeps the signal finite and records the fallback.
                notes.append(f"ref_near_zero_target_nonzero_capped_factor_{max_factor:g}")
                signal_values[i] = max_factor
            else:
                raw_factor = t / r
                if variable_id == "rsds" and raw_factor > max_factor:
                    notes.append(f"multiplicative_factor_capped_{max_factor:g}")
                    signal_values[i] = max_factor
                else:
                    notes.append("multiplicative_factor")
                    signal_values[i] = raw_factor

        merged["signal_value"] = signal_values
        merged["signal_note"] = notes

    else:
        raise ValueError(f"Unsupported variable: {variable_id}")

    keep_cols = [
        "station_id",
        "variable_id",
        "model_chain",
        "month",
        "day",
        "doy",
        "ref_state",
        "target_state",
        "ref_mean",
        "ref_mean_n",
        "target_mean",
        "target_mean_n",
        "signal_kind",
        "signal_value",
        "signal_note",
    ]

    out = merged[keep_cols].sort_values(
        by=["model_chain", "month", "day"],
        kind="stable",
    ).reset_index(drop=True)

    return out


def summarize_signal(df_signal: pd.DataFrame) -> Dict[str, Any]:
    sv = pd.to_numeric(df_signal["signal_value"], errors="coerce")
    non_na = sv.dropna()

    return {
        "row_count": int(len(df_signal)),
        "model_chain_count": int(df_signal["model_chain"].nunique()),
        "doy_min": int(df_signal["doy"].min()),
        "doy_max": int(df_signal["doy"].max()),
        "na_fraction_signal_value": float(sv.isna().mean()),
        "signal_min": None if non_na.empty else float(non_na.min()),
        "signal_max": None if non_na.empty else float(non_na.max()),
        "signal_kind": str(df_signal["signal_kind"].iloc[0]),
    }


def validate_signal(df_signal: pd.DataFrame) -> List[str]:
    warnings: List[str] = []

    variable_id = str(df_signal["variable_id"].iloc[0])
    signal_kind = str(df_signal["signal_kind"].iloc[0])
    sv = pd.to_numeric(df_signal["signal_value"], errors="coerce").dropna()

    if int(df_signal["doy"].min()) != 1 or int(df_signal["doy"].max()) != 365:
        warnings.append("Signal DOY range is not 1..365")

    if variable_id == "hurs" and signal_kind == "factor":
        bad = sv[sv < 0]
        if not bad.empty:
            warnings.append(f"hurs factor contains {len(bad)} negative values")

    if variable_id == "rsds" and signal_kind == "factor":
        bad = sv[sv < 0]
        if not bad.empty:
            warnings.append(f"rsds factor contains {len(bad)} negative values")

    return warnings


def save_dataframe(df: pd.DataFrame, output_path: Path, output_format: str) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if output_format == "parquet":
        try:
            df.to_parquet(output_path, index=False)
            return output_path
        except Exception as e:
            log(f"[WARN] Failed to write parquet ({e}). Falling back to CSV.")
            fallback = output_path.with_suffix(".csv")
            df.to_csv(fallback, index=False)
            return fallback

    df.to_csv(output_path, index=False)
    return output_path


def write_metadata_json(
    output_path: Path,
    parse_metadata: Dict[str, Any],
    summary_metadata: Dict[str, Any],
    validation_warnings: List[str],
) -> None:
    sidecar = output_path.with_suffix(output_path.suffix + ".meta.json")
    payload = {
        "parse_metadata": parse_metadata,
        "summary_metadata": summary_metadata,
        "validation_warnings": validation_warnings,
    }
    with open(sidecar, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build daily climate-change signal table from standardized CH2025 daily tables."
    )
    parser.add_argument("--ref", required=True, help="Path to standardized reference-state table")
    parser.add_argument("--target", required=True, help="Path to standardized target-state table")
    parser.add_argument(
        "--output-dir",
        default="./data_processed/daily_signal",
        help="Directory for signal output",
    )
    parser.add_argument(
        "--output-format",
        choices=["csv", "parquet"],
        default="parquet",
        help="Preferred output format",
    )
    parser.add_argument(
        "--eps",
        type=float,
        default=1e-6,
        help="Small threshold used for factor division near zero",
    )
    parser.add_argument(
        "--max-factor",
        type=float,
        default=5.0,
        help="Cap multiplicative hurs/rsds factors when the reference climatology is near zero or the raw factor is extreme.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    ref_path = Path(args.ref)
    target_path = Path(args.target)
    output_dir = Path(args.output_dir)

    try:
        log("[1/5] Reading standardized input tables ...")
        df_ref = read_table(ref_path)
        df_target = read_table(target_path)

        station_id, variable_id = basic_checks(df_ref, df_target)

        ref_state = str(df_ref["state"].iloc[0])
        target_state = str(df_target["state"].iloc[0])

        log("[2/5] Building day-of-year climatologies ...")
        df_ref_clim = build_daily_climatology(df_ref, "ref_mean")
        df_target_clim = build_daily_climatology(df_target, "target_mean")

        log(f"      Reference rows : {len(df_ref_clim)}")
        log(f"      Target rows    : {len(df_target_clim)}")

        log("[3/5] Computing daily signal ...")
        df_signal = compute_signal(df_ref_clim, df_target_clim, variable_id=variable_id, eps=args.eps, max_factor=args.max_factor)

        summary = summarize_signal(df_signal)
        warnings = validate_signal(df_signal)

        log("[4/5] Inspecting signal table ...")
        log(f"      Station       : {station_id}")
        log(f"      Variable      : {variable_id}")
        log(f"      Ref state     : {ref_state}")
        log(f"      Target state  : {target_state}")
        log(f"      Signal kind   : {summary['signal_kind']}")
        log(f"      Rows          : {summary['row_count']}")
        log(f"      Model chains  : {summary['model_chain_count']}")
        log(f"      DOY range     : {summary['doy_min']}–{summary['doy_max']}")
        log(f"      NA fraction   : {summary['na_fraction_signal_value']:.6f}")
        log(f"      Signal range  : {summary['signal_min']} .. {summary['signal_max']}")

        if warnings:
            log("      Validation warnings:")
            for w in warnings:
                log(f"        - {w}")
        else:
            log("      Validation warnings: none")

        output_name = infer_output_name(
            station_id=station_id,
            variable_id=variable_id,
            ref_state=ref_state,
            target_state=target_state,
            fmt=args.output_format,
        )
        output_path = output_dir / output_name

        log("[5/5] Writing signal output ...")
        written_path = save_dataframe(df_signal, output_path, args.output_format)
        log(f"      Output path   : {written_path}")

        write_metadata_json(
            written_path,
            parse_metadata={
                "ref_input_file": str(ref_path),
                "target_input_file": str(target_path),
                "preferred_output_format": args.output_format,
                "eps": args.eps,
            },
            summary_metadata=summary,
            validation_warnings=warnings,
        )
        log("      Metadata sidecar written.")
        log("      Done.")
        return 0

    except Exception as e:
        log(f"[ERROR] {e}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())