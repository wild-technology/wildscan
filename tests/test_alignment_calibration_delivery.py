"""Calibration delivery in the alignment stage.

The alignment module reads the per-camera calibration decision from the
Wild Sync intake manifest. For ``prior`` / ``groups`` it writes the
decided sidecar beside every image of that camera and an ``.rscmd`` that
AlignZone.bat executes instead of ``-addFolder``; a ``groups`` sidecar
carries the starting focal the intake observed on the camera's images.
For ``off`` (or no manifest) the zone is aligned exactly as without
calibration delivery.

No RealityScan: ``run_batch_script`` is stubbed for the module tests, and
the AlignZone.bat execution tests run the real script next to stub
``SetVariables.bat`` / ``startRealityScan.bat`` files and a stub
RealityScan that only records its arguments.
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from module_base.parameter import Parameter
from module_base.settings_store import SettingsStore
from modules import align_fingerprint, calibration_sidecars, camera_registry
from modules.calibration_sidecars import sidecar_xmp
from modules.realityscan_interface import realityscan_cli as cli_mod
from modules.realityscan_interface import realityscan_interface as ri_mod
from modules.realityscan_interface.realityscan_cli import WorkflowResult
from modules.realityscan_interface.realityscan_interface import RealityScanAlignment

SCRIPTS = Path(cli_mod.SCRIPTS_DIR)
METADATA = Path(cli_mod.METADATA_DIR)

LOG_HEADER = 'filename;X (East);Y (North);Alt\n'
FRAMES = ('20260820_192542.42', '20260820_192545.42', '20260820_192548.42')
POSE_SIDECAR = ('<x:xmpmeta><rdf:Description xcr:Version="3" '
                'xcr:Position="1 2 3"/></x:xmpmeta>\n')
# Starting 35 mm-equivalent focals as the intake records them: cam1 29 mm
# from EXIF FocalLengthIn35mmFilm, cam2 24 mm converted from the sensor
# width (24 x 36 / 35.7).
FOCALS = {'ilx_left': 29.0, 'ilx_right': 24.0 * 36 / 35.7}


def _xmp(camera, mode: str) -> str:
    """The sidecar the alignment writes for ``camera`` in ``mode``."""
    return sidecar_xmp(camera, mode, FOCALS[camera.key])


class Capture(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.messages.append(record.getMessage())


@pytest.fixture
def logs() -> Capture:
    handler = Capture()
    logger = logging.getLogger('align-calibration-test')
    logger.propagate = False
    logger.setLevel(logging.INFO)
    logger.handlers = [handler]
    return handler


@pytest.fixture(autouse=True)
def no_native_install(tmp_path, monkeypatch):
    monkeypatch.delenv('RS_EXECUTABLE', raising=False)
    monkeypatch.delenv('RS_ALIGN_POOL_DIR', raising=False)
    monkeypatch.delenv('RS_ALIGN_PARAMS', raising=False)
    monkeypatch.setattr(cli_mod, 'EXECUTABLE_CANDIDATES', [])
    monkeypatch.setattr(cli_mod, 'SettingsStore',
                        lambda: SettingsStore(str(tmp_path / 'settings.json')))


def _param(name, value):
    p = Parameter(name, None, name, type(value) if value is not None else str,
                  None, prompt_user=False)
    p.set_value(value)
    return p


def _workspace(tmp_path: Path, modes: dict[str, str] | None,
               frames=FRAMES) -> Path:
    """Workspace as Wild Sync Intake + Batch Directory leave it: the
    manifest in raw_images, one copy-layout zone with per-camera folders
    and the zone's own flight log."""
    ws = tmp_path / 'ws'
    raw = ws / 'raw_images'
    raw.mkdir(parents=True)
    if modes is not None:
        manifest = {
            'schema': 2, 'status': 'complete',
            'calibration': {key: {'camera': key, 'mode': mode,
                                  'reason': 'test',
                                  'starting_focal_35mm': FOCALS[key]}
                            for key, mode in modes.items()},
        }
        (raw / 'wildsync_intake.json').write_text(json.dumps(manifest),
                                                  encoding='utf-8')
    zone = ws / 'batched_images_by_zone' / 'zone_1'
    for node, camera in (('Cam1', 'ilx_left'), ('Cam2', 'ilx_right')):
        folder = zone / camera
        folder.mkdir(parents=True)
        for frame in frames:
            (folder / f'{node}_{frame}.card.JPG').write_bytes(b'jpeg')
    (zone / 'flight_log_19T_UTM.txt').write_text(LOG_HEADER, encoding='utf-8')
    return ws


def _images(ws: Path) -> list[Path]:
    zone = ws / 'batched_images_by_zone' / 'zone_1'
    return sorted(p for p in zone.rglob('*') if p.suffix.lower() == '.jpg')


def _module(tmp_path, monkeypatch, ws: Path, logger: logging.Logger,
            on_run=None, result=None, extra_params=None, min_size=None):
    """Chained alignment module whose RealityScan call is recorded.

    ``on_run(args)`` runs at the moment the script would be invoked, so a
    test can inspect the disk exactly as AlignZone.bat would see it. A
    successful stub run leaves the saved project and one component."""
    executable = tmp_path / 'mock_RealityScan.exe'
    executable.write_bytes(b'offline fixture; not an executable')
    module = RealityScanAlignment(logger)
    module.params = {
        'output_dir': _param('output_dir', str(ws)),
        'rs_display_output': _param('rs_display_output', False),
        'rs_project_label': _param('rs_project_label', ''),
        **(extra_params or {}),
    }
    if min_size is not None:
        module.params['rs_min_component_size'] = _param(
            'rs_min_component_size', min_size)
    calls: list[tuple[str, tuple[str, ...]]] = []

    def fake_run(script, args, log_dir, display_output=False, **kw):
        calls.append((script, tuple(args)))
        if on_run is not None:
            on_run(list(args))
        outcome = result or WorkflowResult(True, 0, None, '', [], 0.0)
        if outcome.success:
            out_dir, scene = args[1], args[4]
            Path(out_dir, f'{scene}.rsproj').write_bytes(b'project')
            Path(out_dir, f'{scene}_c0.rsalign').write_bytes(b'component')
        return outcome

    monkeypatch.setattr(module.cli, 'run_batch_script', fake_run)
    monkeypatch.setattr(module.cli, 'find_executable', lambda: str(executable))
    monkeypatch.setattr(module, '_initialize_loading_bar', lambda *a, **k: None)
    monkeypatch.setattr(module, '_update_loading_bar', lambda *a, **k: None)
    monkeypatch.setattr('modules.align_fingerprint._repo_sha', lambda: None)
    return module, calls


