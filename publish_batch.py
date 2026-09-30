#!/usr/bin/env python3
"""Publish every exported component of a workspace to Cesium ion and/or Nira.

Loops exports/<component>/obj (the format BOTH platforms recommend for
photogrammetry) and drives publish_cesium.py / publish_nira.py per component.
Each destination activates only when its credentials are present, and
--dry-run previews every command without uploading anything:

    Cesium ion   CESIUM_ION_TOKEN env var (assets:write + assets:read)
    Nira         NIRACLIENT_DIR env var -> a configured niraclient checkout
                 (Enterprise plan; run `nira.py configure` once)

Results land in <workspace>/publish_report.json so WildScan can show them.
Dry runs write publish_plan.json and preserve the publication report.

Usage:
    py -3.13 publish_batch.py --workspace F:/na156_h2024_v2 \
        --prefix "IN-401" [--flight-log <log>] [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
import time
from pathlib import Path

from modules.cesium_placement import find_rsinfo
from modules.publish_fingerprint import (
    publication_source_matches, publication_source_state)
from publish_cesium import referenced_companions, select_objs
from publish_nira import build_file_list

REPO = Path(__file__).resolve().parent

logger = logging.getLogger('publish_batch')


def resolve_flight_log(workspace: Path) -> Path | None:
    """The workspace's own zone-tagged flight log, or None.

    Searches the merge output (whose union log is what the exported
    components were built against) before raw_images/ and the root, and
    raises SystemExit when zone-tagged logs DISAGREE rather than picking
    one - the same rule flight_logs.find_flight_log applies.
    """
    if str(REPO) not in sys.path:
        sys.path.insert(0, str(REPO))
    from modules.flight_logs import assert_one_zone, crs_for_flight_log
    logs = sorted(workspace.glob('*/flight_log*_UTM.txt')) + \
        sorted((workspace / 'raw_images').glob('flight_log*_UTM.txt')) + \
        sorted(workspace.glob('flight_log*_UTM.txt'))
    tagged = [p for p in logs if crs_for_flight_log(str(p))]
    if not tagged:
        return None
    try:
        assert_one_zone([str(p) for p in tagged], str(workspace))
    except ValueError as exc:
        raise SystemExit(f'cannot resolve a flight log: {exc}') from None
    return tagged[0]


def resolve_input_crs(workspace: Path) -> str | None:
    """``'EPSG:32654'`` from the workspace's own flight log, or None.

    Kept as a cross-check only. The CRS that actually places a mesh now
    comes from its ``.rsInfo`` sidecar, which records what the exporter did
    rather than what the flight-log filename implies.
    """
    if str(REPO) not in sys.path:
        sys.path.insert(0, str(REPO))
    from modules.flight_logs import crs_for_flight_log
    log = resolve_flight_log(workspace)
    return crs_for_flight_log(str(log)) if log else None


def run(argv: list[str], dry_run: bool) -> dict:
    printable = ' '.join(a if ' ' not in a else f'"{a}"' for a in argv)
    if dry_run:
        logger.info('DRY RUN: %s', printable)
        return {'command': printable, 'dry_run': True}
    logger.info('running: %s', printable)
    try:
        proc = subprocess.run(argv, capture_output=True, text=True,
                              stdin=subprocess.DEVNULL)
    except OSError as exc:
        logger.error('cannot start publisher: %s', exc)
        return {'command': printable, 'success': False, 'error': str(exc)}
    if proc.stdout:
        logger.info('%s', proc.stdout.strip()[-2000:])
    if proc.returncode != 0:
        logger.error('failed (%d): %s', proc.returncode,
                     (proc.stderr or '').strip()[-2000:])
    return {'command': printable, 'returncode': proc.returncode,
            'success': proc.returncode == 0}


def main() -> int:
    logging.basicConfig(level=logging.INFO, format='%(levelname)s %(message)s')
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--workspace', required=True)
    parser.add_argument('--prefix', required=True,
                        help='asset-name prefix, e.g. the wreck name')
    parser.add_argument('--flight-log', default=None,
                        help='flight log for this cruise. Its nav envelope '
                             'is an independent check on how each mesh is '
                             'placed (default: resolved from the workspace)')
    parser.add_argument('--components', nargs='*', default=None,
                        help='subset of component names (default: all exported)')
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()

    exports = Path(args.workspace) / 'exports'
    if not exports.is_dir():
        raise SystemExit(f'no exports directory under {args.workspace}')

    # publish_cesium now takes the CRS from each mesh's own .rsInfo sidecar,
    # which records what the exporter actually did rather than what a
    # filename implies. The flight log is still worth passing: its nav
    # envelope is an INDEPENDENT check on the transformToModel reading, and
    # a disagreement between the two is exactly the signal we want.
    flight_log = Path(args.flight_log) if args.flight_log else \
        resolve_flight_log(Path(args.workspace))
    if flight_log:
        logger.info('flight log for nav cross-check: %s (%s)',
                    flight_log, resolve_input_crs(Path(args.workspace)))
    else:
        logger.warning(
            'no zone-tagged flight_log*_UTM.txt under %s - publishing without '
            'the independent nav check. Placement will rest on the .rsInfo '
            'sidecar alone. Pass --flight-log to be explicit.', args.workspace)

    cesium_token = os.environ.get('CESIUM_ION_TOKEN')
    nira_dir = os.environ.get('NIRACLIENT_DIR')
    if not cesium_token:
        logger.warning('CESIUM_ION_TOKEN not set - Cesium uploads inactive')
    if not nira_dir:
        logger.warning('NIRACLIENT_DIR not set - Nira uploads inactive '
                       '(Enterprise plan + configured niraclient required)')
    if not (cesium_token or nira_dir or args.dry_run):
        raise SystemExit('no destination configured and not --dry-run - '
                         'nothing to do')

    comps = sorted(p for p in exports.iterdir()
                   if p.is_dir() and (p / 'obj').is_dir()
                   and any(obj.is_file() and obj.suffix.lower() == '.obj'
                           and obj.stat().st_size > 0
                           for obj in (p / 'obj').iterdir()))
    available = [c.name for c in comps]
    if args.components is not None:
        wanted = set(args.components)
        if not wanted:
            raise SystemExit('--components requires at least one component')
        missing = wanted - set(available)
        if missing:
            raise SystemExit('requested component(s) have no exported OBJ: '
                             + ', '.join(sorted(missing)))
        comps = [c for c in comps if c.name in wanted]
    if not comps:
        raise SystemExit('no exported obj/ components found')

    configured = (["cesium"] if cesium_token else []) + \
        (["nira"] if nira_dir else [])
    destinations = configured or ['cesium', 'nira']
    assets = []
    for comp in comps:
        directory = comp / 'obj'
        files = set()
        if 'cesium' in destinations:
            objs = select_objs(directory, 'whole')
            files.update(objs)
            files.update(referenced_companions(objs))
            files.update(info for obj in objs
                         if (info := find_rsinfo(obj)) is not None)
        if 'nira' in destinations:
            files.update(Path(entry['path']) for entry in build_file_list(directory, 'split'))
        try:
            source = publication_source_state(directory, list(files), destinations)
        except (OSError, ValueError) as exc:
            raise SystemExit(f'cannot identify publication source: {exc}') from None
        assets.append({'component': comp.name,
                       'asset_name': f'{args.prefix} {comp.name}',
                       'source': source,
                       **{d: {'pending': True} for d in destinations}})
    report: dict = {
        'schema': 3, 'started': time.strftime('%Y-%m-%d %H:%M:%S'),
        'dry_run': args.dry_run,
        'requested_components': [c.name for c in comps],
        'destinations': destinations, 'configured_destinations': configured,
        'available_components': available,
        'assets': assets,
    }
    report_path = Path(args.workspace) / (
        'publish_plan.json' if args.dry_run else 'publish_report.json')

    def flush() -> None:
        temporary = report_path.with_suffix('.json.tmp')
        temporary.write_text(json.dumps(report, indent=2), encoding='utf-8')
        temporary.replace(report_path)

    flush()
    for comp, entry in zip(comps, report['assets']):
        name = f'{args.prefix} {comp.name}'

        def submit(argv: list[str]) -> dict:
            if not publication_source_matches(comp / 'obj', entry['source']):
                return {'success': False, 'error': 'source export changed before publication'}
            result = run(argv, args.dry_run)
            if not publication_source_matches(comp / 'obj', entry['source']):
                result['success'] = False
                result['error'] = 'source export changed during publication'
            return result

        if 'cesium' in destinations:
            argv = [sys.executable, str(REPO / 'publish_cesium.py'),
                    '--name', name, '--dir', str(comp / 'obj'),
                    '--parts', 'whole',
                    '--poll', '--verify']
            if flight_log:
                argv += ['--flight-log', str(flight_log)]
            if args.dry_run:
                argv.append('--dry-run')
            entry['cesium'] = submit(argv)
            flush()
        if 'nira' in destinations:
            argv = [sys.executable, str(REPO / 'publish_nira.py'),
                    '--name', name, '--dir', str(comp / 'obj'),
                    '--parts', 'split',
                    '--niraclient', nira_dir or '<NIRACLIENT_DIR>']
            if args.dry_run:
                argv.append('--dry-run')
            entry['nira'] = submit(argv)
            flush()

    if args.dry_run:
        logger.info('planned %d component(s); plan: %s', len(comps), report_path)
        return 0
    succeeded = sum(entry[d].get('success') is True
                    for entry in report['assets'] for d in destinations)
    expected = len(comps) * len(destinations)
    ok = succeeded == expected
    report['complete'] = ok
    report['finished'] = time.strftime('%Y-%m-%d %H:%M:%S')
    flush()
    logger.info('completed %d of %d publication command(s); report: %s',
                succeeded, expected, report_path)
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
