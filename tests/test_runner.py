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
