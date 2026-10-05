#!/usr/bin/env python3
"""Wild Sync Intake (modules/wildsync_intake/).

A Wild Sync run directory is built in ``tmp_path``: two camera nodes, a few
frames each with all three image variants and the Wild Sync sidecar, the
verbatim 23-column ``flight_log.csv`` header with CRLF line endings (as Wild
Sync writes it), and edge rows (empty depth, empty heading_imu, empty pitch,
a large time_err_ms). Pinned here: the golden combined flight log, the
orientation and altitude functions, every fail-closed gate and every
warn-only case of the intake, the calibration decision made from the
original images (including the 2026-08-20 4752x3168 case), the module
wrapper, and that preprocessing and batching keep names such as
``Cam1_20260820_192542.42.card.JPG``.

Offline; needs Pillow (and OpenCV for the preprocessing test).

Run:  py -3.13 -m pytest tests/test_wildsync_intake.py
"""
from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path

import pytest
from PIL import Image

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from module_base.parameter import Parameter
from modules import camera_registry
from modules.flight_logs import FLIGHT_LOG_HEADER
from modules.wildsync_intake import intake
from modules.wildsync_intake.intake import (
    IntakeError,
    IntakeOptions,
    run_intake,
)
from modules.wildsync_intake.wildsync_intake import WildSyncIntake

QUIET = logging.getLogger('wildsync-intake-test')
QUIET.addHandler(logging.NullHandler())
QUIET.propagate = False

# Verbatim, as Wild Sync writes it.
CSV_HEADER = ('filename,datetime,lat,long,xutm,yutm,utm_zone,'
              'depth_from_xplore9,pitch,roll,yaw,heading_mag_xplore,'
              'heading_imu,ax_g,ay_g,az_g,gx_dps,gy_dps,gz_dps,imu_temp_c,'
              'capture_source,time_source,time_err_ms')

RUN_ID = '260820_1925_transect-01'
FRAMES = ('20260820_192542.42', '20260820_192542.92', '20260820_192543.42')
X, Y = 294952.55, 4588707.83          # zone 19T, lat 41.4237752 lon -71.4537835

# Card JPEGs with the 2026-08-20 aspect ratio (4752:3168 = 1.5) and EXIF
# focal lengths, small enough to write quickly.
AUG20_CARD = {'cam1': ((48, 32), 29.0), 'cam2': ((48, 32), 24.0)}
CALIBRATED_CARD = {'cam1': ((1024, 750), 16.0), 'cam2': ((1024, 750), 16.0)}
REVIEW_SIZE = (32, 21)
WILDSYNC_XMP = ('<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF '
                'xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
                '<rdf:Description wildsync:frame="x"/></rdf:RDF></x:xmpmeta>')


def row(cam: str, frame: str, **over) -> dict:
    """One flight_log.csv row, shaped like the real 2026-08-20 logs."""
    cells = {
        'filename': f'{cam.capitalize()}_{frame}.jpg',
        'datetime': '260820_' + frame.split('_', 1)[1],
        'lat': '41.4237752', 'long': '-71.4537835',
        'xutm': f'{X}', 'yutm': f'{Y}', 'utm_zone': '19T',
        'depth_from_xplore9': '', 'pitch': '2.0', 'roll': '-1.0',
        'yaw': '100.0', 'heading_mag_xplore': '', 'heading_imu': '95.0',
        'ax_g': '-0.0376', 'ay_g': '-0.1753', 'az_g': '1.0088',
        'gx_dps': '-5.737', 'gy_dps': '4.395', 'gz_dps': '-1.404',
        'imu_temp_c': '37.4', 'capture_source': 'gpio_edge',
        'time_source': 'jetson', 'time_err_ms': '0.0',
    }
    cells.update({k: str(v) for k, v in over.items()})
    return cells


def edge_rows() -> dict[str, list[dict]]:
    """Static fix, empty depth except one row, one row each without
    heading_imu (falls back to yaw), without pitch (no orientation) and
    with a large time error."""
    return {
        'cam1': [row('cam1', FRAMES[0]),
                 row('cam1', FRAMES[1], heading_imu=''),
                 row('cam1', FRAMES[2], pitch='')],
        'cam2': [row('cam2', FRAMES[0]),
                 row('cam2', FRAMES[1], depth_from_xplore9='12.5'),
                 row('cam2', FRAMES[2], time_err_ms='120.0')],
    }


def write_log(path: Path, rows: list[dict], header: str = CSV_HEADER) -> None:
    columns = header.split(',')
    lines = [header] + [','.join(r.get(c, '') for c in columns) for r in rows]
    path.write_bytes(('\r\n'.join(lines) + '\r\n').encode('utf-8'))


def jpeg(path: Path, size, focal: float | None = None) -> None:
    exif = Image.Exif()
    if focal is not None:
        exif.get_ifd(0x8769)[0x920A] = focal
    Image.new('RGB', size, (40, 90, 120)).save(str(path), exif=exif)


