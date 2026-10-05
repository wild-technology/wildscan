#!/usr/bin/env python3
"""The camera registry: modules/cameras.json loaded by modules/camera_registry.py.

Pins the two ILX-LR1 cameras and their typed calibration fields, the Wild
Sync filename families (cam1 -> ilx_left, cam2 -> ilx_right, encoded once),
the mounts and default accuracies, agreement with the calibration record
calibration/ilx_lr1_stereo_dive_001.json, and the validation that stops a
malformed cameras.json at import.

Offline and standard-library only.

Run:  py -3.13 -m pytest tests/test_camera_registry.py
"""
from __future__ import annotations

import copy
import importlib.util
import json
import os
import shutil
import subprocess
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
sys.path.insert(0, REPO_ROOT)

from modules import camera_registry  # noqa: E402
from modules.calibration_sidecars import intrinsics_to_xmp_values  # noqa: E402
from modules.camera_registry import (Mount, PriorAccuracy,  # noqa: E402
                                     RegistryError, load_registry)

CAMERAS_JSON = os.path.join(REPO_ROOT, 'modules', 'cameras.json')
CALIBRATION_JSON = os.path.join(REPO_ROOT, 'calibration',
                                'ilx_lr1_stereo_dive_001.json')


def _load(path: str) -> dict:
    with open(path, encoding='utf-8') as f:
        return json.load(f)


def _data(section: dict) -> dict:
    """A JSON object without its '_' comment keys."""
    return {k: v for k, v in section.items() if not k.startswith('_')}


# ------------------------------------------------------------- contents

def test_the_registry_holds_exactly_the_two_ilx_cameras():
    assert set(camera_registry.CAMERAS) == {'ilx_left', 'ilx_right'}
    assert dict(camera_registry.FAMILY_CAMERA) == {'cam1': 'ilx_left',
                                                   'cam2': 'ilx_right'}
    assert set(camera_registry.RIGS) == {'ilx_lr1_stereo'}


def test_calibration_fields_are_loaded_and_typed():
    left = camera_registry.CAMERAS['ilx_left']
    right = camera_registry.CAMERAS['ilx_right']
    assert left.calibration_image_size == (4096, 3000)
    assert right.calibration_image_size == (4096, 3000)
    assert left.calibration_focal_mm == 16.0
    assert left.focal_length_35mm == 17.72584
    assert (left.principal_point_u, left.principal_point_v) == (0.01871054,
                                                                -0.02501735)
    assert (right.principal_point_u, right.principal_point_v) == (0.06111425,
                                                                  -0.07754206)
    assert left.distortion_model == right.distortion_model == 'brown3'
    assert left.calibration_prior == 'approximate'
    assert (left.calibration_group, left.lens_distortion_group) == ('7', '7')
    assert (right.calibration_group, right.lens_distortion_group) == ('8', '8')
    assert left.opencv_distortion == (-0.15051064317869475,
                                      0.01672300219900676, 0.0, 0.0, 0.0)
    assert right.opencv_distortion == (-0.14233307188331493,
                                       0.01544261781011792, 0.0, 0.0, 0.0)
    assert left.calibration_aspect == pytest.approx(4096 / 3000)


@pytest.mark.parametrize('name,camera', [
    ('Cam1_20260820_192542.42.jpg', 'ilx_left'),
    ('Cam1_20260820_192542.42.card.JPG', 'ilx_left'),
    ('Cam1_20260820_192542.42.ARW', 'ilx_left'),
    ('Cam1_20260820_192542.42.xmp', 'ilx_left'),
    ('cam1_20260820_192542.42.jpg', 'ilx_left'),
    ('Cam2_20260820_192542.42.jpg', 'ilx_right'),
    ('CAM2_20260820_192542.42.CARD.JPG', 'ilx_right'),
    ('Cam2_20260820_192542.42.arw', 'ilx_right'),
])
def test_wild_sync_file_names_resolve_to_their_camera(name, camera):
    assert camera_registry.identify(name).key == camera
    assert camera_registry.family(name) == {'ilx_left': 'cam1',
                                            'ilx_right': 'cam2'}[camera]


@pytest.mark.parametrize('name', [
    'Cam10_20260820_192542.42.jpg',
    'Cam3_20260820_192542.42.jpg',
    'xCam1_20260820_192542.42.jpg',
    'Cam1-20260820_192542.42.jpg',
    '20260518T205348.030532Z_left.jpg',
    'unrecognised.jpg',
])
def test_other_names_resolve_to_no_camera(name):
    assert camera_registry.identify(name) is None
    assert camera_registry.family(name) is None
    assert camera_registry.mount_for(name) is None


def test_matching_uses_the_base_name_only():
    assert camera_registry.identify(
        os.path.join('Cam2_run', 'Cam1_20260820_192542.42.jpg')).key == 'ilx_left'


