# Setup and run — wildscan

A step-by-step guide to installing wildscan on a new machine and running a
dive through it. Follow the parts in order the first time. Later sessions
start at [part 6](#6-run-the-pipeline).

For the first-use input checklist and workspace inspection, follow
[Analyze a dataset](ANALYZE_A_DATASET.md). The
[documentation index](README.md) routes the current guides and historical
experiment records.

Commands are written for **Windows PowerShell**, the platform the pipeline
runs on. Where a step differs in Command Prompt, the alternative is given.

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
10. [Where to read further](#10-where-to-read-further)

---

## 1. What you need

**Machine**

- Windows 10 or 11, native. WSL is not supported: the workflows are `.bat`,
  PowerShell and VBS.
- One or more CUDA GPUs. RealityScan uses every GPU it finds by default.
- Plenty of free disk. Alignment and model generation are disk-hungry —
  RealityScan's cache has been measured growing about 72 GB per component,
  and `-clearCache` does not reclaim it. Keep the cache on a large drive
  (see `RS_CACHE_DIR` in [part 5](#53-optional-environment-variables)).

**Software**

| Component | Notes |
|---|---|
| RealityScan 2.2 (Epic Games) | A separate install, **not** a pip package. Installed by default under `C:\Program Files\Epic Games\RealityScan_2.2\`. Needs its own Epic licence. |
| 64-bit Python 3.13 or newer recommended | From [python.org](https://www.python.org/downloads/windows/), with the `py` launcher. Development and native validation use 3.13+. The declared package floor is 3.12, matching the minimum dependencies. |
| Git for Windows | [git-scm.com](https://git-scm.com/download/win) |

**Accounts, only for the stages you use**

- **Cesium ion** — an access token, for publishing to ion.
- **Nira** — an Enterprise plan and a local `niraclient` checkout, for
  publishing to Nira.

### Your data

- The dive imagery, or `.mp4` / `.mov` recordings to extract. Video filenames
  must contain a UTC timestamp as `YYYYMMDDTHHMMSSZ` or `YYYYMMDDHHMMSS`, for
  example `20260930T120000Z`. Missing or invalid timestamps prevent extraction.
  Split recordings named with `_0001_`, `_0002_`, and so on need every preceding
  part and readable duration metadata to place frames at the correct time.
- The ROV navigation table (the Kalman CSV) covering the dive, used to
  georeference the images.

The navigation file is comma-separated, with these case-sensitive columns:

```text
Timestamp,kalman_lat,kalman_long,kalman_depth,kalman_yaw_deg,kalman_pitch_deg,kalman_roll_deg
```

`Timestamp` must use UTC `YYYY-MM-DDTHH:MM:SSZ`, for example
`2026-09-30T12:00:00Z`. Coordinates are latitude/longitude in degrees, depth
is metres, and yaw/pitch/roll are degrees. Depth becomes `-abs(depth)` in the
flight log. Empty numeric cells and nonfinite numbers become missing values;
usable positions still require valid coordinates. The pipeline rejects malformed
numeric rows; the standalone georeferencing command skips them. Check the data
before a long run. Camera filename families and rig defaults are recorded in
`modules/cameras.json`.

Extraction uses OpenCV's `VideoCapture`; the installed OpenCV wheel includes
FFmpeg, so a separate `ffmpeg.exe` is unnecessary. Codec support still depends
on the recording: confirm a small sample decodes before processing a dive.
[OpenCV package documentation](https://pypi.org/project/opencv-python/).

---

## 2. Install the prerequisites

1. Install RealityScan 2.2 and launch it once, so it finishes first-run
   setup and licensing.
2. Install Python. On the installer's first screen tick **"Add python.exe to
   PATH"**.
3. Install Git for Windows, accepting the defaults.
4. Open a new PowerShell window and check both tools answer:

   ```powershell
   py --list
   git --version
   ```

   `py --list` should show your installed 64-bit 3.13+ interpreter. The examples
   select 3.13; substitute your installed version, such as `py -3.14`, as needed.

---

## 3. Get the code

Use a Git clone. Create the parent directory before entering it:

```powershell
New-Item -ItemType Directory -Path "C:\tools" -Force | Out-Null
Set-Location -LiteralPath "C:\tools"
git clone https://github.com/wild-technology/wildscan.git
Set-Location -LiteralPath ".\wildscan"
```

An editable installation was checked from a checkout path containing spaces.
Quote paths used as arguments, and use PowerShell's `&` operator when invoking
a quoted executable path. Native RealityScan processing has not been validated
for every path layout; a simple checkout and workspace path remains prudent.
The native command boundary rejects shell metacharacters such as `&`, `%`, `!`,
commas and parentheses even in quoted workflow arguments.

The full pipeline depends on root driver scripts and CRLF batch workflows.
Wheels and packaged source archives omit root drivers and other checkout files;
GitHub's source ZIP does not apply the checkout line-ending rules in
`.gitattributes`. Use the Git checkout and editable install below for the
supported workflow.

---

## 4. Create the environment and install

Install into a virtual environment, so wildscan's processing dependencies stay
separate from the rest of the machine.

```powershell
py -3.13 -m venv .venv
& ".\.venv\Scripts\python.exe" -m pip install --upgrade "pip>=26.2"
& ".\.venv\Scripts\python.exe" -m pip install -e ".[dev]"
```

Upgrade the environment installer before resolving dependencies. The commands
invoke the environment's Python directly. They work without
activation or a PowerShell execution-policy change. Run them from the checkout
folder; use the full quoted executable path with `&` from another directory.

- **Only Python 3.12 installed?** Use `py -3.12 -m venv .venv` to meet the
  declared package floor; 3.13+ remains recommended for native validation.
- **Command Prompt instead of PowerShell?** Omit `&` when invoking
  `.venv\Scripts\python.exe`.
- **Prefer shorter commands?** Activation is optional:

  ```powershell
  .\.venv\Scripts\Activate.ps1
  ```

  In Command Prompt, activation is `.venv\Scripts\activate.bat`. Only after
  activation succeeds can `python` and `wildscan` stand in for the environment's
  full paths. Activate again in each new terminal if using those shorter names.
  If activation is blocked, keep using the direct interpreter commands above.

> A version-qualified `py -3.13` command ignores the active environment and
> runs the global interpreter. Use it to create the environment, then use the
> environment's interpreter for installation and execution.

The install must stay **editable** (`-e`). The app runs the driver scripts
and the RealityScan `.bat` workflows out of the checkout itself.

**Verify before touching real data:**

```powershell
& ".\.venv\Scripts\python.exe" -m pytest
```

Pytest collects the offline suite from `tests/` and reports its current count
and skips. The geoid check skips when the EGM2008 grid is unavailable.
Investigate failures before processing a dive; a failure can concern the
environment, a fixture, or the code. Offline tests do not establish native
RealityScan acceptance. Manual validation commands live in
`scripts/validation/`, with historical plans under `docs/validation/`.

---

## 5. Configure

### 5.1 RealityScan location

Nothing to do if RealityScan sits in its default folder: the pipeline finds
it, falling back to 2.1/2.0 and older "Capturing Reality" install folders.

For a non-standard install, point at it in `rs_settings.json` (see below) or
set `RS_EXECUTABLE`.

### 5.2 `rs_settings.json`

This file lives in the checkout root, is gitignored, and is human-editable.
Every prompt in the pipeline stores your last answer here and offers it as
the default next time, so it largely writes itself — you only need to create
it by hand for the reserved `realityscan` section:

```json
{
  "realityscan": {
    "executable": "C:\\Program Files\\Epic Games\\RealityScan_2.2\\RealityScan.exe",
    "instance_name": "RS1",
    "gpu_devices": "0,1"
  }
}
```

All keys are optional. Omit the file entirely for auto-detection.

### 5.3 Optional environment variables

Set these only when you need them. In PowerShell: `$env:NAME = "value"`.

| Variable | Purpose |
|---|---|
| `RS_EXECUTABLE` | Path to `RealityScan.exe` when it is not in a standard location. |
| `RS_INSTANCE` | Name of the RealityScan instance to own (default `RS1`). Give each parallel run its own name. |
| `RS_GPU_DEVICES` | GPUs for this instance, e.g. `0` — exported as `CUDA_VISIBLE_DEVICES`. |
| `RS_CACHE_DIR` | Move RealityScan's cache to a large drive. |
| `RS_ALIGN_PARAMS` | Alignment parameter XML to apply instead of the repository's `AlignmentParams.xml`; its content is recorded in each alignment fingerprint. |
| `RS_HEADLESS` | `0` launches a visible instance; `1` launches headless. Python drivers default to visible. Hand-run batch workflows default to headless when unset. |
| `CESIUM_ION_TOKEN` | Cesium ion access token, used by the publishers. |
| `NIRACLIENT_DIR` | Path to your `niraclient` checkout, for Nira publishing. |
| `RS_MODULES` | Comma-separated module names to run non-interactively in `main.py`. |
| `RS_NO_INTERACTIVE` | `1` never prompts — required values must come from flags or stored settings. |

**Running two zones at once:** give each run its own instance name and GPU
set (`RS_INSTANCE=RS_GPU0` with `RS_GPU_DEVICES=0`, and a second pair for
the other GPU). A per-instance lock makes a same-instance collision fail
immediately rather than corrupt both runs.

### 5.4 Flight log import format

With the checkout available, close RealityScan and back up `flightlogs.xml`
beside `RealityScan.exe`. Copy only the repository's custom `<format>` element,
with ID `{B438A617-2434-5A24-C1B7-58980F28345A}`, inside the installed
`<FlightLogs>` element. If that exact ID is already present, compare its parser
with the repository's 13-column format rather than adding a duplicate. Preserve
the other installed formats; do not replace the installation dictionary with the
repository's older copy. Editing Program Files may require administrator access.

The full ID must match `gpsLogFileFormat` in both parameter templates. This
read-only check verifies the two templates and the installed format; adjust the
dictionary path for your RealityScan installation:

```powershell
$flightLogDictionary = 'C:\Program Files\Epic Games\RealityScan_2.2\flightlogs.xml'
$formatId = '{B438A617-2434-5A24-C1B7-58980F28345A}'
$dictionaryText = Get-Content -Raw -LiteralPath $flightLogDictionary
$formatPattern = '<format\b[^>]*\bid\s*=\s*["'']' + [regex]::Escape($formatId) + '["'']'
if ([regex]::Matches($dictionaryText, $formatPattern).Count -ne 1) {
    throw 'The installed dictionary must contain exactly one matching format.'
}
foreach ($template in @('FlightLogParams.xml', 'FlightLogParamsLocal.xml')) {
    [xml]$params = Get-Content -Raw -LiteralPath "modules\realityscan_interface\RS_CLI\Metadata\$template"
    $configuredId = @($params.Configuration.entry | Where-Object { $_.key -eq 'gpsLogFileFormat' })
    if ($configuredId.Count -ne 1 -or $configuredId[0].value -ne $formatId) {
        throw "The format ID in $template does not match."
    }
}
```

A missing or mismatched format can import positions while silently dropping
orientation and accuracy. Recheck after a RealityScan update or repair. For the
first native validation, use a few copied images and their generated flight log,
save the project, and inspect the position, orientation and accuracy priors.
The saved-project signatures and the dated comparison are in
[the flight-log reference](rs-reference/06-georeferencing-flightlogs-and-scale.md#resolved-2026-08-23--side-a-holds-what-an-unresolvable-format-guid-actually-does).
The ID check alone does not establish native import correctness.

### 5.5 Cesium publishing: the geoid grid

Cesium publishing converts depths below the sea surface into ellipsoidal
heights using the EGM2008 geoid grid (~80 MB). `publish_cesium.py` enables PROJ
network access, which can fetch needed grid data from `cdn.proj.org`. For
reliable offline use, download the complete grid on an online machine beforehand:

```powershell
& ".\.venv\Scripts\python.exe" -m pyproj sync --file us_nga_egm08_25.tif
```

For a separate offline machine, locate the online machine's grid directory
with `& ".\.venv\Scripts\python.exe" -c "from pyproj.datadir import get_user_data_dir; print(get_user_data_dir())"`.
Transfer `us_nga_egm08_25.tif` into the same user data directory reported by
that command on the offline machine. Use `--no-proj-network` when publishing
there so a missing local grid is reported immediately.

Without the grid, a zero correction would leave the mesh's sea-surface depth
interpreted as an ellipsoidal height: its vertical position would be wrong by
the local geoid undulation. The code rejects unavailable or undefined geoid
transformations.

### 5.6 Nira publishing

Scripted Nira upload requires an Enterprise account and its API key and secret.
Clone the [official client](https://github.com/NiraOfficial/niraclient), configure
it with the same environment interpreter, then expose its checkout to WildScan:

```powershell
git clone https://github.com/NiraOfficial/niraclient.git "C:\tools\niraclient"
Push-Location -LiteralPath 'C:\tools\niraclient'
try {
    & 'C:\tools\wildscan\.venv\Scripts\python.exe' .\nira.py configure
} finally {
    Pop-Location
}
$env:NIRACLIENT_DIR = 'C:\tools\niraclient'
```

Adjust both checkout paths if you chose different locations. The configuration
command prompts for the API key and secret. The official client bundles its
dependencies, so it does not require a separate pip install.
[Official setup and dependency guidance](https://github.com/NiraOfficial/niraclient#quick-start).

`NIRACLIENT_DIR` is set for the current PowerShell session. Set it again in a
new terminal when using batch publishing; the single-service command also
accepts a quoted `--niraclient` path. Configuration does not upload an asset.

---

## 6. Run the pipeline

### 6.1 The workspace

A **workspace** is one folder per dive. Every stage writes into it — image
batches, aligned components, merged projects, models, exports and the JSON
report each stage leaves behind. Give the dive its own folder on a large
drive, for example `F:\na156_h2024`.

### 6.2 WildScan, the recommended way

From the checkout, without activation:

```powershell
& ".\.venv\Scripts\python.exe" -m wildscan "F:\na156_h2024"
```

After successful activation, `wildscan "F:\na156_h2024"` is equivalent. The
workspace argument is optional — without it, WildScan asks for the expedition,
dive and folder and remembers your answers.

On the first screen, enter the dataset locations and a separate results root.
Choose **View results** to inspect an existing workspace without processing,
or **Continue** to create or open the working results root and choose stages.
The nine stages are shown in order with a detected state and summary:

1. Extract Images
2. Georeference
3. Preprocess (CLAHE)
4. Batch into Zones
5. Align Zones
6. Merge Components
7. Generate Models
8. Export Deliverables
9. Publish (Cesium / Nira)

Select the needed stages, answer the parameter wizard, then review the source
paths, settings, and publishing notice before choosing **Run**. The app streams
logs and progress and launches the canonical drivers described below. It is
resume-aware: valid completion evidence keeps stages unselected; incomplete,
changed, or interrupted work may need a retry. Artifact checks differ by stage
and do not establish reconstruction quality. The supported processing target
is native Windows; see [Analyze a dataset](ANALYZE_A_DATASET.md) for the screen
sequence, inspection limits, and pauses between commands.

### 6.3 The stages on the command line

Each of these is what WildScan runs for you. Use them for scripting or
unattended runs.

**Extraction through per-zone alignment** — the interactive module chain
(Extract Images → Georeference Images → Preprocess Images → Batch Directory
→ RealityScan Alignment):

```powershell
& ".\.venv\Scripts\python.exe" main.py
```

It asks which modules to run, then prompts for each one's paths and
settings, offering your previous answers. A module that fails stops the
chain. For an unattended run, select modules by name instead:

```powershell
$env:RS_MODULES = "Georeference Images,Batch Directory"
$env:RS_NO_INTERACTIVE = "1"
& ".\.venv\Scripts\python.exe" main.py
```

`& ".\.venv\Scripts\python.exe" main.py --help` lists the flags for the enabled modules.

**Georeferencing on its own** uses
`& ".\.venv\Scripts\python.exe" georeference_survey.py`, whose
standalone workflow includes multiprocessing image copying. It asks for the image
folder, the ROV data folder and an output folder, or takes `--image-base-dir`,
`--rov-data-dir` and `--output-dir`.

Image-copy retries check existing destination content before reuse. A stale
destination is preserved and rejected. If any image copy fails for a dive,
the standalone command withholds that dive's new flight log and returns a
failed result; validated outputs for successful dives remain available.

**Merge the per-zone components, then model the merged result:**

```powershell
& ".\.venv\Scripts\python.exe" merge_zones.py --components_root "F:\na156_h2024\aligned_components" --images_root "F:\na156_h2024\batched_images_by_zone" --output "F:\na156_h2024\merged"
& ".\.venv\Scripts\python.exe" run_models.py --workspace "F:\na156_h2024"
```

`run_models.py` models every final component, smallest first, and is
scale-gated: a component whose measured metric scale falls outside the
accepted band is not modelled. Saved bands and measured values must be valid
finite numbers; malformed saved verdicts require remeasurement. Reruns reuse
successes only when the saved assembly, source components, navigation and
model recipe still match.

**Export deliverables** (OBJ by parts, FBX by parts, and an ultra-dense
coloured PLY):

```powershell
$workspace = 'F:\na156_h2024'
$report = Get-Content -Raw -LiteralPath "$workspace\merged\merge_report.json" | ConvertFrom-Json
$names = @($report.clusters.final_components | ForEach-Object { ($_.key -split '/')[-1] })
if ($names.Count -eq 0) { throw 'The merge report has no final components.' }
New-Item -ItemType Directory -Path "$workspace\exports" -Force | Out-Null
[System.IO.File]::WriteAllLines("$workspace\exports\components.names", [string[]]$names, [System.Text.UTF8Encoding]::new($false))
& ".\.venv\Scripts\python.exe" modules/export_deliverables.py --project "$workspace\merged\assembly\Merged.rsproj" --exports "$workspace\exports" --names "$workspace\exports\components.names"
```

---

## 7. Publish

Publish everything a workspace has exported, to whichever service you have
credentials for:

```powershell
$env:CESIUM_ION_TOKEN = "<your ion token>"
& ".\.venv\Scripts\python.exe" publish_batch.py --workspace "F:\na156_h2024" --prefix "NA156 H2024" --dry-run
```

Drop `--dry-run` to publish for real. Add `--components` to limit it to
named components.

One export at a time:

```powershell
& ".\.venv\Scripts\python.exe" publish_cesium.py --name "IN-401 hull" --dir "F:\na156_h2024\exports\COMPONENT\obj" --verify
& ".\.venv\Scripts\python.exe" publish_nira.py --name "IN-401 hull" --dir "F:\na156_h2024\exports\COMPONENT\obj" --niraclient "C:\tools\niraclient"
```

Replace `COMPONENT` with an exported component name. Cesium reads the CRS and
transform from the mesh's `.rsInfo` sidecar; pass `--flight-log` to add an
independent navigation-envelope check.

Notes:

- Cesium prepares local copies under `_cesium_local`. A nonempty folder
  without its `.cesium-stage.json` marker is preserved; pass `--staging`
  with a new directory in that case. Nira excludes these generated copies.
- Cesium defaults to the whole OBJ, and Nira defaults to its by-parts copy.
  Use `--parts whole` or `--parts split` on the single-service publishers to
  choose explicitly; Nira requires `--geometry` if the directory mixes mesh formats.
- Both services want the OBJ. Nira rejects PLY point clouds — LAS, LAZ or
  E57 only — and scripted upload needs an Enterprise API key.
- Always pass `--verify` on a real Cesium publish. It is not on by default,
  and without it the publisher stops at "tiling complete" — which is not
  evidence of anything. With it, the publisher reads the finished asset's
  own tileset back and confirms it landed at the right position and depth.
  ion reports COMPLETE for assets in the wrong hemisphere.

---

## 8. Where everything lands

Inside the workspace, each stage leaves a JSON report next to its outputs.
These are what WildScan reads to show stage status, and what the drivers
read to resume:

| File | Written by |
|---|---|
| `merge_report.json` | `merge_zones.py` |
| `grow_report.json` | `grow_zone.py` |
| `models_report.json` | `run_models.py` |
| `publish_report.json` | `publish_batch.py` |
| `publish_plan.json` | `publish_batch.py --dry-run` (preserves publication evidence) |
| `preprocessed_images.manifest.json` | Preprocess Images (settings, completion and source/output integrity) |
| `interrupted_stage.json` | Console runner after Stop; cleared by a successful retry of the affected stages |

Preprocessing verifies image contents before reusing existing output. This
adds reads of the source and processed images, which can take time on large
network drives. Changed sources or settings require regeneration.

Settings you typed live in `rs_settings.json` in the checkout root.

---

## 9. Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `wildscan` is not recognised | Use `& ".\.venv\Scripts\python.exe" -m wildscan` from the checkout, or activate successfully before using the shorter command. |
| Packages installed but Python cannot import them | A version-qualified `py` command installed into the global interpreter. Reinstall with `& ".\.venv\Scripts\python.exe" -m pip install -e ".[dev]"`. |
| `Activate.ps1` cannot be loaded | Activation is optional. Invoke `& ".\.venv\Scripts\python.exe"` directly; no execution-policy change is needed. |
| `import cv2` fails with a DLL load error | Check the Visual C++ runtime; Windows N/KN editions also need the Media Feature Pack. See the [OpenCV Windows FAQ](https://pypi.org/project/opencv-python/#frequently-asked-questions). |
| `RealityScan.exe not found` | Non-standard install. Set `realityscan.executable` in `rs_settings.json` or `RS_EXECUTABLE`. |
| Tests fail on a clean checkout | Inspect the failure and confirm the selected interpreter and dependencies. A code or fixture failure also needs fixing before running data. |
| A stage "succeeds" but produces nothing | RealityScan exits SUCCESS while doing nothing. Check the stage's census and report rather than the exit code; `docs/rs-reference/12-failure-modes-and-race-conditions.md` catalogues every known silent-success mode. |
| A run fails saying the geoid grid is missing | See [5.5](#55-cesium-publishing-the-geoid-grid). A missing grid means a wrong vertical position. |
| Two runs interfere with each other | Both used the same instance name. Give each its own `RS_INSTANCE` and `RS_GPU_DEVICES`. |
| Disk fills mid-run | RealityScan's cache. Point `RS_CACHE_DIR` at a large drive and flush between runs with the `FlushCache` workflow. |
| A run stops with "not found" for an input folder | The path prompt was accepted while empty or stale. Rerun and enter the real folder, or pass it as a flag. |
| A copy stage refuses an existing image | The destination content differs from its source. Preserve and inspect the previous output, or select a fresh output folder. |

---

## 10. Where to read further

- `README.md` — what each script and module does.
- `ARCHITECTURE.md` — architecture, hard rules, and the operating practices
  behind them.
- `docs/rs-reference/README.md` — the RealityScan 2.2 CLI manual, and the
  routing index that sends any RealityScan question to the right one of its
  14 documents in a single hop. Its "facts that silently destroy a run"
  table is worth reading before your first production run.
- `docs/WORKFLOW_WALKTHROUGH.md` — a plain-language end-to-end walkthrough
  of one real dive, from raw images to a final project.
