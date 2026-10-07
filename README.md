# Wild Scan

Wild Scan is a photogrammetry pipeline for one rig: two Sony ILX-LR1 cameras
mounted as a stereo pair and recorded with Wild Sync. It takes a Wild Sync run
directory, prepares the images of both cameras and their navigation priors,
and drives **RealityScan 2.2** (Epic Games, formerly RealityCapture) through
its command line to align the images and build, export and publish textured
models.

The pipeline runs on native Windows. RealityScan performs alignment and
reconstruction; Wild Scan prepares its inputs, runs its workflows and checks
their results.

**New users:** install the checkout with [Setup and run](docs/SETUP-AND-RUN.md),
then follow [Analyze a dataset](docs/ANALYZE_A_DATASET.md). The rig, its
calibration and the conventions the pipeline applies to it are described in
[The ILX-LR1 stereo rig](docs/ILX-LR1.md). The [documentation index](docs/README.md)
lists everything else.

The earlier multi-camera pipeline is preserved at tag `v1.0.0`. Release notes
are in [CHANGELOG.md](CHANGELOG.md); third-party components are listed in
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) and
[sbom.spdx.json](sbom.spdx.json).

## Validation status

The offline test suite covers the intake, the calibration decision, the
sidecar and command-file writers, the flight-log writer, the orientation
function, stage planning and process supervision. The intake has been run on
real Wild Sync data. **No live RealityScan alignment has yet been run with this
version.** The following are not validated against RealityScan:

- the mapping from Wild Sync IMU pitch, roll and heading to RealityScan
  flight-log Yaw, Pitch and Roll;
- the Brown3 coefficient convention and radius normalisation used when the
  measured OpenCV k1, k2 are passed in a sidecar;
- whether the stored calibration applies to a given capture;
- that the `-execRSCMD` command-file mode of `AlignZone.bat` imports the
  images and their sidecars as written;
- whether RealityScan honours an `initial` `xcr:FocalLength35mm` written next
  to the calibration and distortion group ids in a `groups` sidecar, starting
  each camera's focal there and refining it;
- whether RealityScan reads the EXIF focal length from the preprocessed
  copies (the only focal it gets with calibration `off`).

The [first-run validation checklist](docs/validation/ILX-LR1_first_run_checklist.md)
covers each of them. Passing offline tests do not establish reconstruction
quality, geographic accuracy or native behaviour on a new installation.

## Requirements

- Windows 10 or 11, native (RealityScan is Windows-only; the workflows are
  `.bat`, PowerShell and VBS).
