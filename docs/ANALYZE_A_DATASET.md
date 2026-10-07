# Analyze a dataset

This guide takes a Wild Sync recording of the ILX-LR1 stereo rig from the run
directory to a processing plan in the Wild Scan application, and explains how
to read the status of an existing workspace without starting a run. Install
the checkout first with [Setup and run](SETUP-AND-RUN.md).

## Before opening Wild Scan

| Input | Check |
|---|---|
| Wild Sync run directory | `cam1/` and `cam2/`, each with its `flight_log.csv` and the images; the card JPEGs copied off the cameras if you process the card variant. Several runs can be processed together when they lie in one UTM zone and their image names do not repeat |
| Navigation | the `flight_log.csv` header is exactly the 23 Wild Sync columns; note whether the run carries live navigation or one static fix, and whether depth is recorded |
| Workspace | a new folder on a large local drive, outside the run directory, with room for the copied, preprocessed and batched images, the projects, the exports and the RealityScan cache |
| RealityScan | the 13-column flight-log format installed ([Setup and run, section 5.4](SETUP-AND-RUN.md#54-flight-log-import-format)) |

The input format and the rules the intake applies are in
[The ILX-LR1 stereo rig](ILX-LR1.md#wild-sync-input). On a first installation,
start with one short run and the
[first-run validation checklist](validation/ILX-LR1_first_run_checklist.md):
no live RealityScan alignment has yet been run with this version.

## Open your dataset

From the checkout in PowerShell:

```powershell
& ".\.venv\Scripts\python.exe" -m wildscan
```

To open an existing workspace directly, pass its path:

```powershell
& ".\.venv\Scripts\python.exe" -m wildscan "D:\workspace\transect-01"
```

The first screen, **Choose your dataset**, has two fields. Both remember the
previous session's values.

| Field | What to enter |
|---|---|
| Wild Sync run directory or folder of runs | a run directory, or a folder whose subfolders are run directories; separate several paths with `;`. It is only read, never modified |
| Workspace | the results folder for this dataset; created if missing |

As you type, the screen lists what it finds: each run (marked `no run.json`
if that file is absent) and, for each camera node, the camera it belongs to and
its image counts, in this form:

```text
run 260820_1925_transect-01
  cam1 (ilx_left): <n> card, <n> review, <n> RAW (RAW is not an input)
  cam2 (ilx_right): <n> card, <n> review, <n> RAW (RAW is not an input)
```

A node whose name matches no camera in `modules/cameras.json` is flagged. The
counts are file counts by name; they do not show that the images decode or
that every frame has a flight-log row. The intake checks that.

Choose **View results** to see the status of an existing workspace without
processing; **Escape** returns. Choose **Continue** to plan a run: it checks the
run directory, creates the workspace if needed, and opens the stage list. It
does not start processing.

## Choose the work to run

The stage list shows each stage with its detected state and summary. Use the
arrow keys to move, **Space** to select or unselect, and **Enter** to confirm.
Stages already complete start unselected.

| Stage | What it does |
|---|---|
| Wild Sync Intake | copies the chosen image variant of both cameras into `raw_images/`, writes one 13-column flight log with position and orientation priors, decides the calibration delivery per camera, and writes `raw_images/wildsync_intake.json` |
| Preprocess (CLAHE) | contrast-enhanced copies of the images in `preprocessed_images/`, same names |
| Batch into Zones | spatial zones with per-camera folders and their own flight log in `batched_images_by_zone/` |
| Align Zones | RealityScan alignment of each zone, with the decided calibration sidecars; components and membership manifests in `aligned_components/` |
| Merge Components | merges the per-zone components into one assembly in `merged/` |
| Generate Models | checks each final component's metric scale and builds a textured model for the accepted ones |
| Export Deliverables | OBJ and FBX by parts and a dense coloured PLY per component in `exports/` |
| Publish (Cesium / Nira) | uploads the exports to the configured services, or writes a preview plan when none is configured |

A selected later stage does not create missing earlier results: select every
stage whose output does not exist yet.

## Answer the questions

The application then asks one question at a time, using each parameter's own
description as the prompt. Defaults come from the current workspace and run
directory, then from the previous session, then from the module. For the full
chain the questions are:

| Stage | Question | Default |
|---|---|---|
| Wild Sync Intake | Wild Sync run directory or folder of runs | the run directory from the first screen |
| Wild Sync Intake | Image variant: `card` or `review` | `card` |
| Wild Sync Intake | Calibration mode: `auto`, `prior`, `groups` or `off` | `auto` |
| Wild Sync Intake | Magnetic declination in degrees, east positive | `0` |
| Batch into Zones | Target number of images per zone | `3000` |
| Batch into Zones | Initial percent of overlap between batches | `20` |
| Align Zones | Project label for dated project copies; empty disables them | empty |

Leave the calibration mode at `auto` unless you have a reason: it applies the
stored calibration only when the images match it and otherwise gives each
camera its own calibration group. Enter a declination only when you know the
heading in `flight_log.csv` is magnetic. The other intake settings (accuracies,
heading source, match-rate floor) keep their defaults in the application; they
can be set on the command line ([Setup and run, section 6.3](SETUP-AND-RUN.md#63-the-stages-on-the-command-line)).

When the intake is not selected (for example when resuming at alignment), the
later stages ask for their input folders instead, prefilled from the
workspace.

## Review and run

**Review your run** lists the run directories, the workspace, the selected
stages and every answer. When Publish is selected it states whether uploads
will happen (credentials are set) or only a preview plan will be written.

Leave **Continue automatically between stages?** at `false` for a first run.
Intake through alignment run as one command; the application pauses before
Merge, Models, Export and Publish, each its own command. Choose **Run**.

The run screen streams the logs and the progress of the current RealityScan
operation. **Continue** (or **Enter**) starts the next command at a pause, or
retries a command that failed or was stopped. **Stop stage** cancels the
running command and records the interrupted stage for a retry. **Edit plan**
returns to the plan when no command is running; **Escape** opens the status.

After the intake, read its warnings in the log or in
`raw_images/wildsync_intake.json`. For the August 2026 field runs, expect a
static-fix warning (every row carries the same position, so no position is
written for the images), an empty-depth warning (no altitude is written) and
`calibration groups only` for both cameras.

Alignment writes calibration sidecars beside the images it aligns. A sidecar
already there that the pipeline did not write is first moved to
`aligned_components/<zone>/pre_existing_sidecars/`; pose sidecars RealityScan
exports during the run are harvested out of that tree. It works on the
workspace copies, never on the run directory.

## Interpret the results

The status screen has two tables. **Pipeline** shows each stage's state:

| State | Meaning |
|---|---|
| `pending` | the stage's expected results are absent |
| `partial` | some results exist but the completion checks are not satisfied; changed inputs or failed work may need a retry |
| `blocked` | a prerequisite is missing or the recorded results prevent proceeding |
| `done` | the stage's results and reports pass their checks |

For Wild Sync Intake, `done` means a complete manifest exists; its summary
gives the number of images, the variant and the number of runs.

**Final components** lists each component of the merged assembly with its
camera count, measured metric scale and verdict, model state and exported
formats; a dash means the value is not available. A scale verdict does not
measure absolute position, surface detail, seams or texture quality; review
those before delivering a model. Status is read from the saved reports and
files; it does not rerun RealityScan.

The workspace layout and the reports each stage writes are listed in
[Setup and run, section 8](SETUP-AND-RUN.md#8-where-everything-lands).
