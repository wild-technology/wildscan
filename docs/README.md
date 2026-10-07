# Documentation

Wild Scan turns Wild Sync recordings of the two-camera Sony ILX-LR1 stereo
rig into aligned, georeferenced and textured RealityScan 2.2 models.

| Document | Use it for |
|---|---|
| [Setup and run](SETUP-AND-RUN.md) | Windows installation, RealityScan prerequisites, configuration, the stages on the command line, publishing, troubleshooting |
| [Analyze a dataset](ANALYZE_A_DATASET.md) | Processing a Wild Sync run in the application: inputs, screens, stages, results |
| [The ILX-LR1 stereo rig](ILX-LR1.md) | The rig, its calibration and when it is applied, the node-assignment switch, the Wild Sync input and flight log, the orientation convention, the distortion model, preprocessing, and what is not yet validated |
| [First-run validation checklist](validation/ILX-LR1_first_run_checklist.md) | Validating orientation, distortion, calibration applicability, the flight-log format and the calibration command file on the first real alignment |
| [Project overview](../README.md) | What Wild Scan does, validation status, repository layout |
| [Architecture](../ARCHITECTURE.md) | Data flow, code structure, RealityScan execution layer, hard rules |
| [Contributing](../CONTRIBUTING.md) | Development setup and change rules |
| [RealityScan CLI reference](rs-reference/README.md) | RealityScan 2.2 commands, settings, parameter files and failure modes, from RealityScan's documentation extended by this project's testing |
| [Calibration record](../calibration/README.md) | The ILX-LR1 stereo calibration file |

No live RealityScan alignment has yet been run with this version; the
first-run validation checklist is the place to start on a new installation.
A passing offline test suite establishes the tested software behaviour, not
native acceptance or the accuracy of a reconstruction.

Much of the RealityScan reference's empirical evidence comes from earlier
datasets taken with other cameras; its [README](rs-reference/README.md)
explains the provenance tags and the scope of that evidence.