def build_run(root: Path, name: str = RUN_ID, rows=None, card=None,
              frames=FRAMES) -> Path:
    """A Wild Sync run directory with every file Wild Sync writes."""
    run = root / name
    run.mkdir(parents=True)
    (run / 'run.json').write_text(
        json.dumps({'run_id': name, 'time_source': 'jetson'}),
        encoding='utf-8')
    for extra in ('events.log', 'nmea_raw.log', 'ingest_manifest.csv'):
        (run / extra).write_text('', encoding='utf-8')
    rows = edge_rows() if rows is None else rows
    card = AUG20_CARD if card is None else card
    for cam in ('cam1', 'cam2'):
        node = run / cam
        node.mkdir()
        for frame in frames:
            stem = f'{cam.capitalize()}_{frame}'
            jpeg(node / f'{stem}.jpg', REVIEW_SIZE)
            jpeg(node / f'{stem}.card.JPG', *card[cam])
            (node / f'{stem}.ARW').write_bytes(b'II*\x00raw')
            (node / f'{stem}.xmp').write_text(WILDSYNC_XMP, encoding='utf-8')
        write_log(node / 'flight_log.csv', rows[cam])
    return run


def tree(path: Path) -> dict[str, bytes]:
    return {str(p.relative_to(path)): p.read_bytes()
            for p in sorted(path.rglob('*')) if p.is_file()}


@pytest.fixture
def run_dir(tmp_path):
    return build_run(tmp_path / 'runs')


@pytest.fixture
def workspace(tmp_path):
    return tmp_path / 'workspace'


def _names(frames, cam, suffix='.card.JPG'):
    return [f'{cam.capitalize()}_{f}{suffix}' for f in frames]


# ------------------------------------------------------------- file names

@pytest.mark.parametrize('name,fid,variant', [
    ('Cam1_20260820_192542.42.jpg', 'Cam1_20260820_192542.42', 'review'),
    ('Cam1_20260820_192542.42.card.JPG', 'Cam1_20260820_192542.42', 'card'),
    ('cam2_20260820_192542.42.CARD.jpeg', 'cam2_20260820_192542.42', 'card'),
    ('Cam1_20260820_192542.42.ARW', 'Cam1_20260820_192542.42', 'raw'),
    ('Cam1_20260820_192542.42.xmp', 'Cam1_20260820_192542.42', 'sidecar'),
    (r'C:\run\cam1\Cam1_20260820_192542.42.JPG', 'Cam1_20260820_192542.42',
     'review'),
])
def test_frame_id_strips_only_the_wild_sync_suffix(name, fid, variant):
    assert intake.frame_id(name) == fid
    assert intake.image_variant(name) == variant


@pytest.mark.parametrize('name', ['Cam1_20260820_192542.42', 'flight_log.csv',
                                  '.jpg', 'Cam1_x.card.ARW', 'Cam1_x.png'])
def test_names_without_a_wild_sync_suffix_have_no_frame(name):
    assert intake.image_variant(name) is None


# ----------------------------------------------------- orientation prior

NADIR = camera_registry.mount_for('Cam1_x.jpg')


def test_ilx_mounts_are_nominal_nadir_without_a_lever_arm():
    for name in ('Cam1_x.jpg', 'Cam2_x.jpg'):
        mount = camera_registry.mount_for(name)
        assert mount.down_tilt_deg == 90.0 and mount.yaw_offset_deg == 0.0
        assert mount.lever_arm_m is None


@pytest.mark.parametrize('heading,pitch,roll,declination,expected', [
    (95.0, 2.0, -1.0, 0.0, (95.0, 2.0, -1.0)),       # nadir: pitch passes
    (350.0, 0.0, 0.0, 15.0, (5.0, 0.0, 0.0)),        # declination wraps
    (-10.0, -4.5, 3.0, 0.0, (350.0, -4.5, 3.0)),     # negative heading wraps
    (0.0, 0.0, 0.0, -2.5, (357.5, 0.0, 0.0)),
])
def test_orientation_prior_for_the_nadir_mount(heading, pitch, roll,
                                               declination, expected):
    assert intake.orientation_prior(heading, pitch, roll, NADIR,
                                    declination) == pytest.approx(expected)


def test_orientation_prior_composes_mount_tilt_and_yaw_offset():
    oblique = camera_registry.Mount(down_tilt_deg=60.0, yaw_offset_deg=90.0,
                                    pitch_accuracy_deg=15.0, lever_arm_m=None)
    # pitch = 90 + (imu pitch - tilt): a camera 60 deg below the forward
    # axis is 30 deg from nadir; the image top is turned 90 deg right.
    assert intake.orientation_prior(10.0, 5.0, 1.0, oblique, 2.0) == \
        pytest.approx((102.0, 35.0, 1.0))


@pytest.mark.parametrize('heading,pitch,roll', [
    (None, 2.0, -1.0), (95.0, None, -1.0), (95.0, 2.0, None),
    (float('nan'), 2.0, -1.0), (95.0, float('inf'), -1.0)])
def test_any_missing_angle_gives_no_orientation_prior(heading, pitch, roll):
    assert intake.orientation_prior(heading, pitch, roll, NADIR) == \
        (None, None, None)


