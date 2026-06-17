"""Import a numeric-named legacy script (e.g. ``09b_compute_design_conditions.py``)
as a Python module.

The numbered scripts cannot be imported with normal ``import`` syntax because
their filenames are not valid identifiers. This loader executes the file via
``importlib`` and caches the resulting module, so package code and tests can
reuse the validated functions without copying or rewriting them.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from typing import Dict

from .paths import legacy_script

_CACHE: Dict[str, ModuleType] = {}


def load_legacy_module(filename: str, alias: str | None = None) -> ModuleType:
    """Load and return the legacy script ``filename`` as a module.

    ``alias`` is the importable module name registered in ``sys.modules`` (must be
    a valid identifier; defaults to the filename stem with non-identifier chars
    replaced by underscores).
    """
    path: Path = legacy_script(filename)
    key = str(path)
    if key in _CACHE:
        return _CACHE[key]
    if alias is None:
        alias = "swf_legacy_" + "".join(c if c.isalnum() else "_" for c in path.stem)
    spec = importlib.util.spec_from_file_location(alias, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot create import spec for legacy script {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[alias] = module  # required so dataclasses/typing resolve correctly
    spec.loader.exec_module(module)
    _CACHE[key] = module
    return module
