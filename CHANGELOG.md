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

### Changed

- Alignment uses RealityScan's global Brown3 distortion model. The previous
  global setting was Division, with per-camera distortion supplied through XMP
  sidecars.
- Flight logs are imported in their UTM zone only.
- The app's stage list and dataset selection follow the Wild Sync Intake
  chain.
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
- An image without depth gets altitude accuracy 1000 m
  (`--w_surface_alt_accuracy`) with its surface-altitude fallback, instead of
  the normal altitude accuracy that pinned it to the surface; the manifest
  counts these images.
- `wildscan` refuses to start, with one message, when the repository-root
  driver scripts are missing (for example from a built wheel); only an
  editable install from a source checkout is supported.
- `packaging` is declared as a test dependency.
- The test suite moved from `testing/` to `tests/`.

### Removed

- Support for camera families other than the ILX-LR1.
- Video frame extraction.
- Vehicle-navigation georeferencing, the standalone survey georeferencer and
  the filename timestamp parser.
- The local-frame flight-log path and its parameter template.
- Campaign scripts, per-campaign test runners, the zone-9 validation runner and\n  the Cesium depth probe.
- The sensor database file (`sensorsdb.xml`); `flightlogs.xml` keeps only the
  pipeline's flight-log format.
- The batch-time XMP calibration option, the align-script override and unused
  model flags.
- Image analysis, feature merge and legacy utility scripts.

## [1.0.0] - 2026-08-31

Initial release: a RealityScan 2.2 photogrammetry pipeline with a Textual
console, zone-based alignment and merge, and Cesium publishing.