@pytest.mark.parametrize('depth,surface,down,expected', [
    (12.5, 0.0, 0.0, -12.5),
    (-12.5, 0.0, 0.0, -12.5),       # sign of the depth cell does not matter
    (12.5, 0.0, 0.3, -12.8),        # lever arm down
    (None, 0.0, 0.3, 0.0),          # camera at the sea surface
    (None, -2.0, 0.0, -2.0),
])
def test_altitude_prior(depth, surface, down, expected):
    assert intake.altitude_prior(depth, surface, down) == pytest.approx(expected)


def test_auto_heading_takes_heading_imu_then_yaw():
    parsed = intake._parse_row('log', 2, list(row('cam1', FRAMES[0]).values()))
    assert intake.select_heading(parsed, 'auto') == (95.0, 'heading_imu')
    no_imu = intake._parse_row('log', 2, list(
        row('cam1', FRAMES[0], heading_imu='').values()))
    assert intake.select_heading(no_imu, 'auto') == (100.0, 'yaw')
    assert intake.select_heading(no_imu, 'heading_mag_xplore') == (None, None)
    neither = intake._parse_row('log', 2, list(
        row('cam1', FRAMES[0], heading_imu='', yaw='').values()))
    assert intake.select_heading(neither, 'auto') == (None, None)


# ------------------------------------------------------ the golden intake

GOLDEN_ROWS = (
    ('Cam1_20260820_192542.42.card.JPG;294952.550000;4588707.830000;0.000000;'
     '1000.000000;1000.000000;1.000000;95.000000;2.000000;-1.000000;'
     '15.000000;15.000000;15.000000'),
    ('Cam1_20260820_192542.92.card.JPG;294952.550000;4588707.830000;0.000000;'
     '1000.000000;1000.000000;1.000000;100.000000;2.000000;-1.000000;'
     '15.000000;15.000000;15.000000'),
    ('Cam1_20260820_192543.42.card.JPG;294952.550000;4588707.830000;0.000000;'
     '1000.000000;1000.000000;1.000000;;;;;;'),
    ('Cam2_20260820_192542.42.card.JPG;294952.550000;4588707.830000;0.000000;'
     '1000.000000;1000.000000;1.000000;95.000000;2.000000;-1.000000;'
     '15.000000;15.000000;15.000000'),
    ('Cam2_20260820_192542.92.card.JPG;294952.550000;4588707.830000;-12.500000;'
     '1000.000000;1000.000000;1.000000;95.000000;2.000000;-1.000000;'
     '15.000000;15.000000;15.000000'),
    ('Cam2_20260820_192543.42.card.JPG;294952.550000;4588707.830000;0.000000;'
     '1000.000000;1000.000000;1.000000;95.000000;2.000000;-1.000000;'
     '15.000000;15.000000;15.000000'),
)
GOLDEN_LOG = ''.join(line + '\n' for line in (FLIGHT_LOG_HEADER, *GOLDEN_ROWS))


def test_golden_intake_of_the_card_variant(run_dir, workspace):
    before = tree(run_dir)
    result = run_intake([str(run_dir)], str(workspace), log=QUIET)
    raw = workspace / 'raw_images'

    # the flight log: one, combined, zone-tagged, byte-exact
    assert Path(result.flight_log_path) == raw / 'flight_log_19T_UTM.txt'
    assert (raw / 'flight_log_19T_UTM.txt').read_bytes() == \
        GOLDEN_LOG.encode('utf-8')

    # the images: copied unchanged under raw_images/<camera key>/
    for cam, key in (('cam1', 'ilx_left'), ('cam2', 'ilx_right')):
        names = _names(FRAMES, cam)
        assert sorted(os.listdir(raw / key)) == sorted(names)
        for name in names:
            assert (raw / key / name).read_bytes() == \
                (run_dir / cam / name).read_bytes()
    assert sorted(os.listdir(raw)) == sorted([
        'flight_log_19T_UTM.txt', 'ilx_left', 'ilx_right',
        'wildsync_intake.json'])
    assert not list(raw.rglob('*.xmp')) and not list(raw.rglob('*.ARW'))

    # the run directory is only read
    assert tree(run_dir) == before

    manifest = json.loads((raw / 'wildsync_intake.json').read_text('utf-8'))
    assert manifest == result.manifest
    assert manifest['schema'] == 1 and manifest['status'] == 'complete'
    assert manifest['variant'] == 'card'
    assert manifest['flight_log'] == 'flight_log_19T_UTM.txt'
    assert manifest['flight_log_rows'] == 6
    assert manifest['utm_zone'] == '19T'
    assert manifest['cameras'] == {
        'ilx_left': {'images': 3, 'folder': 'ilx_left'},
        'ilx_right': {'images': 3, 'folder': 'ilx_right'}}
    assert manifest['images'] == {
        'matched': 6, 'copied': 6, 'reused': 0, 'unmatched_rows': 0,
        'unmatched_images': 0, 'match_rate_pct': 100.0,
        'min_match_rate_pct': 80.0}
    source, = manifest['sources']
    assert source['run_dir'] == str(run_dir) and source['run_id'] == RUN_ID
    assert source['static_fix'] is True and source['position_spread_m'] == 0.0
    assert [n['node'] for n in source['nodes']] == ['cam1', 'cam2']
    assert [n['camera'] for n in source['nodes']] == ['ilx_left', 'ilx_right']
    assert manifest['static_fix'] is True
    assert manifest['position']['images_without_position'] == 0
    assert manifest['altitude']['images_from_depth'] == 1
    assert manifest['altitude']['images_at_surface_altitude'] == 5
    assert manifest['orientation']['heading_source'] == 'auto'
    assert manifest['orientation']['heading_columns_used'] == {
        'heading_imu': 4, 'none': 1, 'yaw': 1}
    assert manifest['orientation']['images_without_orientation'] == 1
    assert manifest['time']['images_above'] == 1
    assert manifest['time']['time_source'] == {'jetson': 6}
    assert manifest['notices'] == [
        'lever arm not measured for ilx_left, ilx_right: zero offset used']

    # calibration decided from the original card JPEGs
    for key, focal in (('ilx_left', 29.0), ('ilx_right', 24.0)):
        decision = manifest['calibration'][key]
        assert decision['mode'] == 'groups' and decision['requested'] == 'auto'
        assert decision['image_sizes'] == [[48, 32]]
        assert decision['exif_focals_mm'] == [focal]
        assert f'EXIF focal {focal:g} mm vs calibration 16 mm' in \
            decision['reason']
    assert intake.calibration_modes(intake.load_manifest(str(raw))) == {
        'ilx_left': 'groups', 'ilx_right': 'groups'}


