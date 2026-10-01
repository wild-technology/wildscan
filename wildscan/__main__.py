"""Entry point:  wildscan [workspace]   (or: python -m wildscan [workspace])"""
from __future__ import annotations

import sys

try:
    from .app import main
except ImportError as exc:
    raise SystemExit(
        f"missing dependency: {exc.name}. Install WildScan from the checkout "
        "with this interpreter (PowerShell):\n"
        f'    & "{sys.executable}" -m pip install -e .') from exc

if __name__ == "__main__":
    sys.exit(main())
