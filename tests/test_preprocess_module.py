"""Offline regression tests for preprocessing and output provenance."""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import cv2
import numpy as np

from module_base.parameter import Parameter
from modules.preprocess_images.preprocess_images import PreprocessImages, build_transform


class _SerialPool:
    def __init__(self, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def map(self, function, jobs, **kwargs):
        return map(function, jobs)


def _synthetic_module(tmp_path, monkeypatch=None):
    if monkeypatch is not None:
        monkeypatch.setattr('modules.preprocess_images.preprocess_images.ProcessPoolExecutor',
                            _SerialPool)
    source = tmp_path / 'source'
    source.mkdir()
    image = np.random.default_rng(2).integers(96, 128, (128, 128, 3), dtype=np.uint8)
    cv2.imwrite(str(source / 'frame.png'), image)
    module = PreprocessImages(logging.getLogger('preprocess-test'))
    module.params = module.get_parameters()
    module.params['pre_input_image_dir'].set_value(str(source))
    module.params['pre_workers'].set_value(1)
    module.params['output_dir'] = Parameter('Out', 'o', 'output_dir', str,
                                           str(tmp_path / 'workspace'))
    return module, source


def test_matching_preprocessing_provenance_is_reused(tmp_path, monkeypatch):
    module, _source = _synthetic_module(tmp_path, monkeypatch)
    assert module.run()['Processed'] == 1
    result = module.run()
    assert result['Success'] and result['Processed'] == 0
    assert result['Skipped (already done)'] == 1


def test_preprocessing_worker_runs_in_a_process_pool(tmp_path):
    module, _source = _synthetic_module(tmp_path)
    result = module.run()
    assert result['Success'] and result['Processed'] == 1 and result['Failed'] == 0


def test_changed_transform_reprocesses_existing_outputs(tmp_path, monkeypatch):
    module, source = _synthetic_module(tmp_path, monkeypatch)
    assert module.run()['Success']
    module.params['pre_clahe_clip'].set_value(4.0)
    result = module.run()
    assert result['Success'] and result['Processed'] == 1
    expected = build_transform({'clahe_clip': 4.0, 'clahe_tile': 8})(
        cv2.imread(str(source / 'frame.png')))
    actual = cv2.imread(os.path.join(result['Output Directory'], 'frame.png'))
    assert np.array_equal(actual, expected)


def test_changed_source_reprocesses_existing_outputs(tmp_path, monkeypatch):
    module, source = _synthetic_module(tmp_path, monkeypatch)
    assert module.run()['Success']
    cv2.imwrite(str(source / 'frame.png'), np.zeros((128, 128, 3), dtype=np.uint8))
    result = module.run()
    assert result['Success'] and result['Processed'] == 1


def test_damaged_preprocessed_output_is_regenerated(tmp_path, monkeypatch):
    from pathlib import Path
    module, _source = _synthetic_module(tmp_path, monkeypatch)
    first = module.run()
    output = Path(first['Output Directory']) / 'frame.png'
    output.write_bytes(b'')
    result = module.run()
    assert result['Success'] and result['Processed'] == 1
    assert cv2.imread(str(output)) is not None


def test_failed_atomic_write_preserves_previous_destination(tmp_path, monkeypatch):
    from modules.preprocess_images.preprocess_images import _process_one
    source = tmp_path / 'input.jpg'
    cv2.imwrite(str(source), np.zeros((8, 8, 3), dtype=np.uint8))
    output = tmp_path / 'output.jpg'
    output.write_bytes(b'previous output')

    def failed_write(path, *args):
        with open(path, 'wb') as stream:
            stream.write(b'incomplete')
        return False

    monkeypatch.setattr(cv2, 'imwrite', failed_write)
    assert _process_one((str(source), str(output), 2.0, 8, False)) == str(source)
    assert output.read_bytes() == b'previous output'
    assert set(p.name for p in tmp_path.iterdir()) == {'input.jpg', 'output.jpg'}


def test_preprocessing_with_no_successful_images_fails(tmp_path, monkeypatch):
    module, source = _synthetic_module(tmp_path, monkeypatch)
    (source / 'frame.png').write_bytes(b'not an image')
    result = module.run()
    assert not result['Success']
    assert result['Failed'] == 1 and result['Processed'] == 0


def test_removed_source_images_are_not_left_for_batching(tmp_path, monkeypatch):
    import json
    from pathlib import Path
    from modules.preprocess_images.preprocess_images import MANIFEST_NAME
    module, source = _synthetic_module(tmp_path, monkeypatch)
    assert module.run()['Success']
    (source / 'frame.png').unlink()
    result = module.run()
    assert not result['Success'] and 'absent from the input' in result['Failure']
    marker = Path(module.params['output_dir'].get_value()) / MANIFEST_NAME
    assert json.loads(marker.read_text())['status'] == 'failed'


def test_overlapping_preprocessing_trees_are_refused(tmp_path, monkeypatch):
    module, source = _synthetic_module(tmp_path, monkeypatch)
    module.params['output_dir'].set_value(str(source))
    result = module.run()
    assert not result['Success'] and 'overlap' in result['Failure']


def test_failed_retry_persists_manifest_failure(tmp_path, monkeypatch):
    import json
    from pathlib import Path
    from modules.preprocess_images.preprocess_images import MANIFEST_NAME
    module, source = _synthetic_module(tmp_path, monkeypatch)
    assert module.run()['Success']
    (source / 'frame.png').write_bytes(b'not an image')
    assert not module.run()['Success']
    marker = Path(module.params['output_dir'].get_value()) / MANIFEST_NAME
    assert json.loads(marker.read_text())['status'] == 'failed'


def test_failed_input_verification_persists_manifest_failure(tmp_path, monkeypatch):
    import json
    from pathlib import Path
    from modules.preprocess_images.preprocess_images import MANIFEST_NAME
    module, _source = _synthetic_module(tmp_path, monkeypatch)
    assert module.run()['Success']

    def unreadable(path):
        raise PermissionError('Cannot read source')

    monkeypatch.setattr('modules.preprocess_images.preprocess_images._file_signature',
                        unreadable)
    assert not module.run()['Success']
    marker = Path(module.params['output_dir'].get_value()) / MANIFEST_NAME
    assert json.loads(marker.read_text())['status'] == 'failed'


def test_unsupported_stale_output_cannot_flow_into_batching(tmp_path, monkeypatch):
    from pathlib import Path
    module, _source = _synthetic_module(tmp_path, monkeypatch)
    assert module.run()['Success']
    (Path(module.get_output_dir()) / 'stale.tif').write_bytes(b'stale source')
    result = module.run()
    assert not result['Success'] and 'absent from the input' in result['Failure']


def test_nested_output_junction_cannot_change_source_images(tmp_path, monkeypatch):
    import subprocess
    import pytest
    from pathlib import Path
    if os.name != 'nt':
        pytest.skip('Windows NTFS junction fixture')
    module, source = _synthetic_module(tmp_path, monkeypatch)
    nested = source / 'nested'
    nested.mkdir()
    original = source / 'frame.png'
    original.rename(nested / 'frame.png')
    before = (nested / 'frame.png').read_bytes()
    output = Path(module.get_output_dir())
    output.mkdir(parents=True)
    link = output / 'nested'
    made = subprocess.run(['cmd', '/c', 'mklink', '/J', str(link), str(nested)],
                          capture_output=True, text=True)
    if made.returncode != 0:
        pytest.skip('Cannot create an NTFS junction here')
    try:
        result = module.run()
        assert not result['Success'] and 'Unsafe preprocessing output' in result['Failure']
        assert (nested / 'frame.png').read_bytes() == before
        assert sorted(p.name for p in nested.iterdir()) == ['frame.png']
    finally:
        link.rmdir()


if __name__ == '__main__':
    from scripts.validation.check_preprocessing import main
    sys.exit(main())
