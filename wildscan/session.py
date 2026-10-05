"""Session model for the WildScan portal - RC_Main's interaction, preserved.

The portal mirrors the flow the pipeline has had since RC_Main:

    1. Wild Sync run directory (or a folder of runs) and workspace  (this module)
    2. checkbox stage selection, everything sensible pre-selected
    3. ONE question at a time, in module order, each parameter's own
       DESCRIPTION as the prompt, honouring disable_when_module_active
    4. parameter summary, then run - with gates between stages

This file owns the non-UI halves: run-directory detection and the camera
scan, results-root structure, last-run persistence, the question list, and
command assembly. It is a PORTAL ONLY - the pipeline scripts are untouched,
and chained modules run in a single main.py invocation exactly as they
always have (the in-process hand-off between Batch Directory and Alignment
IS the current data handling; splitting them would change behaviour).

Last-run answers persist via the pipeline's own SettingsStore under the
'wildscan' section of rs_settings.json, so the next session opens with the
previous run directories and workspace as defaults.
"""
from __future__ import annotations

import logging
import math
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

from modules import camera_registry
from modules.calibration_sidecars import MODES as CALIBRATION_MODES
from modules.wildsync_intake.intake import (
    VARIANTS,
    IntakeError,
    find_run_directories,
    image_variant,
    node_directories,
    split_run_paths,
)

from .workspace import STAGE_TITLES as CENSUS_TITLES
from .workspace import StageStatus, Workspace, _find_flight_logs, _load_json

REPO = Path(__file__).resolve().parent.parent

_quiet = logging.getLogger("wildscan.session")
_quiet.addHandler(logging.NullHandler())
_quiet.propagate = False

# Pipeline module chain, in main.py's order, plus the post-align stages the
# portal drives as separate commands.
CHAIN_STAGES = ["intake", "preprocess", "batch", "align"]
POST_STAGES = ["merge", "model", "export", "publish"]
ALL_STAGES = CHAIN_STAGES + POST_STAGES

MODULE_DISPLAY = {
    "intake": "Wild Sync Intake",
    "preprocess": "Preprocess Images",
    "batch": "Batch Directory",
    "align": "RealityScan Alignment",
}

STAGE_TITLES = {key: CENSUS_TITLES[key] for key in ALL_STAGES}

# The results layout the pipeline itself creates under the results root -
# shown to the operator so "auto-created structure" is explicit, created by
# the MODULES, never by the portal.
RESULTS_LAYOUT = [
    ("raw_images/", "images copied in by Wild Sync Intake, flight log, manifest"),
    ("preprocessed_images/", "CLAHE output (align + texture source)"),
    ("batched_images_by_zone/", "per-zone trees + batch_inputs.json"),
    ("aligned_components/", "per-zone .rsalign + identity manifests"),
    ("merged/", "merge attempts, assembly project, EVALUATION_READY"),
    ("exports/", "OBJ/FBX/PLY deliverables per component"),
    ("logs/", "driver + resource logs"),
    ("RC_projects/", "dated project copies"),
]


# ------------------------------------------------------- run directories

@dataclass(frozen=True)
class CameraCount:
    """One camera node folder of a run: frame files per image variant."""
    node: str
    camera: str                  # camera key from modules/cameras.json, or ""
    card: int = 0
    review: int = 0
    raw: int = 0
    hidden_files: int = 0

    def summary_line(self) -> str:
        who = (f"{self.node} ({self.camera})" if self.camera
               else f"{self.node} (matches no camera in modules/cameras.json)")
        return (f"{who}: {self.card:,} card, {self.review:,} review, "
                f"{self.raw:,} RAW (RAW is not an input)")


@dataclass(frozen=True)
class RunInfo:
    """A detected Wild Sync run directory."""
    path: Path
    has_run_json: bool
    cameras: tuple[CameraCount, ...] = ()

    @property
    def run_id(self) -> str:
        return self.path.name


@dataclass
class RunDataScan:
    """What the run-directory field actually holds - drives the preview."""
    runs: list[RunInfo] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)

    def summary_lines(self) -> list[str]:
        lines = []
        for run in self.runs:
            lines.append(f"run {run.run_id}"
                         + ("" if run.has_run_json else " (no run.json)"))
            lines += [f"  {cam.summary_line()}" for cam in run.cameras]
        lines += self.problems
        if not lines:
            lines.append("nothing recognised yet - point me at a Wild Sync "
                         "run directory")
        return lines


