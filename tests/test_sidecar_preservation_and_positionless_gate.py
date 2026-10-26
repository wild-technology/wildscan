"""Operator-approved data preservation and bounded positionless fallback.

Offline: native processing is mocked; source files and backups are real.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from modules.realityscan_interface.realityscan_interface import backup_pre_existing_sidecars
from tests.test_alignment_calibration_delivery import _module, _workspace
from tests.test_batch_degenerate_geometry import _run
from tests.test_batch_reuse_guard import _module as batch_module

LOGGER = logging.getLogger(__name__)
POSE = b'<xmp xcr:Position="1 2 3">original user pose</xmp>\r\n'


def test_sidecar_snapshots_are_byte_exact_append_only_and_leave_sources(tmp_path):
    source, output = tmp_path / 'images', tmp_path / 'outputs'
    (source / 'port').mkdir(parents=True)
    pose = source / 'port/photo.xmp'
    foreign = source / 'custom.XMP'
    pose.write_bytes(POSE)
    foreign.write_bytes(b'foreign calibration')
    first = Path(backup_pre_existing_sidecars(str(source), str(output)))
    assert pose.read_bytes() == POSE
    assert foreign.read_bytes() == b'foreign calibration'
    assert (first / 'port/photo.xmp').read_bytes() == POSE
    original_manifest = (first / 'backup_manifest.json').read_bytes()
    assert len(json.loads(original_manifest)['sidecars']) == 2
    pose.write_bytes(b'changed user pose')
    second = Path(backup_pre_existing_sidecars(str(source), str(output)))
    assert first != second
    assert (first / 'port/photo.xmp').read_bytes() == POSE
    assert (first / 'backup_manifest.json').read_bytes() == original_manifest
    assert (second / 'port/photo.xmp').read_bytes() == b'changed user pose'


def test_backup_failure_stops_before_native_changes_and_keeps_original(tmp_path, monkeypatch):
    ws = _workspace(tmp_path, None)
    image = next((ws / 'batched_images_by_zone/zone_1').rglob('*.JPG'))
    sidecar = image.with_suffix('.xmp')
    sidecar.write_bytes(POSE)
    module, calls = _module(tmp_path, monkeypatch, ws, LOGGER)
    monkeypatch.setattr('modules.realityscan_interface.realityscan_interface.os.fsync',
                        lambda _fd: (_ for _ in ()).throw(OSError('disk failure')))
    result = module.run()
    assert result['Success'] is False
    assert calls == []
    assert sidecar.read_bytes() == POSE


def test_native_overwrite_and_retry_keep_original_sidecar_backup(tmp_path, monkeypatch):
    ws = _workspace(tmp_path, None)
    image = next((ws / 'batched_images_by_zone/zone_1').rglob('*.JPG'))
    sidecar = image.with_suffix('.xmp')
    sidecar.write_bytes(POSE)
    # Fix the rename timestamp to exercise a same-second retry collision.
    monkeypatch.setattr('modules.realityscan_interface.realityscan_interface.time.strftime',
                        lambda *_args: 'same_second')
    original_copy = []
    def native_overwrite(args):
        snapshots = list((Path(args[1]) / 'pre_existing_sidecars').glob('*/backup_manifest.json'))
        relative = sidecar.relative_to(ws / 'batched_images_by_zone/zone_1')
        if not original_copy:
            assert snapshots
            original_copy.append(snapshots[0].parent / relative)
            assert original_copy[0].read_bytes() == POSE
        sidecar.write_bytes(b'<xmp xcr:Position="9 8 7"/>')
    module, _calls = _module(tmp_path, monkeypatch, ws, LOGGER, on_run=native_overwrite)
    assert module.run()['Success']
    # Retry twice: prior backup folders must survive both supersede operations.
    for _ in range(2):
        assert module.run()['Success']
    preserved = [path for path in ws.rglob('*.xmp')
                 if 'pre_existing_sidecars' in path.parts and path.read_bytes() == POSE]
    assert len(preserved) == 1
    superseded = list((ws / 'superseded').iterdir())
    assert len(superseded) == 2


@pytest.mark.parametrize('positions', [[(None, None)] * 12, [(1.0, 2.0)] * 12])
def test_large_positionless_or_static_scene_refuses_before_batch_writes(
        tmp_path, monkeypatch, positions):
    result, output = _run(tmp_path, monkeypatch, positions,
                          extra={'batch_max_zone_size': 10})
    assert result['Success'] is False
    assert '--b_allow_positionless_scene true' in result['Error']
    assert list(output.iterdir()) == []


@pytest.mark.parametrize('positions', [[(None, None)] * 12, [(1.0, 2.0)] * 12])
def test_explicit_opt_in_allows_large_positionless_or_static_scene(
        tmp_path, monkeypatch, positions):
    result, output = _run(tmp_path, monkeypatch, positions,
                          extra={'batch_max_zone_size': 10,
                                 'batch_allow_positionless_scene': True})
    assert result['Success']
    assert result['Number of Zones'] == 1
    assert len(list(output.rglob('*.JPG'))) == 12


def test_gate_runs_during_validation_before_existing_output_is_touched(tmp_path, monkeypatch):
    source = tmp_path / 'raw_images'
    source.mkdir()
    name = 'P231C0001_20260720120000.jpg'
    (source / name).write_bytes(b'original image')
    log = source / 'flight_log_19T_UTM.txt'
    log.write_text('filename;X (East);Y (North);Alt\n' + '\n'.join(
        f'image_{index}.jpg;;;' for index in range(12)) + '\n')
    output = tmp_path / 'batched_images_by_zone'
    output.mkdir()
    previous = output / 'previous-result.txt'
    previous.write_bytes(b'previous result')
    module = batch_module(tmp_path, source)
    module.params['batch_flight_log_path'].set_value(str(log))
    module.params['batch_max_zone_size'].set_value(10)
    monkeypatch.setattr('builtins.input', lambda *_a: pytest.fail('must not ask to clear outputs'))
    valid, error = module.validate_parameters()
    assert not valid
    assert '--b_allow_positionless_scene true' in error
    assert previous.read_bytes() == b'previous result'
    assert sorted(path.name for path in output.iterdir()) == ['previous-result.txt']


def test_large_frame_without_optional_geometry_still_requires_opt_in(tmp_path):
    import pandas as pd
    from modules.image_batcher.batch_directory import BatchDirectory
    from tests.test_alignment_calibration_delivery import _param
    module = BatchDirectory(LOGGER)
    module.params = {'batch_max_zone_size': _param('max', 10)}
    no_positions = pd.DataFrame({'filename': [f'image_{index}.jpg' for index in range(12)]})
    assert '--b_allow_positionless_scene true' in module._positionless_scene_error(no_positions, 12)
    module.params['batch_allow_positionless_scene'] = _param('allow', True)
    assert module._positionless_scene_error(no_positions, 12) is None


def test_numeric_position_columns_work_without_optional_geometry(tmp_path):
    import pandas as pd
    from modules.image_batcher.batch_directory import BatchDirectory
    from tests.test_alignment_calibration_delivery import _param
    module = BatchDirectory(LOGGER)
    module.params = {'batch_max_zone_size': _param('max', 10)}
    positioned = pd.DataFrame({'x': list(range(12)), 'y': [0] * 12})
    assert module._positionless_scene_error(positioned, 12) is None
    static = pd.DataFrame({'x': [1] * 12, 'y': [2] * 12})
    assert '--b_allow_positionless_scene true' in module._positionless_scene_error(static, 12)
