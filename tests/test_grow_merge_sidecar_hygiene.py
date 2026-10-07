#!/usr/bin/env python3
"""Calibration sidecars survive the growth and merge drivers' hygiene.

grow_zone.take_census and merge_zones' per-attempt and assembly hygiene
called calibration_sidecars.sanitize_and_census WITHOUT the workspace's
calibration modes, so every pose sidecar RealityScan exported beside an
image of a prior/groups camera was DELETED (logged as "no calibration
sidecar decided for their camera"), and the calibration sidecars the
identity harvest had moved away were never put back. The saved .rsproj
kept its groups, so the solve was unaffected; the tree was wrong for every
later -add and for poses_to_flight_log. Both drivers now read the intake
manifest the alignment stage reads (modules.wildsync_intake.intake) and
rewrite/restore instead of deleting; a workspace without a manifest keeps
the old behaviour.

No RealityScan: the trees are built by hand with the sidecar texts the
pipeline writes.

Run:  py -3.13 -m pytest tests/test_grow_merge_sidecar_hygiene.py
"""
from __future__ import annotations

import json
import logging
import os
import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
sys.path.insert(0, REPO_ROOT)

import grow_zone
import merge_zones
from modules import calibration_sidecars as cs
from modules import camera_registry
from modules.wildsync_intake import intake

LEFT = camera_registry.CAMERAS['ilx_left']
RIGHT = camera_registry.CAMERAS['ilx_right']
MODES = {'ilx_left': 'groups', 'ilx_right': 'groups'}
FOCALS = {'ilx_left': 29.0, 'ilx_right': 24.0}
LEFT_GROUPS = cs.sidecar_xmp(LEFT, 'groups', FOCALS['ilx_left'])
RIGHT_GROUPS = cs.sidecar_xmp(RIGHT, 'groups', FOCALS['ilx_right'])
POSE = ('<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF><rdf:Description '
        'xcr:Version="3" xcr:PosePrior="exact"><xcr:Position>1 2 3'
        '</xcr:Position></rdf:Description></rdf:RDF></x:xmpmeta>')

QUIET = logging.getLogger('sidecar-hygiene-test')
QUIET.addHandler(logging.NullHandler())
QUIET.propagate = False


def _workspace(tmp_path: Path, with_manifest: bool = True) -> Path:
    """Workspace as Wild Sync Intake, Batch Directory and Align Zone leave
    it: the manifest in raw_images, one zone with per-camera folders, and
    the groups sidecar beside every image."""
    ws = tmp_path / 'ws'
    raw = ws / 'raw_images'
    raw.mkdir(parents=True)
    if with_manifest:
        manifest = {
            'schema': 2, 'status': 'complete',
            'calibration': {key: {'camera': key, 'mode': mode,
                                  'reason': 'test',
                                  'starting_focal_35mm': FOCALS[key]}
                            for key, mode in MODES.items()},
        }
        (raw / 'wildsync_intake.json').write_text(json.dumps(manifest),
                                                  encoding='utf-8')
    zone = ws / 'batched_images_by_zone' / 'zone_1'
    for node, camera, text in (('Cam1', 'ilx_left', LEFT_GROUPS),
                               ('Cam2', 'ilx_right', RIGHT_GROUPS)):
        folder = zone / camera
        folder.mkdir(parents=True)
        for frame in ('000001', '000002', '000003'):
            (folder / f'{node}_{frame}.card.JPG').write_bytes(b'jpeg')
            (folder / f'{node}_{frame}.card.xmp').write_text(
                text, encoding='utf-8', newline='')
    return ws


def _sidecars(root: Path) -> dict[str, str]:
    return {p.name: p.read_text(encoding='utf-8')
            for p in sorted(root.rglob('*.xmp'))}


def _expected_tree() -> dict[str, str]:
    return {f'{node}_{frame}.card.xmp': text
            for node, text in (('Cam1', LEFT_GROUPS), ('Cam2', RIGHT_GROUPS))
            for frame in ('000001', '000002', '000003')}


def _after_a_pass(zone: Path) -> None:
    """What RealityScan's -exportXMP and the identity harvest leave: a pose
    sidecar over one image's calibration sidecar, another image's sidecar
    moved away, the rest untouched."""
    (zone / 'ilx_left' / 'Cam1_000001.card.xmp').write_text(
        POSE, encoding='utf-8', newline='')
    (zone / 'ilx_right' / 'Cam2_000002.card.xmp').unlink()


# ------------------------------------------------- manifest lookup

def test_workspace_calibration_is_found_from_the_zone_and_the_batched_root(tmp_path):
    ws = _workspace(tmp_path)
    batched = ws / 'batched_images_by_zone'
    expected = (MODES, FOCALS)
    assert intake.workspace_calibration(str(batched / 'zone_1')) == expected
    assert intake.workspace_calibration(str(batched)) == expected


