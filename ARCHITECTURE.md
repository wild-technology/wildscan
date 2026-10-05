# Wild Scan architecture

Wild Scan drives **RealityScan 2.2** (Epic Games; formerly RealityCapture)
through its command line to reconstruct imagery from one rig: two Sony
ILX-LR1 cameras in a stereo pair, recorded with Wild Sync. It runs on native
Windows with one or more CUDA GPUs. The rig and the conventions applied to it
are described in [The ILX-LR1 stereo rig](docs/ILX-LR1.md); installation and
command lines in [Setup and run](docs/SETUP-AND-RUN.md).

## Data flow

```text
Wild Sync run directory (read only)
  cam1/ flight_log.csv + images      cam2/ flight_log.csv + images
        |
        v  Wild Sync Intake                         modules/wildsync_intake/
<workspace>/raw_images/
  ilx_left/  ilx_right/              copied images, unchanged
  flight_log_<zone><band>_UTM.txt    13-column priors for both cameras
  wildsync_intake.json               manifest, incl. calibration mode per camera
        |
        v  Preprocess Images                        modules/preprocess_images/
<workspace>/preprocessed_images/     CLAHE copies, same names, no EXIF
        |
        v  Batch Directory                          modules/image_batcher/
<workspace>/batched_images_by_zone/zone_<n>/
  ilx_left/  ilx_right/  flight_log_<zone><band>_UTM.txt
        |
        v  RealityScan Alignment                    modules/realityscan_interface/
<workspace>/aligned_components/zone_<n>/
  zone_<n>.rsproj  zone_<n>_c<k>.rsalign + manifests  zone_<n>.rscmd
        |
        v  merge_zones.py -> run_models.py -> export_deliverables.py -> publish_batch.py
<workspace>/merged/   <workspace>/exports/
```

The first four stages are `RSModule` subclasses chained by `main.py` in one
process; a module that reports `Success: False` stops the chain with exit
code 1. The later stages are separate drivers. The application in `wildscan/`
runs the chain as one command and each later stage as its own command, with a
gate between them.

The calibration decision is made once, at intake, from the original images,
because preprocessing writes images without EXIF. RealityScan Alignment reads
it from `raw_images/wildsync_intake.json`; it never recomputes it.

## Getting started as a developer

```powershell
py -3.13 -m venv .venv
& ".\.venv\Scripts\python.exe" -m pip install --upgrade "pip>=26.2"
& ".\.venv\Scripts\python.exe" -m pip install -e ".[dev]"
& ".\.venv\Scripts\python.exe" -m pytest
```

The offline suite needs no RealityScan. Some path, process and NTFS checks
require Windows. Passing tests do not establish native RealityScan behaviour.
`modules/camera_registry.py` and `modules/calibration_sidecars.py` import with
the standard library alone (the latter needs Pillow only to read images).

For any RealityScan command-line question, start at
[docs/rs-reference/README.md](docs/rs-reference/README.md).

## Components

**Entry points**

- `main.py`: the module chain (Wild Sync Intake, Preprocess Images, Batch
  Directory, RealityScan Alignment). `RS_MODULES` selects modules by name and
  `RS_NO_INTERACTIVE=1` disables prompts. Its argument parser is built from the
  enabled modules only.
- `wildscan/`: the Textual application. `session.py` holds run-directory
  detection, the camera scan, the question list and command assembly;
  `app.py` the screens; `runner.py` process supervision.
- `merge_zones.py`: component merge driver; writes `merge_report.json`.
- `grow_zone.py`: within-zone component growth driver; writes `grow_report.json`.
- `run_models.py`: per-component model generation, scale-gated; writes
  `models_report.json`.
- `modules/export_deliverables.py`: the export stage driver.
- `publish_batch.py`, `publish_cesium.py`, `publish_nira.py`: publishers.
- `finish_model.py`: finishes an existing model in a running instance
  (`ModelToFinal.bat`).
- `poses_to_flight_log.py`, `decimate_images.py`: data utilities; they do not
  run RealityScan.

**Rig and inputs**

