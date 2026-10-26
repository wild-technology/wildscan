"""Sony intake keeps pixels, calibration axes and asserted focal provenance aligned.

Small synthetic JPEG fixtures only; no image processing or RealityScan run.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from PIL import Image

from modules import camera_registry
from modules.calibration_sidecars import ImageGeometry, read_image_geometry
from modules.wildsync_intake.intake import IntakeError, IntakeOptions, run_intake
from tests.test_wildsync_intake import (
    CALIBRATED_CARD, FRAMES, QUIET, build_run, jpeg, tree,
)


def _card_without_focal():
    return {cam: ((1024, 750), None, None) for cam in ('cam1', 'cam2')}


@pytest.mark.parametrize('orientation', [0, 2, 3, 4, 5, 6, 7, 8, 9])
@pytest.mark.parametrize('mode', ['auto', 'off'])
def test_non_upright_sony_source_is_refused_before_any_writes(
        tmp_path, orientation, mode):
    run = build_run(tmp_path / 'runs', card=CALIBRATED_CARD)
    source = run / 'cam1' / f'Cam1_{FRAMES[0]}.card.JPG'
    with Image.open(source) as image:
        image.load()
        exif = image.getexif()
        exif[0x0112] = orientation
        image.save(source, exif=exif)
    assert read_image_geometry(str(source)).orientation == orientation
    before = tree(run)
    workspace = tmp_path / 'workspace'
    with pytest.raises(IntakeError, match=f'EXIF Orientation {orientation}.*not upright') as caught:
        run_intake([str(run)], str(workspace), IntakeOptions(calibration=mode),
                   log=QUIET)
    assert 'do not simply change the EXIF tag' in str(caught.value)
    assert tree(run) == before
    assert not workspace.exists()


def test_geometry_callers_keep_the_upright_default():
    assert ImageGeometry(4096, 3000, 16.0).orientation == 1


def test_focal_assertion_supplies_missing_exif_only_for_applicable_prior(tmp_path):
    run = build_run(tmp_path / 'runs', card=_card_without_focal())
    before = tree(run)
    result = run_intake([str(run)], str(tmp_path / 'workspace'),
                        IntakeOptions(calibration='prior', focal_asserted=True), log=QUIET)
    for key in ('ilx_left', 'ilx_right'):
        entry = result.manifest['calibration'][key]
        assert entry['mode'] == 'prior'
        assert entry['focal_asserted'] is True
        assert entry['exif_focal_mm'] is None
        assert entry['exif_focal_35mm'] is None
        assert entry['exif_focals_mm'] == [None]
        assert entry['images_without_exif_focal'] == 3
        assert entry['focal_override_mm'] is None
        assert entry['starting_focal_35mm'] == \
            camera_registry.CAMERAS[key].focal_length_35mm
        assert entry['starting_focal_source'] == 'asserted_calibration'
    assert tree(run) == before


def test_assertion_preserves_known_exif_when_other_images_lack_it(tmp_path):
    run = build_run(tmp_path / 'runs', card=_card_without_focal())
    jpeg(run / 'cam1' / f'Cam1_{FRAMES[0]}.card.JPG', (1024, 750), 16.0, 16)
    result = run_intake([str(run)], str(tmp_path / 'workspace'),
                        IntakeOptions(calibration='prior', focal_asserted=True), log=QUIET)
    entry = result.manifest['calibration']['ilx_left']
    assert entry['exif_focal_mm'] == 16.0
    assert entry['exif_focal_35mm'] == 16.0
    assert entry['exif_focals_mm'] == [16.0, None]
    assert entry['images_without_exif_focal'] == 2
    assert entry['starting_focal_source'] == 'asserted_calibration'
    assert entry['focal_override_mm'] is None


def test_assertion_does_not_replace_known_observed_focal(tmp_path):
    card = {cam: ((1024, 750), 29.0, 29) for cam in ('cam1', 'cam2')}
    run = build_run(tmp_path / 'runs', card=card)
    result = run_intake([str(run)], str(tmp_path / 'workspace'),
                        IntakeOptions(calibration='prior', focal_asserted=True), log=QUIET)
    entry = result.manifest['calibration']['ilx_left']
    assert entry['exif_focal_mm'] == 29.0
    assert entry['exif_focal_35mm'] == 29.0
    assert entry['focal_override_mm'] is None
    assert entry['starting_focal_source'] == 'calibration'


@pytest.mark.parametrize('gate,mode,match', [
    ('aspect', 'auto', 'no EXIF focal length'),
    ('aspect', 'prior', 'calibration prior cannot be applied'),
    ('assignment', 'auto', 'no EXIF focal length'),
    ('assignment', 'prior', 'calibration prior cannot be applied'),
    ('none', 'groups', 'no EXIF focal length'),
    ('none', 'off', 'no EXIF focal length'),
])
def test_assertion_does_not_bypass_other_prior_gates(tmp_path, gate, mode, match):
    card = _card_without_focal()
    if gate == 'aspect':
        card = {cam: ((48, 32), None, None) for cam in ('cam1', 'cam2')}
    registry = camera_registry.REGISTRY
    if gate == 'assignment':
        data = json.loads(Path(camera_registry.CAMERAS_JSON).read_text())
        data['rigs']['ilx_lr1_stereo']['node_assignment_confirmed'] = False
        registry_path = tmp_path / 'cameras.json'
        registry_path.write_text(json.dumps(data))
        registry = camera_registry.load_registry(str(registry_path))
    run = build_run(tmp_path / 'runs', card=card)
    workspace = tmp_path / 'workspace'
    with pytest.raises(IntakeError, match=match):
        run_intake([str(run)], str(workspace),
                   IntakeOptions(focal_asserted=True, calibration=mode),
                   log=QUIET, registry=registry)
    assert not workspace.exists()


def test_assertion_does_not_suppress_an_observed_zoom_change(tmp_path):
    run = build_run(tmp_path / 'runs', card=CALIBRATED_CARD)
    jpeg(run / 'cam1' / f'Cam1_{FRAMES[0]}.card.JPG', (1024, 750), 24.0, 24)
    workspace = tmp_path / 'workspace'
    with pytest.raises(IntakeError, match='EXIF focal length differs'):
        run_intake([str(run)], str(workspace),
                   IntakeOptions(focal_asserted=True), log=QUIET)
    assert not workspace.exists()
