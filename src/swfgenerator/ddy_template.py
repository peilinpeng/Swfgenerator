"""Adapter exposing the reference-DDY template patcher.

Loads ``legacy/scripts/ddy_template.py`` (the parser/classifier/patcher that
reproduces a reference ``.ddy`` structure while overwriting only computed
design-condition fields) without copying any logic. See that module's docstring.
"""
from __future__ import annotations

from types import ModuleType

from .legacy_loader import load_legacy_module


def module() -> ModuleType:
    return load_legacy_module("ddy_template.py", alias="swf_ddy_template")
