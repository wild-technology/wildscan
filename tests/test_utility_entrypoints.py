"""Existing utility commands retain their imports and Windows worker behavior."""
from __future__ import annotations

import importlib
import multiprocessing
import subprocess
import sys
from pathlib import Path

import pytest

from tests.test_georeference_image_workers import LocalPool


@pytest.mark.parametrize("entry", [
    "georeference_survey.py", "geoall.py",
    "poses_to_flight_log.py", "poses2flightlog.py",
    "decimate_images.py", "decimator.py",
])
def test_utility_help_does_not_prompt_or_write_settings(tmp_path, entry):
    repo = Path(__file__).resolve().parents[1]
    result = subprocess.run([sys.executable, str(repo / entry), "--help"],
                            cwd=tmp_path, stdin=subprocess.DEVNULL,
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert "usage:" in result.stdout
    assert not list(tmp_path.iterdir())


def test_legacy_module_override_reaches_canonical_copy_worker(tmp_path, monkeypatch):
    legacy = importlib.import_module("geoall")
    canonical = importlib.import_module("georeference_survey")
    monkeypatch.setattr(legacy, "Pool", LocalPool)
    source = tmp_path / "source.jpg"
    source.write_bytes(b"source image")
    image = {"FULL_PATH": str(source), "FILENAME": "source.jpg",
             "CAMERA_TYPE": "CamLower"}
    output = tmp_path / "output"
    copied, failed, _counts = canonical.copy_matched_images(
        [image], "survey", str(output))
    assert (copied, failed) == (1, 0)
    assert (output / "survey" / "CamLower" / "source.jpg").read_bytes() == source.read_bytes()


def test_legacy_copy_worker_runs_in_a_spawned_process(tmp_path):
    legacy = importlib.import_module("geoall")
    source = tmp_path / "source.jpg"
    source.write_bytes(b"source image")
    image = {"FULL_PATH": str(source), "FILENAME": "source.jpg",
             "CAMERA_TYPE": "CamLower"}
    output = tmp_path / "output"
    with multiprocessing.get_context("spawn").Pool(1) as pool:
        result = pool.apply_async(legacy._copy_single_image,
                                  ((image, "survey", str(output)),)).get(timeout=30)
    assert result["success"] is True and result["skipped"] is False
    assert (output / "survey" / "CamLower" / "source.jpg").read_bytes() == source.read_bytes()
