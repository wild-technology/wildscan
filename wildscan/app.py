"""WildScan - the Wild Technology user interaction portal.

RC_Main's flow, kept faithfully, as screens:

    SessionScreen    expedition / dive / data location / results root,
                     with live raw-data detection and the results layout
    StagePickScreen  the RC_Main checkbox - stages in order, resume-aware
                     pre-selection (done stages start unticked)
    WizardScreen     ONE question at a time, in module order, each
                     parameter's own description as the prompt
    SummaryScreen    the parameter printout, then Run
    RunScreen        sequential stage commands with live log + progress and
                     a press-enter gate between stages (unless Continue
                     Automatically)
    StatusScreen     the pipeline census + final-components browser

The portal never touches data handling: chained modules run in one main.py
invocation (their in-process hand-off IS the pipeline), post stages run the
canonical drivers, and every answer round-trips through rs_settings.json so
the last run becomes the next run's defaults.
"""
from __future__ import annotations

import argparse
from pathlib import Path

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import Screen
from textual.timer import Timer
from textual.widgets import (Button, DataTable, Footer, Input, Label,
                             ProgressBar, RichLog, SelectionList, Static)
from textual.widgets.selection_list import Selection

from . import APP_NAME, ORG, TAGLINE, __version__
from .branding import (CSS, MIST, OK, SAND, STATUS_COLOR, STATUS_GLYPH, TEAL,
                       WARN, WORDMARK)
from .runner import CommandRunner, LogLine, ProgressUpdate, RunFinished
from .session import (ALL_STAGES, Question, RawDataScan, Session, build_commands, build_questions,
                      default_enabled, default_session, export_names_file,
                      prepare_results_root, save_last_run, scan_raw_data,
                      write_camera_records)
from .workspace import STAGE_TITLES, StageStatus, Workspace


def status_text(status: str, text: str) -> Text:
    colour = STATUS_COLOR.get(status, MIST)
    return Text(f"{STATUS_GLYPH.get(status, '?')} {text}", style=colour)


