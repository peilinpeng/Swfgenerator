#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
06_select_fry_from_candidate_pool_v4.py

Purpose:
    Select a Future Reference Year (FRY) using an EN ISO 15927-4-inspired
    monthly FS selection adapted to CH2025 future target distributions.

v4 update:
    - Primary FS variables remain tas / hurs / rsds.
    - Official CH2025 tasmax / tasmin are used only as tie-break targets.
    - The old pool-derived Tmax/Tmin tie-break is replaced by an official
      scenario-consistent tie-break.

Output:
    - selected 8760-hour FRY CSV
    - 12-row selected-month table
    - full candidate-month ranking table
    - metadata JSON
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


def save_dataframe(df: pd.DataFrame, output_path: Path) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(output_path, index=False)
    return output_path


def write_json(path: Path, payload: Dict[str, Any]) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def fs_statistic(sample: np.ndarray, reference: np.ndarray) -> float:
    sample = np.asarray(sample, dtype=float)
    reference = np.asarray(reference, dtype=float)
    sample = sample[~np.isnan(sample)]
    reference = reference[~np.isnan(reference)]
    if len(sample) == 0 or len(reference) == 0:
        return np.inf
    x = np.sort(np.unique(np.concatenate([sample, reference])))
    sample_sorted = np.sort(sample)
    reference_sorted = np.sort(reference)
    cdf_sample = np.searchsorted(sample_sorted, x, side="right") / len(sample_sorted)
    cdf_ref = np.searchsorted(reference_sorted, x, side="right") / len(reference_sorted)
    return float(np.mean(np.abs(cdf_sample - cdf_ref)))


def load_candidate_pool(path: Path) -> pd.DataFrame:
    df = read_table(path)
    required = {"station_id", "model_chain", "ref_state", "target_state", "year", "month", "day", "hour", "tas_future", "hurs_future", "rsds_future"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"Candidate pool missing required columns: {sorted(missing)}")
    if "sfcWind_future" not in df.columns and "sfcWind_retained" not in df.columns:
        raise ValueError("Candidate pool must include sfcWind_future or sfcWind_retained")
    return df.copy()


def load_common_chains(path: Path) -> List[str]:
    df = read_table(path)
    if "model_chain" not in df.columns:
        raise ValueError("Common-chain file missing model_chain column")
    chains = sorted(df["model_chain"].dropna().astype(str).unique().tolist())
    if not chains:
        raise ValueError("No common model chains found")
    return chains


def load_target_daily(path: Path, expected_variable: str, common_chains: List[str]) -> pd.DataFrame:
    df = read_table(path)
    required = {"station_id", "variable_id", "state", "model_chain", "climate_year", "month", "day", "doy", "value"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"Target daily file {path} missing required columns: {sorted(missing)}")
    variables = sorted(df["variable_id"].dropna().astype(str).unique().tolist())
    if variables != [expected_variable]:
        raise ValueError(f"Target daily file {path} expected {expected_variable}, got {variables}")
    df = df[df["model_chain"].astype(str).isin(common_chains)].copy()
    if df.empty:
        raise ValueError(f"After common-chain filtering, target daily file {path} is empty")
    return df


def build_candidate_daily(candidate_pool: pd.DataFrame) -> pd.DataFrame:
    group_cols = ["station_id", "model_chain", "ref_state", "target_state", "year", "month", "day"]
    return candidate_pool.groupby(group_cols, sort=False).agg(
        tas_daily_mean=("tas_future", "mean"),
        hurs_daily_mean=("hurs_future", "mean"),
        rsds_daily_mean=("rsds_future", "mean"),
        tmax_daily=("tas_future", "max"),
        tmin_daily=("tas_future", "min"),
    ).reset_index()


def build_target_monthly_distributions(target_tas: pd.DataFrame, target_hurs: pd.DataFrame, target_rsds: pd.DataFrame) -> Dict[int, Dict[str, np.ndarray]]:
    return {
        month: {
            "tas": target_tas.loc[target_tas["month"] == month, "value"].dropna().to_numpy(dtype=float),
            "hurs": target_hurs.loc[target_hurs["month"] == month, "value"].dropna().to_numpy(dtype=float),
            "rsds": target_rsds.loc[target_rsds["month"] == month, "value"].dropna().to_numpy(dtype=float),
        }
        for month in range(1, 13)
    }