def test_every_warn_only_case_is_warned_and_recorded(run_dir, workspace):
    """Static fix, empty depth, missing orientation, time_err_ms above the
    threshold and the calibration fallback warn - and nothing fails."""
    result = run_intake([str(run_dir)], str(workspace), log=QUIET)
    warnings = result.manifest['warnings']
    expected = [
        f'{RUN_ID}: every row carries the same position',
        ('5 of 6 images have no depth: altitude written as the surface '
         'altitude 0 m'),
        '1 of 6 images lack heading, pitch or roll',
        '1 image(s) carry time_err_ms above 50 ms (largest 120 ms)',
        'ilx_left: calibration groups only - image 48x32',
        'ilx_right: calibration groups only - image 48x32',
    ]
    for text in expected:
        assert sum(text in w for w in warnings) == 1, (text, warnings)
    assert len(warnings) == len(expected), warnings


def test_a_moving_track_is_not_a_static_fix(tmp_path, workspace):
    rows = {cam: [row(cam, f, xutm=X + 5.0 * i, yutm=Y + 2.0 * i)
                  for i, f in enumerate(FRAMES)] for cam in ('cam1', 'cam2')}
    run = build_run(tmp_path / 'runs', rows=rows)
    result = run_intake([str(run)], str(workspace), log=QUIET)
    assert result.manifest['static_fix'] is False
    assert not any('same position' in w for w in result.manifest['warnings'])
    lines = Path(result.flight_log_path).read_text('utf-8').splitlines()
    assert lines[3].split(';')[1:5] == [
        '294962.550000', '4588711.830000', '0.000000', '10.000000']


def test_the_review_variant_takes_the_review_jpegs(run_dir, workspace):
    result = run_intake([str(run_dir)], str(workspace),
                        IntakeOptions(variant='review'), log=QUIET)
    raw = workspace / 'raw_images'
    assert sorted(os.listdir(raw / 'ilx_left')) == _names(FRAMES, 'cam1', '.jpg')
    lines = Path(result.flight_log_path).read_text('utf-8').splitlines()
    assert [ln.split(';')[0] for ln in lines[1:]] == \
        _names(FRAMES, 'cam1', '.jpg') + _names(FRAMES, 'cam2', '.jpg')
    # Wild Sync's own Cam1_<frame>.xmp shares the review JPEG's stem; it is
    # never copied, so it cannot pose as a calibration sidecar.
    assert not list(raw.rglob('*.xmp'))
    assert result.manifest['calibration']['ilx_left']['image_sizes'] == [
        list(REVIEW_SIZE)]


@pytest.mark.parametrize('variant', ['raw', 'ARW', '.arw'])
def test_raw_is_refused_as_an_input_format(run_dir, workspace, variant):
    with pytest.raises(IntakeError, match=r'RAW \(\.ARW\) is not an input '
                                          'format of this pipeline'):
        run_intake([str(run_dir)], str(workspace), IntakeOptions(variant=variant),
                   log=QUIET)
    assert not workspace.exists()


def test_macos_appledouble_files_are_ignored(run_dir, workspace):
    """The 2026-08-20 runs reached Windows through a Mac copy that put an
    AppleDouble '._<name>' beside every file; they are not frames."""
    for cam in ('cam1', 'cam2'):
        for path in list((run_dir / cam).iterdir()):
            (path.parent / ('._' + path.name)).write_bytes(b'\x00\x05\x16\x07')
    result = run_intake([str(run_dir)], str(workspace), log=QUIET)
    assert result.manifest['images']['unmatched_images'] == 0
    assert result.manifest['images']['matched'] == 6
    assert [n['hidden_files_ignored']
            for n in result.manifest['sources'][0]['nodes']] == [13, 13]
    assert result.manifest['notices'][0].startswith('26 hidden file(s)')
    assert not list((workspace / 'raw_images').rglob('._*'))