class SessionScreen(Screen):
    """Question order exactly as the pipeline has always asked: who and
    where, before anything else."""

    BINDINGS = [Binding("q", "quit", "Quit")]

    def __init__(self) -> None:
        super().__init__()
        self._detection_cache: dict[str, tuple[str, object]] = {}
        self._detection_timer: Timer | None = None
        self._suggested_results: str | None = None

    def compose(self) -> ComposeResult:
        yield Static(WORDMARK, id="wordmark")
        yield Static(f"{ORG} · {TAGLINE} · v{__version__}", id="tagline")
        with VerticalScroll(classes="panel", id="s-fields"):
            yield Label("Choose your dataset", classes="panel-title")
            yield Static("Fill in the sources you have. Other source fields can stay blank.")
            yield Label("Expedition (e.g. NA156)")
            yield Input(id="s-expedition")
            yield Label("Dive (e.g. H2024)")
            yield Input(id="s-dive")
            yield Label("Dive folder (recordings and navigation)")
            yield Input(id="s-cruise")
            yield Label("Images folder (existing stills)")
            yield Input(id="s-rawimages")
            yield Label("Survey video (one recording)")
            yield Input(id="s-video")
            yield Label("Processed navigation folder (optional datatables or flight logs)")
            yield Input(id="s-processed")
            yield Label("Results root (created if missing; the pipeline "
                        "builds its structure inside)")
            yield Input(id="s-results")
            yield Static("", id="s-detect")
        yield Static("", id="s-problem")
        with Horizontal(classes="actions"):
            yield Button("Continue", id="s-continue", variant="primary")
            yield Button("View results", id="s-status")
        yield Footer()

    def on_mount(self) -> None:
        app: WildScanApp = self.app  # type: ignore[assignment]
        s = app.session
        self.query_one("#s-expedition", Input).value = s.expedition
        self.query_one("#s-dive", Input).value = s.dive
        self.query_one("#s-cruise", Input).value = s.cruise_folder
        self.query_one("#s-rawimages", Input).value = s.raw_images_dir
        self.query_one("#s-video", Input).value = s.video_path
        self.query_one("#s-processed", Input).value = s.processed_data
        self.query_one("#s-results", Input).value = s.results_root
        self._refresh_detection()

    def _pull(self) -> None:
        app: WildScanApp = self.app  # type: ignore[assignment]
        s = app.session
        s.expedition = self.query_one("#s-expedition", Input).value.strip()
        s.dive = self.query_one("#s-dive", Input).value.strip()
        s.cruise_folder = self.query_one("#s-cruise", Input).value.strip()
        s.raw_images_dir = self.query_one("#s-rawimages", Input).value.strip()
        s.video_path = self.query_one("#s-video", Input).value.strip()
        s.processed_data = self.query_one("#s-processed", Input).value.strip()
        s.results_root = self.query_one("#s-results", Input).value.strip()

    def _refresh_detection(self, *, force: bool = False) -> None:
        from .session import scan_cameras, scan_processed_data
        app: WildScanApp = self.app  # type: ignore[assignment]
        self._detection_timer = None
        self._pull()
        s = app.session

        def scanned(key, path, scanner):
            previous = self._detection_cache.get(key)
            if force or previous is None or previous[0] != path:
                previous = (path, scanner(path))
                self._detection_cache[key] = previous
            return previous[1]

        if s.cruise_folder:
            app.scan = scanned('cruise', s.cruise_folder, scan_raw_data)
        else:
            self._detection_cache.pop('cruise', None)
            app.scan = RawDataScan()
        for key, path in (('raw_images', s.raw_images_dir),
                          ('processed', s.processed_data)):
            if not path:
                self._detection_cache.pop(key, None)
        text = Text()
        if s.cruise_folder:
            text.append("Cruise folder:\n", style=SAND)
            for line in app.scan.summary_lines():
                text.append("  · ", style=TEAL)
                text.append(line + "\n")
        if s.raw_images_dir:
            cams = scanned('raw_images', s.raw_images_dir, scan_cameras)
            text.append("Images — sampled camera names:\n", style=SAND)
            for line in cams.summary_lines() or ["no imagery found there"]:
                style = WARN if line.startswith("UNRECOGNISED") else TEAL
                text.append("  · ", style=style)
                text.append(line + "\n")
        if s.processed_data:
            processed = scanned('processed', s.processed_data, scan_processed_data)
            text.append("Processed data:\n", style=SAND)
            if processed["datatables"]:
                text.append("  · ", style=TEAL)
                text.append(f"ROVDataConcat output: "
                            f"{processed['datatables'][0].name}\n")
            if processed["utm_logs"]:
                text.append("  · ", style=TEAL)
                text.append(f"georeferenced log: "
                            f"{processed['utm_logs'][0].name}\n")
            if not (processed["datatables"] or processed["utm_logs"]):
                text.append("  · ", style=WARN)
                text.append("no datatables found there\n")
        if s.video_path:
            text.append("Survey video: ", style=SAND)
            text.append((Path(s.video_path).name + "\n") if Path(s.video_path).is_file()
                        else "file not found — check the path\n", style=TEAL if Path(s.video_path).is_file() else WARN)
        if s.results_root:
            text.append("\nResults structure (pipeline-created):\n",
                        style=SAND)
            for name, desc in (
                    ("raw_images/", "imagery"),
                    ("batched_images_by_zone/", "zones"),
                    ("aligned_components/", "components"),
                    ("merged/ · exports/ · RC_projects/", "deliverables")):
                text.append(f"  {name:32s}", style=MIST)
                text.append(desc + "\n", style=MIST)
        self.query_one("#s-detect", Static).update(text)

    def on_input_changed(self, event: Input.Changed) -> None:
        self.query_one("#s-problem", Static).update("")
        if event.input.id in ("s-cruise", "s-rawimages", "s-processed", "s-video", "s-results"):
            if self._detection_timer is not None:
                self._detection_timer.stop()
            self._detection_timer = self.set_timer(0.3, self._refresh_detection)
        elif event.input.id in ("s-expedition", "s-dive"):
            app: WildScanApp = self.app  # type: ignore[assignment]
            self._pull()
            results = self.query_one("#s-results", Input)
            if app.session.label and (not results.value.strip() or results.value == self._suggested_results):
                self._suggested_results = app.session.suggested_results_root(None)
                results.value = self._suggested_results

    def on_unmount(self) -> None:
        if self._detection_timer is not None:
            self._detection_timer.stop()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id not in ("s-continue", "s-status"):
            return
        app: WildScanApp = self.app  # type: ignore[assignment]
        self._pull()
        s = app.session
        if not s.results_root:
            self.query_one("#s-problem", Static).update(
                Text("Enter a results folder to continue or view existing results.", style=WARN))
            return
        if event.button.id == "s-status":
            if not Path(s.results_root).is_dir():
                self.query_one("#s-problem", Static).update(
                    Text("No results folder exists here yet. Continue to plan a run, "
                         "or choose an existing results folder.", style=WARN))
                return
            app.push_screen(StatusScreen())
            return
        for name, label, kind in (('cruise_folder', 'Dive folder', 'path'),
                                  ('raw_images_dir', 'Images folder', 'path'),
                                  ('video_path', 'Survey video', 'file'),
                                  ('processed_data', 'Processed navigation folder', 'path')):
            value = getattr(s, name)
            if value:
                problem = Question('session', name, label, kind).validate(value)
                if not problem and name == 'video_path':
                    problem = Question('session', name, label, 'video').validate(value)
                if problem:
                    self.query_one("#s-problem", Static).update(
                        Text(f"{label}: {problem}. Check the path or clear this optional field.", style=WARN))
                    return
        if self._detection_timer is not None:
            self._detection_timer.stop()
        # Preview caches never supply the plan's source identity.
        self._refresh_detection(force=True)
        try:
            prepare_results_root(s)
            ws = s.workspace()
            statuses = ws.detect()
        except (OSError, ValueError) as exc:
            self.query_one("#s-problem", Static).update(
                Text(f"Unable to open the results folder: {exc}. Choose a writable folder.", style=WARN))
            return
        s.enabled = default_enabled(ws, statuses)
        if (s.raw_images_dir and not s.video_path and not app.scan.videos
                and statuses['extract'].summary != 'interrupted run - retry required'):
            s.enabled = [key for key in s.enabled if key != 'extract']
        app.push_screen(StagePickScreen(statuses))


