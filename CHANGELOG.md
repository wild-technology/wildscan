# Changelog

All notable changes to this project are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [2.0.0] - Unreleased

Wild Scan is now a RealityScan 2.2 pipeline for one rig: two Sony ILX-LR1
cameras captured with Wild Sync. This release is breaking.

### Added

- Wild Sync Intake stage, which reads a Wild Sync run directory, and an
  intake screen in the app for choosing one.
- Per-camera calibration delivery with three modes: `prior`, `groups` and
  `off`. Priors need a confirmed node assignment; otherwise calibration is
  delivered as groups only or not at all.
- ILX-LR1 camera registry with a node-assignment switch, and the ILX-LR1
  stereo calibration reference data.
- Calibration command-file mode in `AlignZone.bat`, which adds images from a
  command file during alignment.
- Flight-log writer and camera orientation convention in `modules/flight_logs.py`.
- Wild Sync intake ignores hidden files such as macOS AppleDouble copies in
  camera folders.
- `sbom.spdx.json` (SPDX 2.3) and `THIRD_PARTY_NOTICES.md`.
- RealityScan gets a starting focal length for every image in every
  calibration mode. The intake records each camera's EXIF focal length and
  its 35 mm equivalent (`FocalLengthIn35mmFilm`, else `FocalLength x 36 /
  35.7`), and the starting focal and its source, in `wildsync_intake.json`;
  a `groups` sidecar carries that focal as an `initial`
  `xcr:FocalLength35mm` with `xcr:Skew="0"`. The intake fails when the EXIF
  focal changes within one camera (zoom changed mid-run) or is missing.
- `--w_focal_override` (mm): the operator's focal length for every image of
  both cameras, in place of the EXIF focal length.
- `--w_assert_focal`: asserts the lenses were at the calibration focal
  length when the EXIF focal is missing or differs, so an explicit `prior`
  can pass the focal check.
- Camera registry field `sensor_size_mm` (the ILX-LR1's 35.7 x 23.8 mm
  full-frame sensor).

### Changed

- Preprocessed JPEGs keep the original EXIF block (Orientation reset to 1,
  as OpenCV already rotates the pixels); the pixels are unchanged. Copies
  written without EXIF by an earlier version are processed again.
- The intake manifest is schema 2; a schema-1 manifest is refused by every
  later stage until the intake is re-run.
- Alignment uses RealityScan's global Brown3 distortion model. The previous
  global setting was Division, with per-camera distortion supplied through XMP
  sidecars.
- Flight logs are imported in their UTM zone only.
- The app's stage list and dataset selection follow the Wild Sync Intake
  chain.
- The minimum component size for export is 10 cameras (was 50) and is
  applied by the orchestrator after the identity loop has captured every
  component, so a zone whose components are all too small reports how
  many there were and how large; the small ones are removed from the
  zone output and listed in `components_below_min_size.json`.
- A zone with fewer images than the minimum component size exports
  components holding at least half of its images (at least 2 cameras),
  instead of requiring every image to register.
- Before calibration sidecars are written, an existing sidecar beside an
  image that the pipeline did not write (a pose prior, an edited
  calibration) is moved to `aligned_components/<zone>/pre_existing_sidecars/`
  instead of being overwritten.
- An unreadable `.xmp` sidecar in a zone's image tree is reported by name; it
  is moved aside like a foreign sidecar where a calibration sidecar goes, and
  otherwise fails the zone before RealityScan starts.
- In a Wild Sync intake workspace, a zone without its flight log fails
  instead of being aligned without navigation priors.
- Wild Sync Intake marks its manifest `in_progress` before copying and
  writes the complete manifest last, so an interrupted intake is reported as
  partial and refused by alignment instead of reading as "no intake".
- An intake re-run accepts, and leaves untouched, the pipeline's own
  calibration sidecars beside its images in `raw_images/`.
- Flight-log prior accuracies live on the rig in `modules/cameras.json`
  (`rigs.ilx_lr1_stereo.prior_accuracy`): position 0.1 m (the GPS receiver's
  accuracy), altitude 1 m, orientation 10 degrees for yaw, pitch and roll.
  The `defaults` block is gone and the `AlignmentParams.xml` fallback carries
  the same 10 degrees. (1.0.0 wrote 10 m and 15 degrees.)
- A run whose position never moves (a static fix) gets no position prior:
  its images are written with empty X/Y cells instead of the one repeated
  position. `--w_static_pos_accuracy` is gone.
- An image without depth gets no altitude prior (empty Alt cells) instead of
  altitude 0. `--w_surface_altitude` and `--w_surface_alt_accuracy` are
  gone. The manifest counts the images written without a position and
  without an altitude.
- Batch Directory keeps images that have no position: a workspace where no
  image has one forms a single zone instead of failing, and under the
  single-zone rule unpositioned images join the zone.
- `wildscan` refuses to start, with one message, when the repository-root
  driver scripts are missing (for example from a built wheel); only an
  editable install from a source checkout is supported.
- `packaging` is declared as a test dependency.
- The test suite moved from `testing/` to `tests/`.
- Datasets under 4000 images are one zone (`--b_single_zone_below`); under
  100 images the batcher warns and continues.
- Positions without spatial extent (every image at one point) form one
  zone, and no empty zone is kept.
- Under `RS_NO_INTERACTIVE=1` the pipeline never prompts and exits 2 naming
  every missing required flag; `--output_dir` and `--w_input` are required.
- `scripts/validation/check_preprocessing.py` requires `--dataset`; the
  stored campaign path is gone.
- CI runs on every push and pull request, on Python 3.12 and 3.13.

### Removed

- Support for camera families other than the ILX-LR1.
- Video frame extraction.
- Vehicle-navigation georeferencing, the standalone survey georeferencer and
  the filename timestamp parser.
- The local-frame flight-log path and its parameter template.
- The `utm` dependency.
- The campaign, COLMAP and review documents and the earlier validation plans
  and results. They remain in the Git history at `ea3ad5d`.
- Campaign scripts, per-campaign test runners, the zone-9 validation runner and the Cesium depth probe.
- The sensor database file (`sensorsdb.xml`); `flightlogs.xml` keeps only the
  pipeline's flight-log format.
- The batch-time XMP calibration option, the align-script override and unused
  model flags.
- Image analysis, feature merge and legacy utility scripts.

## [1.0.0] - 2026-09-23

Initial release: a RealityScan 2.2 photogrammetry pipeline with a Textual
console, zone-based alignment and merge, and Cesium publishing.