def test_workspace_calibration_is_none_outside_a_wild_sync_workspace(tmp_path):
    ws = _workspace(tmp_path, with_manifest=False)
    assert intake.workspace_calibration(
        str(ws / 'batched_images_by_zone' / 'zone_1')) is None
    # A per-camera folder is not a zone root: the manifest two levels up
    # from the zone is not reached from three levels down.
    with_manifest = _workspace(tmp_path / 'other')
    assert intake.workspace_calibration(
        str(with_manifest / 'batched_images_by_zone' / 'zone_1' / 'ilx_left')
    ) is None


def test_workspace_calibration_refuses_an_unusable_manifest(tmp_path):
    ws = _workspace(tmp_path)
    (ws / 'raw_images' / 'wildsync_intake.json').write_text(
        '{"schema": 1}', encoding='utf-8')
    with pytest.raises(ValueError, match='schema-1'):
        intake.workspace_calibration(str(ws / 'batched_images_by_zone'))


# ------------------------------------------------- grow_zone.take_census

def test_take_census_keeps_the_calibration_sidecars(tmp_path):
    ws = _workspace(tmp_path)
    zone = ws / 'batched_images_by_zone' / 'zone_1'
    before = _sidecars(zone)
    _basenames, stems = grow_zone.build_image_index(str(zone))
    names = grow_zone.take_census(str(zone), stems, QUIET,
                                  intake.workspace_calibration(str(zone)))
    assert names == set()
    assert _sidecars(zone) == before == _expected_tree()


def test_take_census_rewrites_a_pose_sidecar_and_restores_a_moved_one(tmp_path):
    """A stem-named pose sidecar of a groups camera becomes the groups
    sidecar again (it used to be deleted); the image whose sidecar the
    harvest moved away gets it back; the census still counts the pose."""
    ws = _workspace(tmp_path)
    zone = ws / 'batched_images_by_zone' / 'zone_1'
    _after_a_pass(zone)
    _basenames, stems = grow_zone.build_image_index(str(zone))
    names = grow_zone.take_census(str(zone), stems, QUIET,
                                  intake.workspace_calibration(str(zone)))
    assert names == {'cam1_000001.card.jpg'}
    assert _sidecars(zone) == _expected_tree()


def test_take_census_without_a_manifest_deletes_the_pose_as_before(tmp_path):
    ws = _workspace(tmp_path, with_manifest=False)
    zone = ws / 'batched_images_by_zone' / 'zone_1'
    _after_a_pass(zone)
    _basenames, stems = grow_zone.build_image_index(str(zone))
    names = grow_zone.take_census(str(zone), stems, QUIET,
                                  intake.workspace_calibration(str(zone)))
    assert names == {'cam1_000001.card.jpg'}
    expected = _expected_tree()
    del expected['Cam1_000001.card.xmp']
    del expected['Cam2_000002.card.xmp']
    assert _sidecars(zone) == expected


def test_every_census_in_grow_zone_passes_the_calibration():
    """The driver resolves the manifest once in main() and every census
    gets it; a call without it would delete the sidecars again."""
    source = Path(REPO_ROOT, 'grow_zone.py').read_text(encoding='utf-8')
    calls = re.findall(r'take_census\(([^)]*)\)', source)
    bare = [c for c in calls if 'calibration' not in c
            and not c.startswith('images_root: str')]
    assert len(calls) >= 5 and not bare, calls


# ------------------------------------------------- merge_zones hygiene

def test_merge_hygiene_keeps_the_calibration_sidecars(tmp_path):
    ws = _workspace(tmp_path)
    batched = ws / 'batched_images_by_zone'
    before = _sidecars(batched)
    found = merge_zones.sidecar_hygiene(
        str(batched), intake.workspace_calibration(str(batched)), QUIET)
    assert found == 0
    assert _sidecars(batched) == before == _expected_tree()


def test_merge_hygiene_rewrites_a_pose_sidecar_and_restores_a_moved_one(tmp_path):
    ws = _workspace(tmp_path)
    batched = ws / 'batched_images_by_zone'
    _after_a_pass(batched / 'zone_1')
    found = merge_zones.sidecar_hygiene(
        str(batched), intake.workspace_calibration(str(batched)), QUIET)
    assert found == 1
    assert _sidecars(batched) == _expected_tree()


def test_merge_hygiene_without_a_manifest_deletes_the_pose_as_before(tmp_path):
    ws = _workspace(tmp_path, with_manifest=False)
    batched = ws / 'batched_images_by_zone'
    _after_a_pass(batched / 'zone_1')
    found = merge_zones.sidecar_hygiene(
        str(batched), intake.workspace_calibration(str(batched)), QUIET)
    assert found == 1
    expected = _expected_tree()
    del expected['Cam1_000001.card.xmp']
    del expected['Cam2_000002.card.xmp']
    assert _sidecars(batched) == expected


def test_merge_zones_uses_the_hygiene_at_every_attempt_and_the_assembly():
    source = Path(REPO_ROOT, 'merge_zones.py').read_text(encoding='utf-8')
    assert 'calibration_sidecars.sanitize_and_census(images_root)' not in source
    assert source.count('sidecar_hygiene(images_root, calibration, logger)') == 2