def _sidecars(ws: Path) -> dict[str, str]:
    root = ws / 'batched_images_by_zone'
    return {p.name: p.read_text(encoding='utf-8') for p in root.rglob('*.xmp')}


def _expected_rscmd(ws: Path, modes: dict[str, str]) -> str:
    lines = []
    for image in sorted(str(p) for p in _images(ws)):
        camera = camera_registry.identify(os.path.basename(image))
        if modes.get(camera.key) in ('prior', 'groups'):
            xmp = image[:-len('.JPG')] + '.xmp'
            lines.append(f'-addImageWithCalibration "{image}" "{xmp}"')
        else:
            lines.append(f'-add "{image}"')
    return ''.join(line + '\r\n' for line in lines)


# ------------------------------------------------- argv and files on disk

@pytest.mark.parametrize('modes', [
    {'ilx_left': 'groups', 'ilx_right': 'groups'},
    {'ilx_left': 'prior', 'ilx_right': 'groups'},
    {'ilx_left': 'prior', 'ilx_right': 'off'},
])
def test_sidecars_and_command_file_exist_when_the_script_runs(
        tmp_path, monkeypatch, logs, modes):
    ws = _workspace(tmp_path, modes)
    zone = ws / 'batched_images_by_zone' / 'zone_1'
    out = ws / 'aligned_components' / 'zone_1'
    seen = {}

    def on_run(args):
        rscmd = Path(args[6])
        seen['rscmd'] = rscmd.read_bytes()
        seen['sidecars'] = _sidecars(ws)

    module, calls = _module(tmp_path, monkeypatch, ws,
                            logging.getLogger('align-calibration-test'),
                            on_run=on_run, min_size=2)
    output = module.run()
    assert output['Success'] is True, output

    assert len(calls) == 1
    script, args = calls[0]
    assert script == 'AlignZone.bat'
    assert args == (str(zone), str(out), str(zone / 'flight_log_19T_UTM.txt'),
                    os.path.join(str(ws), 'logs', 'FlightLogParams_19T.xml'),
                    'zone_1', '2', str(out / 'zone_1.rscmd'))

    assert seen['rscmd'] == _expected_rscmd(ws, modes).encode('utf-8')
    assert b'\n' not in seen['rscmd'].replace(b'\r\n', b'')
    expected = {}
    for image in _images(ws):
        camera = camera_registry.identify(image.name)
        if modes[camera.key] in ('prior', 'groups'):
            expected[image.name[:-len('.JPG')] + '.xmp'] = _xmp(
                camera, modes[camera.key])
    assert seen['sidecars'] == expected
    # After the run the same decided sidecars are still there.
    assert _sidecars(ws) == expected


def _today_args(ws: Path, min_size: str) -> tuple[str, ...]:
    zone = ws / 'batched_images_by_zone' / 'zone_1'
    return (str(zone), str(ws / 'aligned_components' / 'zone_1'),
            str(zone / 'flight_log_19T_UTM.txt'),
            os.path.join(str(ws), 'logs', 'FlightLogParams_19T.xml'),
            'zone_1', min_size)


@pytest.mark.parametrize('modes', [
    {'ilx_left': 'off', 'ilx_right': 'off'},
    None,
])
def test_off_and_no_manifest_align_exactly_as_before(tmp_path, monkeypatch,
                                                     modes):
    """Six arguments, no sidecar, no command file, and both hygiene calls
    without modes - the calls the module made before calibration
    delivery existed."""
    ws = _workspace(tmp_path, modes)
    hygiene = []
    real_sanitize = calibration_sidecars.sanitize_and_census
    real_ensure = calibration_sidecars.ensure_calibration_sidecars

    def sanitize(*args):
        hygiene.append(('sanitize', args))
        return real_sanitize(*args)

    def ensure(*args):
        hygiene.append(('ensure', args))
        return real_ensure(*args)

    monkeypatch.setattr(calibration_sidecars, 'sanitize_and_census', sanitize)
    monkeypatch.setattr(calibration_sidecars, 'ensure_calibration_sidecars',
                        ensure)
    module, calls = _module(tmp_path, monkeypatch, ws,
                            logging.getLogger('align-calibration-test'),
                            min_size=2)
    assert module.run()['Success'] is True
    assert calls == [('AlignZone.bat', _today_args(ws, '2'))]
    assert _sidecars(ws) == {}
    assert not list((ws / 'aligned_components').rglob('*.rscmd'))
    zone = str(ws / 'batched_images_by_zone' / 'zone_1')
    assert hygiene == [('sanitize', (zone, None, None)),
                       ('ensure', (zone, None, None)),
                       ('ensure', (zone, None, None))]
    fingerprint = json.loads(
        (ws / 'aligned_components' / 'zone_1' / 'align_inputs.json')
        .read_text(encoding='utf-8'))
    assert 'calibration' not in fingerprint
    assert 'starting_focal_35mm' not in fingerprint


def test_no_manifest_with_camera_named_images_says_calibration_is_off(
        tmp_path, monkeypatch, logs):
    ws = _workspace(tmp_path, None)
    module, _calls = _module(tmp_path, monkeypatch, ws,
                             logging.getLogger('align-calibration-test'),
                             min_size=2)
    assert module.run()['Success'] is True
    assert any('calibration delivery is OFF' in m and 'wildsync_intake.json' in m
               for m in logs.messages), logs.messages


# ------------------------------------------------------------- refusals

def _assert_nothing_aligned(ws: Path, calls) -> None:
    assert calls == []
    assert _sidecars(ws) == {}


def test_a_missing_manifest_fails_when_the_intake_is_part_of_the_run(
        tmp_path, monkeypatch, logs):
    ws = _workspace(tmp_path, None)
    module, calls = _module(
        tmp_path, monkeypatch, ws, logging.getLogger('align-calibration-test'),
        extra_params={'ws_run_dirs': _param('ws_run_dirs', 'runs')})
    assert module.run() == {'Success': False}
    _assert_nothing_aligned(ws, calls)
    assert any('does not exist' in m and 'nothing was aligned' in m
               for m in logs.messages), logs.messages


