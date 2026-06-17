# v4.1.3 Cloud-cover / sky-cover auxiliary layer

This update adds an explicit cloud-cover interpolation layer for EPW Total Sky Cover.

## Why

SwissMetNet automatic hourly station data (`ogd-smn`) often does not contain total cloud cover. Cloud cover is therefore handled as a separate MeteoSwiss observation layer rather than as a required hourly automatic-station field.

## New scripts

- `04b_fetch_meteoswiss_cloudcover_obs_v4.py`  
  Fetches MeteoSwiss meteorological visual observation files from `ch.meteoschweiz.ogd-obs` through the STAC API.

- `04c_parse_cloudcover_observations_v4.py`  
  Parses downloaded cloud-cover observation CSVs into a standardized table.

- `04d_merge_cloudcover_to_hourly_obs_v4.py`  
  Interpolates cloud-cover observations onto the hourly observation backbone and creates:
  - `cloudcover_interpolated`
  - `total_sky_cover_tenths`
  - `opaque_sky_cover_tenths`
  - `skycover_source`
  - `skycover_interpolation_method`

## Pipeline integration

`run_batch_pipeline_v4_1.py` now has:

```bash
--cloudcover-mode auto|required|skip
--cloudcover-granularity d|h|m|y
```

Default is `--cloudcover-mode auto`: try to fetch/parse/interpolate cloud cover; if unavailable for a station, continue with documented fallback sky-cover handling.

## EPW completion

`08_complete_epw_fields_v4.py` now prefers precomputed `total_sky_cover_tenths` and `opaque_sky_cover_tenths`. If absent, it falls back to converting a raw `cloudcover` column, and finally to EPW missing value `99`.

## Batch output fix

`10_compare_weather_files_v4.py` now supports an explicit `--output` path, so batch runs no longer overwrite `weather_file_comparison_summary_v4.csv` across stations/GWLs.

## Station metadata fix

`run_batch_pipeline_v4_1.py` now attempts to auto-refresh `frontend/data/stations_catalog.json` and create `station_metadata/<station>.json` when possible.
