#!/usr/bin/env python3
"""Calibration delivery for the ILX-LR1 cameras (modules/calibration_sidecars.py).

Pins the calibration decision table - including the 2026-08-20 Wild Sync
runs (4752x3168 images at 29 mm and 24 mm), which must come out as
``groups`` - the exact sidecar text per mode, the exact ``.rscmd`` text
with CRLF line endings, and the sidecar hygiene that must never write a
form other than the decided one.

Offline; Pillow is needed only for the EXIF reader test.

Run:  py -3.13 -m pytest tests/test_calibration_sidecars.py
"""
from __future__ import annotations

import dataclasses
import json
import os
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
sys.path.insert(0, REPO_ROOT)

from modules import calibration_sidecars as cs  # noqa: E402
from modules import camera_registry  # noqa: E402
from modules.calibration_sidecars import (CalibrationRefused,  # noqa: E402
                                          ImageGeometry, decide_calibration)

LEFT = camera_registry.CAMERAS['ilx_left']
RIGHT = camera_registry.CAMERAS['ilx_right']
RIG = camera_registry.rig_for('ilx_left')
UNCONFIRMED = dataclasses.replace(RIG, node_assignment_confirmed=False)

CALIBRATED = ImageGeometry(4096, 3000, 16.0)
# The 2026-08-20 transects: 4752x3168 card JPEGs, cam1 at 29 mm, cam2 at 24 mm.
AUG20_CAM1 = ImageGeometry(4752, 3168, 29.0)
AUG20_CAM2 = ImageGeometry(4752, 3168, 24.0)

LEFT_PRIOR = (
    '<x:xmpmeta xmlns:x="adobe:ns:meta/">\n'
    '  <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">\n'
    '    <rdf:Description xcr:Version="4"\n'
    '       xcr:CalibrationPrior="initial" xcr:CalibrationGroup="7"\n'
    '       xcr:DistortionGroup="7" xcr:DistortionModel="brown3"\n'
    '       xcr:DistortionCoeficients="-0.1505106432 0.0167230022 0 0 0 0"\n'
    '       xcr:FocalLength35mm="17.72584" xcr:Skew="0"\n'
    '       xcr:AspectRatio="1" xcr:PrincipalPointU="0.01871054"\n'
    '       xcr:PrincipalPointV="-0.02501735"\n'
    '       xmlns:xcr="http://www.capturingreality.com/ns/xcr/1.1#">\n'
    '    </rdf:Description>\n'
    '  </rdf:RDF>\n'
    '</x:xmpmeta>\n')

RIGHT_PRIOR = (
    '<x:xmpmeta xmlns:x="adobe:ns:meta/">\n'
    '  <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">\n'
    '    <rdf:Description xcr:Version="4"\n'
    '       xcr:CalibrationPrior="initial" xcr:CalibrationGroup="8"\n'
    '       xcr:DistortionGroup="8" xcr:DistortionModel="brown3"\n'
    '       xcr:DistortionCoeficients="-0.1423330719 0.0154426178 0 0 0 0"\n'
    '       xcr:FocalLength35mm="17.72584" xcr:Skew="0"\n'
    '       xcr:AspectRatio="1" xcr:PrincipalPointU="0.06111425"\n'
    '       xcr:PrincipalPointV="-0.07754206"\n'
    '       xmlns:xcr="http://www.capturingreality.com/ns/xcr/1.1#">\n'
    '    </rdf:Description>\n'
    '  </rdf:RDF>\n'
    '</x:xmpmeta>\n')

LEFT_GROUPS = (
    '<x:xmpmeta xmlns:x="adobe:ns:meta/">\n'
    '  <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">\n'
    '    <rdf:Description xcr:Version="4"\n'
    '       xcr:CalibrationGroup="7" xcr:DistortionGroup="7"\n'
    '       xcr:DistortionModel="brown3"\n'
    '       xmlns:xcr="http://www.capturingreality.com/ns/xcr/1.1#">\n'
    '    </rdf:Description>\n'
    '  </rdf:RDF>\n'
    '</x:xmpmeta>\n')

