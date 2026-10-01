"""Both public launch routes preserve useful startup failures."""
from __future__ import annotations

import builtins
from importlib.metadata import EntryPoint
from pathlib import Path
import sys
import tomllib

import pytest

from wildscan import __main__ as entry


def test_console_command_uses_the_module_entry_point():
    repo = Path(__file__).resolve().parents[1]
    project = tomllib.loads((repo / 'pyproject.toml').read_text(encoding='utf-8'))['project']
    command = EntryPoint('wildscan', project['scripts']['wildscan'], 'console_scripts')
    assert command.load() is entry.main


@pytest.mark.parametrize('module', ['rich', 'textual'])
def test_missing_dependency_hint_works_outside_checkout(tmp_path, monkeypatch, module):
    checkout = tmp_path / "Owner's checkout"
    monkeypatch.setattr(entry, '__file__', str(checkout / 'wildscan' / '__main__.py'))
    monkeypatch.setattr(sys, 'executable', "C:\\Owner's Python\\python.exe")
    monkeypatch.chdir(tmp_path)
    original_import = builtins.__import__

    def missing_ui(name, globals=None, locals=None, fromlist=(), level=0):
        if name == 'app' and level == 1:
            raise ModuleNotFoundError(f"No module named '{module}'", name=module)
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, '__import__', missing_ui)
    with pytest.raises(SystemExit) as error:
        entry.main()
    message = str(error.value)
    assert f'missing dependency: {module}' in message
    assert "& 'C:\\Owner''s Python\\python.exe' -m pip install -e " in message
    assert "'" + str(checkout.resolve()).replace("'", "''") + "'" in message


@pytest.mark.parametrize('failure', [
    ImportError('DLL load failed while importing the imaging library'),
    ImportError("cannot import name 'main' from 'wildscan.app'", name='wildscan.app'),
    ModuleNotFoundError("No module named 'module_base'", name='module_base'),
])
def test_broken_import_keeps_the_actual_diagnostic(monkeypatch, failure):
    original_import = builtins.__import__

    def broken_ui(name, globals=None, locals=None, fromlist=(), level=0):
        if name == 'app' and level == 1:
            raise failure
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, '__import__', broken_ui)
    with pytest.raises(SystemExit) as error:
        entry.main()
    assert str(failure) in str(error.value)
    assert 'missing dependency' not in str(error.value)


def test_entry_point_preserves_application_exit_status(monkeypatch):
    from wildscan import app
    monkeypatch.setattr(app, 'main', lambda: 7)
    assert entry.main() == 7
