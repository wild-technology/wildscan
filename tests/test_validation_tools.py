"""Validation tools preserve datasets and unrelated workspace contents."""
from __future__ import annotations

import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import cv2
import numpy as np
import pytest

from scripts.validation import check_preprocessing


@pytest.fixture
def dataset(tmp_path):
    source = tmp_path / 'source'
    source.mkdir()
    image = np.random.default_rng(3).integers(96, 128, (128, 128, 3), dtype=np.uint8)
    assert cv2.imwrite(str(source / 'frame.png'), image)
    return source


@pytest.mark.parametrize('target', ['source', 'ancestor', 'descendant'])
def test_validation_refuses_overlapping_workspaces(tmp_path, dataset, target):
    work = {'source': dataset, 'ancestor': tmp_path,
            'descendant': dataset / 'output'}[target]
    original = (dataset / 'frame.png').read_bytes()
    with pytest.raises(SystemExit) as exc:
        check_preprocessing.main(['--dataset', str(dataset), '--work-dir', str(work)])
    assert exc.value.code == 2
    assert (dataset / 'frame.png').read_bytes() == original
    assert not (dataset / 'output').exists()
    assert not (tmp_path / 'input').exists()


def test_validation_preserves_nonempty_unmanaged_workspace(tmp_path, dataset):
    work = tmp_path / 'previous-work'
    work.mkdir()
    evidence = work / 'keep.txt'
    evidence.write_bytes(b'previous results')
    with pytest.raises(SystemExit):
        check_preprocessing.main(['--dataset', str(dataset), '--work-dir', str(work)])
    assert evidence.read_bytes() == b'previous results'
    assert sorted(p.name for p in work.iterdir()) == ['keep.txt']


def test_validation_refuses_file_as_workspace(tmp_path, dataset):
    work = tmp_path / 'keep.txt'
    work.write_bytes(b'keep')
    with pytest.raises(SystemExit):
        check_preprocessing.main(['--dataset', str(dataset), '--work-dir', str(work)])
    assert work.read_bytes() == b'keep'


@pytest.mark.parametrize('alias_location', ['dataset', 'work', 'work_parent', 'dataset_child'])
def test_validation_refuses_directory_aliases(tmp_path, dataset, alias_location):
    if os.name != 'nt':
        pytest.skip('Windows directory junction fixture')
    real = tmp_path / 'real'
    real.mkdir()
    link = (dataset / 'camera-alias' if alias_location == 'dataset_child'
            else tmp_path / 'alias')
    target = dataset if alias_location == 'dataset' else real
    result = subprocess.run(['cmd', '/c', 'mklink', '/J', str(link), str(target)],
                            capture_output=True, text=True)
    if result.returncode != 0:
        pytest.skip('Cannot create a directory junction here')
    source = link if alias_location == 'dataset' else dataset
    work = (link if alias_location == 'work' else link / 'output'
            if alias_location == 'work_parent' else tmp_path / 'output')
    original = (dataset / 'frame.png').read_bytes()
    try:
        with pytest.raises(SystemExit):
            check_preprocessing.main(['--dataset', str(source), '--work-dir', str(work)])
        assert (dataset / 'frame.png').read_bytes() == original
        assert list(real.iterdir()) == []
        assert not (tmp_path / 'output').exists()
    finally:
        link.rmdir()


def test_validation_runs_on_copies_and_keeps_source_unchanged(tmp_path, dataset, monkeypatch):
    monkeypatch.setattr('modules.preprocess_images.preprocess_images.ProcessPoolExecutor',
                        ThreadPoolExecutor)
    work = tmp_path / 'output'
    work.mkdir()
    original = (dataset / 'frame.png').read_bytes()
    assert check_preprocessing.main([
        '--dataset', str(dataset), '--work-dir', str(work)]) == 0
    assert (dataset / 'frame.png').read_bytes() == original
    assert sorted(p.name for p in dataset.iterdir()) == ['frame.png']
    assert (work / 'input' / 'frame.png').read_bytes() == original
    assert (work / 'preprocessed_images' / 'frame.png').read_bytes() != original


@pytest.mark.parametrize('entry', [
    'scripts/validation/check_preprocessing.py', 'tests/test_preprocess_module.py'])
def test_validation_help_is_available_at_canonical_and_legacy_paths(entry):
    repo = Path(__file__).resolve().parents[1]
    result = subprocess.run([sys.executable, str(repo / entry), '--help'],
                            cwd=repo, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert '--dataset' in result.stdout and '--work-dir' in result.stdout
