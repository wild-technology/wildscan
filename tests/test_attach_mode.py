#!/usr/bin/env python3
"""Unit tests for RealityScanCLI attach mode (run_attach_script).

Attach mode exists because a mesh reconstructed interactively in a GUI
session must survive the scripted finish: every boot-path workflow opens
with startRealityScan.bat, whose already-running branch issues
"-newScene -deleteAutosave" - destroying the very scene the workflow was
asked to finish. These tests pin the safety properties:

1. refuses to run when -getStatus fails (never boots an instance);
2. never invokes startRealityScan.bat / -newScene / -quit;
3. never clears a foreign (or own) instance's marker files;
4. passes the target instance as the workflow script's FIRST argument
   (ModelToFinal.bat: %1);
5. parses the -getStatus live line correctly, including a negative
   (signed 32-bit) sticky lastError, via a temp FILE - never a pipe
   (the GUI child of a 'start ""' boot inherits captured pipes).

No RealityScan: the executable is a stub that answers -getStatus with a
canned status line and records every invocation (same no-real-tool
philosophy as test_batch_reuse_guard.py).

Run:  py -3.13 -m pytest tests/test_attach_mode.py
"""

from __future__ import annotations

import logging
import os
import stat
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
sys.path.insert(0, REPO_ROOT)

from modules.realityscan_interface import realityscan_cli as cli_mod
from modules.realityscan_interface.realityscan_cli import (
    ERRORS_DIR as PACKAGE_ERRORS_DIR,
)
from modules.realityscan_interface.realityscan_cli import RealityScanCLI

CANNED_STATUS = ('id:save progress:100.0% runtime:5 endEstimation:0 '
                 'rev:147 lastError:-2113863583')
OWN_INSTANCE = 'ATTACHTESTOWN'      # what this "checkout" would boot
TARGET = 'ATTACHTESTTGT'            # the already-running instance we attach to


class FakeStore:
    """SettingsStore-shaped double (get/set only), as in test_wildscan."""

    def __init__(self, data=None):
        self.data = data or {}

    def get(self, section, key, fallback=None):
        return self.data.get(section, {}).get(key, fallback)

    def set(self, section, key, value):
        self.data.setdefault(section, {})[key] = value


def _write_script(path, lines):
    """A stub executable: .bat on Windows (CRLF - LF intermittently breaks
    cmd's label search), /bin/sh elsewhere."""
    with open(path, 'w', encoding='ascii',
              newline='\r\n' if os.name == 'nt' else '\n') as f:
        f.write('\n'.join(lines) + '\n')
    if os.name != 'nt':
        os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR)
    return str(path)


def _stub_exe(tmp_path, status_line=CANNED_STATUS, status_rc=0):
    """Fake RealityScan: records every invocation to calls.log; answers
    -getStatus with `status_line` on stdout and exit code `status_rc`."""
    calls = tmp_path / 'calls.log'
    (tmp_path / 'status_line.txt').write_text(status_line + '\n',
                                              encoding='ascii')
    (tmp_path / 'status_rc.txt').write_text(str(status_rc), encoding='ascii')
    if os.name == 'nt':
        exe = _write_script(tmp_path / 'FakeRealityScan.bat', [
            '@echo off',
            'set "HERE=%~dp0"',
            '>>"%HERE%calls.log" echo %*',
            'if /i not "%~1" == "-getStatus" exit /b 0',
            'type "%HERE%status_line.txt"',
            'set /p RC=<"%HERE%status_rc.txt"',
            'exit /b %RC%',
        ])
    else:
        exe = _write_script(tmp_path / 'FakeRealityScan.sh', [
            '#!/bin/sh',
            'HERE="$(dirname "$0")"',
            'echo "$@" >> "$HERE/calls.log"',
            '[ "$1" = "-getStatus" ] || exit 0',
            'cat "$HERE/status_line.txt"',
            'exit "$(cat "$HERE/status_rc.txt")"',
        ])
    return exe, calls


def _stub_workflow(tmp_path, exit_code=0):
    """Fake workflow .bat: records its argument line, exits `exit_code`."""
    record = tmp_path / 'workflow_calls.log'
    if os.name == 'nt':
        script = _write_script(tmp_path / 'StubWorkflow.bat', [
            '@echo off',
            f'>>"{record}" echo %*',
            f'exit /b {exit_code}',
        ])
    else:
        script = _write_script(tmp_path / 'StubWorkflow.sh', [
            '#!/bin/sh',
            f'echo "$@" >> "{record}"',
            f'exit {exit_code}',
        ])
    return script, record


def _cli(exe):
    store = FakeStore({'realityscan': {'executable': exe}})
    return RealityScanCLI(logging.getLogger('test_attach'), settings=store,
                          instance_name=OWN_INSTANCE)


