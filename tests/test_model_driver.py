"""Offline checks for component scale selection and model-driver resumability."""
from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

import run_models
from modules.workspace_census import Workspace

LOG = logging.getLogger(__name__)


def _cloud(directory, count, scale=1.0, prefix=''):
    directory.mkdir(parents=True, exist_ok=True)
    points = [(float(i), float(i % 7), float(i % 3)) for i in range(count)]
    for i, point in enumerate(points):
        position = ' '.join(str(scale * value) for value in point)
        (directory / f'{prefix}{i}.xmp').write_text(
            f'<xcr:Position>{position}</xcr:Position>', encoding='utf-8')
    return points


def _scale_fixture(tmp_path, *, fused=True, scale=.2):
    attempt = tmp_path / 'attempt'
    attempt.mkdir()
    _cloud(attempt / 'identity_r0', 40, prefix='image_' if not fused else '')
    points = _cloud(attempt / 'identity_r1', 39, scale,
                    prefix='image_' if not fused else '')
    members = [f'image_{i}.jpg' for i in range(39)]
    union = tmp_path / 'flight_log_UTM.txt'
    union.write_text('name;x;y;z\n' + ''.join(
        f'{member};{p[0]};{p[1]};{p[2]}\n'
        for member, p in zip(members, points)), encoding='utf-8')
    export = attempt / 'cluster_0_a1_c1.rsalign'
    export.write_bytes(b'component')
    manifest = {'images': members}
    if fused:
        manifest['attribution'] = {'inputs': ['zone_0/c0', 'zone_1/c0']}
    Path(str(export) + '.manifest.json').write_text(
        json.dumps(manifest), encoding='utf-8')
    return export, union


def test_fused_scale_uses_its_own_component_harvest(tmp_path):
    export, union = _scale_fixture(tmp_path)
    status, why, median = run_models.resolve_scale(
        'cluster_0/cluster_0_a1_c1', {'rsalign': str(export)}, {},
        Workspace(tmp_path), str(union), LOG)
    assert status == 'fail', why
    assert median == pytest.approx(.2)


def test_missing_fused_harvest_never_uses_siblings_poses(tmp_path):
    export, union = _scale_fixture(tmp_path)
    export = export.with_name('cluster_0_a1_c2.rsalign')
    export.write_bytes(b'component')
    Path(str(export) + '.manifest.json').write_text(json.dumps({
        'images': [f'image_{i}.jpg' for i in range(39)],
        'attribution': {'inputs': ['zone_0/c0', 'zone_1/c0']},
    }), encoding='utf-8')
    status, why, median = run_models.resolve_scale(
        'cluster_0/cluster_0_a1_c2', {'rsalign': str(export)}, {},
        Workspace(tmp_path), str(union), LOG)
    assert status == 'unmeasured'
    assert 'no component harvest' in why
    assert median is None


@pytest.mark.parametrize('band,status', [((.95, 1.05), 'fail'), ((1.0, 1.2), 'pass')])
def test_quantile_scale_respects_the_saved_band(tmp_path, band, status):
    export, union = _scale_fixture(tmp_path, scale=1.075)
    report = {'scale_gate': {'min': band[0], 'max': band[1]}}
    actual, why, median = run_models.resolve_scale(
        'cluster_0/cluster_0_a1_c1', {'rsalign': str(export)}, report,
        Workspace(tmp_path), str(union), LOG)
    assert actual == status, why
    assert median == pytest.approx(1.075)


def test_stored_stem_verdict_cannot_bypass_a_narrower_policy(tmp_path):
    report = {'scale_gate': {'min': .95, 'max': 1.05},
              'input_scales': {'zone/c0': {
                  'status': 'pass', 'median': 1.075, 'explanation': 'old band'}}}
    status, why, median = run_models.resolve_scale(
        'zone/c0', {}, report, Workspace(tmp_path), '', LOG)
    assert status == 'fail'
    assert 'outside 0.95-1.05' in why
    assert median == 1.075