def test_a_rerun_into_the_same_workspace_reuses_identical_copies(run_dir,
                                                                 workspace):
    first = run_intake([str(run_dir)], str(workspace), log=QUIET)
    second = run_intake([str(run_dir)], str(workspace), log=QUIET)
    assert second.manifest['images']['copied'] == 0
    assert second.manifest['images']['reused'] == 6
    assert Path(second.flight_log_path).read_bytes() == \
        Path(first.flight_log_path).read_bytes()


def test_a_workspace_with_other_images_is_refused(run_dir, workspace):
    stray = workspace / 'raw_images' / 'ilx_left' / 'Cam1_other.card.JPG'
    stray.parent.mkdir(parents=True)
    stray.write_bytes(b'x')
    with pytest.raises(IntakeError, match='did not plan'):
        run_intake([str(run_dir)], str(workspace), log=QUIET)


def test_a_workspace_inside_the_run_directory_is_refused(run_dir):
    before = tree(run_dir)
    with pytest.raises(IntakeError, match='only read'):
        run_intake([str(run_dir)], str(run_dir / 'ws'), log=QUIET)
    assert tree(run_dir) == before


def test_a_folder_of_runs_takes_every_run(tmp_path, workspace):
    later = ('20260820_193000.00', '20260820_193000.50')
    build_run(tmp_path / 'runs', rows=None)
    build_run(tmp_path / 'runs', name='260820_1930_transect-02',
              rows={cam: [row(cam, f) for f in later]
                    for cam in ('cam1', 'cam2')}, frames=later)
    result = run_intake([str(tmp_path / 'runs')], str(workspace), log=QUIET)
    assert [s['run_id'] for s in result.manifest['sources']] == [
        RUN_ID, '260820_1930_transect-02']
    assert result.manifest['flight_log_rows'] == 10


# ------------------------------------------------------- fail-closed gates

def _refused(run_paths, workspace, match, options=None, registry=None):
    with pytest.raises(IntakeError, match=match):
        run_intake([str(p) for p in run_paths], str(workspace), options,
                   log=QUIET, registry=registry)
    assert not (workspace / 'raw_images').exists(), \
        'a refused intake must write nothing'


def test_no_matched_image_fails(run_dir, workspace):
    for path in run_dir.rglob('*.card.JPG'):
        path.unlink()
    _refused([run_dir], workspace, 'no card image matched a flight-log row')


def test_a_match_rate_below_the_floor_fails(run_dir, workspace):
    for cam in ('cam1', 'cam2'):
        (run_dir / cam / f'{cam.capitalize()}_{FRAMES[2]}.card.JPG').unlink()
    (run_dir / 'cam1' / f'Cam1_{FRAMES[1]}.card.JPG').unlink()
    _refused([run_dir], workspace, r'only 3 of 6 frames \(50\.0%\).*below '
                                   r'the 80% floor')


def test_the_floor_can_be_lowered_deliberately(run_dir, workspace):
    (run_dir / 'cam1' / f'Cam1_{FRAMES[2]}.card.JPG').unlink()
    (run_dir / 'cam2' / 'Cam2_20260820_200000.00.card.JPG').write_bytes(
        (run_dir / 'cam2' / f'Cam2_{FRAMES[0]}.card.JPG').read_bytes())
    _refused([run_dir], workspace, r'only 5 of 7 frames')
    result = run_intake([str(run_dir)], str(workspace),
                        IntakeOptions(min_match_pct=70.0), log=QUIET)
    nodes = result.manifest['sources'][0]['nodes']
    assert nodes[0]['unmatched_rows'] == [f'Cam1_{FRAMES[2]}.jpg']
    assert nodes[1]['unmatched_images'] == ['Cam2_20260820_200000.00.card.JPG']
    warnings = result.manifest['warnings']
    assert any('cam1: 1 flight-log row(s) have no card image' in w
               for w in warnings)
    assert any('cam2: 1 card image(s) are named by no flight-log row' in w
               for w in warnings)


def test_mixed_utm_zones_fail(tmp_path, workspace):
    first = build_run(tmp_path / 'runs')
    later = ('20260820_193000.00',)
    second = build_run(
        tmp_path / 'runs', name='260820_1930_transect-02', frames=later,
        rows={cam: [row(cam, later[0], utm_zone='18T', long='-75.5')]
              for cam in ('cam1', 'cam2')})
    _refused([first, second], workspace, r'more than one UTM zone \(18T, 19T\)')


def test_a_zone_in_the_wrong_hemisphere_fails(tmp_path, workspace):
    rows = {cam: [row(cam, f, utm_zone='19G') for f in FRAMES]
            for cam in ('cam1', 'cam2')}
    run = build_run(tmp_path / 'runs', rows=rows)
    _refused([run], workspace, 'other hemisphere')


def test_duplicate_image_names_across_runs_fail(tmp_path, workspace):
    first = build_run(tmp_path / 'a')
    second = build_run(tmp_path / 'b')
    _refused([first, second], workspace, 'image names must be unique')


def test_two_rows_for_one_frame_fail(tmp_path, workspace):
    rows = edge_rows()
    rows['cam1'].append(row('cam1', FRAMES[0]))
    run = build_run(tmp_path / 'runs', rows=rows)
    _refused([run], workspace, r'lines 2 and 5 both name frame '
                               r'Cam1_20260820_192542\.42')


