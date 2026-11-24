"""入口参数：client / month 只能落在 output/ 内（README §11）。"""

import argparse

import pytest

run = pytest.importorskip("scripts.run")


def test_rejects_path_traversal_client(capsys):
    with pytest.raises(SystemExit) as excinfo:
        run.main(["--client", "../../tmp", "--month", "2026-10"])

    assert excinfo.value.code == 2
    assert "--client" in capsys.readouterr().err


def test_rejects_invalid_month(capsys):
    with pytest.raises(SystemExit) as excinfo:
        run.main(["--client", "shenhua", "--month", "2026-13"])

    assert excinfo.value.code == 2
    assert "--month" in capsys.readouterr().err


def test_valid_arguments_stay_within_output():
    parser = argparse.ArgumentParser()
    out_dir = run._validated_out_dir(parser, "shenhua", "2026-10")

    assert out_dir == (run.OUTPUT_DIR / "shenhua" / "2026-10").resolve()