def test_node_assignment_is_encoded_only_in_the_family_rows():
    """cam1/cam2 appear in families and nowhere else; the rig carries only
    the confirmation switch."""
    data = _load(CAMERAS_JSON)
    rig = data['rigs']['ilx_lr1_stereo']
    rig_values = json.dumps(_data(rig)).lower()
    assert 'cam1' not in rig_values and 'cam2' not in rig_values
    assert rig['node_assignment_confirmed'] is True
    assert camera_registry.rig_for('ilx_left').node_assignment_confirmed is True
    assert camera_registry.rig_for('ilx_right').cameras == ('ilx_left',
                                                            'ilx_right')
    camera_values = json.dumps({k: _data(v) for k, v in
                                _data(data['cameras']).items()}).lower()
    assert 'cam1' not in camera_values and 'cam2' not in camera_values


def test_mounts_are_loaded_from_cameras_json():
    data = _load(CAMERAS_JSON)
    for entry in data['families']:
        spec = entry['mount']
        loaded = camera_registry.FAMILIES[entry['family']].mount
        assert loaded == Mount(
            down_tilt_deg=spec['down_tilt_deg'],
            pitch_accuracy_deg=spec['pitch_accuracy_deg'],
            yaw_offset_deg=spec['yaw_offset_deg'],
            lever_arm_m=None if spec['lever_arm_m'] is None
            else tuple(spec['lever_arm_m']))
    # Nominal nadir mount, image top along the heading, lever arm unmeasured.
    assert camera_registry.mount_for('Cam1_x.jpg') == Mount(90.0, 15.0, 0.0, None)
    assert camera_registry.mount_for('Cam2_x.jpg') == Mount(90.0, 15.0, 0.0, None)


def test_default_prior_accuracies_are_loaded():
    data = _load(CAMERAS_JSON)['defaults']
    assert camera_registry.PRIOR_ACCURACY == PriorAccuracy(
        x_m=data['position_accuracy_m']['x'],
        y_m=data['position_accuracy_m']['y'],
        alt_m=data['position_accuracy_m']['alt'],
        yaw_deg=data['orientation_accuracy_deg']['yaw'],
        roll_deg=data['orientation_accuracy_deg']['roll'])
    assert camera_registry.PRIOR_ACCURACY == PriorAccuracy(10.0, 10.0, 1.0,
                                                           15.0, 15.0)


# ------------------------------------------ agreement with the record

def test_cameras_json_agrees_with_the_calibration_record():
    registry = _load(CAMERAS_JSON)
    record = _load(CALIBRATION_JSON)
    for key, recorded in _data(record['cameras']).items():
        current = registry['cameras'][key]
        for field, value in _data(recorded).items():
            assert current[field] == value, f'cameras[{key!r}].{field}'
        assert current['_provenance'] == recorded['_provenance']
        assert current['calibration_focal_mm'] == \
            record['rigs']['ilx_lr1_stereo']['focal_nominal_mm']
        assert current['resolution'] == record['rigs']['ilx_lr1_stereo']['resolution']

    rig = registry['rigs']['ilx_lr1_stereo']
    recorded_rig = record['rigs']['ilx_lr1_stereo']
    assert rig['stereo_baseline_m'] == recorded_rig['stereo_baseline_m']
    assert _data(rig['extrinsics_left_to_right']) == \
        _data(recorded_rig['extrinsics_left_to_right'])
    assert {eye: entry['camera'] for eye, entry in rig['eyes'].items()} == \
        {eye: entry['camera'] for eye, entry in recorded_rig['eyes'].items()}


@pytest.mark.parametrize('key', ['ilx_left', 'ilx_right'])
def test_xmp_values_are_the_conversion_of_the_recorded_intrinsics(key):
    recorded = _load(CALIBRATION_JSON)['cameras'][key]
    values = intrinsics_to_xmp_values(recorded['intrinsics'],
                                      recorded['resolution'])
    camera = camera_registry.CAMERAS[key]
    assert camera.focal_length_35mm == pytest.approx(values['focal35'], abs=1e-6)
    assert camera.principal_point_u == pytest.approx(values['ppu'], abs=1e-8)
    assert camera.principal_point_v == pytest.approx(values['ppv'], abs=1e-8)


# ------------------------------------------------------------ validation

def _write(tmp_path, data) -> str:
    path = tmp_path / 'cameras.json'
    path.write_text(json.dumps(data), encoding='utf-8')
    return str(path)


def _mutated(tmp_path, mutate) -> str:
    data = copy.deepcopy(_load(CAMERAS_JSON))
    mutate(data)
    return _write(tmp_path, data)


def test_the_repository_file_loads():
    registry = load_registry(CAMERAS_JSON)
    assert set(registry.cameras) == {'ilx_left', 'ilx_right'}


def test_comment_keys_are_ignored(tmp_path):
    def mutate(data):
        data['cameras']['_note'] = ['anything']
        data['rigs']['_note'] = 'anything'
        data['cameras']['ilx_left']['_note'] = 1
    assert set(load_registry(_mutated(tmp_path, mutate)).cameras) == {
        'ilx_left', 'ilx_right'}


