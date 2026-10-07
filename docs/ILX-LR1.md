# The ILX-LR1 stereo rig

Wild Scan processes imagery from one rig: two Sony ILX-LR1 cameras mounted as a
stereo pair and recorded with Wild Sync. This document describes the rig as the
pipeline models it, the calibration reference data and the exact conditions
under which it is used, the Wild Sync input, the orientation convention, the
distortion model and the preprocessing.

Every value below is read from `modules/cameras.json`,
`calibration/ilx_lr1_stereo_dive_001.json` and the code named in each section.
Several parts of the chain have not been validated against RealityScan; they
are listed in [Not validated against RealityScan](#not-validated-against-realityscan)
and checked on the first real run with the
[first-run validation checklist](validation/ILX-LR1_first_run_checklist.md).

Wild Scan contains no COLMAP tooling. The camera-geometry study for the
August 2026 data used separate tooling delivered with the data package, and
the RealityScan project files and pose sidecars from the report's section 8.6
comparison are not in this repository; they no longer exist.

## Contents

1. [The rig](#the-rig)
2. [The node assignment switch](#the-node-assignment-switch)
3. [Calibration reference data](#calibration-reference-data)
4. [When the calibration is applied](#when-the-calibration-is-applied)
5. [How the calibration reaches RealityScan](#how-the-calibration-reaches-realityscan)
6. [Distortion model](#distortion-model)
7. [Wild Sync input](#wild-sync-input)
8. [The flight log the intake writes](#the-flight-log-the-intake-writes)
9. [Orientation convention](#orientation-convention)
10. [Preprocessing](#preprocessing)
11. [Not validated against RealityScan](#not-validated-against-realityscan)

## The rig

`modules/cameras.json` is the single source the code loads
(`modules/camera_registry.py` validates it at import and stops with a message
naming any malformed entry).

| Camera key | Wild Sync node | File name pattern | Eye | Calibration group | Lens-distortion group |
|---|---|---|---|---:|---:|
| `ilx_left` | `cam1` | `^cam1_` | L | `7` | `7` |
| `ilx_right` | `cam2` | `^cam2_` | R | `8` | `8` |

Patterns are matched case-insensitively against the file name, so every file of
a frame (`Cam1_....jpg`, `Cam1_....card.JPG`, `Cam1_....ARW`, `Cam1_....xmp`)
resolves to the same camera. The camera key is also the name of the camera's
subfolder in `raw_images/` and in each batched zone.

Mount, per camera (`families[].mount`); none of these values is measured:

| Field | Value | Meaning |
|---|---|---|
| `down_tilt_deg` | `90.0` | tilt below the vehicle's forward axis; 90 = looking straight down |
| `yaw_offset_deg` | `0.0` | direction of the image top relative to the heading |
| `pitch_accuracy_deg` | `15.0` | accuracy written for the pitch prior |
| `lever_arm_m` | `null` | offset from the navigation reference not measured: a zero offset is used and a notice is logged |

The rig `ilx_lr1_stereo` keeps the stereo geometry as data only:

- `stereo_baseline_m` = `0.225425`. The baseline was measured on the rig; the
  calibration did not estimate it: the checkerboard square size is unknown, so
  the stereo solve is in square units and this measured baseline sets its scale.
- `extrinsics_left_to_right`: rotation `R` and translation `T_m` in the OpenCV
  convention (`X_right = R * X_left + T`; camera axes x right, y down,
  z forward; metres), a relative rotation of 1.827 degrees.

RealityScan has no usable stereo-rig input: a sidecar that declares `xcr:Rig`
requires a `.rcrx` rig file that no installation provides. Verified on an
earlier dataset: adding images with such sidecars failed on the missing rig
file and registered no images; the `.rcrx` format is not described in the
RealityScan 2.2 CLI help.
Nothing in this repository writes rig XMP or turns the baseline or the
extrinsics into a solver constraint. The two cameras are aligned as two
independent cameras, each in its own calibration and lens-distortion group.

## The node assignment switch

Which Wild Sync node is which eye is encoded in one place: the two family rows
of `modules/cameras.json` (`cam1` -> `ilx_left`, `cam2` -> `ilx_right`). The
assignment was confirmed by the maintainer; no file in this repository or in
Wild Sync records it.

The rig field `node_assignment_confirmed` (currently `true`) is the switch.
Set it to `false` if the cameras are ever swapped between nodes or housings.
While it is `false`, calibration that depends on the eye (principal point and
distortion) is never applied: the automatic decision gives each camera
calibration and distortion groups only, and an explicit request for the prior
is refused (see [When the calibration is applied](#when-the-calibration-is-applied)).

## Calibration reference data

`calibration/ilx_lr1_stereo_dive_001.json` is the full calibration record
(solve, quality, image pairs); `modules/cameras.json` carries the values the
pipeline loads, and a test asserts that the two files agree. The pipeline does
not read the calibration record itself. See
[calibration/README.md](../calibration/README.md).

The calibration comes from a checkerboard session in a pool on 2026-05-18,
solved on 2026-09-30. The calibration images carry no EXIF.

| | `ilx_left` | `ilx_right` |
|---|---:|---:|
| Image size | 4096 x 3000 | 4096 x 3000 |
| Lens focal length at calibration | 16 mm (stated, not read from EXIF) | 16 mm |
| Boards used | 60 of 61 | 59 of 59 |
| Reprojection RMS | 1.15 px | 1.40 px |
| fx = fy (fixed at the stated 16 mm) | 2016.807 px | 2016.807 px |
| fx from a free-focal solve, same RMS | 1985 +/- 49 px | 1995 +/- 54 px |
| cx, cy | 2124.64, 1424.95 px | 2298.32, 1267.37 px |
| Principal point 1-sigma | +/-14 / 15 px | +/-19 / 20 px |
| `focal_length_35mm` (RealityScan form) | 17.72584 | 17.72584 |
| `principal_point_u`, `principal_point_v` | 0.01871054, -0.02501735 | 0.06111425, -0.07754206 |
| OpenCV k1, k2 | -0.15051064, 0.01672300 | -0.14233307, 0.01544262 |
| k3, p1, p2 | held at 0 | held at 0 |

The focal length in pixels assumes that the 3000 px image height spans the
23.8 mm sensor height. The RealityScan values follow
`calibration_sidecars.intrinsics_to_xmp_values`: `FocalLength35mm = fx / width * 36`,
`PrincipalPointU = (cx - width/2) / width`, `PrincipalPointV = (cy - height/2) / height`.

The stereo solve used 10 of 13 candidate pairs, with a stereo reprojection RMS
of 2.841 px. The two cameras are not hardware-synchronised: pairs are left and
right frames taken within 60 ms of each other, of a hand-held board, so the
relative rotation and the off-axis translation components are approximate.

None of these values has been validated as a RealityScan prior.

## When the calibration is applied

The Wild Sync Intake decides one calibration mode per camera
(`modules/calibration_sidecars.py`, `decide_calibration`) and records it in
`raw_images/wildsync_intake.json`. The decision is made at intake, from the
original images the operator handed in, and recorded once; later stages read
the recorded decision and never recompute it. (The preprocessed copies keep
the original EXIF - see [Preprocessing](#preprocessing) - but the decision
belongs to the originals.)

In every mode RealityScan receives a starting focal length for every image,
and its alignment refines the focal from that starting value (see
[Starting focal length](#starting-focal-length)).

| Mode | What RealityScan receives |
|---|---|
| `prior` | the camera's own calibration and lens-distortion group, the Brown3 model, the calibration's 35 mm-equivalent focal length, the principal point and the measured distortion coefficients, as an initial (adjustable) prior |
| `groups` | the camera's own calibration and lens-distortion group, the Brown3 model, and the focal length observed on the images as an initial (adjustable) 35 mm-equivalent focal; RealityScan solves each camera's intrinsics separately, starting from that focal |
| `off` | no sidecars; RealityScan reads the focal length from the EXIF of the images it aligns (the preprocessed copies keep it) |

The intake parameter `--w_calibration` chooses `auto` (default), `prior`,
`groups` or `off`.

`auto` gives `prior` only when all three conditions hold for every image of the
camera, and `groups` otherwise:

1. the rig's `node_assignment_confirmed` is `true`;
2. the image aspect ratio (width / height) is within 1 % of the calibration
   aspect ratio, 4096 / 3000 = 1.365;
3. the EXIF `FocalLength` (or the `--w_focal_override` focal, when given) is
   within 0.5 mm of the calibration focal length, 16 mm, or the operator
   asserts it with `--w_assert_focal true`.

Image size and the EXIF `FocalLength` and `FocalLengthIn35mmFilm` are read
with Pillow from the original files; the EXIF orientation tag is not applied. When `auto` falls back to `groups`, the
intake logs and records a warning naming each failed condition with both
values, for example `image 4752x3168 (aspect 1.500) vs calibration 4096x3000
(aspect 1.365)` and `EXIF focal 29 mm vs calibration 16 mm`.

An explicit `prior` is refused, and the intake fails, when the node assignment
is not confirmed, when an image's aspect ratio differs or when no image size is
known. When only the focal-length check fails, an explicit `prior` is applied
with a warning. `groups` and `off` are applied as requested.

### Starting focal length

The intake records, per camera, the focal length observed on the original
images (`modules/calibration_sidecars.py`, `observe_focal`):

- `exif_focal_mm`: the EXIF `FocalLength` (the median over the camera's
  images);
- `exif_focal_35mm`: its 35 mm equivalent - the EXIF `FocalLengthIn35mmFilm`
  when every image carries it, else `FocalLength x 36 / 35.7`, the width of
  the ILX-LR1's full-frame 35.7 x 23.8 mm sensor (`sensor_size_mm` in
  `modules/cameras.json`). On the full-frame ILX-LR1 the 35 mm equivalent is
  the lens focal itself; the x 36 / 35.7 conversion is RealityScan's
  `FocalLength35mm` scale (focal relative to a 36 mm image width).

The intake fails, naming both values, when the EXIF focal lengths of one
camera's images differ by more than 0.5 mm (the zoom changed during the run),
and fails when an image carries no EXIF focal length, unless the operator
gives the focal: `--w_focal_override <mm>` replaces the EXIF focal length of
every image of both cameras, both as the starting focal (converted with
x 36 / 35.7) and in the `auto` decision above. The override cannot be
combined with `--w_assert_focal` or with `--w_calibration off` (without a
sidecar it would not reach RealityScan).

What RealityScan starts from, per mode, is recorded as `starting_focal_35mm`
with its `starting_focal_source`:

| Mode | Starting focal (35 mm equivalent) | Source |
|---|---|---|
| `groups` | the observed EXIF focal, or the override, written as `xcr:FocalLength35mm` with `xcr:CalibrationPrior="initial"` and `xcr:Skew="0"` | `exif` or `override` |
| `prior` | the calibration's `focal_length_35mm` (17.72584), in the prior sidecar | `calibration` |
| `off` | the images' own EXIF, read by RealityScan from the preprocessed copies | `exif` |

The starting focal is never locked or exact: it is where RealityScan's
alignment starts, and the solved focal is expected to move away from it. On
the 2026-08-20 runs the lenses (29 mm on `cam1`, 24 mm on `cam2`) sit behind
a Nauticam WACP-C corrective port, so the in-water focal differs from the
in-air EXIF value; RealityScan refines it from the starting value, unlike a
fixed pinhole. The first-run checklist checks where it lands.

The August 2026 field runs have 4752 x 3168 card images (aspect 1.5), EXIF focal
29 mm on `cam1` and 24 mm on `cam2`; the 1616 x 1080 review images have aspect
1.496. Under `auto` both cameras therefore receive `groups`. Running the intake
on the 2026-08-20 19:25 transect produced exactly that: 672 images matched,
`groups` for both cameras, with static-fix and empty-depth warnings and one
combined `flight_log_19T_UTM.txt`.

Each camera's entry under `calibration` in `wildsync_intake.json` records:
`requested`, `mode`, `reason`, `warnings`, the distinct `image_sizes` and
`exif_focals_mm` seen, `calibration_image_size`, `calibration_focal_mm`,
`node_assignment_confirmed` and `focal_asserted`, and the observed focal:
`exif_focal_mm`, `exif_focal_35mm`, `exif_focal_35mm_from`,
`images_without_exif_focal`, `focal_override_mm`, `starting_focal_35mm` and
`starting_focal_source`. The manifest is schema 2; a schema-1 manifest (written
before the focal was recorded) is refused by every later stage with a request
to re-run the intake, which reuses the copied images.

## How the calibration reaches RealityScan

RealityScan takes per-image calibration only from XMP sidecars. The
RealityScan Alignment stage (`modules/realityscan_interface/realityscan_interface.py`)
reads the per-camera modes and starting focals from the intake manifest (a
camera without a usable `starting_focal_35mm` fails the stage before anything
is aligned). When at least one camera's mode is `prior` or `groups`, for every
zone it aligns it:

1. moves any existing sidecar beside an image of that camera that is not byte
   for byte one of the pipeline's own calibration sidecars (a pose prior, an
   edited calibration, a file from another tool) into
   `aligned_components/<zone>/pre_existing_sidecars/`, keeping its path
   relative to the zone and never replacing a file already there (a
   numbered name `<stem>.<n>.xmp` instead), and logs one warning with the
   count and the folder; the pipeline's own sidecars of either mode (a
   `groups` sidecar with any starting focal, or in the earlier form without
   one) are replaced in place;
2. writes the decided sidecar beside every image of that camera in the zone,
   a `groups` sidecar with the camera's starting focal;
   the sidecar is the image name without its last extension plus `.xmp`
   (`Cam1_20260820_192542.42.card.JPG` -> `Cam1_20260820_192542.42.card.xmp`);
3. writes `<zone>.rscmd` into the zone's output folder
   (`aligned_components/<zone>/`): one line per image, sorted by path, absolute
   quoted paths, CRLF line endings,

   ```text
   -addImageWithCalibration "<image>" "<sidecar>"
   -add "<image>"
   ```

   (the second form for an image without a sidecar);
4. passes the `.rscmd` path to `AlignZone.bat` as its seventh argument.
   `AlignZone.bat` then adds the images with `-execRSCMD <file>` instead of
   `-addFolder`.

When every camera's mode is `off`, or the workspace has no intake manifest,
no sidecar or `.rscmd` is written and `AlignZone.bat` adds the zone with
`-addFolder`. An unreadable or incomplete manifest, or a missing one when Wild
Sync Intake is part of the same run, fails the stage before anything is
aligned. When the workspace has an intake manifest, or Wild Sync Intake is part
of the run, a zone without its flight log fails instead of being aligned
without navigation priors. Calibration delivery cannot be combined with the
pool zone layout (`RS_ALIGN_POOL_DIR`); the stage refuses that combination.

A previous run's zone output folder that holds a `pre_existing_sidecars`
folder is moved to `superseded/` with its projects and components, never
cleared.

Pose-bearing sidecars beside images that get no calibration sidecar (every
image when calibration is `off`) are not moved: RealityScan imports them as
pose priors, its pose export overwrites them and the hygiene below rewrites
or deletes what remains. The stage warns before the run that their content is
not preserved.

A sidecar that cannot be read is never skipped silently: in place of a
calibration sidecar it is moved aside like a foreign one; if it cannot be
moved, or it lies anywhere else in the zone's image tree, the zone fails
before RealityScan starts, with a warning naming each file.

After each run, the sidecar hygiene (`sanitize_and_census`,
`ensure_calibration_sidecars`) rewrites any pose-bearing sidecar left beside
an image to the decided calibration sidecar and restores missing ones; it never
writes another form. The zone's `align_inputs.json` records the starting focal
of the `groups` cameras, so a re-run with another starting focal is reported
as a retry with changed inputs.

The sidecars use the `xcr` 1.1 attribute form. `groups`, for `ilx_left` with an
observed 29 mm focal (EXIF `FocalLengthIn35mmFilm` 29):

```xml
<x:xmpmeta xmlns:x="adobe:ns:meta/">
  <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
    <rdf:Description xcr:Version="4"
       xcr:CalibrationPrior="initial" xcr:CalibrationGroup="7"
       xcr:DistortionGroup="7" xcr:DistortionModel="brown3"
       xcr:FocalLength35mm="29" xcr:Skew="0"
       xmlns:xcr="http://www.capturingreality.com/ns/xcr/1.1#">
    </rdf:Description>
  </rdf:RDF>
</x:xmpmeta>
```

Without `FocalLengthIn35mmFilm` the same image gets
`xcr:FocalLength35mm="29.243697479"` (29 x 36 / 35.7).

`prior`, for `ilx_left`:

```xml
<x:xmpmeta xmlns:x="adobe:ns:meta/">
  <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
    <rdf:Description xcr:Version="4"
       xcr:CalibrationPrior="initial" xcr:CalibrationGroup="7"
       xcr:DistortionGroup="7" xcr:DistortionModel="brown3"
       xcr:DistortionCoeficients="-0.1505106432 0.0167230022 0 0 0 0"
       xcr:FocalLength35mm="17.72584" xcr:Skew="0"
       xcr:AspectRatio="1" xcr:PrincipalPointU="0.01871054"
       xcr:PrincipalPointV="-0.02501735"
       xmlns:xcr="http://www.capturingreality.com/ns/xcr/1.1#">
    </rdf:Description>
  </rdf:RDF>
</x:xmpmeta>
```

A sidecar never carries a pose. The prior is written as `initial`, never
locked or exact. `initial` is inferred to be the XMP token for RealityScan's
"Approximate" prior (it is the value in RealityScan's own sample sidecar).

`xcr:DistortionCoeficients` (RealityScan's spelling) holds six slots in the
order k1, k2, k3, k4, t1, t2. The OpenCV coefficients (k1, k2, p1, p2, k3) are
written as (k1, k2, k3, 0, p2, p1): RealityScan's t1 is OpenCV's p2 and its t2
is OpenCV's p1. The slot order comes from RealityScan's shipped export templates:
its OpenCV export writes the slots as k1, k2, t2, t1, k3, k4 and labels that
order as OpenCV's;
the radius normalisation of the coefficients is inferred, not verified, and so
is the normalisation of the principal point for an image that is not square.
This is why the prior is never locked.

## Distortion model

Alignment uses RealityScan's Brown3 model (three radial coefficients, no
tangential terms). `sfmDistortionModel` is set to `Brown3` in
`modules/realityscan_interface/RS_CLI/Metadata/AlignmentParams.xml`, which
`AlignZone.bat` applies with `-set` before every alignment. The setting is
global and all-or-nothing: a per-image `xcr:DistortionModel` in a sidecar does
not override it. Verified on an earlier dataset: with the global model set to
Division, sidecars that declared `brown3` were all exported back as
`division`.
The sidecars declare `brown3` to match.

The camera registry accepts only `brown3` and refuses a camera whose stored
tangential coefficients (p1, p2) are non-zero.

## Wild Sync input

A Wild Sync run directory:

```text
<run_id>/                                   for example 260820_1925_transect-01
  run.json  events.log  ingest_manifest.csv  nmea_raw.log
  cam1/  flight_log.csv
         Cam1_20260820_192542.42.jpg        review JPEG (1616 x 1080)
         Cam1_20260820_192542.42.card.JPG   card JPEG (4752 x 3168 in the August 2026 runs)
         Cam1_20260820_192542.42.ARW        RAW
         Cam1_20260820_192542.42.xmp        Wild Sync metadata sidecar
  cam2/  (the same, with the Cam2_ prefix)
```

The intake (`modules/wildsync_intake/`) treats as a camera node folder every
direct subfolder of the run directory that holds a `flight_log.csv`. The
input (`--w_input`) is a run directory or a folder whose direct subfolders are
run directories; several paths are separated with `;`. Run directories are only
read, never modified, and the workspace may not lie inside one.

The frame id of a file is its name without the suffix
`(.card)?.(jpg|jpeg|arw|xmp)` (case-insensitive):
`Cam1_20260820_192542.42.card.JPG` -> `Cam1_20260820_192542.42`. The frame id
itself contains a dot.

Image variant (`--w_variant`): `card` (default, the camera's full-size JPEG) or
`review` (the small review JPEG). RAW (`.ARW`) is not an input format of this
pipeline: preprocessing and batching handle JPEG and PNG only. Wild Sync's own
`.xmp` sidecars are never copied. Hidden files (names starting with `.`) are
ignored.

### `flight_log.csv`

Comma-separated, one header line, exactly these 23 columns in this order:

```text
filename,datetime,lat,long,xutm,yutm,utm_zone,depth_from_xplore9,pitch,roll,yaw,heading_mag_xplore,heading_imu,ax_g,ay_g,az_g,gx_dps,gy_dps,gz_dps,imu_temp_c,capture_source,time_source,time_err_ms
```

The intake reads each log strictly:

- the header must match exactly; unknown, missing, repeated or reordered
  columns make the log unreadable;
- `filename`, `datetime`, `utm_zone`, `capture_source` and `time_source` are
  text; every other column is numeric;
- an empty cell is a missing value; a numeric cell that does not parse as a
  number makes the log unreadable; one that parses to NaN or infinity is a
  missing value;
- `utm_zone` is a zone number with its MGRS latitude band (`19T`) or empty;
- blank lines are skipped; a UTF-8 byte-order mark is accepted.

`filename` names the review JPEG; the card JPEG and the RAW share its frame id.
A row whose file name belongs to the other camera than its node folder, two rows
for one frame, or two images of one variant for one frame make the run
unreadable.

Columns the intake uses: `xutm`, `yutm`, `utm_zone` (position),
`depth_from_xplore9` (altitude), `heading_imu`, `yaw` or `heading_mag_xplore`
(heading), `pitch`, `roll`, `lat` and `long` (hemisphere and zone cross-check),
`time_err_ms` (warning only), `capture_source` and `time_source` (counted in
the manifest). `depth_from_xplore9` and `heading_mag_xplore` may be empty.

### What the intake writes

Into `<workspace>/raw_images/`:

- each matched image, copied byte for byte, to `<camera key>/<original file name>`;
- one combined flight log for both cameras, `flight_log_<zone><band>_UTM.txt`
  ([below](#the-flight-log-the-intake-writes));
- the manifest `wildsync_intake.json`: source runs and per-node counts, the
  variant, matched / unmatched rows and images and the match rate, the UTM zone,
  the static-fix flag and position spread per run, the accuracies written, the
  heading columns used, the declination, the orientation convention as text,
  capture- and time-source counts, the calibration decision and the observed
  and starting focal length per camera, and every warning and notice.

The intake fails, before writing anything, on: an unreadable log; no matched
image; a match rate below `--w_min_match_rate` (default 80 %; the rate is matched
frames over matched frames plus rows without an image plus images without a
row); a duplicate image name across the runs taken in together; matched rows in
more than one UTM zone; no matched row with a UTM position; a `utm_zone` whose
hemisphere contradicts the row's latitude; an explicit calibration prior that
cannot apply; an EXIF focal length that differs by more than 0.5 mm between
images of one camera, or is missing, without `--w_focal_override`; an unusable
focal override; and files in `raw_images/` that it did not plan or that differ
from their source (use a fresh workspace). Re-running the same intake into the
same workspace reuses identical copies. A `<stem>.xmp` beside a planned image
that is byte for byte one of the alignment stage's own calibration sidecars for
that image's camera (written when alignment is pointed at `raw_images/`
directly) is not counted as unplanned and is left as it is; any other sidecar
is.

Before it copies anything, the intake writes `wildsync_intake.json` with
status `in_progress`; the complete manifest replaces it only when the copies
and the flight log are written. An intake that is interrupted therefore leaves
the in-progress manifest: the status screen shows the intake as partial, and
the alignment stage refuses the workspace (with or without Wild Sync Intake in
the same run) until the intake is re-run to completion.

It warns, and records the warning in the manifest, on: a static fix; empty
depth; images without an orientation prior or without a position; `time_err_ms`
above `--w_time_err_warn` (default 50 ms; nothing is dropped); rows without an
image and images without a row; latitude/longitude outside the stated UTM zone;
images that cannot be read for the calibration decision; and a calibration that
falls back to `groups`.

## The flight log the intake writes

`raw_images/flight_log_<zone><band>_UTM.txt` (for example
`flight_log_19T_UTM.txt`) is in RealityScan flight-log format
`{B438A617-2434-5A24-C1B7-58980F28345A}`: 13 semicolon-separated columns, one
header line, image name first, numbers with six decimals, an empty cell where a
value is missing, UTF-8, LF line endings. It is written by
`modules/flight_logs.py` (`write_flight_log`).

```text
filename;X (East);Y (North);Alt;X Accuracy;Y Accuracy;Alt Accuracy;Yaw;Pitch;Roll;Yaw Accuracy;Pitch Accuracy;Roll Accuracy
```

| Column | Source | Default accuracy |
|---|---|---|
| `filename` | the copied image's name | |
| `X (East)`, `Y (North)` | `xutm`, `yutm` (plus the horizontal lever arm, zero while it is not measured) | 10 m (`--w_pos_accuracy`); 1000 m for a static fix (`--w_static_pos_accuracy`) |
| `Alt` | `-abs(depth_from_xplore9)` minus the downward lever arm; `--w_surface_altitude` (default 0.0, camera at the sea surface) when depth is empty | 1 m (`--w_alt_accuracy`); 1000 m when depth is empty (`--w_surface_alt_accuracy`) |
| `Yaw`, `Pitch`, `Roll` | [orientation convention](#orientation-convention) | yaw and roll 15 degrees (`--w_orientation_accuracy`); pitch from the mount, 15 degrees |

A run is a static fix when every row's position lies within 1 m (bounding-box
diagonal). The August 2026 runs carry the same static navigation fix on every
row and empty depth, so their images are written with position accuracy 1000 m,
which cannot constrain the solve, and altitude 0 with altitude accuracy
1000 m, so the fallback does not pin the cameras to the surface. The manifest
records, under `altitude`, the surface-altitude accuracy and how many images
it was written for (`images_with_surface_altitude_accuracy`).

The 13-column format must be installed in RealityScan's own `flightlogs.xml`
before alignment; see
[Setup and run, section 5.4](SETUP-AND-RUN.md#54-flight-log-import-format).
Before each import the alignment stage writes
`<workspace>/logs/FlightLogParams_<zone><band>.xml` from
`RS_CLI/Metadata/FlightLogParams.xml`, with the coordinate system set to WGS84
UTM in the zone named by the log file name.

## Orientation convention

One function computes the orientation prior of every image
(`modules/wildsync_intake/intake.py`, `orientation_prior`, built on
`modules/flight_logs.py`, `realityscan_orientation`):

```text
yaw   = (heading + declination + mount yaw offset) mod 360
pitch = 90 + (IMU pitch - mount down tilt)
roll  = IMU roll
```

- `heading` comes from `--w_heading_source`: `auto` (default) takes
  `heading_imu`, and `yaw` where `heading_imu` is empty; `heading_imu`, `yaw`
  or `heading_mag_xplore` name one column.
- `declination` is `--w_declination`, degrees east positive, default 0 (the
  heading is used as given).
- The mount yaw offset is 0 and the down tilt is 90 for both cameras, so
  `pitch` equals the IMU pitch and `yaw` equals the heading plus declination.
- RealityScan's pitch is 0 for a camera looking straight down and 90 for a
  horizontal camera.
- A missing heading, pitch or roll gives no orientation prior for that image:
  all three angles and their accuracies are written empty, never as zeros.

The pitch accuracy comes from the mount (15 degrees); yaw and roll accuracy from
`--w_orientation_accuracy` (15 degrees). These wide accuracies keep an error in
the convention from dominating the solve.

This mapping follows RealityScan's convention. According to the RealityScan
2.2 CLI help, imported Yaw/Pitch/Roll are rotations about Z, Y and X in a
North-East-Down frame, evaluated right to left, and a render from a camera
with all three angles 0 is a view from above (pitch 0 looks straight down).
One other help page assigns the axes differently; check C of the
[first-run validation checklist](validation/ILX-LR1_first_run_checklist.md#c-orientation-convention)
tests that alternative too.
It has **not** been validated for the Wild Sync IMU axes: the sign and zero of
the IMU pitch and roll, whether the IMU heading is magnetic or true, and the
orientation of the image top relative to the heading are all unconfirmed.
`FlightLogParams.xml` does not pin RealityScan's Euler-order or camera-mount
import settings, so their installation defaults apply. Validate the convention
on the first real run with the
[first-run validation checklist](validation/ILX-LR1_first_run_checklist.md);
until then, do not tighten the orientation accuracies.

## Preprocessing

The pipeline's preprocessing is this repository's CLAHE
(`modules/preprocess_images/preprocess_images.py`):

- contrast-limited adaptive histogram equalisation on the L channel in LAB
  colour space, clip limit 2.0 (`--p_clahe_clip`, 0 disables it), 8 x 8 tiles
  (`--p_clahe_tile`);
- gray-world white balance before CLAHE, off by default (`--p_white_balance`);
- output written to `<workspace>/preprocessed_images/` with the input's folder
  structure and file names, as JPEG at quality 95 with the original EXIF block
  (focal length, make and model, capture time), so RealityScan can read the
  focal length from the copies when no calibration sidecar is written; the
  copies in `raw_images/` are kept unchanged.

OpenCV encodes the pixels exactly as before EXIF was kept; the original APP1
EXIF segment, read with Pillow, is then inserted into the encoded file
unchanged, except that the Orientation tag is set to 1 because OpenCV has
already applied it to the pixels. An EXIF block that cannot be carried over
fails that image rather than producing a copy without it. Copies written by an
earlier version, without EXIF, are not reused: the preprocessing manifest
records the EXIF policy, and a manifest without it reprocesses every image.

Batching and alignment use the preprocessed copies when they exist. The
defaults were chosen on earlier datasets taken with other cameras; they have
not been tuned on ILX-LR1 imagery.

The example figures in the 31 August 2026 report were made with Wild Sync's
`rig/vslam.py --pre flat` (a Gaussian flatten followed by CLAHE). That
preprocessing is part of Wild Sync, not of this pipeline; Wild Scan applies no
flattening step.

## Not validated against RealityScan

The code paths below are covered by the offline test suite and the intake has
been run on real Wild Sync data, but no live RealityScan alignment has yet been
run with this version of the pipeline. Until one has:

1. **Orientation mapping.** The mapping from Wild Sync IMU pitch, roll and
   heading to RealityScan flight-log Yaw, Pitch and Roll is unvalidated.
2. **Distortion convention.** The Brown3 coefficient convention and radius
   normalisation used when measured OpenCV k1, k2 are passed in a sidecar is
   unvalidated.
3. **Calibration applicability.** Whether the 2026-05-18 calibration applies to
   a given capture is not established by the automatic checks; they only rule
   out captures that clearly differ.
4. **Calibration command-file mode.** That `-execRSCMD` with quoted
   `-addImageWithCalibration` lines, and sidecars beside the images, imports as
   written in `AlignZone.bat` is unvalidated.
5. **Starting focal in a `groups` sidecar.** Whether RealityScan honours an
   `initial` `xcr:FocalLength35mm` (with `xcr:Skew="0"`) written next to the
   calibration and distortion group ids, starts the camera's focal there and
   refines it, is unvalidated; so is whether `xcr:CalibrationPrior="initial"`
   there leaves the principal point and distortion, which the sidecar does not
   give, free.
6. **EXIF focal from the preprocessed copies.** Whether RealityScan reads the
   focal length from the EXIF the preprocessed copies keep (the only focal it
   gets with calibration `off`), and how it converts it to a 35 mm equivalent
   without a sidecar, is unvalidated.
7. **Flight-log import settings.** `FlightLogParams.xml` carries the import
   keys `ifKGrp`, `ifKmode`, `ifuuInh` and `ifuuInhEn`, whose meaning is not
   documented; whether the flight-log import changes the calibration grouping
   or prior set by the sidecars is unvalidated.

The [first-run validation checklist](validation/ILX-LR1_first_run_checklist.md)
gives a check for each.