def test_a_row_in_the_wrong_node_folder_fails(tmp_path, workspace):
    rows = edge_rows()
    rows['cam1'][0] = row('cam2', FRAMES[0])
    run = build_run(tmp_path / 'runs', rows=rows)
    _refused([run], workspace, 'sits in node folder cam1')


@pytest.mark.parametrize('header,match', [
    (CSV_HEADER + ',extra', r"unknown column\(s\) 'extra'"),
    (CSV_HEADER.replace(',time_err_ms', ''), r'missing column\(s\) time_err_ms'),
    (CSV_HEADER.replace('xutm,yutm', 'yutm,xutm'), 'out of order'),
    (CSV_HEADER.replace(',', ';'), 'not a Wild Sync flight_log.csv header'),
])
def test_an_unknown_header_makes_the_log_unreadable(tmp_path, workspace,
                                                    header, match):
    run = build_run(tmp_path / 'runs')
    write_log(run / 'cam2' / 'flight_log.csv',
              [row('cam2', f) for f in FRAMES], header=header)
    _refused([run], workspace, match)


@pytest.mark.parametrize('over,match', [
    ({'pitch': 'level'}, "pitch 'level' is not a number"),
    ({'utm_zone': '19'}, "utm_zone '19' is not a UTM zone"),
    ({'utm_zone': '61T'}, "utm_zone '61T' is not a UTM zone"),
    ({'filename': ''}, 'empty filename'),
    ({'filename': 'Cam1_x.png'}, 'is not a Wild Sync image name'),
    ({'filename': 'Cam9_x.jpg'}, 'matches no camera family'),
])
def test_a_bad_cell_makes_the_log_unreadable(tmp_path, workspace, over, match):
    rows = edge_rows()
    rows['cam1'][1] = row('cam1', FRAMES[1], **over)
    run = build_run(tmp_path / 'runs', rows=rows)
    _refused([run], workspace, match)


def test_a_short_row_makes_the_log_unreadable(tmp_path, workspace):
    run = build_run(tmp_path / 'runs')
    log = run / 'cam1' / 'flight_log.csv'
    log.write_bytes(log.read_bytes() + b'Cam1_x.jpg,1,2\r\n')
    _refused([run], workspace, 'line 5 has 3 cells, expected 23')


def test_an_empty_or_undecodable_log_is_unreadable(tmp_path, workspace):
    run = build_run(tmp_path / 'runs')
    (run / 'cam1' / 'flight_log.csv').write_bytes(b'')
    _refused([run], workspace, 'empty file')
    (run / 'cam1' / 'flight_log.csv').write_bytes(b'\xff\xfe\x00bad')
    _refused([run], workspace, 'cannot read the flight log')


def test_non_finite_numbers_are_missing_values(tmp_path, workspace):
    rows = edge_rows()
    rows['cam2'][0] = row('cam2', FRAMES[0], heading_imu='nan', yaw='inf')
    run = build_run(tmp_path / 'runs', rows=rows)
    result = run_intake([str(run)], str(workspace), log=QUIET)
    line = Path(result.flight_log_path).read_text('utf-8').splitlines()[4]
    assert line.startswith('Cam2_20260820_192542.42.card.JPG;')
    assert line.endswith(';;;;;;')


def test_no_utm_position_at_all_fails(tmp_path, workspace):
    rows = {cam: [row(cam, f, xutm='', yutm='', utm_zone='') for f in FRAMES]
            for cam in ('cam1', 'cam2')}
    run = build_run(tmp_path / 'runs', rows=rows)
    _refused([run], workspace, 'no matched flight-log row carries a UTM '
                               'position')


def test_a_path_that_is_not_a_run_fails(tmp_path, workspace):
    (tmp_path / 'empty').mkdir()
    _refused([tmp_path / 'empty'], workspace, 'neither a Wild Sync run')
    _refused([tmp_path / 'absent'], workspace, 'not found')


@pytest.mark.parametrize('field,value,match', [
    ('heading_source', 'compass', 'heading source'),
    ('calibration', 'locked', 'calibration mode'),
    ('position_accuracy_m', 0.0, 'position accuracy'),
    ('min_match_pct', 101.0, 'at most 100'),
    ('declination_deg', float('nan'), 'declination'),
])
def test_invalid_options_fail(run_dir, workspace, field, value, match):
    _refused([run_dir], workspace, match,
             options=IntakeOptions(**{field: value}))


# ------------------------------------------------- calibration at intake

def test_the_2026_08_20_card_size_comes_out_as_groups(tmp_path, workspace):
    """Real 4752x3168 card JPEGs at 29 mm (cam1) and 24 mm (cam2)."""
    card = {'cam1': ((4752, 3168), 29.0), 'cam2': ((4752, 3168), 24.0)}
    run = build_run(tmp_path / 'runs', card=card, frames=FRAMES[:1],
                    rows={cam: [row(cam, FRAMES[0])]
                          for cam in ('cam1', 'cam2')})
    result = run_intake([str(run)], str(workspace), log=QUIET)
    for key, focal in (('ilx_left', 29), ('ilx_right', 24)):
        decision = result.manifest['calibration'][key]
        assert decision['mode'] == 'groups'
        assert decision['image_sizes'] == [[4752, 3168]]
        assert ('image 4752x3168 (aspect 1.500) vs calibration 4096x3000 '
                '(aspect 1.365)') in decision['reason']
        assert f'EXIF focal {focal} mm vs calibration 16 mm' in \
            decision['reason']


