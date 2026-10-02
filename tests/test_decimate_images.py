"""Percentage thinning handles small image sets using its existing rounding."""
from pathlib import Path
import subprocess
import sys

import pytest

import decimate_images
from decimate_images import select_images_to_copy


@pytest.mark.parametrize('count, percent, expected', [
    (0, 10, 0), (1, 10, 0), (4, 10, 0), (5, 10, 0), (6, 10, 1),
    (10, 50, 5), (3, 100, 3),
])
def test_selection_uses_rounded_count_without_dividing_by_zero(count, percent, expected):
    images = [Path(f'image_{index}.jpg') for index in range(count)]
    selected = select_images_to_copy(images, percent)
    assert len(selected) == expected
    assert len(set(selected)) == len(selected)
    assert all(image in images for image in selected)


@pytest.mark.parametrize('argument, exit_code', [('--help', 0), ('--unknown', 2)])
def test_cli_handles_help_and_unknown_arguments_without_prompting(tmp_path, argument,
                                                                exit_code):
    result = subprocess.run([sys.executable, decimate_images.__file__, argument], input='',
                            cwd=tmp_path, capture_output=True, text=True, timeout=10)
    assert result.returncode == exit_code
    assert 'usage:' in result.stdout + result.stderr
    assert 'Enter path' not in result.stdout + result.stderr
    assert list(tmp_path.iterdir()) == []


def test_empty_source_returns_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(decimate_images, 'SettingsStore', lambda: object())
    monkeypatch.setattr(decimate_images, 'get_valid_directory', lambda *args, **kwargs: tmp_path)
    assert decimate_images.main([]) == 1
