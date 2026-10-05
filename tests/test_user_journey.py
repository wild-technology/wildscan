"""Offline first-run navigation and truthful workspace feedback."""
from __future__ import annotations

import asyncio

import pytest
from textual.widgets import Input, ProgressBar

import wildscan.app as ui
import wildscan.session as session_mod
from tests.test_wildscan import FakeStore
from wildscan.session import Question, RawDataScan, StageCommand


@pytest.fixture
def settings(monkeypatch):
    store = FakeStore()
    monkeypatch.setattr(session_mod, '_settings', lambda: store)
    return store


@pytest.mark.parametrize('labels', [('NA173', 'H2104'), ('', '')])
def test_last_run_keeps_the_exact_chosen_results_folder(tmp_path, settings, labels):
    root = str(tmp_path / 'custom results folder')
    session = session_mod.Session(expedition=labels[0], dive=labels[1], results_root=root)
    session_mod.save_last_run(session)
    restored = session_mod.default_session()
    assert restored.results_root == root
    assert settings.data['wildscan']['results_root'] == root


def test_previous_settings_without_results_root_keep_the_legacy_label_fallback(
        tmp_path, settings):
    settings.set('wildscan', 'results_base', str(tmp_path))
    settings.set('wildscan', 'expedition', 'NA173')
    settings.set('wildscan', 'dive', 'H2104')
    assert session_mod.default_session().results_root == str(tmp_path / 'na173_h2104')


@pytest.mark.parametrize('stage,arg', [('preprocess', 'p_input'),
                                     ('batch', 'b_input'), ('align', 'r_input')])
def test_standalone_image_stages_require_an_input_in_the_wizard(tmp_path, stage, arg):
    session = session_mod.Session(results_root=str(tmp_path / 'results'), enabled=[stage])
    question = next(q for q in session_mod.build_questions(session, RawDataScan())
                    if q.arg == arg)
    assert question.required and question.validate('') is not None


@pytest.mark.parametrize('prepared', ['batched_images_by_zone',
                                    'preprocessed_images', 'raw_images'])
def test_align_defaults_prefer_the_current_prepared_source(tmp_path, prepared):
    results = tmp_path / 'current results'
    for name in ('raw_images', 'preprocessed_images', 'batched_images_by_zone'):
        (results / name).mkdir(parents=True)
        if name == prepared:
            break
    session = session_mod.Session(results_root=str(results), enabled=['align'],
                                  answers={'r_input': 'old/dataset',
                                           'r_flight_log': 'old/flight_log.txt',
                                           'r_project_label': 'OTHER_DIVE'})
    questions = {q.arg: q for q in session_mod.build_questions(session, RawDataScan())}
    assert questions['r_input'].default == str(results / prepared)
    assert questions['r_flight_log'].default == ''
    assert questions['r_flight_log'].validate('') is None
    assert questions['r_project_label'].default == ''