RIGHT_GROUPS = LEFT_GROUPS.replace('"7"', '"8"')

POSE = '<x:xmpmeta><xcr:Position>1 2 3</xcr:Position></x:xmpmeta>'


# -------------------------------------------------------- decision table

@pytest.mark.parametrize('rig,geometries,asserted,mode', [
    # every condition holds
    (RIG, [CALIBRATED], False, 'prior'),
    (RIG, [ImageGeometry(8192, 6000, 16.0)], False, 'prior'),
    (RIG, [ImageGeometry(4100, 3000, 16.2)], False, 'prior'),
    (RIG, [CALIBRATED, CALIBRATED], False, 'prior'),
    # focal unknown or different, unless asserted
    (RIG, [ImageGeometry(4096, 3000, None)], False, 'groups'),
    (RIG, [ImageGeometry(4096, 3000, None)], True, 'prior'),
    (RIG, [ImageGeometry(4096, 3000, 29.0)], False, 'groups'),
    (RIG, [ImageGeometry(4096, 3000, 29.0)], True, 'prior'),
    # aspect ratio differs - asserting the focal cannot help
    (RIG, [ImageGeometry(4096, 2700, 16.0)], False, 'groups'),
    (RIG, [AUG20_CAM1], True, 'groups'),
    (RIG, [CALIBRATED, AUG20_CAM1], False, 'groups'),
    (RIG, [], True, 'groups'),
    (RIG, [ImageGeometry(0, 0, 16.0)], True, 'groups'),
    # node assignment not confirmed
    (UNCONFIRMED, [CALIBRATED], False, 'groups'),
    (UNCONFIRMED, [CALIBRATED], True, 'groups'),
])
def test_auto_decision_table(rig, geometries, asserted, mode):
    decision = decide_calibration(LEFT, rig, geometries, 'auto', asserted)
    assert decision.mode == mode
    assert decision.requested == 'auto'
    assert bool(decision.warnings) == (mode == 'groups')
    assert decision.node_assignment_confirmed is rig.node_assignment_confirmed


@pytest.mark.parametrize('camera,geometry', [(LEFT, AUG20_CAM1),
                                             (RIGHT, AUG20_CAM2)])
def test_the_2026_08_20_runs_come_out_as_groups(camera, geometry):
    decision = decide_calibration(camera, RIG, [geometry] * 3)
    assert decision.mode == 'groups'
    focal = f'{geometry.focal_mm:g} mm'
    for text in (decision.reason, decision.warnings[0]):
        assert '4752x3168' in text and '4096x3000' in text
        assert f'EXIF focal {focal}' in text and 'calibration 16 mm' in text
    assert decision.image_sizes == ((4752, 3168),)
    assert decision.exif_focals_mm == (geometry.focal_mm,)
    record = decision.as_dict()
    assert json.loads(json.dumps(record)) == record
    assert record['mode'] == 'groups' and record['camera'] == camera.key
    assert record['calibration_image_size'] == [4096, 3000]
    assert record['calibration_focal_mm'] == 16.0


def test_the_warning_names_the_unconfirmed_node_assignment():
    decision = decide_calibration(RIGHT, UNCONFIRMED, [CALIBRATED])
    assert decision.mode == 'groups'
    assert 'node_assignment_confirmed is false' in decision.warnings[0]


def test_explicit_groups_and_off_are_honoured():
    for rig in (RIG, UNCONFIRMED):
        for geometries in ([CALIBRATED], [AUG20_CAM1], []):
            assert decide_calibration(LEFT, rig, geometries, 'groups').mode == 'groups'
            off = decide_calibration(LEFT, rig, geometries, 'off')
            assert off.mode == 'off' and not off.warnings


