# v4.1.6 XMY Metric Upgrade

This release upgrades the four fit-for-purpose XMY profile definitions from simple meteorological extremes to building-response-oriented meteorological stress metrics.

## Main algorithm changes

### FRY
No change. FRY remains FS rank-sum over `tas`, `hurs`, and `rsds`, with official CH2025 `tasmax` / `tasmin` used for tie-break.

### `seasonal_warm`
Changed from fixed-threshold summer CDH to CIBSE-style warm-season WCDH:

```text
WCDH = sum(max(Tas_future - Tc, 0)^2)
Tc = 0.33 * T_rm + 18.8
T_rm[i] = 0.8 * T_rm[i-1] + 0.2 * T_mean[i-1]
```

Implementation detail:
- `T_rm` is calculated from the full candidate year starting on Jan 1.
- WCDH is evaluated only over the configured warm season.
- Initial `T_rm` uses the candidate year's first daily mean temperature.

### `peak_event`
Changed from single-point `max_daily_tmax` to rolling-window CDH.

Default:
- Primary: max 3-day rolling CDH26
- Secondary: max 5-day rolling CDH26
- Tertiary: max daily Tmax / max hourly Tas

### `sustained_heat`
Changed from longest hot spell only to maximum single heatwave-event CDH.

Default:
- Hot day: daily Tmax >= 30°C
- Heatwave event: at least 3 consecutive hot days
- Score: maximum CDH26 of one detected heatwave event
- Separated heatwave events are not summed.
- If no heatwave event is detected, `heatwave_detected=false` and event score is 0.

### `nocturnal_heat`
Changed from tropical-night count to nighttime CDH above 20°C.

Default:
- Night window: 22:00–06:00 inclusive
- Primary: nighttime CDH20
- Secondary: tropical-night count
- Tertiary: longest tropical-night spell / nighttime CDH26

The nocturnal profile remains in the meteorological domain and does not use PET, SET*, or t-SET physiology.

## Updated files

- `07_select_xmy_profiles_from_candidate_pool_v4_1.py`
- `12_build_run_summary_v4.py`
- `run_batch_pipeline_v4_1.py`
- `frontend/app.js`

## New CLI options

`07_select_xmy_profiles_from_candidate_pool_v4_1.py` now supports:

```bash
--cdh-base-temperature-c 26
--wcdh-alpha 0.8
--peak-window-days 3
--peak-secondary-window-days 5
--min-heatwave-duration-days 3
--night-start-hour 22
--night-end-hour 6
--night-base-temperature-c 20
```

The batch runner exposes matching `--xmy-*` options.
