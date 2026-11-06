# Product scope and validation

WildScan is a beta workflow for preparing, reconstructing, and reviewing ROV
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
