# Documentation

WildScan prepares ROV imagery and navigation for RealityScan 2.2, supervises
alignment and reconstruction workflows, and organizes their outputs. Start
with the current guides below; the dated research records explain earlier
decisions and observations.

## Start here

| Guide | Use it for |
|---|---|
| [Analyze a dataset](ANALYZE_A_DATASET.md) | First-use input checks, workspace inspection, stage selection, and interpreting results |
| [Setup and run](SETUP-AND-RUN.md) | Windows installation, native prerequisites, configuration, command-line use, and publishing |
| [Project overview](../README.md) | Supported workflow and repository layout |
| [Architecture](../ARCHITECTURE.md) | Code structure, shared execution layer, settings, and operating practices |
| [RealityScan CLI reference](rs-reference/README.md) | Commands, settings, parameter files, observed failure modes, and empirical evidence |
| [Product scope](PRODUCT_READINESS.md) | Current validation boundaries and implemented reliability behavior |

The processing target is native Windows 11 with RealityScan 2.2. A successful
offline test run establishes the tested software behavior; it does not
establish native acceptance or the scientific accuracy of a reconstruction.

## Historical records

These documents preserve observations and decisions from the stated date.
Their dataset paths, counts, proposed changes, and open issues belong to that
record. Use current source and the guides above for today's commands and
behavior.

| Record | Context |
|---|---|
| [H2023 workflow walkthrough](WORKFLOW_WALKTHROUGH.md) | July 2026 worked example, including proposals that were implemented later |
| [Flight-log-first architecture](FLIGHTLOG_ARCHITECTURE.md) | August 2026 investigation and implementation plan; calibration XMP remains part of the current workflow |
| [Merge growth strategy](merge-growth-strategy-2026-07.md) | July 2026 component-growth experiments and reasoning |
| [Merge rework recommendations](MERGE_REWORK_RECOMMENDATIONS.md) | Dated review of the earlier merge/growth design |
| [Settings evaluation](settings-evaluation-2026-07.md) | July 2026 native measurements and settings decisions |
| [First-machine code review](code-review-2026-07.md) | July 2026 Windows validation of the earlier execution layer |
| [COLMAP crossover](COLMAP_CROSSOVER.md) | July 2026 reconciliation questions between separate COLMAP and RealityScan workflows |
| [COLMAP fact base](COLMAP_FINDINGS_UNIFIED.md) | Frozen external research record received in July 2026 |
| [Alignment and merge hardening plan](validation/alignment_merge_hardening_plan.md) | Historical validation plan |
| [Euler pin test plan](validation/euler_pin_test_plan.md) | Historical orientation-prior experiment |
| [Merge strategy report](validation/merge_strategy_report.md) | Historical merge experiment results |
| [Merge test plan](validation/merge_test_plan.md) | Historical merge validation plan |
| [Priors and distortion test plan](validation/priors_distortion_test_plan.md) | Historical camera-prior experiment |
| [Zone 9 test plan](validation/zone9_test_plan.md) | Historical native validation plan |

Preserved transcripts, summaries, and forensic logs live in
[`validation/results/`](validation/results/). Their original commands and
machine paths are evidence, not an installation recipe. The current manual
validation entry points live in `scripts/validation/`; they are separate from
the offline suite in `tests/` and some run native processing or create remote
assets.

References to `FINDINGS`, campaign IDs, and predecessor commits identify the
original engineering record. That log is external to this repository; the
reference documents state the supported observation in place. Retired source
is available in the
[original Git snapshot](https://github.com/wild-technology/wildscan/tree/0401a5a04097cba149989f7e8c60e57c09c1c549/archive),
while a local `archive/` remains ignored.
