#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
04b_fetch_meteoswiss_cloudcover_obs_v4.py

Purpose:
    Automatically discover and download MeteoSwiss meteorological visual
    observation files from the FSDI / data.geo.admin.ch STAC API.

Why this exists:
    Cloud cover is usually not part of the SwissMetNet automatic hourly
    observation file (ogd-smn). For EPW Total Sky Cover, the workflow needs a
    separate auxiliary cloud-cover layer. This script fetches the ground-based
    visual-observation product (ogd-obs) when available. The next script parses
    cloud-cover variables and interpolates them onto the hourly backbone.

Default use:
    python3 04b_fetch_meteoswiss_cloudcover_obs_v4.py \
      --station gve --granularity d --update-types historical --metadata

Notes:
    - Collection default: ch.meteoschweiz.ogd-obs.
    - Visual observations may be daily/low-frequency and may not be available
      for every automatic station. If unavailable, the batch runner treats this
      as an optional EPW auxiliary fallback, not as a core workflow failure.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

STAC_ROOT = "https://data.geo.admin.ch/api/stac/v1"
DEFAULT_COLLECTION_ID = "ch.meteoschweiz.ogd-obs"
DEFAULT_TIMEOUT = 90


def log(msg: str) -> None:
    print(msg, flush=True)


def normalize_text(value: Any) -> str:
    return "" if value is None else str(value).strip().lower()


