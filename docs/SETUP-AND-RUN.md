# Setup and run

This guide installs Wild Scan on a Windows machine and runs a Wild Sync
recording of the ILX-LR1 stereo rig through it. Follow the parts in order the
first time; later sessions start at [part 6](#6-run-the-pipeline). For the
application's screens step by step, see [Analyze a dataset](ANALYZE_A_DATASET.md).

Commands are for PowerShell. Where Command Prompt differs, the difference is
stated.

Contents:

1. [What you need](#1-what-you-need)
2. [Install the prerequisites](#2-install-the-prerequisites)
3. [Get the code](#3-get-the-code)
4. [Create the environment and install](#4-create-the-environment-and-install)
5. [Configure](#5-configure)
6. [Run the pipeline](#6-run-the-pipeline)
7. [Publish](#7-publish)
8. [Where everything lands](#8-where-everything-lands)
9. [Troubleshooting](#9-troubleshooting)

---

## 1. What you need

**Machine**

- Windows 10 or 11, native. WSL is not supported: the workflows are `.bat`,
  PowerShell and VBS.
- One or more CUDA GPUs. RealityScan uses every GPU it finds by default.
- Plenty of free disk on a local drive. RealityScan's cache grows by tens of
  gigabytes per component and `-clearCache` does not reclaim it; put the cache
  on a large drive (`RS_CACHE_DIR`, [part 5.3](#53-optional-environment-variables)).

**Software**

| Component | Notes |
|---|---|
| RealityScan 2.2 (Epic Games) | A separate installation with its own licence, not a pip package. Default location `C:\Program Files\Epic Games\RealityScan_2.2\`. |
| 64-bit Python 3.12 or newer | From [python.org](https://www.python.org/downloads/windows/), with the `py` launcher. |
| Git for Windows | [git-scm.com](https://git-scm.com/download/win) |

**Accounts, only for publishing**

- **Cesium ion**: an access token.
- **Nira**: an Enterprise plan and a local `niraclient` checkout.

### Your data

A Wild Sync run directory (or a folder holding several): one folder per
camera node, `cam1/` and `cam2/`, each with its `flight_log.csv` and the
images of every frame. The intake takes the card JPEGs (default) or the review
JPEGs; RAW files are not an input. The directory layout, the 23-column
`flight_log.csv` and the rules the intake applies to it are in
[The ILX-LR1 stereo rig](ILX-LR1.md#wild-sync-input). Run directories are only
read; the pipeline copies what it needs into the workspace.

---

## 2. Install the prerequisites

1. Install RealityScan 2.2 and start it once, so it finishes first-run setup
   and licensing.
2. Install Python. On the installer's first screen tick **Add python.exe to
   PATH**.
3. Install Git for Windows with the default options.
4. In a new PowerShell window, check both:

   ```powershell
   py --list
   git --version
   ```

   `py --list` shows the installed interpreters. The examples use `py -3.13`;
   substitute your version (3.12 or newer).

---

## 3. Get the code

Use a Git clone, in a folder of your choice:

```powershell
git clone https://github.com/wild-technology/wildscan.git
Set-Location -LiteralPath ".\wildscan"
```

The pipeline needs the root driver scripts and the CRLF line endings Git
applies to the `.bat` workflows; a wheel, a packaged source archive or GitHub's
source ZIP does not provide both. Quote paths that contain spaces. The native
command boundary rejects shell metacharacters such as `&`, `%`, `!`, commas and
parentheses in workflow arguments, even when quoted, so keep the checkout and
workspace paths simple.

---

## 4. Create the environment and install

```powershell
py -3.13 -m venv .venv
& ".\.venv\Scripts\python.exe" -m pip install --upgrade "pip>=26.2"
& ".\.venv\Scripts\python.exe" -m pip install -e ".[dev]"
```

The commands call the environment's interpreter directly, so activation and
an execution-policy change are unnecessary. Run them from the checkout folder.

- **Command Prompt:** omit `&`.
- **Shorter commands:** after `.\.venv\Scripts\Activate.ps1` (Command Prompt:
  `.venv\Scripts\activate.bat`) succeeds, `python` and `wildscan` stand for the
  environment's interpreter and application in that terminal.
- A version-qualified `py -3.13` always runs the global interpreter, even in an
  activated environment. Use it only to create the environment.

The installation must stay editable (`-e`): the application runs the driver
scripts and the RealityScan workflows from the checkout.

**Verify before touching real data:**

```powershell
& ".\.venv\Scripts\python.exe" -m pytest
```

The output reports the test count and skips. The EGM2008 geoid test skips when
the grid is not available. Investigate every failure before processing data.
The offline tests do not run RealityScan.

---

## 5. Configure

### 5.1 RealityScan location

Nothing to do when RealityScan is in its default folder: the pipeline finds
it, and falls back to 2.1, 2.0 and `Capturing Reality` install folders. For
another location, set `realityscan.executable` in `rs_settings.json` or the
`RS_EXECUTABLE` environment variable.

### 5.2 `rs_settings.json`

This file lives in the checkout root, is ignored by Git and can be edited by
hand. Every prompt stores its last answer here and offers it as the default
next time. Create it by hand only for the `realityscan` section:

```json
{
  "realityscan": {
    "executable": "C:\\Program Files\\Epic Games\\RealityScan_2.2\\RealityScan.exe",
    "instance_name": "RS1",
    "gpu_devices": "0,1"
  }
}
```

All keys are optional; `headless` and `cache_dir` are also read from this
section. Without the file, auto-detection and defaults apply.

### 5.3 Optional environment variables

Set these only when needed. In PowerShell: `$env:NAME = "value"`.

| Variable | Purpose |
|---|---|
| `RS_EXECUTABLE` | Path to `RealityScan.exe` outside the standard locations. |
| `RS_INSTANCE` | Name of the RealityScan instance to own (default `RS1`). Give each parallel run its own name. |
| `RS_GPU_DEVICES` | GPUs for this instance, for example `0`; exported as `CUDA_VISIBLE_DEVICES`. |
| `RS_CACHE_DIR` | RealityScan cache location on a large drive. |
| `RS_HEADLESS` | `0` starts a visible instance, `1` a headless one. Python drivers default to visible; workflows run by hand default to headless. |
| `RS_ALIGN_PARAMS` | An alignment parameter XML to apply instead of `Metadata/AlignmentParams.xml`; recorded in each alignment fingerprint. It changes the result: record it with the run. |
| `RS_MODULES` | Comma-separated module names `main.py` runs without asking. |
| `RS_NO_INTERACTIVE` | `1`: `main.py` does not ask which modules to run. |
| `CESIUM_ION_TOKEN` | Cesium ion access token for publishing. |
| `NIRACLIENT_DIR` | Path to the `niraclient` checkout for Nira publishing. |

**Two zones at once:** give each run its own instance name and GPU set
(`RS_INSTANCE=RS_GPU0` with `RS_GPU_DEVICES=0`, and a second pair for the other
GPU). A lock per instance makes a collision fail immediately.

### 5.4 Flight log import format

The intake writes a 13-column flight log in the pipeline's own RealityScan
format. RealityScan can read it only after that format is installed in its
`flightlogs.xml`.

With the checkout available, close RealityScan and back up `flightlogs.xml`
beside `RealityScan.exe`. Copy only the repository's `<format>` element with
ID `{B438A617-2434-5A24-C1B7-58980F28345A}` into the installed `<FlightLogs>`
element. If that exact ID is already present, compare its parser with the
repository's 13-column format instead of adding a duplicate. Keep the other
installed formats: do not replace the installed file with the repository file,
which holds only this one format. Editing Program Files may require
administrator rights.

The full ID must match `gpsLogFileFormat` in the parameter template
`FlightLogParams.xml`. This read-only check verifies the template and the
installed format; adjust the dictionary path to your RealityScan installation:

```powershell
$flightLogDictionary = 'C:\Program Files\Epic Games\RealityScan_2.2\flightlogs.xml'
$formatId = '{B438A617-2434-5A24-C1B7-58980F28345A}'
$dictionaryText = Get-Content -Raw -LiteralPath $flightLogDictionary
$formatPattern = '<format\b[^>]*\bid\s*=\s*["'']' + [regex]::Escape($formatId) + '["'']'
if ([regex]::Matches($dictionaryText, $formatPattern).Count -ne 1) {
    throw 'The installed dictionary must contain exactly one matching format.'
}
[xml]$params = Get-Content -Raw -LiteralPath 'modules\realityscan_interface\RS_CLI\Metadata\FlightLogParams.xml'
$configuredId = @($params.Configuration.entry | Where-Object { $_.key -eq 'gpsLogFileFormat' })
if ($configuredId.Count -ne 1 -or $configuredId[0].value -ne $formatId) {
    throw 'The format ID in FlightLogParams.xml does not match.'
}
```

A missing or mismatched format imports positions while silently dropping
orientation and accuracies. Check again after a RealityScan update or repair.
The ID check alone does not show that the import works: on the first
alignment, inspect the saved project as described in check A of the
[first-run validation checklist](validation/ILX-LR1_first_run_checklist.md#a-flight-log-format-installed).
The project signatures that distinguish a resolved format from an unresolved
one are in
[the flight-log reference](rs-reference/06-georeferencing-flightlogs-and-scale.md#resolved-2026-08-23--side-a-holds-what-an-unresolvable-format-guid-actually-does).

### 5.5 Cesium publishing: the geoid grid

Cesium publishing converts depths below the sea surface into ellipsoidal
heights with the EGM2008 geoid grid (about 80 MB). `publish_cesium.py` enables
PROJ network access, which can fetch grid data from `cdn.proj.org`. For
offline use, download the complete grid beforehand:

```powershell
& ".\.venv\Scripts\python.exe" -m pyproj sync --file us_nga_egm08_25.tif
```

For a separate offline machine, find the grid directory with
`& ".\.venv\Scripts\python.exe" -c "from pyproj.datadir import get_user_data_dir; print(get_user_data_dir())"`,
copy `us_nga_egm08_25.tif` into the directory the same command reports on the
offline machine, and publish there with `--no-proj-network` so a missing grid
is reported immediately. Without the grid the vertical position would be wrong
by the local geoid undulation; the code refuses an unavailable or undefined
geoid transformation.

### 5.6 Nira publishing

Scripted Nira upload requires an Enterprise account with an API key and
secret. Clone the [official client](https://github.com/NiraOfficial/niraclient),
configure it with the environment's interpreter, and point Wild Scan at it.
From the Wild Scan checkout:

```powershell
$wildscanPython = (Resolve-Path ".\.venv\Scripts\python.exe").Path
git clone https://github.com/NiraOfficial/niraclient.git "$HOME\niraclient"
Push-Location -LiteralPath "$HOME\niraclient"
try {
    & $wildscanPython .\nira.py configure
} finally {
    Pop-Location
}
$env:NIRACLIENT_DIR = "$HOME\niraclient"
```

The configuration command asks for the API key and secret. The client bundles
its dependencies
([official setup guidance](https://github.com/NiraOfficial/niraclient#quick-start)).
`NIRACLIENT_DIR` is set for the current PowerShell session only; set it again
in a new terminal. The single-service publisher also accepts `--niraclient`.

---

## 6. Run the pipeline

### 6.1 The workspace

A **workspace** is one folder per dataset on a large local drive, separate from
the Wild Sync run directories, for example `D:\workspace\transect-01`. Every
stage writes into it ([part 8](#8-where-everything-lands)).

### 6.2 The application

```powershell
& ".\.venv\Scripts\python.exe" -m wildscan
```

To open an existing workspace directly:
`& ".\.venv\Scripts\python.exe" -m wildscan "D:\workspace\transect-01"`.
After activation, `wildscan` is equivalent.

On the first screen, enter the Wild Sync run directory (or a folder of runs;
separate several paths with `;`) and the workspace. The screen lists each run
and, per camera node, the camera it belongs to and its card, review and RAW
image counts. **View results** shows the status of an existing workspace
without processing; **Continue** opens the stage list:

1. Wild Sync Intake
2. Preprocess (CLAHE)
3. Batch into Zones
4. Align Zones
5. Merge Components
6. Generate Models
7. Export Deliverables
8. Publish (Cesium / Nira)

Stages already complete are unselected. Select the stages, answer the
questions, review the plan and choose **Run**. The first four stages run as one
`main.py` command; each later stage is its own command, with a pause between
commands unless you chose to continue automatically.
[Analyze a dataset](ANALYZE_A_DATASET.md) walks through the screens.

### 6.3 The stages on the command line

Each command below is what the application runs.

**Intake through alignment** (Wild Sync Intake, Preprocess Images, Batch
Directory, RealityScan Alignment):

```powershell
$env:RS_MODULES = "Wild Sync Intake,Preprocess Images,Batch Directory,RealityScan Alignment"
$env:RS_NO_INTERACTIVE = "1"
& ".\.venv\Scripts\python.exe" main.py --output_dir "D:\workspace\transect-01" --continue_automatically true --w_input "D:\wildsync\260820_1925_transect-01" --w_variant card --w_calibration auto --w_declination 0 --b_target_images 3000 --b_overlap_percent 20 --r_project_label= --r_display_output false
```

`RS_MODULES` and `RS_NO_INTERACTIVE` select the modules without the checkbox
prompt. Any parameter the module chain asks for and that is not given as a
flag is asked for at the console, with the stored or default value offered
(Enter accepts it). `--r_project_label=` passes an empty label, which disables
the dated project copies. `& ".\.venv\Scripts\python.exe" main.py --help` lists
every flag with its description.

Wild Sync Intake flags (defaults in brackets):

| Flag | Meaning |
|---|---|
| `--w_input` | Run directory, or folder of run directories; several separated with `;` |
| `--w_variant` | `card` or `review` [`card`] |
| `--w_calibration` | `auto`, `prior`, `groups` or `off` [`auto`] |
| `--w_assert_focal` | `true` asserts the lenses were at the calibration focal length when EXIF is missing or differs [`false`] |
| `--w_declination` | Magnetic declination in degrees, east positive, added to the heading [`0`] |
| `--w_heading_source` | `auto` (`heading_imu`, else `yaw`), `heading_imu`, `yaw` or `heading_mag_xplore` [`auto`] |
| `--w_pos_accuracy` | X/Y position accuracy in metres [`10`] |
| `--w_static_pos_accuracy` | X/Y accuracy when a run carries one static fix [`1000`] |
| `--w_alt_accuracy` | Altitude accuracy in metres [`1`] |
| `--w_surface_altitude` | Altitude written when depth is empty [`0.0`, camera at the sea surface] |
| `--w_orientation_accuracy` | Yaw and roll accuracy in degrees; pitch accuracy comes from the mount [`15`] |
| `--w_min_match_rate` | Fail below this percentage of matched frames; `0` disables [`80`] |
| `--w_time_err_warn` | Warn when `time_err_ms` exceeds this [`50`] |

What these mean and how they are applied is in
[The ILX-LR1 stereo rig](ILX-LR1.md). Preprocess Images takes `--p_clahe_clip`
[`2.0`], `--p_clahe_tile` [`8`], `--p_white_balance` [`false`] and
`--p_workers` [`0` = CPU count]. Batch Directory takes `--b_target_images`
[`3000`], `--b_min_zone` [`1000`], `--b_max_zone` [`4000`],
`--b_overlap_percent` [`20`], `--b_single_zone_below` [`4000`; a dataset with
fewer images is one zone, without clustering or overlap copies] and others
listed by `--help`. RealityScan
Alignment takes `--r_min_component_size` [`50`; a zone with fewer images uses
its image count, at least 2], `--r_display_output` and `--r_project_label`.

**Merge the per-zone components, then model the merged result:**

```powershell
& ".\.venv\Scripts\python.exe" merge_zones.py --components_root "D:\workspace\transect-01\aligned_components" --images_root "D:\workspace\transect-01\batched_images_by_zone" --output "D:\workspace\transect-01\merged"
& ".\.venv\Scripts\python.exe" run_models.py --workspace "D:\workspace\transect-01"
```

The application passes further merge options explicitly (`--min_size 50
--target 0.95 --ladder merge_first --merge_scope neighbour --pair_gate overlap
--loss_tolerance 0.0025 --scale_gate true --scale_min 0.9 --scale_max 1.1`
among others); `merge_zones.py --help` lists them. `run_models.py` models every
final component, smallest first, and skips a component whose measured metric
scale falls outside the accepted band. A rerun reuses earlier results only when
the assembly, source components, navigation and model recipe still match.

**Export deliverables** (OBJ by parts, FBX by parts, dense coloured PLY):

```powershell
$workspace = 'D:\workspace\transect-01'
$report = Get-Content -Raw -LiteralPath "$workspace\merged\merge_report.json" | ConvertFrom-Json
$names = @($report.clusters.final_components | ForEach-Object { ($_.key -split '/')[-1] })
if ($names.Count -eq 0) { throw 'The merge report has no final components.' }
New-Item -ItemType Directory -Path "$workspace\exports" -Force | Out-Null
[System.IO.File]::WriteAllLines("$workspace\exports\components.names", [string[]]$names, [System.Text.UTF8Encoding]::new($false))
& ".\.venv\Scripts\python.exe" modules/export_deliverables.py --project "$workspace\merged\assembly\Merged.rsproj" --exports "$workspace\exports" --names "$workspace\exports\components.names"
```

---

## 7. Publish

Publish everything a workspace has exported, to whichever service has
credentials:

```powershell
$env:CESIUM_ION_TOKEN = "<your ion token>"
& ".\.venv\Scripts\python.exe" publish_batch.py --workspace "D:\workspace\transect-01" --prefix "transect-01" --dry-run
```

Drop `--dry-run` to publish. `--components` limits it to named components, and
`--flight-log` adds an independent check of the placement against the
navigation envelope.

One export at a time:

```powershell
& ".\.venv\Scripts\python.exe" publish_cesium.py --name "transect-01 component" --dir "D:\workspace\transect-01\exports\COMPONENT\obj" --verify
& ".\.venv\Scripts\python.exe" publish_nira.py --name "transect-01 component" --dir "D:\workspace\transect-01\exports\COMPONENT\obj" --niraclient "$HOME\niraclient"
```

Replace `COMPONENT` with an exported component name. Cesium reads the
coordinate system and transform from the mesh's `.rsInfo` sidecar.

- Cesium prepares local copies under `_cesium_local`. A non-empty folder
  without its `.cesium-stage.json` marker is left alone; pass `--staging` with
  a new directory in that case. Nira excludes these copies.
- Cesium defaults to the whole OBJ and Nira to the by-parts copy; `--parts
  whole` or `--parts split` chooses explicitly. Nira requires `--geometry` when
  the directory mixes mesh formats.
- Both services take the OBJ. Nira does not accept PLY point clouds.
- Always pass `--verify` on a real Cesium publish. Without it the publisher
  stops at "tiling complete"; with it, it reads the finished tileset back and
  confirms its position and depth.

---

## 8. Where everything lands

```text
<workspace>/
  raw_images/                 Wild Sync Intake: ilx_left/, ilx_right/, flight_log_<zone><band>_UTM.txt, wildsync_intake.json
  preprocessed_images/        Preprocess Images: CLAHE copies, same names
  preprocessed_images.manifest.json
  batched_images_by_zone/     Batch Directory: zone_<n>/ with per-camera folders and a flight log; batch_inputs.json
  aligned_components/         RealityScan Alignment: zone_<n>/ with the saved scene, components, manifests, .rscmd
  superseded/                 earlier zone outputs moved aside on a re-run (never deleted)
  merged/                     merge attempts, merge_report.json, the assembly project
  exports/                    per-component deliverables, components.names
  logs/                       driver logs and the generated FlightLogParams_<zone><band>.xml
  RC_projects/                dated project copies (only with a project label)
```

| Report | Written by |
|---|---|
| `raw_images/wildsync_intake.json` | Wild Sync Intake |
| `preprocessed_images.manifest.json` | Preprocess Images (settings, completion, source and output integrity) |
| `batched_images_by_zone/batch_inputs.json` | Batch Directory (what the zones were built from) |
| `merge_report.json` | `merge_zones.py` |
| `grow_report.json` | `grow_zone.py` |
| `models_report.json` | `run_models.py` |
| `publish_report.json`, `publish_plan.json` | `publish_batch.py` (the plan with `--dry-run`) |
| `interrupted_stage.json` | the application after **Stop stage**; cleared by a successful retry |

Answers you typed are stored in `rs_settings.json` in the checkout root.

---

## 9. Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `wildscan` is not recognised | Use `& ".\.venv\Scripts\python.exe" -m wildscan` from the checkout, or activate the environment first. |
| Packages installed but Python cannot import them | A version-qualified `py` command installed into the global interpreter. Reinstall with `& ".\.venv\Scripts\python.exe" -m pip install -e ".[dev]"`. |
| `Activate.ps1` cannot be loaded | Activation is optional; call `& ".\.venv\Scripts\python.exe"` directly. |
| `import cv2` fails with a DLL error | Check the Visual C++ runtime; Windows N/KN editions also need the Media Feature Pack ([OpenCV FAQ](https://pypi.org/project/opencv-python/#frequently-asked-questions)). |
| `RealityScan.exe not found` | Set `realityscan.executable` in `rs_settings.json` or `RS_EXECUTABLE`. |
| Intake: `not a Wild Sync flight_log.csv header` | The header must be exactly the 23 columns in [The ILX-LR1 stereo rig](ILX-LR1.md#flight_logcsv). |
| Intake: `only N of M frames ... below the 80% floor` | Many rows have no image of the chosen variant, or images have no row. Check `--w_variant` and that the images were copied off the cards; lower `--w_min_match_rate` only deliberately. |
| Intake: a file `already exists with different content`, or files `this intake did not plan` | The workspace holds another dataset. Use a fresh workspace. |
| Intake warns `calibration groups only` | The images do not match the stored calibration (aspect ratio or EXIF focal length), or the node assignment is not confirmed. Expected for the August 2026 field runs. |
| Alignment: `Calibration sidecars cannot be delivered with the pool layout` | Batch with the default copy layout, or re-run the intake with `--w_calibration off`. |
| A stage "succeeds" but produces nothing | RealityScan reports success in several no-op cases. Check the stage's report and census, not the exit code; [failure modes](rs-reference/12-failure-modes-and-race-conditions.md) lists them. |
| Orientation or accuracies missing after import | The flight-log format is not installed or its ID does not match ([5.4](#54-flight-log-import-format)). |
| Publishing fails on the geoid grid | See [5.5](#55-cesium-publishing-the-geoid-grid). |
| Two runs interfere | Both use the same instance name. Give each its own `RS_INSTANCE` and `RS_GPU_DEVICES`. |
| The disk fills during a run | RealityScan's cache. Move it with `RS_CACHE_DIR` and clear it between runs with the `FlushCache` workflow. |