@pytest.mark.parametrize('verdict', [
    {'status': 'pass'},
    *({'status': 'pass', 'median': value} for value in (
        None, True, False, '1.0', float('nan'), float('inf'),
        float('-inf'), 10 ** 400)),
    {'status': 'unknown', 'median': 1.0},
    'invalid',
])
def test_malformed_saved_scale_cannot_pass_without_a_measurement(tmp_path, verdict):
    report = {'input_scales': {'zone/c0': verdict}}
    status, why, median = run_models.resolve_scale(
        'zone/c0', {}, report, Workspace(tmp_path), '', LOG)
    assert status == 'unmeasured'
    assert median is None


@pytest.mark.parametrize('value', [None, True, '1.0', float('nan'), float('inf')])
def test_malformed_saved_scale_is_remeasured_from_component(tmp_path, value):
    export, union = _scale_fixture(tmp_path)
    key = 'cluster_0/cluster_0_a1_c1'
    report = {'input_scales': {key: {'status': 'pass', 'median': value}}}
    status, why, median = run_models.resolve_scale(
        key, {'rsalign': str(export)}, report,
        Workspace(tmp_path), str(union), LOG)
    assert status == 'fail', why
    assert median == pytest.approx(.2)
    assert 'quantile-ratio' in why


@pytest.mark.parametrize('gate', [
    None, [], 'invalid',
    *({'min': value, 'max': 1.1} for value in (
        None, True, '0.9', float('nan'), float('inf'), 0, -1, 10 ** 400)),
    *({'min': .9, 'max': value} for value in (
        None, True, '1.1', float('nan'), float('inf'), 0, -1, .8)),
])
def test_invalid_saved_band_fails_closed(tmp_path, gate):
    report = {'scale_gate': gate, 'input_scales': {
        'zone/c0': {'status': 'pass', 'median': 1.0}}}
    status, why, median = run_models.resolve_scale(
        'zone/c0', {}, report, Workspace(tmp_path), '', LOG)
    assert status == 'fail'
    assert why == 'invalid saved scale band'
    assert median is None


@pytest.mark.parametrize('value', [1, 1.0])
def test_finite_numeric_stored_scale_can_pass(tmp_path, value):
    report = {'input_scales': {'zone/c0': {'status': 'pass', 'median': value}}}
    status, why, median = run_models.resolve_scale(
        'zone/c0', {}, report, Workspace(tmp_path), '', LOG)
    assert status == 'pass', why
    assert median == 1.0


def test_original_zone_quantile_filters_cumulative_harvest(tmp_path):
    export, union = _scale_fixture(tmp_path, fused=False)
    _cloud(export.parent / 'identity_r0', 100, prefix='other_')
    status, why, median = run_models.resolve_scale(
        'zone_0/zone_0_c1', {'rsalign': str(export)}, {},
        Workspace(tmp_path), str(union), LOG)
    assert status == 'pass', why
    assert median == pytest.approx(1.0)


def _workspace(tmp_path, count=2):
    assembly = tmp_path / 'merged' / 'assembly'
    assembly.mkdir(parents=True)
    project = assembly / 'Assembly.rsproj'
    project.write_bytes(b'project')
    data = assembly / 'Assembly'
    data.mkdir()
    (data / 'sfm0.dat').write_bytes(b'scene data')
    (data / '.lock').write_bytes(b'runtime lock')
    log = assembly / 'flight_log_UTM.txt'
    log.write_text('name;x;y;z\n', encoding='utf-8')
    finals = []
    for index in range(count):
        export = tmp_path / 'aligned_components' / f'zone_{index}' / 'c0.rsalign'
        export.parent.mkdir(parents=True)
        export.write_bytes(f'component {index}'.encode())
        Path(str(export) + '.manifest.json').write_text(
            json.dumps({'images': ['image.jpg']}), encoding='utf-8')
        finals.append({'key': f'zone_{index}/zone_{index}_c0',
                       'camera_count': 30 + index, 'rsalign': str(export)})
    report = {'input_scales': {
        component['key']: {'status': 'pass', 'median': 1.0, 'explanation': 'sound'}
        for component in finals}, 'clusters': [{'final_components': finals}],
        'scale_gate': {'enabled': True, 'min': .90, 'max': 1.10}}
    merge_report = tmp_path / 'merged' / 'merge_report.json'
    merge_report.write_text(json.dumps(report), encoding='utf-8')
    return project, merge_report, report


