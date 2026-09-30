"""Publication input preservation, representation selection and completion."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import publish_batch
import publish_cesium
from modules.cesium_placement import PlacementError
from modules.publish_fingerprint import publication_source_state
from modules.workspace_census import Workspace
from publish_nira import build_file_list


def write(path: Path, text: str = 'v 100 200 300\n') -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding='utf-8')
    return path


def localised(obj: Path):
    return [(obj, [(1.0, 2.0, 3.0)])]


@pytest.mark.parametrize('location', ['source', 'ancestor'])
def test_staging_cannot_replace_source_tree(tmp_path, location):
    obj = write(tmp_path / 'source' / 'm.obj')
    target = obj.parent if location == 'source' else tmp_path
    original = obj.read_bytes()
    with pytest.raises(PlacementError, match='contains a source'):
        publish_cesium.stage(localised(obj), target, [obj])
    assert obj.read_bytes() == original


def test_staging_cannot_replace_referenced_texture(tmp_path):
    obj = write(tmp_path / 'source' / 'm.obj',
                'mtllib materials/m.mtl\nv 100 200 300\n')
    write(obj.parent / 'materials' / 'm.mtl', 'map_Kd ../textures/a.png\n')
    texture = write(obj.parent / 'textures' / 'a.png', 'texture')
    with pytest.raises(PlacementError, match='contains a source'):
        publish_cesium.stage(localised(obj), texture.parent, [obj])
    assert texture.read_text(encoding='utf-8') == 'texture'


@pytest.mark.parametrize('alias_at_parent', [False, True])
def test_staging_rejects_directory_aliases(tmp_path, alias_at_parent):
    obj = write(tmp_path / 'source' / 'm.obj')
    actual = tmp_path / 'actual'
    actual.mkdir()
    alias = tmp_path / 'alias'
    if os.name == 'nt':
        result = subprocess.run(
            ['cmd', '/c', 'mklink', '/J', str(alias), str(actual)],
            capture_output=True)
        if result.returncode:
            pytest.skip('directory junction creation unavailable')
    else:
        alias.symlink_to(actual, target_is_directory=True)
    target = alias / 'stage' if alias_at_parent else alias
    with pytest.raises(PlacementError, match='alias'):
        publish_cesium.stage(localised(obj), target, [obj])
    assert list(actual.iterdir()) == []
    assert obj.read_text(encoding='utf-8') == 'v 100 200 300\n'


def test_nonempty_unmarked_staging_is_preserved(tmp_path):
    obj = write(tmp_path / 'source' / 'm.obj')
    unrelated = write(tmp_path / 'stage' / 'notes.txt', 'keep')
    with pytest.raises(PlacementError, match='nonempty and unmarked'):
        publish_cesium.stage(localised(obj), unrelated.parent, [obj])
    assert unrelated.read_text(encoding='utf-8') == 'keep'


def test_staging_preserves_material_topology_and_originals(tmp_path):
    obj = write(tmp_path / 'source' / 'm.obj',
                'mtllib materials/m.mtl\nv 100 200 300\n')
    material = write(obj.parent / 'materials' / 'm.mtl',
                     'map_Kd ../textures/a.png\n')
    texture = write(obj.parent / 'textures' / 'a.png', 'texture')
    originals = {p: p.read_bytes() for p in (obj, material, texture)}
    target = obj.parent / '_cesium_local'
    staged = publish_cesium.stage(localised(obj), target, [obj])
    assert {p.relative_to(target).as_posix() for p in staged} == {
        'm.obj', 'materials/m.mtl', 'textures/a.png'}
    assert 'v 1.000000 2.000000 3.000000' in (
        target / 'm.obj').read_text(encoding='utf-8')
    assert {p: p.read_bytes() for p in originals} == originals
    assert (target / publish_cesium.STAGING_MARKER).is_file()
    write(target / 'old.png', 'old')
    publish_cesium.stage(localised(obj), target, [obj])
    assert not (target / 'old.png').exists()


def test_failed_staging_keeps_prior_copy(tmp_path, monkeypatch):
    obj = write(tmp_path / 'source' / 'm.obj')
    target = tmp_path / 'stage'
    publish_cesium.stage(localised(obj), target, [obj])
    prior = {p.name: p.read_bytes() for p in target.iterdir()}

    def fail(*args):
        raise OSError('cannot write mesh')

    monkeypatch.setattr(publish_cesium, 'rewrite_obj_local', fail)
    with pytest.raises(OSError, match='cannot write mesh'):
        publish_cesium.stage(localised(obj), target, [obj])
    assert {p.name: p.read_bytes() for p in target.iterdir()} == prior
    assert not list(tmp_path.glob('.stage-*'))


def test_failed_staging_swap_restores_prior_copy(tmp_path, monkeypatch):
    obj = write(tmp_path / 'source' / 'm.obj')
    target = tmp_path / 'stage'
    publish_cesium.stage(localised(obj), target, [obj])
    previous = (target / 'm.obj').read_bytes()
    rename = Path.rename

    def fail_prepared_swap(path, destination):
        if path.name.startswith('.stage-') and '-previous-' not in path.name:
            raise OSError('cannot install staging')
        return rename(path, destination)

    monkeypatch.setattr(Path, 'rename', fail_prepared_swap)
    with pytest.raises(OSError, match='cannot install staging'):
        publish_cesium.stage(localised(obj), target, [obj])
    assert (target / 'm.obj').read_bytes() == previous
    assert not list(tmp_path.glob('.stage-*'))


def test_nira_excludes_default_and_custom_cesium_staging(tmp_path):
    obj = write(tmp_path / 'm.obj')
    write(tmp_path / '_cesium_local' / 'm.obj')
    custom = tmp_path / 'custom-local'
    publish_cesium.stage(localised(obj), custom, [obj])
    entries = build_file_list(tmp_path)
    assert [Path(e['path']) for e in entries] == [obj]


@pytest.mark.parametrize('parts, expected', [
    ('whole', {'m.obj', 'whole.mtl', 'whole.png'}),
    ('split', {'m_0000000.obj', 'split.mtl', 'split.png'}),
])
def test_nira_selects_one_geometry_copy_and_its_textures(tmp_path, parts, expected):
    write(tmp_path / 'm.obj', 'mtllib whole.mtl\nv 1 2 3\n')
    write(tmp_path / 'm_0000000.obj', 'mtllib split.mtl\nv 1 2 3\n')
    for name in ('whole', 'split'):
        write(tmp_path / f'{name}.mtl', f'map_Kd {name}.png\n')
        write(tmp_path / f'{name}.png', 'texture')
    assert {Path(e['path']).name for e in build_file_list(tmp_path, parts)} == expected


def test_nira_requires_choice_between_mesh_formats(tmp_path):
    obj = write(tmp_path / 'm.obj')
    write(tmp_path / 'm.fbx')
    with pytest.raises(SystemExit, match='multiple mesh formats'):
        build_file_list(tmp_path)
    assert [Path(e['path']) for e in build_file_list(tmp_path, geometry='obj')] == [obj]


def test_nira_keeps_pointcloud_and_current_sidecar(tmp_path):
    obj = write(tmp_path / 'm.obj')
    info = write(tmp_path / 'm.obj.rsInfo', '<Model/>')
    cloud = write(tmp_path / 'points.laz', 'points')
    assert {Path(e['path']) for e in build_file_list(tmp_path)} == {obj, info, cloud}


@pytest.mark.parametrize('suffix', ['.rsInfo', '.rcInfo'])
def test_nira_parts_keep_matching_group_sidecar(tmp_path, suffix):
    write(tmp_path / 'model.obj')
    part = write(tmp_path / 'model_0000000.obj')
    info = write(tmp_path / ('model.obj' + suffix), '<Model/>')
    write(tmp_path / ('unrelated.obj' + suffix), '<Model/>')
    assert {Path(e['path']) for e in build_file_list(tmp_path)} == {part, info}


def test_nira_keeps_tiff_textures_with_spaced_material_names(tmp_path):
    obj = write(tmp_path / 'm.obj', 'mtllib material one.mtl\nv 1 2 3\n')
    material = write(tmp_path / 'material one.mtl',
                     'map_Kd -s 1 1 1 texture one.tiff\n')
    texture = write(tmp_path / 'texture one.tiff', 'texture')
    assert {Path(e['path']) for e in build_file_list(tmp_path)} == {
        obj, material, texture}


def test_missing_declared_material_fails_before_staging_changes(tmp_path):
    obj = write(tmp_path / 'm.obj', 'mtllib missing.mtl\nv 1 2 3\n')
    with pytest.raises(PlacementError, match='material not found'):
        publish_cesium.stage(localised(obj), tmp_path / 'stage', [obj])
    assert not (tmp_path / 'stage').exists()


def exported(workspace: Path, names=('one', 'two')):
    for name in names:
        write(workspace / 'exports' / name / 'obj' / f'{name}.obj')


def invoke_batch(monkeypatch, workspace, *extra):
    monkeypatch.setattr(sys, 'argv', [
        'publish_batch.py', '--workspace', str(workspace), '--prefix', 'Survey',
        *extra])
    return publish_batch.main()


@pytest.fixture
def publishing_env(monkeypatch):
    monkeypatch.setenv('CESIUM_ION_TOKEN', 'fixture-token')
    monkeypatch.setenv('NIRACLIENT_DIR', 'fixture-client')
    monkeypatch.setattr(publish_batch, 'resolve_flight_log', lambda _: None)


def test_publish_plan_preserves_previous_publication(tmp_path, monkeypatch, publishing_env):
    exported(tmp_path)
    report = write(tmp_path / 'publish_report.json', '{"prior": true}')
    monkeypatch.delenv('NIRACLIENT_DIR')
    assert invoke_batch(monkeypatch, tmp_path, '--dry-run') == 0
    assert report.read_text(encoding='utf-8') == '{"prior": true}'
    plan = json.loads((tmp_path / 'publish_plan.json').read_text(encoding='utf-8'))
    assert plan['dry_run'] is True
    assert plan['destinations'] == ['cesium']
    assert all(a['cesium']['dry_run'] for a in plan['assets'])


@pytest.mark.parametrize('components', [[], ['missing'], ['one', 'missing']])
def test_invalid_requested_components_fail_before_publishing(tmp_path, monkeypatch,
                                                             publishing_env, components):
    exported(tmp_path)
    calls = []
    monkeypatch.setattr(publish_batch, 'run', lambda *args: calls.append(args))
    with pytest.raises(SystemExit, match='requires at least|no exported OBJ'):
        invoke_batch(monkeypatch, tmp_path, '--components', *components)
    assert calls == []
    assert not (tmp_path / 'publish_report.json').exists()


def test_requested_publications_are_recorded_before_execution(tmp_path, monkeypatch,
                                                              publishing_env):
    exported(tmp_path)
    seen = []

    def run(argv, dry_run):
        report = json.loads((tmp_path / 'publish_report.json').read_text(encoding='utf-8'))
        seen.append(report)
        return {'success': True}

    monkeypatch.setattr(publish_batch, 'run', run)
    assert invoke_batch(monkeypatch, tmp_path) == 0
    assert seen[0]['requested_components'] == ['one', 'two']
    assert seen[0]['assets'][1]['nira'] == {'pending': True}
    assert seen[1]['assets'][0]['cesium']['success'] is True
    assert Workspace(tmp_path).detect()['publish'].status == 'done'


def test_one_failed_destination_leaves_publish_partial(tmp_path, monkeypatch,
                                                       publishing_env):
    exported(tmp_path)
    monkeypatch.setattr(publish_batch, 'run', lambda argv, _: {
        'success': Path(argv[1]).name == 'publish_cesium.py'})
    assert invoke_batch(monkeypatch, tmp_path) == 1
    status = Workspace(tmp_path).detect()['publish']
    assert status.status == 'partial'
    assert '0 of 2' in status.summary
    assert all('nira' in detail for detail in status.details)


def test_completed_subset_does_not_complete_workspace_publish(tmp_path, monkeypatch,
                                                              publishing_env):
    exported(tmp_path)
    monkeypatch.setattr(publish_batch, 'run', lambda *args: {'success': True})
    assert invoke_batch(monkeypatch, tmp_path, '--components', 'one') == 0
    status = Workspace(tmp_path).detect()['publish']
    assert status.status == 'partial'
    assert '1 of 2' in status.summary
    assert any('two' in detail for detail in status.details)


@pytest.mark.parametrize('changed', ['one.obj', 'one.mtl', 'texture.png', 'one.obj.rsInfo'])
def test_publication_census_requires_current_selected_sources(
        tmp_path, monkeypatch, publishing_env, changed):
    directory = tmp_path / 'exports' / 'one' / 'obj'
    write(directory / 'one.obj', 'mtllib one.mtl\nv 1 2 3\n')
    write(directory / 'one.mtl', 'map_Kd texture.png\n')
    write(directory / 'texture.png', 'texture')
    write(directory / 'one.obj.rsInfo', '<Model/>')
    monkeypatch.setattr(publish_batch, 'run', lambda *args: {'success': True})
    assert invoke_batch(monkeypatch, tmp_path) == 0
    report = json.loads((tmp_path / 'publish_report.json').read_text(encoding='utf-8'))
    files = report['assets'][0]['source']['files']
    assert {entry['path'] for entry in files} == {
        'one.obj', 'one.mtl', 'texture.png', 'one.obj.rsInfo'}
    assert all(len(entry['sha256']) == 64 for entry in files)
    assert Workspace(tmp_path).detect()['publish'].status == 'done'
    path = directory / changed
    path.write_bytes(path.read_bytes() + b'changed')
    status = Workspace(tmp_path).detect()['publish']
    assert status.status == 'partial'
    assert any('current source export' in detail for detail in status.details)


def test_publication_identity_excludes_generated_staging_and_discarded_whole(
        tmp_path, monkeypatch, publishing_env):
    monkeypatch.delenv('CESIUM_ION_TOKEN')
    directory = tmp_path / 'exports' / 'one' / 'obj'
    whole = write(directory / 'one.obj')
    write(directory / 'one_0000000.obj')
    monkeypatch.setattr(publish_batch, 'run', lambda *args: {'success': True})
    assert invoke_batch(monkeypatch, tmp_path) == 0
    report = json.loads((tmp_path / 'publish_report.json').read_text(encoding='utf-8'))
    assert [entry['path'] for entry in report['assets'][0]['source']['files']] == [
        'one_0000000.obj']
    whole.write_bytes(b'new discarded whole')
    write(directory / '_cesium_local' / 'one_0000000.obj')
    write(directory / 'custom-stage' / publish_cesium.STAGING_MARKER, '{"schema": 1}')
    write(directory / 'custom-stage' / 'one_0000000.obj')
    assert Workspace(tmp_path).detect()['publish'].status == 'done'
    write(directory / 'one_0000001.obj')
    assert Workspace(tmp_path).detect()['publish'].status == 'partial'


def test_cesium_source_identity_matches_explicit_whole_geometry_command(
        tmp_path, monkeypatch, publishing_env):
    monkeypatch.delenv('NIRACLIENT_DIR')
    directory = tmp_path / 'exports' / 'one' / 'obj'
    whole = write(directory / 'one.obj')
    part = write(directory / 'one_0000000.obj')
    calls = []

    def run(argv, _):
        calls.append(argv)
        return {'success': True}

    monkeypatch.setattr(publish_batch, 'run', run)
    assert invoke_batch(monkeypatch, tmp_path) == 0
    assert calls[0][calls[0].index('--parts') + 1] == 'whole'
    report = json.loads((tmp_path / 'publish_report.json').read_text(encoding='utf-8'))
    assert [entry['path'] for entry in report['assets'][0]['source']['files']] == ['one.obj']
    part.write_bytes(b'new discarded part')
    assert Workspace(tmp_path).detect()['publish'].status == 'done'
    whole.write_bytes(b'new selected whole')
    assert Workspace(tmp_path).detect()['publish'].status == 'partial'


def test_source_change_during_publication_cannot_claim_success(
        tmp_path, monkeypatch, publishing_env):
    exported(tmp_path, ('one',))
    calls = []

    def run(*args):
        calls.append(args)
        write(tmp_path / 'exports' / 'one' / 'obj' / 'one.obj', 'changed source')
        return {'success': True}

    monkeypatch.setattr(publish_batch, 'run', run)
    assert invoke_batch(monkeypatch, tmp_path) == 1
    assert len(calls) == 1
    report = json.loads((tmp_path / 'publish_report.json').read_text(encoding='utf-8'))
    assert report['assets'][0]['cesium']['success'] is False
    assert 'during' in report['assets'][0]['cesium']['error']
    assert report['assets'][0]['nira']['success'] is False
    assert 'before' in report['assets'][0]['nira']['error']
    assert Workspace(tmp_path).detect()['publish'].status == 'partial'


def test_modern_publication_report_without_source_identity_is_partial(tmp_path):
    exported(tmp_path, ('one',))
    write(tmp_path / 'publish_report.json', json.dumps({
        'schema': 2, 'requested_components': ['one'], 'destinations': ['cesium'],
        'assets': [{'component': 'one', 'cesium': {'success': True}}]}))
    assert Workspace(tmp_path).detect()['publish'].status == 'partial'


@pytest.mark.parametrize('removed', ['one.obj', 'one.mtl', 'texture.png'])
def test_missing_selected_publication_source_invalidates_prior_success(
        tmp_path, monkeypatch, publishing_env, removed):
    directory = tmp_path / 'exports' / 'one' / 'obj'
    write(directory / 'one.obj', 'mtllib one.mtl\nv 1 2 3\n')
    write(directory / 'one.mtl', 'map_Kd texture.png\n')
    write(directory / 'texture.png', 'texture')
    monkeypatch.setattr(publish_batch, 'run', lambda *args: {'success': True})
    assert invoke_batch(monkeypatch, tmp_path) == 0
    (directory / removed).unlink()
    assert Workspace(tmp_path).detect()['publish'].status == 'partial'


@pytest.mark.parametrize('result, expected', [
    ({'success': True}, 'done'), ({'success': False}, 'partial'),
    ({'dry_run': True}, 'partial'), (None, 'partial'), ('invalid', 'partial'),
])
@pytest.mark.parametrize('has_source', [True, False])
def test_legacy_publish_requires_source_and_all_present_destinations(
        tmp_path, result, expected, has_source):
    exported(tmp_path, ('one',))
    directory = tmp_path / 'exports' / 'one' / 'obj'
    source = publication_source_state(directory, [directory / 'one.obj'], ['cesium', 'nira'])
    write(tmp_path / 'publish_report.json', json.dumps({'assets': [{
        'component': 'one', 'cesium': {'success': True}, 'nira': result,
        **({'source': source} if has_source else {})}]}))
    assert Workspace(tmp_path).detect()['publish'].status == (
        expected if has_source else 'partial')


def test_interrupted_publish_overrides_stale_success(tmp_path):
    exported(tmp_path, ('one',))
    directory = tmp_path / 'exports' / 'one' / 'obj'
    source = publication_source_state(directory, [directory / 'one.obj'], ['cesium'])
    write(tmp_path / 'publish_report.json', json.dumps({'assets': [{
        'component': 'one', 'cesium': {'success': True}, 'source': source}]}))
    marker = write(tmp_path / 'interrupted_stage.json', json.dumps({
        'cancelled': True, 'stage': 'Publish', 'stages': ['publish']}))
    status = Workspace(tmp_path).detect()['publish']
    assert status.status == 'partial'
    assert 'interrupted' in status.summary
    marker.unlink()
    assert Workspace(tmp_path).detect()['publish'].status == 'done'


def test_interrupted_stage_without_artifacts_is_blocked(tmp_path):
    write(tmp_path / 'interrupted_stage.json', json.dumps({
        'cancelled': True, 'stages': ['model', None, 'unknown']}))
    assert Workspace(tmp_path).detect()['model'].status == 'blocked'
