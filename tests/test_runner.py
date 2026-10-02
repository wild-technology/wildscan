"""Process-tree ownership and cancellation for the console runner."""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
import sys
import threading
import time

import pytest

from wildscan.runner import CommandRunner, RunFinished
from wildscan.session import StageCommand


class Sink:
    def __init__(self):
        self.messages = []
        self.finished = threading.Event()

    def post_message(self, message):
        self.messages.append(message)
        if isinstance(message, RunFinished):
            self.finished.set()
        return True


def wait_for(path: Path, timeout: float = 5.0):
    deadline = time.monotonic() + timeout
    while not path.exists():
        if time.monotonic() >= deadline:
            pytest.fail(f"Child did not create {path.name}")
        time.sleep(0.02)


def child_program(ready: Path, completed: Path) -> str:
    return ("from pathlib import Path; import time; "
            f"Path({str(ready)!r}).write_text('ready'); time.sleep(1); "
            f"Path({str(completed)!r}).write_text('complete')")


def test_normal_stage_reports_success(tmp_path):
    sink = Sink()
    runner = CommandRunner(sink)
    marker = tmp_path / "interrupted_stage.json"
    command = StageCommand("fixture", [sys.executable, "-c", "print('finished')"], {},
                           workspace=str(tmp_path), stages=("model",))
    marker.write_text(json.dumps({"command": command.argv}), encoding="utf-8")
    runner.start(command)
    assert sink.finished.wait(5)
    assert not runner.running
    assert sink.messages[-1].returncode == 0
    assert not marker.exists()


def test_stop_terminates_descendants_and_records_interruption(tmp_path):
    sink = Sink()
    runner = CommandRunner(sink)
    ready, completed = tmp_path / "ready", tmp_path / "completed"
    child = child_program(ready, completed)
    parent = ("import subprocess,sys,time; "
              f"subprocess.Popen([sys.executable,'-c',{child!r}]); time.sleep(60)")
    runner.start(StageCommand("fixture", [sys.executable, "-c", parent], {},
                              workspace=str(tmp_path), stages=("model",)))
    try:
        wait_for(ready)
        runner.terminate()
        assert sink.finished.wait(5)
        assert not runner.running
        assert sink.messages[-1].cancelled
        assert sink.messages[-1].returncode != 0
        marker = json.loads((tmp_path / "interrupted_stage.json").read_text())
        assert marker["stages"] == ["model"]
        time.sleep(1.1)
        assert not completed.exists()
    finally:
        if runner.running:
            runner.terminate()


@pytest.mark.skipif(os.name != "nt", reason="Windows job ownership")
@pytest.mark.parametrize("inherit_output", [True, False])
def test_driver_cannot_leave_descendants(tmp_path, inherit_output):
    sink = Sink()
    runner = CommandRunner(sink)
    ready, completed = tmp_path / "ready", tmp_path / "completed"
    child = child_program(ready, completed)
    output = "" if inherit_output else ", stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL"
    parent = ("import subprocess,sys,time; "
              f"subprocess.Popen([sys.executable,'-c',{child!r}]{output}); time.sleep(.3)")
    runner.start(StageCommand("fixture", [sys.executable, "-c", parent], {}))
    try:
        assert sink.finished.wait(5)
        assert not runner.running
        assert sink.messages[-1].returncode != 0
        time.sleep(1.1)
        assert not completed.exists()
    finally:
        if runner.running:
            runner.terminate()


def test_launch_failure_leaves_runner_available(tmp_path):
    runner = CommandRunner(Sink())
    with pytest.raises(OSError):
        runner.start(StageCommand("fixture", [str(tmp_path / "missing.exe")], {}))
    assert not runner.running


def test_planned_paths_and_child_cwd_stay_with_the_intake_folder(tmp_path, monkeypatch):
    from wildscan.session import Session, build_commands

    caller = tmp_path / 'intake with spaces'
    caller.mkdir()
    raw = caller / 'raw clip.mov'
    raw.write_text('fixture input', encoding='utf-8')
    results = caller / 'results'
    results.mkdir()
    monkeypatch.chdir(caller)
    plan = build_commands(Session(results_root='results', enabled=['extract'],
                                  answers={'i_input': raw.name}))[0]
    assert plan.cwd == str(caller)
    assert plan.workspace == str(results)
    assert Path(plan.argv[1]).is_absolute()
    input_path = plan.argv[plan.argv.index('--i_input') + 1]
    output_path = plan.argv[plan.argv.index('--output_dir') + 1]
    child = ("from pathlib import Path; import os,sys; "
             "assert Path.cwd() == Path(sys.argv[3]); "
             "root=Path(sys.argv[2]); root.mkdir(exist_ok=True); "
             "(root/'copied.txt').write_text(Path(sys.argv[1]).read_text(),encoding='utf-8')")
    plan.argv = [sys.executable, '-B', '-c', child, input_path, output_path, str(caller)]
    marker = results / 'interrupted_stage.json'
    marker.write_text(json.dumps({'stages': ['extract']}), encoding='utf-8')
    other = tmp_path / 'other'
    other.mkdir()
    monkeypatch.chdir(other)
    sink = Sink()
    runner = CommandRunner(sink)
    runner.start(plan)
    assert sink.finished.wait(5)
    assert sink.messages[-1].returncode == 0
    assert (results / 'copied.txt').read_text(encoding='utf-8') == 'fixture input'
    assert not marker.exists()
    assert not (other / 'results').exists()


