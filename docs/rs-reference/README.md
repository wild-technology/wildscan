# RealityScan 2.2 CLI reference — index

Wild Technologies' working reference for driving **RealityScan 2.2** (Epic Games; the product
formerly named RealityCapture) from its **command line**, headless and unattended. It is written
from RealityScan's own documentation — the offline Help, the CLI help and the schema files that
ship with the install — and extended by this project's testing: where the documentation is
silent, ambiguous or wrong, the entry records what was observed, when, and how.

**Scope note:** this is not Epic's documentation and may lag RealityScan releases; Epic's own
Help is authoritative for anything this reference does not mark as tested.

For Wild Scan installation and first-use workflow, start with
[Setup and run](../SETUP-AND-RUN.md) or [Analyze a dataset](../ANALYZE_A_DATASET.md).
This reference combines documented contracts with dated native observations;
check each entry's provenance and build scope before applying it to a new
installation. Historical measurements are not a current release-acceptance claim.

**Scope of the evidence.** Most empirical evidence cited here was gathered on
earlier datasets taken with other cameras (a four-camera underwater vehicle
rig and others), and the code it cites is the code of that time. The
repository now targets one rig only, the two-camera Sony ILX-LR1 stereo pair
recorded with Wild Sync; that rig is described in
[The ILX-LR1 stereo rig](../ILX-LR1.md) and in document 13, section 10. Facts
about RealityScan itself carry over; numbers measured on the earlier cameras
(registration rates, mount angles, accuracies that worked) do not, until they
are re-measured on ILX-LR1 data.

It combines two sources that do not otherwise exist in one place:

1. **Epic's shipped documentation** — the offline Help
   (`C:\Program Files\Epic Games\RealityScan_2.2\Help\en-US\`), plus the XML schema and format
   dictionaries shipped in the install tree (`flightlogs.xml`, `sensorsdb.xml`, `epsg.xml`,
   `calibration.xml`, `Settings\SimplifiedExport\*.xml`, …). Help topics are cited by path and
   paraphrased; command names, keys, token lists and option names are reproduced exactly
   because they are the interface.
2. **This project's testing** — production and test runs driving RealityScan headless over
   underwater photogrammetry at 8,000+ camera scale, and the working code in
   `modules/realityscan_interface/`. The test records themselves are not distributed; this
   reference is their distilled, self-contained form, so every citation states the fact it
   supports.

**Where the two disagree, both are recorded.** The documented claim, the observed behavior, and
how the observation was made are all stated, and the entry is tagged `[CONTRADICTED]`. Those
entries are the highest-value content in this set; none of them has been smoothed over. The tag
also appears on cross-references to a contradiction documented in full elsewhere.

Every document is self-contained enough to answer a question without loading its siblings, and
cross-references siblings by exact filename when the full treatment lives elsewhere.

---

## Provenance tags

Every non-obvious claim in this reference carries exactly one tag.

| Tag | Meaning | Citation form |
|---|---|---|
| `[OFFICIAL]` | Stated by the shipped Help / Epic docs | `[OFFICIAL: appbasics/allcommands]` — the Help topic path |
| `[VERIFIED]` | Established empirically by this project | `[VERIFIED: project testing 2026-07-23]`, `[VERIFIED: bug B5]` |
| `[CONTRADICTED]` | Docs say X, observation says Y — **both** stated, with how Y was observed | `[CONTRADICTED: probe D1/D2, 2026-07-24]` |
| `[UNDOCUMENTED]` | True behavior with no Help coverage at all; the entry says how it is known | `[UNDOCUMENTED: binary string extraction]` |
| `[INFERRED]` | Reasoned, not tested. The entry says what would settle it | `[INFERRED: absence of the literal string implies …]` |
| `[OPEN]` | Known unknown: the question plus the cheapest probe that answers it | `[OPEN, §18-Q9]` |

Compound forms appear where they carry information: `[SUPERSEDED]` marks a refuted claim that is
**retained** (deleting it guarantees rediscovering it), `[VERIFIED-as-decision]` marks a policy
choice rather than a measurement, `[VERIFIED-by-inspection]` marks a fact read off a file rather
than produced by a run.

**Aggregation rule: a claim built out of tagged facts keeps the WEAKEST tag.** A mean over
`[INFERRED]` values is `[INFERRED]`. A conclusion that chains one `[VERIFIED]` measurement to one
`[INFERRED]` step is `[INFERRED]`. Do not upgrade a tag because the claim sounds right.

### What the citations inside a tag mean

| Citation | Meaning |
|---|---|
| `project testing <date>` | an observation recorded in this project's test log on that date |
| `project test records, <date>` | a dated test plan, decision record or result file from this project's testing |
| `earlier multi-camera dataset, <date>` | evidence gathered while processing an earlier dataset taken with a different multi-camera rig |
| `dataset A` … `dataset D`, `an earlier survey site` | earlier datasets, labelled neutrally; the label only says which observations came from the same data |
| `stereo fixture <date>` | a small two-camera test set used to probe rig, pose and flight-log behaviour |
| `smoke fixture` | the small scene used for cheap probes before any full-size run |
| `bug B1`–`B11` | the numbered bugs mapped in [12-failure-modes-and-race-conditions.md](12-failure-modes-and-race-conditions.md) §14 |
| `cell U<n>`, `probe D<n>`, `PD-<n>`, `A<n>`, `Z<n>` | individual cells of this project's test matrices (alignment hardening, merge probes, priors and distortion, merge strategy, zone tests); each citing entry states what the cell varied |
| `[VERIFIED-by-inspection: <file>]` | read off a file in this repository or the install tree, not produced by a run |
| Help topic paths (`appbasics/allcommands`) | the topic's location under `Help\en-US\`, without the `.htm` suffix |

Tag counts across documents 01–13 at this revision (occurrences, not unique facts):

| `[OFFICIAL]` | `[VERIFIED]` | `[CONTRADICTED]` | `[UNDOCUMENTED]` | `[INFERRED]` | `[OPEN]` | `[SUPERSEDED]` |
|---:|---:|---:|---:|---:|---:|---:|
| 1210 | 1585 | 162 | 242 | 251 | 337 | 41 |

---

## Document map

One hop per question. Pick the row whose trigger matches, read that file.

| # | File | Read this when… |
|---|---|---|
| 01 | [01-cli-fundamentals.md](01-cli-fundamentals.md) | You need to *start* RealityScan and keep control of it: executable discovery, command-line grammar and quoting, startup switches, `-headless` vs `-hideUI`, named instances, `-delegateTo`, the `-waitCompleted` / `-getStatus` contract, verified shutdown, the `-writeProgress` file format, the `appProcessAction` completion hook, exit/result codes, `RealityScan.log`, licensing, multi-GPU pinning, cache/disk behavior. |
| 02 | [02-command-reference.md](02-command-reference.md) | You need the exact spelling, parameters, process ID and real behavior of **any** command. All 218 command names, grouped as the Help groups them, plus commands that do **not** exist, Help-prose-only and typo spellings, one command hidden inside an HTML comment in the Help source (`-undercut`), deprecated commands, commands unusable under delegation, and an alphabetical index. |
| 03 | [03-settings-keys.md](03-settings-keys.md) | You need a `-set` / `-preset` key: its type, default, allowed values, whether it needs a restart, whether it is dead or inert. 740 keys across four tiers, plus the quoting mechanics, the persistence rules, and the keys this pipeline pins in production and why. |
| 04 | [04-image-input-and-handling.md](04-image-input-and-handling.md) | You are getting images *into* a scene or selecting them: `-add`, `.imagelist`, `-addFolder` + `appIncSubdirs`, video/LiDAR/HDR/BLK3D import, image **identity is path identity**, selection commands, per-image controls, calibration and lens groups, image layers, masks, depth/normal/mask export, preprocessing (CLAHE) before import, copies vs hardlinks vs junctions. |
| 05 | [05-metadata-xmp-and-sidecars.md](05-metadata-xmp-and-sidecars.md) | You are reading or writing XMP sidecars: the `<stem>.xmp` naming contract, `_common.xmp`, the complete `xcr:` attribute set and value enumerations, prior semantics per mode, `-exportXMP` vs `-exportXMPForSelectedComponent` naming, the auto-import trap and the sidecar cleaning protocol, EXIF handling, `sensorsdb.xml`. |
| 06 | [06-georeferencing-flightlogs-and-scale.md](06-georeferencing-flightlogs-and-scale.md) | You are putting real-world position, orientation or **metric scale** into a scene: `-importFlightLog` / `-importTrajectory`, the log format, `flightlogs.xml` reader definitions, `FlightLogParams.xml` field by field, `authority:id` coordinate systems, GCPs, control points, markers, distance constraints, `-update`, scale as an automated acceptance oracle, and **the VERTICAL datum** — why the exported Z is a sea-surface depth and not an ellipsoidal height, and the geoid correction that separates them. |
| 07 | [07-alignment.md](07-alignment.md) | You are running or tuning alignment: `-align` / `-draft` / `-detectFeatures` / `-update`, every `sfm*` and `lis*` key with its measured effect, `AlignmentParams.xml` field by field, `-editInputSelection` / `inp*` per-image controls, what priors do to a solve, component control during alignment, how to judge a bad solve, and a tuning playbook. |
| 08 | [08-components-and-merge.md](08-components-and-merge.md) | You are working with components or trying to merge them: what a component is, every component command, the `.rsalign` file, the `.complist` input file, **merge semantics** (what actually fuses, what silently does not), the verification protocol, and merge strategy at 8,000+ camera scale. |
| 09 | [09-xml-parameter-files.md](09-xml-parameter-files.md) | You are writing, editing or debugging a `params.xml`: which commands consume one, which silently ignore one, the `<Configuration>` schema per profile type, `.rsortho` / `.rsbox` / `.rsinfo` / `.rcconfig` — including the `.rsInfo` `<Model>` tag and how to decode `transformToModel`, the only on-disk record of what frame an export landed in — the install-tree format dictionaries, this repository's profiles as worked examples, and an authoring guide. |
| 10 | [10-reconstruction-texturing-export.md](10-reconstruction-texturing-export.md) | You are downstream of a solved alignment: reconstruction region, mesh quality tiers, depth-map controls, model naming and lifecycle, cleaning/filtering/holes/simplify/smooth, classification and DTM, colorization vs texturing, unwrapping and the texture budget, reprojection, every export command and its profile, LoD/3D Tiles, ortho/DSM/DTM/contours, publishing targets, reports. |
| 11 | [11-automation-patterns.md](11-automation-patterns.md) | You are building the harness: persistent instances, the canonical `:run` subroutine, the ErrorWriter completion trigger, marker-file ownership, crossing the cmd/`.bat` data boundary, multi-GPU isolation, progress and stall monitoring, checkpoint/rollback, census-based verification, log snapshotting, four end-to-end recipes, and an anti-pattern list. |
| 12 | [12-failure-modes-and-race-conditions.md](12-failure-modes-and-race-conditions.md) | Something failed, hung, or "succeeded" without doing anything. 88 numbered entries `F-01`…`F-88` (symptom → cause → how detected → mitigation → detection test), the complete exit-code / result-code / `err:NNNN` / process-ID tables, the `B1`–`B11` bug map, and an ordered diagnostic playbook keyed by symptom. |
| 13 | [13-camera-rigs-priors-and-orientation.md](13-camera-rigs-priors-and-orientation.md) | You are telling RealityScan where a camera is and which way it points: the prior model, XMP as the per-image channel, `xcr:Rig` / `RigInstance` / `RigPoseIndex`, calibration vs distortion groups, distortion models and their coefficient vectors, rotation conventions, prior strength/accuracy/composition, every coordinate frame in play and the transforms between them, applied to the two-camera ILX-LR1 stereo rig this repository targets. |

**Routing shortcuts.**
Command spelling or parameters → 02. Key name, type or default → 03. "Why did it not fail
loudly?" → 12. "How do I build the loop that runs it?" → 11. "What file do I pass as
`params.xml`?" → 09.

---

## Fast path: the facts that silently destroy a run

Each of these produces a **clean exit status** and no error while doing the wrong thing — or
nothing at all. They are the reason this reference exists. Read the linked section before doing
the thing in column 1.

| If you are about to… | Read | Do NOT | Tag |
|---|---|---|---|
| Publish a georeferenced mesh to Cesium ion | [10-reconstruction-texturing-export.md](10-reconstruction-texturing-export.md) §17.2.1 | Do not hand Cesium the exported Z. It is a depth below the **sea surface** (the georeference stage writes `-abs(depth)`), while Cesium reads every height as above the **WGS84 ellipsoid**, and the project CRS is 2D so nothing declares which. The asset sinks or floats by the geoid undulation — **+72.69 m** at an earlier survey site, +70.4 m Solomon Sea, −27.1 m Gulf of Mexico. Convert with `h = −depth + N` (`modules/cesium_placement.py`) and confirm by decoding the finished tileset, never by the upload's exit status. ion itself is blameless: it honours a negative height to **−0.000 m** | `[VERIFIED: probe asset 5171554, project testing 2026-08-31]` |
| Compute a geoid correction with PROJ | `10-…` §17.2.1 | Never call `Transformer.from_crs` without `allow_ballpark=False`. With the grid absent PROJ **succeeds and returns Z unchanged**, having silently chosen a "ballpark vertical transformation" — the correction reads as applied and is zero. The EGM2008 grid (`us_nga_egm08_25.tif`, ~80 MB) needs `PROJ_NETWORK=ON` or a local grid installed with `python -m pyproj sync --file us_nga_egm08_25.tif` | `[VERIFIED: project testing 2026-08-31]` |
| Conclude that a merge worked | [08-components-and-merge.md](08-components-and-merge.md) §6; [12-failure-modes-and-race-conditions.md](12-failure-modes-and-race-conditions.md) §1 | Do not read exit status, `errors.txt` emptiness, or "completed" as evidence. `-mergeComponents` exits **SUCCESS** and leaves the components separate under every flag combination when nothing can fuse. The verdict is a **camera census** of the resulting component, never the status | `[VERIFIED: earlier multi-camera dataset, 2026-07; project testing 2026-07-23/24]` |
| Plan which components will fuse | [08-components-and-merge.md](08-components-and-merge.md) §5.2 | Do not assume georeference or flags can fuse components with **zero image-content overlap** — nothing does, silently. The governing rule is **content overlap**, not path identity: shared camera paths are *sufficient but not necessary* (probe D7 fused two components with zero shared basenames and zero shared paths). The older "components fuse only through shared image identity" rule is retained as `[SUPERSEDED]`, not deleted. `sfmMergeGeoreferencedComponents=true`, whose documented purpose is exactly overlap-free merging, has **never** been observed to work headless | `[VERIFIED: D7 probe wave 2026-07-24]` + `[CONTRADICTED: probe D1/D2]` |
| Run `-importComponent` | [08-components-and-merge.md](08-components-and-merge.md) §3.3; `12-…` `F-36` | Never import a `.rsalign` from anywhere except its **original export location**. A relocated copy does not fail — it hangs the instance permanently in `#timeout` (≥6 h observed, no error, no minidump). Pass a `.complist` of in-place paths | `[VERIFIED: bug B1]` `[UNDOCUMENTED]` |
| Pass alignment settings to `-align` | [07-alignment.md](07-alignment.md) §2 | `-align` takes **no parameters** in 2.x. A `params.xml` argument is accepted on the command line and **silently ignored**. Push every `sfm*` / `lis*` key through `-set "key=value"` *before* the align | `[VERIFIED: project testing 2026-07-21]` |
| Trust that a flight log's orientation priors arrived | [06-georeferencing-flightlogs-and-scale.md](06-georeferencing-flightlogs-and-scale.md) §2.3 | Do not assume `gpsLogFileFormat` resolved. If that GUID is **not present in the installed `flightlogs.xml`**, RealityScan imports **position only**, drops every orientation and accuracy column, and still returns **exit code 0** with no warning. Check the saved `.rsproj`: `absPrior="registered"` and missing `absu*` means the format did not resolve; `absPrior="pose"` with `absu*` present means it did. Verify the params GUID and the installed GUID **match each other** | `[VERIFIED: stereo fixture 2026-08-23]` |
| Put `FocalLength` in a flight log | `06-…` §2.4 | It is in the documented variable vocabulary, imports without error, and is **never stored** — no `FocalLength35mm` / `FocalPrior` reaches the project and the solve ignores it. Per-image intrinsics must go through XMP sidecars | `[CONTRADICTED: stereo fixture 2026-08-23]` |
| Declare a camera rig via `xcr:Rig` in XMP | [13-camera-rigs-priors-and-orientation.md](13-camera-rigs-priors-and-orientation.md) §3.5 | The documented sidecar attributes are **not sufficient**. `-add` then demands `rig<GUID>.rcrx` beside the images and the run dies (`0 registered`). No `.rcrx` ships and the extension is in no Help topic. To transport an external solve, supply **poses** instead and omit `xcr:Rig` entirely | `[VERIFIED: stereo fixture 2026-08-23, 2/2]` |
| Attribute an alignment difference to a setting | `13-…` §4.4 | Do not, without a replicate. Bit-identical reruns on marginal geometry gave **26 vs 55** and **76 vs 61** registered; that within-configuration spread covered a third to a half of the range across thirteen different configurations. `-align` exposes no seed. Run one repeat **before** ranking anything | `[VERIFIED: stereo fixture 2026-08-23]` |
| Parse `xcr:Rotation` out of a sidecar | [05-metadata-xmp-and-sidecars.md](05-metadata-xmp-and-sidecars.md) §2.3 | Do not match the attribute form only. RealityScan 2.2 writes **both** `Position` and `Rotation` as child **elements** (`xcr:Version="4"`), whereas Epic's sample writes `Rotation` as an **attribute** (and older exports wrote `Position` as one). Match both forms for both, or read zero rotations silently | `[CONTRADICTED: stereo fixture 2026-08-23]` |
| Pass a `key=value` through a `.bat` | [11-automation-patterns.md](11-automation-patterns.md) §5; [03-settings-keys.md](03-settings-keys.md) §1.2 | Never pass unquoted delimited data (`=` `;` `,`) as a `.bat` argument: cmd splits it, Python `subprocess` only quotes on whitespace, and RealityScan logs `err:7155 Parsing setting … failed` while applying **nothing**. Quote the whole pair (`-set "key=value"`); cross the `.bat` boundary as `key:value` and convert inside the workflow; cross list data as a **file** (`.complist` / `.imagelist`) | `[VERIFIED: bug B5]` |
| Write a calibration or pose sidecar | [05-metadata-xmp-and-sidecars.md](05-metadata-xmp-and-sidecars.md) §1.1 | Never name it `image.jpg.xmp`. RealityScan binds `<stem>.xmp` **in the same folder** and ignores anything else **silently** — no warning, no log line, no code. Every calibration prior this repo wrote before 2026-07-22 was never loaded. Invariant to check: `count(*.xmp) == count(images)` | `[VERIFIED: bug B7]` |
| Start a run that must be prior-free | [05-metadata-xmp-and-sidecars.md](05-metadata-xmp-and-sidecars.md) §10 | Do not leave old sidecars beside the images. `-add` / `-addFolder` **auto-import `<stem>.xmp` as pose and calibration priors**, so a previous run's exported poses become this run's inputs and the run silently grades its own homework. Clean the tree first (`calibration_sidecars.sanitize_and_census`) | `[VERIFIED: bug B7]` |
| Lay out an image tree | [04-image-input-and-handling.md](04-image-input-and-handling.md) §12.3; `12-…` `F-57` | Never put a **directory junction** (or any reparse point) on the path a scene's images resolve through. RealityScan then writes **zero** XMP sidecars and reports success (`Exporting Registration completed in 8.758 seconds`, nothing on disk). Compounding it, PowerShell 5.1 `Get-ChildItem -Recurse` does not descend into junction *children*. Use real directories of **hardlinked** images with **copied** sidecars. Cost when learned: 5 h 12 m of correct GPU work discarded | `[VERIFIED: project testing 2026-07-27/28]` `[UNDOCUMENTED]` |
| Edit or generate a workflow `.bat` | `12-…` `F-62`; [11-automation-patterns.md](11-automation-patterns.md) §2 | Do not save it LF. cmd's `call :label` search is byte-offset sensitive: the same `call :run` resolved ten times and then failed with `The system cannot find the batch label specified - run`. All `.bat` and `.vbs` must be **CRLF**; re-verify after any scripted edit (e.g. "342 CRLF, 0 bare LF") | `[VERIFIED: project testing 2026-07-24]` |
| Wait for a delegated command | [01-cli-fundamentals.md](01-cli-fundamentals.md) §6; [11-automation-patterns.md](11-automation-patterns.md) §2 | Do not trust a single `-waitCompleted`. Delegated commands are **queued**, and `-waitCompleted` issued before the instance picks the command up **returns immediately**, so the next command runs against a busy instance. Use the `:run` shape: delegate → grace delay → `-waitCompleted` → grace → `-waitCompleted` → check the errors marker | `[CONTRADICTED: earlier multi-camera dataset, 2026-07]` |
| Diagnose any ambiguous failure | `12-…` §2 and `F-39`/`F-40` | Do not boot another instance first. `%LOCALAPPDATA%\Temp\RealityScan.log` is **global and truncated on every instance boot**, and it is the only place the real reason behind the generic `0x8000FFFF` exists. Snapshot it inside the driver, immediately after the failing call returns — and validate the snapshot, because a snapshot taken while two instances ran spliced two runs together | `[VERIFIED: bug B6; project testing 2026-07-27]` |
| Export anything (XMP, components, models) | [04-image-input-and-handling.md](04-image-input-and-handling.md) §4; [02-command-reference.md](02-command-reference.md) `-exportXMP` | Three silent-zero traps in one step: (a) `appIncSubdirs` defaults **false**, so `-addFolder` over a per-camera tree adds **0 images** and the whole zone "succeeds" in 25 s; (b) `-setMinComponentSize` defaults **5**, silently excluding smaller components from export *and* selection — set it to `1`; (c) exports are **selection-driven** and `-importFlightLog` leaves images actively selected, so under `-silent` an export finishes in 0.057 s having written nothing — `-deselectAllImages` first | `[VERIFIED: project testing 2026-07-23; project testing 2026-07-21]` |

---

## Coverage

| Namespace | Documented | Total known | Where |
|---|---:|---:|---|
| `RealityScan.exe` command names (incl. Help-prose-only, hidden, undocumented, and proven-nonexistent) | 218 | 218 | 02, cross-referenced from 04/06/07/08/10 |
| `-set` keys documented in the Help (tier 1) | 100 | 100 | 03 §3–§7 |
| Keys with a concrete `<Configuration>` XML source (tier 2) | 106 | 106 | 03 §8, 09 |
| Key-shaped identifiers in the 2.2 binary (tier 3, candidate namespace) | 468 | 468 | 03 §14 |
| Selection-edit keys — `-editInputSelection` and siblings (tier 4) | 68 | 68 | 03 §9, 04 §15, 07 §5, 13 |
| **Settings namespace total** | **742** | **742** | |

`RSNode.exe`'s three flags (`-hostAddress`, `-port`, `-landingPage`) are documented in
[02-command-reference.md](02-command-reference.md) §17 and are deliberately not counted as `RealityScan.exe` commands.

Inclusion in tier 3 is **not** proof that `-set` accepts the key — it is a binary string, tagged
accordingly.

---

## Conventions

- The product is **RealityScan** / RS throughout. `RealityCapture` appears only where a literal
  string genuinely contains it: current API identifiers (`reader="RealityScan.Import.CSVFlightLog"`
  is the *current* name), legacy-but-readable extensions (`.rcalign`, `.rcproj`), and the dead
  `RealityCapture*` settings keys, which are documented **as dead**.
- Identifiers — commands, keys, XML attributes, enum values, numeric defaults — are reproduced
  **exactly**. Case matters. Nothing is paraphrased.
- Examples are Windows and runnable. Paths typed into cmd/`.bat` use backslashes and the real
  shapes used in production (`F:\survey\...`, `C:\Program Files\Epic Games\RealityScan_2.2\`),
  not `path/to/thing`.
- Section references inside a document are `§N.M`; references to another document give its exact
  filename. Failure entries are cited as `F-nn` (document 12). Open questions are cited as
  `O-nn` or `§N-Qn` in the document that owns them.

## Sources

| Class | Location |
|---|---|
| Shipped Help (HTML) | `C:\Program Files\Epic Games\RealityScan_2.2\Help\en-US\**` (408 files) |
| Install-tree schemas | `C:\Program Files\Epic Games\RealityScan_2.2\*.xml`, `…\Settings\SimplifiedExport\*.xml` |
| Project testing | this project's dated test log, test matrices, decision records and result files (not distributed), cited as described under [Provenance tags](#provenance-tags) |
| Working code | `modules/realityscan_interface/realityscan_cli.py`, `modules/realityscan_interface/RS_CLI/Scripts/*.bat`, `RS_CLI/Metadata/*.xml`, `modules/camera_registry.py`, `modules/calibration_sidecars.py`, `modules/flight_logs.py`, `merge_zones.py`, `poses_to_flight_log.py` |
| Architecture, hard rules, operating practices | `ARCHITECTURE.md` |

Current Python checks live in `tests/`; manual validation tools live under
`scripts/validation/`. Code citations describe the code at the revision noted below; where an
entry describes a workflow that has since been removed, it says so, and the RealityScan
behaviour it records still holds.

---

## Version and provenance

| | |
|---|---|
| Product | RealityScan 2.2 (Epic Games) |
| Build measured | `RealityScan.exe` FileVersion `2.2.0.119430.RS`, ProductVersion `2.2.0.119430`, installed at `C:\Program Files\Epic Games\RealityScan_2.2\` |
| Help build | The offline Help shipped with that install — `Help\en-US\`, 408 files, newest file dated 2026-07-21. 153 topics were converted to plain text and read for this reference |
| Empirical record | Project testing through 2026-08-31, on earlier datasets and on a two-camera stereo fixture; revised against Wild Scan 1.0.0 (2026-08-31) and the current two-camera ILX-LR1 configuration (2026-10) |
| Platform | Windows 11, native (no WSL), multi-GPU CUDA, cmd/`.bat`/PowerShell substrate |
| Written | 2026-08-04; revised 2026-09-21 and 2026-10-07 |

Facts that hold only for a particular build say so. Everything unqualified is RealityScan 2.2.