def test_calibrated_images_get_the_prior(tmp_path, workspace):
    run = build_run(tmp_path / 'runs', card=CALIBRATED_CARD)
    result = run_intake([str(run)], str(workspace), log=QUIET)
    assert intake.calibration_modes(result.manifest) == {
        'ilx_left': 'prior', 'ilx_right': 'prior'}
    assert not any('calibration' in w for w in result.manifest['warnings'])


def _registry(tmp_path, confirmed: bool):
    data = json.loads(Path(camera_registry.CAMERAS_JSON).read_text('utf-8'))
    data['rigs']['ilx_lr1_stereo']['node_assignment_confirmed'] = confirmed
    path = tmp_path / f'cameras_{confirmed}.json'
    path.write_text(json.dumps(data), encoding='utf-8')
    return camera_registry.load_registry(str(path))


@pytest.mark.parametrize('confirmed,mode', [(True, 'prior'), (False, 'groups')])
def test_node_assignment_switch_gates_the_prior(tmp_path, workspace,
                                                confirmed, mode):
    registry = _registry(tmp_path, confirmed)
    assert registry.rig_for('ilx_left').node_assignment_confirmed is confirmed
    run = build_run(tmp_path / 'runs', card=CALIBRATED_CARD)
    result = run_intake([str(run)], str(workspace), log=QUIET,
                        registry=registry)
    for key in ('ilx_left', 'ilx_right'):
        decision = result.manifest['calibration'][key]
        assert decision['mode'] == mode
        assert decision['node_assignment_confirmed'] is confirmed
    if not confirmed:
        assert 'node assignment of rig ilx_lr1_stereo is not confirmed' in \
            result.manifest['calibration']['ilx_left']['reason']


def test_an_explicit_prior_is_refused_while_unconfirmed(tmp_path, workspace):
    run = build_run(tmp_path / 'runs', card=CALIBRATED_CARD)
    _refused([run], workspace, 'calibration prior cannot be applied',
             options=IntakeOptions(calibration='prior'),
             registry=_registry(tmp_path, False))


def test_an_explicit_prior_is_refused_for_the_wrong_aspect(run_dir, workspace):
    _refused([run_dir], workspace, 'calibration prior cannot be applied.*'
                                   '48x32', options=IntakeOptions(
                                       calibration='prior'))


def test_off_reads_no_image_and_writes_no_warning(run_dir, workspace):
    result = run_intake([str(run_dir)], str(workspace),
                        IntakeOptions(calibration='off'), log=QUIET)
    for decision in result.manifest['calibration'].values():
        assert decision['mode'] == 'off' and decision['image_sizes'] == []
    assert not any('calibration' in w for w in result.manifest['warnings'])


def test_an_unreadable_card_image_is_warned_not_fatal(run_dir, workspace):
    (run_dir / 'cam1' / f'Cam1_{FRAMES[0]}.card.JPG').write_bytes(b'not a jpeg')
    result = run_intake([str(run_dir)], str(workspace), log=QUIET)
    assert any('ilx_left: 1 image(s) could not be read for the calibration '
               'decision' in w for w in result.manifest['warnings'])
    assert result.manifest['calibration']['ilx_left']['image_sizes'] == [[48, 32]]


def test_a_partial_manifest_is_never_acted_on(tmp_path):
    (tmp_path / 'wildsync_intake.json').write_text(
        '{"schema": 1, "status": "in_progress"}', encoding='utf-8')
    with pytest.raises(ValueError, match='not a complete'):
        intake.load_manifest(str(tmp_path))
    assert intake.load_manifest(str(tmp_path / 'absent')) is None
    with pytest.raises(ValueError, match='invalid calibration entry'):
        intake.calibration_modes({'calibration': {'ilx_left': {'mode': 'auto'}}})


# ------------------------------------------------------- the RSModule

def _module(run_paths: str, workspace: Path, **values) -> WildSyncIntake:
    module = WildSyncIntake(QUIET)
    params = module.get_parameters()
    params['output_dir'] = Parameter('Out', 'o', 'output_dir', str,
                                     str(workspace), prompt_user=False)
    for p in params.values():
        if p.get_value() is None:
            p.set_value(p.get_default_value())
    params['ws_run_dirs'].set_value(run_paths)
    for key, value in values.items():
        params[key].set_value(value)
    module.params = params
    return module


