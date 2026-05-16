#!/usr/bin/env bash
set -euo pipefail

# Example multi-station/multi-GWL batch run. Edit station/GWL lists as needed.
# This will automatically fetch CH2025 and MeteoSwiss hourly data.

python3 run_batch_pipeline_v4_1.py \
  --stations sma,bas,ber,lug,gve \
  --gwls gwl1.5,gwl2.0,gwl3.0 \
  --profiles seasonal_warm,peak_event,sustained_heat,nocturnal_heat \
  --continue-on-error
