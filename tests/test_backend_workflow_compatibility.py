"""Legacy WCA/Zeuss contracts alongside optional Sony intake.

Native execution is stubbed: these verify inputs delivered to RealityScan,
not acceptance or reconstruction quality on native Windows/CUDA.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from modules import camera_registry
from modules.flight_logs import params_template_frame
from modules.image_batcher.batch_directory import BatchDirectory
from modules.realityscan_interface import realityscan_cli as cli_mod
from modules.realityscan_interface import realityscan_interface as ri_mod
from tests.test_alignment_calibration_delivery import _module, _param, _workspace
from tests.test_batch_copy_accounting import _module as batch_module
from tests.test_cmd_boundary_guards import _cli

LOGGER = logging.getLogger(__name__)
LEGACY_NAMES = ('P231C0001_20260720120000.jpg',
                'C231C0002_20260720120000.jpg',
                'zeuss_20260720120000.jpg')


def _legacy_zone(tmp_path):
    ws = tmp_path / 'ws'
    zone = ws / 'batched_images_by_zone' / 'zone_1'
    zone.mkdir(parents=True)
    for name in LEGACY_NAMES:
        (zone / name).write_bytes(b'jpg')
    # The untagged name is the established local-Euclidean contract.
    (zone / 'flight_log_UTM.txt').write_text(
        'filename;X (East);Y (North);Alt\n' + '\n'.join(
            f'{name};{i};{i + 1};-5' for i, name in enumerate(LEGACY_NAMES)) + '\n')
    return ws, zone


def test_global_distortion_defaults_are_dataset_scoped(monkeypatch):
    monkeypatch.delenv('RS_ALIGN_PARAMS', raising=False)
    generic = Path(ri_mod.alignment_params_template(False))
    sony = Path(ri_mod.alignment_params_template(True))
    assert 'value="Division"' in generic.read_text()
    assert 'value="Brown3"' in sony.read_text()
    monkeypatch.setenv('RS_ALIGN_PARAMS', 'my_settings.xml')
    assert ri_mod.alignment_params_template(False) == os.path.abspath('my_settings.xml')
    assert ri_mod.alignment_params_template(True) == os.path.abspath('my_settings.xml')


def test_mixed_wca_zeuss_local_alignment_keeps_contracts(tmp_path, monkeypatch):
    ws, zone = _legacy_zone(tmp_path)
    def during_run(args):
        assert len(args) == 6  # legacy addFolder invocation, no Sony .rscmd
        assert params_template_frame(args[3]) == 'local_euclidean'
        assert args[5] == '50'  # no Sony small-scene lowering
        assert args[0] == str(zone)
    module, calls = _module(tmp_path, monkeypatch, ws, LOGGER, on_run=during_run)
    assert module.run()['Success'] is True
    assert len(calls) == 1  # camera subfolders remain one mixed scene
    assert Path(module.cli.alignment_params_path).name == 'AlignmentParams.xml'
    assert module.cli.capture_all_components is False
    for name in LEGACY_NAMES:
        camera = camera_registry.identify(name)
        assert camera is not None
        assert (zone / name).with_suffix('.xmp').read_text() == camera_registry.calibration_xmp(camera)
    fp = json.loads((ws / 'aligned_components/zone_1/align_inputs.json').read_text())
    assert fp['frame'] == 'local_euclidean'
    assert fp['min_component_size'] == 50


def test_alignment_script_override_is_preserved(tmp_path, monkeypatch):
    ws, _zone = _legacy_zone(tmp_path)
    monkeypatch.setenv('RS_ALIGN_SCRIPT', 'MyAlign.bat')
    module, calls = _module(tmp_path, monkeypatch, ws, LOGGER)
    assert module.run()['Success']
    assert calls[0][0] == 'MyAlign.bat'


def test_sony_intake_keeps_its_own_model_and_threshold(tmp_path, monkeypatch):
    monkeypatch.delenv('RS_ALIGN_PARAMS', raising=False)
    ws = _workspace(tmp_path, {'ilx_left': 'off', 'ilx_right': 'off'})
    module, calls = _module(tmp_path, monkeypatch, ws, LOGGER)
    assert module.run()['Success']
    assert calls[0][1][5] == '3'  # 6-image Sony scene, 10 configured
    assert Path(module.cli.alignment_params_path).name == 'AlignmentParamsSony.xml'
    assert module.cli.capture_all_components is True


def test_legacy_batch_calibration_flag_is_opt_in_and_unchanged(tmp_path):
    module = batch_module()
    assert module.get_parameters()['batch_single_zone_below'].get_default_value() == 0
    assert module.get_parameters()['batch_xmp_priors'].cli_long == 'b_xmp_priors'
    source = tmp_path / 'source'
    source.mkdir()
    for name in LEGACY_NAMES:
        (source / name).write_bytes(b'image')
    module.params = {'batch_xmp_priors': _param('batch_xmp_priors', True)}
    copied, missing = module._BatchDirectory__copy_files(
        str(source), str(tmp_path / 'zone'), LEGACY_NAMES)
    assert (copied, missing) == (3, 0)
    for name in LEGACY_NAMES:
        camera = camera_registry.identify(name)
        sidecars = list((tmp_path / 'zone').rglob(Path(name).stem + '.xmp'))
        assert len(sidecars) == 1
        assert sidecars[0].read_text() == camera_registry.calibration_xmp(camera)


def test_batch_calibration_does_not_overwrite_user_sidecar(tmp_path):
    module = batch_module()
    module.params = {'batch_xmp_priors': _param('batch_xmp_priors', True)}
    source = tmp_path / 'source'
    source.mkdir()
    (source / LEGACY_NAMES[0]).write_bytes(b'image')
    destination = tmp_path / 'zone'
    # Resolve the normal camera-specific location by one initial copy.
    module._BatchDirectory__copy_files(str(source), str(destination), LEGACY_NAMES[:1])
    sidecar = next(destination.rglob('*.xmp'))
    sidecar.write_text('user calibration')
    with pytest.raises(ValueError, match='Existing sidecar differs'):
        module._BatchDirectory__copy_files(str(source), str(destination), LEGACY_NAMES[:1])
    assert sidecar.read_text() == 'user calibration'


@pytest.mark.parametrize('explicit', [False, True])
def test_scoped_alignment_settings_reach_child_without_global_change(
        tmp_path, monkeypatch, explicit):
    cli = _cli(tmp_path)
    cli.alignment_params_path = str(tmp_path / 'Sony.xml')
    cli.capture_all_components = True
    if explicit:
        monkeypatch.setenv('RS_ALIGN_PARAMS', 'Operator.xml')
    else:
        monkeypatch.delenv('RS_ALIGN_PARAMS', raising=False)
    monkeypatch.setattr(cli_mod, 'ERRORS_DIR', str(tmp_path / 'errors'))
    monkeypatch.setattr(cli, 'is_instance_running', lambda: False)
    monkeypatch.setattr(cli, 'wait_for_instance_shutdown', lambda: True)
    monkeypatch.setattr(cli, '_monitor_until_exit', lambda *a, **k: None)
    expected = os.path.abspath('Operator.xml') if explicit else cli.alignment_params_path
    def capture(command, **kwargs):
        assert kwargs['env']['RS_ALIGN_PARAMS'] == expected
        assert kwargs['env']['RS_CAPTURE_ALL_COMPONENTS'] == '1'
        return SimpleNamespace(returncode=1)
    monkeypatch.setattr(cli_mod.subprocess, 'Popen', capture)
    cli.run_batch_script('AlignZone.bat', [], str(tmp_path / 'logs'))
    assert os.environ.get('RS_ALIGN_PARAMS') == ('Operator.xml' if explicit else None)


def test_model_flags_and_native_utility_workflows_remain_available():
    params = ri_mod.RealityScanAlignment(LOGGER).get_parameters()
    assert {params[name].cli_long for name in
            ('rs_model_generate', 'rs_model_cull_poly', 'rs_model_texture',
             'rs_model_simplify')} == {
        'r_model_generate', 'r_model_cull_poly', 'r_model_texture', 'r_model_simplify'}
    for name in ('AlignImagesFromFolder', 'CalibCellAlign', 'ComputeModel',
                 'GuiWorkbench', 'NightGrow'):
        payload = Path(cli_mod.SCRIPTS_DIR, name + '.bat').read_bytes()
        assert b'\r\n' in payload
        assert b'\n' not in payload.replace(b'\r\n', b'')


def test_legacy_batch_flag_cannot_bypass_sony_calibration_checks(tmp_path):
    module = batch_module()
    module.params = {'batch_xmp_priors': _param('batch_xmp_priors', True)}
    source = tmp_path / 'source'
    source.mkdir()
    name = 'Cam1_20260820_192542.42.card.JPG'
    (source / name).write_bytes(b'image')
    assert module._BatchDirectory__copy_files(
        str(source), str(tmp_path / 'zone'), [name]) == (1, 0)
    assert not list((tmp_path / 'zone').rglob('*.xmp'))


def test_foreign_standalone_images_do_not_inherit_sony_workspace_settings(tmp_path, monkeypatch):
    ws = _workspace(tmp_path, {'ilx_left': 'prior', 'ilx_right': 'groups'})
    foreign = tmp_path / 'legacy_images'
    foreign.mkdir()
    module, _ = _module(tmp_path, monkeypatch, ws, LOGGER,
                         extra_params={'rs_input_image_dir': _param('input', str(foreign))})
    assert module.intake_calibration() is None


def test_alignment_discovers_georeference_output_in_its_original_image_tree(tmp_path):
    imagery = tmp_path / 'original_images'
    imagery.mkdir()
    nav = imagery / 'flight_log_53N_UTM.txt'
    nav.write_text('filename;X (East);Y (North);Alt\n')
    module = ri_mod.RealityScanAlignment(LOGGER)
    module.params = {'output_dir': _param('output', str(tmp_path / 'workspace')),
                     'geo_input_image_dir': _param('input', str(imagery))}
    assert module._RealityScanAlignment__get_flight_log_path() == str(nav)


def test_native_direct_align_retains_requested_threshold_and_default():
    text = Path(cli_mod.SCRIPTS_DIR, 'AlignZone.bat').read_text()
    assert 'if "%min_component_size%" == "" set "min_component_size=50"' in text
    assert 'set "native_min_component_size=%min_component_size%"' in text
    assert 'if "%RS_CAPTURE_ALL_COMPONENTS%" == "1" set "native_min_component_size=1"' in text
    assert 'call :run -setMinComponentSize %native_min_component_size% || goto :fail' in text