- `modules/cameras.json` and `modules/camera_registry.py`: the single source
  for the two cameras (`ilx_left`, `ilx_right`), their calibration, the Wild
  Sync file name families (`Cam1_`, `Cam2_`; node `cam1` is the left eye,
  `cam2` the right), each camera's mount, the rig `ilx_lr1_stereo` with its
  `node_assignment_confirmed` switch, and the default prior accuracies. The
  registry is validated at import; a malformed entry stops the import with a
  message naming it. `calibration/ilx_lr1_stereo_dive_001.json` is the
  calibration record; a test keeps the two files in agreement.
- `modules/wildsync_intake/`: `intake.py` does the work (strict
  `flight_log.csv` reader, frame matching, copying, priors, gates, manifest);
  `wildsync_intake.py` is the `RSModule` with its parameters.
- `modules/calibration_sidecars.py`: the per-camera decision between `prior`,
  `groups` and `off`; the `xcr` 1.1 sidecar text for each mode; the `.rscmd`
  builder; and the sidecar hygiene (`sanitize_and_census`,
  `ensure_calibration_sidecars`), which only ever writes the decided form.
- `modules/flight_logs.py`: the 13-column flight-log writer, flight-log
  discovery (`find_flight_log`, the only way a stage locates a log), the UTM
  zone tag in the file name, the regenerated `FlightLogParams` XML
  (`write_flight_log_params`), and the orientation convention
  (`realityscan_orientation`).

**Processing modules**

- `modules/preprocess_images/`: CLAHE and gray-world white balance; the
  canonical transforms, also used by `scripts/validation/check_preprocessing.py`.
- `modules/image_batcher/batch_directory.py`: spatial zoning. A dataset with
  fewer images than `--b_single_zone_below` (default 4000) is one zone with no
  clustering or overlap copies; fewer than 100 images logs a warning. The default copy
  layout puts each zone's images in per-camera subfolders with a copy of the
  zone's flight log; the pool layout (`--b_zone_layout pool`) writes an
  `.imagelist` instead and cannot be combined with calibration delivery.
- `modules/scale_oracle.py`: metric-scale measurement and the 0.90 to 1.10
  acceptance band used by the merge and model stages.
- `modules/component_analysis.py`, `modules/component_manifest.py`: component
  census, membership and borders.
- `modules/workspace_census.py`: what a workspace holds; the application's
  status screen is built on it.
- `modules/align_fingerprint.py`, `modules/publish_fingerprint.py`: input
  fingerprints for safe retries and resumes.
- `modules/cesium_placement.py`: placement of a mesh on the WGS84 globe from
  its `.rsInfo`, with the sea-surface depth converted to an ellipsoidal height
  through EGM2008.
- `module_base/settings_store.py`: `rs_settings.json`, the stored answers and
  the `realityscan` machine settings.

**RealityScan execution**, in `modules/realityscan_interface/` only:

- `realityscan_cli.py` (`RealityScanCLI`): executable discovery, one lock file
  per instance, marker-file hygiene, progress tailing, stall warnings
  (`STALL_WARNING_SECONDS`), and verified shutdown.
- `realityscan_interface.py`: the RealityScan Alignment stage. Per zone it
  writes the calibration sidecars and the `.rscmd` when the intake decided
  `prior` or `groups` (first moving any sidecar in their place that the
  pipeline did not write to `<zone output>/pre_existing_sidecars/`), regenerates the flight-log parameter XML for the log's
  UTM zone, runs `AlignZone.bat`, restores the decided sidecars and builds the
  component manifests.
- `RS_CLI/Scripts/*.bat`: the workflows. Every operation runs through the
  shared `:run` subroutine: `-delegateTo %RS_INSTANCE%`, a grace delay, two
  `-waitCompleted` calls with a second grace between them, and an abort if
  `RS_CLI/Errors/errors_<instance>.txt` is non-empty.
  - `AlignZone.bat`: aligns one zone as one scene with the settings of
    `Metadata/AlignmentParams.xml` applied by `-set`, imports the flight log,
    saves the scene, then runs the in-session identity loop: each lap exports
    the poses of all remaining components (`identity_r<k>`), and exports and
    deletes the largest one as `<zone>_c<k>`; membership is the difference
    between successive laps. It quits without saving after the loop and
    builds no model. Optional seventh argument: an `.rscmd` file, executed
    with `-execRSCMD` instead of `-addFolder`.
  - `MergeZoneComponents.bat`, `GrowZone.bat`, `GenerateModel.bat`,
    `ExportDeliverables.bat`, `SaveProjectCopy.bat`, `FlushCache.bat`.
  - `startRealityScan.bat`, `SetVariables.bat`: instance start and paths.
  - `ModelToFinal.bat` attaches to a running instance instead of starting one
    and never creates a scene; it accepts `*` as the instance.
