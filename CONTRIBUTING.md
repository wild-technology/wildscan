# Contributing to WildScan

WildScan targets native Windows and RealityScan 2.2. Use 64-bit Python 3.13
or newer for development, and keep the checkout available: the full pipeline
uses an editable installation and its root driver scripts.

## Set up and test

From a Git checkout in PowerShell:

```powershell
py -3.13 -m venv .venv
& ".\.venv\Scripts\python.exe" -m pip install --upgrade "pip>=26.2"
& ".\.venv\Scripts\python.exe" -m pip install -e ".[dev]"
& ".\.venv\Scripts\python.exe" -m pip check
& ".\.venv\Scripts\python.exe" -m pytest -ra
```

The offline tests use temporary inputs and settings. The EGM2008 geoid test
skips when its grid is unavailable. Passing tests do not establish native
alignment, model quality, or authenticated publishing. Manual validation
tools in `scripts/validation/` can process data or create remote assets;
read their help before using them.

## Make changes

- Reuse existing path, settings, camera, and process helpers. Check callers,
  serialized data, numeric types, and downstream effects before changing a contract.
- Use `snake_case` for Python files, functions, and variables and `PascalCase`
  for classes. Preserve native workflow filenames, CLI flags, saved setting
  keys, and legacy compatibility aliases.
- Keep `.bat` and `.vbs` files in CRLF format; `.gitattributes` supplies the
  checkout rules. Route native execution through `RealityScanCLI`.
- Keep tests in `tests/`, manual checks in `scripts/validation/`, and
  dataset-specific drivers in `scripts/campaigns/`. Keep source data,
  credentials, local settings, generated models, and runtime logs out of Git.
- Update `pyproject.toml` and `requirements.txt` together when changing
  dependencies. The dependency regression checks their semantic equivalence.
- Add a focused regression for changed behavior. Run relevant tests first,
  then the offline suite before submitting a change.
- Update current user guides when behavior changes. Preserve dated experiment
  evidence and identify conclusions superseded by later measurements.

## Report a problem or submit a change

Include the Windows and Python versions, WildScan commit, installation method,
steps to reproduce, and the expected and observed results. Use a small example
when possible. Remove credentials and private dataset paths from logs.

A pull request should explain the user-visible change and its validation.
State whether native RealityScan processing or live publishing was exercised.
See [the architecture](ARCHITECTURE.md) for implementation conventions and
[the documentation index](docs/README.md) for user guides and reference material.