def test_module_parameters_follow_the_conventions():
    params = WildSyncIntake(QUIET).get_parameters()
    expected = {
        'ws_run_dirs': ('w_i', None), 'ws_variant': ('w_v', 'card'),
        'ws_calibration': ('w_cal', 'auto'),
        'ws_assert_focal': ('w_af', False),
        'ws_declination_deg': ('w_d', 0.0),
        'ws_heading_source': ('w_hs', 'auto'),
        'ws_pos_accuracy_m': ('w_pa', 10.0),
        'ws_static_pos_accuracy_m': ('w_spa', 1000.0),
        'ws_alt_accuracy_m': ('w_aa', 1.0),
        'ws_surface_alt_m': ('w_sa', 0.0),
        'ws_orientation_accuracy_deg': ('w_oa', 15.0),
        'ws_min_match_pct': ('w_mr', 80.0),
        'ws_time_err_warn_ms': ('w_te', 50.0),
    }
    assert set(params) == set(expected)
    for key, (short, default) in expected.items():
        assert params[key].cli_short == short
        assert params[key].get_default_value() == default
    assert len({p.cli_short for p in params.values()}) == len(params)


def test_module_runs_the_intake(run_dir, workspace):
    module = _module(f'"{run_dir}"', workspace)
    assert module.validate_parameters() == (True, None)
    result = module.run()
    assert result['Success'] is True
    assert result['Images'] == 6 and result['Flight Log Rows'] == 6
    assert result['Static Fix'] is True
    assert result['Calibration'] == {'ilx_left': 'groups',
                                     'ilx_right': 'groups'}


def test_module_reports_failure_instead_of_raising(run_dir, workspace):
    for path in run_dir.rglob('*.card.JPG'):
        path.unlink()
    result = _module(str(run_dir), workspace).run()
    assert result['Success'] is False
    assert 'no card image matched' in result['Failure']


@pytest.mark.parametrize('values,match', [
    ({'ws_variant': 'raw'}, 'RAW'),
    ({'ws_calibration': 'exact'}, 'calibration mode'),
    ({'ws_pos_accuracy_m': 'ten'}, 'position accuracy'),
])
def test_module_validation_names_the_bad_parameter(run_dir, workspace, values,
                                                   match):
    ok, message = _module(str(run_dir), workspace, **values).validate_parameters()
    assert ok is False and match in message


def test_module_validation_refuses_a_missing_run(tmp_path, workspace):
    ok, message = _module(str(tmp_path / 'absent'), workspace).validate_parameters()
    assert ok is False and 'not found' in message


def test_several_runs_in_one_parameter_value(tmp_path):
    assert intake.split_run_paths(' "C:\\a b";D:\\c\r\n\'E:\\d\' ;') == [
        'C:\\a b', 'D:\\c', 'E:\\d']


def test_main_chains_the_intake_first():
    main_text = (REPO_ROOT / 'main.py').read_text(encoding='utf-8')
    order = [main_text.index(f"'{name}':") for name in (
        'Wild Sync Intake', 'Preprocess Images', 'Batch Directory',
        'RealityScan Alignment')]
    assert order == sorted(order)


# ----------------------------------------- names survive preprocess + batch

class _SerialPool:
    def __init__(self, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def map(self, function, jobs, **kwargs):
        return map(function, jobs)


def test_preprocessing_keeps_the_card_names(run_dir, workspace, monkeypatch):
    pytest.importorskip('cv2')
    from modules.preprocess_images.preprocess_images import PreprocessImages
    monkeypatch.setattr(
        'modules.preprocess_images.preprocess_images.ProcessPoolExecutor',
        _SerialPool)
    run_intake([str(run_dir)], str(workspace), log=QUIET)
    module = PreprocessImages(QUIET)
    params = module.get_parameters()
    params.pop('pre_input_image_dir')        # chained after Wild Sync Intake
    params['pre_workers'].set_value(1)
    params['output_dir'] = Parameter('Out', 'o', 'output_dir', str,
                                     str(workspace), prompt_user=False)
    for p in params.values():
        if p.get_value() is None:
            p.set_value(p.get_default_value())
    module.params = params
    result = module.run()
    assert result['Success'] and result['Processed'] == 6, result
    out = workspace / 'preprocessed_images'
    for cam, key in (('cam1', 'ilx_left'), ('cam2', 'ilx_right')):
        assert sorted(os.listdir(out / key)) == sorted(_names(FRAMES, cam))
        for name in _names(FRAMES, cam):
            with Image.open(out / key / name) as image:
                assert image.format == 'JPEG'


def test_batching_matches_and_keeps_the_card_names(run_dir, workspace):
    from modules.image_batcher.batch_directory import BatchDirectory
    result = run_intake([str(run_dir)], str(workspace), log=QUIET)
    raw = workspace / 'raw_images'
    module = BatchDirectory(QUIET)
    names = [ln.split(';')[0] for ln in
             Path(result.flight_log_path).read_text('utf-8').splitlines()[1:]]
    assert all(os.path.splitext(n)[1].lower()
               in BatchDirectory.ACCEPTED_EXTENSIONS for n in names)
    zone = workspace / 'zone_1'
    zone.mkdir()
    copied, missing = module._BatchDirectory__copy_files(str(raw), str(zone),
                                                         names)
    assert (copied, missing) == (6, 0)
    assert sorted(os.listdir(zone / 'ilx_left')) == sorted(_names(FRAMES, 'cam1'))
    assert sorted(os.listdir(zone / 'ilx_right')) == sorted(_names(FRAMES, 'cam2'))
