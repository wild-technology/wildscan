"""Entry point:  wildscan [workspace]   (or: python -m wildscan [workspace])"""
from __future__ import annotations

import sys
from pathlib import Path


def main() -> int:
    """Load the UI with the same startup diagnostics for both launch routes."""
    checkout = Path(__file__).resolve().parent.parent
    try:
        from .app import main as app_main
    except ModuleNotFoundError as exc:
        if exc.name and exc.name.split('.')[0] in {'wildscan', 'modules', 'module_base'}:
            raise SystemExit(
                f'Unable to load WildScan: {exc}. Use a complete Git checkout '
                'and install it in editable mode.') from exc
        executable_arg = sys.executable.replace("'", "''")
        checkout_arg = str(checkout).replace("'", "''")
        raise SystemExit(
            f'missing dependency: {exc.name}. Install WildScan with this '
            'interpreter (PowerShell):\n'
            f"    & '{executable_arg}' -m pip install -e '{checkout_arg}'") from exc
    except ImportError as exc:
        raise SystemExit(
            f'Unable to load WildScan: {exc}\n'
            f'See {checkout / "docs" / "SETUP-AND-RUN.md"} for troubleshooting.') from exc
    return app_main()


if __name__ == "__main__":
    sys.exit(main())
