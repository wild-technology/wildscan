#!/usr/bin/env python3
"""The ONE boundary where Python hands arguments to a cmd .bat.

Two guards live in RealityScanCLI (hard rule 1: one place launches
RealityScan, so one check covers every driver), plus the structural
properties of the workflow scripts themselves that the same audit fixed.

WHY (audit 2026-08-07, all measured with an echo-only .bat, every case
rc=0 - the corruption is silent):

  'D:\\NA167 Wreck & Debris\\exports' -> ARG1='D:\\NA167 Wreck ', rest RUN
  'D:\\NA167^b\\exports'              -> 'D:\\NA167b\\exports' (caret eaten)
  'D:\\dive\\a=b\\exports'            -> split; later positionals shift
  'D:\\dive\\with,comma\\final'       -> split

ARCHITECTURE.md hard rule 8 names this trap for delimited DATA; nothing enforced
it for PATHS, which is what a fresh user supplies.

And the boot path accepted '*' as an instance name. '*' means "first
available instance", a GUI/Epic-Launcher RealityScan answers it, and
run_batch_script -quits any instance answering the name before
startRealityScan.bat issues '-newScene -deleteAutosave' - i.e. it would
destroy a multi-hour interactive reconstruction with no prompt (the ON2026
near-miss class). Attach mode was hardened against exactly this; the boot
path never was.

No RealityScan is launched: the executable is a stub and every guard fires
BEFORE any subprocess (that is the point of the guard).

Run:  py -3.13 -m pytest tests/test_cmd_boundary_guards.py
"""
from __future__ import annotations

import logging
import os
import re
import sys
from types import SimpleNamespace

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
sys.path.insert(0, REPO_ROOT)

from modules.realityscan_interface import realityscan_cli as cli_mod  # noqa: E402
from modules.realityscan_interface.realityscan_cli import (  # noqa: E402
    RealityScanCLI, assert_bat_safe)

SCRIPTS = os.path.join(REPO_ROOT, 'modules', 'realityscan_interface',
                       'RS_CLI', 'Scripts')


class FakeStore:
    def __init__(self, data=None):
        self.data = data or {}

    def get(self, section, key, fallback=None):
        return self.data.get(section, {}).get(key, fallback)

    def set(self, section, key, value):
        self.data.setdefault(section, {})[key] = value


def _cli(tmp_path, instance='RS1'):
    exe = tmp_path / 'RealityScan.exe'
    exe.write_text('stub', encoding='utf-8')
    store = FakeStore({'realityscan': {'executable': str(exe)}})
    return RealityScanCLI(logging.getLogger('test'), store,
                          instance_name=instance)


def _no_subprocess(monkeypatch):
    """Any subprocess call is a test failure: the guards must fire first."""
    def boom(*a, **k):  # pragma: no cover - only runs when a guard is absent
        raise AssertionError('a subprocess was spawned before the guard fired')
    monkeypatch.setattr(cli_mod.subprocess, 'Popen', boom)
    monkeypatch.setattr(cli_mod.subprocess, 'run', boom)


# ------------------------------------------------------- the arg validator

@pytest.mark.parametrize('arg', [
    r'D:\NA167 Wreck & Debris\exports',      # & : rest of the line RUNS
    r'D:\NA167^b\exports',                   # ^ : eaten, different path
    r'D:\dive\a=b\exports',                  # = : splits, positionals shift
    r'D:\dive\with,comma\final',             # , : splits
    r'D:\dive\with;semi\final',              # ; : splits
    r'D:\dive\(parens)\final',               # ( ) : block syntax
    r'D:\dive\100%complete\final',           # % : parse-time expansion
    r'D:\dive\bang!\final',                  # ! : delayed expansion
    r'D:\dive\pipe|it\final',                # | : pipes
    r'D:\dive\redirect>out\final',           # > : redirection
])
def test_metacharacter_arguments_are_refused(arg):
    with pytest.raises(ValueError) as exc:
        assert_bat_safe([arg], 'Some.bat')
    message = str(exc.value)
    assert 'metacharacter' in message
    assert repr(arg) in message, 'the message must name the actual value'
    assert 'hard rule 8' in message, 'point the user at the documented route'


def test_ordinary_paths_and_settings_pass():
    # Everything the live drivers really pass: absolute paths with spaces,
    # component names, numbers, empty strings, and merge key:value settings.
    assert_bat_safe([
        r'D:\NA167 Wreck\batched_images_by_zone\zone_1',
        r'D:\NA167 Wreck\aligned_components\zone_1',
        '', 'zone_1', '50',
        'sfmMergeGeoreferencedComponents:true', 'sfmImagesOverlap:High',
    ], 'AlignZone.bat')