class StagePickScreen(Screen):
    """RC_Main's checkbox, verbatim in spirit: arrow keys to move, space to
    select, enter to confirm."""

    BINDINGS = [
        Binding("escape", "back", "Back"),
        Binding("enter", "confirm", "Confirm", priority=True),
    ]

    def __init__(self, statuses: dict[str, StageStatus] | None = None) -> None:
        super().__init__()
        self._initial_statuses = statuses

    def compose(self) -> ComposeResult:
        with Vertical(classes="panel"):
            yield Label("Choose stages (arrow keys to move, space to "
                        "select, enter to confirm)", classes="panel-title")
            yield SelectionList(id="stage-pick")
            yield Static("", id="pick-note")
        yield Footer()

    def on_mount(self) -> None:
        app: WildScanApp = self.app  # type: ignore[assignment]
        ws = app.session.workspace()
        statuses = (ws.detect() if self._initial_statuses is None
                    else self._initial_statuses)
        self._initial_statuses = None
        picker = self.query_one("#stage-pick", SelectionList)
        for key in ALL_STAGES:
            st = statuses.get(key)
            note = f" — {st.status}: {st.summary}" if st and st.summary else ""
            picker.add_option(Selection(
                f"{STAGE_TITLES.get(key, key)}{note}", key,
                key in app.session.enabled))
        done = [STAGE_TITLES[k] for k in ALL_STAGES
                if statuses.get(k) and statuses[k].status == "done"]
        note = Text("Stages run from top to bottom. Untick stages you do not need.\n", style=MIST)
        if done:
            note.append("Already complete and unselected: " + ", ".join(done) + "\n")
        if app.session.raw_images_dir and 'extract' not in app.session.enabled:
            note.append("Existing images supplied: video extraction starts unselected.\n")
        note.append("Preparation and alignment share one command; pauses occur before merge, models, exports and publishing.")
        self.query_one("#pick-note", Static).update(note)

    def action_back(self) -> None:
        self.app.pop_screen()

    def action_confirm(self) -> None:
        app: WildScanApp = self.app  # type: ignore[assignment]
        picker = self.query_one("#stage-pick", SelectionList)
        app.session.enabled = [k for k in ALL_STAGES
                               if k in picker.selected]
        if not app.session.enabled:
            self.query_one("#pick-note", Static).update(
                Text("select at least one stage", style=WARN))
            return
        app.scan = (scan_raw_data(app.session.cruise_folder)
                    if app.session.cruise_folder else RawDataScan())
        app.questions = build_questions(app.session, app.scan)
        if app.questions:
            app.push_screen(WizardScreen())
        else:
            app.push_screen(SummaryScreen())


