"""Utility commands print their help without prompting or writing settings."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize("entry", ["poses_to_flight_log.py", "decimate_images.py"])
def test_utility_help_does_not_prompt_or_write_settings(tmp_path, entry):
    repo = Path(__file__).resolve().parents[1]
    result = subprocess.run([sys.executable, str(repo / entry), "--help"],
                            cwd=tmp_path, stdin=subprocess.DEVNULL,
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert "usage:" in result.stdout
    assert not list(tmp_path.iterdir())