def test_argument_index_is_reported():
    with pytest.raises(ValueError, match=r'argument 3'):
        assert_bat_safe(['ok', 'also ok', 'not&ok'], 'X.bat')


def test_batch_mode_refuses_before_spawning(tmp_path, monkeypatch):
    _no_subprocess(monkeypatch)
    cli = _cli(tmp_path)
    with pytest.raises(ValueError, match='metacharacter'):
        cli.run_batch_script('AlignZone.bat', [r'D:\a&b'], str(tmp_path))


def test_attach_mode_refuses_before_spawning(tmp_path, monkeypatch):
    _no_subprocess(monkeypatch)
    cli = _cli(tmp_path)
    with pytest.raises(ValueError, match='metacharacter'):
        cli.run_attach_script('ModelToFinal.bat', [r'D:\out,here', 'Final'],
                              str(tmp_path), instance='*')


# ------------------------------------------------ boot-path instance guard

# '' is not in this list on purpose: the constructor's `or` chain already
# turns a falsy name into DEFAULT_INSTANCE_NAME. A BLANK-but-truthy name
# is the case that reaches the boot.
@pytest.mark.parametrize('instance', [
    '*', 'RS?', 'a/b', 'a\\b', 'a:b', '   ', 'RS&echo command', 'RS^1',
    'RS%NAME%', 'RS!NAME!', 'RS(1)', 'RS=1', 'RS,1', 'RS;1', 'RS`1',
    'RS 1', 'RS\n1', 'RS\t1', 'NUL', 'con', 'COM1', 'LPT9', 'NUL.ext',
])
def test_boot_refuses_unsafe_instance_names(tmp_path, monkeypatch, instance):
    _no_subprocess(monkeypatch)
    cli = _cli(tmp_path, instance=instance)
    with pytest.raises(RuntimeError) as exc:
        cli.run_batch_script('AlignZone.bat', ['a', 'b'], str(tmp_path))
    message = str(exc.value)
    assert 'BOOT' in message
    # The remedy has to be in the message, not just the refusal.
    assert 'attach' in message.lower()
    assert 'deleteAutosave' in message


def test_boot_accepts_a_normal_instance_name(tmp_path, monkeypatch):
    """The guard must not fire on the ordinary case - proved by getting
    PAST it to the first subprocess call."""
    calls = []

    def fake_run(*a, **k):
        calls.append(a)
        raise RuntimeError('stop here: the guard let us through')
    monkeypatch.setattr(cli_mod.subprocess, 'run', fake_run)
    cli = _cli(tmp_path, instance='RS1')
    with pytest.raises(RuntimeError, match='stop here'):
        cli.run_batch_script('AlignZone.bat', ['a', 'b'], str(tmp_path))
    assert calls, 'the guard blocked a legitimate instance name'


def test_wildcard_stays_legal_for_attach(tmp_path, monkeypatch):
    """'*' is attach mode's whole point and must NOT be refused there."""
    monkeypatch.setattr(RealityScanCLI, 'get_instance_status',
                        lambda self, inst=None: None)
    cli = _cli(tmp_path, instance='RS1')
    with pytest.raises(RuntimeError) as exc:
        cli.run_attach_script('ModelToFinal.bat', ['D:/out', 'Final'],
                              str(tmp_path), instance='*')
    # Refused for the RIGHT reason (no reachable instance), not for '*'.
    assert 'never boots' in str(exc.value)


@pytest.mark.parametrize('instance', [
    '', 'RS&echo command', 'RS^1', 'RS%NAME%', 'RS!NAME!', 'RS(1)',
    'RS=1', 'RS,1', 'RS;1', 'RS`1', 'RS 1', 'RS\n1', 'RS\t1',
])
def test_attach_refuses_unsafe_target_before_side_effects(
        tmp_path, monkeypatch, instance):
    _no_subprocess(monkeypatch)
    cli = _cli(tmp_path)
    logs = tmp_path / 'logs'
    with pytest.raises(RuntimeError, match='ATTACH'):
        cli.run_attach_script('ModelToFinal.bat', ['D:/out', 'Final'],
                              str(logs), instance=instance)
    assert not logs.exists()


def test_attach_refuses_unsafe_configured_instance(tmp_path, monkeypatch):
    _no_subprocess(monkeypatch)
    cli = _cli(tmp_path, instance='RS&echo command')
    with pytest.raises(RuntimeError):
        cli.run_attach_script('ModelToFinal.bat', ['D:/out', 'Final'],
                              str(tmp_path / 'logs'), instance='*')


