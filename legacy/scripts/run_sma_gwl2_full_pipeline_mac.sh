#!/usr/bin/env bash
set -euo pipefail

# Compatibility wrapper for the new v4.1 batch runner.
# Run from the project root after activating the virtual environment.

python3 run_batch_pipeline_v4_1.py \
  --stations sma \
  --gwls gwl2.0 \
  --profiles seasonal_warm,peak_event,sustained_heat,nocturnal_heat
