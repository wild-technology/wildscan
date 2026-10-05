# Product scope

WildScan is a beta workflow for preparing, reconstructing, and reviewing
photogrammetry datasets. For a first survey, use
[Analyze a dataset](ANALYZE_A_DATASET.md) and
[Setup and run](SETUP-AND-RUN.md).

## Current validation boundaries

- Intake identifies candidate files and camera filename families. It does not
  verify video decoding, navigation coverage, survey completeness, or optical
  calibration. Confirm those inputs before processing.
- Workspace status is derived from saved artifacts and reports. Modern resume
  checks bind relevant inputs to a recorded result; legacy evidence may remain
  readable without establishing that the current stage is complete.
- A scale verdict describes the recorded component measurement. It does not
  establish mesh accuracy, seam quality, absolute positional accuracy, or the
  suitability of an exported model for a customer's analysis.
- Native workflows and remote publishers need their own installation and
  dataset checks. Offline passing tests do not establish native processing or
  delivery acceptance. Use a small working copy before a large reconstruction.

## Implemented reliability behavior

- Process-tree cancellation records interrupted stages and keeps the instance
  reserved until child processes stop. Failed and cancelled console stages can
  be retried. Native RealityScan cancellation still needs an installation check.
- Model resume checks assembly state, source components, navigation and recipe
  content. Scale-gate skips, model failures and failed dated saves produce a
  failed stage result.
- Publishing preserves unrelated staging directories, excludes generated
  Cesium derivatives from Nira input, and requires all requested destinations
  for stage completion. Dry-run plans preserve existing publication reports.