- `RS_CLI/Errors/ErrorWriter.bat` and `ErrorWriterLaunch.vbs`: the completion
  hook RealityScan calls (`appProcessAction=ExecuteProgram`); completions go to
  `results_<instance>.log`, failures to `errors_<instance>.txt`.
- `RS_CLI/Metadata/*.xml`: parameter files, documented in
  [docs/rs-reference/09-xml-parameter-files.md](docs/rs-reference/09-xml-parameter-files.md).
  `AlignmentParams.xml` sets `sfmDistortionModel=Brown3`, which is global for
  all cameras.

## RealityScan facts the code depends on

Details and evidence are in [docs/rs-reference/](docs/rs-reference/README.md).

- Delegated commands (`-delegateTo <instance> <cmd>`) are queued; the
  delegating process returns at hand-over, not at completion.
- `-waitCompleted <instance>` can return before the instance picks up a queued
  command, hence the double wait in `:run`.
- `-align` takes no parameters in 2.x; alignment settings must be applied with
  `-set` first.
- RealityScan reports success while doing nothing in several cases (merges
  that do not fuse, exports that write nothing). Verify by counting cameras,
  sidecars and manifests, never by exit status.
- A sidecar named `<stem>.xmp` beside an image is imported automatically on the
  next add, including any pose it carries. The sidecar hygiene exists for this.
- An unresolved flight-log format GUID imports positions only, without
  orientation or accuracies, and still exits 0.
- `RealityScan.log` is global and truncated whenever an instance starts.
- `*` as an instance name means "first available instance".
- Exit codes: 0 success; with `appQuitOnError=true` the error's decimal code;
  3 for a crash (minidump at the `-silent` path).

## Environment

- Native Windows; no WSL. `.bat` and `.vbs` files must have CRLF line endings
  (`.gitattributes` enforces it); with LF, cmd's label search fails
  intermittently.
- Python 3.12 or newer. Invoke the environment's interpreter directly
  (`.venv\Scripts\python.exe`) or activate it; a version-qualified `py -3.13`
  uses the global interpreter.
- Legacy cp1252 consoles cannot print all Unicode output; set
  `PYTHONIOENCODING=utf-8` and use a Unicode-capable terminal.
- Data paths are user-specific; they are prompted for and stored through
  `SettingsStore`, never hard-coded.

## Hard rules

1. Run RealityScan only through `RealityScanCLI` and the `:run` pattern; never
   add a second way to launch or monitor it.
2. Never infer completion from process names.
3. No overall timeouts on RealityScan operations; alignment and model
   generation legitimately run for many hours. Only startup and shutdown are
   bounded (`SHUTDOWN_VERIFY_TIMEOUT_SECONDS`, `STATUS_CALL_TIMEOUT_SECONDS` in
   `realityscan_cli.py`; 120 s in `startRealityScan.bat`).
4. Clear the progress, errors and results marker files only through
   `RealityScanCLI`; they are the source of truth while a run is live.
5. Never hard-code data paths; prompt through `SettingsStore` with the previous
   value as default.
6. One implementation each: the flight-log writer (`write_flight_log`), the
   orientation convention (`realityscan_orientation`), the calibration decision
   (`decide_calibration`), and the camera registry (`modules/cameras.json`).
7. Import a component (`-importComponent`) only from its original export
   location; a relocated `.rsalign` hangs the instance.
8. Never pass delimited data as `.bat` arguments: cmd splits `;`, `,` and `=`.
   Lists cross the boundary as files (`.complist`, `.imagelist`, `.rscmd`);
   settings as `key:value`, converted inside the workflow.
   `RealityScanCLI` rejects shell metacharacters in workflow arguments.
9. Never pass a calibration prior as locked or exact: its conversion to
   RealityScan's conventions is not verified.
10. Consult `docs/rs-reference/` before writing a new workflow.

## Naming

Python files, functions and variables use `snake_case`, classes `PascalCase`.
Preserve native workflow file names, command-line flags, environment
variables and stored setting keys. The product is called RealityScan (`RS`)
throughout; the legacy file extensions `.rcalign` and `.rcproj` are still
read.