class WizardScreen(Screen):
    """One question at a time - the parameter's own description, prefilled
    from detection, then the last run, then the module default."""

    BINDINGS = [Binding("escape", "back", "Back")]

    def __init__(self) -> None:
        super().__init__()
        self.index = 0
        self.answered: set[str] = set()

    def compose(self) -> ComposeResult:
        with VerticalScroll(classes="panel"):
            yield Label("", id="w-progress", classes="panel-title")
            yield Static("", id="w-stage")
            yield Label("", id="w-prompt")
            yield Input(id="w-answer")
            yield Static("", id="w-problem")
            with Horizontal():
                yield Button("Back", id="w-back")
                yield Button("Next", id="w-next", variant="primary")
        yield Footer()

    def on_mount(self) -> None:
        self._show()

    def _question(self):
        app: WildScanApp = self.app  # type: ignore[assignment]
        return app.questions[self.index]

    def _show(self) -> None:
        app: WildScanApp = self.app  # type: ignore[assignment]
        q = self._question()
        total = len(app.questions)
        self.query_one("#w-progress", Label).update(
            f"Question {self.index + 1} of {total}")
        self.query_one("#w-stage", Static).update(
            Text(STAGE_TITLES.get(q.stage,
                                  "Camera identification"
                                  if q.stage == "cameras" else q.stage),
                 style=TEAL))
        if q.stage == 'cameras':
            self.query_one("#w-stage", Static).update(Text(
                "Optional camera notes — these record what you know. They do not add "
                "calibration or position priors; unknown cameras still need a registry entry.", style=WARN))
        self.query_one("#w-prompt", Label).update(
            q.prompt + ("  *" if q.required else ""))
        answer = self.query_one("#w-answer", Input)
        answer.value = (app.session.answers.get(q.arg, q.default)
                        if q.arg in self.answered else q.default)
        self.query_one("#w-problem", Static).update("")
        answer.focus()

    def _commit_and(self, delta: int) -> None:
        app: WildScanApp = self.app  # type: ignore[assignment]
        q = self._question()
        value = self.query_one("#w-answer", Input).value
        if delta > 0:
            problem = q.validate(value)
            if problem:
                self.query_one("#w-problem", Static).update(
                    Text(f"! {problem}", style=WARN))
                return
        app.session.answers[q.arg] = value.strip()
        self.answered.add(q.arg)
        self.index += delta
        if self.index < 0:
            self.app.pop_screen()
        elif self.index >= len(app.questions):
            self.index = len(app.questions) - 1
            app.push_screen(SummaryScreen())
        else:
            self._show()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "w-answer":
            self._commit_and(+1)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "w-next":
            self._commit_and(+1)
        elif event.button.id == "w-back":
            self._commit_and(-1)

    def action_back(self) -> None:
        self._commit_and(-1)


