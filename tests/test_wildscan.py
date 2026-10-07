#!/usr/bin/env python3
"""WildScan portal tests: RC_Main's interaction, preserved.

The portal contract under test:
    - Wild Sync run-directory detection and the camera scan feeding prefills
    - stage checkbox with resume-aware pre-selection
    - RC_Main's question order and disable_when_module_active semantics
    - last-run answers becoming the next session's defaults
    - command assembly: one main.py invocation for the chain (in-process
      hand-off preserved - the portal never changes data handling), post
      stages as separate gated commands

Run:  py -3.13 -m pytest tests/test_wildscan.py
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
sys.path.insert(0, REPO_ROOT)

pytest.importorskip("textual")

import wildscan.session as session_mod
from tests.test_wildsync_intake import FRAMES, RUN_ID, build_run
from wildscan.session import (
    RunDataScan,
    Session,
    build_commands,
    build_questions,
    default_enabled,
    scan_cameras,
    scan_run_data,
)
from wildscan.workspace import Workspace


class FakeStore:
    """SettingsStore stand-in so tests never touch the repo's rs_settings."""

    def __init__(self):
        self.data = {}

    def get(self, section, key, fallback=None):
        return self.data.get(section, {}).get(key, fallback)

    def set(self, section, key, value):
        self.data.setdefault(section, {})[key] = value


@pytest.fixture
def store(monkeypatch):
    fake = FakeStore()
    monkeypatch.setattr(session_mod, "_settings", lambda: fake)
    return fake


def make_workspace(tmp_path, *, stage: str) -> Workspace:
    """A results root advanced through the pipeline up to `stage`."""
    ws = tmp_path / "workspace"
    order = ["empty", "images", "intake", "batch", "align",
             "merge", "model", "export"]
    upto = order.index(stage)
    if upto >= 1:
        raw = ws / "raw_images"
        raw.mkdir(parents=True)
        for i in range(4):
            (raw / f"img_{i:03d}.jpg").write_bytes(b"j")
    if upto >= 2:
        # The intake writes a flight log covering every copied image.
        (ws / "raw_images" / "flight_log_4Q_UTM.txt").write_text(
            "filename;X (East);Y (North);Alt\n"
            + "".join(f"img_{i:03d}.jpg;1;2;3\n" for i in range(4)),
            encoding="utf-8")
        (ws / "raw_images" / "wildsync_intake.json").write_text(json.dumps({
            "schema": 1, "status": "complete", "variant": "card",
            "sources": [{"run_id": RUN_ID}], "images": {"matched": 4}}),
            encoding="utf-8")
    if upto >= 3:
        for zone in ("zone_1", "zone_2"):
            z = ws / "batched_images_by_zone" / zone
            z.mkdir(parents=True)
            (z / "a.jpg").write_bytes(b"j")
            (z / "flight_log_4Q_UTM.txt").write_text(
                "filename;X (East);Y (North);Alt\n", encoding="utf-8")
        (ws / "batched_images_by_zone" / "batch_inputs.json").write_text(
            "{}", encoding="utf-8")
    if upto >= 4:
        for zone, comps in (("zone_1", 2), ("zone_2", 1)):
            z = ws / "aligned_components" / zone
            z.mkdir(parents=True)
            for c in range(comps):
                name = f"{zone}_c{c}"
                (z / f"{name}.rsalign").write_bytes(b"r")
                (z / f"{name}.rsalign.manifest.json").write_text(json.dumps({
                    "schema": 1, "zone": zone, "component": name,
                    "rsalign": str(z / f"{name}.rsalign"),
                    "camera_count": 100 + c, "images": ["a.jpg"],
                    "bbox_utm": [0, 0, 10, 10]}), encoding="utf-8")
    if upto >= 5:
        m = ws / "final_assembly"
        (m / "assembly").mkdir(parents=True)
        (m / "assembly" / "Assembly.rsproj").write_bytes(b"p")
        (m / "EVALUATION_READY.txt").write_text("READY", encoding="utf-8")
        (m / "merge_report.json").write_text(json.dumps({
            "input_scales": {},
            "clusters": [{"cluster": "cluster_0", "final_components": [
                {"key": "zone_1/zone_1_c0", "camera_count": 100},
                {"key": "zone_2/zone_2_c0", "camera_count": 100}]}],
        }), encoding="utf-8")
    if upto >= 6:
        (ws / "fused_models_report.json").write_text(json.dumps({
            "components": [
                {"component": "zone_1_c0", "success": True},
                {"component": "zone_2_c0", "success": True}]}),
            encoding="utf-8")
    if upto >= 7:
        for comp in ("zone_1_c0", "zone_2_c0"):
            d = ws / "exports" / comp / "obj"
            d.mkdir(parents=True)
            (d / f"{comp}.obj").write_bytes(b"o")
    return Workspace(ws)


