"""Input workflow selection keeps WCA/Zeuss and Wild Sync independent."""
from __future__ import annotations

import logging

import pytest

import main as driver
from wildscan.session import (
    LEGACY_CHAIN_STAGES,
    POST_STAGES,
    Session,
    build_commands,
    default_enabled,
)
from wildscan.workspace import Workspace

QUIET = logging.getLogger(__name__)


def test_noninteractive_default_retains_legacy_module_names(monkeypatch):
    monkeypatch.setenv('RS_NO_INTERACTIVE', '1')
    monkeypatch.delenv('RS_MODULES', raising=False)
    modules = driver.initialize_modules(QUIET)
    assert list(modules) == ['Extract Images', 'Georeference Images',
                            'Preprocess Images', 'Batch Directory',
                            'RealityScan Alignment']
    assert 'Wild Sync Intake' not in modules
    params = driver.initialize_parameters(modules)
    parser = driver.build_arg_parser(params)
    parsed = parser.parse_args(['--i_input', 'videos', '--g_flight_log', 'nav.csv',
                               '--g_type', 'WCA'])
    assert parsed.g_type == 'WCA'


def test_wildsync_is_an_explicit_alternative(monkeypatch):
    monkeypatch.setenv('RS_MODULES', 'Wild Sync Intake,Preprocess Images')
    modules = driver.initialize_modules(QUIET)
    assert list(modules) == ['Wild Sync Intake', 'Preprocess Images']
    params = driver.initialize_parameters(modules)
    assert params['ws_run_dirs'].cli_long == 'w_input'
    assert 'pre_input_image_dir' not in params


def test_georeference_does_not_remove_explicit_preprocess_input_override(monkeypatch):
    monkeypatch.setenv('RS_MODULES', 'Georeference Images,Preprocess Images')
    params = driver.initialize_parameters(driver.initialize_modules(QUIET))
    parsed = driver.build_arg_parser(params).parse_args([
        '--g_input', 'source/images', '--g_flight_log', 'nav.csv',
        '--g_type', 'Zeuss', '--p_input', 'source/images'])
    assert parsed.p_input == 'source/images'
    s = Session(enabled=['georeference', 'preprocess'], answers={
        'g_input': 'source/images', 'g_flight_log': 'nav.csv',
        'g_type': 'Zeuss', 'p_input': 'source/images'})
    assert '--p_input' in build_commands(s)[0].argv


def test_long_only_option_has_no_none_alias_and_legacy_short_flags_still_work(
        monkeypatch, capsys):
    monkeypatch.setenv('RS_MODULES', 'Batch Directory')
    parser = driver.build_arg_parser(driver.initialize_parameters(
        driver.initialize_modules(QUIET)))
    parsed = parser.parse_args(['--b_allow_positionless_scene', 'true',
                               '-b_i', 'images'])
    assert parsed.b_allow_positionless_scene is True
    assert parsed.b_input == 'images'
    assert '-None' not in parser.format_help()
    with pytest.raises(SystemExit) as error:
        parser.parse_args(['-None', 'true'])
    assert error.value.code == 2
    assert 'unrecognized arguments' in capsys.readouterr().err


@pytest.mark.parametrize('legacy', ['Extract Images', 'Georeference Images'])
def test_main_rejects_conflicting_input_producers(monkeypatch, legacy):
    monkeypatch.setenv('RS_MODULES', f'Wild Sync Intake,{legacy}')
    with pytest.raises(SystemExit) as exc:
        driver.initialize_modules(QUIET)
    assert exc.value.code == 2


def test_help_lists_both_workflows_without_changing_environment(monkeypatch):
    monkeypatch.setenv('RS_MODULES', 'Wild Sync Intake')
    parser = driver.build_arg_parser(driver.initialize_parameters(
        driver.initialize_modules(QUIET, help_only=True)))
    help_text = parser.format_help()
    assert all(flag in help_text for flag in ('--i_input', '--g_type', '--w_input'))
    assert __import__('os').environ['RS_MODULES'] == 'Wild Sync Intake'


def test_generic_workspace_does_not_enable_wildsync_by_default(tmp_path):
    assert default_enabled(Workspace(tmp_path)) == LEGACY_CHAIN_STAGES + POST_STAGES


def test_legacy_camera_notes_and_labels_stay_out_of_pipeline_args(tmp_path):
    s = Session(expedition='na156', dive='h2024', results_root=str(tmp_path),
                enabled=['georeference'], answers={
                    'g_input': 'images', 'g_flight_log': 'nav.csv', 'g_type': 'Zeuss',
                    'cam_test_name': 'Survey camera', 'w_input': 'unrelated/run'})
    cmd = build_commands(s)[0]
    assert cmd.env['RS_MODULES'] == 'Georeference Images'
    assert '--g_type' in cmd.argv
    assert '--cam_test_name' not in cmd.argv
    assert '--w_input' not in cmd.argv
    assert s.label == 'NA156_H2024'


