# Documentation

WildScan prepares ROV imagery and navigation for RealityScan 2.2, supervises
alignment and reconstruction workflows, and organizes their outputs. Start
with the guides below.

## Start here

| Guide | Use it for |
|---|---|
| [Analyze a dataset](ANALYZE_A_DATASET.md) | First-use input checks, workspace inspection, stage selection, and interpreting results |
| [Setup and run](SETUP-AND-RUN.md) | Windows installation, native prerequisites, configuration, command-line use, and publishing |
| [Project overview](../README.md) | Supported workflow and repository layout |
| [Architecture](../ARCHITECTURE.md) | Code structure, shared execution layer, settings, and operating practices |
| [RealityScan CLI reference](rs-reference/README.md) | Commands, settings, parameter files, observed failure modes, and empirical evidence |
| [Product scope and validation](PRODUCT_READINESS.md) | Current validation boundaries |
| [Optional Sony Wild Sync intake](ILX-LR1.md) | Import Sony recordings without changing the WCA and Zeuss workflow |
| [Third-party notices](../THIRD_PARTY_NOTICES.md) | Dependency licences and SPDX SBOM |

The processing target is native Windows 11 with RealityScan 2.2. A successful
offline test run establishes the tested software behavior; it does not
establish native acceptance or the scientific accuracy of a reconstruction.

Manual validation tools are in `scripts/validation/`. Some run native processing or create remote assets; read their help before use.