def scan_cameras(run_dir: str | Path) -> tuple[CameraCount, ...]:
    """Frame files per camera node and image variant of one run directory.

    Node folders, variants and frame suffixes come from the intake module's
    own helpers; hidden files (names starting with '.', such as macOS '._*'
    copies) are never frames, exactly as in the intake.
    """
    counts = []
    for node_dir in node_directories(str(run_dir)):
        node = os.path.basename(node_dir)
        family = next((f for f in camera_registry.REGISTRY.families
                       if f.name.lower() == node.lower()), None)
        found = {"card": 0, "review": 0, "raw": 0}
        hidden = 0
        for name in os.listdir(node_dir):
            if name.startswith("."):
                hidden += 1
                continue
            variant = image_variant(name)
            if variant in found and os.path.isfile(os.path.join(node_dir, name)):
                found[variant] += 1
        counts.append(CameraCount(node, family.camera if family else "",
                                  hidden_files=hidden, **found))
    return tuple(counts)


def scan_run_data(location: str | Path) -> RunDataScan:
    """Read-only census of the run-directory field: each ';'-separated path
    is a Wild Sync run directory or a folder of them (the intake's own
    rule). Anything else is reported as a problem."""
    scan = RunDataScan()
    seen: set[Path] = set()
    for path in split_run_paths(str(location)):
        try:
            runs = find_run_directories([path])
        except IntakeError as exc:
            scan.problems.append(str(exc))
            continue
        for run in runs:
            run_path = Path(run)
            if run_path in seen:
                continue
            seen.add(run_path)
            scan.runs.append(RunInfo(run_path,
                                     (run_path / "run.json").is_file(),
                                     scan_cameras(run_path)))
    return scan


# -------------------------------------------------------------- persistence

def _settings():
    from module_base.settings_store import SettingsStore
    return SettingsStore()


_PERSISTED_FIELDS = ("run_dirs", "results_root", "continue_automatically")


def load_last_run() -> dict:
    """The previous session's answers - the new defaults (project decision)."""
    store = _settings()
    out = {}
    for key in _PERSISTED_FIELDS:
        value = store.get("wildscan", key, None)
        if value not in (None, ""):
            out[key] = str(value)
    answers = store.get("wildscan", "answers", None)
    if isinstance(answers, dict):
        out["answers"] = {str(k): str(v) for k, v in answers.items()}
    return out


def save_last_run(session: Session) -> None:
    store = _settings()
    store.set("wildscan", "run_dirs", session.run_dirs)
    store.set("wildscan", "results_root", session.results_root)
    store.set("wildscan", "continue_automatically",
              session.continue_automatically)
    store.set("wildscan", "answers", dict(session.answers))


# ------------------------------------------------------------------ session

@dataclass
class Session:
    # One Wild Sync run directory, or a folder of them; several paths are
    # separated by ';' (the intake's own convention).
    run_dirs: str = ""
    results_root: str = ""
    continue_automatically: bool = False
    enabled: list[str] = field(default_factory=list)
    answers: dict[str, str] = field(default_factory=dict)   # cli_long -> value

    def workspace(self) -> Workspace:
        return Workspace(self.results_root)


def default_session() -> Session:
    last = load_last_run()
    return Session(
        run_dirs=last.get("run_dirs", ""),
        results_root=last.get("results_root", ""),
        continue_automatically=(last.get("continue_automatically", "false")
                                .lower() == "true"),
        answers=dict(last.get("answers", {})),
    )


def stage_statuses(ws: Workspace) -> dict[str, StageStatus]:
    """The census for every stage the portal drives, intake first."""
    census = ws.detect()
    return {key: census[key] for key in ALL_STAGES}


def default_enabled(ws: Workspace,
                    statuses: dict[str, StageStatus] | None = None) -> list[str]:
    """Resume-aware pre-selection: stages already DONE are unticked, the
    rest ticked - RC_Main pre-selected everything; a resumable portal
    pre-selects what remains."""
    if statuses is None:
        statuses = stage_statuses(ws)
    interrupted = _load_json(ws.root / "interrupted_stage.json").get("stages", [])
    if not isinstance(interrupted, list):
        interrupted = []
    return [k for k in ALL_STAGES
            if k in interrupted or statuses.get(k) is None or statuses[k].status != "done"]