def build_official_monthly_tmax_tmin_targets(target_tasmax: pd.DataFrame, target_tasmin: pd.DataFrame) -> pd.DataFrame:
    tmax = target_tasmax.groupby("month", sort=True)["value"].mean().rename("target_mean_daily_tmax").reset_index()
    tmin = target_tasmin.groupby("month", sort=True)["value"].mean().rename("target_mean_daily_tmin").reset_index()
    out = tmax.merge(tmin, on="month", how="inner")
    if len(out) != 12:
        raise ValueError(f"Official tasmax/tasmin target should contain 12 months, got {len(out)}")
    return out


def rank_candidates_for_month(
    month: int,
    candidate_daily: pd.DataFrame,
    target_distributions: Dict[int, Dict[str, np.ndarray]],
    official_tmax_tmin: pd.DataFrame,
    top_n_for_tiebreak: int,
) -> Tuple[pd.DataFrame, pd.Series]:
    month_df = candidate_daily[candidate_daily["month"] == month].copy()
    if month_df.empty:
        raise ValueError(f"No candidate daily data found for month={month}")
    target_month = target_distributions[month]
    trow = official_tmax_tmin[official_tmax_tmin["month"] == month]
    if trow.empty:
        raise ValueError(f"No official CH2025 tasmax/tasmin target found for month={month}")
    target_tmax = float(trow["target_mean_daily_tmax"].iloc[0])
    target_tmin = float(trow["target_mean_daily_tmin"].iloc[0])
    records: List[Dict[str, Any]] = []
    for (model_chain, source_year), grp in month_df.groupby(["model_chain", "year"], sort=False):
        records.append({
            "month": month,
            "model_chain": str(model_chain),
            "source_year": int(source_year),
            "fs_tas": fs_statistic(grp["tas_daily_mean"].dropna().to_numpy(dtype=float), target_month["tas"]),
            "fs_hurs": fs_statistic(grp["hurs_daily_mean"].dropna().to_numpy(dtype=float), target_month["hurs"]),
            "fs_rsds": fs_statistic(grp["rsds_daily_mean"].dropna().to_numpy(dtype=float), target_month["rsds"]),
            "mean_daily_tmax": float(grp["tmax_daily"].mean()),
            "mean_daily_tmin": float(grp["tmin_daily"].mean()),
            "target_mean_daily_tmax": target_tmax,
            "target_mean_daily_tmin": target_tmin,
            "tiebreak_source": "official_CH2025_tasmax_tasmin",
        })
    rank_df = pd.DataFrame(records)
    for var in ["tas", "hurs", "rsds"]:
        rank_df[f"rank_{var}"] = rank_df[f"fs_{var}"].rank(method="min", ascending=True)
    rank_df["rank_sum"] = rank_df["rank_tas"] + rank_df["rank_hurs"] + rank_df["rank_rsds"]
    rank_df["fs_sum"] = rank_df["fs_tas"] + rank_df["fs_hurs"] + rank_df["fs_rsds"]
    rank_df = rank_df.sort_values(["rank_sum", "fs_sum", "model_chain", "source_year"], kind="stable").reset_index(drop=True)
    shortlist = rank_df.head(min(top_n_for_tiebreak, len(rank_df))).copy()
    shortlist["tiebreak_score"] = shortlist.apply(
        lambda r: max(abs(r["mean_daily_tmax"] - r["target_mean_daily_tmax"]), abs(r["mean_daily_tmin"] - r["target_mean_daily_tmin"])),
        axis=1,
    )
    shortlist = shortlist.sort_values(["tiebreak_score", "rank_sum", "fs_sum", "model_chain", "source_year"], kind="stable").reset_index(drop=True)
    selected = shortlist.iloc[0].copy()
    rank_df["selected_flag"] = (rank_df["model_chain"] == selected["model_chain"]) & (rank_df["source_year"] == selected["source_year"])
    rank_df["tiebreak_score"] = np.nan
    rank_df.loc[rank_df["selected_flag"], "tiebreak_score"] = selected["tiebreak_score"]
    return rank_df, selected


