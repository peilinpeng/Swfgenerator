# v4.1.5 — Satellite CFC + Wehrli benchmark + frontend chart polish

## Main changes

### 1. Satellite CFC sky-cover layer
Added two new scripts:

- `04e_fetch_meteoswiss_satellite_cfc_v4.py`
  - Discovers and downloads MeteoSwiss satellite-derived Cloud Fractional Cover (CFC) NetCDF assets through the FSDI STAC collection `ch.meteoschweiz.ogd-satellite-derived-grid`.
  - Writes a manifest and exits with code `2` if no matching CFC assets are currently available for the requested period/frequency.

- `04f_extract_cfc_to_station_hourly_v4.py`
  - Extracts the nearest grid cell to the station latitude/longitude from CFC NetCDF files.
  - Converts CFC to a standardized cloud-cover table compatible with `04d_merge_cloudcover_to_hourly_obs_v4.py`.
  - Requires optional NetCDF dependencies: `xarray netCDF4`.

The batch runner now attempts cloud cover in this order:

1. MeteoSwiss visual observations at the target station.
2. Satellite-derived CFC by station lat/lon.
3. Documented fallback if `--cloudcover-mode auto` is used.

If `--cloudcover-mode required` is used, the run fails unless either visual cloud cover or satellite CFC succeeds.

### 2. Wehrli / SIA 2028 external benchmark for frontend comparison
Added:

- `13_add_external_wehrli_benchmark.py`

This downloads and normalizes a Wehrli/SIA 2028 benchmark package from the supplied URL, with default:

```bash
https://s.geo.admin.ch/94e9d38450
```

It is intended only for weather-level frontend comparison. It is not treated as an EPW and is not part of the CH2025-generated XMY set.

Batch usage:

```bash
python3 run_batch_pipeline_v4_1.py \
  --stations sma \
  --gwls gwl2.0 \
  --profiles seasonal_warm,peak_event,sustained_heat,nocturnal_heat \
  --cloudcover-mode auto \
  --include-wehrli-benchmark
```

### 3. Frontend chart improvements

- Monthly variable charts now support more than two series, e.g. Reference 1991–2020, FRY, and external Wehrli benchmark.
- Bar and line charts include hover tooltips for all plotted values.
- CDF chart includes hover tooltips for target, selected candidate, and shortlisted alternatives.
- Variable charts, XMY statistic chart, carpet plot, and simulation comparison include legends where multiple colours are used.
- The Swiss Confederation-style banner remains removed; the interface is presented as a research tool, not an official website.

## Notes on CFC archive availability

MeteoSwiss documentation describes satellite-based Cloud Fractional Cover as gridded NetCDF data available via the Open Data STAC API. However, archive exposure through the public STAC can depend on current publication status. In `auto` mode, the pipeline therefore falls back gracefully when CFC assets are not available.
