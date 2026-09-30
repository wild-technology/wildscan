"""Pose refinement accepts path-bearing names without hiding collisions."""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.realpath(__file__))))
import poses_to_flight_log


def _inputs(tmp_path):
    poses = tmp_path / 'poses'
    camera = poses / 'CamLower'
    camera.mkdir(parents=True)
    names = [f'camlower_20260930T01000{i}Z' for i in range(3)]
    for index, name in enumerate(names):
        (camera / f'{name}.xmp').write_text(
            f'<xcr:Position>{index} {index * index} 0</xcr:Position>', encoding='utf-8')
    log = tmp_path / 'flight_log.txt'
    header = ('Name;X (East);Y (North);Alt;X Accuracy;Y Accuracy;Alt Accuracy;'
              'Yaw;Pitch;Roll;Yaw Accuracy;Pitch Accuracy;Roll Accuracy\n')
    rows = [f'CamLower/{name.upper()}.JPG;{500000 + index};'
            f'{4000000 + index * index};-100;10;10;1;0;10;0;15;5;15'
            for index, name in enumerate(names)]
    log.write_text(header + '\n'.join(rows) + '\n', encoding='utf-8')
    return poses, log, names, rows


def _run(poses, log, output, monkeypatch):
    monkeypatch.setattr(sys, 'argv', ['poses_to_flight_log.py', '--images-dir', str(poses),
                                    '--flight-log', str(log), '--output', str(output)])
    monkeypatch.setattr(poses_to_flight_log, 'SettingsStore', lambda: object())
    poses_to_flight_log.main()


def test_path_bearing_names_match_and_preserve_written_identity(tmp_path, monkeypatch):
    poses, log, _names, original = _inputs(tmp_path)
    output = tmp_path / 'refined.txt'
    _run(poses, log, output, monkeypatch)
    _header, rows = poses_to_flight_log.read_flight_log(str(output))
    assert len(rows) == 3
    assert [row[0] for row in rows] == [row.split(';')[0] for row in original]
    assert all(float(row[4]) == 1.0 for row in rows)


def test_two_log_paths_one_stem_are_refused_before_output(tmp_path, monkeypatch):
    poses, log, _names, rows = _inputs(tmp_path)
    with log.open('a', encoding='utf-8') as stream:
        stream.write(rows[0].replace('CamLower/', 'other/') + '\n')
    output = tmp_path / 'refined.txt'
    with pytest.raises(SystemExit, match='Ambiguous flight-log image stem'):
        _run(poses, log, output, monkeypatch)
    assert not output.exists()


def test_two_xmp_paths_one_stem_are_refused(tmp_path):
    poses, _log, names, _rows = _inputs(tmp_path)
    other = poses / 'other'
    other.mkdir()
    (other / f'{names[0].upper()}.xmp').write_text(
        '<xcr:Position>0 0 0</xcr:Position>', encoding='utf-8')
    with pytest.raises(ValueError, match='Ambiguous XMP image stem'):
        poses_to_flight_log.read_xmp_positions(str(poses))


def test_registered_pose_without_prior_is_refined_but_excluded_from_fit(tmp_path,
                                                                     monkeypatch):
    poses, log, _names, _rows = _inputs(tmp_path)
    name = 'camlower_20260930T010003Z'
    (poses / 'CamLower' / f'{name}.xmp').write_text(
        '<xcr:Position>3 9 0</xcr:Position>', encoding='utf-8')
    with log.open('a', encoding='utf-8') as stream:
        stream.write(f'{name}.jpg;;;;10;10;1;0;10;0;15;5;15\n')
    output = tmp_path / 'refined.txt'
    _run(poses, log, output, monkeypatch)
    _header, rows = poses_to_flight_log.read_flight_log(str(output))
    assert len(rows) == 4
    assert [float(value) for value in rows[-1][1:4]] == pytest.approx(
        [500003, 4000009, -100], abs=1e-6)
    residuals = output.with_name('refined_residuals.csv').read_text(encoding='utf-8')
    assert len(residuals.splitlines()) == 4


@pytest.mark.parametrize('value', ['nan', 'inf', '-inf'])
def test_nonfinite_pose_prior_is_excluded_and_refined(tmp_path, monkeypatch, value):
    poses, log, _names, _rows = _inputs(tmp_path)
    name = 'camlower_20260930T010003Z'
    (poses / 'CamLower' / f'{name}.xmp').write_text(
        '<xcr:Position>3 9 0</xcr:Position>', encoding='utf-8')
    with log.open('a', encoding='utf-8') as stream:
        stream.write(f'{name}.jpg;{value};4000009;-100;10;10;1;0;10;0;15;5;15\n')
    output = tmp_path / 'refined.txt'
    _run(poses, log, output, monkeypatch)
    _header, rows = poses_to_flight_log.read_flight_log(str(output))
    assert [float(value) for value in rows[-1][1:4]] == pytest.approx(
        [500003, 4000009, -100], abs=1e-6)
    assert len(output.with_name('refined_residuals.csv').read_text(
        encoding='utf-8').splitlines()) == 4


@pytest.mark.parametrize('coordinates', ['nan 0 0', '0 inf 0', '0 0 -inf', '1 2', '1 2 3 4'])
def test_invalid_xmp_positions_are_refused_before_output(tmp_path, monkeypatch, coordinates):
    poses, log, names, _rows = _inputs(tmp_path)
    (poses / 'CamLower' / f'{names[0]}.xmp').write_text(
        f'<xcr:Position>{coordinates}</xcr:Position>', encoding='utf-8')
    output = tmp_path / 'refined.txt'
    with pytest.raises(SystemExit, match='three finite coordinates'):
        _run(poses, log, output, monkeypatch)
    assert not output.exists()
