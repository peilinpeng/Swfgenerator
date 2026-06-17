"""Path resolution that does not depend on the current working directory.

The project root is located by walking up from this file until a ``pyproject.toml``
marker is found. All pipeline paths are derived from the project root, the loaded
config's ``output_root``, or packaged resources — never from ``os.getcwd()``.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path


class ProjectLayoutError(RuntimeError):
    pass


@lru_cache(maxsize=1)
def project_root() -> Path:
    """Return the repository root (the directory containing ``pyproject.toml``)."""
    here = Path(__file__).resolve()
    for parent in [here, *here.parents]:
        if (parent / "pyproject.toml").is_file():
            return parent
    raise ProjectLayoutError(
        "Could not locate project root (no pyproject.toml found above "
        f"{here}). Install the package with `pip install -e .`."
    )


def package_dir() -> Path:
    return Path(__file__).resolve().parent


def resources_dir() -> Path:
    return package_dir() / "resources"


def legacy_scripts_dir() -> Path:
    """Directory holding the validated numbered legacy scripts."""
    d = project_root() / "legacy" / "scripts"
    if not d.is_dir():
        raise ProjectLayoutError(
            f"Legacy scripts directory not found: {d}. The orchestrator invokes the "
            "validated numbered scripts from there."
        )
    return d


def legacy_script(name: str) -> Path:
    p = legacy_scripts_dir() / name
    if not p.is_file():
        raise ProjectLayoutError(f"Legacy script not found: {p}")
    return p


def bundled_reference_ddy(station_key: str) -> Path | None:
    """Return a packaged reference .ddy for a station key, or None if absent.

    Looks under ``resources/reference/<station_key>/*.ddy`` first, then the
    versioned ``data/reference/<station_key>/*.ddy`` in the repo.
    """
    for base in (resources_dir() / "reference", project_root() / "data" / "reference"):
        d = base / station_key
        if d.is_dir():
            ddys = sorted(d.glob("*.ddy"))
            if ddys:
                return ddys[0]
    return None