class ModelCLI:
    def __init__(self, results, *, mutate_failures=False, failure_shutdown=True):
        self.results = list(results)
        self.mutate_failures = mutate_failures
        self.failure_shutdown = failure_shutdown
        self.calls = []

    def run_batch_script(self, script, args, logs):
        self.calls.append((script, args))
        success = self.results.pop(0)
        if script == 'GenerateModel.bat' and (success or self.mutate_failures):
            project = Path(args[0])
            project.write_bytes(project.read_bytes() + args[1].encode())
            data = project.with_suffix('') / 'sfm0.dat'
            data.write_bytes(data.read_bytes() + args[1].encode())
        return SimpleNamespace(success=success,
                               errors='' if success else 'failed',
                               shutdown_verified=True if success else self.failure_shutdown)


def _run(tmp_path, monkeypatch, results, *, free_space=None, mutate_failures=False,
         failure_shutdown=True):
    cli = ModelCLI(results, mutate_failures=mutate_failures,
                   failure_shutdown=failure_shutdown)
    with monkeypatch.context() as context, patch.dict(os.environ, os.environ.copy()):
        context.setattr(sys, 'argv', ['run_models.py', '--workspace', str(tmp_path)])
        context.setattr(run_models, 'make_cli', lambda: cli)
        context.setattr(run_models.logging, 'FileHandler', lambda *a, **k: logging.NullHandler())
        context.setattr(run_models.logging, 'basicConfig', lambda *a, **k: None)
        context.setattr(run_models.shutil, 'disk_usage', free_space or (
            lambda path: SimpleNamespace(free=100 * 1024**3)))
        rc = run_models.main()
    output = json.loads((tmp_path / 'models_report.json').read_text(encoding='utf-8'))
    return rc, output, cli


def test_partial_model_failure_returns_failure(tmp_path, monkeypatch):
    _workspace(tmp_path)
    rc, report, cli = _run(tmp_path, monkeypatch, [True, False, True])
    assert rc == 1
    assert [model['success'] for model in report['models']] == [True, False]
    assert cli.calls[-1][0] == 'SaveProjectCopy.bat'


@pytest.mark.parametrize('shutdown_verified', [False, None])
def test_failed_model_without_verified_shutdown_never_starts_project_copy(
        tmp_path, monkeypatch, shutdown_verified):
    _workspace(tmp_path)
    rc, report, cli = _run(tmp_path, monkeypatch, [True, False],
                           failure_shutdown=shutdown_verified)
    assert rc == 1
    assert [model['success'] for model in report['models']] == [True, False]
    assert 'dated_copy' not in report
    assert [script for script, args in cli.calls] == [
        'GenerateModel.bat', 'GenerateModel.bat']


def test_failed_dated_copy_returns_failure(tmp_path, monkeypatch):
    _workspace(tmp_path, count=1)
    rc, report, cli = _run(tmp_path, monkeypatch, [True, False])
    assert rc == 1
    assert not report['dated_copy']['success']


def test_scale_blocked_component_keeps_partial_run_failed(tmp_path, monkeypatch):
    _, merge_path, merge = _workspace(tmp_path)
    merge['input_scales']['zone_1/zone_1_c0']['status'] = 'fail'
    merge_path.write_text(json.dumps(merge), encoding='utf-8')
    rc, report, cli = _run(tmp_path, monkeypatch, [True, True])
    assert rc == 1
    assert report['models'][1]['skipped'] == 'scale_gate'


def test_disk_floor_after_a_success_keeps_partial_run_failed(tmp_path, monkeypatch):
    _workspace(tmp_path)
    space = iter([100 * 1024**3, 0])
    rc, report, cli = _run(tmp_path, monkeypatch, [True, True],
                           free_space=lambda path: SimpleNamespace(free=next(space)))
    assert rc == 1
    assert report['models'][1]['skipped'] == 'disk_floor'