def test_explicit_prior_is_honoured_when_only_the_focal_differs():
    decision = decide_calibration(LEFT, RIG, [ImageGeometry(4096, 3000, 29.0)],
                                  'prior')
    assert decision.mode == 'prior'
    assert 'EXIF focal 29 mm vs calibration 16 mm' in decision.warnings[0]
    clean = decide_calibration(LEFT, RIG, [CALIBRATED], 'prior')
    assert clean.mode == 'prior' and not clean.warnings


@pytest.mark.parametrize('rig,geometries,match', [
    (UNCONFIRMED, [CALIBRATED], 'node assignment'),
    (RIG, [AUG20_CAM1], '4752x3168'),
    (RIG, [CALIBRATED, AUG20_CAM2], '4752x3168'),
    (RIG, [], 'no image size'),
])
def test_explicit_prior_refuses_an_unsafe_calibration(rig, geometries, match):
    with pytest.raises(CalibrationRefused, match=match):
        decide_calibration(LEFT, rig, geometries, 'prior', focal_asserted=True)


def test_an_unknown_mode_is_refused():
    with pytest.raises(ValueError, match='calibration mode'):
        decide_calibration(LEFT, RIG, [CALIBRATED], 'locked')


# -------------------------------------------------------------- sidecars

def test_prior_sidecars_exact_text():
    assert cs.sidecar_xmp(LEFT, 'prior') == LEFT_PRIOR
    assert cs.sidecar_xmp(RIGHT, 'prior') == RIGHT_PRIOR


def test_groups_sidecars_exact_text():
    assert cs.sidecar_xmp(LEFT, 'groups') == LEFT_GROUPS
    assert cs.sidecar_xmp(RIGHT, 'groups') == RIGHT_GROUPS


@pytest.mark.parametrize('text', [LEFT_PRIOR, RIGHT_PRIOR, LEFT_GROUPS])
def test_sidecars_carry_no_pose_rig_or_fixed_prior(text):
    for absent in ('Position', 'Rotation', 'PosePrior', 'xcr:Rig', 'locked',
                   'exact', 'Camera:', 'xcr/1.0', 'approximate'):
        assert absent not in text


def test_off_writes_no_sidecar():
    with pytest.raises(ValueError):
        cs.sidecar_xmp(LEFT, 'off')


def test_opencv_coefficients_go_to_realityscan_brown_slots():
    # OpenCV (k1, k2, p1, p2, k3) -> RealityScan (k1, k2, k3, k4, t1, t2)
    assert cs.rs_distortion_coefficients((1, 2, 3, 4, 5)) == (1, 2, 5, 0, 4, 3)


@pytest.mark.parametrize('image,sidecar', [
    ('Cam1_20260820_192542.42.card.JPG', 'Cam1_20260820_192542.42.card.xmp'),
    ('Cam1_20260820_192542.42.jpg', 'Cam1_20260820_192542.42.xmp'),
    (os.path.join('zone_1', 'ilx_left', 'Cam1_a.jpeg'),
     os.path.join('zone_1', 'ilx_left', 'Cam1_a.xmp')),
])
def test_sidecar_takes_the_image_stem(image, sidecar):
    assert cs.sidecar_path(image) == sidecar


def _images(tmp_path, names):
    for name in names:
        (tmp_path / name).write_bytes(b'jpeg')
    return [str(tmp_path / name) for name in names]


def test_write_sidecars_writes_each_decided_form(tmp_path):
    images = _images(tmp_path, ['Cam2_b.card.JPG', 'Cam1_a.card.JPG',
                                'other.jpg'])
    entries = cs.write_sidecars(images, {'ilx_left': 'prior',
                                         'ilx_right': 'groups'})
    root = os.path.abspath(str(tmp_path))
    assert entries == [
        (os.path.join(root, 'Cam1_a.card.JPG'), os.path.join(root, 'Cam1_a.card.xmp')),
        (os.path.join(root, 'Cam2_b.card.JPG'), os.path.join(root, 'Cam2_b.card.xmp')),
        (os.path.join(root, 'other.jpg'), None),
    ]
    assert (tmp_path / 'Cam1_a.card.xmp').read_bytes() == LEFT_PRIOR.encode()
    assert (tmp_path / 'Cam2_b.card.xmp').read_bytes() == RIGHT_GROUPS.encode()
    assert not (tmp_path / 'other.xmp').exists()


