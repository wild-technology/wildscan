#!/usr/bin/env python3
"""Upload a RealityScan export to Nira as a photogrammetry asset.

RealityScan 2.2 has a built-in "Upload to Nira" (Share tab) but it is
GUI-only. The scripted path is Nira's official client
(github.com/NiraOfficial/niraclient) - NOTE it requires a Nira ENTERPRISE
plan; Individual/Professional accounts are browser-upload only. This wrapper
builds the recommended JSON file list (Nira's docs: image files auto-detect
unreliably, so every file is typed explicitly) and drives `nira.py`.

Nira format guidance for RealityScan (help.nira.app article 5591333681307):
OBJ, "Save mesh by parts: Yes", NO vertex colors, decimal-6 numbers, matched
cartesian project/output CRS - which is exactly what
ExportDeliverables.bat's OBJ export produces. Include the .rcInfo file (it
carries georeferencing) and the .mtl + textures. PLY point clouds are NOT
accepted (LAS/LAZ/E57 only) and point clouds must be part of the INITIAL
upload - they cannot be appended later.

One-time setup:
    git clone https://github.com/NiraOfficial/niraclient
    py -3.13 niraclient/nira.py configure     (org admin API key)

Example:
    py -3.13 publish_nira.py --name "IN-401 hull" \
        --dir F:/na156_h2024_v2/exports/cluster_0_a2_c0/obj \
        --niraclient C:/tools/niraclient
"""
from __future__ import annotations

import argparse
import json
import logging
import subprocess
import sys
from pathlib import Path

from publish_cesium import (PART_RE, STAGING_MARKER, referenced_companions, select_objs)
from modules.publish_fingerprint import GEOMETRY, POINTCLOUD, SIDECAR, source_files

logger = logging.getLogger('publish_nira')

MATERIAL = {'.mtl'}
TEXTURE = {'.jpg', '.jpeg', '.png', '.tiff', '.tif', '.bmp'}


def build_file_list(directory: Path, parts: str = 'split',
                    geometry: str | None = None) -> list[dict]:
    """Select one mesh representation, excluding local Cesium derivatives."""
    if directory.name.lower() == '_cesium_local' or \
            (directory / STAGING_MARKER).is_file():
        raise SystemExit(f'Cesium staging is not a source export: {directory}')
    try:
        files = source_files(directory)
    except (OSError, ValueError) as exc:
        raise SystemExit(str(exc)) from None

    meshes = [p for p in files if p.suffix.lower() in GEOMETRY]
    formats = {p.suffix.lower()[1:] for p in meshes}
    if geometry is None:
        if len(formats) > 1:
            raise SystemExit(
                f'multiple mesh formats under {directory}: '
                f'{", ".join(sorted(formats))}; select one with --geometry')
        geometry = next(iter(formats), None)
    chosen = {p for p in meshes if p.suffix.lower() == f'.{geometry}'}
    if geometry == 'obj':
        chosen = {obj for parent in sorted({p.parent for p in chosen})
                  for obj in select_objs(parent, parts)}
        chosen.intersection_update(files)
        companions = set(referenced_companions(sorted(chosen), TEXTURE))
        if companions - set(files):
            raise SystemExit('a material or texture is outside the source '
                             'export or inside excluded Cesium staging')
    else:
        companions = {p for p in files if p.suffix.lower() in MATERIAL | TEXTURE}

    if geometry is not None and not chosen:
        raise SystemExit(f'no {geometry} geometry found under {directory}')
    clouds = {p for p in files if p.suffix.lower() in POINTCLOUD}
    model_names = {p.name.lower() for p in chosen | clouds}
    model_names.update((match.group('stem') + p.suffix).lower()
                       for p in chosen if p.suffix.lower() == '.obj'
                       and (match := PART_RE.match(p.stem)))
    sidecar_names = {name + ext for name in model_names for ext in SIDECAR}
    sidecars = {p for p in files if p.name.lower() in sidecar_names}
    included = chosen | companions | sidecars | clouds
    entries: list[dict] = []
    for path in sorted(included):
        ext = path.suffix.lower()
        if ext in GEOMETRY or ext in MATERIAL or ext in SIDECAR:
            entries.append({'path': str(path)})
        elif ext in TEXTURE:
            entries.append({'path': str(path), 'type': 'image'})
        elif ext in POINTCLOUD:
            entries.append({'path': str(path)})
    if not any(Path(e['path']).suffix.lower() in GEOMETRY | POINTCLOUD
               for e in entries):
        raise SystemExit(f'no geometry or point cloud found under {directory}')
    return entries


def main() -> int:
    logging.basicConfig(level=logging.INFO, format='%(levelname)s %(message)s')
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--name', required=True, help='Nira asset name')
    parser.add_argument('--dir', required=True,
                        help='export directory (obj/ from ExportDeliverables)')
    parser.add_argument('--niraclient', required=True,
                        help='path to a checkout of NiraOfficial/niraclient')
    parser.add_argument('--parts', choices=('whole', 'split'), default='split',
                        help='OBJ representation when whole and by-parts '
                             'copies coexist (default: split)')
    parser.add_argument('--geometry', choices=('obj', 'fbx', 'dae', 'gltf', 'glb'),
                        help='mesh format to upload (required when several '
                             'formats coexist)')
    parser.add_argument('--wait', type=int, default=0,
                        help='seconds to wait for server-side processing '
                             '(0 = do not wait)')
    parser.add_argument('--dry-run', action='store_true',
                        help='print the file list and command, upload nothing')
    args = parser.parse_args()

    nira_py = Path(args.niraclient) / 'nira.py'
    if not nira_py.is_file():
        raise SystemExit(
            f'nira.py not found at {nira_py}. Clone '
            'github.com/NiraOfficial/niraclient and run "nira.py configure" '
            'first (requires a Nira Enterprise plan API key).')

    directory = Path(args.dir)
    if not directory.is_dir():
        raise SystemExit(f'not a directory: {directory}')

    entries = build_file_list(directory, args.parts, args.geometry)
    payload = json.dumps(entries, indent=2)
    logger.info('%d file(s) for asset %r', len(entries), args.name)

    cmd = [sys.executable, str(nira_py), 'asset', 'create',
           args.name, 'photogrammetry']
    if args.wait:
        cmd += ['--wait-for-asset-processing', str(args.wait)]

    if args.dry_run:
        print(payload)
        print('command:', ' '.join(cmd), '< filelist.json')
        return 0

    proc = subprocess.run(cmd, input=payload, text=True,
                          capture_output=True)
    if proc.stdout:
        logger.info('%s', proc.stdout.strip())
    if proc.returncode != 0:
        logger.error('niraclient failed (%d):\n%s', proc.returncode,
                     proc.stderr.strip())
        return proc.returncode
    return 0


if __name__ == '__main__':
    sys.exit(main())
