"""Reparse-point guard for the peel harvest.

The peel harvest is a PowerShell `Get-ChildItem -Recurse`, which does NOT
descend into junction children, and RealityScan writes no XMP sidecars when
a scene's images resolve through a reparse point. An image root containing
directory junctions therefore yields an empty peel on every attempt -
indistinguishable from a legitimately empty scene.

Every live driver shares this ONE implementation of `assert_harvestable`.
Tests: tests/test_harvest_guard.py.
"""
from __future__ import annotations

import logging
import os


def assert_harvestable(images_root: str, logger: logging.Logger) -> None:
    """The peel harvest is a PowerShell `Get-ChildItem -Recurse`, which does
    NOT descend into junction CHILDREN at ANY depth. Handing merge_zones.py a
    directory containing reparse points yields an empty peel on every attempt,
    which the driver cannot distinguish from a legitimately empty scene, so
    a real fusion would be silently discarded. The scan is recursive: a
    junction one level down (a zone subfolder that is a link) reproduces the
    blindness just as completely as a top-level one.
    """
    def is_reparse(path: str) -> bool:
        if os.path.islink(path):
            return True
        # islink() is False for Windows junctions on some Python builds;
        # the reparse attribute is the reliable test.
        try:
            return bool(os.stat(path, follow_symlinks=False).st_reparse_tag)
        except (AttributeError, OSError):
            return False

    reparse = []
    for dirpath, dirnames, _files in os.walk(images_root):
        for name in list(dirnames):
            full = os.path.join(dirpath, name)
            if is_reparse(full):
                reparse.append(os.path.relpath(full, images_root))
                dirnames.remove(name)   # do not descend into the link
    if reparse:
        raise RuntimeError(
            f"images_root {images_root} has reparse-point children {reparse}; "
            "the peel harvest cannot cross them. Pass the real image tree.")
    logger.info("images_root %s is harvestable (no reparse-point children)",
                images_root)