@pytest.mark.parametrize('content', [
    '{not json',
    json.dumps({'schema': 2, 'status': 'partial', 'calibration': {}}),
    json.dumps({'schema': 2, 'status': 'complete',
                'calibration': {'ilx_left': {'mode': 'locked',
                                             'starting_focal_35mm': 29.0}}}),
    json.dumps({'schema': 2, 'status': 'complete',
                'calibration': {'some_camera': {'mode': 'groups',
                                                'starting_focal_35mm': 29.0}}}),
    # A schema-1 manifest predates the observed focal.
    json.dumps({'schema': 1, 'status': 'complete',
                'calibration': {'ilx_left': {'mode': 'groups'}}}),
    # A camera without a usable starting focal.
    json.dumps({'schema': 2, 'status': 'complete',
                'calibration': {'ilx_left': {'mode': 'groups'}}}),
    json.dumps({'schema': 2, 'status': 'complete',
                'calibration': {'ilx_left': {'mode': 'groups',
                                             'starting_focal_35mm': 0}}}),
    json.dumps({'schema': 2, 'status': 'complete',
                'calibration': {'ilx_left': {'mode': 'off',
                                             'starting_focal_35mm': None}}}),
])
def test_an_unusable_manifest_fails_before_anything_is_aligned(
        tmp_path, monkeypatch, logs, content):
    ws = _workspace(tmp_path, None)
    (ws / 'raw_images' / 'wildsync_intake.json').write_text(content,
                                                           encoding='utf-8')
    module, calls = _module(tmp_path, monkeypatch, ws,
                            logging.getLogger('align-calibration-test'))
    assert module.run() == {'Success': False}
    _assert_nothing_aligned(ws, calls)
    assert any('nothing was aligned' in m for m in logs.messages), logs.messages


@pytest.mark.parametrize('in_run', [False, True])
def test_a_missing_flight_log_fails_the_zone_when_there_is_an_intake(
        tmp_path, monkeypatch, logs, in_run):
    ws = _workspace(tmp_path, {'ilx_left': 'off', 'ilx_right': 'off'})
    (ws / 'batched_images_by_zone' / 'zone_1' / 'flight_log_19T_UTM.txt').unlink()
    extra = ({'ws_run_dirs': _param('ws_run_dirs', 'runs')} if in_run
             else None)
    module, calls = _module(tmp_path, monkeypatch, ws,
                            logging.getLogger('align-calibration-test'),
                            extra_params=extra, min_size=2)
    output = module.run()
    assert output['Success'] is False
    assert calls == []
    errors = [c.get('Error', '') for c in output['Components'].values()]
    assert any('No flight log' in e and 'Wild Sync intake' in e
               and 'not aligned' in e for e in errors), errors
    assert not any('aligning WITHOUT' in m for m in logs.messages)


def test_a_missing_flight_log_without_an_intake_aligns_without_priors(
        tmp_path, monkeypatch, logs):
    ws = _workspace(tmp_path, None)
    (ws / 'batched_images_by_zone' / 'zone_1' / 'flight_log_19T_UTM.txt').unlink()
    module, calls = _module(tmp_path, monkeypatch, ws,
                            logging.getLogger('align-calibration-test'),
                            min_size=2)
    assert module.run()['Success'] is True
    assert len(calls) == 1 and calls[0][1][2] == ''
    assert any('aligning WITHOUT georeferencing priors' in m
               for m in logs.messages), logs.messages


def test_the_pool_layout_with_calibration_is_refused(tmp_path, monkeypatch,
                                                     logs):
    ws = _workspace(tmp_path, {'ilx_left': 'groups', 'ilx_right': 'groups'})
    monkeypatch.setenv('RS_ALIGN_POOL_DIR', str(tmp_path / 'pool'))
    module, calls = _module(tmp_path, monkeypatch, ws,
                            logging.getLogger('align-calibration-test'))
    output = module.run()
    assert output['Success'] is False
    _assert_nothing_aligned(ws, calls)
    errors = [c.get('Error', '') for c in output['Components'].values()]
    assert any('pool layout' in e for e in errors), errors


def test_the_pool_layout_without_calibration_is_unchanged(tmp_path,
                                                          monkeypatch):
    ws = _workspace(tmp_path, {'ilx_left': 'off', 'ilx_right': 'off'})
    monkeypatch.setenv('RS_ALIGN_POOL_DIR', str(tmp_path / 'pool'))
    (tmp_path / 'pool').mkdir()
    module, calls = _module(tmp_path, monkeypatch, ws,
                            logging.getLogger('align-calibration-test'),
                            min_size=2)
    module.run()
    assert calls == [('AlignZone.bat', _today_args(ws, '2'))]


# ---------------------------------------------------------------- hygiene

@pytest.mark.parametrize('succeeded', [True, False])
def test_hygiene_restores_and_never_deletes_the_decided_sidecars(
        tmp_path, monkeypatch, succeeded):
    """The stub does what the identity harvest does to the sidecars:
    -exportXMP overwrites a registered image's sidecar with a pose, the
    harvest moves some of them away and leaves others behind. A failed
    run must leave the decided sidecars too."""
    modes = {'ilx_left': 'groups', 'ilx_right': 'prior'}
    ws = _workspace(tmp_path, modes)

    def on_run(args):
        out = Path(args[1])
        harvest = out / 'identity_r0'
        harvest.mkdir()
        for index, image in enumerate(_images(ws)):
            sidecar = image.with_name(image.name[:-len('.JPG')] + '.xmp')
            sidecar.write_text(POSE_SIDECAR, encoding='utf-8')
            if index % 2 == 0:
                shutil.move(str(sidecar), str(harvest / sidecar.name))

    module, _calls = _module(tmp_path, monkeypatch, ws,
                             logging.getLogger('align-calibration-test'),
                             on_run=on_run, min_size=2,
                             result=None if succeeded else
                             WorkflowResult(False, 1, None, 'boom', [], 0.0))
    assert module.run()['Success'] is succeeded
    sidecars = _sidecars(ws)
    assert len(sidecars) == len(_images(ws))
    for image in _images(ws):
        camera = camera_registry.identify(image.name)
        text = sidecars[image.name[:-len('.JPG')] + '.xmp']
        assert text == _xmp(camera, modes[camera.key]), image.name
        assert 'xcr:Position' not in text


def test_a_stale_sidecar_of_another_form_is_replaced_before_the_run(
        tmp_path, monkeypatch):
    modes = {'ilx_left': 'groups', 'ilx_right': 'groups'}
    ws = _workspace(tmp_path, modes)
    left = camera_registry.CAMERAS['ilx_left']
    stale = _images(ws)[0]
    assert camera_registry.identify(stale.name) is left
    name = stale.name[:-len('.JPG')] + '.xmp'
    stale.with_name(name).write_bytes(
        sidecar_xmp(left, 'prior').encode('utf-8'))
    seen = {}
    module, _calls = _module(
        tmp_path, monkeypatch, ws, logging.getLogger('align-calibration-test'),
        on_run=lambda args: seen.update(_sidecars(ws)), min_size=2)
    module.run()
    assert seen[name] == _xmp(left, 'groups')


