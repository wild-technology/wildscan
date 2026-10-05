# Analyze a dataset

This guide takes a new ROV survey from input review to a processing plan. It
also explains how to inspect an existing WildScan results folder without
starting a run. Install the Git checkout and virtual environment first using
[Setup and run](SETUP-AND-RUN.md).

Screenshots show the actual interface using a labelled sample of 24 still
images and one navigation CSV. Its results folder is empty; no native
processing or publishing has run.

## Before opening WildScan

Keep the delivered data intact and choose a separate working results folder.
Have the following available:

| Input | Check |
|---|---|
| Still images | Filenames retain their camera and time identity; choose a folder containing the images you intend to process |
| Survey video, if extracting images | The extractor accepts `.mp4` and `.mov`; filenames need a supported UTC timestamp and split recordings need the earlier parts |
| Navigation | A Kalman CSV covering the imagery, with the required case-sensitive columns and UTC timestamps |
| Existing flight log, if already georeferenced | Confirm its image names or paths, coordinate frame, units, and matching imagery |
| Results folder | Separate from source data, with enough space for prepared images, zones, projects, exports, and the RealityScan cache |

See [video and navigation formats](SETUP-AND-RUN.md#your-data) for the exact
contracts. Decode a small video sample and check the navigation time coverage
before a long run. The installed OpenCV package supplies video decoding;
WildScan does not require a separate `ffmpeg.exe`.

Intake detection lists candidate recordings and navigation files. It does
not decode the recordings or validate CSV contents. Camera identification
uses filename families and samples up to 200 image names per directory;
those camera counts are a sample, not a complete inventory or calibration
measurement.

## Open your dataset

From the checkout in PowerShell:

```powershell
& ".\.venv\Scripts\python.exe" -m wildscan
```

To reuse an existing results folder:

```powershell
& ".\.venv\Scripts\python.exe" -m wildscan "D:\survey results\dive_1"
```

On **Choose your dataset**, fill in only the sources you have:

| Field | What to enter |
|---|---|
| Expedition / Dive | Labels for this survey; review values remembered from the previous session |
| Dive folder | The delivered folder containing recordings and navigation candidates |
| Images folder | Existing still images; this takes precedence over detected image folders |
| Survey video | One specific recording; this takes precedence over detected recordings |
| Processed navigation folder | Existing ROVDataConcat datatables or georeferenced flight logs |
| Results root | The working workspace for this dive, or an existing workspace to inspect |

Read the detected-file summary and correct the fields if it identifies the
wrong recording or table. A suggested file is a convenience, not an approval
of that input. You will review the stage parameters before execution.

For an existing workspace, choose **View results** to open the pipeline and
component tables. This action reads the saved artifacts and does not launch
processing or create a missing results folder. Press **Escape** to return.

For a new plan, choose **Continue**. This validates supplied source paths,
refreshes detection, and creates the results root if needed. It does not
start the processing drivers.

## Choose the work to run

The stage picker shows the detected state and summary for each stage. Use
the arrow keys to move, **Space** to select or unselect a stage, and **Enter**
to confirm. Completed stages start unselected. If you supply stills without
a video, extraction also starts unselected.

| Stage | Purpose |
|---|---|
| Extract Images | Decode survey video and select frames |
| Georeference | Match imagery to ROV navigation and write a RealityScan flight log |
| Preprocess (CLAHE) | Create contrast-enhanced image copies |
| Batch into Zones | Organize overlapping spatial zones and their flight logs |
| Align Zones | Run RealityScan alignment and harvest component identity evidence |
| Merge Components | Combine compatible per-zone components through recorded attempts |
| Generate Models | Check the recorded metric-scale verdict and reconstruct accepted components |
| Export Deliverables | Export component geometry and textures |
| Publish (Cesium / Nira) | Upload exported components to configured destinations, or write a preview plan when neither destination is configured |

Select only the work for which you have inputs. For example, existing stills
do not need extraction; an already prepared zone tree may only need alignment
and later stages. A selected downstream stage does not automatically generate
missing upstream data. Check the wizard's input locations when resuming part
of a workflow.

The wizard asks one question at a time. Freshly detected paths supply defaults
before remembered answers and module defaults. Confirm that every default
belongs to this dive. An unrecognized camera prefix prompts for identification
notes. Lens and mount notes are optional records, not installed calibration
or measured navigation priors; entering them does not extend the runtime
camera registry. Resolve unknown camera conventions before relying on camera
orientation or offsets.

For alignment alone, the current workspace's prepared zone tree supplies the
input default when present. A blank **Flight Log Path** lets alignment discover
the input folder's log or each zone's own log. Enter an explicit file when
needed. Without a matching log, alignment runs without navigation priors;
that does not establish metric scale or geographic placement.

## Review and run

On **Review your run**, check the source folders, results root, selected
stages, and parameter answers. Check the publishing notice: configured
destinations mean real uploads. Leave publishing unselected until the exports
and account configuration are ready.

Leave **Continue automatically between stages?** set to `false` for a first
run. Preparation through alignment runs as one module-chain command; pauses
occur between that command and the merge, model, export, and publish commands,
not between every preparation module. Choose **Run** when the plan is ready.

Alignment writes, moves, or replaces XMP sidecars in its image input tree.
Run against prepared working images rather than irreplaceable originals.
Before native alignment, complete the
[flight-log format setup](SETUP-AND-RUN.md#54-flight-log-import-format) and
confirm your RealityScan instance and cache settings. Begin with a small
working sample to check the installation and dataset conventions.

During execution, the run screen streams driver logs and progress. Use
**Continue** between commands. A failed or stopped command requires a retry;
it does not advance automatically. **Stop stage** requests cancellation of
the process tree and records interrupted work. Native cancellation behavior
still needs validation on the installation. When no command is running,
**Escape** opens status.

## Interpret the results

Workspace status describes the available artifacts, not the scientific
quality of the reconstruction:

| State | Meaning |
|---|---|
| `pending` | The expected stage evidence is absent |
| `partial` | Some evidence exists, but the completion checks are not satisfied; changed inputs, failed work, and incomplete legacy reports can need a retry |
| `blocked` | A required prerequisite is missing or the recorded evidence prevents proceeding |
| `done` | The stage's artifact and report checks are satisfied |

Resume checks differ by stage. They check saved input identities and relevant
file metadata; opening status does not hash all imagery or rerun the native
workflow. Retained legacy reports can help explain a workspace without
proving that its current inputs are complete.

The **Final components** table displays camera counts, recorded scale
verdicts, model completion, and available export formats. A dash means the
corresponding value is unavailable. A passing scale result does not measure
absolute positional accuracy, surface detail, seams, or texture quality.
Review those separately before delivering or publishing a model.

In this sample, the sources have been identified but nothing has been processed.
The status table describes the results folder, so its stages remain pending.

The usual workspace layout is:

```text
dive_1/
  raw_images/                extracted or georeferenced working imagery
  preprocessed_images/       CLAHE image copies
  batched_images_by_zone/    zone inputs and flight logs
  aligned_components/       per-zone components and identity evidence
  merged/                   merge reports and assembly projects
  exports/                  per-component geometry and textures
  RC_projects/              dated project copies
  logs/                     driver logs
```

Keep the reports and project files with the exports. For scripted operation,
see [the command-line stages](SETUP-AND-RUN.md#63-the-stages-on-the-command-line).
For interpretation of flight logs, scale, and coordinate frames, use the
[RealityScan reference](rs-reference/README.md).