def choose_col(df: pd.DataFrame, candidates: List[str]) -> str | None:
    for c in candidates:
        if c in df.columns:
            return c
    return None


def extract_selected_hourly(candidate_pool: pd.DataFrame, selected_months: pd.DataFrame, synthetic_year: int) -> pd.DataFrame:
    blocks: List[pd.DataFrame] = []
    for _, row in selected_months.sort_values("month", kind="stable").iterrows():
        month = int(row["month"])
        chain = str(row["model_chain"])
        year = int(row["source_year"])
        block = candidate_pool[(candidate_pool["model_chain"].astype(str) == chain) & (candidate_pool["year"] == year) & (candidate_pool["month"] == month)].copy()
        if block.empty:
            raise ValueError(f"No hourly rows found for selected month={month}, chain={chain}, source_year={year}")
        block = block.sort_values(["day", "hour"], kind="stable").reset_index(drop=True)
        block["source_year"] = year
        block["source_model_chain"] = chain
        blocks.append(block)
    fry = pd.concat(blocks, ignore_index=True)
    fry["datetime_fry"] = pd.to_datetime({"year": synthetic_year, "month": fry["month"].astype(int), "day": fry["day"].astype(int), "hour": fry["hour"].astype(int)}, errors="raise")
    output = pd.DataFrame({
        "station_id": fry["station_id"],
        "ref_state": fry["ref_state"],
        "target_state": fry["target_state"],
        "file_type": "FRY",
        "source_model_chain": fry["source_model_chain"],
        "source_year": fry["source_year"],
        "datetime": fry["datetime_fry"],
        "month": fry["month"],
        "day": fry["day"],
        "hour": fry["hour"],
        "tas": fry["tas_future"],
        "hurs": fry["hurs_future"],
        "rsds": fry["rsds_future"],
    })
    wind_col = choose_col(fry, ["sfcWind_future", "sfcWind_retained", "sfcWind"])
    if wind_col:
        output["sfcWind"] = fry[wind_col]
    for out_name, candidates in {
        "pres": ["pres_retained", "pres_future", "pres"],
        "windDir": ["windDir_retained", "windDir_future", "windDir"],
        "horizIR": ["horizIR_retained", "horizIR_future", "horizIR"],
        "cloudcover": ["cloudcover_retained", "cloudcover_future", "cloudcover"],
        "dhi_obs": ["dhi_retained", "dhi_future", "dhi"],
    }.items():
        col = choose_col(fry, candidates)
        if col:
            output[out_name] = fry[col]
    return output


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Select future FRY with official CH2025 tasmax/tasmin tie-break.")
    p.add_argument("--candidate-pool", default="./data_processed/hourly_future_candidates/hourly_future_candidates_sma_ref91-20_to_gwl2.0_v4.csv")
    p.add_argument("--target-tas", default="./data_processed/ch2025_daily/ch2025_daily_sma_tas_gwl2.0.csv")
    p.add_argument("--target-hurs", default="./data_processed/ch2025_daily/ch2025_daily_sma_hurs_gwl2.0.csv")
    p.add_argument("--target-rsds", default="./data_processed/ch2025_daily/ch2025_daily_sma_rsds_gwl2.0.csv")
    p.add_argument("--target-tasmax", default="./data_processed/ch2025_daily/ch2025_daily_sma_tasmax_gwl2.0.csv")
    p.add_argument("--target-tasmin", default="./data_processed/ch2025_daily/ch2025_daily_sma_tasmin_gwl2.0.csv")
    p.add_argument("--common-chains", default="./data_processed/common_model_chains/common_model_chains_sma_ref91-20_to_gwl2.0_v4.csv")
    p.add_argument("--output-dir", default="./data_processed/fry")
    p.add_argument("--top-n-for-tiebreak", type=int, default=5)
    p.add_argument("--synthetic-year", type=int, default=2001)
    return p.parse_args()