class SummaryScreen(Screen):
    """RC_Main printed 'Parameters:' before running - same, plus the gate
    style choice, then Run."""

    BINDINGS = [Binding("escape", "back", "Back")]

    def compose(self) -> ComposeResult:
        with VerticalScroll(classes="panel"):
            yield Label("Review your run", classes="panel-title")
            yield Static("", id="sum-params")
            yield Label("Continue automatically between stages? (true/false)")
            yield Input(id="sum-auto")
            yield Static("", id="sum-problem")
        with Horizontal(classes="actions"):
            yield Button("Run", id="sum-run", variant="primary")
        yield Footer()

    def on_mount(self) -> None:
        app: WildScanApp = self.app  # type: ignore[assignment]
        s = app.session
        text = Text()
        text.append(f"  expedition_dive: {s.label or '(unset)'}\n")
        text.append(f"  cruise folder:   {s.cruise_folder or '(unset)'}\n")
        text.append(f"  raw images:      {s.raw_images_dir or '(unset)'}\n")
        text.append(f"  video:           {s.video_path or '(unset)'}\n")
        text.append(f"  processed data:  {s.processed_data or '(unset)'}\n")
        text.append(f"  results root:    {s.results_root}\n")
        text.append(f"  stages:          "
                    f"{', '.join(STAGE_TITLES.get(k, k) for k in s.enabled)}\n\n")
        for q in app.questions:
            text.append(f"  {q.label or q.prompt}: {s.answers.get(q.arg, q.default) or '(default)'}\n",
                        style=MIST)
        if 'publish' in s.enabled:
            import os
            live = bool(os.environ.get('CESIUM_ION_TOKEN') or os.environ.get('NIRACLIENT_DIR'))
            text.append("\nPublishing: " + ("uploads to configured services" if live else
                        "preview only — no publishing credentials are set") + "\n", style=WARN if live else MIST)
        self.query_one("#sum-params", Static).update(text)
        self.query_one("#sum-auto", Input).value = (
            "true" if s.continue_automatically else "false")

    def action_back(self) -> None:
        self.app.pop_screen()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id != "sum-run":
            return
        app: WildScanApp = self.app  # type: ignore[assignment]
        value = self.query_one("#sum-auto", Input).value
        problem = Question('run', 'auto', '', 'bool', required=True).validate(value)
        if problem:
            self.query_one("#sum-problem", Static).update(
                Text("Enter true to continue automatically, or false to pause between commands.", style=WARN))
            return
        app.session.continue_automatically = (
            value.strip().lower() == "true")
        app.push_screen(RunScreen())


