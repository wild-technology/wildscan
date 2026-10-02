"""Publication source identities shared by the batch writer and census.

The writer hashes selected payloads. The census compares their sizes and
nanosecond modification times, plus candidate names, without rereading large
meshes and textures on every refresh. Edits that preserve both file metadata
values require a new publication to establish fresh content evidence.
"""
from __future__ import annotations

import os
from pathlib import Path

from .align_fingerprint import sha256_file

STAGING_MARKER = '.cesium-stage.json'
GEOMETRY = {'.obj', '.fbx', '.dae', '.gltf', '.glb'}
POINTCLOUD = {'.las', '.laz', '.e57'}
SIDECAR = {'.rcinfo', '.rsinfo'}


def source_files(directory: Path) -> list[Path]:
    """Original files under an export, excluding staged copies and aliases."""
    if (not directory.is_dir() or directory.is_symlink() or directory.is_junction()
            or directory.name.lower() == '_cesium_local'
            or (directory / STAGING_MARKER).is_file()):
        raise ValueError(f'not an original export directory: {directory}')
    files = []

    def failed(error: OSError) -> None:
        raise error

    for root, dirs, names in os.walk(directory, followlinks=False, onerror=failed):
        base = Path(root)
        dirs[:] = [name for name in dirs
                   if name.lower() != '_cesium_local'
                   and not (base / name / STAGING_MARKER).is_file()
                   and not (base / name).is_symlink()
                   and not (base / name).is_junction()]
        files.extend(base / name for name in names
                     if (base / name).is_file()
                     and not (base / name).is_symlink())
    return sorted(files)


def _candidates(directory: Path, destinations: list[str]) -> list[str]:
    """Names whose addition/removal can change the publisher's file choice."""
    files = source_files(directory)
    if 'nira' in destinations:
        eligible = [p for p in files if p.suffix.lower() in GEOMETRY | POINTCLOUD | SIDECAR]
    else:
        eligible = [p for p in files if p.parent == directory
                    and p.suffix.lower() in {'.obj'} | SIDECAR]
    return sorted(p.relative_to(directory).as_posix() for p in eligible)


def publication_source_state(directory: Path, files: list[Path],
                             destinations: list[str]) -> dict:
    """Content evidence for the chosen payload and its source selection."""
    root = directory.resolve()
    entries = []
    for path in sorted(set(files)):
        resolved = path.resolve()
        if not resolved.is_relative_to(root) or path.is_symlink():
            raise ValueError(f'publication payload is outside the export: {path}')
        before = path.stat()
        digest = sha256_file(str(path))
        after = path.stat()
        if digest is None or (before.st_size, before.st_mtime_ns) != (
                after.st_size, after.st_mtime_ns):
            raise ValueError(f'publication source changed while hashing: {path}')
        entries.append({'path': path.relative_to(directory).as_posix(),
                        'bytes': after.st_size, 'mtime_ns': after.st_mtime_ns,
                        'sha256': digest})
    if not entries:
        raise ValueError(f'no publication payload under {directory}')
    return {'schema': 1, 'root': str(root), 'destinations': destinations,
            'candidates': _candidates(directory, destinations), 'files': entries}


def publication_source_matches(directory: Path, saved: object) -> bool:
    """Cheap source check; recorded hashes document content at submission."""
    if not isinstance(saved, dict) or saved.get('schema') != 1:
        return False
    entries, destinations = saved.get('files'), saved.get('destinations')
    if not isinstance(entries, list) or not entries or not isinstance(destinations, list):
        return False
    if not destinations or any(d not in ('cesium', 'nira') for d in destinations):
        return False
    try:
        root = directory.resolve()
        if saved.get('root') != str(root) or saved.get('candidates') != _candidates(
                directory, destinations):
            return False
        seen = set()
        for entry in entries:
            if not isinstance(entry, dict) or not isinstance(entry.get('path'), str):
                return False
            relative = Path(entry['path'])
            if relative.is_absolute() or '..' in relative.parts or relative in seen:
                return False
            seen.add(relative)
            path = directory / relative
            if path.is_symlink() or not path.resolve().is_relative_to(root):
                return False
            digest = entry.get('sha256')
            if not isinstance(digest, str) or len(digest) != 64:
                return False
            current = path.stat()
            if not path.is_file() or (entry.get('bytes'), entry.get('mtime_ns')) != (
                    current.st_size, current.st_mtime_ns):
                return False
        return True
    except (OSError, ValueError):
        return False
