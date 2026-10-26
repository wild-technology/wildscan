# Contributing to Wild Scan

Wild Scan targets native Windows and RealityScan 2.2 for WCA, Zeuss and other
registered cameras, with an optional Sony ILX-LR1 Wild Sync intake. Use 64-bit Python 3.13 (3.12 is
the declared floor), from a Git checkout with an editable installation (`pip install -e .`).
A built wheel is not a supported way to run the pipeline.

## Set up and test

In PowerShell, from the checkout:

```powershell
py -3.13 -m venv .venv
& ".\.venv\Scripts\python.exe" -m pip install --upgrade "pip>=26.2"
& ".\.venv\Scripts\python.exe" -m pip install -e ".[dev]"
& ".\.venv\Scripts\python.exe" -m pip check
& ".\.venv\Scripts\python.exe" -m pytest -ra
```

The offline tests use temporary inputs and settings and need no RealityScan.
The EGM2008 geoid test skips when its grid is unavailable. Passing tests do not
establish native alignment, model quality or authenticated publishing.
`scripts/validation/check_preprocessing.py` checks preprocessing on copies of
your images; read its `--help` first.

## Make changes

- Reuse the existing helpers for paths, settings, cameras, flight logs and
  processes. Check callers, stored data and downstream stages before changing
  a contract.
- Shared camera and calibration data live in `modules/cameras.json`. The
  restored legacy georeferencer also retains its `MOUNTS` table; preserve that
  interface and its mount tests when changing generic navigation behavior.
  Preserve existing filename families, mounts, settings keys and public commands
  when adding cameras. Scope intake-specific policies to recordings that
  explicitly select that intake; generic datasets must keep their defaults.
  Keep `calibration/ilx_lr1_stereo_dive_001.json` in agreement with it (a test
  checks this) and never change a calibration number without a new calibration
  record.
- Run RealityScan only through `RealityScanCLI` and the `:run` pattern (see
  [Architecture](ARCHITECTURE.md#hard-rules)).
- Keep `.bat` and `.vbs` files in CRLF; `.gitattributes` sets the checkout rule.
- Use `snake_case` for Python files, functions and variables and `PascalCase`
  for classes. Preserve workflow file names, command-line flags and stored
  setting keys.
- Keep tests in `tests/` and manual checks in `scripts/validation/`. Keep
  source data, credentials, local settings, generated models and logs out of
  Git.
- Change `pyproject.toml` and `requirements.txt` together; a test checks that
  they agree.
- Add a focused test for every behaviour change, run it, then run the whole
  offline suite.
- Update the user documentation when behaviour changes. A statement about
  RealityScan behaviour that has not been checked against RealityScan must say
  so.

## Report a problem or submit a change

Include the Windows and Python versions, the Wild Scan commit, the
installation method, the steps to reproduce, and the expected and observed
results, with a small example where possible. Remove credentials and private
paths from logs.

A pull request explains the user-visible change and how it was validated,
including whether native RealityScan processing or live publishing was
exercised. See [Architecture](ARCHITECTURE.md) for implementation conventions
and the [documentation index](docs/README.md) for the user guides.
