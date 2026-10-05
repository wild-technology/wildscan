"""WildScan - Wild Technology's interactive subsea photogrammetry console.

A Textual interface over the repository's canonical pipeline drivers:
main.py's module chain, merge_zones.py, the native workflows, and the
publishing scripts. The supported processing platform is native Windows.
Dataset inspection and saved-result browsing use the same source and
reports as the drivers; they do not verify native processing or model quality.

Run:  wildscan [workspace]   (or: python -m wildscan [workspace])
"""
from __future__ import annotations

__version__ = "2.0.0"
APP_NAME = "WildScan"
ORG = "Wild Technology"
TAGLINE = "Subsea Photogrammetry Pipeline"
