@echo off
setlocal
:: One-click launcher for the zone_9 CLI test plan (see docs/validation/zone9_test_plan.md).
:: Pass-through args go to run_zone9_validation.py (e.g. run_zone9_validation.bat --full).

set "TEST_PYTHON=%VIRTUAL_ENV%\Scripts\python.exe"
if not defined VIRTUAL_ENV set "TEST_PYTHON=%~dp0..\..\.venv\Scripts\python.exe"
if not exist "%TEST_PYTHON%" (
    echo ERROR: Activate the repository environment or create .venv with Python 3.13+.
    echo Install the project there with: python -m pip install -e ".[dev]"
    goto :failed
)

"%TEST_PYTHON%" -c "import sys; sys.exit(0 if sys.version_info >= (3, 13) else 1)"
if errorlevel 1 (
    echo ERROR: The native test plan requires Python 3.13+.
    goto :failed
)

"%TEST_PYTHON%" -c "import cv2, numpy"
if errorlevel 1 (
    echo ERROR: Install the project dependencies in the selected environment.
    goto :failed
)

"%TEST_PYTHON%" "%~dp0run_zone9_validation.py" %*
exit /b %errorlevel%

:failed
exit /b 1
