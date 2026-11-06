# WildScan Architecture and Setup

WildScan prepares underwater imagery and navigation, runs RealityScan 2.2 on
Windows, and manages reconstruction and publishing outputs. RealityScan
performs the photogrammetry; WildScan coordinates the inputs and stages.

## Setup

Use a 64-bit Python installation and an editable source checkout. Install and
configure RealityScan before running native stages. For prerequisites,
installation commands, project settings and publishing setup, follow
[Setup and run](docs/SETUP-AND-RUN.md). Use the
[dataset guide](docs/ANALYZE_A_DATASET.md) to choose inputs and stages.
[Contributing](CONTRIBUTING.md) covers development checks.

Keep original images and navigation data unchanged. Alignment writes XMP
sidecars to its working image tree, so use prepared copies for native work.
Offline tests do not establish native processing, geographic accuracy or
publishing acceptance.

## Repository layout

| Location | Responsibility |
| --- | --- |
| `wildscan/app.py` | Terminal interface screens |
| `wildscan/discovery.py` | Read-only source and camera discovery |
| `wildscan/session.py` | Session planning, persistence, questions and stage commands |
| `wildscan/runner.py` | Subprocess supervision, logs and progress |
| `main.py` | Extraction, georeferencing, preprocessing, batching and alignment |
| `grow_zone.py`, `merge_zones.py` | Component growth and assembly |
| `run_models.py`, `finish_model.py` | Model creation and finishing |
| `modules/` | Processing, camera, navigation, component and export services |
| `module_base/` | Shared parameters, settings, execution and checkpoint helpers |
| `modules/realityscan_interface/` | RealityScan controller and native batch workflows |
| `publish_batch.py`, `publish_cesium.py`, `publish_nira.py` | Optional publishing |
| `tests/` | Offline regression checks |
| `docs/` | User guides and RealityScan reference |

Standalone data preparation commands remain at the repository root. Existing
command names, settings keys, camera profiles and file formats are compatibility
interfaces; check their callers before changing them. Use the `RealityScan.*`
settings names required by the installed version.

## Processing and data

The normal sequence prepares imagery and navigation, batches images into zones,
aligns them in RealityScan, optionally grows or merges components, and generates models.
Publishing is optional. Stages record their inputs and outputs so that a later
run can decide what can be reused. A successful process exit alone does not
establish complete or scientifically valid output.

Camera definitions and calibration policies are in `modules/cameras.json` and
`modules/camera_registry.py`. Flight-log discovery and templates are in
`modules/flight_logs.py`. The georeferencing implementations share navigation
and mount conventions. Preserve calibrated image geometry, source metadata and
existing XMP files when preparing processing inputs. WCA and Zeuss use the
existing extraction and georeferencing workflows. Sony Wild Sync is an explicit
intake with its own manifest, calibration decisions and alignment settings;
registered camera profiles do not change the defaults for generic datasets.
Explicit alignment overrides take precedence. Keep local and UTM coordinate
frames separate.

## RealityScan execution

`modules/realityscan_interface/realityscan_cli.py` starts, monitors and closes
named RealityScan instances. Batch workflows in `RS_CLI/Scripts/` use a shared
`:run` pattern to send commands and check their results. Metadata presets and
error hooks are alongside those scripts. Some finishing workflows attach to an
existing scene; they must not create a fresh scene in its place.

Use [the RealityScan reference](docs/rs-reference/README.md) for commands,
settings, version qualifications and known failure modes. Validate native
changes on representative working copies in the target installation.

## Hard rules

1. Launch and monitor RealityScan through `RealityScanCLI` and the shared `:run`
   pattern. Extend these interfaces when new workflow support is needed.
2. Do not infer completion from process names. Check the operation result,
   expected outputs and component census.
3. Long native operations have no fixed overall timeout. Startup and shutdown
   bounds are defined by the constants in `realityscan_cli.py`.
4. Manage progress, error and result markers through their owning execution
   layer. Concurrent instances must not share or overwrite each other's state.
5. Keep user-specific paths out of code. Resolve paths through `SettingsStore`
   or explicit configuration, and protect source data and scene checkpoints.
6. Preserve the shared georeferencing behavior of `georeference_survey.py` and
   `modules/georeference/`, including mount and settings compatibility.
7. Import components from their original export locations. Moving component
   files can cause RealityScan to wait indefinitely.
8. Do not pass delimited lists as batch arguments: `cmd` can split or alter
   them. Pass lists in files and settings as `key:value` for conversion in the
   workflow. Keep secrets in files or environment variables.
9. Consult `docs/rs-reference/` and the installed RealityScan help when changing
   native behavior. Check reconstruction and authenticated publishing separately
   from installation and launch.