@pytest.mark.parametrize('instance', ['RS1', 'RS_GPU0', 'RS-2', 'RS.3'])
def test_plain_instance_tokens_are_accepted(instance):
    cli_mod.assert_instance_name(instance)
    cli_mod.assert_instance_name(instance, attach=True)


def test_live_supervisor_owns_workflow_lock(tmp_path, monkeypatch):
    cli = _cli(tmp_path)
    monkeypatch.setattr(cli_mod, 'ERRORS_DIR', str(tmp_path / 'errors'))
    monkeypatch.setenv('RS_WORKFLOW_OWNER_PID', '4321')
    monkeypatch.setattr(cli, '_pid_alive', lambda pid: pid == 4321)
    cli._acquire_lock()
    path = cli._lock_path()
    with open(path, encoding='utf-8') as lock:
        assert lock.read() == '4321'
    cli._release_lock(owner_pid=1234)
    assert os.path.isfile(path)
    cli._release_lock(owner_pid=4321)
    assert not os.path.exists(path)


@pytest.mark.parametrize('owner', ['', 'invalid', '-1', '0', '4321'])
def test_missing_or_dead_supervisor_uses_driver_pid(tmp_path, monkeypatch, owner):
    cli = _cli(tmp_path)
    monkeypatch.setattr(cli_mod, 'ERRORS_DIR', str(tmp_path / 'errors'))
    monkeypatch.setenv('RS_WORKFLOW_OWNER_PID', owner)
    monkeypatch.setattr(cli, '_pid_alive', lambda pid: False)
    cli._acquire_lock()
    with open(cli._lock_path(), encoding='utf-8') as lock:
        assert lock.read() == str(os.getpid())
    cli._release_lock()
    assert not os.path.exists(cli._lock_path())


@pytest.mark.parametrize('failure', ['shutdown', 'monitor', 'launch', 'existing_instance'])
def test_supervised_unsafe_failure_keeps_instance_locked(
        tmp_path, monkeypatch, failure):
    cli = _cli(tmp_path)
    monkeypatch.setattr(cli_mod, 'ERRORS_DIR', str(tmp_path / 'errors'))
    monkeypatch.setenv('RS_WORKFLOW_OWNER_PID', '4321')
    monkeypatch.setattr(cli, '_pid_alive', lambda pid: pid == 4321)
    monkeypatch.setattr(cli, 'is_instance_running',
                        lambda: failure == 'existing_instance')
    monkeypatch.setattr(cli, 'shutdown_instance', lambda: False)
    monkeypatch.setattr(cli, 'wait_for_instance_shutdown', lambda: False)

    def fail(*args, **kwargs):
        raise OSError('fixture failure')

    monkeypatch.setattr(cli_mod.subprocess, 'Popen', fail if failure == 'launch'
                        else lambda *a, **k: SimpleNamespace(returncode=0))
    monkeypatch.setattr(cli, '_monitor_until_exit', fail if failure == 'monitor'
                        else lambda *a, **k: None)
    if failure == 'shutdown':
        result = cli.run_batch_script('AlignZone.bat', ['a', 'b'], str(tmp_path / 'logs'))
        assert not result.success and result.shutdown_verified is False
    else:
        with pytest.raises((OSError, RuntimeError)):
            cli.run_batch_script('AlignZone.bat', ['a', 'b'], str(tmp_path / 'logs'))
    with open(cli._lock_path(), encoding='utf-8') as lock:
        assert lock.read() == '4321'
    with pytest.raises(RuntimeError, match='already.*driven'):
        cli._acquire_lock()
    cli._release_lock(owner_pid=4321)
    assert not os.path.exists(cli._lock_path())


@pytest.mark.parametrize('returncode', [0, 1])
def test_verified_shutdown_allows_sequential_supervised_scripts(
        tmp_path, monkeypatch, returncode):
    cli = _cli(tmp_path)
    monkeypatch.setattr(cli_mod, 'ERRORS_DIR', str(tmp_path / 'errors'))
    monkeypatch.setenv('RS_WORKFLOW_OWNER_PID', '4321')
    monkeypatch.setattr(cli, '_pid_alive', lambda pid: pid == 4321)
    monkeypatch.setattr(cli, 'is_instance_running', lambda: False)
    monkeypatch.setattr(cli, 'wait_for_instance_shutdown', lambda: True)
    monkeypatch.setattr(cli, '_monitor_until_exit', lambda *a, **k: None)
    monkeypatch.setattr(cli_mod.subprocess, 'Popen',
                        lambda *a, **k: SimpleNamespace(returncode=returncode))
    for _ in range(2):
        result = cli.run_batch_script('AlignZone.bat', ['a', 'b'], str(tmp_path / 'logs'))
        assert result.success == (returncode == 0)
        assert result.shutdown_verified is True
        assert not os.path.exists(cli._lock_path())