def test_write_sidecars_writes_nothing_for_off_or_no_decision(tmp_path):
    images = _images(tmp_path, ['Cam1_a.jpg', 'Cam2_a.jpg'])
    entries = cs.write_sidecars(images, {'ilx_left': 'off'})
    assert [xmp for _image, xmp in entries] == [None, None]
    assert sorted(os.listdir(tmp_path)) == ['Cam1_a.jpg', 'Cam2_a.jpg']


def test_write_sidecars_refuses_a_shared_sidecar_name(tmp_path):
    images = _images(tmp_path, ['Cam1_a.jpg', 'Cam1_a.png'])
    with pytest.raises(ValueError, match='share the sidecar'):
        cs.write_sidecars(images, {'ilx_left': 'groups'})
    assert not (tmp_path / 'Cam1_a.xmp').exists()


def test_write_sidecars_refuses_an_undecidable_mode(tmp_path):
    with pytest.raises(ValueError, match='ilx_left'):
        cs.write_sidecars([], {'ilx_left': 'auto'})


# ----------------------------------------------------------------- rscmd

def test_rscmd_exact_text_with_crlf(tmp_path):
    a = str(tmp_path / 'zone 1' / 'ilx_left' / 'Cam1_a.card.JPG')
    a_xmp = str(tmp_path / 'zone 1' / 'ilx_left' / 'Cam1_a.card.xmp')
    b = str(tmp_path / 'zone 1' / 'ilx_right' / 'Cam2_a.card.JPG')
    c = str(tmp_path / 'zone 1' / 'other' / 'x.jpg')
    path = cs.write_rscmd(str(tmp_path / 'add.rscmd'),
                          [(c, None), (b, None), (a, a_xmp)])
    expected = (
        f'-addImageWithCalibration "{os.path.abspath(a)}" "{os.path.abspath(a_xmp)}"\r\n'
        f'-add "{os.path.abspath(b)}"\r\n'
        f'-add "{os.path.abspath(c)}"\r\n')
    data = open(path, 'rb').read()
    assert data == expected.encode('utf-8')
    assert data.count(b'\n') == data.count(b'\r\n') == 3
    assert path == os.path.abspath(str(tmp_path / 'add.rscmd'))


def test_rscmd_order_is_deterministic(tmp_path):
    entries = [(str(tmp_path / f'Cam{c}_{i}.jpg'), None)
               for i in range(5) for c in (1, 2)]
    assert cs.rscmd_text(entries) == cs.rscmd_text(list(reversed(entries)))


