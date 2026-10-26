"""Existing WCA/Zeuss optical and mount policies coexist with Sony intake.

These fixtures have no real images and do not run RealityScan or image
processing. They guard the settings handed to the native workflow.
"""
from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from modules import calibration_sidecars as cs
from modules import camera_registry as cr


@pytest.mark.parametrize('filename,camera,family,focal,model,tilt,lever', [
    ('P245C0001_HERC_20250102112233.jpg', 'port', 'wca_port', 16.0,
     'division', 0.0, (1.0, 0.0, 1.0)),
    ('C245C0001_20250102112233.jpg', 'cinema', 'wca_cinema', 16.0,
     'brown3', 45.0, (1.0, 0.0, 0.0)),
    ('cammid_20250102112233.jpg', 'port', 'legacy_cammid', 16.0,
     'division', 20.0, (1.0, 0.0, 1.0)),
    ('camlower_20250102112233.jpg', 'cinema', 'legacy_camlower', 16.0,
     'brown3', 10.0, (1.0, 0.0, 1.0)),
    ('camupper_20250102112233.jpg', 'starboard', 'legacy_camupper', 16.0,
     'division', 70.0, (1.0, 0.0, 0.0)),
    ('survey_HERC_20250102112233.jpg', 'zeuss', 'zeuss', 23.0,
     'brown3', 30.0, (0.5, 0.0, 0.5)),
    ('survey-zeuss-20250102112233.jpg', 'zeuss', 'zeuss', 23.0,
     'brown3', 30.0, (0.5, 0.0, 0.5)),
])
def test_legacy_filename_optics_and_mount_identity(filename, camera, family,
                                                  focal, model, tilt, lever):
    physical = cr.identify(filename)
    assert physical.key == camera
    assert cr.family(filename) == family
    assert physical.focal_length_35mm == focal
    assert physical.distortion_model == model
    assert cr.mount_for(filename).down_tilt_deg == tilt
    assert cr.mount_for(filename).lever_arm_m == lever


def test_missing_starboard_mount_is_not_invented():
    assert cr.identify('S245C0001.jpg').key == 'starboard'
    assert cr.mount_for('S245C0001.jpg') is None


def test_legacy_camera_constructor_and_profile_stay_compatible():
    assert cr.CAMERAS['port'] == cr.Camera(
        'port', '2', 'Approximate', 16.0, '2', 'Approximate', 'division')


def test_generic_sidecars_restore_focal_and_groups_without_image_exif(tmp_path):
    # WCA source JPEGs can be EXIF-identical and lack focal length.
    # No attempt to infer Sony sensor geometry is valid for these cameras.
    names = ('P245C0001.jpg', 'C245C0001.jpg', 'survey_HERC_1.jpg')
    for filename in names:
        (tmp_path / filename).write_bytes(b'fixture; no image decoding needed')
    assert cs.ensure_calibration_sidecars(str(tmp_path)) == (3, 0)
    camera_ns = '{http://www.capturingreality.com/ns/camera/1.0/}'
    xcr_ns = '{http://www.capturingreality.com/ns/xcr/1.0/}'
    for filename in names:
        physical = cr.identify(filename)
        sidecar = tmp_path / Path(filename).with_suffix('.xmp')
        content = sidecar.read_text()
        assert content == cr.calibration_xmp(physical)
        root = ET.fromstring(content)
        assert root.find(f'.//{camera_ns}CalibrationGroup').text == \
            physical.calibration_group
        assert root.find(f'.//{camera_ns}DistortionModel').text == \
            physical.distortion_model
        assert float(root.find(f'.//{xcr_ns}FocalLength35mm').text) == \
            physical.focal_length_35mm
        assert 'Position' not in content


def test_generic_defaults_do_not_enable_sony_or_voyis_priors(tmp_path,
                                                          monkeypatch):
    monkeypatch.delenv('RS_VOYIS_CALIB_SIDECARS', raising=False)
    assert cs.default_calibration_modes() == {
        'zeuss': 'prior', 'port': 'prior', 'cinema': 'prior',
        'starboard': 'prior'}
    for name in ('Cam1_1.jpg', 'Cam2_1.jpg', 'L_1.jpg', 'R_1.jpg'):
        (tmp_path / name).write_bytes(b'fixture')
    assert cs.ensure_calibration_sidecars(str(tmp_path)) == (0, 0)
    assert not list(tmp_path.glob('*.xmp'))
    monkeypatch.setenv('RS_VOYIS_CALIB_SIDECARS', '1')
    assert cs.ensure_calibration_sidecars(str(tmp_path)) == (2, 0)
    assert not (tmp_path / 'Cam1_1.xmp').exists()