- RealityScan 2.2. The executable is found under
  `C:\Program Files\Epic Games\RealityScan_2.2\` (with fallbacks to 2.1, 2.0
  and `Capturing Reality` install folders), or set `RS_EXECUTABLE` or
  `"realityscan": {"executable": ...}` in `rs_settings.json`.
- 64-bit Python 3.12 or newer (`numpy>=2.5` and `scipy>=1.18` require 3.12).
- One or more CUDA GPUs. RealityScan uses all of them by default.

## Quickstart

In PowerShell:

```powershell
git clone https://github.com/wild-technology/wildscan.git
cd wildscan
py -3.13 -m venv .venv
& ".\.venv\Scripts\python.exe" -m pip install --upgrade "pip>=26.2"
& ".\.venv\Scripts\python.exe" -m pip install -e ".[dev]"
& ".\.venv\Scripts\python.exe" -m pytest
```

Use any installed Python 3.12 or newer in place of `py -3.13`. The install
must be editable (`-e`) from a Git clone: the application runs the driver
scripts and the RealityScan `.bat` workflows from the checkout, and the `.bat`
files need the CRLF line endings Git applies. A built wheel is not a supported
way to run the pipeline; `wildscan` refuses to start without the driver
scripts.

Before the first alignment, install the pipeline's 13-column flight-log format
into RealityScan's `flightlogs.xml`
([Setup and run, section 5.4](docs/SETUP-AND-RUN.md#54-flight-log-import-format)).

Start the application:

```powershell
& ".\.venv\Scripts\python.exe" -m wildscan
```

On its first screen, enter a Wild Sync run directory (or a folder of runs) and
a workspace folder for the results. The application shows the two ILX-LR1
cameras found in the run with their card, review and RAW image counts, then
lets you choose stages, answer their parameters and run them. To reopen an
existing workspace, pass its path:
`& ".\.venv\Scripts\python.exe" -m wildscan "D:\workspace\transect-01"`.

## The pipeline

| Stage | What it does |
|---|---|
| Wild Sync Intake | copies the chosen image variant of both cameras into `raw_images/`, writes one 13-column flight log with position and orientation priors, decides per camera how calibration is delivered to RealityScan, and records everything in `raw_images/wildsync_intake.json` |
| Preprocess Images | CLAHE contrast enhancement into `preprocessed_images/`, same file names |
| Batch Directory | groups the images into spatial zones under `batched_images_by_zone/`, each with its own flight log |
| RealityScan Alignment | aligns each zone with `AlignZone.bat`, delivers the decided calibration sidecars, and exports every component with its membership manifest to `aligned_components/` |
| Merge Components | `merge_zones.py`: merges the per-zone components into one assembly under `merged/` |
| Generate Models | `run_models.py`: builds a textured model for every final component that passes the metric-scale check |
| Export Deliverables | `modules/export_deliverables.py`: OBJ and FBX by parts and a dense coloured PLY under `exports/` |
| Publish | `publish_batch.py`: uploads the exports to Cesium ion and/or Nira |

The first four stages are the module chain of `main.py`; the application runs
them as one command and each later stage as its own command.

Calibration delivery is decided per camera at intake: `prior` (the stored
calibration as an initial prior), `groups` (each camera its own calibration
and distortion group, solved by RealityScan) or `off`. The stored calibration
is applied only when the node assignment is confirmed and the images match its
aspect ratio and focal length; the August 2026 field runs therefore receive
`groups`. Alignment uses RealityScan's Brown3 distortion model for every
camera. Details: [The ILX-LR1 stereo rig](docs/ILX-LR1.md).

## Documentation

| Task | Read |
|---|---|
| Install on a new Windows machine and run from the command line | [Setup and run](docs/SETUP-AND-RUN.md) |
| Process a Wild Sync run or resume a workspace | [Analyze a dataset](docs/ANALYZE_A_DATASET.md) |
| Understand the rig, its calibration and conventions | [The ILX-LR1 stereo rig](docs/ILX-LR1.md) |
| Validate a new installation on real data | [First-run validation checklist](docs/validation/ILX-LR1_first_run_checklist.md) |
| Understand the code and the native execution rules | [Architecture](ARCHITECTURE.md) |
| Contribute a change | [Contributing](CONTRIBUTING.md) |

## Repository layout

| Path | Purpose |
|---|---|
| `main.py` | Module chain: Wild Sync Intake, Preprocess Images, Batch Directory, RealityScan Alignment; `RS_MODULES` / `RS_NO_INTERACTIVE` select modules without prompting; a failed module stops the chain with exit code 1 |
| `wildscan/` | The interactive application (`python -m wildscan`): dataset and workspace selection, stage selection, parameter questions, live run screen, workspace status |
| `modules/wildsync_intake/` | Wild Sync Intake |
| `modules/camera_registry.py`, `modules/cameras.json` | The two ILX-LR1 cameras: calibration, Wild Sync file name families, mounts, the stereo rig and its node-assignment switch, default prior accuracies |
| `modules/calibration_sidecars.py` | The per-camera calibration decision, the XMP sidecars and the `.rscmd` command file |
| `modules/flight_logs.py` | Flight-log naming, discovery, writing, UTM coordinate-system generation and the orientation convention |
| `modules/preprocess_images/`, `modules/image_batcher/` | Preprocess Images and Batch Directory |
| `modules/realityscan_interface/` | Everything that runs RealityScan: `realityscan_cli.py`, the RealityScan Alignment stage, `RS_CLI/Scripts/*.bat` workflows, `RS_CLI/Metadata/*.xml` parameter files |
| `calibration/` | The ILX-LR1 stereo calibration record |
| `merge_zones.py`, `grow_zone.py` | Component merge driver and within-zone growth driver |
| `run_models.py`, `finish_model.py` | Model generation for every final component; finishing a model in a running RealityScan instance |
| `publish_batch.py`, `publish_cesium.py`, `publish_nira.py` | Publishing to Cesium ion and Nira |
| `poses_to_flight_log.py`, `decimate_images.py` | Refined flight log from aligned poses; dataset thinning |
| `flightlogs.xml` | The 13-column flight-log format to install into RealityScan |
| `module_base/` | `RSModule`, `Parameter`, `SettingsStore` |
| `scripts/validation/` | Manual preprocessing check |
| `tests/` | Offline test suite |

## Settings

Answers to every prompt are stored in `rs_settings.json` at the checkout root
(ignored by Git) and offered as defaults next time. Its `realityscan` section
holds machine settings, all optional:

```json
{
  "realityscan": {
    "executable": "C:\\Program Files\\Epic Games\\RealityScan_2.2\\RealityScan.exe",
    "instance_name": "RS1",
    "gpu_devices": "0,1"
  }
}
```

## License

MIT; see [LICENSE](LICENSE).
