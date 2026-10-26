"""Standalone georeferencing copy and validation accounting on temporary images."""
from __future__ import annotations

import os
import sys

from PIL import Image
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.realpath(__file__))))
import georeference_survey


class LocalPool:
    """Run file workers locally while retaining their iterator contract."""

    def __init__(self, workers):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def imap(self, worker, jobs):
        return map(worker, jobs)


def test_copy_reports_copied_skipped_and_failed_sources(tmp_path, monkeypatch):
    monkeypatch.setattr(georeference_survey, 'Pool', LocalPool)
    source = tmp_path / 'source.jpg'
    source.write_bytes(b'image')
    output = tmp_path / 'output'
    camera = output / 'NA173_H2104' / 'CamLower'
    camera.mkdir(parents=True)
    (camera / 'existing.jpg').write_bytes(b'image')
    images = [
        {'FULL_PATH': str(source), 'FILENAME': 'new.jpg', 'CAMERA_TYPE': 'CamLower'},
        {'FULL_PATH': str(source), 'FILENAME': 'existing.jpg', 'CAMERA_TYPE': 'CamLower'},
        {'FULL_PATH': str(tmp_path / 'absent.jpg'), 'FILENAME': 'absent.jpg',
         'CAMERA_TYPE': 'CamLower'},
    ]
    copied, failed, counts = georeference_survey.copy_matched_images(images, 'NA173_H2104', str(output))
    assert (copied, failed, dict(counts)) == (2, 1, {'CamLower': 2})
    assert (camera / 'new.jpg').read_bytes() == source.read_bytes()
    assert (camera / 'existing.jpg').read_bytes() == b'image'
    assert not (camera / 'absent.jpg').exists()


def test_validation_reports_corrupt_images_and_preserves_them(tmp_path, monkeypatch):
    monkeypatch.setattr(georeference_survey, 'Pool', LocalPool)
    camera = tmp_path / 'NA173_H2104' / 'CamLower'
    camera.mkdir(parents=True)
    Image.new('RGB', (8, 8)).save(camera / 'valid.jpg')
    corrupt = camera / 'corrupt.jpg'
    corrupt.write_bytes(b'invalid image')
    stats = georeference_survey.validate_and_cleanup_images(str(tmp_path), ['NA173_H2104'],
                                               delete_corrupt='no')
    assert stats['total_checked'] == 2
    assert stats['valid_images'] == 1 and stats['corrupt_images'] == 1
    assert dict(stats['per_dive_corrupt']) == {'NA173_H2104': ['corrupt.jpg']}
    assert stats['deleted'] == 0 and corrupt.read_bytes() == b'invalid image'


def test_copy_refuses_same_size_stale_destination(tmp_path):
    source = tmp_path / 'source.jpg'
    source.write_bytes(b'newer')
    camera = tmp_path / 'output' / 'dive' / 'CamLower'
    camera.mkdir(parents=True)
    destination = camera / 'frame.jpg'
    destination.write_bytes(b'older')
    os.utime(source, (1000, 1000))
    os.utime(destination, (1000, 1000))
    result = georeference_survey._copy_single_image((
        {'FULL_PATH': str(source), 'FILENAME': 'frame.jpg', 'CAMERA_TYPE': 'CamLower'},
        'dive', str(tmp_path / 'output')))
    assert not result['success']
    assert 'does not match selected source' in result['error']
    assert destination.read_bytes() == b'older'


def test_copy_reuses_actual_hardlink_without_hashing(tmp_path, monkeypatch):
    source = tmp_path / 'source.jpg'
    source.write_bytes(b'image')
    camera = tmp_path / 'output' / 'dive' / 'CamLower'
    camera.mkdir(parents=True)
    os.link(source, camera / 'frame.jpg')
    monkeypatch.setattr(georeference_survey, 'sha256_file',
                        lambda path: pytest.fail('Shared file should not be hashed'))
    result = georeference_survey._copy_single_image((
        {'FULL_PATH': str(source), 'FILENAME': 'frame.jpg', 'CAMERA_TYPE': 'CamLower'},
        'dive', str(tmp_path / 'output')))
    assert result['success'] and result['skipped']


class MemorySettings:
    def ask(self, section, key, explicit, default):
        return explicit if explicit is not None else default


def _survey_inputs(tmp_path, monkeypatch):
    monkeypatch.setattr(georeference_survey, 'SettingsStore', MemorySettings)
    monkeypatch.setattr(georeference_survey, 'Pool', LocalPool)
    images = tmp_path / 'images'
    edt = images / 'edt'
    edt.mkdir(parents=True)
    source = edt / 'P231C0001_20260930T010000Z.jpg'
    Image.new('RGB', (8, 8), color=(20, 40, 60)).save(source)
    nav = tmp_path / 'nav'
    nav.mkdir()
    (nav / 'NA173_H2104_final_datatable.csv').write_text(
        'Timestamp,kalman_lat,kalman_long,kalman_depth,kalman_yaw_deg,'
        'kalman_pitch_deg,kalman_roll_deg\n'
        '2026-09-30T01:00:00Z,35,139,100,90,0,0\n', encoding='utf-8')
    output = tmp_path / 'output'
    arguments = ['--image-base-dir', str(images), '--rov-data-dir', str(nav),
                 '--output-dir', str(output), '--delete-corrupt', 'no']
    return source, output, arguments


def test_successful_survey_keeps_copy_and_flight_log_behavior(tmp_path, monkeypatch):
    source, output, arguments = _survey_inputs(tmp_path, monkeypatch)
    original = source.read_bytes()
    assert georeference_survey.main(arguments) == 0
    logs = list(output.glob('*_UTM.txt'))
    assert len(logs) == 1
    rows = logs[0].read_text(encoding='utf-8').splitlines()
    assert len(rows) == 2 and source.name in rows[1]
    destination = next((output / 'NA173_H2104').rglob('*.jpg'))
    assert destination.read_bytes() == original == source.read_bytes()


def test_failed_copy_returns_failure_without_generating_flight_log(tmp_path, monkeypatch,
                                                                 capsys):
    source, output, arguments = _survey_inputs(tmp_path, monkeypatch)
    original = source.read_bytes()

    def fail_copy(*args, **kwargs):
        raise OSError('destination unavailable')

    monkeypatch.setattr(georeference_survey.shutil, 'copy2', fail_copy)
    assert georeference_survey.main(arguments) == 1
    assert not list(output.glob('flight_log*.txt'))
    assert source.read_bytes() == original
    messages = capsys.readouterr().out
    assert 'NOT GENERATED (image copy failed)' in messages
    assert 'Lines Written:            0' in messages
    assert 'Processing complete!' not in messages


def test_stale_copy_preserves_existing_log_and_fails_survey(tmp_path, monkeypatch):
    source, output, arguments = _survey_inputs(tmp_path, monkeypatch)
    original = source.read_bytes()
    camera = output / 'NA173_H2104' / georeference_survey.get_camera_type(source.name)
    camera.mkdir(parents=True)
    destination = camera / source.name
    destination.write_bytes(b'x' * len(original))
    os.utime(destination, ns=(source.stat().st_atime_ns, source.stat().st_mtime_ns))
    previous_log = output / 'flight_log_NA173_H2104_54S_UTM.txt'
    previous_log.write_bytes(b'previous verified log')
    assert georeference_survey.main(arguments) == 1
    assert destination.read_bytes() == b'x' * len(original)
    assert previous_log.read_bytes() == b'previous verified log'
    assert list(output.glob('flight_log*.txt')) == [previous_log]
    assert source.read_bytes() == original