def test_a_groups_sidecar_of_an_earlier_starting_focal_is_rewritten(
        tmp_path, monkeypatch, logs):
    """The pipeline's own groups sidecar with another starting focal (an
    earlier intake), or in the form without a focal, is replaced with the
    current one - not moved aside as foreign."""
    modes = {'ilx_left': 'groups', 'ilx_right': 'groups'}
    ws = _workspace(tmp_path, modes)
    left = camera_registry.CAMERAS['ilx_left']
    first, second = [p for p in _images(ws)
                     if camera_registry.identify(p.name) is left][:2]
    _sidecar_of(first).write_bytes(
        sidecar_xmp(left, 'groups', 16.0).encode('utf-8'))
    _sidecar_of(second).write_bytes(
        calibration_sidecars._legacy_groups_xmp(left).encode('utf-8'))
    seen = {}
    module, _calls = _module(
        tmp_path, monkeypatch, ws, logging.getLogger('align-calibration-test'),
        on_run=lambda args: seen.update(_sidecars(ws)), min_size=2)
    assert module.run()['Success'] is True
    for image in (first, second):
        assert seen[_sidecar_of(image).name] == _xmp(left, 'groups')
    assert not (ws / PRE_EXISTING).exists()


def _sidecar_of(image: Path) -> Path:
    return image.with_name(image.name[:-len('.JPG')] + '.xmp')


PRE_EXISTING = Path('aligned_components', 'zone_1', 'pre_existing_sidecars')


def test_a_foreign_pose_sidecar_is_moved_aside_before_the_run(
        tmp_path, monkeypatch, logs):
    """A sidecar beside an image that this pipeline did not write (here a
    pose prior) is moved, content intact, under the zone's output folder
    before the decided sidecar is written - never overwritten."""
    modes = {'ilx_left': 'groups', 'ilx_right': 'prior'}
    ws = _workspace(tmp_path, modes)
    zone = ws / 'batched_images_by_zone' / 'zone_1'
    image = _images(ws)[0]
    foreign = _sidecar_of(image)
    foreign.write_bytes(POSE_SIDECAR.encode('utf-8'))
    seen = {}
    module, _calls = _module(
        tmp_path, monkeypatch, ws, logging.getLogger('align-calibration-test'),
        on_run=lambda args: seen.update(_sidecars(ws)), min_size=2)
    assert module.run()['Success'] is True

    moved = ws / PRE_EXISTING / foreign.relative_to(zone)
    assert moved.read_bytes() == POSE_SIDECAR.encode('utf-8')
    camera = camera_registry.identify(image.name)
    assert seen[foreign.name] == _xmp(camera, modes[camera.key])
    warnings = [m for m in logs.messages if 'pre-existing' in m]
    assert len(warnings) == 1, logs.messages
    assert '1 pre-existing sidecar(s)' in warnings[0]
    assert str(ws / PRE_EXISTING) in warnings[0]
    assert 'MOVED' in warnings[0]


def test_own_stale_sidecar_of_another_mode_is_replaced_not_moved(
        tmp_path, monkeypatch, logs):
    modes = {'ilx_left': 'groups', 'ilx_right': 'groups'}
    ws = _workspace(tmp_path, modes)
    image = _images(ws)[0]
    camera = camera_registry.identify(image.name)
    # Bytes as the pipeline writes them (LF; write_text would give CRLF on
    # Windows, which is not byte for byte the pipeline's own sidecar).
    _sidecar_of(image).write_bytes(sidecar_xmp(camera, 'prior').encode('utf-8'))
    module, _calls = _module(tmp_path, monkeypatch, ws,
                             logging.getLogger('align-calibration-test'),
                             min_size=2)
    assert module.run()['Success'] is True
    assert _sidecar_of(image).read_text(encoding='utf-8') == \
        _xmp(camera, 'groups')
    assert not (ws / PRE_EXISTING).exists()
    assert not any('pre-existing' in m for m in logs.messages), logs.messages


@pytest.mark.parametrize('modes', [
    {'ilx_left': 'off', 'ilx_right': 'off'},
    None,
])
def test_nothing_is_moved_aside_with_calibration_off(tmp_path, monkeypatch,
                                                     logs, modes):
    ws = _workspace(tmp_path, modes)
    foreign = _sidecar_of(_images(ws)[0])
    foreign.write_bytes(POSE_SIDECAR.encode('utf-8'))
    seen = {}
    module, _calls = _module(
        tmp_path, monkeypatch, ws, logging.getLogger('align-calibration-test'),
        on_run=lambda args: seen.update(_sidecars(ws)), min_size=2)
    module.run()
    assert seen == {foreign.name: POSE_SIDECAR}
    assert not (ws / PRE_EXISTING).exists()
    heads_up = [m for m in logs.messages if 'HEADS UP' in m]
    assert len(heads_up) == 1, logs.messages
    assert 'imports them as pose priors' in heads_up[0]
    assert 'not preserved' in heads_up[0]
    assert 'pre_existing_sidecars' not in heads_up[0]


def test_a_moved_sidecar_never_clobbers_an_earlier_one(tmp_path, monkeypatch,
                                                       logs):
    modes = {'ilx_left': 'groups', 'ilx_right': 'groups'}
    ws = _workspace(tmp_path, modes)
    zone = ws / 'batched_images_by_zone' / 'zone_1'
    foreign = _sidecar_of(_images(ws)[0])
    target = ws / PRE_EXISTING / foreign.relative_to(zone)
    target.parent.mkdir(parents=True)
    target.write_bytes(b'earlier')
    target.with_name(target.stem + '.1.xmp').write_bytes(b'earlier 1')
    foreign.write_bytes(b'user edit')
    module, _calls = _module(tmp_path, monkeypatch, ws,
                             logging.getLogger('align-calibration-test'),
                             min_size=2)
    module.run()
    # The previous run's folder (holding the earlier moves) is kept: it is
    # moved aside with the rest of the zone output, never deleted.
    superseded = list((ws / 'superseded').iterdir())
    assert len(superseded) == 1
    kept = superseded[0] / 'pre_existing_sidecars' / foreign.relative_to(zone)
    assert kept.read_bytes() == b'earlier'
    assert kept.with_name(kept.stem + '.1.xmp').read_bytes() == b'earlier 1'
    assert (ws / PRE_EXISTING / foreign.relative_to(zone)).read_bytes() == \
        b'user edit'