# ------------------------------------------------- workflow-script guards
# Structural properties of the .bat files, checked as text (the same
# no-real-tool philosophy as test_attach_mode's forbidden-token list). No
# script is executed - they are all boot-capable.

def _bat(name: str) -> str:
    with open(os.path.join(SCRIPTS, name), encoding='utf-8', newline='') as f:
        return f.read()


def test_every_bat_is_crlf():
    """LF line endings intermittently break cmd's label search, and every
    guard below is reached by `goto` (Windows trap registry)."""
    for name in sorted(os.listdir(SCRIPTS)):
        if not name.lower().endswith('.bat'):
            continue
        with open(os.path.join(SCRIPTS, name), 'rb') as f:
            data = f.read()
        assert data.count(b'\r\n') == data.count(b'\n'), f'{name} is not CRLF'


def test_align_zone_refuses_a_zero_setting_align():
    """AlignZone's own header says settings come from AlignmentParams.xml
    'never from instance defaults'. The parse loop could yield zero
    matches and -align would still succeed on whatever the instance held."""
    text = _bat('AlignZone.bat')
    assert 'set /a applied_settings=0' in text
    assert 'set /a applied_settings+=1' in text
    assert 'if %applied_settings% EQU 0 goto :noSettings' in text
    assert re.search(r'(?m)^:noSettings\s*$', text)


def test_identity_harvest_move_failures_are_terminating():
    """Move-Item failures are NON-TERMINATING, so powershell.exe exited 0
    on a partial harvest. Membership is a successive difference of these
    folders, so an under-harvest shifts members between components."""
    for name in ('AlignZone.bat', 'MergeZoneComponents.bat'):
        text = _bat(name)
        assert "$ErrorActionPreference='Stop'" in text, name
        assert 'catch {' in text and 'exit 1 }' in text, name
        assert 'if errorlevel 1' in text, name


def test_merge_apply_set_aborts_on_a_rejected_setting():
    """A rejected ladder setting used to leave the rung on instance
    defaults while the log still printed 'Setting <key>=<value>'."""
    text = _bat('MergeZoneComponents.bat')
    assert 'if errorlevel 1 goto :applySetFailed' in text
    assert re.search(r'(?m)^:applySetFailed\s*$', text)
    # exit /b must be reached via the label, never inside a ( ) block.
    for call in re.findall(r'(?m)^if not \[%\d\] == \[\] .*applySet.*$', text):
        assert 'goto :fail' in call, call


def test_generate_model_proves_the_deliverable_before_saving():
    """21 deletes rest on RealityScan erroring for a missing model name -
    the one assumption the fact base says not to make. One delegated
    -selectModel proves the deliverable survived, before -save persists
    whatever is left."""
    text = _bat('GenerateModel.bat')
    verify = text.index(
        'call :run -selectModel "%model_tag%_Simplified_Textured" '
        '|| goto :deliverableGone')
    save = text.index('call :run -save "%scene_path%"')
    assert verify < save, 'the proof must precede the save'
    assert re.search(r'(?m)^:deliverableGone\s*$', text)


def test_export_deliverables_refuses_an_empty_name_list():
    """An empty list makes the per-component `for /f` run ZERO iterations,
    fall through to -quit and exit 0 - a no-op reporting success."""
    text = _bat('ExportDeliverables.bat')
    assert 'set /a name_count=0' in text
    assert 'if %name_count% EQU 0 goto :emptyList' in text
    assert re.search(r'(?m)^:emptyList\s*$', text)
    # The count must happen BEFORE an instance boots (cheap failure).
    assert text.index('goto :emptyList') < text.index('startRealityScan.bat')


def test_boot_gate_does_not_exit_from_inside_a_block():
    """`exit /b N` inside a multi-statement parenthesized block returns 0
    to the process caller - and this is the BOOT gate, where a
    mis-propagated failure means every later :run fires at a nonexistent
    instance."""
    text = _bat('startRealityScan.bat')
    assert 'if %startTries% GEQ 120 goto :startTimeout' in text
    assert re.search(r'(?m)^:startTimeout\s*$', text)
    # No `exit /b` may sit inside a parenthesised IF block in this file.
    depth = 0
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith('::'):
            continue
        if depth > 0:
            assert not stripped.startswith('exit /b'), \
                f'exit /b inside a block: {line!r}'
        depth += line.count('(') - line.count(')')
        depth = max(depth, 0)
