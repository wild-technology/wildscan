"""Entry point:  wildscan [workspace]   (or: python -m wildscan [workspace])"""
from __future__ import annotations

import sys

try:
    from .app import main
except ImportError as exc:
    raise SystemExit(
        f"missing dependency: {exc.name}. Install WildScan from the checkout "
        "(with the virtual environment active):\n"
        '    python -m pip install -e ".[tui]"') from exc

if __name__ == "__main__":
    sys.exit(main())