def _calls(calls_log):
    if not calls_log.is_file():
        return []
    return [l.strip() for l in
            calls_log.read_text(encoding='ascii').splitlines() if l.strip()]


@pytest.fixture(autouse=True)
def errors_dir(tmp_path, monkeypatch):
    """Marker files and instance locks live in this test's own folder,
    never in the package's RS_CLI/Errors: the tests neither write into the
    source tree nor collide on a lock with a concurrent pytest run."""
    path = tmp_path / 'errors'
    path.mkdir()
    monkeypatch.setattr(cli_mod, 'ERRORS_DIR', str(path))
    return str(path)


@pytest.fixture
def target_markers(errors_dir):
    """Pre-existing marker files, foreign AND own.

    Foreign (TARGET) markers must survive byte-identical - they belong to
    whoever booted that instance. OWN-instance semantics changed with the
    B9 live gate (2026-08-07): when attaching to the instance this
    checkout owns, stale ErrorWriter files (errors/results) are OURS and
    tripped ModelToFinal's own-marker gate - attach now clears them like
    run_batch_script would. progress_<own> must still survive: the live
    instance holds it open via -writeProgress."""
    payloads = {
        os.path.join(errors_dir, f'errors_{TARGET}.txt'): 'stale-foreign-error',
        os.path.join(errors_dir, f'progress_{TARGET}.txt'): 'id:texture 41%',
        os.path.join(errors_dir, f'results_{TARGET}.log'): 'earlier op OK',
        os.path.join(errors_dir, f'errors_{OWN_INSTANCE}.txt'): 'own-stale-err',
        os.path.join(errors_dir, f'progress_{OWN_INSTANCE}.txt'): 'own 12%',
        os.path.join(errors_dir, f'results_{OWN_INSTANCE}.log'): 'own earlier',
    }
    for path, text in payloads.items():
        with open(path, 'w', encoding='utf-8') as f:
            f.write(text)
    return payloads


def test_markers_and_locks_never_touch_the_package_errors_dir(
        tmp_path, monkeypatch, errors_dir, target_markers):
    before = sorted(os.listdir(PACKAGE_ERRORS_DIR))
    exe, _ = _stub_exe(tmp_path)
    script, _ = _stub_workflow(tmp_path)
    cli = _cli(exe)
    assert os.path.dirname(cli._lock_path(TARGET)) == errors_dir
    assert all(os.path.dirname(p) == errors_dir for p in target_markers)
    held = []
    acquire = cli._acquire_lock

    def acquire_and_record(*args, **kwargs):
        owner = acquire(*args, **kwargs)
        held.extend(n for n in os.listdir(errors_dir) if n.endswith('.lock'))
        return owner

    monkeypatch.setattr(cli, '_acquire_lock', acquire_and_record)
    result = cli.run_attach_script(script, ['D:/out'], str(tmp_path / 'logs'),
                                   instance=TARGET)
    assert result.success
    assert held == [f'{TARGET}.lock'], held
    assert sorted(os.listdir(PACKAGE_ERRORS_DIR)) == before


# ------------------------------------------------------------- refusal (a)

def test_refuses_when_getstatus_fails(tmp_path):
    exe, calls = _stub_exe(tmp_path, status_rc=1)
    script, record = _stub_workflow(tmp_path)
    cli = _cli(exe)

    with pytest.raises(RuntimeError, match='never boots'):
        cli.run_attach_script(script, ['D:/out'], str(tmp_path / 'logs'),
                              instance=TARGET)

    assert not record.is_file(), 'workflow must not run without an instance'
    recorded = _calls(calls)
    assert recorded and all('-getStatus' in line for line in recorded), \
        'only readiness probes may reach the executable on refusal'


# ------------------------------------- no boot / no teardown / markers (b)

def test_attach_never_boots_shuts_down_or_clears_markers(
        tmp_path, target_markers):
    exe, calls = _stub_exe(tmp_path)
    script, record = _stub_workflow(tmp_path)
    cli = _cli(exe)

    result = cli.run_attach_script(script, ['D:/out', 'Final'],
                                   str(tmp_path / 'logs'), instance=TARGET)

    assert result.success and result.return_code == 0
    # FOREIGN attach: EVERY pre-existing marker survives byte-identical -
    # the target's belong to its booter, and our OWN files are simply not
    # involved when the target is not our instance.
    for path, text in target_markers.items():
        assert os.path.isfile(path), f'marker was deleted: {path}'
        with open(path, 'r', encoding='utf-8') as f:
            assert f.read() == text, f'marker was modified: {path}'
    # TARGET results marker is surfaced informationally, never cleared.
    assert result.completed_processes == ['earlier op OK']
    # The executable saw status probes only - no boot, no reset, no quit.
    joined = '\n'.join(_calls(calls))
    for forbidden in ('startRealityScan', '-newScene', '-deleteAutosave',
                      '-setInstanceName', '-quit'):
        assert forbidden not in joined, f'attach mode issued {forbidden}'
    # The run log and resource CSV exist like a batch run's.
    logs = os.listdir(tmp_path / 'logs')
    assert any(name.startswith('output_') for name in logs)
    assert any(name.startswith('resources_') for name in logs)


