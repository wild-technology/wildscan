"""Offline workspace completion checks against current input provenance."""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from modules.align_fingerprint import sha256_file
from modules.workspace_census import Workspace
from tests.test_model_driver import _run, _workspace


def _signature(path):
    stat = path.stat()
    return {'size': stat.st_size, 'mtime_ns': stat.st_mtime_ns,
            'sha256': sha256_file(str(path))}


def _preprocessed(tmp_path):
    source = tmp_path / 'source'
    output = tmp_path / 'workspace' / 'preprocessed_images'
    source.mkdir()
    output.mkdir(parents=True)
    (source / 'frame.png').write_bytes(b'source image')
    (output / 'frame.png').write_bytes(b'processed image')
    manifest = {'schema': 1, 'status': 'complete',
                'settings': {'input_dir': str(source)}, 'images': {
                    'frame.png': {'source': _signature(source / 'frame.png'),
                                  'output': _signature(output / 'frame.png')}}}
    marker = output.parent / 'preprocessed_images.manifest.json'
    marker.write_text(json.dumps(manifest), encoding='utf-8')
    return Workspace(output.parent), source, output, marker, manifest


def test_complete_preprocessing_censuses_current_unchanged_files(tmp_path):
    workspace, *_ = _preprocessed(tmp_path)
    assert workspace._detect_preprocess().status == 'done'


@pytest.mark.parametrize('status', ['failed', 'in_progress', None])
def test_preprocessing_completion_is_not_inferred_from_old_images(tmp_path, status):
    workspace, source, output, marker, manifest = _preprocessed(tmp_path)
    manifest['status'] = status
    marker.write_text(json.dumps(manifest), encoding='utf-8')
    assert workspace._detect_preprocess().status == 'partial'


def test_preprocessing_legacy_outputs_need_verification(tmp_path):
    workspace, source, output, marker, _ = _preprocessed(tmp_path)
    marker.unlink()
    assert workspace._detect_preprocess().status == 'partial'


@pytest.mark.parametrize('changed', [
    'source_content', 'output_content', 'source_added', 'source_removed',
    'output_added', 'output_removed', 'unsupported_output_added', 'bad_record',
])
def test_preprocessing_changed_inputs_or_outputs_are_partial(tmp_path, changed):
    workspace, source, output, marker, manifest = _preprocessed(tmp_path)
    if changed == 'source_content':
        (source / 'frame.png').write_bytes(b'new source image')
    elif changed == 'output_content':
        (output / 'frame.png').write_bytes(b'new processed image')
    elif changed.endswith('_added'):
        folder = source if changed == 'source_added' else output
        suffix = '.heif' if changed == 'unsupported_output_added' else '.png'
        (folder / ('extra' + suffix)).write_bytes(b'extra')
    elif changed.endswith('_removed'):
        folder = source if changed == 'source_removed' else output
        (folder / 'frame.png').unlink()
    else:
        manifest['images']['frame.png'] = 'invalid'
        marker.write_text(json.dumps(manifest), encoding='utf-8')
    assert workspace._detect_preprocess().status == 'partial'


def test_preprocessing_file_alias_never_counts_as_complete(tmp_path, monkeypatch):
    workspace, source, output, marker, _ = _preprocessed(tmp_path)
    target = output / 'frame.png'
    real = Path.is_symlink
    monkeypatch.setattr(Path, 'is_symlink', lambda path: path == target or real(path))
    assert workspace._detect_preprocess().status == 'blocked'


@pytest.mark.skipif(os.name != 'nt', reason='Windows NTFS junction fixture')
def test_preprocessing_junction_never_reuses_an_old_complete_manifest(tmp_path):
    workspace, source, output, marker, manifest = _preprocessed(tmp_path)
    (source / 'nested').mkdir()
    (source / 'frame.png').rename(source / 'nested' / 'frame.png')
    (output / 'frame.png').unlink()
    link = output / 'nested'
    made = subprocess.run(['cmd', '/c', 'mklink', '/J', str(link), str(source / 'nested')],
                          capture_output=True, text=True)
    if made.returncode:
        pytest.skip('Cannot create an NTFS junction here')
    try:
        manifest['images'] = {os.path.join('nested', 'frame.png'): {
            'source': _signature(source / 'nested' / 'frame.png'),
            'output': _signature(link / 'frame.png')}}
        marker.write_text(json.dumps(manifest), encoding='utf-8')
        before = (source / 'nested' / 'frame.png').read_bytes()
        assert workspace._detect_preprocess().status == 'blocked'
        assert (source / 'nested' / 'frame.png').read_bytes() == before
    finally:
        link.rmdir()


def test_current_model_report_censuses_done_after_mocked_driver(tmp_path, monkeypatch):
    _workspace(tmp_path)
    result, _, _ = _run(tmp_path, monkeypatch, [True, True, True])
    assert result == 0
    assert Workspace(tmp_path)._detect_model().status == 'done'


@pytest.mark.parametrize('changed', [
    'project', 'companion', 'component', 'manifest', 'navigation', 'scale_policy',
    'membership', 'recipe', 'settings', 'dated_copy', 'direct_record', 'legacy_record',
])
def test_model_census_rejects_stale_or_incomplete_completion(tmp_path, monkeypatch, changed):
    project, merge_path, merge = _workspace(tmp_path, count=1)
    _, report, _ = _run(tmp_path, monkeypatch, [True, True])
    component = Path(merge['clusters'][0]['final_components'][0]['rsalign'])
    if changed == 'project':
        project.write_bytes(b'replaced assembly')
    elif changed == 'companion':
        (project.with_suffix('') / 'sfm0.dat').write_bytes(b'replaced scene data')
    elif changed == 'component':
        component.write_bytes(b'replaced alignment')
    elif changed == 'manifest':
        Path(str(component) + '.manifest.json').write_text('{}', encoding='utf-8')
    elif changed == 'navigation':
        (project.parent / 'flight_log_UTM.txt').write_text('new navigation', encoding='utf-8')
    elif changed in ('scale_policy', 'membership'):
        if changed == 'scale_policy':
            merge['scale_gate']['min'] = .99
        else:
            merge['clusters'][0]['final_components'].append({
                'key': 'zone_new/c0', 'rsalign': str(tmp_path / 'new.rsalign')})
        merge_path.write_text(json.dumps(merge), encoding='utf-8')
    elif changed in ('recipe', 'settings'):
        from modules.realityscan_interface import realityscan_cli
        folder = tmp_path / 'configuration'
        folder.mkdir()
        if changed == 'recipe':
            (folder / 'GenerateModel.bat').write_text('changed recipe', encoding='utf-8')
            monkeypatch.setattr(realityscan_cli, 'SCRIPTS_DIR', str(folder))
        else:
            (folder / 'Mesh.xml').write_text('<changed/>', encoding='utf-8')
            monkeypatch.setattr(realityscan_cli, 'METADATA_DIR', str(folder))
    else:
        if changed == 'dated_copy':
            report['dated_copy']['success'] = False
        elif changed == 'direct_record':
            report['models'][0]['mode'] = 'direct'
        else:
            report = {'models': [{'component': 'zone_0_c0', 'success': True}]}
        (tmp_path / 'models_report.json').write_text(json.dumps(report), encoding='utf-8')
    assert Workspace(tmp_path)._detect_model().status == 'partial'