def test_align_resume_discovers_each_zone_log_after_the_real_command_parser(
        tmp_path, settings, monkeypatch):
    import main as driver
    from modules.realityscan_interface.realityscan_interface import RealityScanAlignment
    from tests.test_align_and_rollback_safety import QUIET, _batched, _module_with_stub

    results = tmp_path / 'current results'
    zones = _batched(results)
    session = session_mod.Session(results_root=str(results), enabled=['align'], answers={
        'r_input': 'old/dataset', 'r_flight_log': 'old/flight_log.txt',
        'r_project_label': 'OTHER_DIVE'})
    questions = session_mod.build_questions(session, RawDataScan())
    session.answers.update({q.arg: q.default for q in questions})
    command = session_mod.build_commands(session)[0]
    settings.set('main', 'r_flight_log', 'old/flight_log.txt')
    settings.set('main', 'r_project_label', 'OTHER_DIVE')
    monkeypatch.setattr(driver, 'SettingsStore', lambda: settings)
    monkeypatch.setattr('builtins.input', lambda *args: pytest.fail('Unexpected prompt'))
    params = driver.initialize_parameters({'RealityScan Alignment': RealityScanAlignment(QUIET)})
    driver.parse_arguments(command.argv[1:], params, QUIET)
    assert params['rs_input_image_dir'].get_value() == str(zones)
    assert params['rs_flight_log_path'].get_value() == ''
    assert params['rs_project_label'].get_value() == ''

    module, queued = _module_with_stub(tmp_path, monkeypatch, params, produce=True)
    monkeypatch.setattr(module.cli, 'find_executable', lambda: str(tmp_path / 'mock.exe'))
    for name in ('RS_PROJECTS_DIR', 'RS_ALIGN_PARAMS', 'RS_ALIGN_POOL_DIR'):
        monkeypatch.delenv(name, raising=False)
    assert module.validate_parameters() == (True, None)
    outcome = module.run()
    assert outcome['Success'] and outcome['Zones Succeeded'] == 2
    assert [(args[4], args[2]) for _script, args in queued] == [
        (zone, str(zones / zone / 'flight_log_53N_UTM.txt'))
        for zone in ('zone_1', 'zone_2')]


@pytest.mark.parametrize('navigation', ['explicit', 'discovered', 'none'])
def test_explicit_external_alignment_input_keeps_its_own_navigation(
        tmp_path, settings, monkeypatch, navigation):
    import main as driver
    from modules.realityscan_interface.realityscan_interface import RealityScanAlignment
    from tests.test_align_and_rollback_safety import LOG_HEADER, QUIET, _module_with_stub

    results = tmp_path / 'results'
    old_raw = results / 'raw_images'
    old_raw.mkdir(parents=True)
    (old_raw / 'flight_log_53N_UTM.txt').write_text(LOG_HEADER, encoding='utf-8')
    external = tmp_path / 'external images'
    external.mkdir()
    (external / 'sample.jpg').write_bytes(b'fixture')
    nav = (tmp_path / 'flight_log_53N_UTM.txt' if navigation == 'explicit'
           else external / 'flight_log_53N_UTM.txt')
    if navigation != 'none':
        nav.write_text(LOG_HEADER, encoding='utf-8')
    session = session_mod.Session(results_root=str(results), enabled=['align'])
    session.answers.update({q.arg: q.default for q in
                            session_mod.build_questions(session, RawDataScan())})
    # An operator can override detected defaults with valid external inputs.
    session.answers['r_input'] = str(external)
    session.answers['r_flight_log'] = str(nav) if navigation == 'explicit' else ''
    command = session_mod.build_commands(session)[0]
    monkeypatch.setattr(driver, 'SettingsStore', lambda: settings)
    monkeypatch.setattr('builtins.input', lambda *args: pytest.fail('Unexpected prompt'))
    params = driver.initialize_parameters({'RealityScan Alignment': RealityScanAlignment(QUIET)})
    driver.parse_arguments(command.argv[1:], params, QUIET)
    module, queued = _module_with_stub(tmp_path, monkeypatch, params, produce=True)
    monkeypatch.setattr(module.cli, 'find_executable', lambda: str(tmp_path / 'mock.exe'))
    for name in ('RS_PROJECTS_DIR', 'RS_ALIGN_PARAMS', 'RS_ALIGN_POOL_DIR'):
        monkeypatch.delenv(name, raising=False)
    assert module.validate_parameters() == (True, None)
    assert module.run()['Success']
    assert len(queued) == 1
    assert queued[0][1][0] == str(external)
    assert queued[0][1][2] == (str(nav) if navigation != 'none' else '')