def test_own_instance_attach_clears_stale_errorwriter_markers(
        tmp_path, errors_dir, target_markers):
    """B9 own-instance exception (2026-08-07): attaching to the instance
    THIS checkout owns clears OUR stale ErrorWriter files (they tripped
    ModelToFinal's own-marker gate on the first delegated op) - but never
    progress_<own>, which a live instance holds open via -writeProgress,
    and never anything belonging to other instances."""
    exe, calls = _stub_exe(tmp_path)
    script, record = _stub_workflow(tmp_path)
    cli = _cli(exe)

    result = cli.run_attach_script(script, ['D:/out', 'Final'],
                                   str(tmp_path / 'logs'),
                                   instance=OWN_INSTANCE)

    assert result.success
    # OWN ErrorWriter markers cleared (removed or truncated) ...
    for name in (f'errors_{OWN_INSTANCE}.txt', f'results_{OWN_INSTANCE}.log'):
        path = os.path.join(errors_dir,name)
        assert not os.path.isfile(path) or not open(path).read(), \
            f'own stale marker survived an own-instance attach: {path}'
    # ... OWN progress untouched ...
    own_progress = os.path.join(errors_dir,f'progress_{OWN_INSTANCE}.txt')
    assert os.path.isfile(own_progress)
    with open(own_progress, 'r', encoding='utf-8') as f:
        assert f.read() == 'own 12%'
    # ... and OTHER instances' markers survive byte-identical.
    for name in (f'errors_{TARGET}.txt', f'progress_{TARGET}.txt',
                 f'results_{TARGET}.log'):
        path = os.path.join(errors_dir,name)
        assert os.path.isfile(path)
        with open(path, 'r', encoding='utf-8') as f:
            assert f.read() == target_markers[path]


# ------------------------------------------------- instance as first arg (c)

def test_instance_is_first_script_argument(tmp_path):
    exe, _ = _stub_exe(tmp_path)
    script, record = _stub_workflow(tmp_path)
    cli = _cli(exe)

    cli.run_attach_script(script, ['D:/out', 'Final', '4x8k'],
                          str(tmp_path / 'logs'), instance=TARGET)

    (line,) = _calls(record)
    assert line.split() == [TARGET, 'D:/out', 'Final', '4x8k']


def test_wildcard_instance_passes_through_and_locks_safely(tmp_path,
                                                          errors_dir):
    exe, _ = _stub_exe(tmp_path)
    script, record = _stub_workflow(tmp_path)
    cli = _cli(exe)

    result = cli.run_attach_script(script, ['D:/out'],
                                   str(tmp_path / 'logs'))  # default '*'

    assert result.success
    (line,) = _calls(record)
    assert line.split()[0] == '*', 'the wildcard must reach the script as %1'
    # '*' is not a legal filename character; the sanitized lock must be gone.
    assert not os.path.isfile(os.path.join(errors_dir,'WILDCARD.lock'))


# ------------------------------------------------------- status parsing

def test_get_instance_status_parses_negative_lasterror(tmp_path):
    exe, _ = _stub_exe(tmp_path)
    cli = _cli(exe)

    status = cli.get_instance_status(TARGET)

    assert status is not None
    assert status['id'] == 'save'
    assert status['progress'] == '100.0%'
    assert status['runtime'] == 5
    assert status['endEstimation'] == 0
    assert status['rev'] == 147
    assert status['lastError'] == -2113863583, \
        'lastError is a SIGNED 32-bit decimal and must parse as one'
    assert status['raw'] == CANNED_STATUS


def test_get_instance_status_none_when_instance_missing(tmp_path):
    exe, _ = _stub_exe(tmp_path, status_rc=1)
    assert _cli(exe).get_instance_status(TARGET) is None


def test_failed_workflow_reports_sticky_lasterror(tmp_path):
    exe, _ = _stub_exe(tmp_path)
    script, _ = _stub_workflow(tmp_path, exit_code=1)
    cli = _cli(exe)

    result = cli.run_attach_script(script, ['D:/out'],
                                   str(tmp_path / 'logs'), instance=TARGET)

    assert not result.success and result.return_code == 1
    assert result.errors == 'lastError:-2113863583'


if __name__ == '__main__':
    sys.exit(pytest.main([__file__, '-v']))