def test_unchanged_saved_models_resume_after_their_own_scene_updates(tmp_path, monkeypatch):
    project, _, _ = _workspace(tmp_path)
    rc, report, _ = _run(tmp_path, monkeypatch, [True, True, True])
    assert rc == 0
    assert report['project_state'] == run_models.project_state(project)
    (project.with_suffix('') / '.lock').write_bytes(b'new runtime lock')
    rc, resumed, cli = _run(tmp_path, monkeypatch, [True])
    assert rc == 0
    assert [script for script, args in cli.calls] == ['SaveProjectCopy.bat']
    assert len(resumed['models']) == 2


@pytest.mark.parametrize('changed', [
    'project', 'companion', 'component', 'manifest', 'navigation', 'scale_policy',
    'project_path', 'model_settings',
])
def test_changed_model_inputs_do_not_authorize_resume(tmp_path, monkeypatch, changed):
    project, merge_path, merge = _workspace(tmp_path, count=1)
    _, prior, _ = _run(tmp_path, monkeypatch, [True, True])
    component = Path(merge['clusters'][0]['final_components'][0]['rsalign'])
    if changed == 'project':
        project.write_bytes(b'new assembly')
    elif changed == 'companion':
        (project.with_suffix('') / 'sfm0.dat').write_bytes(b'changed scene data')
    elif changed == 'component':
        component.write_bytes(b'new component')
    elif changed == 'manifest':
        Path(str(component) + '.manifest.json').write_text('{}', encoding='utf-8')
    elif changed == 'navigation':
        (project.parent / 'flight_log_UTM.txt').write_text('new navigation', encoding='utf-8')
    elif changed == 'scale_policy':
        merge['scale_gate']['min'] = .95
        merge_path.write_text(json.dumps(merge), encoding='utf-8')
    elif changed == 'project_path':
        prior['project_state']['path'] = str(tmp_path / 'old' / 'Assembly.rsproj')
        (tmp_path / 'models_report.json').write_text(json.dumps(prior), encoding='utf-8')
    else:
        settings = tmp_path / 'metadata'
        settings.mkdir()
        (settings / 'Texturing.xml').write_text('<settings/>', encoding='utf-8')
        monkeypatch.setattr(run_models, 'METADATA_DIR', str(settings))
    rc, report, cli = _run(tmp_path, monkeypatch, [True, True])
    assert rc == 0
    assert cli.calls[0][0] == 'GenerateModel.bat'


@pytest.mark.parametrize('legacy', [True, False])
def test_legacy_and_direct_records_never_authorize_workspace_resume(
        tmp_path, monkeypatch, legacy):
    _workspace(tmp_path, count=1)
    _, prior, _ = _run(tmp_path, monkeypatch, [True, True])
    if legacy:
        prior = {'project': 'D:/old/Assembly.rsproj',
                 'models': [{'component': 'zone_0_c0', 'success': True}]}
    else:
        prior['models'][0]['mode'] = 'direct'
    (tmp_path / 'models_report.json').write_text(json.dumps(prior), encoding='utf-8')
    rc, report, cli = _run(tmp_path, monkeypatch, [True, True])
    assert rc == 0
    assert cli.calls[0][0] == 'GenerateModel.bat'


def test_failed_workflow_does_not_certify_a_changed_scene(tmp_path, monkeypatch):
    project, _, _ = _workspace(tmp_path)
    rc, report, _ = _run(tmp_path, monkeypatch, [True, False, True],
                         mutate_failures=True)
    assert rc == 1
    assert report['project_state'] != run_models.project_state(project)
    rc, report, cli = _run(tmp_path, monkeypatch, [True, True, True])
    assert rc == 0
    assert [script for script, args in cli.calls].count('GenerateModel.bat') == 2


@pytest.mark.parametrize('changed', ['status', 'success', 'component_key'])
def test_malformed_success_records_cannot_authorize_resume(tmp_path, monkeypatch, changed):
    _workspace(tmp_path, count=1)
    _, prior, _ = _run(tmp_path, monkeypatch, [True, True])
    prior['models'][0][changed] = {
        'status': 'fail', 'success': 'false', 'component_key': ['zone_0/zone_0_c0'],
    }[changed]
    (tmp_path / 'models_report.json').write_text(json.dumps(prior), encoding='utf-8')
    rc, report, cli = _run(tmp_path, monkeypatch, [True, True])
    assert rc == 0
    assert cli.calls[0][0] == 'GenerateModel.bat'
