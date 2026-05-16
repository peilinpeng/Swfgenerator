#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
04e_fetch_meteoswiss_satellite_cfc_v4.py

Fetch MeteoSwiss satellite-derived Cloud Fractional Cover (CFC) NetCDF assets
from the FSDI STAC API. This is an auxiliary sky-cover layer for EPW completion.

Design notes:
    - CFC is gridded WGS84 / EPSG:4326 NetCDF, not station data.
    - The script is deliberately discovery-based: it searches the STAC collection
      for assets containing CFC / Cloud Fractional Cover tokens and the requested
      years/months.
    - Current MeteoSwiss Open Data publication may not expose the full 1991-2020
      hourly archive yet. In that case the script writes a manifest and exits with
      code 2 so the batch runner can fallback in auto mode.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.request
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

COLLECTION_URL = "https://data.geo.admin.ch/api/stac/v1/collections/ch.meteoschweiz.ogd-satellite-derived-grid"


def log(msg: str) -> None:
    print(msg, flush=True)


def fetch_json(url: str, timeout: int = 60) -> Dict[str, Any]:
    req = urllib.request.Request(url, headers={"User-Agent": "ch2025-epw-pipeline/4.1.5"})
    with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310
        return json.loads(r.read().decode("utf-8"))


def download(url: str, path: Path, timeout: int = 240, overwrite: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and not overwrite:
        log(f"      cached: {path}")
        return
    req = urllib.request.Request(url, headers={"User-Agent": "ch2025-epw-pipeline/4.1.5"})
    with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310
        path.write_bytes(r.read())
    log(f"      saved: {path}")


def iter_items(collection_url: str, limit: int = 1000) -> Iterable[Dict[str, Any]]:
    url = f"{collection_url}/items?limit={limit}"
    while url:
        data = fetch_json(url)
        for item in data.get("features", []):
            yield item
        next_url = None
        for link in data.get("links", []):
            if link.get("rel") == "next" and link.get("href"):
                next_url = link["href"]
                break
        url = next_url


def text_blob(item: Dict[str, Any], asset_key: str, asset: Dict[str, Any]) -> str:
    parts = [
        item.get("id", ""),
        item.get("title", ""),
        item.get("properties", {}).get("title", ""),
        item.get("properties", {}).get("datetime", ""),
        asset_key,
        asset.get("title", ""),
        asset.get("description", ""),
        asset.get("href", ""),
    ]
    return " ".join(str(x) for x in parts if x).lower()


def year_month_from_text(text: str) -> Tuple[int | None, int | None]:
    # Prefer compact timestamps in filenames, e.g. 202501010000 or 202501.
    m = re.search(r"(19\d{2}|20\d{2})(0[1-9]|1[0-2])(?:\d{2})?(?:\d{4})?", text)
    if m:
        return int(m.group(1)), int(m.group(2))
    y = re.search(r"(19\d{2}|20\d{2})", text)
    return (int(y.group(1)), None) if y else (None, None)


def frequency_match(text: str, frequency: str) -> bool:
    if frequency == "h":
        tokens = [".h_", ".h.", "_h_", "-h-", "hour", "hourly", "cfc.h", "msg.cfc.h"]
    elif frequency == "d":
        tokens = [".d_", ".d.", "_d_", "daily", "cfc.d", "msg.cfc.d"]
    elif frequency == "m":
        tokens = [".m_", ".m.", "_m_", "monthly", "cfc.m", "msg.cfc.m"]
    else:
        tokens = [".y_", ".y.", "_y_", "yearly", "annual", "cfc.y", "msg.cfc.y"]
    return any(t in text for t in tokens)


def find_assets(args: argparse.Namespace) -> List[Dict[str, Any]]:
    wanted_years = set(range(args.start_year, args.end_year + 1))
    wanted_months = set(range(1, 13)) if args.months.lower() in {"all", "*"} else {int(x) for x in args.months.split(",") if x.strip()}
    matches: List[Dict[str, Any]] = []
    log("[1/4] Discovering MeteoSwiss satellite-derived grid collection ...")
    collection = fetch_json(args.collection_url)
    log(f"      Collection: {collection.get('id', '<unknown>')}")
    log(f"      Title     : {collection.get('title', '<no title>')}")
    log("[2/4] Searching CFC NetCDF assets ...")
    for item in iter_items(args.collection_url, limit=args.stac_limit):
        assets = item.get("assets", {}) or {}
        for key, asset in assets.items():
            href = asset.get("href") or ""
            blob = text_blob(item, key, asset)
            if "cfc" not in blob and "cloud fractional" not in blob and "cloud_fraction" not in blob:
                continue
            if ".nc" not in href.lower() and "netcdf" not in blob:
                continue
            if not frequency_match(blob, args.frequency):
                # Keep ambiguous archive assets; discard clearly wrong frequency.
                if any(x in blob for x in ["daily", "monthly", "yearly", "hourly", "cfc.d", "cfc.m", "cfc.y", "cfc.h"]):
                    continue
            yr, mo = year_month_from_text(blob)
            if yr is not None and yr not in wanted_years:
                continue
            if args.frequency == "h" and mo is not None and mo not in wanted_months:
                continue
            score = 0
            score += 100 if "cfc" in blob else 0
            score += 40 if frequency_match(blob, args.frequency) else 0
            score += 20 if yr in wanted_years else 0
            score += 5 if href.lower().endswith(".nc") else 0
            matches.append({
                "score": score,
                "item_id": item.get("id"),
                "asset_key": key,
                "href": href,
                "year": yr,
                "month": mo,
                "title": asset.get("title") or item.get("title") or "",
            })
    matches.sort(key=lambda x: (-int(x.get("score", 0)), str(x.get("year") or ""), str(x.get("month") or ""), str(x.get("asset_key"))))
    # De-duplicate hrefs while preserving order.
    seen = set(); uniq = []
    for m in matches:
        if m["href"] in seen:
            continue
        seen.add(m["href"]); uniq.append(m)
    if args.max_files and args.max_files > 0:
        uniq = uniq[:args.max_files]
    return uniq


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Fetch MeteoSwiss satellite CFC NetCDF assets via STAC.")
    p.add_argument("--collection-url", default=COLLECTION_URL)
    p.add_argument("--frequency", choices=["h", "d", "m", "y"], default="h")
    p.add_argument("--start-year", type=int, default=1991)
    p.add_argument("--end-year", type=int, default=2020)
    p.add_argument("--months", default="all", help="Comma-separated months for hourly monthly files, or all.")
    p.add_argument("--output-dir", default="cache/meteoswiss_cfc")
    p.add_argument("--manifest", default=None)
    p.add_argument("--stac-limit", type=int, default=1000)
    p.add_argument("--max-files", type=int, default=0, help="Safety cap. 0 = no cap.")
    p.add_argument("--overwrite", action="store_true")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    out_dir = Path(args.output_dir) / args.frequency / f"{args.start_year}-{args.end_year}"
    manifest_path = Path(args.manifest) if args.manifest else out_dir / "cfc_fetch_manifest.json"
    try:
        assets = find_assets(args)
        manifest: Dict[str, Any] = {
            "collection_url": args.collection_url,
            "frequency": args.frequency,
            "start_year": args.start_year,
            "end_year": args.end_year,
            "months": args.months,
            "asset_count": len(assets),
            "assets": assets,
            "downloaded_files": [],
            "status": "ok" if assets else "no_assets_found",
            "note": "CFC Open Data archive availability can be limited; auto mode should fallback if no files are found.",
        }
        if not assets:
            manifest_path.parent.mkdir(parents=True, exist_ok=True)
            manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
            log("[WARN] No matching CFC assets found for the requested period/frequency.")
            log(f"      Manifest: {manifest_path}")
            return 2
        log(f"      Matched assets: {len(assets)}")
        log("[3/4] Downloading CFC NetCDF assets ...")
        for a in assets:
            href = a["href"]
            name = Path(href.split("?")[0]).name or f"{a['asset_key']}.nc"
            dest = out_dir / name
            download(href, dest, overwrite=args.overwrite)
            a["local_path"] = str(dest)
            manifest["downloaded_files"].append(str(dest))
        log("[4/4] Writing manifest ...")
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
        log(f"      Manifest: {manifest_path}")
        log("      Done.")
        return 0
    except Exception as exc:
        log(f"[ERROR] {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
