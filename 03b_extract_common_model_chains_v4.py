#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
03b_extract_common_model_chains_v4.py

Purpose:
    Extract common model chains across an arbitrary set of CH2025 standardized
    daily/signal tables.

Typical FRY v4 use:
    python 03b_extract_common_model_chains_v4.py \
      --inputs tas=...daily_signal_sma_tas_ref91-20_to_gwl2.0.csv \
               hurs=...daily_signal_sma_hurs_ref91-20_to_gwl2.0.csv \
               rsds=...daily_signal_sma_rsds_ref91-20_to_gwl2.0.csv \
               tasmax=...ch2025_daily_sma_tasmax_gwl2.0.csv \
               tasmin=...ch2025_daily_sma_tasmin_gwl2.0.csv

Notes:
    For v4, the intersection should ideally include tas/hurs/rsds/tasmax/tasmin
    so the official tasmax/tasmin tie-break uses the same model-chain set.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List, Set, Tuple

import pandas as pd


def log(msg: str) -> None:
    print(msg, flush=True)


def read_table(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"Input file not found: {path}")
    if path.suffix.lower() == ".parquet":
        return pd.read_parquet(path)
    return pd.read_csv(path)


def parse_labeled_input(text: str) -> Tuple[str, Path]:
    if "=" not in text:
        raise ValueError(f"Input must be in label=path form, got: {text}")
    label, path = text.split("=", 1)
    label = label.strip()
    if not label:
        raise ValueError(f"Empty label in input: {text}")
    return label, Path(path.strip())


def extract_model_chains(df: pd.DataFrame, label: str) -> Set[str]:
    if "model_chain" not in df.columns:
        raise ValueError(f"{label}: missing required column 'model_chain'")
    chains = set(df["model_chain"].dropna().astype(str).unique())
    if not chains:
        raise ValueError(f"{label}: no model chains found")
    return chains


def unique_values(df: pd.DataFrame, col: str) -> List[str]:
    if col not in df.columns:
        return []
    return sorted(df[col].dropna().astype(str).unique().tolist())


def infer_station_ref_target(metadata_by_label: Dict[str, Dict[str, Any]]) -> Tuple[str, str, str]:
    stations = set()
    ref_states = set()
    target_states = set()
    states = set()
    for meta in metadata_by_label.values():
        stations.update(meta.get("station_ids", []))
        ref_states.update(meta.get("ref_states", []))
        target_states.update(meta.get("target_states", []))
        states.update(meta.get("states", []))
    if len(stations) != 1:
        raise ValueError(f"Station mismatch across inputs: {sorted(stations)}")
    station = sorted(stations)[0]
    # Signal files have ref_state/target_state; raw daily target files only have state.
    ref_state = sorted(ref_states)[0] if len(ref_states) == 1 else "unknown_ref"
    if len(target_states) == 1:
        target_state = sorted(target_states)[0]
    elif len(states) == 1:
        target_state = sorted(states)[0]
    else:
        target_state = "unknown_target"
    return station, ref_state, target_state


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Extract common model chains across arbitrary CH2025 tables.")
    p.add_argument("--inputs", nargs="+", required=True, help="Input tables in label=path form")
    p.add_argument("--output-dir", default="./data_processed/common_model_chains")
    p.add_argument("--output-format", choices=["csv", "parquet"], default="csv")
    p.add_argument("--name-suffix", default="v4", help="Suffix included in output filename")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    try:
        labeled_paths = [parse_labeled_input(x) for x in args.inputs]
        chains_by_label: Dict[str, Set[str]] = {}
        meta_by_label: Dict[str, Dict[str, Any]] = {}
        input_files: Dict[str, str] = {}
        for label, path in labeled_paths:
            log(f"Reading {label}: {path}")
            df = read_table(path)
            chains_by_label[label] = extract_model_chains(df, label)
            input_files[label] = str(path)
            meta_by_label[label] = {
                "station_ids": unique_values(df, "station_id"),
                "variable_ids": unique_values(df, "variable_id"),
                "states": unique_values(df, "state"),
                "ref_states": unique_values(df, "ref_state"),
                "target_states": unique_values(df, "target_state"),
                "row_count": int(len(df)),
                "chain_count": len(chains_by_label[label]),
            }
        labels = list(chains_by_label.keys())
        common = set.intersection(*(chains_by_label[label] for label in labels))
        union = set.union(*(chains_by_label[label] for label in labels))
        common_sorted = sorted(common)
        station, ref_state, target_state = infer_station_ref_target(meta_by_label)
        table = pd.DataFrame({
            "station_id": station,
            "ref_state": ref_state,
            "target_state": target_state,
            "model_chain": common_sorted,
        })
        base = f"common_model_chains_{station}_{ref_state}_to_{target_state}_{args.name_suffix}"
        if args.output_format == "parquet":
            table_path = output_dir / f"{base}.parquet"
            try:
                table.to_parquet(table_path, index=False)
            except Exception as exc:
                log(f"[WARN] parquet failed ({exc}); falling back to CSV")
                table_path = output_dir / f"{base}.csv"
                table.to_csv(table_path, index=False)
        else:
            table_path = output_dir / f"{base}.csv"
            table.to_csv(table_path, index=False)
        json_path = output_dir / f"{base}.json"
        payload = {
            "station_id": station,
            "ref_state": ref_state,
            "target_state": target_state,
            "input_files": input_files,
            "labels": labels,
            "counts": {label: len(chains_by_label[label]) for label in labels} | {"union": len(union), "common_intersection": len(common)},
            "chains": {
                "common": common_sorted,
                "by_label": {label: sorted(chains_by_label[label]) for label in labels},
                "missing_from_common_by_label": {label: sorted(chains_by_label[label] - common) for label in labels},
            },
            "metadata_by_label": meta_by_label,
        }
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        log(f"Common model chains: {len(common_sorted)} / union {len(union)}")
        log(f"Table: {table_path}")
        log(f"JSON : {json_path}")
        return 0
    except Exception as exc:
        log(f"[ERROR] {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