# ---------------------------------------------------------------- questions

@dataclass
class Question:
    stage: str                   # stage key it belongs to
    arg: str                     # cli_long
    prompt: str                  # the parameter's own description (RC_Main)
    kind: str                    # text | path | file | rundirs | number | bool
    default: str = ""
    required: bool = False
    choices: tuple[str, ...] = ()
    value_type: type = str
    label: str = ""

    def validate(self, value: str) -> str | None:
        value = value.strip()
        if not value:
            return "this one is required" if self.required else None
        if self.choices and value.lower() not in {c.lower()
                                                  for c in self.choices}:
            return "must be one of: " + ", ".join(self.choices)
        if self.kind == "path" and not Path(value).is_dir():
            return f"{value} is not a directory"
        if self.kind == "file" and not Path(value).is_file():
            return f"{value} is not a file"
        if self.kind == "rundirs":
            try:
                find_run_directories(split_run_paths(value))
            except IntakeError as exc:
                return str(exc)
        if self.kind == "number":
            try:
                parsed = self.value_type(value) if self.value_type is int else float(value)
            except ValueError:
                return "Enter a whole number." if self.value_type is int else "Enter a number."
            if self.value_type is not int and not math.isfinite(parsed):
                return "Enter a finite number."
        if self.kind == "bool" and value.lower() not in ("true", "false"):
            return "true or false"
        return None


_KIND_BY_NAME = {
    "ws_run_dirs": "rundirs",
    "pre_input_image_dir": "path",
    "batch_input_image_dir": "path",
    "batch_flight_log_path": "file",
    "rs_input_image_dir": "path",
    "rs_flight_log_path": "file",
    "rs_flight_log_params": "file",
}
# Parameters that must be answered even though the module gives them a
# default (a parameter whose default is None is always required).
_REQUIRED: set[str] = set()
# Answers constrained to a fixed set - validated at the question, not four
# screens later.
_CHOICES_BY_NAME: dict[str, tuple[str, ...]] = {
    "ws_variant": VARIANTS,
    "ws_calibration": tuple(CALIBRATION_MODES),
}
# Alignment answers the portal fixes instead of asking: RealityScan's own
# console display stays off.
_FORCED_ANSWERS = {"r_display_output": "false"}

_MODULES = None


def _module_registry() -> dict:
    global _MODULES
    if _MODULES is None:
        from modules.image_batcher.batch_directory import BatchDirectory
        from modules.preprocess_images.preprocess_images import PreprocessImages
        from modules.realityscan_interface.realityscan_interface import (
            RealityScanAlignment,
        )
        from modules.wildsync_intake.wildsync_intake import WildSyncIntake
        _MODULES = {
            "intake": WildSyncIntake(_quiet),
            "preprocess": PreprocessImages(_quiet),
            "batch": BatchDirectory(_quiet),
            "align": RealityScanAlignment(_quiet),
        }
    return _MODULES


def chain_arg_names(chain: list[str]) -> set[str]:
    """Every ``--<cli_long>`` main.py's parser ACCEPTS for this chain.

    Mirrors main.py's initialize_parameters exactly: the two global
    parameters, plus each enabled module's parameters minus any whose
    disable_when_module_active names another enabled module. main.py builds
    its argparse from the ENABLED modules only and rejects anything else
    with exit 2, so forwarding the full persisted answer set made the
    portal's own command unrunnable on 29 of 31 stage selections - and
    default_enabled() unticks completed stages, so the SECOND session
    always landed in the broken region.
    """
    names = {"output_dir", "continue_automatically"}
    enabled_displays = {MODULE_DISPLAY[k] for k in chain if k in MODULE_DISPLAY}
    for key in chain:
        module = _module_registry().get(key)
        if module is None:
            continue
        for p in module.get_parameters().values():
            disabled_by = getattr(p, "disable_when_module_active", None)
            if disabled_by:
                if isinstance(disabled_by, str):
                    disabled_by = [disabled_by]
                if any(d in enabled_displays for d in disabled_by):
                    continue
            names.add(p.cli_long)
    return names