@pytest.mark.parametrize('size', [(100, 40), (120, 50)])
def test_intake_actions_and_read_only_status_are_available(
        tmp_path, settings, monkeypatch, size):
    source = tmp_path / 'images'
    source.mkdir()
    (source / 'sample.jpg').write_bytes(b'fixture')
    results = tmp_path / 'results'
    results.mkdir()

    def forbidden(*args, **kwargs):
        pytest.fail('Viewing results must not prepare or launch a run')

    monkeypatch.setattr(ui, 'prepare_results_root', forbidden)
    monkeypatch.setattr(ui, 'save_last_run', forbidden)
    monkeypatch.setattr(ui.CommandRunner, 'start', forbidden)

    async def drive():
        app = ui.WildScanApp(str(results))
        async with app.run_test(size=size) as pilot:
            await pilot.pause()
            screen = app.screen
            for name in ('s-continue', 's-status'):
                assert screen.query_one('#' + name).region.bottom <= size[1] - 1
            screen.query_one('#s-rawimages', Input).value = str(source)
            await pilot.click('#s-status')
            await pilot.pause()
            assert isinstance(app.screen, ui.StatusScreen)
            assert app.screen.query_one('#st-pipeline').row_count == 9
            assert 'No final components' in str(app.screen.query_one('#st-note').content)
            app.screen.action_back()
            await pilot.pause()
            assert app.screen is screen
            assert not settings.data
            assert sorted(p.name for p in results.iterdir()) == []

    asyncio.run(drive())


def test_invalid_intake_path_has_visible_feedback_without_creating_results(
        tmp_path, settings):
    results = tmp_path / 'results'

    async def drive():
        app = ui.WildScanApp(str(results))
        async with app.run_test(size=(100, 40)) as pilot:
            await pilot.pause()
            screen = app.screen
            screen.query_one('#s-rawimages').value = str(tmp_path / 'missing')
            await pilot.click('#s-continue')
            await pilot.pause()
            assert app.screen is screen
            problem = screen.query_one('#s-problem')
            assert 'Images folder' in str(problem.content)
            assert problem.region.bottom <= 39
            assert not results.exists()

    asyncio.run(drive())


def test_automatic_results_name_tracks_typing_but_preserves_manual_folder(
        tmp_path, settings):
    async def drive():
        app = ui.WildScanApp()
        async with app.run_test(size=(100, 40)) as pilot:
            await pilot.pause()
            screen = app.screen
            for value in ('N', 'NA', 'NA173'):
                screen.query_one('#s-expedition').value = value
                await pilot.pause()
            screen.query_one('#s-dive').value = 'H2104'
            await pilot.pause()
            assert screen.query_one('#s-results').value.endswith('na173_h2104')
            manual = str(tmp_path / 'chosen')
            screen.query_one('#s-results').value = manual
            screen.query_one('#s-dive').value = 'H2105'
            await pilot.pause()
            assert screen.query_one('#s-results').value == manual

    asyncio.run(drive())


def test_detected_question_defaults_and_summary_back_preserve_current_answers(
        tmp_path, settings):
    source = tmp_path / 'images'
    source.mkdir()

    async def drive():
        app = ui.WildScanApp(str(tmp_path / 'results'))
        async with app.run_test(size=(100, 40)) as pilot:
            await pilot.pause()
            app.session.enabled = ['preprocess']
            app.session.raw_images_dir = str(source)
            app.session.answers = {'p_input': 'old/dataset'}
            app.questions = [q for q in session_mod.build_questions(app.session, RawDataScan())
                             if q.arg == 'p_input']
            wizard = ui.WizardScreen()
            app.push_screen(wizard)
            await pilot.pause()
            assert wizard.query_one('#w-answer', Input).value == str(source)
            wizard._commit_and(1)
            await pilot.pause()
            assert isinstance(app.screen, ui.SummaryScreen)
            app.screen.action_back()
            await pilot.pause()
            assert app.screen is wizard and wizard.index == 0
            assert wizard._question().arg == 'p_input'
            wizard._commit_and(1)
            await pilot.pause()
            assert isinstance(app.screen, ui.SummaryScreen)
            assert app.session.answers['p_input'] == str(source)

    asyncio.run(drive())


