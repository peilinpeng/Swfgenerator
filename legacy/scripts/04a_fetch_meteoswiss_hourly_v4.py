#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
04a_fetch_meteoswiss_hourly_v4.py

Purpose:
    Automatically discover and download MeteoSwiss Open Data ground-based
    measurement files from the FSDI / data.geo.admin.ch STAC API.

Default use in this thesis pipeline:
    Fetch SwissMetNet hourly station files, e.g. SMA hourly observations,
    without any manual browser download.

Example:
    python3 04a_fetch_meteoswiss_hourly_v4.py \
      --station sma \
      --granularity h \
      --update-types historical \
      --cache-dir ./cache/meteoswiss

Then parse all downloaded files:
    python3 04_parse_meteoswiss_hourly_v4.py \
      --input ./cache/meteoswiss/sma/h \
      --station-id sma \
      --output-format csv

Notes:
    - Uses only Python standard library.
    - Default collection is ch.meteoschweiz.ogd-smn, i.e. Automatic weather
      stations / SwissMetNet measurement values.
    - MeteoSwiss OGD is currently file-based through STAC. This script still
      uses an API, but the downloaded object is a file asset rather than a
      row-level query endpoint.
    - Historical files may be split into several assets, e.g. by decade. The
      script downloads all matching assets and writes a manifest JSON.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

STAC_ROOT = "https://data.geo.admin.ch/api/stac/v1"
DEFAULT_COLLECTION_ID = "ch.meteoschweiz.ogd-smn"
DEFAULT_TIMEOUT = 90


def log(msg: str) -> None:
    print(msg, flush=True)


def normalize_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip().lower()


def fetch_json(url: str, timeout: int = DEFAULT_TIMEOUT) -> Dict[str, Any]:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "ch2025-epw-pipeline/0.4 meteoswiss-fetcher",
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        if resp.status != 200:
            raise RuntimeError(f"HTTP {resp.status} while requesting {url}")
        return json.loads(resp.read().decode("utf-8"))


def download_file(url: str, dest: Path, force: bool = False, timeout: int = DEFAULT_TIMEOUT) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and not force:
        return
    req = urllib.request.Request(url, headers={"User-Agent": "ch2025-epw-pipeline/0.4"})
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


def item_links_get(obj: Dict[str, Any], rel: str) -> Optional[str]:
    for link in obj.get("links", []):
        if link.get("rel") == rel:
            return link.get("href")
    return None


def load_collection(collection_id: str) -> Dict[str, Any]:
    url = f"{STAC_ROOT}/collections/{urllib.parse.quote(collection_id, safe='')}"
    return fetch_json(url)


def discover_station_item(collection: Dict[str, Any], station: str) -> Dict[str, Any]:
    station_norm = normalize_text(station)
    items_url = item_links_get(collection, "items")
    if not items_url:
        cid = collection.get("id")
        if not cid:
            raise RuntimeError("Collection has neither an 'items' link nor an 'id'.")
        items_url = f"{STAC_ROOT}/collections/{urllib.parse.quote(cid, safe='')}/items"

    matches: List[Tuple[int, Dict[str, Any]]] = []
    for item in iter_paginated(items_url, "features"):
        iid = normalize_text(item.get("id"))
        title = normalize_text(item.get("title"))
        desc = normalize_text(item.get("description"))
        props = item.get("properties", {}) if isinstance(item.get("properties"), dict) else {}
        prop_text = " ".join(normalize_text(v) for v in props.values())
        text = " ".join([iid, title, desc, prop_text])

        score = 0
        if iid == station_norm:
            score += 50
        if f"_{station_norm}_" in text or f"-{station_norm}-" in text:
            score += 20
        if re.search(rf"\b{re.escape(station_norm)}\b", text):
            score += 15
        if station_norm in text:
            score += 5
        if score > 0:
            matches.append((score, item))

    if not matches:
        raise RuntimeError(
            f"Could not find station item '{station}' in collection {collection.get('id')}. "
            "Try checking station code or use the STAC Browser."
        )
    matches.sort(key=lambda x: x[0], reverse=True)
    return matches[0][1]