def _detection_prefills(session: Session) -> dict[str, str]:
    """Auto-detected answers, keyed by cli_long. Only offered as defaults -
    every one still passes through its question."""
    ws = session.workspace()
    out: dict[str, str] = {}
    if session.run_dirs.strip():
        out["w_input"] = session.run_dirs.strip()
    raw = ws.raw_images if ws.raw_images.is_dir() else None
    if raw:
        out["p_input"] = str(raw)
    batch_src = ws.preprocessed if ws.preprocessed.is_dir() else raw
    if batch_src:
        out["b_input"] = str(batch_src)
    alignment_src = ws.batched if ws.batched.is_dir() else batch_src
    if alignment_src:
        out["r_input"] = str(alignment_src)
    # Each zone discovers its own navigation. An old explicit override must
    # never replace the current input's logs or invent priors for bare images.
    out["r_flight_log"] = ""
    logs = _find_flight_logs(ws.raw_images) or _find_flight_logs(ws.root)
    logs = [p for p in logs if ws.batched not in p.parents]
    if logs:
        out["b_flight_log_path"] = str(logs[0])
    out["r_project_label"] = ""
    return out


def build_questions(session: Session) -> list[Question]:
    """RC_Main's question list: per enabled chain module, in order, each
    prompt_user parameter - SKIPPING any whose disable_when_module_active
    names another enabled module (the upstream module will hand the value
    over in-process, exactly as the pipeline already does)."""
    enabled_displays = {MODULE_DISPLAY[k] for k in session.enabled
                        if k in MODULE_DISPLAY}
    detected = _detection_prefills(session)
    questions: list[Question] = []

    for key in CHAIN_STAGES:
        if key not in session.enabled:
            continue
        for name, p in _module_registry()[key].get_parameters().items():
            if not p.prompt_user:
                continue
            if p.cli_long in _FORCED_ANSWERS:
                continue
            disabled_by = getattr(p, "disable_when_module_active", None)
            if disabled_by:
                if isinstance(disabled_by, str):
                    disabled_by = [disabled_by]
                if any(d in enabled_displays for d in disabled_by):
                    continue
            kind = _KIND_BY_NAME.get(name)
            if kind is None:
                kind = ("bool" if p.type is bool
                        else "number" if p.type in (int, float) else "text")
            default = (detected[p.cli_long] if p.cli_long in detected
                       else session.answers.get(p.cli_long,
                           "" if p.default_value is None else str(p.default_value)))
            questions.append(Question(
                stage=key, arg=p.cli_long,
                prompt=(p.description or p.name).strip(),
                kind=kind, default=default,
                required=name in _REQUIRED or p.default_value is None,
                choices=_CHOICES_BY_NAME.get(name, ()),
                value_type=p.type, label=p.name))
    return questions


# ----------------------------------------------------------------- commands

@dataclass
class StageCommand:
    stage: str                   # display label for the gate screen
    argv: list[str]
    env: dict[str, str]
    needs_realityscan: bool = False
    workspace: str | None = None
    stages: tuple[str, ...] = ()
    cwd: str | None = None

    @property
    def display(self) -> str:
        return " ".join(a if " " not in a else f'"{a}"' for a in self.argv)


def _anchor_run_dirs(value: str, cwd: Path) -> str:
    """Run-directory paths made absolute against ``cwd``, joined with ';'."""
    return ";".join(os.path.abspath(os.path.join(cwd, part))
                    for part in split_run_paths(value))


