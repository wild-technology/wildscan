"""Calibration delivery in the alignment stage.

The alignment module reads the per-camera calibration decision from the
Wild Sync intake manifest. For ``prior`` / ``groups`` it writes the
decided sidecar beside every image of that camera and an ``.rscmd`` that
AlignZone.bat executes instead of ``-addFolder``; for ``off`` (or no
manifest) the zone is aligned exactly as without calibration delivery.

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
            'schema': 1, 'status': 'complete',
            'calibration': {key: {'camera': key, 'mode': mode,
                                  'reason': 'test'}
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
            expected[image.name[:-len('.JPG')] + '.xmp'] = sidecar_xmp(
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
    assert hygiene == [('sanitize', (zone, None)), ('ensure', (zone, None)),
                       ('ensure', (zone, None))]
    fingerprint = json.loads(
        (ws / 'aligned_components' / 'zone_1' / 'align_inputs.json')
        .read_text(encoding='utf-8'))
    assert 'calibration' not in fingerprint


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
    json.dumps({'schema': 1, 'status': 'partial', 'calibration': {}}),
    json.dumps({'schema': 1, 'status': 'complete',
                'calibration': {'ilx_left': {'mode': 'locked'}}}),
    json.dumps({'schema': 1, 'status': 'complete',
                'calibration': {'some_camera': {'mode': 'groups'}}}),
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
        assert text == sidecar_xmp(camera, modes[camera.key]), image.name
        assert 'xcr:Position' not in text


def test_a_stale_sidecar_of_another_form_is_replaced_before_the_run(
        tmp_path, monkeypatch):
    modes = {'ilx_left': 'groups', 'ilx_right': 'groups'}
    ws = _workspace(tmp_path, modes)
    left = camera_registry.CAMERAS['ilx_left']
    stale = _images(ws)[0]
    assert camera_registry.identify(stale.name) is left
    name = stale.name[:-len('.JPG')] + '.xmp'
    stale.with_name(name).write_text(sidecar_xmp(left, 'prior'),
                                     encoding='utf-8')
    seen = {}
    module, _calls = _module(
        tmp_path, monkeypatch, ws, logging.getLogger('align-calibration-test'),
        on_run=lambda args: seen.update(_sidecars(ws)), min_size=2)
    module.run()
    assert seen[name] == sidecar_xmp(left, 'groups')


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
            expected[image.name[:-len('.JPG')] + '.xmp'] = sidecar_xmp(
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
        calibration={'ilx_right': 'groups', 'ilx_left': 'prior'})
    assert 'calibration' not in plain
    assert calibrated['calibration'] == {'ilx_left': 'prior',
                                         'ilx_right': 'groups'}
    assert align_fingerprint.diff_fingerprints(plain, plain) == []
    assert any('calibration delivery changed' in c for c in
               align_fingerprint.diff_fingerprints(plain, calibrated))


# ---------------------------------------------------------- small dataset

def test_one_stereo_pair_aligns_with_the_default_minimum(tmp_path,
                                                         monkeypatch):
    """Two images, default Min Component Size: the threshold drops to the
    scene's image count, so a component holding both cameras counts."""
    ws = _workspace(tmp_path, {'ilx_left': 'groups', 'ilx_right': 'groups'},
                    frames=FRAMES[:1])
    module, calls = _module(tmp_path, monkeypatch, ws,
                            logging.getLogger('align-calibration-test'))
    output = module.run()
    assert output['Success'] is True, output
    assert calls[0][1][5] == '2'


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
    assert '--r_min_component_size' in message


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
    assert ri_mod.MANIFEST_NAME == 'wildsync_intake.json'