def test_generic_pose_hygiene_restores_each_legacy_optical_profile(tmp_path):
    xmp = tmp_path / 'P245C0001.xmp'
    xmp.write_text('<xcr:Position>1 2 3</xcr:Position>')
    assert cs.sanitize_and_census(str(tmp_path)) == (1, 1, 0)
    assert xmp.read_text() == cr.calibration_xmp(cr.CAMERAS['port'])


def test_explicit_disabled_modes_are_not_replaced_by_generic_defaults(tmp_path):
    (tmp_path / 'P245C0001.jpg').write_bytes(b'fixture')
    assert cs.ensure_calibration_sidecars(str(tmp_path), {}) == (0, 0)
    assert not (tmp_path / 'P245C0001.xmp').exists()


def test_wca_accuracy_and_assumed_mount_defaults_are_retained():
    data = json.loads(Path(cr.CAMERAS_JSON).read_text())
    assert data['defaults']['position_accuracy_m'] == {
        'x': 10.0, 'y': 10.0, 'alt': 1.0}
    assert data['defaults']['orientation_accuracy_deg'] == {
        'yaw': 15.0, 'roll': 15.0}
    assert data['defaults']['assumed_mount']['pitch_deg'] == 10.0
    assert data['defaults']['assumed_mount']['pitch_accuracy_deg'] == 30.0
    assert cr.rig_for('port').prior_accuracy == cr.LEGACY_PRIOR_ACCURACY
    assert cr.rig_for('ilx_left').prior_accuracy == cr.PRIOR_ACCURACY


def test_explicit_intrinsics_utility_retains_its_positional_interface(tmp_path):
    for name in ('L_1.jpg', 'R_1.jpg', 'unknown.jpg'):
        (tmp_path / name).write_bytes(b'fixture')
    intrinsics = [[1000, 0, 1050], [0, 1000, 980], [0, 0, 1]]
    assert cs.write_sidecars(str(tmp_path), intrinsics, (2000, 2000)) == {
        'L': 1, 'R': 1, 'skipped_no_eye': 1}
    left = (tmp_path / 'L_1.xmp').read_text()
    right = (tmp_path / 'R_1.xmp').read_text()
    assert 'xcr:CalibrationGroup="0"' in left
    assert 'xcr:CalibrationGroup="1"' in right
    assert 'xcr:DistortionModel="division"' in left
    assert 'xcr:FocalLength35mm="18.0000000000"' in left
    assert cs.eye_of('record_image_left_processed_D.jpg') == 'L'
    assert cs.eye_of('record_image_right_processed_D.jpg') == 'R'


@pytest.mark.parametrize('call_style', ['keywords', 'mixed_intrinsics',
                                       'mixed_resolution'])
def test_legacy_keyword_sidecar_calls_keep_the_original_xmp(tmp_path, call_style):
    (tmp_path / 'L_1.jpg').write_bytes(b'fixture')
    matrix = [[1000, 0, 1050], [0, 1000, 980], [0, 0, 1]]
    size = (2000, 2000)
    if call_style == 'keywords':
        counts = cs.write_sidecars(image_dir=str(tmp_path), intrinsics=matrix,
                                   resolution=size, prior='approximate',
                                   distortion='division')
    elif call_style == 'mixed_intrinsics':
        counts = cs.write_sidecars(str(tmp_path), intrinsics=matrix,
                                   resolution=size)
    else:
        counts = cs.write_sidecars(str(tmp_path), matrix, resolution=size)
    assert counts == {'L': 1, 'R': 0, 'skipped_no_eye': 0}
    assert (tmp_path / 'L_1.xmp').read_text() == (
        '<x:xmpmeta xmlns:x="adobe:ns:meta/">\n'
        '  <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">\n'
        '    <rdf:Description xcr:Version="4"\n'
        '       xcr:CalibrationPrior="approximate" xcr:CalibrationGroup="0"\n'
        '       xcr:DistortionGroup="0" xcr:DistortionModel="division"\n'
        '       xcr:DistortionCoeficients="0 0 0 0 0 0"\n'
        '       xcr:FocalLength35mm="18.0000000000" xcr:Skew="0"\n'
        '       xcr:AspectRatio="1" xcr:PrincipalPointU="0.0250000000"\n'
        '       xcr:PrincipalPointV="-0.0100000000"\n'
        '       xmlns:xcr="http://www.capturingreality.com/ns/xcr/1.1#">\n'
        '    </rdf:Description>\n'
        '  </rdf:RDF>\n'
        '</x:xmpmeta>\n')


def test_duplicate_legacy_keyword_is_refused_before_writing(tmp_path):
    (tmp_path / 'L_1.jpg').write_bytes(b'fixture')
    with pytest.raises(TypeError, match='multiple values.*image_dir'):
        cs.write_sidecars(str(tmp_path), [[1000, 0, 1050], [0, 1000, 980],
                                         [0, 0, 1]], (2000, 2000),
                           image_dir=str(tmp_path))
    assert not (tmp_path / 'L_1.xmp').exists()