def test_a_moved_sidecar_gets_a_numbered_name_on_collision(tmp_path):
    camera = camera_registry.CAMERAS['ilx_left']
    images = tmp_path / 'images' / 'ilx_left'
    images.mkdir(parents=True)
    image = images / 'Cam1_20260820_192542.42.card.JPG'
    image.write_bytes(b'jpeg')
    destination = tmp_path / 'out' / 'pre_existing_sidecars'
    for attempt in range(3):
        _sidecar_of(image).write_bytes(f'user {attempt}'.encode())
        moved = calibration_sidecars.move_foreign_sidecars(
            [str(image)], {camera.key: 'groups'}, str(tmp_path / 'images'),
            str(destination))
        assert len(moved) == 1
    names = sorted(p.name for p in (destination / 'ilx_left').iterdir())
    assert names == ['Cam1_20260820_192542.42.card.1.xmp',
                     'Cam1_20260820_192542.42.card.2.xmp',
                     'Cam1_20260820_192542.42.card.xmp']
    assert (destination / 'ilx_left' / names[2]).read_bytes() == b'user 0'
    assert (destination / 'ilx_left' / names[1]).read_bytes() == b'user 2'


def _unreadable(monkeypatch, module, paths: set[str]) -> None:
    """Make reading ``paths`` with ``open`` in ``module`` fail as an
    access-denied file would (writing a new file there still works)."""
    real_open = open

    def fake_open(file, mode='r', *args, **kwargs):
        if 'r' in mode and os.path.normcase(os.path.abspath(file)) in paths:
            raise PermissionError(13, 'Permission denied', str(file))
        return real_open(file, mode, *args, **kwargs)

    monkeypatch.setattr(module, 'open', fake_open, raising=False)


def test_an_unreadable_sidecar_is_moved_aside_as_foreign(tmp_path,
                                                         monkeypatch, logs):
    modes = {'ilx_left': 'groups', 'ilx_right': 'groups'}
    ws = _workspace(tmp_path, modes)
    zone = ws / 'batched_images_by_zone' / 'zone_1'
    foreign = _sidecar_of(_images(ws)[0])
    foreign.write_bytes(b'locked')
    _unreadable(monkeypatch, calibration_sidecars,
                {os.path.normcase(str(foreign))})
    module, calls = _module(tmp_path, monkeypatch, ws,
                            logging.getLogger('align-calibration-test'),
                            min_size=2)
    assert module.run()['Success'] is True
    assert len(calls) == 1
    assert (ws / PRE_EXISTING / foreign.relative_to(zone)).read_bytes() == \
        b'locked'


def test_an_unreadable_sidecar_that_cannot_be_moved_fails_the_zone(
        tmp_path, monkeypatch, logs):
    modes = {'ilx_left': 'groups', 'ilx_right': 'groups'}
    ws = _workspace(tmp_path, modes)
    foreign = _sidecar_of(_images(ws)[0])
    foreign.write_bytes(b'locked')
    _unreadable(monkeypatch, calibration_sidecars,
                {os.path.normcase(str(foreign))})
    real_rename = os.rename

    def no_rename(src, dst):
        if os.path.normcase(str(src)) == os.path.normcase(str(foreign)):
            raise PermissionError(13, 'Permission denied', str(src))
        return real_rename(src, dst)

    monkeypatch.setattr(os, 'rename', no_rename)
    module, calls = _module(tmp_path, monkeypatch, ws,
                            logging.getLogger('align-calibration-test'),
                            min_size=2)
    output = module.run()
    assert output['Success'] is False
    assert calls == []
    assert foreign.read_bytes() == b'locked'
    errors = [c.get('Error', '') for c in output['Components'].values()]
    assert any('cannot move the sidecar' in e and str(foreign) in e
               for e in errors), errors


@pytest.mark.parametrize('modes', [
    {'ilx_left': 'off', 'ilx_right': 'off'},
    None,
])
def test_an_unreadable_sidecar_fails_the_zone_with_calibration_off(
        tmp_path, monkeypatch, logs, modes):
    ws = _workspace(tmp_path, modes)
    foreign = _sidecar_of(_images(ws)[0])
    foreign.write_bytes(b'locked')
    _unreadable(monkeypatch, ri_mod, {os.path.normcase(str(foreign))})
    module, calls = _module(tmp_path, monkeypatch, ws,
                            logging.getLogger('align-calibration-test'),
                            min_size=2)
    output = module.run()
    assert output['Success'] is False
    assert calls == []
    assert foreign.read_bytes() == b'locked'
    errors = [c.get('Error', '') for c in output['Components'].values()]
    assert any('1 unreadable .xmp sidecar(s)' in e for e in errors), errors
    assert any('could not be read' in m and str(foreign) in m
               for m in logs.messages), logs.messages


def test_hygiene_reports_an_unreadable_sidecar(tmp_path, monkeypatch, caplog):
    folder = tmp_path / 'ilx_left'
    folder.mkdir()
    (folder / 'Cam1_20260820_192542.42.card.JPG').write_bytes(b'jpeg')
    locked = folder / 'Cam1_20260820_192542.42.card.xmp'
    locked.write_text(POSE_SIDECAR, encoding='utf-8')
    _unreadable(monkeypatch, calibration_sidecars,
                {os.path.normcase(str(locked))})
    with caplog.at_level(logging.WARNING, logger='modules.calibration_sidecars'):
        calibration_sidecars.sanitize_and_census(str(tmp_path),
                                                 {'ilx_left': 'groups'}, FOCALS)
    assert locked.exists()
    assert any('1 sidecar(s) could not be read' in r.getMessage()
               and str(locked) in r.getMessage() for r in caplog.records), \
        caplog.text


def test_a_change_of_calibration_mode_is_reported_on_a_rerun(tmp_path,
                                                             monkeypatch,
                                                             logs):
    ws = _workspace(tmp_path, {'ilx_left': 'off', 'ilx_right': 'off'})
    module, _calls = _module(tmp_path, monkeypatch, ws,
                             logging.getLogger('align-calibration-test'),
                             min_size=2)
    assert module.run()['Success'] is True
    manifest = ws / 'raw_images' / 'wildsync_intake.json'
    data = json.loads(manifest.read_text(encoding='utf-8'))
    for entry in data['calibration'].values():
        entry['mode'] = 'groups'
    manifest.write_text(json.dumps(data), encoding='utf-8')
    assert module.run()['Success'] is True
    assert any('RETRY WITH CHANGED INPUTS' in m and 'calibration delivery '
               'changed' in m for m in logs.messages), logs.messages


