"""Reused zone copies must still match selected source pixels."""
import logging
import os

import cv2
import numpy as np
import pytest

from modules.image_batcher import batch_directory
from modules.image_batcher.batch_directory import BatchDirectory
from modules.preprocess_images.preprocess_images import _process_one


@pytest.mark.parametrize('source_kind', ['raw_images', 'preprocessed_images'])
def test_same_size_same_mtime_source_edit_refuses_stale_zone_copy(tmp_path, source_kind):
    source_dir = tmp_path / source_kind
    source_dir.mkdir()
    source = source_dir / 'P231C0001_20260930T010000Z.jpg'
    raw = tmp_path / 'input.jpg'

    def write_source(value):
        if source_kind == 'raw_images':
            source.write_bytes(b'first' if value == 40 else b'other')
        else:
            assert cv2.imwrite(str(raw), np.full((64, 64, 3), value, dtype=np.uint8),
                               [cv2.IMWRITE_JPEG_QUALITY, 95])
            assert _process_one((str(raw), str(source), 2.0, 8, False)) is None
        os.utime(source, (1000, 1000))

    write_source(40)
    module = BatchDirectory(logging.getLogger('source-integrity-test'))
    module.params = {}
    output = tmp_path / 'zone_1'
    assert module._BatchDirectory__copy_files(str(source_dir), str(output), [source.name]) == (1, 0)
    destination = next(output.rglob('*.jpg'))
    original = destination.read_bytes()
    signature = module._source_signature(str(source_dir))
    write_source(80)
    assert source.read_bytes() != original
    assert module._source_signature(str(source_dir)) == signature
    with pytest.raises(ValueError, match='does not match selected source'):
        module._BatchDirectory__copy_files(str(source_dir), str(output), [source.name])
    assert destination.read_bytes() == original


def test_zone_copy_reuses_actual_hardlink_without_hashing(tmp_path, monkeypatch):
    source_dir = tmp_path / 'raw_images'
    source_dir.mkdir()
    source = source_dir / 'P231C0001_20260930T010000Z.jpg'
    source.write_bytes(b'image')
    module = BatchDirectory(logging.getLogger('source-integrity-test'))
    module.params = {}
    output = tmp_path / 'zone_1'
    module._BatchDirectory__copy_files(str(source_dir), str(output), [source.name])
    destination = next(output.rglob('*.jpg'))
    destination.unlink()
    os.link(source, destination)
    monkeypatch.setattr(batch_directory, 'sha256_file',
                        lambda path: pytest.fail('Shared file should not be hashed'))
    assert module._BatchDirectory__copy_files(str(source_dir), str(output), [source.name]) == (1, 0)