def safe_filename_from_asset(asset_key: str, asset: Dict[str, Any]) -> str:
    href = asset.get("href", "")
    if href:
        path = urllib.parse.urlparse(href).path
        name = os.path.basename(path)
        if name:
            return name
    return asset_key.replace("/", "_")


def parse_years_from_text(text: str) -> List[int]:
    return [int(x) for x in re.findall(r"(?:19|20)\d{2}", text)]


def asset_overlaps_year_range(text: str, start_year: Optional[int], end_year: Optional[int]) -> bool:
    if start_year is None and end_year is None:
        return True
    years = parse_years_from_text(text)
    # If no year appears in the asset name/metadata, keep it. The file may be
    # named simply *_historical.csv or *_recent.csv.
    if not years:
        return True
    amin, amax = min(years), max(years)
    s = start_year if start_year is not None else -10**9
    e = end_year if end_year is not None else 10**9
    return not (amax < s or amin > e)


def asset_matches(
    asset_key: str,
    asset: Dict[str, Any],
    station: str,
    granularity: str,
    update_types: List[str],
    start_year: Optional[int],
    end_year: Optional[int],
) -> Tuple[bool, int, str]:
    station_norm = normalize_text(station)
    gran = normalize_text(granularity)
    updates = [normalize_text(x) for x in update_types]

    key = normalize_text(asset_key)
    href = normalize_text(asset.get("href"))
    title = normalize_text(asset.get("title"))
    desc = normalize_text(asset.get("description"))
    media_type = normalize_text(asset.get("type"))
    text = " ".join([key, href, title, desc, media_type])

    # Must look like a CSV asset for the requested station and granularity.
    score = 0
    station_patterns = [
        f"_{station_norm}_",
        f"-{station_norm}-",
        f"/{station_norm}/",
        f"{station_norm}_",
    ]
    if any(p in text for p in station_patterns):
        score += 25
    elif station_norm in text:
        score += 8
    else:
        return False, 0, "station_not_matched"

    gran_patterns = [f"_{gran}_", f"-{gran}-", f"_{gran}-", f"-{gran}_"]
    if any(p in text for p in gran_patterns):
        score += 25
    elif re.search(rf"\b{re.escape(gran)}\b", text):
        score += 8
    else:
        return False, 0, "granularity_not_matched"

    if updates:
        update_hit = False
        for u in updates:
            if u and u in text:
                update_hit = True
                score += 15
        if not update_hit:
            return False, 0, "update_type_not_matched"

    if not asset_overlaps_year_range(text, start_year, end_year):
        return False, 0, "outside_requested_year_range"

    if href.endswith(".csv") or "text/csv" in media_type or "csv" in text:
        score += 5
    else:
        return False, 0, "not_csv"

    if "historical" in text:
        score += 3
    if "recent" in text:
        score += 2
    if "now" in text:
        score += 1

    return True, score, "matched"


def select_assets(
    item: Dict[str, Any],
    station: str,
    granularity: str,
    update_types: List[str],
    start_year: Optional[int],
    end_year: Optional[int],
) -> List[Tuple[str, Dict[str, Any], int]]:
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
        available = list(assets.keys())[:30]
        raise RuntimeError(
            "No MeteoSwiss asset matched the requested station/granularity/update type.\n"
            f"  station={station}, granularity={granularity}, update_types={update_types}\n"
            f"  rejected_summary={rejected}\n"
            f"  first_available_asset_keys={available}"
        )

    selected.sort(key=lambda x: (x[2], x[0]), reverse=True)
    return selected


def download_collection_metadata(collection: Dict[str, Any], cache_dir: Path, force: bool) -> List[Dict[str, Any]]:
    downloaded: List[Dict[str, Any]] = []
    assets = collection.get("assets", {}) if isinstance(collection.get("assets"), dict) else {}
    for key, asset in assets.items():
        href = asset.get("href")
        if not href:
            continue
        filename = safe_filename_from_asset(key, asset)
        dest = cache_dir / "metadata" / filename
        download_file(href, dest, force=force)
        downloaded.append({"asset_key": key, "href": href, "path": str(dest)})
    return downloaded


