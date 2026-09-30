# Product readiness backlog — wildscan as a shipping analysis product

Remaining product work covers reliability, delivery, usability and scientific
validation. The delivery goal is a unified component per major surveyed
feature (ON2026: hull, two masts, stern flag-pole), with provenance. Offline
checks do not establish native processing or delivery acceptance.

Update discipline: move completed items to DONE and record their commit
hash when committed. Every fix ships with a test or an empirical verification transcript.

## DONE

- Process-tree cancellation records interrupted stages and keeps the instance
  reserved until child processes stop. Failed and cancelled console stages can
  be retried. Native RealityScan cancellation still needs an installation check.
- Model resume checks assembly state, source components, navigation and recipe
  content. Scale-gate skips, model failures and failed dated saves produce a
  failed stage result.
- Publishing preserves unrelated staging directories, excludes generated
  Cesium derivatives from Nira input, and requires all requested destinations
  for stage completion. Dry-run plans preserve existing publication reports.
- 2026-08-09 calibration-prior question CLOSED by clean A/B/C ladder
  (confounds removed): manufacturer approximate intrinsics COLLAPSE
  ON2026 registration to 45% (2x replicated) — production stays
  calibration-prior-free; VOYIS values retained in cameras.json as
  reference data only. Explicit `-addImageWithCalibration` delivery
  validated; `-setPriorCalibrationGroup` proven silently
  non-functional from the CLI. FINDINGS 2026-08-09.
- 2026-08-08/09 flight-log-first architecture (project decision):
  docs/FLIGHTLOG_ARCHITECTURE.md design of record; probes closed
  (path rows match EXACT-PATH; the custom format GUID must resolve in the
  installed flightlogs.xml to preserve orientation and accuracy priors;
  re-import+`-update` re-places
  aligned components onto current priors, so per-step reload is
  verified). Landed: `batch_zone_layout='pool'` (canonical pool +
  full-path zone logs + .imagelist, fixes the merge no-fuse defect at
  root), `export_rs_flightlog --path-mode=absolute`, grow per-step
  reload (`--flight_log`). Remaining: align-from-imagelist .bat step
  (safe window), unified log writer across nav sources.

- 2026-08-08 `8e38316` batch 1: local-frame align unblocked
  (frame-derived FlightLogParams selection + `--r_flight_log_params`,
  unit-tested); merge-scene camera ceiling enforced pre-launch
  (`scene_ceiling_verdict`, default 34,000, C-20260802-01, tested incl.
  the recorded OOM scene); `--help` exits 0 with the full parser;
  `--r_min_component_size` exposed (field-validated same day: fixture
  align exported a 20-camera component at threshold 10); root
  requirements.txt (discovery: textual/rich absent on HONEYBADGER — the
  WildScan portal could not import and 26 of its tests were dormant; 461
  tests now pass, zero skips).

## MUST-FIX

