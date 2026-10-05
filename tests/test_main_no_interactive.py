"""main.py parameter prompts under RS_NO_INTERACTIVE and without stdin.

The --help epilog promises that RS_NO_INTERACTIVE=1 never prompts and
that missing required values fail fast. A prompted parameter given on the
command line, or carrying a stored or default value, is not missing.
"""
from __future__ import annotations

import logging

import pytest

import main as driver
from module_base.parameter import Parameter

QUIET = logging.getLogger('main-no-interactive-test')
QUIET.addHandler(logging.NullHandler())
QUIET.propagate = False


class FakeStore:
    def __init__(self, data=None):
        self.data = data or {}

    def get(self, section, key, fallback=None):
        return self.data.get(section, {}).get(key, fallback)

    def set(self, section, key, value):
        self.data.setdefault(section, {})[key] = value


def _params():
    return {
        'needed': Parameter('Needed input', 'n_in', 'n_input', str, None,
                            description='Input folder', required=True),
        'other': Parameter('Other input', 'o_in', 'o_input', str, None,
                           description='Other folder', required=True),
        'optional': Parameter('Optional path', 'op', 'opt_path', str, None,
                              description='Empty means discover it'),
        'defaulted': Parameter('Defaulted', 'd_v', 'd_value', int, 3000,
                               description='Defaulted value'),
    }


@pytest.fixture
def store(monkeypatch):
    fake = FakeStore()
    monkeypatch.setattr(driver, 'SettingsStore', lambda: fake)
    return fake


def _no_input(monkeypatch):
    def fail(*_args):
        raise AssertionError('input() was called')
    monkeypatch.setattr('builtins.input', fail)


def test_no_interactive_names_every_missing_flag_and_exits(monkeypatch, store,
                                                          capsys):
    monkeypatch.setenv('RS_NO_INTERACTIVE', '1')
    _no_input(monkeypatch)
    with pytest.raises(SystemExit) as exc:
        driver.parse_arguments(['main.py'], _params(), QUIET)
    assert exc.value.code != 0
    err = capsys.readouterr().err
    assert '--n_input' in err and '--o_input' in err
    assert '--d_value' not in err and '--opt_path' not in err


def test_no_interactive_takes_flags_stored_and_default_values(monkeypatch,
                                                             store):
    monkeypatch.setenv('RS_NO_INTERACTIVE', '1')
    _no_input(monkeypatch)
    store.set('main', 'o_input', 'stored/folder')
    params = _params()
    driver.parse_arguments(['main.py', '--n_input', 'given/folder'], params,
                           QUIET)
    assert params['needed'].get_value() == 'given/folder'
    assert params['other'].get_value() == 'stored/folder'
    assert params['defaulted'].get_value() == 3000
    assert params['optional'].get_value() is None


def test_without_the_variable_missing_values_are_still_prompted(monkeypatch,
                                                               store):
    monkeypatch.delenv('RS_NO_INTERACTIVE', raising=False)
    answers = iter(['typed/a', 'typed/b', '', ''])
    monkeypatch.setattr('builtins.input', lambda *_a: next(answers))
    params = _params()
    driver.parse_arguments(['main.py'], params, QUIET)
    assert params['needed'].get_value() == 'typed/a'
    assert params['other'].get_value() == 'typed/b'
    assert params['defaulted'].get_value() == 3000


def test_closed_stdin_without_a_value_is_a_clear_error(monkeypatch, store,
                                                       capsys):
    monkeypatch.delenv('RS_NO_INTERACTIVE', raising=False)

    def eof(*_a):
        raise EOFError
    monkeypatch.setattr('builtins.input', eof)
    with pytest.raises(SystemExit) as exc:
        driver.parse_arguments(['main.py', '--o_input', 'x'], _params(), QUIET)
    assert exc.value.code != 0
    assert '--n_input' in capsys.readouterr().err


def test_closed_stdin_with_a_stored_value_uses_it(monkeypatch, store):
    monkeypatch.delenv('RS_NO_INTERACTIVE', raising=False)

    def eof(*_a):
        raise EOFError
    monkeypatch.setattr('builtins.input', eof)
    store.set('main', 'n_input', 'stored/a')
    store.set('main', 'o_input', 'stored/b')
    params = _params()
    driver.parse_arguments(['main.py'], params, QUIET)
    assert params['needed'].get_value() == 'stored/a'
    assert params['other'].get_value() == 'stored/b'
