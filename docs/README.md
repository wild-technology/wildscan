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
| [Euler pin test plan](validation/euler_pin_test_plan.md) | Historical orientation-prior experiment |

The current manual validation entry points live in `scripts/validation/`; they
are separate from the offline suite in `tests/`. Earlier dated test plans,
decision records, reviews and result logs are preserved in the repository
history at commit `ea3ad5d`; the RealityScan reference cites them as
`ea3ad5d:docs/<path>`, readable with `git show`.

References to `FINDINGS`, campaign IDs, and predecessor commits identify the
original engineering record. That log is external to this repository; the
reference documents state the supported observation in place. Retired source
is available in the
[original Git snapshot](https://github.com/wild-technology/wildscan/tree/0401a5a04097cba149989f7e8c60e57c09c1c549/archive),
while a local `archive/` remains ignored.