def build_commands(session: Session) -> list[StageCommand]:
    """The run plan: ONE main.py invocation for every enabled chain module
    (in-process hand-off preserved - portal only), then each post stage as
    its own command with a gate between them."""
    commands: list[StageCommand] = []
    caller_cwd = Path.cwd()
    # Absolute paths survive the native workflow's separate working directory.
    # abspath preserves aliases so downstream alias guards can still inspect them.
    if session.results_root.strip():
        session.results_root = os.path.abspath(
            os.path.join(caller_cwd, session.results_root.strip()))
    session.run_dirs = _anchor_run_dirs(session.run_dirs, caller_cwd)
    chain = [k for k in CHAIN_STAGES if k in session.enabled]
    path_args = {'output_dir'}
    run_dir_args = set()
    for module in _module_registry().values():
        for name, parameter in module.get_parameters().items():
            if _KIND_BY_NAME.get(name) in ('path', 'file'):
                path_args.add(parameter.cli_long)
            elif _KIND_BY_NAME.get(name) == 'rundirs':
                run_dir_args.add(parameter.cli_long)
    for arg, value in session.answers.items():
        if arg in path_args and value.strip():
            session.answers[arg] = os.path.abspath(os.path.join(caller_cwd, value.strip()))
        elif arg in run_dir_args:
            session.answers[arg] = _anchor_run_dirs(value, caller_cwd)
    ws = session.workspace()

    # RealityScan machine constants (RS_INSTANCE / RS_CACHE_DIR /
    # RS_HEADLESS), resolved ONCE per run plan from the settings store's
    # 'realityscan' section (module_base.settings_store.realityscan_env -
    # the single source of truth; headless defaults False = visible).
    # PRECEDENCE: a variable already set in the
    # user's environment wins over the stored default - realityscan_env
    # returns the env value unchanged in that case, so when CommandRunner
    # overlays this dict onto the inherited environment the user's
    # override survives.
    from module_base.settings_store import realityscan_env
    rs_env = realityscan_env(_settings())

    if chain:
        argv = [sys.executable, str(REPO / "main.py"),
                "--output_dir", session.results_root,
                "--continue_automatically", "true"]
        # ONLY the flags main.py's parser accepts for THIS selection.
        # session.answers is the persisted superset (it carries the whole
        # previous run's answers by design, so a resumed session keeps its
        # defaults) - forwarding all of it made argparse exit 2 with
        # "unrecognized arguments" before a single stage ran.
        accepted = chain_arg_names(chain)
        for arg, value in session.answers.items():
            if arg not in accepted:
                continue
            if value.strip() or arg in ('r_flight_log', 'r_project_label'):
                argv += [f"--{arg}", value.strip()]
        # The forced answers belong to RealityScan Alignment, so they are
        # only legal when 'align' is in the chain; they used to be appended
        # unconditionally, which alone rejected every align-less selection.
        if "align" in chain:
            for arg, value in _FORCED_ANSWERS.items():
                argv += [f"--{arg}", value]
        env = {"RS_MODULES": ",".join(MODULE_DISPLAY[k] for k in chain),
               "RS_NO_INTERACTIVE": "1", "PYTHONIOENCODING": "utf-8"}
        needs_rs = "align" in chain
        if needs_rs:
            env.update(rs_env)
        commands.append(StageCommand(
            stage=" + ".join(MODULE_DISPLAY[k] for k in chain),
            argv=argv, env=env, needs_realityscan=needs_rs, stages=tuple(chain)))

    if "merge" in session.enabled:
        argv = [sys.executable, str(REPO / "merge_zones.py"),
                "--components_root", str(ws.aligned),
                "--images_root", str(ws.batched),
                "--output", str(ws.root / "merged"),
                "--name", f"{ws.root.name}_Assembly",
                "--project_label", "",
                "--min_size", "50", "--target", "0.95",
                "--visible", "true", "--auto_model", "false",
                "--ladder", "merge_first", "--merge_scope", "neighbour",
                "--pair_gate", "overlap", "--assemble_only", "false",
                # 0.0025 = the bounded-loss decision:
                # 0.25% of input cameras, an order of magnitude above the
                # loss of a sound joint solve.
                # Pinned HERE deliberately (not rs_settings): drivers that
                # left merge options unpinned inherited another session's
                # stored values, and
                # test_wildscan pins this flag by test. Scale band 0.90-1.10
                # is the metric-scale oracle gate: align-time scale collapses
                # can pass camera-count oracles, and known-good components
                # fall inside the band. Full provenance: merge_zones.merge_cluster's
                # loss_tolerance_frac comment.
                "--loss_tolerance", "0.0025", "--scale_gate", "true",
                "--scale_min", "0.9", "--scale_max", "1.1"]
        commands.append(StageCommand(
            stage="Merge Components", argv=argv,
            env={"PYTHONIOENCODING": "utf-8", **rs_env},
            needs_realityscan=True, stages=("merge",)))

    if "model" in session.enabled:
        commands.append(StageCommand(
            stage="Generate Models",
            argv=[sys.executable, str(REPO / "run_models.py"),
                  "--workspace", session.results_root],
            env={"PYTHONIOENCODING": "utf-8", **rs_env},
            needs_realityscan=True, stages=("model",)))

    if "export" in session.enabled:
        # Through the python driver -> RealityScanCLI.run_batch_script,
        # like merge and model (hard rule 1). The old ["cmd","/c",bat,...]
        # Popen had no instance lock, no marker hygiene, no verified
        # shutdown, broke on space-containing checkout paths, and let the
        # 'start ""'-booted RealityScan GUI inherit the runner's stdout
        # PIPE (a Windows trap) - run_batch_script gives the .bat
        # a log file instead. Deliverable pinning (OBJ_NiraParts /
        # FBX_Parts / dense PLY) stays in ExportDeliverables.bat and its
        # Metadata presets; the driver only carries the same three
        # arguments the .bat has always taken.
        names_file = ws.exports / "components.names"
        commands.append(StageCommand(
            stage="Export Deliverables",
            argv=[sys.executable,
                  str(REPO / "modules" / "export_deliverables.py"),
                  "--project", str(ws.assembly_project() or ""),
                  "--exports", str(ws.exports),
                  "--names", str(names_file),
                  "--log_dir", str(ws.root / "logs")],
            env={"PYTHONIOENCODING": "utf-8", **rs_env},
            needs_realityscan=True, stages=("export",)))

    if "publish" in session.enabled:
        argv = [sys.executable, str(REPO / "publish_batch.py"),
                "--workspace", session.results_root,
                "--prefix", ws.root.name]
        # Placement comes from each mesh's own .rsInfo sidecar, which records
        # what the exporter actually did. The flight log is pinned here as the
        # INDEPENDENT nav check on that reading - and pinned rather than left
        # to publish_batch for the same reason --loss_tolerance is: the portal
        # states what it ran. --input-crs is no longer passed: it could not
        # express the vertical, which left published models at the sea
        # surface.
        log = workspace_flight_log(ws)
        if log:
            argv += ["--flight-log", str(log)]
        if not (os.environ.get("CESIUM_ION_TOKEN")
                or os.environ.get("NIRACLIENT_DIR")):
            argv.append("--dry-run")
        commands.append(StageCommand(
            stage="Publish (Cesium / Nira)", argv=argv,
            env={"PYTHONIOENCODING": "utf-8"}, stages=("publish",)))

    for command in commands:
        command.cwd = str(caller_cwd)
        command.workspace = os.path.abspath(ws.root)
    return commands


