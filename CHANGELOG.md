# Changelog

All notable changes to this project are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [2.0.0] - 2026-10-26

The Sony Wild Sync intake is added alongside the existing WCA, Zeuss and other
registered-camera workflows. This version does not retire video extraction,
ROV navigation, local-coordinate projects or the existing command-line tools.

### Added

- Optional Wild Sync Intake, with per-camera calibration decisions and manifests.
  Select it explicitly for Sony recordings; normal datasets retain the existing
  extract/georeference/preprocess/batch/alignment chain and saved settings.
- SPDX 2.3 inventories generated from the installed Windows environments,
  including transitive dependencies, licence texts and hashes; repeatable
  dependency checks and Windows checks on Python 3.12 and 3.13.
- Sony-specific Brown3 alignment settings, chosen for a Wild Sync intake rather
  than replacing the generic alignment template or an explicit operator override.
- Regression coverage for WCA/Zeuss filename families, mounts, calibration,
  generic dataset selection and the restored navigation and command-line paths.

### Fixed

- Preserve original EXIF for Sony intake or explicit `--p_preserve_exif` only;
  generic preprocessing keeps its historical output and resume policy.
- Use 10 m Sony moving-track position uncertainty until end-to-end accuracy
  is established. Static fixes still receive no position prior.
- Default Sony calibration to camera groups until measured-prior conversion
  is validated in RealityScan. Previously saved `auto` defaults require a new
  explicit selection; existing intake manifests retain their decisions.
- Reject non-upright Sony images before copying them and support an explicit
  missing-focal assertion only after calibration applicability checks pass.
- Preserve existing pose/calibration sidecars before writing intake sidecars;
  retain the appropriate per-camera calibration through alignment, growth and merge.
- Refuse incomplete intake manifests and missing required intake flight logs.
- Do not manufacture position or altitude priors when a Sony intake has only a
  static fix or missing depth. Existing navigation workflows retain their policies.
- Non-interactive runs fail clearly on missing required arguments instead of prompting.
- Resume the workflow owned by an explicitly opened workspace rather than
  inheriting incompatible source selections from the previous session.
- Preserve pre-existing XMP copies before Python alignment/growth/merge changes
  sidecars. Require explicit opt-in for an oversized scene without positions.
- Keep restored legacy modules, dependencies, scripts, profiles, sensor-format data
  and flight-log formats available. Restore the public sidecar helper signatures.
- Keep generic spatial batching, component export and coordinate-frame behavior;
  Sony intake policies and explicit opt-in settings are separate.
- Match Windows image basenames in navigation files when inspecting a project on
  another host, without losing the collision checks.

### Validation boundary

Offline tests check software contracts and mocked native execution. The new Sony
calibration and alignment path still requires the documented native Windows/CUDA
first-run checks. No new reconstruction-quality or field-performance claim is
made by this compatibility correction.

## [1.0.0] - 2026-09-23

Initial release: a RealityScan 2.2 photogrammetry pipeline with a Textual
console, zone-based alignment and merge, and Cesium publishing.