@pytest.mark.parametrize('value', ['1.5', 'inf', 'nan'])
def test_integer_question_rejects_values_the_driver_cannot_parse(tmp_path, value):
    session = session_mod.Session(results_root=str(tmp_path), enabled=['batch'])
    question = next(q for q in session_mod.build_questions(session, RawDataScan())
                    if q.arg == 'b_target_images')
    assert question.value_type is int
    assert question.validate(value) is not None
    assert question.validate('3') is None
    assert Question('test', 'number', '', 'number', value_type=float).validate('nan')


def test_run_planning_failure_stays_visible_and_can_return_to_edit(
        tmp_path, settings, monkeypatch):
    def fail(session):
        raise OSError('results folder is not writable')

    monkeypatch.setattr(ui, 'build_commands', fail)
    monkeypatch.setattr(ui, 'write_camera_records', lambda session: None)
    monkeypatch.setattr(ui.CommandRunner, 'start', lambda *args: pytest.fail('Unexpected launch'))

    async def drive():
        app = ui.WildScanApp(str(tmp_path))
        async with app.run_test(size=(100, 40)) as pilot:
            await pilot.pause()
            app.push_screen(ui.SummaryScreen())
            await pilot.pause()
            summary = app.screen
            app.push_screen(ui.RunScreen())
            await pilot.pause()
            assert 'Unable to prepare' in str(app.screen.query_one('#r-title').content)
            assert not app.screen.query_one('#r-back').disabled
            assert not app.screen.waiting_gate
            await pilot.click('#r-back')
            await pilot.pause()
            assert app.screen is summary
            assert not settings.data

    asyncio.run(drive())


def test_review_uses_plain_labels_and_rejects_invalid_automatic_continue(
        tmp_path, settings, monkeypatch):
    monkeypatch.delenv('CESIUM_ION_TOKEN', raising=False)
    monkeypatch.delenv('NIRACLIENT_DIR', raising=False)

    async def drive():
        app = ui.WildScanApp(str(tmp_path))
        async with app.run_test(size=(100, 40)) as pilot:
            await pilot.pause()
            app.session.enabled = ['publish']
            app.session.answers['i_mpx'] = '3'
            app.questions = [Question('extract', 'i_mpx', 'Long description', 'number',
                                      label='Image megapixels')]
            app.push_screen(ui.SummaryScreen())
            await pilot.pause()
            screen = app.screen
            text = str(screen.query_one('#sum-params').content)
            assert 'Image megapixels: 3' in text and 'i_mpx' not in text
            assert 'preview only' in text
            assert screen.query_one('#sum-run').region.bottom <= 39
            screen.query_one('#sum-auto').value = 'maybe'
            await pilot.click('#sum-run')
            await pilot.pause()
            assert app.screen is screen
            assert 'true' in str(screen.query_one('#sum-problem').content)
            assert not settings.data

    asyncio.run(drive())


def test_retry_resets_previous_operation_progress(tmp_path, settings, monkeypatch):
    class FakeRunner:
        running = False

        def __init__(self, screen):
            pass

        def start(self, command):
            pass

    monkeypatch.setattr(ui, 'CommandRunner', FakeRunner)
    monkeypatch.setattr(ui, 'build_commands', lambda session: [StageCommand('sample stage', ['sample.py'], {})])
    monkeypatch.setattr(ui, 'write_camera_records', lambda session: None)

    async def drive():
        app = ui.WildScanApp(str(tmp_path))
        async with app.run_test(size=(100, 40)) as pilot:
            await pilot.pause()
            screen = ui.RunScreen()
            app.push_screen(screen)
            await pilot.pause()
            screen.on_progress_update(ui.ProgressUpdate(.8, 300, 'align'))
            screen.on_run_finished(ui.RunFinished(1))
            screen.action_gate()
            assert screen.query_one('#r-progress', ProgressBar).progress == 0
            assert 'Waiting' in str(screen.query_one('#r-eta').content)

    asyncio.run(drive())
