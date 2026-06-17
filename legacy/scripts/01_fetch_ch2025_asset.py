#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
01_fetch_ch2025_asset.py

Purpose:
    Discover and download one CH2025 DAILY-LOCAL asset from the
    data.geo.admin.ch STAC API into a local cache.

Example:
    python 01_fetch_ch2025_asset.py --station sma --variable tas --state gwl2.0 --fmt csv

Notes:
    - Uses only Python standard library.
    - Tries to discover the CH2025 DAILY-LOCAL collection automatically.
    - If automatic discovery fails, you can pass --collection-id manually.
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
DEFAULT_TIMEOUT = 60


def log(msg: str) -> None:
    print(msg, flush=True)


def fetch_json(url: str, timeout: int = DEFAULT_TIMEOUT) -> Dict[str, Any]:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "ch2025-fetcher/0.1",
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        if resp.status != 200:
            raise RuntimeError(f"HTTP {resp.status} while requesting {url}")
        data = resp.read().decode("utf-8")
    return json.loads(data)


def download_file(url: str, dest: Path, timeout: int = DEFAULT_TIMEOUT) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)

    req = urllib.request.Request(
        url,
        headers={"User-Agent": "ch2025-fetcher/0.1"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        if resp.status != 200:
            raise RuntimeError(f"HTTP {resp.status} while downloading {url}")
        with open(dest, "wb") as f:
            f.write(resp.read())


def normalize_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip().lower()


def item_links_get(obj: Dict[str, Any], rel: str) -> Optional[str]:
    for link in obj.get("links", []):
        if link.get("rel") == rel:
            return link.get("href")
    return None


def iter_paginated(url: str, array_key: str) -> Iterable[Dict[str, Any]]:
    """
    Follow STAC pagination using 'next' links.
    Works for:
      - /collections  -> array_key='collections'
      - /items        -> array_key='features'
    """
    next_url = url
    while next_url:
        payload = fetch_json(next_url)
        rows = payload.get(array_key, [])
        if not isinstance(rows, list):
            raise RuntimeError(f"Unexpected response structure at {next_url}")
        for row in rows:
            yield row

        next_link = None
        for link in payload.get("links", []):
            if link.get("rel") == "next":
                next_link = link.get("href")
                break
        next_url = next_link


def discover_collection(preferred_collection_id: Optional[str] = None) -> Dict[str, Any]:
    """
    Discover the CH2025 DAILY-LOCAL collection automatically.
    If preferred_collection_id is given, use it directly.
    """
    if preferred_collection_id:
        url = f"{STAC_ROOT}/collections/{urllib.parse.quote(preferred_collection_id, safe='')}"
        return fetch_json(url)

    collections_url = f"{STAC_ROOT}/collections"
    candidates: List[Tuple[int, Dict[str, Any]]] = []

    for coll in iter_paginated(collections_url, "collections"):
        cid = normalize_text(coll.get("id"))
        title = normalize_text(coll.get("title"))
        desc = normalize_text(coll.get("description"))

        text = " ".join([cid, title, desc])

        score = 0
        if "ch2025" in text:
            score += 10
        if "daily-local" in text or "daily local" in text:
            score += 10
        if "station" in text or "per station" in text:
            score += 5
        if "climate scenarios" in text:
            score += 3

        if score > 0:
            candidates.append((score, coll))

    if not candidates:
        raise RuntimeError(
            "Could not auto-discover the CH2025 DAILY-LOCAL collection. "
            "Try passing --collection-id manually."
        )

    candidates.sort(key=lambda x: x[0], reverse=True)
    best = candidates[0][1]
    return best


def discover_station_item(collection: Dict[str, Any], station: str) -> Dict[str, Any]:
    """
    Find the station item, e.g. SMA.
    """
    station_norm = normalize_text(station)
    items_url = item_links_get(collection, "items")
    if not items_url:
        collection_id = collection.get("id")
        if not collection_id:
            raise RuntimeError("Collection has neither 'items' link nor 'id'.")
        items_url = f"{STAC_ROOT}/collections/{urllib.parse.quote(collection_id, safe='')}/items"

    matches: List[Tuple[int, Dict[str, Any]]] = []

    for item in iter_paginated(items_url, "features"):
        iid = normalize_text(item.get("id"))
        title = normalize_text(item.get("title"))
        desc = normalize_text(item.get("description"))

        text = " ".join([iid, title, desc])

        score = 0
        if station_norm == iid:
            score += 20
        if f"({station_norm})" in text:
            score += 15
        if re.search(rf"\b{re.escape(station_norm)}\b", text):
            score += 10
        if station_norm in text:
            score += 5

        if score > 0:
            matches.append((score, item))

    if not matches:
        raise RuntimeError(
            f"Could not find a station item matching '{station}'. "
            f"Collection checked: {collection.get('id')}"
        )

    matches.sort(key=lambda x: x[0], reverse=True)
    return matches[0][1]


def asset_format_matches(asset_key: str, asset: Dict[str, Any], fmt: str) -> bool:
    fmt = fmt.lower()
    key = normalize_text(asset_key)
    title = normalize_text(asset.get("title"))
    href = normalize_text(asset.get("href"))
    media_type = normalize_text(asset.get("type"))

    checks = [key, title, href, media_type]
    return any(fmt in c for c in checks)


def choose_asset(
    item: Dict[str, Any],
    variable: str,
    state: str,
    fmt: str,
) -> Tuple[str, Dict[str, Any]]:
    """
    Choose the best asset from the station item.
    Example target:
      variable = tas
      state    = gwl2.0 or ref91-20
      fmt      = csv
    """
    variable_norm = normalize_text(variable)
    state_norm = normalize_text(state)
    fmt_norm = normalize_text(fmt)

    assets = item.get("assets", {})
    if not isinstance(assets, dict) or not assets:
        raise RuntimeError("Item has no assets.")

    candidates: List[Tuple[int, str, Dict[str, Any]]] = []

    for asset_key, asset in assets.items():
        key = normalize_text(asset_key)
        title = normalize_text(asset.get("title"))
        href = normalize_text(asset.get("href"))
        media_type = normalize_text(asset.get("type"))
        text = " ".join([key, title, href, media_type])

        score = 0

        if variable_norm in text:
            score += 10
        if state_norm in text:
            score += 10
        if asset_format_matches(asset_key, asset, fmt_norm):
            score += 10

        # Prefer exact token-like hits in filenames
        if f"_{variable_norm}_" in key or f"_{variable_norm}_" in href:
            score += 5
        if f"_{state_norm}" in key or f"_{state_norm}" in href:
            score += 5

        # Prefer direct file links over generic asset names
        if href.endswith(f".{fmt_norm}"):
            score += 3

        if score > 0:
            candidates.append((score, asset_key, asset))

    if not candidates:
        available = ", ".join(assets.keys())
        raise RuntimeError(
            f"No asset matched variable='{variable}', state='{state}', fmt='{fmt}'. "
            f"Available asset keys: {available}"
        )

    candidates.sort(key=lambda x: x[0], reverse=True)
    _, best_key, best_asset = candidates[0]
    return best_key, best_asset


def safe_filename_from_asset(asset_key: str, asset: Dict[str, Any], fallback_fmt: str) -> str:
    href = asset.get("href")
    if href:
        path = urllib.parse.urlparse(href).path
        name = os.path.basename(path)
        if name:
            return name
    key = asset_key.replace("/", "_")
    return f"{key}.{fallback_fmt}"


def save_metadata_json(dest_file: Path, metadata: Dict[str, Any]) -> None:
    meta_path = dest_file.with_suffix(dest_file.suffix + ".meta.json")
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(metadata, f, ensure_ascii=False, indent=2)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fetch one CH2025 DAILY-LOCAL asset from the STAC API."
    )
    parser.add_argument("--station", required=True, help="Station code, e.g. sma")
    parser.add_argument("--variable", required=True, help="Variable, e.g. tas, hurs, rsds, sfcWind")
    parser.add_argument("--state", required=True, help="State, e.g. ref91-20 or gwl2.0")
    parser.add_argument("--fmt", default="csv", choices=["csv", "zip"], help="Desired file format")
    parser.add_argument(
        "--collection-id",
        default=None,
        help="Optional manual collection id override if auto-discovery fails",
    )
    parser.add_argument(
        "--cache-dir",
        default="./cache/ch2025",
        help="Local cache root directory",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Force re-download even if cached file exists",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Discover and print the asset, but do not download it",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    station = args.station.strip().lower()
    variable = args.variable.strip()
    state = args.state.strip().lower()
    fmt = args.fmt.strip().lower()

    try:
        log(f"[1/4] Discovering collection for CH2025 DAILY-LOCAL ...")
        collection = discover_collection(args.collection_id)
        collection_id = collection.get("id", "<unknown>")
        collection_title = collection.get("title", "<no title>")
        log(f"      Collection: {collection_id}")
        log(f"      Title     : {collection_title}")

        log(f"[2/4] Finding station item for '{station}' ...")
        item = discover_station_item(collection, station)
        item_id = item.get("id", "<unknown>")
        item_title = item.get("title", "<no title>")
        log(f"      Item ID   : {item_id}")
        log(f"      Item title: {item_title}")

        log(f"[3/4] Selecting asset for variable='{variable}', state='{state}', fmt='{fmt}' ...")
        asset_key, asset = choose_asset(item, variable, state, fmt)
        href = asset.get("href")
        if not href:
            raise RuntimeError(f"Chosen asset '{asset_key}' has no href.")

        filename = safe_filename_from_asset(asset_key, asset, fmt)
        dest = Path(args.cache_dir) / station / variable / state / filename

        log(f"      Asset key : {asset_key}")
        log(f"      HREF      : {href}")
        log(f"      Cache path: {dest}")

        metadata = {
            "stac_root": STAC_ROOT,
            "collection_id": collection_id,
            "collection_title": collection_title,
            "item_id": item_id,
            "item_title": item_title,
            "asset_key": asset_key,
            "asset": asset,
            "requested_station": station,
            "requested_variable": variable,
            "requested_state": state,
            "requested_fmt": fmt,
            "cache_path": str(dest),
        }

        if args.dry_run:
            log("[4/4] Dry run enabled. No file downloaded.")
            return 0

        if dest.exists() and not args.force:
            log("[4/4] Cached file already exists. Reusing local copy.")
            save_metadata_json(dest, metadata)
            log("      Done.")
            return 0

        log(f"[4/4] Downloading file ...")
        download_file(href, dest)
        save_metadata_json(dest, metadata)
        log("      Download complete.")
        log("      Metadata sidecar written next to file.")
        return 0

    except Exception as exc:
        log(f"[ERROR] {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())