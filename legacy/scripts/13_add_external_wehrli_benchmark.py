#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
13_add_external_wehrli_benchmark.py

Download/parse an external Wehrli / SIA 2028 weather CSV package and normalize one
published 1-in-10 warm-summer file for frontend-only weather comparison.

This script is intentionally permissive because the official ZIP package can contain
multiple station/scenario CSV files with slightly different delimiters and names.
It does NOT claim the file is an EPW; it only normalizes variables for comparison.
"""
from __future__ import annotations

import argparse
import json
import re
import urllib.request
import zipfile
from pathlib import Path
from typing import Dict, List, Tuple

import pandas as pd

DEFAULT_URL = "https://s.geo.admin.ch/94e9d38450"


def log(msg: str) -> None:
    print(msg, flush=True)


def download(url: str, output: Path, overwrite: bool = False) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists() and not overwrite:
        log(f"      cached: {output}")
        return output
    req = urllib.request.Request(url, headers={"User-Agent": "ch2025-epw-pipeline/4.1.5"})
    with urllib.request.urlopen(req, timeout=300) as r:  # noqa: S310
        output.write_bytes(r.read())
    log(f"      saved: {output}")
    return output


def maybe_extract(path: Path, extract_dir: Path) -> List[Path]:
    if zipfile.is_zipfile(path):
        extract_dir.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(path) as z:
            z.extractall(extract_dir)
        return sorted([p for p in extract_dir.rglob("*") if p.suffix.lower() in {".csv", ".txt", ".dat"}])
    return [path]


def score_file(path: Path, station: str, keywords: List[str]) -> int:
    name = path.name.lower()
    score = 0
    if station.lower() in name:
        score += 100
    for kw in keywords:
        if kw.lower() in name:
            score += 20
    if any(k in name for k in ["1in10", "1-in-10", "1_10", "warm", "summer", "ws"]):
        score += 10
    return score


def read_any_csv(path: Path) -> pd.DataFrame:
    # Try common separators; keep column names untouched initially.
    for sep in [";", ",", "\t", r"\s+"]:
        try:
            df = pd.read_csv(path, sep=sep, engine="python")
            if df.shape[1] >= 5:
                return df
        except Exception:
            pass
    raise ValueError(f"Could not parse {path}")


def norm_col(c: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(c).strip().lower())


def find_col(df: pd.DataFrame, candidates: List[str]) -> str | None:
    lookup = {norm_col(c): c for c in df.columns}
    for cand in candidates:
        key = norm_col(cand)
        if key in lookup:
            return lookup[key]
    # substring fallback
    for c in df.columns:
        nc = norm_col(c)
        for cand in candidates:
            if norm_col(cand) and norm_col(cand) in nc:
                return c
    return None


def build_datetime(df: pd.DataFrame) -> pd.Series:
    yy = find_col(df, ["time.yy", "year", "yy"])
    mm = find_col(df, ["time.mm", "month", "mm"])
    dd = find_col(df, ["time.dd", "day", "dd"])
    hh = find_col(df, ["time.hh", "hour", "hh"])
    if yy and mm and dd and hh:
        # prSIA-style time.hh is often 1-24; convert 24 to next-day 00 if needed.
        y = pd.to_numeric(df[yy], errors="coerce").astype("Int64")
        m = pd.to_numeric(df[mm], errors="coerce").astype("Int64")
        d = pd.to_numeric(df[dd], errors="coerce").astype("Int64")
        h_raw = pd.to_numeric(df[hh], errors="coerce")
        h = h_raw.fillna(1).astype(int)
        base = pd.to_datetime({"year": y, "month": m, "day": d}, errors="coerce")
        if h.min() >= 1 and h.max() <= 24:
            return base + pd.to_timedelta(h - 1, unit="h")
        return base + pd.to_timedelta(h, unit="h")
    for dtc in ["datetime", "date", "time", "timestamp"]:
        col = find_col(df, [dtc])
        if col:
            return pd.to_datetime(df[col], errors="coerce")
    raise ValueError("Cannot infer datetime from Wehrli/SIA CSV columns")


def normalize(df: pd.DataFrame, station: str, source_file: Path, label: str) -> pd.DataFrame:
    colmap = {
        "tas": ["tre200h0", "temp", "temperature", "drybulb"],
        "hurs": ["ure200h0", "relhum", "relativehumidity", "rh"],
        "sfcWind": ["fkl010h0", "wind", "windspeed"],
        "windDir": ["dkl010h0", "winddir", "winddirection"],
        "cloudcover": ["skycover", "cloudcover", "cloudiness"],
        "rsds": ["gls", "radglobal", "globalradiation", "ghi"],
        "dhi": ["str.diffus", "strdiffus", "diffuse", "dhi"],
        "dni": ["str.direkt", "strdirekt", "direct", "dni"],
        "precip": ["precip", "rr", "rain"],
    }
    out = pd.DataFrame()
    out["datetime_local_std"] = build_datetime(df)
    out = out.dropna(subset=["datetime_local_std"])
    out["year"] = out["datetime_local_std"].dt.year
    out["month"] = out["datetime_local_std"].dt.month
    out["day"] = out["datetime_local_std"].dt.day
    out["hour"] = out["datetime_local_std"].dt.hour
    for dst, cands in colmap.items():
        col = find_col(df, cands)
        if col:
            out[dst] = pd.to_numeric(df.loc[out.index, col], errors="coerce")
    out["file"] = label
    out["label"] = label
    out["source"] = "Wehrli / SIA 2028 published benchmark"
    out["source_file"] = str(source_file)
    out["station_requested"] = station.upper()
    return out.reset_index(drop=True)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Normalize Wehrli/SIA 2028 1-in-10 benchmark CSV for frontend comparison.")
    p.add_argument("--url", default=DEFAULT_URL)
    p.add_argument("--station", default="sma")
    p.add_argument("--keywords", default="1in10,1-in-10,warm,summer,2060,rcp85", help="Comma-separated file-name selection keywords.")
    p.add_argument("--label", default="Wehrli 1-in-10 warm summer")
    p.add_argument("--cache-dir", default="cache/external_benchmarks/wehrli")
    p.add_argument("--output", required=True)
    p.add_argument("--overwrite", action="store_true")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    cache = Path(args.cache_dir)
    log("[1/4] Downloading external Wehrli/SIA benchmark package ...")
    pkg = download(args.url, cache / "wehrli_sia2028_package", overwrite=args.overwrite)
    log("[2/4] Extracting/discovering CSV files ...")
    files = maybe_extract(pkg, cache / "extracted")
    if not files:
        raise SystemExit("No CSV/TXT files found in downloaded package.")
    keywords = [x.strip() for x in args.keywords.split(",") if x.strip()]
    ranked = sorted([(score_file(f, args.station, keywords), f) for f in files], reverse=True)
    chosen = ranked[0][1]
    log(f"      selected: {chosen} (score={ranked[0][0]})")
    log("[3/4] Parsing and normalizing selected benchmark file ...")
    raw = read_any_csv(chosen)
    norm = normalize(raw, args.station, chosen, args.label)
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    norm.to_csv(out, index=False)
    meta = {
        "source_url": args.url,
        "selected_file": str(chosen),
        "label": args.label,
        "station_requested": args.station.upper(),
        "rows": int(len(norm)),
        "columns": list(norm.columns),
        "positioning": "external published benchmark for frontend weather-level comparison; not a generated XMY and not a complete EPW",
    }
    out.with_suffix(out.suffix + ".meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    log("[4/4] Done.")
    log(f"Output: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
