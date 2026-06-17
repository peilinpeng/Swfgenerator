"""swfgenerator — future Swiss weather file generation engine.

A research-grade computational package that wraps the validated CH2025/MeteoSwiss
pipeline (FRY/XMY selection, reference-based EPW morphing, and ASHRAE-style design
conditions / DDY generation) behind a config-driven CLI.

The validated numbered scripts live under ``legacy/scripts`` and are invoked as
subprocess stages by :mod:`swfgenerator.orchestrator`; their algorithms are not
reimplemented here.
"""
from __future__ import annotations

__version__ = "0.1.0"

__all__ = ["__version__"]
