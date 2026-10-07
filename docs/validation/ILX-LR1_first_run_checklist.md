# ILX-LR1 first-run validation checklist

The parts of the ILX-LR1 chain listed in
[The ILX-LR1 stereo rig](../ILX-LR1.md#not-validated-against-realityscan) have
not been validated against RealityScan, and the flight-log format
installation is a per-machine prerequisite. This checklist validates them on the first real alignment. Run
it once per RealityScan installation and again after a RealityScan update.

Each check changes one variable, states what it expects before it runs, and
has a decision rule. A check whose oracle cannot tell a known-good case from a
known-bad case is inconclusive: stop and report it instead of interpreting it.

| Check | Question | Needs |
|---|---|---|
| [A](#a-flight-log-format-installed) | Does RealityScan read the 13-column flight-log format, including orientation and accuracies? | any run |
| [B](#b-calibration-command-file-mode) | Does `AlignZone.bat` import every image with its sidecar through `-execRSCMD`? | any run with calibration `groups` or `prior` |
| [C](#c-orientation-convention) | Does the Wild Sync IMU to RealityScan Yaw/Pitch/Roll mapping hold? | a run with changes of heading and attitude; live navigation for the absolute part |
| [D](#d-distortion-convention) | Do the stored OpenCV k1, k2 mean the same thing in RealityScan's Brown3 slots? | a capture for which `prior` applies |
| [E](#e-calibration-applicability) | Does the stored calibration describe this capture? | any capture considered for `prior` |
| [F](#f-starting-focal-length) | Does RealityScan start each camera at the starting focal it is given and refine it to the in-water focal? | any run with calibration `groups`; a second run for the oracle |

## Before the run

- [ ] The offline test suite passes in this checkout
      (`& ".\.venv\Scripts\python.exe" -m pytest`).
- [ ] The flight-log format is installed and the read-only ID check in
      [Setup and run, section 5.4](../SETUP-AND-RUN.md#54-flight-log-import-format) passes.
- [ ] One short Wild Sync run is chosen, and a fresh workspace on a local disk.
      Every check below runs on this small dataset first; nothing is validated
      on a full survey first.
- [ ] Record the environment with the workspace: `RealityScan.exe` file version,
      the Git commit of this checkout, the run directory names, and
      `raw_images/wildsync_intake.json` after the intake.
- [ ] Budget: note the expected duration of one zone alignment and the abort
      criterion (for example, no progress for twice the expected duration) before
      starting.
- [ ] RealityScan's `%LOCALAPPDATA%\Temp\RealityScan.log` is overwritten on every
      instance start. Copy it, with `RS_CLI/Errors/results_<instance>.log` and
      `errors_<instance>.txt`, after every alignment in this checklist, before
      the next one starts.

Run the chain (Wild Sync Intake, Preprocess Images, Batch Directory,
RealityScan Alignment) with default settings. Check in the manifest that the
calibration mode of each camera is the one expected (`groups` for the
August 2026 runs).

## A. Flight-log format installed

**Expectation.** The saved zone project imports position, orientation and the
accuracy columns.

1. Open `aligned_components/<zone>/<zone>.rsproj` as text.
2. Find the camera prior entries: the saved project stores each image as an
   `<input>` element whose attributes carry its priors (`absX`, `absY`, `absZ`
   for position, `absRX`, `absRY`, `absRZ` for orientation, `absu*` for the
   accuracies, and `absPrior`). To count them:

   ```powershell
   $p = 'aligned_components\<zone>\<zone>.rsproj'
   (Select-String -LiteralPath $p -Pattern 'absPrior="pose"' -AllMatches).Matches.Count
   (Select-String -LiteralPath $p -Pattern 'absPrior="registered"' -AllMatches).Matches.Count
   (Select-String -LiteralPath $p -Pattern '\babsu\w*=' -AllMatches).Matches.Count
   ```

**Decision rule** (verified on an earlier dataset with RealityScan 2.2):

- `absPrior="pose"` with `absu*` accuracy attributes present: the format
  resolved. Pass.
- `absPrior="registered"` with no `absu*` attributes: the format GUID did not
  resolve; RealityScan imported positions only and dropped orientation and
  accuracies, with exit code 0. Fail. Fix the installation; checks C to E
  cannot be interpreted until A passes.

## B. Calibration command-file mode

**Expectation.** Every image of the zone is added once, with its sidecar, and
each camera ends in its own calibration and lens-distortion group (`7` for
`ilx_left`, `8` for `ilx_right` in the sidecars). RealityScan can renumber
supplied groups (verified on an earlier dataset: groups 1 and 2 came back as
2 and 3), so judge the grouping, not the numbers.

1. Before alignment output is trusted, confirm in the zone's output folder that
   `<zone>.rscmd` exists, has CRLF line endings, has one line per image in the
   zone, and that every `-addImageWithCalibration` line names an existing image
   and an existing `.xmp` beside it.
2. In the alignment log, confirm the line
   `Calibration delivery for <zone> (...): N of N image(s) added with a calibration sidecar`
   and that the RealityScan log shows the `-execRSCMD` command completing
   without an error.
3. Count the images in the saved project: it must equal the number of lines in
   the `.rscmd`.
4. Read the grouping back on the saved project with
   `-exportReport <out.html> <template>`, where the template is a text file
   that uses the report function `$ExportInputsGrouping` and, inside it,
   `$IterateGroups` with the per-group variables `groupIndex`,
   `calibrationGroup`, `distortionGroup` and `count` (RealityScan 2.2 CLI
   help, report templates; the shipped templates are in
   `<install>\Reports`). A minimal template:

   ```text
   $ExportInputsGrouping(groups=$(groupCount) grouped=$(groupedInputCount) ungrouped=$(ungroupedInputCount)
   $IterateGroups(g$(groupIndex) calib=$(calibrationGroup) lens=$(distortionGroup) n=$(count)
   ))
   ```

   This read-back has not yet been run with this pipeline. If it hangs or
   writes no report, the check is inconclusive, not failed.

**Oracle check.** Repeat step 4 on the same zone aligned with
`--w_calibration off` (one variable changed; a fresh workspace). Without
sidecars there must not be one calibration group per camera. If both runs
show the same per-camera grouping, the read-back cannot distinguish the cases:
stop.

**Decision rule.** Pass when the sidecar run shows one calibration group and
one lens group per camera (`7` and `8` unless renumbered), each holding exactly
that camera's images, no ungrouped image, and the `off` run does not. Fail
when any image is missing from the project, when the groups are absent, or
when images of both cameras share a group.

## C. Orientation convention

The mapping under test (see
[The ILX-LR1 stereo rig](../ILX-LR1.md#orientation-convention)):
`yaw = heading + declination + mount yaw offset`, `pitch = IMU pitch` for the
nadir mount (RealityScan pitch 0 = looking straight down), `roll = IMU roll`,
imported as yaw about Z, pitch about Y, roll about X in a North-East-Down
frame, composed right to left (RealityScan 2.2 CLI help, flight-log import;
one other help page assigns yaw to Y, pitch to X and roll to Z, which step 5
tests as an alternative).
`FlightLogParams.xml` does not pin the flight-log import settings
**Euler angles order (YPR)** and **Camera mount** (the latter is offered when
the format carries Yaw/Pitch/Roll), so the installation defaults are part of
what is tested. Before the run, open RealityScan's flight-log import dialog
with this format selected and record both values with the environment.

The test must not grade the priors against a solve the same priors produced.

1. **Prior-free solve.** Align the zone with orientation priors that cannot
   constrain the solve: `--w_orientation_accuracy 180`, and in a separate
   working copy of the checkout `pitch_accuracy_deg` set to 180 for both mounts
   in `modules/cameras.json`. Everything else unchanged.
2. **Repeat.** Align the same zone a second time unchanged. RealityScan
   alignment is not repeatable on marginal geometry (verified on an earlier
   dataset: identical reruns registered 26 and 55 images);
   the difference between the two solves is the noise floor for every
   comparison below.
3. **Read the solved rotations** from the pose sidecars the identity harvest
   moved to `aligned_components/<zone>/identity_r0/`. Match both the attribute
   and the element form of `xcr:Rotation`: the sample sidecar in the
   RealityScan 2.2 CLI help uses the attribute (`xcr:Rotation="..."`),
   while RealityScan 2.2 was seen writing the element
   (`<xcr:Rotation>...</xcr:Rotation>`) on an earlier dataset. Either holds
   nine numbers, a 3 x 3 matrix in row order.
4. **Reader check (known-good case).** For pairs of simultaneous `cam1`/`cam2`
   frames, the angle between the two solved rotations must be close to the
   stereo extrinsics rotation, 1.827 degrees (the cameras are not synchronised,
   so allow for motion within about 60 ms). If it is not, the rotations are
   being read wrongly: stop.
5. **Relative rotations.** For pairs of frames of one camera with a clear change
   of heading, pitch or roll, compare the solved relative rotation with the one
   predicted from the flight log's Yaw/Pitch/Roll under the documented
   convention, and under at least these wrong alternatives: pitch and roll
   swapped; roll sign flipped; pitch sign flipped; yaw sign flipped; yaw about
   Y, pitch about X and roll about Z. Relative
   rotations do not depend on the georeferenced frame, so this part works on a
   static-fix run. Evaluate both readings of `xcr:Rotation` (world-to-camera and
   its transpose); on near-nadir imagery the two can be hard to separate,
   because a downward-looking camera's rotation is close to a 180-degree
   rotation, which is its own transpose (three attempts on an earlier dataset
   were inconclusive for this reason).
6. **Absolute heading.** Only on a run with live navigation (not a static fix):
   compare the solved heading of the georeferenced component with the logged
   heading. This settles the yaw zero, the image-top direction relative to the
   heading (mount yaw offset) and whether a magnetic declination is needed.

**Decision rule.**

- Pass (relative part): the documented convention has the lowest median
  residual, and it is separated from every alternative by clearly more than the
  difference between the two repeat solves.
- Inconclusive: two candidates tie, usually because the run has too little
  attitude change. Use a run with turns and attitude variation; do not tighten
  orientation accuracies on an inconclusive result.
- Fail: an alternative fits better. Do not use orientation priors at tighter
  than the default 15 degrees until the mapping is corrected and this check
  passes.
- The absolute part (step 6) stays open until a run with live navigation is
  available. Record it as open, not as passed.

## D. Distortion convention

Applies only to a capture for which `prior` applies (4096:3000 aspect, EXIF
focal 16 mm, node assignment confirmed). The August 2026 field runs do not
qualify; for them, record only the solved coefficients from step 1.

**Expectation.** If RealityScan's Brown3 k1, k2 use the same radius
normalisation as OpenCV's, a solve that starts from the stored coefficients
stays close to them, and a free solve converges near them.

1. Align the zone with `--w_calibration groups` (free intrinsics per camera).
2. Align the same zone with `--w_calibration prior` (one variable changed).
3. Export the registration of both with
   `-exportRegistration <file>.csv <params>.xml` in RealityScan's
   OpenCV-compliant Internal/External Camera Parameters format, which writes
   `f_pix`, `px_pix`, `py_pix` and `k1,k2,t2,t1,k3,k4` in OpenCV ordering; its
   header line names the columns. Save `<params>.xml` once from the Export
   Registration dialog with that format chosen: without a params file the
   command blocked indefinitely in a headless run on an earlier dataset.

**Decision rule.**

- Pass: the free solve's k1, k2 per camera agree with the stored values
  (ilx_left -0.1505, 0.0167; ilx_right -0.1423, 0.0154) in sign and within
  roughly the change between two repeat solves, and the prior run's registered
  count is not lower than the groups run's beyond the repeat spread.
- Fail: the free solve's coefficients differ by a consistent factor (a
  different radius normalisation) or in sign. Record the factor; do not use
  `prior` until the conversion in `modules/calibration_sidecars.py` is
  corrected and this check passes.

## E. Calibration applicability

The stored calibration was solved at 16 mm on 4096 x 3000 images. The automatic
checks at intake only refuse captures that clearly differ (aspect ratio, EXIF
focal length, unconfirmed node assignment); they do not prove that a capture
matches.

1. Read the per-camera decision, image sizes and EXIF focal lengths from
   `raw_images/wildsync_intake.json`.
2. Confirm with the operator that the lenses were at 16 mm and the cameras were
   not swapped between nodes or housings since the calibration session.
3. From the `groups` solve of check D, compare each camera's solved
   35 mm-equivalent focal length with the stored 17.726, and its solved
   principal point with the stored values.

**Decision rule.** Use `prior` only when the intake chose it, the operator
confirms 2, and the solved focal length is within the free-focal uncertainty of
the calibration (about 2.5 %) and the principal point within a few of its
stated 1-sigma intervals. Otherwise use `groups` and record why.

## F. Starting focal length

Every camera reaches RealityScan with a starting focal: with `groups` the
sidecar's `initial` `xcr:FocalLength35mm` (the EXIF focal observed at intake,
or `--w_focal_override`), with `off` the EXIF the preprocessed copies keep.
The 2026-08-20 optics are a 29 mm (`cam1`) and a 24 mm (`cam2`) lens behind a
Nauticam WACP-C corrective port: the in-water focal is not the in-air EXIF
value, and RealityScan refines the focal from its starting value, unlike a
fixed pinhole. This check covers items 5 and 6 of the not-validated list.

**Before the run**, write down for each camera: the starting focal,
`starting_focal_35mm` and `starting_focal_source` in
`raw_images/wildsync_intake.json`; and the expected in-water 35 mm-equivalent
focal with its source (for example Nauticam's stated in-water field of view
for the lens on the WACP-C, converted as `18 / tan(HFOV / 2)` for a 36 mm
width, or an in-water calibration of that lens and port) and the tolerance
you will accept. If no expected value can be stated, the "lands near" part of
this check is open, not passed.

**Expectation.** The solved focal of each camera differs from its starting
focal by more than the spread between two repeat solves, and lands within the
stated tolerance of the expected in-water focal.

1. Confirm the start was delivered: one sidecar per camera carries
   `xcr:CalibrationPrior="initial"` and `xcr:FocalLength35mm` equal to the
   manifest's `starting_focal_35mm`, and the alignment log shows
   `ilx_left=groups (initial focal <value> mm 35mm-eq.)` (and likewise for
   `ilx_right`).
2. Export the registration as in check D step 3 and convert each camera's
   solved focal to a 35 mm equivalent: `f_pix / image width in pixels x 36`.
3. Record, per camera: starting focal, solved focal, expected in-water focal.

**Oracle check.** Align the same zone again in a fresh workspace with
`--w_focal_override` set about 15 % away from the EXIF focal (one variable
changed: the start). If RealityScan honours the start and refines the focal,
the two solves converge to the same focal within the repeat spread, away from
both starting values.

**Decision rule.**

- Pass: in both runs the solved focal moved away from its start, the two
  solves agree within the repeat spread, and the solved focal is within the
  stated tolerance of the expected in-water focal.
- Fail, not refined: a solved focal equals its starting focal to the exported
  precision. The focal was held fixed; confirm no sidecar says `exact` or
  `locked` and record the finding.
- Fail, lands elsewhere: the two solves agree but outside the tolerance of the
  expected in-water focal. Record the solved value; the expected value or the
  port model needs revisiting before the starting focal is changed.
- Inconclusive: the two solves differ by about the difference of their starting
  values (the solve stays near wherever it starts), or the two runs' solves are
  identical although their sidecars differ (the sidecar focal may be ignored).
  Stop and report; do not tune the starting focal on this result.

With calibration `off`, repeat steps 2 and 3 once to see where RealityScan
starts from the EXIF of the preprocessed copies; a solved focal equal to the
in-air EXIF focal there is the same "not refined" failure.

## Record the result

For each check, record with the workspace: the date, the environment from
[Before the run](#before-the-run), the command lines and parameters, the copied
logs, the decision (pass, fail, inconclusive or open) and the evidence for it.
A failed or inconclusive check is a result too. Update
[The ILX-LR1 stereo rig](../ILX-LR1.md#not-validated-against-realityscan) only
when a check passes.