@pytest.mark.parametrize('before, after', [
    ({'ilx_left': 'groups', 'ilx_right': 'groups'},
     {'ilx_left': 'off', 'ilx_right': 'off'}),
    ({'ilx_left': 'prior', 'ilx_right': 'groups'},
     {'ilx_left': 'off', 'ilx_right': 'groups'}),
])
def test_switching_calibration_off_removes_its_earlier_sidecars(
        tmp_path, monkeypatch, logs, before, after):
    ws = _workspace(tmp_path, before)
    module, _calls = _module(tmp_path, monkeypatch, ws,
                             logging.getLogger('align-calibration-test'),
                             min_size=2)
    assert module.run()['Success'] is True
    left_images = [p for p in _images(ws)
                   if camera_registry.identify(p.name).key == 'ilx_left']
    own = left_images[0].with_name(left_images[0].name[:-len('.JPG')] + '.xmp')
    own.write_text('<x:xmpmeta/>\n', encoding='utf-8')
    manifest = ws / 'raw_images' / 'wildsync_intake.json'
    data = json.loads(manifest.read_text(encoding='utf-8'))
    for key, entry in data['calibration'].items():
        entry['mode'] = after[key]
    manifest.write_text(json.dumps(data), encoding='utf-8')

    seen = {}
    module, _calls = _module(
        tmp_path, monkeypatch, ws, logging.getLogger('align-calibration-test'),
        on_run=lambda args: seen.update(_sidecars(ws)), min_size=2)
    assert module.run()['Success'] is True
    expected = {}
    for image in _images(ws):
        camera = camera_registry.identify(image.name)
        if after[camera.key] in ('prior', 'groups'):
            expected[image.name[:-len('.JPG')] + '.xmp'] = _xmp(
                camera, after[camera.key])
    expected[own.name] = '<x:xmpmeta/>\n'
    assert seen == expected
    assert _sidecars(ws) == expected
    assert any('not written by this pipeline' in m for m in logs.messages), \
        logs.messages


def test_fingerprint_records_calibration_only_when_given(tmp_path):
    params = METADATA / 'AlignmentParams.xml'
    plain = align_fingerprint.build_fingerprint(None, None, str(params), 50)
    calibrated = align_fingerprint.build_fingerprint(
        None, None, str(params), 50,
        calibration={'ilx_right': 'groups', 'ilx_left': 'prior'},
        starting_focals={'ilx_right': 24.0})
    assert 'calibration' not in plain
    assert 'starting_focal_35mm' not in plain
    assert calibrated['calibration'] == {'ilx_left': 'prior',
                                         'ilx_right': 'groups'}
    assert calibrated['starting_focal_35mm'] == {'ilx_right': 24.0}
    assert align_fingerprint.diff_fingerprints(plain, plain) == []
    assert any('calibration delivery changed' in c for c in
               align_fingerprint.diff_fingerprints(plain, calibrated))
    refocused = dict(calibrated, starting_focal_35mm={'ilx_right': 29.0})
    assert align_fingerprint.diff_fingerprints(calibrated, refocused) == [
        ("starting focal of the groups sidecars changed: {'ilx_right': 24.0} "
         "-> {'ilx_right': 29.0}")]


# ------------------------------------------------------- starting focal

@pytest.mark.parametrize('modes', [
    {'ilx_left': 'groups', 'ilx_right': 'groups'},
    {'ilx_left': 'groups', 'ilx_right': 'prior'},
])
def test_the_manifest_starting_focal_reaches_every_groups_sidecar(
        tmp_path, monkeypatch, logs, modes):
    """The intake's observed focal goes, unchanged, from the manifest into
    the groups sidecar of every image of that camera as an initial focal;
    a prior camera keeps the calibration's focal. The fingerprint records
    the starting focal of the groups cameras."""
    ws = _workspace(tmp_path, modes)
    seen = {}
    module, _calls = _module(
        tmp_path, monkeypatch, ws, logging.getLogger('align-calibration-test'),
        on_run=lambda args: seen.update(_sidecars(ws)), min_size=2)
    assert module.run()['Success'] is True
    left = camera_registry.CAMERAS['ilx_left']
    right = camera_registry.CAMERAS['ilx_right']
    for image in _images(ws):
        camera = camera_registry.identify(image.name)
        text = seen[_sidecar_of(image).name]
        if modes[camera.key] == 'groups':
            focal = {'ilx_left': '29', 'ilx_right': '24.2016806723'}[camera.key]
            assert f'xcr:FocalLength35mm="{focal}" xcr:Skew="0"' in text
            assert 'xcr:CalibrationPrior="initial"' in text
        else:
            assert text == sidecar_xmp(right, 'prior')
            assert 'xcr:FocalLength35mm="17.72584"' in text
    fingerprint = json.loads(
        (ws / 'aligned_components' / 'zone_1' / 'align_inputs.json')
        .read_text(encoding='utf-8'))
    assert fingerprint['starting_focal_35mm'] == {
        key: FOCALS[key] for key, mode in modes.items() if mode == 'groups'}
    assert any('ilx_left=groups (starting focal 29 mm 35mm-eq.)' in m
               for m in logs.messages), logs.messages
    assert left.key in fingerprint['calibration']


def test_a_changed_starting_focal_is_reported_on_a_rerun(tmp_path,
                                                         monkeypatch, logs):
    ws = _workspace(tmp_path, {'ilx_left': 'groups', 'ilx_right': 'groups'})
    module, _calls = _module(tmp_path, monkeypatch, ws,
                             logging.getLogger('align-calibration-test'),
                             min_size=2)
    assert module.run()['Success'] is True
    manifest = ws / 'raw_images' / 'wildsync_intake.json'
    data = json.loads(manifest.read_text(encoding='utf-8'))
    data['calibration']['ilx_left']['starting_focal_35mm'] = 28.0
    manifest.write_text(json.dumps(data), encoding='utf-8')
    seen = {}
    module, _calls = _module(
        tmp_path, monkeypatch, ws, logging.getLogger('align-calibration-test'),
        on_run=lambda args: seen.update(_sidecars(ws)), min_size=2)
    assert module.run()['Success'] is True
    assert any('RETRY WITH CHANGED INPUTS' in m and 'starting focal of the '
               'groups sidecars changed' in m for m in logs.messages), \
        logs.messages
    left = camera_registry.CAMERAS['ilx_left']
    image = next(p for p in _images(ws)
                 if camera_registry.identify(p.name) is left)
    assert seen[_sidecar_of(image).name] == sidecar_xmp(left, 'groups', 28.0)


def test_a_groups_camera_without_a_starting_focal_aligns_nothing(
        tmp_path, monkeypatch, logs):
    """__align_zone refuses a groups camera with no starting focal before
    anything is written (the manifest reader refuses it earlier still)."""
    ws = _workspace(tmp_path, {'ilx_left': 'groups', 'ilx_right': 'groups'})
    module, calls = _module(tmp_path, monkeypatch, ws,
                            logging.getLogger('align-calibration-test'),
                            min_size=2)
    monkeypatch.setattr(
        module, 'intake_calibration',
        lambda: ({'ilx_left': 'groups', 'ilx_right': 'groups'},
                 {'ilx_left': 29.0}))
    output = module.run()
    assert output['Success'] is False
    _assert_nothing_aligned(ws, calls)
    errors = [c.get('Error', '') for c in output['Components'].values()]
    assert any('No starting focal for camera(s) ilx_right' in e
               for e in errors), errors