@pytest.mark.parametrize('legacy', ['extract', 'georeference'])
def test_portal_rejects_mixed_input_workflows_before_launch(tmp_path, legacy):
    s = Session(results_root=str(tmp_path), enabled=['intake', legacy])
    with pytest.raises(ValueError, match='alternative input workflows'):
        build_commands(s)


def test_interrupted_intake_without_manifest_keeps_its_input_workflow(tmp_path):
    import json
    from wildscan.app import session_stages

    (tmp_path / 'interrupted_stage.json').write_text(json.dumps({
        'cancelled': True, 'stages': ['intake', 'preprocess']}), encoding='utf-8')
    stages = default_enabled(Workspace(tmp_path))
    assert 'intake' in stages
    assert 'georeference' not in stages and 'extract' not in stages
    assert session_stages(Session(results_root=str(tmp_path)))[0] == 'intake'


def _saved_session_store(monkeypatch, **values):
    import wildscan.session as session_mod

    class Store:
        def get(self, section, key, fallback=None):
            return values.get(key, fallback) if section == 'wildscan' else fallback

    monkeypatch.setattr(session_mod, '_settings', Store)


def test_explicit_generic_workspace_does_not_inherit_saved_sony_sources(
        monkeypatch, tmp_path):
    from wildscan.app import WildScanApp, session_stages

    generic = tmp_path / 'generic'
    (generic / 'raw_images').mkdir(parents=True)
    _saved_session_store(monkeypatch, results_root=str(tmp_path / 'sony'),
                         run_dirs='earlier/sony/run', expedition='NA156',
                         answers={'w_variant': 'review', 'g_type': 'WCA'})
    app = WildScanApp(str(generic))
    assert app.session.run_dirs == ''
    assert session_stages(app.session) == LEGACY_CHAIN_STAGES + POST_STAGES
    assert app.session.expedition == 'NA156'
    assert app.session.answers == {'w_variant': 'review', 'g_type': 'WCA'}


@pytest.mark.parametrize('marker', ['manifest', 'interrupted'])
def test_explicit_sony_workspace_uses_its_own_workflow_not_saved_generic_sources(
        monkeypatch, tmp_path, marker):
    import json
    from wildscan.app import WildScanApp, session_stages

    sony = tmp_path / 'sony'
    sony.mkdir()
    if marker == 'manifest':
        (sony / 'raw_images').mkdir()
        (sony / 'raw_images' / 'wildsync_intake.json').write_text('{}', encoding='utf-8')
    else:
        (sony / 'interrupted_stage.json').write_text(json.dumps({
            'cancelled': True, 'stages': ['intake']}), encoding='utf-8')
    _saved_session_store(monkeypatch, results_root=str(tmp_path / 'generic'),
                         cruise_folder='earlier/cruise', raw_images_dir='earlier/images',
                         video_path='earlier/video.mov', processed_data='earlier/nav',
                         answers={'g_type': 'Zeuss'})
    app = WildScanApp(str(sony))
    assert session_stages(app.session)[0] == 'intake'
    for field in ('cruise_folder', 'raw_images_dir', 'video_path', 'processed_data'):
        assert getattr(app.session, field) == ''
    assert app.session.answers == {'g_type': 'Zeuss'}


def test_same_workspace_keeps_the_saved_fresh_sony_source_selection(monkeypatch, tmp_path):
    from wildscan.app import WildScanApp, session_stages

    _saved_session_store(monkeypatch, results_root=str(tmp_path),
                         run_dirs='runs/not-imported-yet', answers={'w_variant': 'review'})
    app = WildScanApp(str(tmp_path / '.'))
    assert app.session.run_dirs == 'runs/not-imported-yet'
    assert session_stages(app.session)[0] == 'intake'
    assert app.session.answers['w_variant'] == 'review'


def test_same_generic_workspace_preserves_saved_source_fields(monkeypatch, tmp_path):
    from wildscan.app import WildScanApp

    _saved_session_store(monkeypatch, results_root=str(tmp_path),
                         cruise_folder='cruise', raw_images_dir='images',
                         video_path='video.mov', processed_data='nav',
                         answers={'g_type': 'Zeuss'})
    session = WildScanApp(str(tmp_path / '.')).session
    assert session.cruise_folder == 'cruise'
    assert session.raw_images_dir == 'images'
    assert session.video_path == 'video.mov'
    assert session.processed_data == 'nav'
    assert session.answers['g_type'] == 'Zeuss'