def workspace_flight_log(ws: Workspace) -> Path | None:
    """The workspace's zone-tagged flight log, or None when no flight log
    carries a zone tag.

    The merge output is searched first: the exported components were built
    against the merge's union log.
    """
    from modules.flight_logs import crs_for_flight_log
    candidates = list(_find_flight_logs(ws.raw_images)) or \
        list(_find_flight_logs(ws.root))
    merge = ws.latest_merge()
    if merge:
        candidates = sorted(merge.glob("flight_log*_UTM.txt")) + candidates
    for path in candidates:
        if crs_for_flight_log(str(path)):
            return path
    return None


def workspace_input_crs(ws: Workspace) -> str | None:
    """``'EPSG:32654'`` for the workspace's imagery, or None.

    No longer what places an asset - that comes from each mesh's `.rsInfo`
    sidecar - but still the quickest way to state a workspace's zone.
    """
    from modules.flight_logs import crs_for_flight_log
    log = workspace_flight_log(ws)
    return crs_for_flight_log(str(log)) if log else None


def prepare_results_root(session: Session) -> list[str]:
    """Create the root (only the root - modules own their subfolders) and
    return the layout description for the operator."""
    root = Path(session.results_root)
    root.mkdir(parents=True, exist_ok=True)
    return [f"{name:28s} {desc}" for name, desc in RESULTS_LAYOUT]


def export_names_file(session: Session) -> None:
    """Author exports/components.names from the merge report (BOM-free)."""
    ws = session.workspace()
    merge = ws.latest_merge()
    if not merge:
        return
    from .workspace import _load_json, _records
    rep = _load_json(merge / "merge_report.json")
    names = [c.get("key", "").split("/")[-1]
             for rec in _records(rep, "clusters")
             for c in _records(rec, "final_components")]
    names = [n for n in names if n]
    # `if names:` is deliberate but has a sharp edge the caller must cover:
    # when the CURRENT report yields nothing this returns without touching
    # an existing components.names, so a stale list survives. The export
    # stage re-resolves both --project and --names at launch time
    # (wildscan/app.py _refresh_export_command) for exactly that reason.
    if names:
        ws.exports.mkdir(parents=True, exist_ok=True)
        with open(ws.exports / "components.names", "w",
                  encoding="utf-8", newline="\r\n") as fh:
            fh.write("\n".join(names) + "\n")