def test_rscmd_paths_are_absolute(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    text = cs.rscmd_text([('Cam1_a.jpg', 'Cam1_a.xmp')])
    assert text == (f'-addImageWithCalibration "{os.path.join(os.getcwd(), "Cam1_a.jpg")}" '
                    f'"{os.path.join(os.getcwd(), "Cam1_a.xmp")}"\r\n')


@pytest.mark.parametrize('bad', ['Cam1_"a".jpg', 'Cam1_a\n.jpg', 'Cam1_$(arg1).jpg'])
def test_rscmd_refuses_a_path_it_cannot_quote(tmp_path, bad):
    with pytest.raises(ValueError, match='rscmd'):
        cs.rscmd_text([(str(tmp_path / bad), None)])


def test_rscmd_refuses_an_image_listed_twice(tmp_path):
    image = str(tmp_path / 'Cam1_a.jpg')
    with pytest.raises(ValueError, match='twice'):
        cs.rscmd_text([(image, None), (image, str(tmp_path / 'Cam1_a.xmp'))])


def test_sidecars_and_rscmd_together(tmp_path):
    images = _images(tmp_path, ['Cam1_a.jpg', 'Cam2_a.jpg'])
    entries = cs.write_sidecars(images, {'ilx_left': 'groups',
                                         'ilx_right': 'off'})
    root = os.path.abspath(str(tmp_path))
    assert cs.rscmd_text(entries) == (
        f'-addImageWithCalibration "{os.path.join(root, "Cam1_a.jpg")}" '
        f'"{os.path.join(root, "Cam1_a.xmp")}"\r\n'
        f'-add "{os.path.join(root, "Cam2_a.jpg")}"\r\n')


# --------------------------------------------------------------- hygiene

def test_ensure_without_modes_writes_nothing(tmp_path):
    _images(tmp_path, ['Cam1_a.jpg', 'Cam2_a.jpg'])
    assert cs.ensure_calibration_sidecars(str(tmp_path)) == (0, 0)
    assert cs.ensure_calibration_sidecars(str(tmp_path), {}) == (0, 0)
    assert sorted(os.listdir(tmp_path)) == ['Cam1_a.jpg', 'Cam2_a.jpg']


def test_ensure_restores_only_the_decided_form(tmp_path):
    _images(tmp_path, ['Cam1_a.jpg', 'Cam1_b.JPG', 'Cam2_a.jpg', 'other.jpg'])
    (tmp_path / 'Cam1_b.xmp').write_text('kept', encoding='utf-8')
    created, unknown = cs.ensure_calibration_sidecars(
        str(tmp_path), {'ilx_left': 'prior', 'ilx_right': 'off'})
    assert (created, unknown) == (1, 1)
    assert (tmp_path / 'Cam1_a.xmp').read_text(encoding='utf-8') == LEFT_PRIOR
    assert (tmp_path / 'Cam1_b.xmp').read_text(encoding='utf-8') == 'kept'
    assert not (tmp_path / 'Cam2_a.xmp').exists()
    # Idempotent on a complete tree.
    assert cs.ensure_calibration_sidecars(
        str(tmp_path), {'ilx_left': 'prior', 'ilx_right': 'off'}) == (0, 1)


def test_sanitize_rewrites_a_pose_sidecar_to_the_decided_form(tmp_path):
    (tmp_path / 'Cam1_a.xmp').write_text(POSE, encoding='utf-8')
    (tmp_path / 'Cam2_a.xmp').write_text(POSE, encoding='utf-8')
    assert cs.sanitize_and_census(
        str(tmp_path), {'ilx_left': 'groups', 'ilx_right': 'prior'}) == (2, 2, 0)
    assert (tmp_path / 'Cam1_a.xmp').read_text(encoding='utf-8') == LEFT_GROUPS
    assert (tmp_path / 'Cam2_a.xmp').read_text(encoding='utf-8') == RIGHT_PRIOR


def test_sanitize_deletes_a_pose_sidecar_it_has_no_form_for(tmp_path):
    (tmp_path / 'Cam1_a.xmp').write_text(POSE, encoding='utf-8')
    (tmp_path / 'Cam2_a.xmp').write_text(POSE, encoding='utf-8')
    (tmp_path / '00000.xmp').write_text(POSE, encoding='utf-8')
    (tmp_path / 'stranger.xmp').write_text(POSE, encoding='utf-8')
    assert cs.sanitize_and_census(str(tmp_path), {'ilx_left': 'off'}) == (4, 0, 1)
    assert os.listdir(tmp_path) == []


def test_sanitize_never_touches_a_sidecar_without_a_pose(tmp_path):
    (tmp_path / 'Cam1_a.xmp').write_text(LEFT_PRIOR, encoding='utf-8')
    (tmp_path / 'Cam2_a.xmp').write_text('<x:xmpmeta/>', encoding='utf-8')
    assert cs.sanitize_and_census(str(tmp_path)) == (0, 0, 0)
    assert (tmp_path / 'Cam1_a.xmp').read_text(encoding='utf-8') == LEFT_PRIOR
    assert (tmp_path / 'Cam2_a.xmp').read_text(encoding='utf-8') == '<x:xmpmeta/>'


def test_off_removes_only_this_pipelines_calibration_sidecars(tmp_path):
    """Switching a camera to off after prior/groups must not leave the
    earlier calibration sidecars for -addFolder to pick up; anything the
    pipeline did not write stays. Sidecars are written byte for byte as the
    pipeline writes them (LF line endings)."""
    _images(tmp_path, ['Cam1_a.jpg', 'Cam1_b.jpg', 'Cam1_c.jpg',
                       'Cam1_d.jpg', 'Cam2_a.jpg', 'other.jpg'])
    (tmp_path / 'Cam1_a.xmp').write_bytes(LEFT_PRIOR.encode())
    (tmp_path / 'Cam1_b.xmp').write_bytes(LEFT_GROUPS.encode())
    (tmp_path / 'Cam1_c.xmp').write_bytes(POSE.encode())
    (tmp_path / 'Cam1_d.xmp').write_text(LEFT_GROUPS.replace('"7"', '"9"'),
                                         encoding='utf-8')
    (tmp_path / 'Cam2_a.xmp').write_bytes(RIGHT_GROUPS.encode())
    (tmp_path / 'other.xmp').write_bytes(LEFT_PRIOR.encode())
    removed, kept = cs.remove_calibration_sidecars(
        str(tmp_path), {'ilx_left': 'off', 'ilx_right': 'groups'})
    assert (removed, kept) == (2, 2)
    assert not (tmp_path / 'Cam1_a.xmp').exists()
    assert not (tmp_path / 'Cam1_b.xmp').exists()
    assert (tmp_path / 'Cam1_c.xmp').read_text(encoding='utf-8') == POSE
    assert (tmp_path / 'Cam1_d.xmp').exists()
    assert (tmp_path / 'Cam2_a.xmp').read_text(encoding='utf-8') == RIGHT_GROUPS
    assert (tmp_path / 'other.xmp').read_text(encoding='utf-8') == LEFT_PRIOR
    assert cs.remove_calibration_sidecars(
        str(tmp_path), {'ilx_left': 'off', 'ilx_right': 'groups'}) == (0, 2)


def test_remove_without_an_off_decision_touches_nothing(tmp_path):
    _images(tmp_path, ['Cam1_a.jpg', 'Cam2_a.jpg'])
    (tmp_path / 'Cam1_a.xmp').write_bytes(LEFT_PRIOR.encode())
    (tmp_path / 'Cam2_a.xmp').write_bytes(RIGHT_GROUPS.encode())
    assert cs.remove_calibration_sidecars(str(tmp_path)) == (0, 0)
    assert cs.remove_calibration_sidecars(
        str(tmp_path), {'ilx_left': 'prior', 'ilx_right': 'groups'}) == (0, 0)
    assert sorted(os.listdir(tmp_path)) == ['Cam1_a.jpg', 'Cam1_a.xmp',
                                            'Cam2_a.jpg', 'Cam2_a.xmp']


# ------------------------------------------------------------ EXIF reader

def test_read_image_geometry_reads_size_and_exif_focal(tmp_path):
    image_mod = pytest.importorskip('PIL.Image')
    exif = image_mod.Exif()
    exif.get_ifd(0x8769)[0x920A] = 29.0
    image_mod.new('RGB', (64, 48)).save(str(tmp_path / 'Cam1_a.jpg'), exif=exif)
    image_mod.new('RGB', (40, 30)).save(str(tmp_path / 'Cam2_a.jpg'))
    assert cs.read_image_geometry(str(tmp_path / 'Cam1_a.jpg')) == \
        ImageGeometry(64, 48, 29.0)
    assert cs.read_image_geometry(str(tmp_path / 'Cam2_a.jpg')) == \
        ImageGeometry(40, 30, None)
