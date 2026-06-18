# Basel BPS Weather Matrix Policy

This workflow uses the already-generated Basel future weather matrix. Do not
regenerate CH2025 morphing, FRY/XMY selection, EPW solar decomposition, or DDY
design-condition generation when preparing BPS inputs.

## Weather Matrix

Expected annual EPWs:

```text
4 GWLs x 5 weather types = 20 EPWs
```

GWLs:

```text
gwl1.5
gwl2.0
gwl2.5
gwl3.0
```

Weather types per GWL:

```text
fry
seasonal_warm
peak_event
sustained_heat
nocturnal_heat
```

Expected DDYs:

```text
outputs/basel_weather_batch/gwl1.5/ddy/bas_gwl1.5.ddy
outputs/basel_weather_batch/gwl2.0/ddy/bas_gwl2.0.ddy
outputs/basel_weather_batch/gwl2.5/ddy/bas_gwl2.5.ddy
outputs/basel_weather_batch/gwl3.0/ddy/bas_gwl3.0.ddy
```

Each GWL has one paired DDY computed from the corresponding multi-year future
pool. Do not create one DDY per EPW.

## Pairing Rule

```text
gwl1.5 five EPWs -> bas_gwl1.5.ddy
gwl2.0 five EPWs -> bas_gwl2.0.ddy
gwl2.5 five EPWs -> bas_gwl2.5.ddy
gwl3.0 five EPWs -> bas_gwl3.0.ddy
```

The EPW header `DESIGN CONDITIONS` line is metadata only. The authoritative
EnergyPlus autosizing design days are the paired `.ddy` objects, which must be
injected into the IDF / Honeybee / OpenStudio workflow.

## Header-Updated EPWs

Create BPS-ready header copies with:

```bash
python3 legacy/scripts/10c_update_epw_headers.py
```

The script writes:

```text
outputs/basel_weather_batch/<gwl>/epw_header_updated/*_header_updated.epw
```

Policy:

- `LOCATION` remains the generated EPW/reference station metadata.
- `DESIGN CONDITIONS` is regenerated from the GWL multi-year design-condition
  summary JSON, not from the single FRY/XMY EPW year.
- `TYPICAL/EXTREME PERIODS` and `GROUND TEMPERATURES` are inherited from the
  Basel reference EPW for now.
- `COMMENTS 1` / `COMMENTS 2` document what is computed and what is inherited.
- Hourly EPW weather data are unchanged; the script validates matching hashes
  before and after.

## Smoke Tests Before Full Matrix

Run these before the full 20-run annual BPS matrix:

```text
Run 0: Reference Basel EPW + Reference Basel DDY + baseline IDF
Run 1: GWL2.0 FRY header-updated EPW + bas_gwl2.0.ddy + baseline IDF
Run 2: GWL2.0 peak_event header-updated EPW + bas_gwl2.0.ddy + baseline IDF
```

Acceptance checks:

- EnergyPlus completes.
- No fatal errors.
- The weather file is read correctly.
- The paired DDY is injected into the sizing workflow.
- Autosizing works normally.
- `eplusout.err` has no workflow-breaking severe warnings.
- Output CSV/SQL files can be parsed.
- Key outputs expected by the post-processing workflow are present.

Only after the three smoke tests pass should the full `4 x 5 = 20` annual BPS
matrix be run with the building model held constant.