# ---------------------------------------------------------- small dataset

def test_one_stereo_pair_aligns_with_the_default_minimum(tmp_path,
                                                         monkeypatch):
    """Two images, default Min Component Size: the threshold drops to half
    the scene but never below 2, so a component holding both cameras
    counts."""
    ws = _workspace(tmp_path, {'ilx_left': 'groups', 'ilx_right': 'groups'},
                    frames=FRAMES[:1])
    module, calls = _module(tmp_path, monkeypatch, ws,
                            logging.getLogger('align-calibration-test'))
    output = module.run()
    assert output['Success'] is True, output
    assert calls[0][1][5] == '2'


@pytest.mark.parametrize('configured, images, expected', [
    (50, 30, 15),
    (50, 12, 6),
    (50, 3, 2),
    (50, 2, 2),
    (50, 1, 2),
    (50, 50, 50),
    (50, 400, 50),
    (4, 6, 4),
    (10, 9, 5),
])
def test_small_scene_threshold_is_half_the_scene(configured, images, expected):
    """A scene smaller than the configured minimum exports components that
    hold at least half of it (never fewer than 2 cameras), so one camera
    that fails to register no longer fails the zone."""
    assert ri_mod.small_scene_min_component_size(configured, images) == expected


def test_a_small_scene_exports_components_of_half_its_images(tmp_path,
                                                             monkeypatch,
                                                             logs):
    frames = tuple(f'20260820_1925{n:02d}.42' for n in range(10, 16))
    ws = _workspace(tmp_path, None, frames=frames)
    assert len(_images(ws)) == 12
    # configured 50 (the 1.0.0 default) so the half rule applies to 12 images
    module, calls = _module(tmp_path, monkeypatch, ws,
                            logging.getLogger('align-calibration-test'),
                            min_size=50)
    module.run()
    assert calls[0][1][5] == '6'
    assert any('>= 6 cameras' in m and 'configured 50' in m
               for m in logs.messages), logs.messages


def test_a_large_scene_keeps_the_configured_minimum(tmp_path, monkeypatch):
    ws = _workspace(tmp_path, None)
    module, calls = _module(tmp_path, monkeypatch, ws,
                            logging.getLogger('align-calibration-test'),
                            min_size=4)
    module.run()
    assert calls[0][1][5] == '4'


def test_no_exported_component_is_explained(tmp_path, monkeypatch, logs):
    ws = _workspace(tmp_path, None, frames=FRAMES[:1])
    module, _calls = _module(tmp_path, monkeypatch, ws,
                             logging.getLogger('align-calibration-test'))
    original = module.cli.run_batch_script

    def no_component(script, args, log_dir, display_output=False, **kw):
        original(script, args, log_dir, display_output)
        os.remove(os.path.join(args[1], f'{args[4]}_c0.rsalign'))
        return WorkflowResult(True, 0, 'align.log', '', [], 0.0)

    monkeypatch.setattr(module.cli, 'run_batch_script', no_component)
    assert module.run()['Success'] is False
    message = next(m for m in logs.messages if 'no component of >=' in m)
    assert 'RealityScan finished' in message
    assert '(2 image(s) in the scene)' in message
    assert 'no component of >= 2 cameras' in message
    assert 'threshold used for this scene; configured 10' in message
    assert 'no component identity was captured' in message
    assert '--r_min_component_size' in message


def test_components_below_the_minimum_size_are_set_aside_and_reported(
        tmp_path, monkeypatch, logs):
    """The identity loop captures every component; the orchestrator keeps
    the ones of at least the minimum size, removes the rest from the zone
    output and records their sizes (owner decision D6, 2026-10-07)."""
    modes = {'ilx_left': 'groups', 'ilx_right': 'groups'}
    ws = _workspace(tmp_path, modes)
    images = _images(ws)
    assert len(images) >= 3

    def on_run(args):
        out = Path(args[1])
        # lap 0 harvests every registered camera, lap 1 the one left after
        # the maximal component was peeled, lap 2 is the empty terminal
        for lap, members in ((0, images), (1, images[-1:]), (2, [])):
            harvest = out / f'identity_r{lap}'
            harvest.mkdir()
            for image in members:
                (harvest / (image.name[:-len('.JPG')] + '.xmp')).write_text(
                    POSE_SIDECAR, encoding='utf-8')
        (out / f'{args[4]}_c1.rsalign').write_bytes(b'small component')

    module, _calls = _module(tmp_path, monkeypatch, ws,
                             logging.getLogger('align-calibration-test'),
                             on_run=on_run, min_size=2)
    output = module.run()
    assert output['Success'] is True, output
    out = ws / 'aligned_components' / 'zone_1'
    assert (out / 'zone_1_c0.rsalign').is_file()
    assert (out / 'zone_1_c0.rsalign.manifest.json').is_file()
    assert not (out / 'zone_1_c1.rsalign').exists()
    assert not (out / 'zone_1_c1.rsalign.manifest.json').exists()
    record = json.loads((out / 'components_below_min_size.json')
                        .read_text(encoding='utf-8'))
    assert record == {'zone': 'zone_1', 'min_component_size': 2,
                      'components': {'zone_1_c1': 1}}
    zone, = output['Components'].values()
    assert zone['Component Count'] == 1
    assert zone['Registered Cameras'] == len(images) - 1
    assert any('1 component(s) below 2 cameras set aside' in m
               for m in logs.messages), logs.messages


# ------------------------------------------------------- AlignZone.bat text

def _bat_bytes() -> bytes:
    return (SCRIPTS / 'AlignZone.bat').read_bytes()


def test_align_zone_is_crlf_throughout():
    data = _bat_bytes()
    assert b'\n' not in data.replace(b'\r\n', b'')
    assert b'\r' not in data.replace(b'\r\n', b'')


