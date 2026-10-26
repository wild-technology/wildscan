"""Video extraction timing and output accounting with synthetic captures."""
from __future__ import annotations

import logging
import os
import sys

import cv2
import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.realpath(__file__))))
from module_base.parameter import Parameter
from modules.extract_images.extract_images import ExtractImages


def _module():
    return ExtractImages(logging.getLogger('extraction-test'))


def _part(number):
    return f'S231C0007_20231104020854_{number:04d}_chf3_nyx2.mov'


@pytest.mark.parametrize('parts, expected', [
    ([2], {}), ([1, 3], {1: 0.0}), ([1, 2, 3], {1: 0.0, 2: 60.0, 3: 120.0})])
def test_continuations_require_known_predecessors(monkeypatch, parts, expected):
    module = _module()
    monkeypatch.setattr(module, '_ExtractImages__video_duration_sec', lambda path: 60.0)
    actual = module._ExtractImages__compute_part_offsets('.', [_part(n) for n in parts])
    assert actual == {_part(number): offset for number, offset in expected.items()}


def test_unreadable_predecessor_blocks_later_parts(monkeypatch):
    module = _module()
    monkeypatch.setattr(module, '_ExtractImages__video_duration_sec', lambda path: None)
    assert module._ExtractImages__compute_part_offsets('.', [_part(1), _part(2)]) == {
        _part(1): 0.0}


class Capture:
    def __init__(self):
        self.released = False

    def isOpened(self):
        return True

    def get(self, prop):
        return 1 if prop == cv2.CAP_PROP_FRAME_COUNT else 30.0

    def set(self, *args):
        return True

    def read(self):
        return True, np.zeros((8, 8, 3), dtype=np.uint8)

    def release(self):
        self.released = True


def test_failed_image_write_is_not_counted(monkeypatch, tmp_path):
    capture = Capture()
    monkeypatch.setattr(cv2, 'VideoCapture', lambda path: capture)
    monkeypatch.setattr(cv2, 'imwrite', lambda *args: False)
    result = _module()._ExtractImages__extract_video_cv2(
        'camlower_20260930T010000Z.mov', str(tmp_path), 1.0, 3)
    assert not result['Success']
    assert result['Extracted Frame Count'] == 0
    assert capture.released


@pytest.mark.parametrize('timestamp', ['20260930T010000Z', '20260930010000'])
def test_multiple_frames_keep_timestamp_and_within_second_identity(monkeypatch, tmp_path,
                                                                 timestamp):
    capture = Capture()
    monkeypatch.setattr(capture, 'get', lambda prop:
                        60 if prop == cv2.CAP_PROP_FRAME_COUNT else 30.0)
    monkeypatch.setattr(cv2, 'VideoCapture', lambda path: capture)
    written = []

    def write(path, *args):
        written.append(os.path.basename(path))
        return True

    monkeypatch.setattr(cv2, 'imwrite', write)
    result = _module()._ExtractImages__extract_video_cv2(
        f'camlower_{timestamp}.mov', str(tmp_path), 120.0, 3)
    assert result['Success'] and result['Extracted Frame Count'] == 4
    assert written == [
        'camlower_20260930T010000Z_frame0.jpg',
        'camlower_20260930T010000Z_frame1.jpg',
        'camlower_20260930T010001Z_frame0.jpg',
        'camlower_20260930T010001Z_frame1.jpg',
    ]
    assert capture.released


@pytest.mark.parametrize('field', [cv2.CAP_PROP_FPS, cv2.CAP_PROP_FRAME_COUNT])
@pytest.mark.parametrize('value', [float('nan'), float('inf'), float('-inf')])
def test_nonfinite_video_metadata_fails_cleanly(monkeypatch, tmp_path, field, value):
    capture = Capture()
    valid_get = capture.get
    monkeypatch.setattr(capture, 'get', lambda prop: value if prop == field else valid_get(prop))
    monkeypatch.setattr(cv2, 'VideoCapture', lambda path: capture)
    module = _module()
    result = module._ExtractImages__extract_video_cv2(
        'camlower_20260930T010000Z.mov', str(tmp_path), 1.0, 3)
    assert not result['Success'] and capture.released
    capture.released = False
    assert module._ExtractImages__video_duration_sec('fixture.mov') is None
    assert capture.released


def test_partial_video_failures_stop_the_stage(monkeypatch, tmp_path):
    source = tmp_path / 'videos'
    source.mkdir()
    for name in ('camlower_20260930T010000Z.mov', 'camlower_20260930T010100Z.mov'):
        (source / name).write_bytes(b'video')
    module = _module()
    module.params = module.get_parameters()
    module.params['image_input_video'].set_value(str(source))
    module.params['output_dir'] = Parameter('Out', 'o', 'output_dir', str, str(tmp_path))
    results = iter([{'Success': True, 'Input Frame Count': 1, 'Extracted Frame Count': 1},
                    {'Success': False, 'Input Frame Count': 1, 'Extracted Frame Count': 0}])
    monkeypatch.setattr(module, '_ExtractImages__extract_video_cv2',
                        lambda *args, **kwargs: next(results))
    result = module.run()
    assert not result['Success']
    assert result['Total Extracted Frame Count'] == 1
    assert result['Failed Videos'] == 1


def test_selected_continuation_reads_predecessor_metadata(monkeypatch, tmp_path):
    for number in (1, 2):
        (tmp_path / _part(number)).write_bytes(b'video')
    module = _module()
    module.params = module.get_parameters()
    module.params['image_input_video'].set_value(str(tmp_path / _part(2)))
    module.params['output_dir'] = Parameter('Out', 'o', 'output_dir', str, str(tmp_path))
    monkeypatch.setattr(module, '_ExtractImages__video_duration_sec', lambda path: 60.0)
    offsets = []

    def extract(path, *args, start_offset_sec=0.0):
        offsets.append(start_offset_sec)
        return {'Success': True, 'Input Frame Count': 1, 'Extracted Frame Count': 1}

    monkeypatch.setattr(module, '_ExtractImages__extract_video_cv2', extract)
    assert module.run()['Success']
    assert offsets == [60.0]


def test_selected_continuation_without_predecessor_fails(monkeypatch, tmp_path):
    (tmp_path / _part(2)).write_bytes(b'video')
    module = _module()
    module.params = module.get_parameters()
    module.params['image_input_video'].set_value(str(tmp_path / _part(2)))
    module.params['output_dir'] = Parameter('Out', 'o', 'output_dir', str, str(tmp_path))
    monkeypatch.setattr(module, '_ExtractImages__extract_video_cv2',
                        lambda *args, **kwargs: pytest.fail('Unknown start must not be extracted'))
    result = module.run()
    assert not result['Success'] and result['Failed Videos'] == 1