### Blocks run2 (ON2026 per-feature delivery)
1. **The original [run_on2026_wreck.py](https://github.com/wild-technology/wildscan/blob/0401a5a04097cba149989f7e8c60e57c09c1c549/testing/run_on2026_wreck.py) is the retired plan** — monolith terminal stage,
   old campaign paths, nav-blind `zone_done()` (any .rsalign+.json =
   skip). Replace per the run2 architecture spec:
   folder-copy Z-aware zones from `M:\ON2026_run2\nav\flight_log_run2.txt`
   → per-zone aligns → per-feature merge complists under the ceiling →
   ModelToFinal per feature → deliverable manifest. Every merge arg
   explicit (rs_settings inheritance is a recorded incident). The campaign
   implementations now live in `scripts/campaigns/run_on2026_run2.py`,
   `run_on2026_run3.py`, and `run_on2026_union.py`; the retired driver is
   retained only in the ignored local archive and the Git snapshot above.
2. **Campaign resume and merge need fingerprint checks** — alignment now
   records flight-log, import-format and template hashes, repository SHA and
   executable metadata in `align_inputs.json`. Use that record in campaign
   `zone_done()` checks and verify frame/settings unanimity before merges.

### Scientific rigor (analysis-product claims)
3. **"Metric" is asserted, not measured** — ON2026_final_metric.obj's
   scale claim rests on the export preset + a vertex ratio (convention,
   not measurement); the scale oracle never ran on exported geometry.
   Add a post-export measured-scale verification + record the result.
4. **No provenance travels with deliverables** — `final\` holds geometry
   only. Emit DELIVERABLE_MANIFEST.json + rendered README per export
   (role/feature/path/format/scale/units/coordinate_frame/nav_version/mesh stats/sha256,
   plus variant and checkpoint roles); point the workspace census at it.
5. **No customer-visible accuracy statement** — the analysis-product
   question "what positional accuracy is this mesh?" is unanswerable from
   the deliverable. Derive from nav priors + solve residuals + scale-gate
   result; state it in the manifest/README.
6. **Settings persistence has zero provenance** (a
   REFUSED run's bogus path resurfaces as the next default identically to
   a good run's). rs_settings entries need {value, saved-when, campaign,
   last-run-outcome}; surface that provenance in the prompt default.
7. **Mixed alignment settings are not refused at merge** — zones record
   their input fingerprints, but merge needs to enforce compatible settings
   and frames (item 2).

### User experience
9. **Deliverable directory answers nothing** — no manifest/README (item
   4); `final\` absent from WildScan's results taxonomy (session.py); the
   scale-100 Unreal build carries the plain "final" name while the metric
   analysis product carries a suffix. Fix naming + taxonomy + manifest
   together.
10. **ModelToFinal silently overwrites a prior deliverable** on same-name
    re-export (no existence check/versioning) — and fixed internal model
    names collide on re-run in a persistent scene.
11. **Non-tty prompts silently self-answer** with stored/fallback
    defaults; piped answers are not read. Non-tty + missing required value
    must fail fast naming the
    flag (partially true today only for output_dir); a `--yes` flag
    should be the only auto-accept path.
12. **No pre-run confirmation gate** — the "Parameters:" echo prints
    after prompts and execution starts immediately. Implement the
    PRE-FLIGHT CHECKLIST (data locations w/ provenance,
    write-into-originals consent, resolved coordinate frame, priors
    accuracies vs campaign, detector/texture budgets, instance+cache,
    disk headroom vs stage estimate, resume state) with explicit
    confirm; `--yes` for unattended.
13. **StatusScreen unreachable without launching a run** (wildscan).
14. **Wizard prefill precedence inverted** — previous survey's persisted
    answers override fresh auto-detection for a NEW dive
    (wrong-provenance risk, verified app.py:275).

## MUST-FIX — added from the goal-verification decisions (2026-08-08)

15. **Stop the staging rename** (project decision, N2): RhodyProc
    stereo_rename.py strips the left/right eye token from VOYIS original
    filenames, manufacturing the basename collision the L_/R_ views then
    solve. Original filenames are preserved end-to-end; pairing keys on
    the eye token in the ORIGINAL name; disambiguation only on true
    collision. (Cross-repo: RhodyProc + the colmap_studio bridge.)
16. **Calibration registry update** (requested): Zeuss 25 mm
    rectilinear, Port/Starboard/Upper/Mid 15 mm fisheye, Cinema 17 mm
    rectilinear — replaces the 2026-07-23 values (23/14 mm) in
    cameras.json, camera_registry XMP content, ARCHITECTURE.md, georeference_survey
    constants, wildscan OFFICIAL_CAMERAS, and the rig-mount tests, in
    ONE change set.
17. **Unknown-camera intake flow**: ask the user for what is known
    (never require invented numbers — supersedes the wizard
    invented-lever-arm finding); DIVISION is the default distortion
    model across all camera types.
18. **Video robustness**: h.264 AND h.265 across varied container
    extensions; HEIC reader (pillow-heif) across all six extension
    filters — all four data families are v1 scope.
19. **Naming scheme implementation**: `{expedition}_{dive}_{product}`
    with explicit `LOCAL`/zone tags in flight-log names (parser learns
    the LOCAL tag); Expedition/Dive/[data] deliverable trees; ON2026
    merged code `ON2026_RH0041_RH2042` (verify RH2042 vs RH0042 before
    first use).

## SHOULD-FIX
- Resolved-settings provenance banner at merge startup (CLI vs stored vs
  fallback per arg).
- batch_use_z silent 2D degeneration when the alt column is missing
  (still logs "Z-aware batching").
- pair_gate 2D bboxes chain vertically-separated zones (extend manifests
  with Z range; 3D overlap when present).
- GenerateModel: keep-largest-component unconditional + large-triangle
  cull — thin-feature hostile on the run_models/driver path (ModelToFinal
  avoids it); parameterize; delete the dead `_HighPoly` cleanup entry.
- shared_graph_spans admits merge rungs on basenames without path
  identity (rigid-glue scored by additive counts) — acknowledged hazard,
  ungated.
- build_union_flight_log sources per-zone logs (stale-frame risk);
  local-frame decision reads first-in-walk-order log; add unanimity check
  (partially fixed 7a22b51 — verify remaining gap).
- AlignZone applier silently drops the 7 GUI-obfuscated keys
  (s235l/s236l/s237l/s251l–s254l) from AlignmentParams.xml.
- Direct .bat invocation path: instance-name collision at boot yields a
  raw HRESULT; frame guard not applied.
- Silent 900 s shutdown-verify wait on cancel/failure; orphaned instance
  left with no PID named and no recovery instruction.
- "Re-run resumes from successful zones" failure message is untrue —
  every re-run supersedes and redoes the zone; align either resumes or
  the message changes.
- Progress UX during multi-hour ops: unlabeled raw tuples; zone-count
  granularity bar.
- Campaign-level retry organization: sibling roots hand-minted per merge
  retry (merged, merged_lt005, ...) with no product support or record.
- Model names don't identify surveyed features ("maximal_HighPoly_
  Textured") — conflicts with one-component-per-feature delivery.
- run_models.py workspace mode reaches only latest_merge() and cannot
  pass the triangle threshold.
- Camera wizard requires invented lever-arm/tilt numbers for unknown
  prefixes (fabricated-provenance risk).
- SessionScreen re-scans the filesystem on every keystroke in path
  fields.
- Resolve the RealityScan build version from the executable, beyond the
  path/size/mtime recorded in alignment fingerprints.
- Folder-copy zoning duplicates large image trees where validated hardlinks
  could reduce disk use.
- Per-zone flight logs inherit the master's row for EVERY member incl.
  overlap donations — fine — but zone logs and components carry no frame
  tag (FRAME_WARNING markers are manual); fold frame identity into item
  2's fingerprint.

## NITS
- Logging-formatted prompts + Matplotlib/Seaborn version banner as the
  first user-visible line of every invocation.
- Attempt ordinals carry no "accepted" marker in merged trees.

## Delivery and confirmation fields
- PRE-FLIGHT CHECKLIST: data locations and provenance, write destinations,
  coordinate frame, prior accuracies, processing budgets, instance/cache,
  free space and resume state.
- DELIVERABLE_MANIFEST.json: primary/variant/project/checkpoint roles,
  feature labels, navigation version, scale, units, coordinate frame, mesh
  statistics and file hashes.
