"""Former automatic-calibration defaults do not opt a new session into priors.

Only saved defaults are migrated. Current choices and existing intake
manifests remain authoritative and are never rewritten by settings loading.
"""
from __future__ import annotations

import copy
import logging

import pytest

import main as driver
import wildscan.session as portal
from module_base.parameter import Parameter

QUIET = logging.getLogger(__name__)


class Store:
    def __init__(self, data):
        self.data = copy.deepcopy(data)

    def get(self, section, key, fallback=None):
        return self.data.get(section, {}).get(key, fallback)

    def set(self, section, key, value):
        self.data.setdefault(section, {})[key] = value


def _parameters():
    return {'ws_calibration': Parameter(
        'Calibration Mode', 'w_cal', 'w_calibration', str, 'groups',
        description='Calibration mode', prompt_user=True)}


@pytest.mark.parametrize('stored', ['auto', 'AUTO', ' auto '])
def test_unattended_saved_auto_uses_groups_without_mutating_settings(
        monkeypatch, stored):
    saved = Store({'main': {'w_calibration': stored, 'g_type': 'Zeuss'}})
    monkeypatch.setattr(driver, 'SettingsStore', lambda: saved)
    monkeypatch.setenv('RS_NO_INTERACTIVE', '1')
    monkeypatch.setattr('builtins.input', lambda *_args: pytest.fail('prompted'))
    before = copy.deepcopy(saved.data)
    params = _parameters()
    driver.parse_arguments(['main.py'], params, QUIET)
    assert params['ws_calibration'].get_value() == 'groups'
    assert saved.data == before


@pytest.mark.parametrize('mode', ['auto', 'prior'])
def test_explicit_cli_choice_survives_saved_auto_migration(monkeypatch, mode):
    saved = Store({'main': {'w_calibration': 'auto'}})
    monkeypatch.setattr(driver, 'SettingsStore', lambda: saved)
    monkeypatch.setenv('RS_NO_INTERACTIVE', '1')
    params = _parameters()
    driver.parse_arguments(['main.py', '--w_calibration', mode], params, QUIET)
    assert params['ws_calibration'].get_value() == mode


def test_saved_explicit_prior_remains_an_opt_in(monkeypatch):
    saved = Store({'main': {'w_calibration': 'prior'}})
    monkeypatch.setattr(driver, 'SettingsStore', lambda: saved)
    monkeypatch.setenv('RS_NO_INTERACTIVE', '1')
    params = _parameters()
    driver.parse_arguments(['main.py'], params, QUIET)
    assert params['ws_calibration'].get_value() == 'prior'


def test_interactive_session_can_select_auto_again(monkeypatch):
    saved = Store({'main': {'w_calibration': 'auto'}})
    monkeypatch.setattr(driver, 'SettingsStore', lambda: saved)
    monkeypatch.delenv('RS_NO_INTERACTIVE', raising=False)

    def choose(prompt):
        assert '[groups]' in prompt
        return 'auto'

    monkeypatch.setattr('builtins.input', choose)
    params = _parameters()
    driver.parse_arguments(['main.py'], params, QUIET)
    assert params['ws_calibration'].get_value() == 'auto'


def test_portal_load_migrates_only_inherited_auto(monkeypatch, tmp_path):
    saved = Store({'wildscan': {
        'results_root': str(tmp_path / 'workspace'),
        'answers': {'w_calibration': 'auto', 'w_variant': 'card',
                    'g_type': 'Zeuss'}}})
    monkeypatch.setattr(portal, '_settings', lambda: saved)
    before = copy.deepcopy(saved.data)
    session = portal.default_session()
    assert session.answers == {'w_calibration': 'groups', 'w_variant': 'card',
                               'g_type': 'Zeuss'}
    session.enabled = ['intake']
    command = portal.build_commands(session)[0]
    assert command.argv[command.argv.index('--w_calibration') + 1] == 'groups'
    assert saved.data == before


@pytest.mark.parametrize('mode', ['auto', 'prior'])
def test_portal_live_choice_is_not_remapped_during_questions_or_commands(
        monkeypatch, tmp_path, mode):
    saved = Store({'wildscan': {
        'results_root': str(tmp_path / 'workspace'),
        'answers': {'w_calibration': 'auto'}}})
    monkeypatch.setattr(portal, '_settings', lambda: saved)
    session = portal.default_session()
    session.enabled = ['intake']
    session.answers['w_calibration'] = mode  # current wizard choice
    questions = portal.build_questions(session, portal.RawDataScan())
    question = next(q for q in questions if q.arg == 'w_calibration')
    assert question.default == mode
    command = portal.build_commands(session)[0]
    assert command.argv[command.argv.index('--w_calibration') + 1] == mode


def test_portal_retains_prior_setting_and_leaves_existing_manifest_untouched(
        monkeypatch, tmp_path):
    workspace = tmp_path / 'workspace'
    raw = workspace / 'raw_images'
    raw.mkdir(parents=True)
    manifest = raw / 'wildsync_intake.json'
    previous = b'{"schema":2,"status":"complete","calibration":{"mode":"prior"}}\n'
    manifest.write_bytes(previous)
    saved = Store({'wildscan': {'results_root': str(workspace),
                               'answers': {'w_calibration': 'prior'}}})
    monkeypatch.setattr(portal, '_settings', lambda: saved)
    assert portal.default_session().answers['w_calibration'] == 'prior'
    assert manifest.read_bytes() == previous
