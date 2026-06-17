"""Config loading + station-agnostic core checks."""
import dataclasses

import pytest

from swfgenerator.config import ConfigError, StationConfig, load_config
from swfgenerator.paths import project_root


EXAMPLE = "configs/examples/zurich_gwl2.yaml"


def test_example_config_loads():
    cfg = load_config(project_root() / EXAMPLE)
    assert cfg.station.code == "sma"
    assert cfg.station.elevation_m == 555.6
    assert "gwl2.0" in cfg.gwls
    assert cfg.data_source_mode == "api"
    assert cfg.allow_reference_fallback is False
    # Production output must never be test_outputs.
    assert cfg.output_root.name != "test_outputs"


def test_core_station_has_no_baked_in_defaults():
    # Station-identifying fields must be REQUIRED (no Zurich/SMA defaults).
    required = {"code", "name", "latitude", "longitude", "elevation_m", "timezone_utc_offset"}
    for f in dataclasses.fields(StationConfig):
        if f.name in required:
            assert f.default is dataclasses.MISSING and f.default_factory is dataclasses.MISSING, (
                f"StationConfig.{f.name} must not have a default")


def test_missing_station_block_is_rejected(tmp_path):
    p = tmp_path / "nostation.yaml"
    p.write_text("scenario: {gwls: [gwl2.0]}\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(p)


def test_test_outputs_rejected(tmp_path):
    cfg_text = (
        "station: {code: x, name: X, latitude: 1, longitude: 1, elevation_m: 1, "
        "timezone_utc_offset: 1}\n"
        "scenario: {gwls: [gwl2.0]}\n"
        "output: {root: test_outputs}\n"
    )
    p = tmp_path / "bad.yaml"
    p.write_text(cfg_text, encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(p)