def make_runs(tmp_path):
    """A folder holding two Wild Sync run directories (same frames, two ids)."""
    root = tmp_path / "runs"
    root.mkdir()
    build_run(root, RUN_ID)
    build_run(root, "260820_1930_transect-02")
    return root


def run_ids(scan):
    return [run.run_id for run in scan.runs]


# -------------------------------------------------------------- detection

def test_a_single_run_directory_is_detected_by_its_structure(tmp_path):
    run = build_run(tmp_path)
    scan = scan_run_data(run)
    assert scan.problems == []
    assert [r.path for r in scan.runs] == [run]
    assert scan.runs[0].has_run_json
    assert scan.runs[0].run_id == RUN_ID


def test_a_folder_of_runs_yields_every_run_in_name_order(tmp_path):
    root = make_runs(tmp_path)
    (root / "notes").mkdir()
    (root / "notes" / "readme.txt").write_text("not a run", encoding="utf-8")
    scan = scan_run_data(root)
    assert scan.problems == []
    assert run_ids(scan) == [RUN_ID, "260820_1930_transect-02"]


def test_several_paths_and_a_run_named_twice_are_taken_once(tmp_path):
    root = make_runs(tmp_path)
    first = root / RUN_ID
    scan = scan_run_data(f"{first};{root};{first}")
    assert run_ids(scan) == [RUN_ID, "260820_1930_transect-02"]


def test_a_run_without_run_json_is_still_a_run_but_flagged(tmp_path):
    run = build_run(tmp_path)
    (run / "run.json").unlink()
    scan = scan_run_data(run)
    assert run_ids(scan) == [RUN_ID]
    assert not scan.runs[0].has_run_json
    assert "(no run.json)" in scan.summary_lines()[0]