def parse_update_types(value: str) -> List[str]:
    if not value.strip():
        return []
    return [x.strip().lower() for x in value.split(",") if x.strip()]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fetch MeteoSwiss hourly station data through the STAC API.")
    parser.add_argument("--station", required=True, help="Station code, e.g. sma")
    parser.add_argument("--granularity", default="h", choices=["t", "h", "d", "m", "y"], help="MeteoSwiss data granularity; h = hourly")
    parser.add_argument("--update-types", default="historical", help="Comma-separated update types: historical,recent,now. Default: historical")
    parser.add_argument("--collection-id", default=DEFAULT_COLLECTION_ID, help="MeteoSwiss STAC collection id")
    parser.add_argument("--cache-dir", default="./cache/meteoswiss", help="Local cache root")
    parser.add_argument("--start-year", type=int, default=None, help="Optional year-range filter for historical assets")
    parser.add_argument("--end-year", type=int, default=None, help="Optional year-range filter for historical assets")
    parser.add_argument("--metadata", action="store_true", help="Also download collection-level metadata CSV files")
    parser.add_argument("--force", action="store_true", help="Force re-download existing files")
    parser.add_argument("--dry-run", action="store_true", help="Discover matching assets but do not download")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    station = args.station.strip().lower()
    granularity = args.granularity.strip().lower()
    update_types = parse_update_types(args.update_types)
    cache_root = Path(args.cache_dir)
    out_dir = cache_root / station / granularity
    manifest_path = out_dir / f"meteoswiss_{station}_{granularity}_fetch_manifest.json"

    try:
        log("[1/5] Loading MeteoSwiss STAC collection ...")
        collection = load_collection(args.collection_id)
        log(f"      Collection: {collection.get('id')} — {collection.get('title')}")

        log(f"[2/5] Finding station item for '{station}' ...")
        item = discover_station_item(collection, station)
        log(f"      Item ID   : {item.get('id')}")
        log(f"      Item title: {item.get('title', '<no title>')}")

        log("[3/5] Selecting matching data assets ...")
        matches = select_assets(item, station, granularity, update_types, args.start_year, args.end_year)
        for key, asset, score in matches:
            log(f"      score={score:02d}  {key}  -> {asset.get('href')}")

        if args.dry_run:
            log("[4/5] Dry run enabled. No data files downloaded.")
            return 0

        log("[4/5] Downloading matched data files ...")
        downloaded: List[Dict[str, Any]] = []
        for key, asset, score in matches:
            href = asset.get("href")
            if not href:
                continue
            filename = safe_filename_from_asset(key, asset)
            dest = out_dir / filename
            download_file(href, dest, force=args.force)
            downloaded.append({
                "asset_key": key,
                "href": href,
                "score": score,
                "path": str(dest),
                "type": asset.get("type"),
                "title": asset.get("title"),
                "description": asset.get("description"),
            })
            log(f"      saved: {dest}")

        metadata_downloaded: List[Dict[str, Any]] = []
        if args.metadata:
            log("      Downloading collection metadata assets ...")
            metadata_downloaded = download_collection_metadata(collection, cache_root, force=args.force)
            for row in metadata_downloaded:
                log(f"      metadata: {row['path']}")

        log("[5/5] Writing fetch manifest ...")
        out_dir.mkdir(parents=True, exist_ok=True)
        manifest = {
            "stac_root": STAC_ROOT,
            "collection_id": args.collection_id,
            "station": station,
            "granularity": granularity,
            "update_types": update_types,
            "start_year": args.start_year,
            "end_year": args.end_year,
            "item_id": item.get("id"),
            "item_title": item.get("title"),
            "downloaded_files": downloaded,
            "metadata_files": metadata_downloaded,
            "notes": {
                "source": "MeteoSwiss Open Data via FSDI/data.geo.admin.ch STAC API",
                "manual_download_required": False,
                "next_step": f"python3 04_parse_meteoswiss_hourly_v4.py --input {out_dir} --station-id {station} --output-format csv",
            },
        }
        with open(manifest_path, "w", encoding="utf-8") as f:
            json.dump(manifest, f, ensure_ascii=False, indent=2)
        log(f"      Manifest: {manifest_path}")
        log("      Done.")
        return 0

    except Exception as exc:
        log(f"[ERROR] {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