class RunScreen(Screen):
    """Stages in sequence; between stages, a gate - RC_Main's 'Press enter
    to continue...' - unless Continue Automatically."""

    BINDINGS = [
        Binding("enter", "gate", "Continue", priority=True),
        Binding("x", "stop", "Stop stage"),
        Binding("escape", "leave", "Status"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.runner = CommandRunner(self)
        self.commands = []
        self.current = -1
        self.waiting_gate = False
        self.retry_current = False

    def compose(self) -> ComposeResult:
        with Vertical(classes="panel", id="r-header"):
            yield Label("", id="r-title", classes="panel-title")
            yield Static("", id="r-gate")
            with Horizontal(id="r-controls"):
                yield Button("Continue", id="r-continue", variant="primary",
                             disabled=True)
                yield Button("Stop stage", id="r-stop", variant="warning",
                             disabled=True)
                yield Button("Edit plan", id="r-back", disabled=True)
                yield ProgressBar(id="r-progress", total=100, show_eta=False)
                yield Static("", id="r-eta")
        yield RichLog(id="r-log", max_lines=3000, wrap=False)
        yield Footer()

    def on_mount(self) -> None:
        app: WildScanApp = self.app  # type: ignore[assignment]
        try:
            if "export" in app.session.enabled:
                export_names_file(app.session)
            write_camera_records(app.session)
            self.commands = build_commands(app.session)
            save_last_run(app.session)     # persist the anchored filesystem paths
        except (OSError, ValueError, RuntimeError) as exc:
            self.query_one("#r-title", Label).update("Unable to prepare this run")
            self.query_one("#r-log", RichLog).write(Text(str(exc), style="bold red"))
            self.query_one("#r-gate", Static).update(
                Text("Check the error below. Edit the plan to correct it, or Esc to view results.", style=WARN))
            self.query_one("#r-back", Button).disabled = False
            return
        self._advance()

    def _refresh_export_command(self) -> None:
        """Re-resolve the Export stage's --project/--names from a FRESH
        workspace, immediately before it launches.

        Both were baked in at plan time (on_mount), before any stage ran,
        so a run that included Merge exported the PREVIOUS run's assembly
        under the new run's name: the argv carried the old
        ws.assembly_project() and export_names_file's `if names:` guard
        left a stale components.names in place (audit 2026-08-07).
        """
        app: WildScanApp = self.app  # type: ignore[assignment]
        cmd = self.commands[self.current]
        if "export_deliverables.py" not in " ".join(cmd.argv):
            return
        export_names_file(app.session)
        ws = Workspace(app.session.results_root)
        project = str(ws.assembly_project() or "")
        for flag, value in (("--project", project),
                            ("--names", str(ws.exports / "components.names"))):
            if flag in cmd.argv:
                cmd.argv[cmd.argv.index(flag) + 1] = value

    def _advance(self) -> None:
        self.current += 1
        log = self.query_one("#r-log", RichLog)
        if self.current >= len(self.commands):
            self.query_one("#r-title", Label).update("All stages finished")
            self.query_one("#r-gate", Static).update(
                Text("Esc for the pipeline status view.", style=OK))
            self.query_one("#r-continue", Button).disabled = True
            self.query_one("#r-back", Button).disabled = False
            return
        cmd = self.commands[self.current]
        self.query_one("#r-title", Label).update(
            f"Stage {self.current + 1} of {len(self.commands)}: {cmd.stage}")
        self.query_one("#r-progress", ProgressBar).update(progress=0)
        self.query_one("#r-eta", Static).update("Waiting for operation progress")
        try:
            self._refresh_export_command()
            log.write(Text(f"launching: {cmd.display}", style=TEAL))
            self.runner.start(cmd)
        except (OSError, ValueError, RuntimeError) as exc:
            log.write(Text(f"launch failed: {exc}", style="bold red"))
            self.retry_current = True
            self.waiting_gate = True
            self.query_one("#r-stop", Button).disabled = True
            self.query_one("#r-continue", Button).disabled = False
            self.query_one("#r-back", Button).disabled = False
            self.query_one("#r-gate", Static).update(
                Text("Launch failed. Fix the problem, then press Continue "
                     "to retry this stage, or Esc for status.", style=WARN))
            return
        self.query_one("#r-stop", Button).disabled = False
        self.query_one("#r-back", Button).disabled = True
        self.query_one("#r-continue", Button).disabled = True
        self.query_one("#r-gate", Static).update("")

    def on_log_line(self, message: LogLine) -> None:
        self.query_one("#r-log", RichLog).write(message.line)

    def on_progress_update(self, message: ProgressUpdate) -> None:
        self.query_one("#r-progress", ProgressBar).update(
            progress=message.fraction * 100)
        self.query_one("#r-eta", Static).update(
            Text(f"Current operation · about {message.eta_s / 60:.1f} min remaining",
                 style=MIST))

    def on_run_finished(self, message: RunFinished) -> None:
        app: WildScanApp = self.app  # type: ignore[assignment]
        log = self.query_one("#r-log", RichLog)
        ok = message.returncode == 0
        log.write(Text(f"stage finished with exit code {message.returncode}",
                       style=OK if ok else "bold red"))
        self.query_one("#r-stop", Button).disabled = True
        self.query_one("#r-back", Button).disabled = False
        if ok:
            self.query_one("#r-progress", ProgressBar).update(progress=100)
        self.query_one("#r-eta", Static).update(
            "Command finished" if ok else "Cancelled" if message.cancelled else "Command failed")
        if not ok:
            self.retry_current = True
            self.query_one("#r-gate", Static).update(
                Text(("Stage cancelled. Review outputs, then press Continue "
                      "to retry this stage, or Esc for status." if message.cancelled
                      else "Stage failed. Fix the problem, then press Continue "
                      "to retry this stage, or Esc for status."), style=WARN))
            self.query_one("#r-continue", Button).disabled = False
            self.waiting_gate = True
            return
        if (app.session.continue_automatically
                or self.current + 1 >= len(self.commands)):
            self._advance()
        else:
            nxt = self.commands[self.current + 1].stage
            self.query_one("#r-gate", Static).update(
                Text(f"Press enter to continue with: {nxt}", style=SAND))
            self.query_one("#r-continue", Button).disabled = False
            self.waiting_gate = True

    def action_gate(self) -> None:
        if self.waiting_gate and not self.runner.running:
            self.waiting_gate = False
            if self.retry_current:
                self.current -= 1
                self.retry_current = False
            self._advance()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "r-continue":
            self.action_gate()
        elif event.button.id == "r-stop":
            self.runner.terminate()
        elif event.button.id == "r-back" and not self.runner.running:
            self.app.pop_screen()

    def action_stop(self) -> None:
        self.runner.terminate()

    def action_leave(self) -> None:
        if not self.runner.running:
            self.app.push_screen(StatusScreen())


class StatusScreen(Screen):
    """The census + final components - presenting results."""

    BINDINGS = [Binding("escape", "back", "Back")]

    def compose(self) -> ComposeResult:
        with Vertical(classes="panel", id="st-pipeline-panel"):
            yield Label("Pipeline", classes="panel-title")
            table = DataTable(id="st-pipeline", cursor_type="row")
            table.add_columns("stage", "state", "summary")
            yield table
        with VerticalScroll(classes="panel", id="st-components-panel"):
            yield Label("Final components", classes="panel-title")
            comps = DataTable(id="st-components", cursor_type="row")
            comps.add_columns("component", "cameras", "scale", "verdict",
                              "model", "exports")
            yield comps
            yield Static("", id="st-note")
        yield Footer()

    def on_mount(self) -> None:
        app: WildScanApp = self.app  # type: ignore[assignment]
        ws = app.session.workspace()
        table = self.query_one("#st-pipeline", DataTable)
        for key, st in ws.detect().items():
            table.add_row(Text(st.title, style=SAND),
                          status_text(st.status, st.status), st.summary)
        comps = self.query_one("#st-components", DataTable)
        components = ws.components()
        for c in components:
            scale = "-" if c.scale is None else f"{c.scale:.3f}"
            comps.add_row(
                Text(c.key, style=SAND),
                f"{c.cameras:,}" if c.cameras else "-",
                scale, c.scale_status or "-",
                "done" if c.modelled else "-",
                ", ".join(c.exported) or "-")
        note = Text(f"Results folder: {ws.root}\n", style=MIST)
        note.append("No final components are available yet.\n" if not components else
                    "Scale verdicts and model completion use the saved reports for this assembly.\n")
        note.append("Folder presence alone does not verify native processing or dataset quality.")
        self.query_one("#st-note", Static).update(note)

    def action_back(self) -> None:
        self.app.pop_screen()


class WildScanApp(App):
    TITLE = f"{APP_NAME} · {ORG}"
    CSS = CSS

    def __init__(self, workspace: str | None = None) -> None:
        super().__init__()
        self.session: Session = default_session()
        if workspace:
            self.session.results_root = workspace
        self.scan = RawDataScan()
        self.questions = []

    @property
    def workspace(self) -> Workspace | None:
        return (Workspace(self.session.results_root)
                if self.session.results_root else None)

    def on_mount(self) -> None:
        self.push_screen(SessionScreen())


def main() -> int:
    parser = argparse.ArgumentParser(prog='wildscan', description=TAGLINE)
    parser.add_argument('workspace', nargs='?', help='workspace to open')
    parser.add_argument('--version', action='version',
                        version=f'{APP_NAME} {__version__}')
    args = parser.parse_args()
    WildScanApp(args.workspace).run()
    return 0
