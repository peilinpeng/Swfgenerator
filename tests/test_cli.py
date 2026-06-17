"""CLI smoke tests: --help works and a --dry-run stage builds commands."""
from swfgenerator.cli import main
from swfgenerator.paths import project_root


EXAMPLE = "configs/examples/zurich_gwl2.yaml"


def test_help_exits_zero():
    # argparse --help raises SystemExit(0).
    import pytest
    with pytest.raises(SystemExit) as exc:
        main(["--help"])
    assert exc.value.code == 0


def test_design_conditions_dry_run(capsys):
    cfg = str(project_root() / EXAMPLE)
    rc = main(["design-conditions", "--config", cfg, "--dry-run"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "09b_compute_design_conditions.py" in out
    assert "--mode future" in out
    # Reference DDY is passed (no silent fallback path).
    assert "--reference-ddy" in out


def test_full_dry_run(capsys):
    cfg = str(project_root() / EXAMPLE)
    rc = main(["full", "--config", cfg, "--dry-run"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "run_batch_pipeline_v4_1.py" in out