def _family(data, name):
    return next(f for f in data['families'] if f['family'] == name)


@pytest.mark.parametrize('mutate,match', [
    (lambda d: _family(d, 'cam1').update(camera='ilx_missing'),
     r"names camera 'ilx_missing'"),
    (lambda d: d['cameras']['ilx_left'].pop('principal_point_u'),
     r"principal_point_u"),
    (lambda d: d['cameras']['ilx_left'].pop('calibration_focal_mm'),
     r"calibration_focal_mm"),
    (lambda d: d['cameras']['ilx_left'].update(resolution='4096x3000'),
     r"resolution"),
    (lambda d: d['cameras']['ilx_left'].update(focal_length_35mm=True),
     r"focal_length_35mm"),
    (lambda d: d['cameras']['ilx_left'].update(calibration_prior='exact'),
     r"calibration_prior"),
    (lambda d: d['cameras']['ilx_left'].update(distortion_model='division'),
     r"distortion_model"),
    (lambda d: d['cameras']['ilx_left'].update(
        opencv_distortion_k1_k2_p1_p2_k3=[-0.15, 0.016, 0.001, 0.0, 0.0]),
     r"tangential"),
    (lambda d: d['cameras']['ilx_left'].update(
        opencv_distortion_k1_k2_p1_p2_k3=[-0.15, 0.016]),
     r"opencv_distortion_k1_k2_p1_p2_k3"),
    (lambda d: d['cameras']['ilx_right'].update(calibration_group='7'),
     r"own group"),
    (lambda d: d['cameras']['ilx_left'].update(calibration_group='seven'),
     r"calibration_group"),
    (lambda d: _family(d, 'cam1').update(pattern='^cam1_('),
     r"regular expression"),
    (lambda d: _family(d, 'cam1').pop('mount'), r"mount"),
    (lambda d: _family(d, 'cam1')['mount'].update(pitch_accuracy_deg=0),
     r"pitch_accuracy_deg"),
    (lambda d: _family(d, 'cam1')['mount'].update(lever_arm_m=[0.1, 0.2]),
     r"lever_arm_m"),
    (lambda d: d['families'].append(dict(_family(d, 'cam1'))),
     r"more than once"),
    (lambda d: d['rigs']['ilx_lr1_stereo'].update(
        node_assignment_confirmed='yes'), r"node_assignment_confirmed"),
    (lambda d: d['rigs']['ilx_lr1_stereo'].pop('node_assignment_confirmed'),
     r"node_assignment_confirmed"),
    (lambda d: d['rigs']['ilx_lr1_stereo']['eyes'].pop('R'),
     r"exactly one rig"),
    (lambda d: d['rigs']['ilx_lr1_stereo']['eyes']['R'].update(
        camera='ilx_missing'), r"ilx_missing"),
    (lambda d: d['defaults']['position_accuracy_m'].update(x=-1),
     r"position_accuracy_m"),
    (lambda d: d.update(schema_version=1), r"schema_version"),
])
def test_a_malformed_registry_is_refused_with_a_named_entry(tmp_path, mutate,
                                                            match):
    with pytest.raises(RegistryError, match=match):
        load_registry(_mutated(tmp_path, mutate))


def test_an_unreadable_registry_is_refused(tmp_path):
    path = tmp_path / 'cameras.json'
    path.write_text('{"schema_version": 2,', encoding='utf-8')
    with pytest.raises(RegistryError, match='not valid JSON'):
        load_registry(str(path))
    with pytest.raises(RegistryError, match='cannot be read'):
        load_registry(str(tmp_path / 'absent.json'))


def test_a_malformed_registry_stops_the_import(tmp_path):
    """The module validates at import: a copy of it beside a broken
    cameras.json must not import."""
    def mutate(data):
        _family(data, 'cam2')['camera'] = 'ilx_missing'
    _mutated(tmp_path, mutate)
    module_path = tmp_path / 'camera_registry_probe.py'
    shutil.copyfile(os.path.join(REPO_ROOT, 'modules', 'camera_registry.py'),
                    module_path)
    name = 'camera_registry_probe'
    spec = importlib.util.spec_from_file_location(name, str(module_path))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        with pytest.raises(ValueError, match="names camera 'ilx_missing'"):
            spec.loader.exec_module(module)
    finally:
        sys.modules.pop(name, None)


def test_registry_modules_import_with_the_standard_library_only():
    """No site-packages (-S): the registry, the calibration delivery and
    the flight-log helpers must still import."""
    code = ('import sys; sys.path.insert(0, sys.argv[1]); '
            'import modules.camera_registry, modules.calibration_sidecars, '
            'modules.flight_logs')
    result = subprocess.run([sys.executable, '-I', '-S', '-c', code, REPO_ROOT],
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