def fetch_json(url: str, timeout: int = DEFAULT_TIMEOUT) -> Dict[str, Any]:
    req = urllib.request.Request(url, headers={"User-Agent": "ch2025-epw-pipeline/0.413", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        if resp.status != 200:
            raise RuntimeError(f"HTTP {resp.status} while requesting {url}")
        return json.loads(resp.read().decode("utf-8"))


def download_file(url: str, dest: Path, force: bool = False, timeout: int = DEFAULT_TIMEOUT) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and not force:
        return
    req = urllib.request.Request(url, headers={"User-Agent": "ch2025-epw-pipeline/0.413"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        if resp.status != 200:
            raise RuntimeError(f"HTTP {resp.status} while downloading {url}")
        with open(dest, "wb") as f:
            f.write(resp.read())


def iter_paginated(url: str, array_key: str) -> Iterable[Dict[str, Any]]:
    next_url = url
    while next_url:
        payload = fetch_json(next_url)
        rows = payload.get(array_key, [])
        if not isinstance(rows, list):
            raise RuntimeError(f"Unexpected response structure at {next_url}")
        for row in rows:
            yield row
        next_url = None
        for link in payload.get("links", []):
            if link.get("rel") == "next":
                next_url = link.get("href")
                break


def link_href(obj: Dict[str, Any], rel: str) -> Optional[str]:
    for link in obj.get("links", []):
        if link.get("rel") == rel:
            return link.get("href")
    return None


def load_collection(collection_id: str) -> Dict[str, Any]:
    return fetch_json(f"{STAC_ROOT}/collections/{urllib.parse.quote(collection_id, safe='')}")


def discover_station_item(collection: Dict[str, Any], station: str) -> Dict[str, Any]:
    station_norm = normalize_text(station)
    items_url = link_href(collection, "items") or f"{STAC_ROOT}/collections/{urllib.parse.quote(collection.get('id'), safe='')}/items"
    matches: List[Tuple[int, Dict[str, Any]]] = []
    for item in iter_paginated(items_url, "features"):
        iid = normalize_text(item.get("id"))
        title = normalize_text(item.get("title"))
        props = item.get("properties", {}) if isinstance(item.get("properties"), dict) else {}
        prop_text = " ".join(normalize_text(v) for v in props.values())
        text = " ".join([iid, title, prop_text])
        score = 0
        if iid == station_norm:
            score += 50
        if f"_{station_norm}_" in text or f"/{station_norm}/" in text:
            score += 20
        if re.search(rf"\b{re.escape(station_norm)}\b", text):
            score += 10
        if station_norm in text:
            score += 3
        if score:
            matches.append((score, item))
    if not matches:
        raise RuntimeError(f"Could not find visual-observation station item '{station}' in collection {collection.get('id')}.")
    matches.sort(key=lambda x: x[0], reverse=True)
    return matches[0][1]


def parse_years_from_text(text: str) -> List[int]:
    return [int(x) for x in re.findall(r"(?:19|20)\d{2}", text)]


def overlaps_year_range(text: str, start_year: Optional[int], end_year: Optional[int]) -> bool:
    if start_year is None and end_year is None:
        return True
    years = parse_years_from_text(text)
    if not years:
        return True
    amin, amax = min(years), max(years)
    s = start_year if start_year is not None else -10**9
    e = end_year if end_year is not None else 10**9
    return not (amax < s or amin > e)


def safe_filename(asset_key: str, asset: Dict[str, Any]) -> str:
    href = asset.get("href", "")
    name = os.path.basename(urllib.parse.urlparse(href).path) if href else ""
    return name or asset_key.replace("/", "_")


def asset_matches(asset_key: str, asset: Dict[str, Any], station: str, granularity: str, update_types: List[str], start_year: Optional[int], end_year: Optional[int]) -> Tuple[bool, int, str]:
    station_norm = normalize_text(station)
    gran = normalize_text(granularity)
    updates = [normalize_text(u) for u in update_types if u]
    key = normalize_text(asset_key)
    href = normalize_text(asset.get("href"))
    title = normalize_text(asset.get("title"))
    desc = normalize_text(asset.get("description"))
    media = normalize_text(asset.get("type"))
    text = " ".join([key, href, title, desc, media])
    score = 0
    if any(p in text for p in [f"_{station_norm}_", f"/{station_norm}/", f"-{station_norm}-"]):
        score += 25
    elif station_norm in text:
        score += 8
    else:
        return False, 0, "station_not_matched"
    if any(p in text for p in [f"_{gran}_", f"-{gran}-"]):
        score += 25
    elif re.search(rf"\b{re.escape(gran)}\b", text):
        score += 8
    else:
        return False, 0, "granularity_not_matched"
    if updates:
        if not any(u in text for u in updates):
            return False, 0, "update_type_not_matched"
        score += 15
    if not overlaps_year_range(text, start_year, end_year):
        return False, 0, "outside_year_range"
    if href.endswith(".csv") or "csv" in media or "csv" in text:
        score += 5
    else:
        return False, 0, "not_csv"
    if "historical" in text:
        score += 3
    return True, score, "matched"


def select_assets(item: Dict[str, Any], station: str, granularity: str, update_types: List[str], start_year: Optional[int], end_year: Optional[int]) -> List[Tuple[str, Dict[str, Any], int]]:
    assets = item.get("assets", {})
    if not isinstance(assets, dict) or not assets:
        raise RuntimeError("Station item has no assets.")
    selected: List[Tuple[str, Dict[str, Any], int]] = []
    rejected: Dict[str, int] = {}
    for key, asset in assets.items():
        ok, score, reason = asset_matches(key, asset, station, granularity, update_types, start_year, end_year)
        if ok:
            selected.append((key, asset, score))
        else:
            rejected[reason] = rejected.get(reason, 0) + 1
    if not selected:
        raise RuntimeError(f"No cloud-cover observation asset matched station={station}, granularity={granularity}, update_types={update_types}. rejected={rejected}; available={list(assets.keys())[:20]}")
    selected.sort(key=lambda x: (x[2], x[0]), reverse=True)
    return selected


def download_collection_metadata(collection: Dict[str, Any], cache_dir: Path, force: bool) -> List[Dict[str, Any]]:
    downloaded: List[Dict[str, Any]] = []
    assets = collection.get("assets", {}) if isinstance(collection.get("assets"), dict) else {}
    for key, asset in assets.items():
        href = asset.get("href")
        if not href:
            continue
        dest = cache_dir / "metadata" / safe_filename(key, asset)
        download_file(href, dest, force=force)
        downloaded.append({"asset_key": key, "href": href, "path": str(dest)})
    return downloaded


def split_csv(text: str) -> List[str]:
    return [x.strip().lower() for x in text.split(",") if x.strip()]


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Fetch MeteoSwiss visual-observation cloud-cover files through STAC.")
    p.add_argument("--station", required=True)
    p.add_argument("--granularity", default="d", choices=["h", "d", "m", "y"], help="Visual observations are often daily/low-frequency; default d.")
    p.add_argument("--update-types", default="historical")
    p.add_argument("--collection-id", default=DEFAULT_COLLECTION_ID)
    p.add_argument("--cache-dir", default="./cache/meteoswiss_cloudcover")
    p.add_argument("--start-year", type=int, default=None)
    p.add_argument("--end-year", type=int, default=None)
    p.add_argument("--metadata", action="store_true")
    p.add_argument("--force", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    station = args.station.strip().lower()
    granularity = args.granularity.strip().lower()
    update_types = split_csv(args.update_types)
    cache_root = Path(args.cache_dir)
    out_dir = cache_root / station / granularity
    manifest_path = out_dir / f"meteoswiss_cloudcover_{station}_{granularity}_fetch_manifest.json"
    try:
        log("[1/5] Loading MeteoSwiss visual-observation STAC collection ...")
        collection = load_collection(args.collection_id)
        log(f"      Collection: {collection.get('id')} — {collection.get('title')}")
        log(f"[2/5] Finding visual-observation station item for '{station}' ...")
        item = discover_station_item(collection, station)
        log(f"      Item ID   : {item.get('id')}")
        log(f"      Item title: {item.get('title', '<no title>')}")
        log("[3/5] Selecting matching cloud-cover observation assets ...")
        matches = select_assets(item, station, granularity, update_types, args.start_year, args.end_year)
        for key, asset, score in matches:
            log(f"      score={score:02d}  {key}  -> {asset.get('href')}")
        if args.dry_run:
            log("[4/5] Dry run. No files downloaded.")
            return 0
        log("[4/5] Downloading matched cloud-cover files ...")
        downloaded: List[Dict[str, Any]] = []
        for key, asset, score in matches:
            href = asset.get("href")
            if not href:
                continue
            dest = out_dir / safe_filename(key, asset)
            download_file(href, dest, force=args.force)
            downloaded.append({"asset_key": key, "href": href, "score": score, "path": str(dest), "type": asset.get("type"), "title": asset.get("title")})
            log(f"      saved: {dest}")
        metadata_files: List[Dict[str, Any]] = []
        if args.metadata:
            log("      Downloading collection metadata assets ...")
            metadata_files = download_collection_metadata(collection, cache_root, args.force)
            for row in metadata_files:
                log(f"      metadata: {row['path']}")
        out_dir.mkdir(parents=True, exist_ok=True)
        manifest = {
            "collection_id": args.collection_id,
            "station": station,
            "granularity": granularity,
            "update_types": update_types,
            "downloaded_files": downloaded,
            "metadata_files": metadata_files,
            "notes": {
                "source": "MeteoSwiss meteorological visual observations via FSDI/data.geo.admin.ch STAC API",
                "manual_download_required": False,
                "next_step": f"python3 04c_parse_cloudcover_observations_v4.py --input {out_dir} --station-id {station} --output-format csv",
            },
        }
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        log("[5/5] Writing fetch manifest ...")
        log(f"      Manifest: {manifest_path}")
        log("      Done.")
        return 0
    except Exception as exc:
        log(f"[ERROR] {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