def test_align_zone_text_for_the_command_file_mode():
    text = _bat_bytes().decode('utf-8')
    assert 'set "calibration_rscmd=%~7"' in text
    assert ('if not "%calibration_rscmd%" == "" if not exist '
            '"%calibration_rscmd%" goto :rscmdMissing') in text
    assert ('if not "%calibration_rscmd%" == "" if defined RS_ALIGN_POOL_DIR '
            'if not "%RS_ALIGN_POOL_DIR%" == "" goto :rscmdWithPool') in text
    block = re.search(
        r'(?m)^:addViaRscmd\r\n(.*?)^:addViaList\r\n', text, re.DOTALL).group(1)
    assert 'call :run -execRSCMD "%calibration_rscmd%" || goto :fail' in block
    assert 'goto :imagesAdded' in block
    assert '-addFolder' not in block
    # Both refusals happen before an instance boots, and through labels
    # (an exit /b inside a parenthesised block returns 0 to the caller).
    boot = text.index('call "%~dp0startRealityScan.bat"')
    assert text.index('goto :rscmdMissing') < boot
    assert text.index('goto :rscmdWithPool') < boot
    for label in ('rscmdMissing', 'rscmdWithPool'):
        body = re.search(rf'(?m)^:{label}\r\n(.*?)\r\n\r\n', text, re.DOTALL).group(1)
        assert body.splitlines()[-1] == 'exit /b 1', body
    # The pool layout still wins the add step; -addFolder stays the default.
    pool = text.index('goto :addViaList\r\n')
    rscmd = text.index('goto :addViaRscmd\r\n')
    folder = text.index('call :run -addFolder "%input_dir%" || goto :fail')
    assert pool < rscmd < folder


# ------------------------------------------------ AlignZone.bat execution

STUB_RS = ('@echo off\r\n'
           '>>"%RS_STUB_LOG%" echo %*\r\n'
           'if /I "%~3" == "-align" >"%RS_STUB_ERRORS%" echo stub align failure\r\n'
           'exit /b 0\r\n')


def _stub_scripts(tmp_path: Path) -> Path:
    scripts = tmp_path / 'scripts'
    scripts.mkdir()
    shutil.copyfile(SCRIPTS / 'AlignZone.bat', scripts / 'AlignZone.bat')
    (scripts / 'rs_stub.cmd').write_bytes(STUB_RS.encode('ascii'))
    (scripts / 'startRealityScan.bat').write_bytes(b'@exit /b 0\r\n')
    (scripts / 'SetVariables.bat').write_bytes('\r\n'.join([
        '@echo off',
        'set RealityScan=call "%~dp0rs_stub.cmd"',
        f'set Metadata={METADATA}',
        'set ErrorPath=%~dp0errors',
        'if not exist "%ErrorPath%" mkdir "%ErrorPath%"',
        '']).encode('ascii'))
    return scripts


def _run_align_zone(tmp_path: Path, extra_args: list[str],
                    env_extra: dict[str, str] | None = None
                    ) -> tuple[int, list[str], str]:
    scripts = _stub_scripts(tmp_path)
    images = tmp_path / 'images'
    images.mkdir(exist_ok=True)
    log = tmp_path / 'rs_calls.txt'
    env = {k: v for k, v in os.environ.items()
           if k not in ('RS_ALIGN_POOL_DIR', 'RS_ALIGN_PARAMS',
                        'RS_PROJECTS_DIR', 'RS_PROJECT_LABEL')}
    env.update({'RS_INSTANCE': 'RS1', 'RS_STUB_LOG': str(log),
                'RS_STUB_ERRORS': str(scripts / 'errors' / 'errors_RS1.txt')})
    env.update(env_extra or {})
    completed = subprocess.run(
        [str(scripts / 'AlignZone.bat'), str(images), str(tmp_path / 'out'),
         '', '', 'zone_1', '2', *extra_args],
        cwd=str(scripts), env=env, capture_output=True, text=True,
        timeout=120, creationflags=subprocess.CREATE_NO_WINDOW, check=False)
    calls = (log.read_text(encoding='utf-8', errors='replace').splitlines()
             if log.exists() else [])
    return completed.returncode, calls, completed.stdout


windows_only = pytest.mark.skipif(os.name != 'nt', reason='runs cmd.exe')


@windows_only
def test_align_zone_adds_images_from_the_command_file(tmp_path):
    rscmd = tmp_path / 'zone_1.rscmd'
    rscmd.write_bytes(b'-add "x.jpg"\r\n')
    code, calls, stdout = _run_align_zone(tmp_path, [str(rscmd)])
    delegated = [c for c in calls if c.startswith('-delegateTo RS1 ')]
    assert f'-delegateTo RS1 -execRSCMD "{rscmd}"' in delegated, calls
    assert not any('-addFolder' in c or 'appIncSubdirs' in c for c in calls)
    assert any(c.startswith('-delegateTo RS1 -set "sfmDistortionModel=Brown3"')
               for c in calls), calls
    assert f'Calibration Command File: {rscmd}' in stdout
    # The stub fails -align, so the run ends through :fail.
    assert code == 1 and any(c.endswith('-align') for c in delegated)


@windows_only
def test_align_zone_without_a_command_file_adds_the_folder(tmp_path):
    code, calls, stdout = _run_align_zone(tmp_path, [])
    images = tmp_path / 'images'
    assert '-delegateTo RS1 -set "appIncSubdirs=true"' in calls, calls
    assert f'-delegateTo RS1 -addFolder "{images}"' in calls, calls
    assert not any('-execRSCMD' in c for c in calls)
    assert 'Calibration Command File' not in stdout
    assert code == 1


@windows_only
def test_align_zone_refuses_a_missing_command_file_before_booting(tmp_path):
    code, calls, stdout = _run_align_zone(
        tmp_path, [str(tmp_path / 'missing.rscmd')])
    assert code == 1
    assert calls == []
    assert 'calibration command file not found' in stdout


@windows_only
def test_align_zone_refuses_a_command_file_with_the_pool_layout(tmp_path):
    rscmd = tmp_path / 'zone_1.rscmd'
    rscmd.write_bytes(b'-add "x.jpg"\r\n')
    code, calls, stdout = _run_align_zone(
        tmp_path, [str(rscmd)], {'RS_ALIGN_POOL_DIR': str(tmp_path / 'pool')})
    assert code == 1
    assert calls == []
    assert 'cannot be combined with the pool layout' in stdout


# ------------------------------------------------------ AlignmentParams.xml

def test_alignment_params_use_the_global_brown3_model():
    text = (METADATA / 'AlignmentParams.xml').read_text(encoding='utf-8')
    assert '<entry key="sfmDistortionModel" value="Brown3"/>' in text
    assert 'global and all-or-nothing' in text
    assert 'per-camera via XMP' not in text


def test_module_reads_the_manifest_from_the_workspace_raw_images(tmp_path):
    ws = _workspace(tmp_path, {'ilx_left': 'prior', 'ilx_right': 'groups'})
    module = RealityScanAlignment(logging.getLogger('align-calibration-test'))
    module.params = {'output_dir': _param('output_dir', str(ws))}
    assert module.intake_calibration_modes() == {'ilx_left': 'prior',
                                                 'ilx_right': 'groups'}
    assert module.intake_calibration() == (
        {'ilx_left': 'prior', 'ilx_right': 'groups'}, FOCALS)
    assert ri_mod.MANIFEST_NAME == 'wildsync_intake.json'
