"""Thin adapter exposing the validated ``09b_compute_design_conditions`` functions.

This module does NOT reimplement any algorithm. It loads the legacy script as a
module and re-exports the functions that package code and tests rely on
(Gumbel, psychrometrics round-trip, reference-DDY parsing, the design-day name
filter). Behaviour is identical to running the legacy script directly.
"""
from __future__ import annotations

from typing import Any

from .legacy_loader import load_legacy_module

LEGACY_FILENAME = "09b_compute_design_conditions.py"


def _module():
    return load_legacy_module(LEGACY_FILENAME, alias="swf_design_conditions")


def run_gumbel_unit_test(*args: Any, **kwargs: Any):
    return _module().run_gumbel_unit_test(*args, **kwargs)


def gumbel_return_value(*args: Any, **kwargs: Any):
    return _module().gumbel_return_value(*args, **kwargs)


def psychro_roundtrip_check(*args: Any, **kwargs: Any):
    return _module().psychro_roundtrip_check(*args, **kwargs)


def parse_reference_ddy(*args: Any, **kwargs: Any):
    return _module().parse_reference_ddy(*args, **kwargs)


def name_survives_honeybee_filter(*args: Any, **kwargs: Any):
    return _module().name_survives_honeybee_filter(*args, **kwargs)
