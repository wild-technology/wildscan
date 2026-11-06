"""Compatibility exports for the processing-layer workspace census.

The implementation lives in modules.workspace_census so both drivers and the
terminal interface share the same output and stage-status interpretation.
"""
from __future__ import annotations

from modules.workspace_census import (  # noqa: F401
    IMAGE_EXTS,
    MODEL_REPORT_NAMES,
    STAGE_ORDER,
    STAGE_TITLES,
    ComponentInfo,
    StageStatus,
    Workspace,
    _count_images,
    _find_flight_logs,
    _load_json,
    _records,
)

__all__ = [
    "IMAGE_EXTS", "STAGE_ORDER", "STAGE_TITLES",
    "ComponentInfo", "StageStatus", "Workspace",
]