def test_a_folder_that_is_not_a_run_is_reported_not_scanned(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    (empty / "cam1").mkdir()                # a node folder without its log
    (empty / "cam1" / "Cam1_x.jpg").write_bytes(b"j")
    scan = scan_run_data(empty)
    assert scan.runs == []
    assert len(scan.problems) == 1
    assert "neither a Wild Sync run directory" in scan.problems[0]


def test_a_missing_path_is_a_problem(tmp_path):
    scan = scan_run_data(tmp_path / "missing")
    assert scan.runs == []
    assert "not found" in scan.problems[0]


@pytest.mark.parametrize('location', ['', '   ', '\t', ' ; '])
def test_empty_run_path_does_not_scan_current_directory(tmp_path, monkeypatch, location):
    build_run(tmp_path)
    monkeypatch.chdir(tmp_path)
    assert scan_run_data(location) == RunDataScan()


def test_camera_scan_counts_each_variant_of_both_ilx_cameras(tmp_path):
    run = build_run(tmp_path)
    cameras = {c.node: c for c in scan_run_data(run).runs[0].cameras}
    assert sorted(cameras) == ["cam1", "cam2"]
    assert cameras["cam1"].camera == "ilx_left"
    assert cameras["cam2"].camera == "ilx_right"
    for cam in cameras.values():
        assert (cam.card, cam.review, cam.raw) == (len(FRAMES),) * 3
        assert cam.hidden_files == 0
    line = cameras["cam1"].summary_line()
    assert "cam1 (ilx_left)" in line
    assert "3 card, 3 review, 3 RAW" in line and "not an input" in line


def test_camera_scan_counts_differ_per_variant_and_keep_the_frame_id_dot(tmp_path):
    run = build_run(tmp_path)
    node = run / "cam1"
    (node / f"Cam1_{FRAMES[0]}.jpg").unlink()                 # one review gone
    (node / f"Cam1_{FRAMES[1]}.ARW").unlink()
    (node / f"Cam1_{FRAMES[2]}.ARW").unlink()                 # two RAW gone
    (node / "Cam1_20260820_192544.42.card.jpeg").write_bytes(b"j")  # extra card
    cam1 = next(c for c in scan_cameras(run) if c.node == "cam1")
    assert (cam1.card, cam1.review, cam1.raw) == (4, 2, 1)
    cam2 = next(c for c in scan_cameras(run) if c.node == "cam2")
    assert (cam2.card, cam2.review, cam2.raw) == (3, 3, 3)


def test_hidden_files_are_never_counted_as_frames(tmp_path):
    run = build_run(tmp_path)
    node = run / "cam2"
    twins = [f"._{p.name}" for p in node.iterdir() if p.is_file()]
    for name in twins:
        (node / name).write_bytes(b"\x00\x05\x16\x07")      # AppleDouble stub
    cam2 = next(c for c in scan_cameras(run) if c.node == "cam2")
    assert (cam2.card, cam2.review, cam2.raw) == (3, 3, 3)
    assert cam2.hidden_files == len(twins) == 13
    assert scan_run_data(run).problems == []


def test_a_node_outside_the_registry_is_named_as_unrecognised(tmp_path):
    run = build_run(tmp_path)
    shutil.copytree(run / "cam1", run / "cam9")
    cams = {c.node: c for c in scan_cameras(run)}
    assert cams["cam9"].camera == ""
    assert "matches no camera" in cams["cam9"].summary_line()


def test_resume_aware_stage_preselection(tmp_path):
    ws = make_workspace(tmp_path, stage="align")
    enabled = default_enabled(ws)
    for done in ("intake", "batch", "align"):
        assert done not in enabled, f"{done} is done - must start unticked"
    for todo in ("merge", "model", "export", "publish"):
        assert todo in enabled


def test_intake_status_comes_from_its_manifest(tmp_path):
    ws = make_workspace(tmp_path, stage="images")
    assert session_mod.stage_statuses(ws)["intake"].status == "pending"
    ws = make_workspace(tmp_path / "next", stage="intake")
    status = session_mod.stage_statuses(ws)["intake"]
    assert status.status == "done"
    assert status.summary == "4 card images from 1 run(s)"
    (ws.raw_images / "wildsync_intake.json").write_text("{", encoding="utf-8")
    assert session_mod.stage_statuses(ws)["intake"].status == "partial"


def test_the_stage_table_follows_the_module_chain(tmp_path):
    assert session_mod.ALL_STAGES == [
        "intake", "preprocess", "batch", "align",
        "merge", "model", "export", "publish"]
    assert [session_mod.MODULE_DISPLAY[k] for k in session_mod.CHAIN_STAGES] == [
        "Wild Sync Intake", "Preprocess Images", "Batch Directory",
        "RealityScan Alignment"]
    registry = session_mod._module_registry()
    assert [registry[k].name for k in session_mod.CHAIN_STAGES] == [
        session_mod.MODULE_DISPLAY[k] for k in session_mod.CHAIN_STAGES]
    assert session_mod.STAGE_TITLES["intake"] == "Wild Sync Intake"
    assert set(session_mod.STAGE_TITLES) == set(session_mod.ALL_STAGES)


# -------------------------------------------------------------- questions

def _session(tmp_path, enabled, runs=None) -> Session:
    s = Session(run_dirs=str(runs) if runs else "",
                results_root=str(tmp_path / "results"))
    s.enabled = enabled
    return s


def test_questions_follow_module_order_and_use_descriptions(tmp_path):
    s = _session(tmp_path, ["intake", "preprocess", "batch", "align"],
                 build_run(tmp_path))
    qs = build_questions(s)
    stages = [q.stage for q in qs]
    assert stages == sorted(stages, key=session_mod.CHAIN_STAGES.index), (
        "questions must arrive in module order (RC_Main)")
    assert stages[0] == "intake"
    runs = next(q for q in qs if q.arg == "w_input")
    assert runs.required and runs.kind == "rundirs"
    assert "Wild Sync run directory" in runs.prompt, (
        "the prompt is the parameter's own description")
    asked = {q.arg for q in qs}
    assert {"w_input", "w_variant", "w_calibration", "w_declination"} <= asked
    assert not asked & {"p_input", "b_input", "r_input"}, (
        "an enabled intake hands the downstream stages their input")


def test_the_run_directory_prefills_the_intake_question(tmp_path):
    run = build_run(tmp_path)
    s = _session(tmp_path, ["intake"], run)
    qs = build_questions(s)
    assert next(q for q in qs if q.arg == "w_input").default == str(run)


def test_intake_questions_validate_their_answers(tmp_path):
    run = build_run(tmp_path)
    qs = {q.arg: q for q in build_questions(_session(tmp_path, ["intake"], run))}
    assert qs["w_input"].validate(str(run)) is None
    assert qs["w_input"].validate(f"{run};{tmp_path / 'missing'}") is not None
    not_a_run = tmp_path / "not_a_run"
    not_a_run.mkdir()
    assert qs["w_input"].validate(str(not_a_run)) is not None
    assert qs["w_input"].validate("") == "this one is required"
    assert qs["w_variant"].choices == ("card", "review")
    assert qs["w_variant"].default == "card"
    assert qs["w_variant"].validate("REVIEW") is None
    assert "card, review" in qs["w_variant"].validate("raw")
    assert qs["w_calibration"].choices == ("auto", "prior", "groups", "off")
    assert qs["w_calibration"].default == "auto"
    assert qs["w_calibration"].validate("bogus") is not None
    assert qs["w_declination"].validate("-14.5") is None
    assert qs["w_declination"].validate("east") is not None


def test_stages_after_intake_prefill_from_the_workspace(tmp_path):
    results = tmp_path / "results"
    (results / "raw_images").mkdir(parents=True)
    s = _session(tmp_path, ["preprocess"])
    qs = build_questions(s)
    assert next(q for q in qs if q.arg == "p_input").default == str(
        results / "raw_images")
    assert not [q for q in qs if q.arg.startswith("w_")]


def test_disable_when_module_active_suppresses_redundant_questions(tmp_path):
    """RC_Main semantics: an enabled upstream module answers for you."""
    with_batch = _session(tmp_path, ["batch", "align"])
    qs = {q.arg for q in build_questions(with_batch)}
    assert "r_input" not in qs, "Batch Directory hands alignment its input"
    assert "r_flight_log" not in qs

    align_alone = _session(tmp_path, ["align"])
    qs = {q.arg for q in build_questions(align_alone)}
    assert "r_input" in qs, "without batch, alignment must ask"


def test_last_run_answers_are_the_new_defaults(tmp_path, store):
    s = _session(tmp_path, ["batch"])
    s.run_dirs = str(tmp_path / "runs")
    s.answers["b_target_images"] = "2500"
    session_mod.save_last_run(s)
    reloaded = session_mod.default_session()
    assert reloaded.run_dirs == str(tmp_path / "runs")
    assert reloaded.results_root == str(tmp_path / "results")
    assert reloaded.answers.get("b_target_images") == "2500"
    qs = build_questions(_session_with_answers(tmp_path, reloaded.answers))
    target = next(q for q in qs if q.arg == "b_target_images")
    assert target.default == "2500", "the last run must prefill the next"


def _session_with_answers(tmp_path, answers) -> Session:
    s = _session(tmp_path, ["batch"])
    s.answers = dict(answers)
    return s


# --------------------------------------------------------------- commands

def test_chain_runs_as_one_invocation_preserving_handoff(tmp_path):
    run = build_run(tmp_path)
    s = _session(tmp_path, ["intake", "preprocess", "batch", "align"], run)
    s.answers = {"w_input": str(run), "b_flight_log_path": "D:/nav.txt"}
    commands = build_commands(s)
    chain = commands[0]
    assert chain.env["RS_MODULES"] == (
        "Wild Sync Intake,Preprocess Images,Batch Directory,"
        "RealityScan Alignment"), (
        "chained modules MUST share one main.py process - the in-process "
        "hand-off is the pipeline's current data handling")
    argv = " ".join(chain.argv)
    assert chain.argv[chain.argv.index('--w_input') + 1] == str(run)
    assert "--r_display_output false" in argv, "console display forced off"
    assert chain.stages == ("intake", "preprocess", "batch", "align")
    assert chain.stage == ("Wild Sync Intake + Preprocess Images + "
                           "Batch Directory + RealityScan Alignment")
    assert chain.needs_realityscan


def test_intake_command_passes_the_modules_own_flags(tmp_path, monkeypatch):
    runs = make_runs(tmp_path)
    monkeypatch.chdir(tmp_path)
    s = Session(run_dirs="runs", results_root="results", enabled=["intake"])
    s.answers = {"w_input": f"runs/{RUN_ID};runs/260820_1930_transect-02",
                 "w_variant": "review", "w_calibration": "groups",
                 "w_declination": "-14.5", "w_assert_focal": "true",
                 "w_heading_source": "yaw", "w_min_match_rate": "90",
                 "p_input": "ignored"}
    command = build_commands(s)[0]
    results = tmp_path / "results"
    flags = {
        "--output_dir": str(results),
        "--continue_automatically": "true",
        "--w_input": f"{runs / RUN_ID};{runs / '260820_1930_transect-02'}",
        "--w_variant": "review", "--w_calibration": "groups",
        "--w_declination": "-14.5", "--w_assert_focal": "true",
        "--w_heading_source": "yaw", "--w_min_match_rate": "90",
    }
    assert command.argv[0] == sys.executable
    assert command.argv[1].endswith("main.py")
    assert dict(zip(command.argv[2::2], command.argv[3::2])) == flags
    assert command.env["RS_MODULES"] == "Wild Sync Intake"
    assert not command.needs_realityscan
    assert command.stages == ("intake",)
    assert command.workspace == str(results)
    assert s.run_dirs == str(runs), "the session's run directory is anchored"


def test_intake_flags_match_the_module_parameters():
    module = session_mod._module_registry()["intake"]
    flags = {p.cli_long: p.cli_short for p in module.get_parameters().values()}
    assert flags["w_input"] == "w_i" and flags["w_variant"] == "w_v"
    assert flags["w_calibration"] == "w_cal" and flags["w_declination"] == "w_d"
    assert {"w_input", "w_variant", "w_calibration", "w_declination"} <= (
        session_mod.chain_arg_names(["intake"]))


def test_post_stages_are_separate_gated_commands(tmp_path):
    s = _session(tmp_path, ["merge", "model", "export", "publish"])
    commands = build_commands(s)
    assert [c.stage for c in commands] == [
        "Merge Components", "Generate Models", "Export Deliverables",
        "Publish (Cesium / Nira)"]
    merge = " ".join(commands[0].argv)
    assert "--pair_gate overlap" in merge
    assert "--loss_tolerance 0.0025" in merge
    assert "--scale_gate true" in merge
    assert "--name results_Assembly" in merge


def test_export_runs_through_the_python_driver(tmp_path):
    """Hard rule 1: every RealityScan launch goes through RealityScanCLI.
    The export stage used to Popen the .bat via raw ["cmd","/c",...] - no
    lock, no marker hygiene, no verified shutdown, and the booted GUI
    inherited the runner's stdout pipe (WINDOWS TRAP 2026-08-07)."""
    ws = make_workspace(tmp_path, stage="model")
    s = _session(tmp_path, ["export"])
    s.results_root = str(ws.root)
    export = build_commands(s)[0]
    assert export.stage == "Export Deliverables"
    assert export.needs_realityscan
    assert "cmd" not in export.argv, "raw cmd /c launches are banned"
    assert export.argv[0] == sys.executable
    assert export.argv[1].endswith("export_deliverables.py")
    joined = " ".join(export.argv)
    assert "--project" in joined
    assert str(ws.assembly_project()) in export.argv
    assert str(ws.exports) in export.argv
    assert str(ws.exports / "components.names") in export.argv
    assert str(ws.root / "logs") in export.argv, (
        "driver logs belong in the workspace logs/ dir like every stage")
    assert export.env.get("PYTHONIOENCODING") == "utf-8"


def test_export_driver_goes_through_the_execution_layer(tmp_path, monkeypatch):
    """run_export must delegate to RealityScanCLI.run_batch_script with the
    .bat's own three-argument contract - never spawn cmd itself."""
    from modules import export_deliverables as ed

    calls = {}

    class FakeCLI:
        def __init__(self, logger, settings=None, instance_name=None):
            pass

        def run_batch_script(self, script_name, args, log_dir, **kwargs):
            calls["script"] = script_name
            calls["args"] = list(args)
            calls["log_dir"] = log_dir

            class R:
                success = True
            return R()

    monkeypatch.setattr(ed, "RealityScanCLI", FakeCLI)
    # Keep the test hermetic: run_export overlays realityscan_env onto
    # os.environ (env wins in production); a real overlay would leak
    # RS_* into every later test in this process.
    monkeypatch.setattr(ed, "realityscan_env", lambda store: {})

    project = tmp_path / "Assembly.rsproj"
    project.write_bytes(b"p")
    exports = tmp_path / "exports"
    exports.mkdir()
    names = exports / "components.names"
    names.write_text("zone_1_c0\n", encoding="utf-8")

    res = ed.run_export(str(project), str(exports), str(names),
                        settings=FakeStore())
    assert res.success
    assert calls["script"] == "ExportDeliverables.bat"
    assert calls["args"] == [str(project), str(exports), str(names)], (
        "argument order is the .bat contract: project, out dir, name list")
    assert calls["log_dir"] == str(tmp_path / "logs"), (
        "default log dir is <exports parent>/logs - the workspace logs/")


def test_publish_defaults_to_dry_run_without_credentials(tmp_path, monkeypatch):
    monkeypatch.delenv("CESIUM_ION_TOKEN", raising=False)
    monkeypatch.delenv("NIRACLIENT_DIR", raising=False)
    s = _session(tmp_path, ["publish"])
    publish = build_commands(s)[0]
    assert "--dry-run" in publish.argv


# ---------------------------------------------------------------- census

def test_empty_workspace_is_all_pending(tmp_path):
    ws = Workspace(tmp_path / "nowhere")
    assert all(s.status == "pending" for s in ws.detect().values())


def test_batch_without_fingerprint_is_partial(tmp_path):
    ws = make_workspace(tmp_path, stage="batch")
    (ws.batched / "batch_inputs.json").unlink()
    assert ws.detect()["batch"].status == "partial", (
        "unknown provenance must never read as done - the "
        "blended-zoning case (more images on disk than reported) is "
        "what this glyph exists for")


def test_merge_without_gate_is_partial(tmp_path):
    ws = make_workspace(tmp_path, stage="merge")
    (ws.latest_merge() / "EVALUATION_READY.txt").unlink()
    assert ws.detect()["merge"].status == "partial"


def test_components_join_scale_models_exports(tmp_path):
    ws = make_workspace(tmp_path, stage="export")
    comps = {c.key: c for c in ws.components()}
    assert not comps["zone_1_c0"].modelled, 'legacy success alone does not verify the current scene'
    assert comps["zone_1_c0"].exported == ["obj"]


# ------------------------------------------------------------- app smoke

def test_portal_walks_session_to_stage_pick(tmp_path, store):
    from wildscan.app import StagePickScreen, WildScanApp

    run = build_run(tmp_path)
    results = tmp_path / "results"

    async def drive():
        app = WildScanApp()
        async with app.run_test(size=(120, 50)) as pilot:
            await pilot.pause()
            screen = app.screen
            screen.query_one("#s-runs").value = str(run)
            screen.query_one("#s-results").value = str(results)
            await pilot.pause(0.5)
            preview = str(screen.query_one("#s-detect").content)
            assert f"run {RUN_ID}" in preview
            assert "cam1 (ilx_left): 3 card, 3 review, 3 RAW" in preview
            assert "cam2 (ilx_right): 3 card, 3 review, 3 RAW" in preview
            await pilot.click("#s-continue")
            await pilot.pause()
            assert isinstance(app.screen, StagePickScreen)
            picker = app.screen.query_one("#stage-pick")
            # One option per stage: intake, preprocess, batch, align, merge,
            # model, export, publish.
            assert picker.option_count == len(session_mod.ALL_STAGES) == 8
            assert picker.get_option_at_index(0).value == "intake"
            assert "Wild Sync Intake" in str(picker.get_option_at_index(0).prompt)
            assert "intake" in picker.selected
            assert results.is_dir(), "the results root must be auto-created"
    asyncio.run(drive())


def test_run_persists_anchored_source_paths_before_launch(tmp_path, store, monkeypatch):
    import wildscan.app as app_mod
    monkeypatch.chdir(tmp_path)
    launches = []

    class FakeRunner:
        running = False

        def __init__(self, screen):
            pass

        def start(self, command):
            launches.append(command)

    monkeypatch.setattr(app_mod, 'CommandRunner', FakeRunner)

    async def drive():
        app = app_mod.WildScanApp()
        async with app.run_test(size=(120, 50)) as pilot:
            await pilot.pause()
            app.session.results_root = 'results'
            app.session.run_dirs = 'runs'
            app.session.enabled = ['intake']
            app.session.answers = {'w_input': 'runs;more/runs',
                                   'r_input': 'zones', 'r_flight_log': 'nav.csv'}
            app.push_screen(app_mod.RunScreen())
            await pilot.pause()
            assert len(launches) == 1
            assert store.data['wildscan']['run_dirs'] == str(tmp_path / 'runs')
            assert store.data['wildscan']['answers']['w_input'] == (
                str(tmp_path / 'runs') + ';' + str(tmp_path / 'more' / 'runs'))
            assert store.data['wildscan']['answers']['r_input'] == str(tmp_path / 'zones')
            assert store.data['wildscan']['answers']['r_flight_log'] == str(tmp_path / 'nav.csv')
            assert store.data['wildscan']['results_root'] == str(tmp_path / 'results')
    asyncio.run(drive())


@pytest.mark.parametrize("launch_failure", [True, False])
def test_failed_console_stage_retries_current_command(tmp_path, store, monkeypatch,
                                                      launch_failure):
    import wildscan.app as app_mod
    from wildscan.runner import RunFinished
    from wildscan.session import StageCommand

    commands = [StageCommand("first", ["first.py"], {}),
                StageCommand("second", ["second.py"], {})]
    calls = []

    class FakeRunner:
        running = False

        def __init__(self, screen):
            pass

        def start(self, command):
            calls.append(command)
            if launch_failure and len(calls) == 1:
                raise OSError("unavailable executable")

    monkeypatch.setattr(app_mod, "CommandRunner", FakeRunner)
    monkeypatch.setattr(app_mod, "build_commands", lambda session: commands)

    async def drive():
        app = app_mod.WildScanApp(str(tmp_path))
        async with app.run_test(size=(120, 50)) as pilot:
            await pilot.pause()
            screen = app_mod.RunScreen()
            app.push_screen(screen)
            await pilot.pause()
            if not launch_failure:
                screen.on_run_finished(RunFinished(1, cancelled=True))
            assert screen.waiting_gate
            assert not screen.query_one("#r-continue").disabled
            screen.action_gate()
            assert calls == [commands[0], commands[0]]
            assert screen.current == 0
    asyncio.run(drive())


@pytest.mark.parametrize("stages", [None, 1, "model", {}])
def test_resume_ignores_malformed_interruption_stages(tmp_path, stages):
    (tmp_path / "interrupted_stage.json").write_text(
        json.dumps({"stages": stages}), encoding="utf-8")
    assert "model" in default_enabled(Workspace(tmp_path))


def test_intake_detection_debounces_and_scopes_the_run_scan(tmp_path, store, monkeypatch):
    import wildscan.app as app_mod
    calls = []

    def runs(path):
        calls.append(path)
        return RunDataScan()

    monkeypatch.setattr(app_mod, 'scan_run_data', runs)

    async def drive():
        app = app_mod.WildScanApp()
        async with app.run_test(size=(120, 50)) as pilot:
            await pilot.pause()
            screen = app.screen
            screen.query_one('#s-runs').value = str(tmp_path / 'partial')
            screen.query_one('#s-runs').value = str(tmp_path / 'runs')
            await pilot.pause(0.5)
            assert calls == [str(tmp_path / 'runs')]
            screen.query_one('#s-results').value = str(tmp_path / 'results')
            await pilot.pause(0.5)
            assert calls == [str(tmp_path / 'runs')], (
                'changing the workspace must not rescan the run directories')
            screen.query_one('#s-runs').value = str(tmp_path / 'partial')
            screen.query_one('#s-runs').value = str(tmp_path / 'other_runs')
            await pilot.pause(0.5)
            assert calls == [str(tmp_path / 'runs'), str(tmp_path / 'other_runs')]
            screen.query_one('#s-runs').value = ''
            await pilot.pause(0.5)
            assert app.scan == RunDataScan()
            assert len(calls) == 2
    asyncio.run(drive())


def test_run_preview_is_refreshed_when_continuing(tmp_path, store):
    import wildscan.app as app_mod
    run = build_run(tmp_path)

    async def drive():
        app = app_mod.WildScanApp()
        async with app.run_test(size=(120, 50)) as pilot:
            await pilot.pause()
            screen = app.screen
            screen.query_one('#s-runs').value = str(run)
            screen.query_one('#s-results').value = str(tmp_path / 'results')
            await pilot.pause(0.5)
            assert app.scan.runs[0].cameras[0].card == 3
            (run / 'cam1' / 'Cam1_20260820_192544.42.card.JPG').write_bytes(b'j')
            screen.query_one('#s-continue').press()
            await pilot.pause()
            assert isinstance(app.screen, app_mod.StagePickScreen)
            assert app.scan.runs[0].cameras[0].card == 4
    asyncio.run(drive())


def test_stage_picker_reuses_only_the_current_transition_census(tmp_path, store, monkeypatch):
    import wildscan.app as app_mod
    from modules.workspace_census import StageStatus
    calls = []

    def detect(workspace):
        calls.append(workspace.root)
        statuses = {key: StageStatus(key) for key in session_mod.ALL_STAGES}
        if len(calls) == 1:
            statuses['align'].status = 'done'
        return statuses

    monkeypatch.setattr(Workspace, 'detect', detect)

    async def picker_after_mount(app, pilot):
        # The picker applies the session's selection in on_mount, which runs
        # a message cycle after the screen is pushed; wait for it rather
        # than assert against a not-yet-populated widget.
        for _ in range(50):
            await pilot.pause()
            if isinstance(app.screen, app_mod.StagePickScreen):
                picker = app.screen.query_one('#stage-pick')
                if len(picker.selected) == len(app.session.enabled):
                    return picker
        raise AssertionError('stage picker never reflected the session selection')

    async def drive():
        app = app_mod.WildScanApp()
        async with app.run_test(size=(120, 50)) as pilot:
            await pilot.pause()
            app.screen.query_one('#s-results').value = str(tmp_path / 'results')
            app.screen.query_one('#s-continue').press()
            picker = await picker_after_mount(app, pilot)
            assert len(calls) == 1
            assert 'align' not in app.session.enabled
            assert 'align' not in picker.selected
            app.screen.action_back()
            await pilot.pause()
            app.screen.query_one('#s-continue').press()
            picker = await picker_after_mount(app, pilot)
            assert len(calls) == 2
            assert 'align' in app.session.enabled
            assert 'align' in picker.selected
    asyncio.run(drive())


@pytest.mark.parametrize('arguments, exitcode, output', [
    (['--help'], 0, 'usage: wildscan'),
    (['--version'], 0, 'WildScan'),
    (['--unknown'], 2, 'unrecognized arguments'),
    (['first', 'second'], 2, 'unrecognized arguments'),
])
def test_console_metadata_and_invalid_args_do_not_open_ui_or_settings(
        tmp_path, monkeypatch, capsys, arguments, exitcode, output):
    import wildscan.app as app_mod

    def forbidden(*args, **kwargs):
        pytest.fail('Argument-only invocation reached the UI or settings')

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, 'argv', ['wildscan', *arguments])
    monkeypatch.setattr(app_mod, 'WildScanApp', forbidden)
    monkeypatch.setattr(session_mod, '_settings', forbidden)
    with pytest.raises(SystemExit) as stopped:
        app_mod.main()
    assert stopped.value.code == exitcode
    captured = capsys.readouterr()
    assert output in (captured.out if exitcode == 0 else captured.err)
    if arguments == ['--version']:
        assert captured.out.strip() == f'{app_mod.APP_NAME} {app_mod.__version__}'
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize('workspace', [None, 'C:/surveys/my workspace'])
def test_console_workspace_argument_is_forwarded_to_normal_app_run(
        monkeypatch, workspace):
    from unittest.mock import Mock

    import wildscan.app as app_mod
    app = Mock()
    constructor = Mock(return_value=app)
    monkeypatch.setattr(app_mod, 'WildScanApp', constructor)
    monkeypatch.setattr(sys, 'argv', ['wildscan'] + ([] if workspace is None else [workspace]))
    assert app_mod.main() == 0
    constructor.assert_called_once_with(workspace)
    app.run.assert_called_once_with()


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
