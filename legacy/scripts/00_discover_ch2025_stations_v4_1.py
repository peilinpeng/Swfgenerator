#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
00_discover_ch2025_stations_v4_1.py

Discover available CH2025 DAILY-LOCAL station items from the official STAC API
and write a station catalog for batch runs and the frontend station selector.
"""
from __future__ import annotations

import argparse
import json
import urllib.request
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

STAC_ROOT = "https://data.geo.admin.ch/api/stac/v1"
DEFAULT_COLLECTION_ID = "ch.meteoschweiz.ogd-climate-scenarios-ch2025"


def fetch_json(url: str, timeout: int = 60) -> Dict[str, Any]:
    req = urllib.request.Request(url, headers={"User-Agent": "ch2025-station-discoverer/0.1", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def iter_paginated(url: str, array_key: str) -> Iterable[Dict[str, Any]]:
    next_url = url
    while next_url:
        payload = fetch_json(next_url)
        for row in payload.get(array_key, []):
            yield row
        next_url = None
        for link in payload.get("links", []):
            if link.get("rel") == "next":
                next_url = link.get("href")
                break


def asset_variables(item: Dict[str, Any]) -> List[str]:
    vars_found = set()
    for key in item.get("assets", {}).keys():
        # filename pattern: ogd-climate-scenarios-ch2025_sma_tas_gwl2.0.csv
        parts = str(key).replace(".zip", "").replace(".csv", "").split("_")
        if len(parts) >= 4:
            vars_found.add(parts[-2])
    return sorted(vars_found)


def item_to_station(item: Dict[str, Any]) -> Dict[str, Any]:
    props = item.get("properties", {}) or {}
    sid = str(item.get("id", "")).lower()
    name = props.get("title") or props.get("name") or item.get("title") or sid.upper()
    geom = item.get("geometry") or {}
    coords = geom.get("coordinates") if geom.get("type") == "Point" else None
    lon = coords[0] if isinstance(coords, list) and len(coords) >= 2 else None
    lat = coords[1] if isinstance(coords, list) and len(coords) >= 2 else None
    return {
        "id": sid.upper(),
        "value": sid.upper(),
        "code": sid,
        "name": str(name),
        "label": f"{str(name)} · {sid.upper()}" if str(name).lower() != sid else sid.upper(),
        "latitude": lat,
        "longitude": lon,
        "available_variables": asset_variables(item),
    }


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Discover CH2025 station catalog from STAC.")
    p.add_argument("--collection-id", default=DEFAULT_COLLECTION_ID)
    p.add_argument("--output", default="frontend/data/stations_catalog.json")
    p.add_argument("--limit", type=int, default=0, help="Optional limit for quick tests; 0 means all.")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    try:
        items_url = f"{STAC_ROOT}/collections/{args.collection_id}/items"
        rows: List[Dict[str, Any]] = []
        for i, item in enumerate(iter_paginated(items_url, "features"), start=1):
            rows.append(item_to_station(item))
            if args.limit and i >= args.limit:
                break
        rows = sorted(rows, key=lambda x: (x.get("name") or "", x.get("id") or ""))
        payload = {
            "source": "data.geo.admin.ch STAC",
            "collection_id": args.collection_id,
            "station_count": len(rows),
            "stations": rows,
        }
        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        with open(out, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)
        print(f"Wrote station catalog: {out} ({len(rows)} stations)")
        return 0
    except Exception as exc:
        print(f"[ERROR] {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
