"""Subprocess runner: stream a LaunchPlan's output as Textual messages.

One worker thread per run; every stdout/stderr line becomes a LogLine
message, RealityScan `#progress` heartbeats become ProgressUpdate, and the
exit code arrives as RunFinished. The UI stays responsive and the child is
never polled.
"""
from __future__ import annotations

import os
import json
import re
import signal
import subprocess
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path

from textual.message import Message

from module_base.process_job import WindowsJob

REPO = Path(__file__).resolve().parent.parent

# "20533 0.67 33.41 17.11 #progress"  ->  op, fraction, elapsed, eta
_PROGRESS = re.compile(
    r"(\d+)\s+([01]\.\d+)\s+([\d.]+)\s+([\d.]+)\s+#(progress|started|completed)")


class LogLine(Message):
    def __init__(self, line: str) -> None:
        self.line = line
        super().__init__()


class ProgressUpdate(Message):
    def __init__(self, fraction: float, eta_s: float, op: str) -> None:
        self.fraction = fraction
        self.eta_s = eta_s
        self.op = op
        super().__init__()


class RunFinished(Message):
    def __init__(self, returncode: int, cancelled: bool = False) -> None:
        self.returncode = returncode
        self.cancelled = cancelled
        super().__init__()


class CommandRunner:
    """Owns exactly one child process; post_target receives the messages."""

    def __init__(self, post_target) -> None:
        self._post = post_target.post_message
        self._proc: subprocess.Popen | None = None
        self._thread: threading.Thread | None = None
        self._job: WindowsJob | None = None
        self._cancelled = False
        self._plan = None
        self._active = False
        self._workspace: Path | None = None

    @property
    def running(self) -> bool:
        return self._active

    def start(self, plan) -> None:
        """`plan` is anything with argv + env (session.StageCommand)."""
        if self.running:
            raise RuntimeError("a stage is already running")
        env = dict(os.environ)
        env.update(plan.env)
        self._plan = plan
        self._cancelled = False
        cwd = getattr(plan, "cwd", None) or str(REPO)
        workspace = getattr(plan, "workspace", None)
        self._workspace = (Path(cwd) / workspace).resolve() if workspace else None
        self._active = True
        if getattr(plan, "needs_realityscan", False):
            env["RS_WORKFLOW_OWNER_PID"] = str(os.getpid())
        # stdin=DEVNULL: a child that reaches input() must get EOF (and take
        # its stored-default path) - never block invisibly on an inherited
        # console.
        options = ({"creationflags": 0x00000004}  # CREATE_SUSPENDED
                   if os.name == "nt" else {"start_new_session": True})
        self._proc = None
        try:
            self._job = WindowsJob() if os.name == "nt" else None
            self._proc = subprocess.Popen(
                plan.argv, cwd=cwd, env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace", bufsize=1,
                **options)
            if self._job:
                self._job.assign_and_resume(self._proc)
        except BaseException:
            if self._proc and self._proc.poll() is None:
                self._proc.kill()
                self._proc.wait()
            if self._job:
                self._job.close()
                self._job = None
            self._active = False
            raise
        self._thread = threading.Thread(target=self._pump, daemon=True)
        self._thread.start()

    def terminate(self) -> None:
        """Stop the owned tree; completion is reported only after teardown."""
        if not self.running:
            return
        self._cancelled = True
        try:
            self._record_interruption()
        finally:
            try:
                if self._job:
                    self._job.terminate()
                elif self._proc and self._proc.poll() is None:
                    os.killpg(self._proc.pid, signal.SIGTERM)
            except OSError as exc:
                self._post(LogLine(f"Unable to stop stage processes: {exc}"))

    def _record_interruption(self) -> None:
        root = self._workspace
        if root is None or not root.is_dir():
            return
        payload = {"stage": self._plan.stage, "command": self._plan.argv,
                   "stages": list(getattr(self._plan, "stages", ())),
                   "driver_pid": self._proc.pid, "cancelled": True,
                   "at": datetime.now(timezone.utc).isoformat()}
        path = root / "interrupted_stage.json"
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8",
                                             dir=root, delete=False) as handle:
                temporary = handle.name
                json.dump(payload, handle, indent=2)
            os.replace(temporary, path)
        except OSError as exc:
            self._post(LogLine(f"Unable to record stage interruption: {exc}"))
        finally:
            if temporary and os.path.exists(temporary):
                try:
                    os.unlink(temporary)
                except OSError as exc:
                    self._post(LogLine(f"Unable to remove interruption temporary file: {exc}"))

    def _release_supervised_lock(self) -> None:
        if not getattr(self._plan, "needs_realityscan", False):
            return
        from modules.realityscan_interface.realityscan_cli import RealityScanCLI
        from module_base.settings_store import SettingsStore
        import logging

        instance = self._plan.env.get("RS_INSTANCE") or os.environ.get("RS_INSTANCE")
        cli = RealityScanCLI(logging.getLogger(__name__), SettingsStore(),
                            instance_name=instance)
        cli._release_lock(owner_pid=os.getpid())

    def _pump(self) -> None:
        assert self._proc and self._proc.stdout
        reader = threading.Thread(target=self._read_output, daemon=True)
        reader.start()
        self._proc.wait()
        returncode = self._proc.returncode
        if self._job:
            while True:
                try:
                    if self._job.active_processes:
                        self._job.terminate()
                        if not self._cancelled:
                            self._post(LogLine("Driver exited with live descendants; stopped remaining work"))
                            returncode = 1
                    self._job.wait_empty()
                    break
                except (OSError, TimeoutError) as exc:
                    self._post(LogLine(f"Stage teardown incomplete; instance remains locked: {exc}"))
                    returncode = 1
                    # Keep supervising until the tree is actually empty. Reporting
                    # completion now would let a retry collide with live children.
                    threading.Event().wait(1.0)
            try:
                self._release_supervised_lock()
            except (OSError, RuntimeError, ValueError) as exc:
                self._post(LogLine(f"Unable to release stage instance lock: {exc}"))
                returncode = 1
            self._job.close()
            self._job = None
        reader.join()
        self._proc.stdout.close()
        returncode = returncode or int(self._cancelled)
        if returncode == 0:
            if self._workspace is not None:
                marker = self._workspace / "interrupted_stage.json"
                try:
                    prior = json.loads(marker.read_text(encoding="utf-8"))
                    stages = set(getattr(self._plan, "stages", ()))
                    interrupted = prior.get("stages") if isinstance(prior, dict) else None
                    same_stages = (isinstance(interrupted, list) and bool(interrupted)
                                   and all(isinstance(stage, str) and stage in stages
                                           for stage in interrupted))
                    same_command = (isinstance(prior, dict)
                                    and prior.get("command") == self._plan.argv)
                    if same_stages or same_command:
                        marker.unlink()
                except FileNotFoundError:
                    pass
                except (OSError, ValueError) as exc:
                    self._post(LogLine(f"Unable to clear stage interruption: {exc}"))
        self._active = False
        self._post(RunFinished(returncode, self._cancelled))

    def _read_output(self) -> None:
        assert self._proc and self._proc.stdout
        for raw in self._proc.stdout:
            line = raw.rstrip("\r\n")
            if not line:
                continue
            m = _PROGRESS.search(line)
            if m and m.group(5) == "progress":
                self._post(ProgressUpdate(float(m.group(2)),
                                          float(m.group(4)), m.group(1)))
            self._post(LogLine(line))
