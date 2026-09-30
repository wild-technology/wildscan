# Setup and run — wildscan

A step-by-step guide to installing wildscan on a new machine and running a
dive through it. Follow the parts in order the first time. Later sessions
start at [part 6](#6-run-the-pipeline).

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
| Python 3.12 or 3.13 | From [python.org](https://www.python.org/downloads/windows/), which installs the `py` launcher. 3.12 is the hard floor: `numpy>=2.5` and `scipy>=1.18` do not install on anything older. |
| Git for Windows | [git-scm.com](https://git-scm.com/download/win) |

**Accounts, only for the stages you use**

- **Cesium ion** — an access token, for publishing to ion.
- **Nira** — an Enterprise plan and a local `niraclient` checkout, for
  publishing to Nira.

**Your data**

- The dive imagery.
- The ROV navigation table (the Kalman CSV) covering the dive, used to
  georeference the images.

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

   `py --list` should show 3.13 (or 3.12).

### 2.1 Flight log import format

Close RealityScan before editing its installation dictionary. Back up
`flightlogs.xml` beside `RealityScan.exe`, then copy the custom `<format>`
element from the repository's root `flightlogs.xml`, with ID
`{B438A617-2434-5A24-C1B7-58980F28345A}`, inside
the installed `<FlightLogs>` element. Preserve the other installed formats;
do not replace the installation dictionary with the repository's older copy.
Editing a file under Program Files may require administrator access.

The ID must match `gpsLogFileFormat` in both
`modules/realityscan_interface/RS_CLI/Metadata/FlightLogParams.xml` and
`FlightLogParamsLocal.xml`. A missing or mismatched format can import positions
while dropping orientation and accuracy without an error. Before processing
a dive on a new installation, import a small flight-log fixture and inspect
the saved project for the intended position, orientation and accuracy priors.
See [the flight-log reference](rs-reference/06-georeferencing-flightlogs-and-scale.md#resolved-2026-08-23--side-a-holds-what-an-unresolvable-format-guid-actually-does).

---

## 3. Get the code

Clone into a path **without spaces**. A checkout path containing spaces has
broken runs in the past.

```powershell
cd C:\tools
git clone https://github.com/wild-technology/wildscan.git
cd wildscan
```

---

## 4. Create the environment and install

Install into a virtual environment, so wildscan's processing dependencies stay
separate from the rest of the machine.

```powershell
py -3.13 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
```

Your prompt now starts with `(.venv)`.

- **Only Python 3.12 installed?** Use `py -3.12 -m venv .venv`.
- **Command Prompt instead of PowerShell?** Activate with
  `.venv\Scripts\activate.bat`.
- **PowerShell refuses to run `Activate.ps1`?** Allow local scripts once for
  your account, then activate again:

  ```powershell
  Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
  ```

> **Two rules that save a lot of confusion.**
> **Use `python`, not `py -3.13`, once the environment is active.** A `py`
> launch with a version flag ignores the active environment and runs the
> global interpreter, so your packages appear to vanish.
> **Activate the environment in every new terminal** (`.venv\Scripts\Activate.ps1`
> from the checkout folder).

The install must stay **editable** (`-e`). The app runs the driver scripts
and the RealityScan `.bat` workflows out of the checkout itself.

**Verify before touching real data:**

```powershell
python -m pytest
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
| `RS_HEADLESS` | `0` launches a visible RealityScan instead of headless. |
| `CESIUM_ION_TOKEN` | Cesium ion access token, used by the publishers. |
| `NIRACLIENT_DIR` | Path to your `niraclient` checkout, for Nira publishing. |
| `RS_MODULES` | Comma-separated module names to run non-interactively in `main.py`. |
| `RS_NO_INTERACTIVE` | `1` never prompts — required values must come from flags or stored settings. |

**Running two zones at once:** give each run its own instance name and GPU
set (`RS_INSTANCE=RS_GPU0` with `RS_GPU_DEVICES=0`, and a second pair for
the other GPU). A per-instance lock makes a same-instance collision fail
immediately rather than corrupt both runs.

### 5.4 Cesium publishing: the geoid grid

Cesium publishing converts depths below the sea surface into ellipsoidal
heights using the EGM2008 geoid grid (~80 MB). `publish_cesium.py`
downloads it from `cdn.proj.org` the first time it runs. On a machine with
no internet, download it on an online machine beforehand:

```powershell
python -m pyproj sync --file us_nga_egm08_25.tif
```

For a separate offline machine, locate the online machine's grid directory
with `python -c "from pyproj.datadir import get_user_data_dir; print(get_user_data_dir())"`.
Transfer `us_nga_egm08_25.tif` into the same user data directory reported by
that command on the offline machine. Use `--no-proj-network` when publishing
there so a missing local grid is reported immediately.

This matters: without the grid, PROJ would silently apply a zero correction
and your model would sit at sea level. The code refuses to continue rather
than publish a wrong position.

---

## 6. Run the pipeline

### 6.1 The workspace

A **workspace** is one folder per dive. Every stage writes into it — image
batches, aligned components, merged projects, models, exports and the JSON
report each stage leaves behind. Give the dive its own folder on a large
drive, for example `F:\na156_h2024`.

### 6.2 WildScan, the recommended way

With the environment active:

```powershell
wildscan F:\na156_h2024
```

`python -m wildscan F:\na156_h2024` is the same thing. The workspace
argument is optional — without it, WildScan asks for the expedition, dive
and folder and remembers your answers.

WildScan surveys the workspace and shows the nine stages in order, each
marked done, partial or pending:

1. Extract Images
2. Georeference
3. Preprocess (CLAHE)
4. Batch into Zones
5. Align Zones
6. Merge Components
7. Generate Models
8. Export Deliverables
9. Publish (Cesium / Nira)

Pick a stage and WildScan shows the exact command, settings and estimate
before anything runs, then streams progress as it goes. It always launches
the same drivers described below, so you can move between the two freely. It
is resume-aware: reopen the workspace later and stages with valid reports and
matching inputs stay marked done. Changed inputs or interrupted work require
a retry.

RealityScan stages need Windows. On macOS or Linux the app still opens a
workspace for inspection, exports review and publishing.

### 6.3 The stages on the command line

Each of these is what WildScan runs for you. Use them for scripting or
unattended runs.

**Extraction through per-zone alignment** — the interactive module chain
(Extract Images → Georeference Images → Preprocess Images → Batch Directory
→ RealityScan Alignment):

```powershell
python main.py
```

It asks which modules to run, then prompts for each one's paths and
settings, offering your previous answers. A module that fails stops the
chain. For an unattended run, select modules by name instead:

```powershell
$env:RS_MODULES = "Georeference Images,Batch Directory"
$env:RS_NO_INTERACTIVE = "1"
python main.py
```

`python main.py --help` lists the flags for the enabled modules.

**Georeferencing on its own** uses `python georeference_survey.py`, whose
standalone workflow includes multiprocessing image copying. It asks for the image
folder, the ROV data folder and an output folder, or takes `--image-base-dir`,
`--rov-data-dir` and `--output-dir`.

Image-copy retries check existing destination content before reuse. A stale
destination is preserved and rejected. If any image copy fails for a dive,
the standalone command withholds that dive's new flight log and returns a
failed result; validated outputs for successful dives remain available.

**Merge the per-zone components, then model the merged result:**

```powershell
python merge_zones.py --components_root F:\na156_h2024\aligned_components --images_root F:\na156_h2024\batched_images_by_zone --output F:\na156_h2024\merged
python run_models.py --workspace F:\na156_h2024
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
python modules/export_deliverables.py --project "$workspace\merged\assembly\Merged.rsproj" --exports "$workspace\exports" --names "$workspace\exports\components.names"
```

---

## 7. Publish

Publish everything a workspace has exported, to whichever service you have
credentials for:

```powershell
$env:CESIUM_ION_TOKEN = "<your ion token>"
python publish_batch.py --workspace F:\na156_h2024 --prefix "NA156 H2024" --dry-run
```

Drop `--dry-run` to publish for real. Add `--components` to limit it to
named components.

One export at a time:

```powershell
python publish_cesium.py --name "IN-401 hull" --dir "F:\na156_h2024\exports\COMPONENT\obj" --verify
python publish_nira.py --name "IN-401 hull" --dir "F:\na156_h2024\exports\COMPONENT\obj" --niraclient C:\tools\niraclient
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
| `wildscan` is not recognised | The environment is not active. Run `.venv\Scripts\Activate.ps1` from the checkout. |
| Packages installed but Python cannot import them | A `py -3.13` command was used inside the active environment, which installed into the global interpreter. Reinstall with `python -m pip install -e ".[dev]"`. |
| `Activate.ps1` cannot be loaded | PowerShell execution policy. Run `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`, then activate again. |
| `RealityScan.exe not found` | Non-standard install. Set `realityscan.executable` in `rs_settings.json` or `RS_EXECUTABLE`. |
| Tests fail on a clean checkout | Inspect the failure and confirm the selected interpreter and dependencies. A code or fixture failure also needs fixing before running data. |
| A stage "succeeds" but produces nothing | RealityScan exits SUCCESS while doing nothing. Check the stage's census and report rather than the exit code; `docs/rs-reference/12-failure-modes-and-race-conditions.md` catalogues every known silent-success mode. |
| A run fails saying the geoid grid is missing | See [5.4](#54-cesium-publishing-the-geoid-grid). Do not work around it — a missing grid means a wrong depth. |
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