def test_cancel_marker_is_anchored_to_the_explicit_child_cwd(tmp_path, monkeypatch):
    caller = tmp_path / 'caller'
    results = caller / 'results'
    results.mkdir(parents=True)
    ready = caller / 'ready'
    other = tmp_path / 'other'
    other.mkdir()
    monkeypatch.chdir(other)
    child = "from pathlib import Path; import time; Path('ready').touch(); time.sleep(60)"
    sink = Sink()
    runner = CommandRunner(sink)
    runner.start(StageCommand('fixture', [sys.executable, '-B', '-c', child], {},
                              workspace='results', stages=('extract',), cwd=str(caller)))
    try:
        wait_for(ready)
        runner.terminate()
        assert sink.finished.wait(5)
        assert sink.messages[-1].cancelled
        assert (results / 'interrupted_stage.json').is_file()
        assert not (other / 'results').exists()
    finally:
        if runner.running:
            runner.terminate()


def test_manual_command_keeps_the_default_checkout_cwd(tmp_path, monkeypatch):
    from wildscan.runner import LogLine, REPO

    monkeypatch.chdir(tmp_path)
    sink = Sink()
    runner = CommandRunner(sink)
    runner.start(StageCommand('fixture', [sys.executable, '-B', '-c',
                                        'from pathlib import Path; print(Path.cwd())'], {}))
    assert sink.finished.wait(5)
    assert sink.messages[-1].returncode == 0
    assert [m.line for m in sink.messages if isinstance(m, LogLine)] == [str(REPO)]


@pytest.mark.parametrize("marker", [[], {"stages": ["model"], "command": ["old settings"]}])
def test_retry_clears_only_matching_stage_interruption(tmp_path, marker):
    path = tmp_path / "interrupted_stage.json"
    path.write_text(json.dumps(marker), encoding="utf-8")
    sink = Sink()
    runner = CommandRunner(sink)
    runner.start(StageCommand("fixture", [sys.executable, "-c", "pass"], {},
                              workspace=str(tmp_path), stages=("model",)))
    assert sink.finished.wait(5)
    assert not runner.running
    assert sink.messages[-1].returncode == 0
    assert path.exists() == isinstance(marker, list)


def test_retry_preserves_unfinished_stages(tmp_path):
    path = tmp_path / "interrupted_stage.json"
    path.write_text(json.dumps({"stages": ["extract", "georeference"]}), encoding="utf-8")
    sink = Sink()
    runner = CommandRunner(sink)
    runner.start(StageCommand("fixture", [sys.executable, "-c", "pass"], {},
                              workspace=str(tmp_path), stages=("georeference",)))
    assert sink.finished.wait(5)
    assert path.exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows job ownership")
def test_cancel_releases_instance_lock_only_after_descendants_stop(tmp_path, monkeypatch):
    import modules.realityscan_interface.realityscan_cli as cli_mod

    errors = tmp_path / "errors"
    monkeypatch.setattr(cli_mod, "ERRORS_DIR", str(errors))
    sink = Sink()
    runner = CommandRunner(sink)
    ready, completed = tmp_path / "ready", tmp_path / "completed"
    child = child_program(ready, completed)
    parent = ("import logging,subprocess,sys,time; "
              "import modules.realityscan_interface.realityscan_cli as cli_mod; "
              f"cli_mod.ERRORS_DIR={str(errors)!r}; "
              "cli=cli_mod.RealityScanCLI(logging.getLogger('fixture')); "
              "cli._acquire_lock(); "
              f"subprocess.Popen([sys.executable,'-c',{child!r}]); time.sleep(60)")
    runner.start(StageCommand("fixture", [sys.executable, "-c", parent],
                              {"RS_INSTANCE": "TestTree"}, needs_realityscan=True,
                              workspace=str(tmp_path), stages=("model",)))
    released = []
    release_lock = runner._release_supervised_lock

    def verify_release():
        released.append(runner._job.active_processes)
        release_lock()

    monkeypatch.setattr(runner, "_release_supervised_lock", verify_release)
    try:
        wait_for(ready)
        lock = errors / "TestTree.lock"
        assert lock.read_text() == str(os.getpid())
        cli = cli_mod.RealityScanCLI(logging.getLogger('fixture'),
                                    instance_name="TestTree")
        with pytest.raises(RuntimeError, match="already.*driven"):
            cli._acquire_lock()
        runner.terminate()
        assert sink.finished.wait(5)
        assert released == [0]
        assert not lock.exists()
        assert not runner.running
    finally:
        if runner.running:
            runner.terminate()


def test_marker_write_and_cleanup_failures_do_not_prevent_stop(tmp_path, monkeypatch):
    import wildscan.runner as runner_mod

    sink = Sink()
    runner = CommandRunner(sink)
    ready = tmp_path / "ready"
    child = f"from pathlib import Path; import time; Path({str(ready)!r}).touch(); time.sleep(60)"
    runner.start(StageCommand("fixture", [sys.executable, "-c", child], {},
                              workspace=str(tmp_path), stages=("model",)))

    def fail(*args, **kwargs):
        raise OSError("unwritable marker")

    try:
        wait_for(ready)
        with monkeypatch.context() as patch:
            patch.setattr(runner_mod.os, "replace", fail)
            patch.setattr(runner_mod.os, "unlink", fail)
            runner.terminate()
            assert sink.finished.wait(5)
        assert not runner.running
        assert sink.messages[-1].cancelled
    finally:
        if runner.running:
            runner.terminate()