def main() -> int:
    args = parse_args()
    try:
        output_dir = Path(args.output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        common_chains = load_common_chains(Path(args.common_chains))
        pool = load_candidate_pool(Path(args.candidate_pool))
        pool = pool[pool["model_chain"].astype(str).isin(common_chains)].copy()
        station_id = sorted(pool["station_id"].dropna().astype(str).unique().tolist())[0]
        ref_state = sorted(pool["ref_state"].dropna().astype(str).unique().tolist())[0]
        target_state = sorted(pool["target_state"].dropna().astype(str).unique().tolist())[0]
        log("[1/5] Loading official CH2025 target distributions ...")
        target_tas = load_target_daily(Path(args.target_tas), "tas", common_chains)
        target_hurs = load_target_daily(Path(args.target_hurs), "hurs", common_chains)
        target_rsds = load_target_daily(Path(args.target_rsds), "rsds", common_chains)
        target_tasmax = load_target_daily(Path(args.target_tasmax), "tasmax", common_chains)
        target_tasmin = load_target_daily(Path(args.target_tasmin), "tasmin", common_chains)
        log("[2/5] Building candidate daily summaries ...")
        daily = build_candidate_daily(pool)
        target_dist = build_target_monthly_distributions(target_tas, target_hurs, target_rsds)
        official_tmax_tmin = build_official_monthly_tmax_tmin_targets(target_tasmax, target_tasmin)
        log("[3/5] Ranking candidate months ...")
        rank_tables: List[pd.DataFrame] = []
        selected: List[pd.Series] = []
        for month in range(1, 13):
            r, s = rank_candidates_for_month(month, daily, target_dist, official_tmax_tmin, args.top_n_for_tiebreak)
            rank_tables.append(r)
            selected.append(s)
        selection_table = pd.DataFrame(selected).reset_index(drop=True)
        full_ranks = pd.concat(rank_tables, ignore_index=True)
        log("[4/5] Extracting selected months ...")
        fry = extract_selected_hourly(pool, selection_table, args.synthetic_year)
        base = f"fry_{station_id}_{ref_state}_to_{target_state}_v4"
        fry_path = output_dir / f"{base}.csv"
        selection_path = output_dir / f"{base}_selection.csv"
        rankings_path = output_dir / f"{base}_rankings.csv"
        save_dataframe(fry, fry_path)
        save_dataframe(selection_table, selection_path)
        save_dataframe(full_ranks, rankings_path)
        meta = {
            "inputs": {
                "candidate_pool": args.candidate_pool,
                "target_tas": args.target_tas,
                "target_hurs": args.target_hurs,
                "target_rsds": args.target_rsds,
                "target_tasmax": args.target_tasmax,
                "target_tasmin": args.target_tasmin,
                "common_chains": args.common_chains,
            },
            "method": {
                "primary_selection_variables": ["tas", "hurs", "rsds"],
                "primary_selection_score": "equal-weight rank sum of FS distances",
                "tiebreak_variables": ["tasmax", "tasmin"],
                "tiebreak_source": "official_CH2025_daily_tasmax_tasmin",
                "tiebreak_scope": f"top {args.top_n_for_tiebreak} candidates per month",
                "tiebreak_score": "max absolute difference between candidate monthly mean Tmax/Tmin and official CH2025 monthly mean tasmax/tasmin",
                "tasmax_tasmin_not_used_as_primary_FS_variables": True,
            },
            "summary_metadata": {
                "station_id": station_id,
                "ref_state": ref_state,
                "target_state": target_state,
                "common_chain_count": len(common_chains),
                "candidate_pool_rows_after_chain_filter": int(len(pool)),
                "candidate_daily_rows": int(len(daily)),
                "fry_row_count": int(len(fry)),
                "synthetic_year": int(args.synthetic_year),
                "passthrough_columns": [c for c in ["pres", "windDir", "horizIR", "cloudcover", "dhi_obs"] if c in fry.columns],
            },
        }
        write_json(fry_path.with_suffix(fry_path.suffix + ".meta.json"), meta)
        log("[5/5] Done.")
        log(f"FRY: {fry_path}")
        return 0
    except Exception as exc:
        log(f"[ERROR] {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
