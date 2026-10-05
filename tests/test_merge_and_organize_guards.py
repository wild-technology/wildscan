#!/usr/bin/env python3
"""Merge union log and merge exit codes.

merge_zones.build_union_flight_log:
  - the coordinate system for the whole merge came from zone_logs[0] in
    os.walk order, while the ROWS were read in sorted() order. One stray
    untagged or foreign-zone *_UTM.txt anywhere under images_root decided
    the frame of the whole merge on a logger.warning.
  - when only_basenames matched nothing, a HEADER-ONLY union log was
    written and logged at INFO as '0 rows'; the workflow then imported it,
    ran -update against zero constraints, and shipped an UNGEOREFERENCED
    merged component with workflow_success true.

merge_zones.main:
  - --auto_model logged every model failure and still returned 0, so a run
    in which NO model was produced reported 'Merge stage complete'.
    run_models.py does the opposite for the same operation.
  - EVALUATION_READY.txt was written BEFORE the assembly result was
    checked: an on-disk document declaring a terminal state for a project
    that was never saved.

Offline: no RealityScan; merge_zones' union builder is called directly and
the exit-code contracts are checked as source structure.

Run:  py -3.13 -m pytest tests/test_merge_and_organize_guards.py
"""
from __future__ import annotations

import logging
import os
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
sys.path.insert(0, REPO_ROOT)

QUIET = logging.getLogger('merge-test')
QUIET.addHandler(logging.NullHandler())
QUIET.propagate = False

HEADER = 'filename;X (East);Y (North);Alt'


def _zone_log(directory, name, images):
    directory.mkdir(parents=True, exist_ok=True)
    rows = [HEADER] + [f'{n};1;2;3' for n in images]
    (directory / name).write_text('\n'.join(rows) + '\n', encoding='utf-8')


def _merge_zones():
    pytest.importorskip('numpy')
    import merge_zones
    return merge_zones


# ------------------------------------------------------------- union frame

def test_a_stray_untagged_log_cannot_flip_the_whole_merge(tmp_path):
    """One untagged log under images_root used to decide the frame of the
    entire merge, on a warning."""
    merge_zones = _merge_zones()
    images = tmp_path / 'batched'
    _zone_log(images / 'zone_1', 'flight_log_53N_UTM.txt', ['a.jpg'])
    _zone_log(images / 'zone_2', 'flight_log_UTM.txt', ['b.jpg'])
    out = tmp_path / 'merged'
    out.mkdir()
    with pytest.raises(ValueError) as exc:
        merge_zones.build_union_flight_log(str(images), str(out), QUIET)
    assert 'no zone tag' in str(exc.value)
    assert 'flight_log_UTM.txt' in str(exc.value)


def test_disagreeing_zones_are_refused(tmp_path):
    merge_zones = _merge_zones()
    images = tmp_path / 'batched'
    _zone_log(images / 'zone_1', 'flight_log_53N_UTM.txt', ['a.jpg'])
    _zone_log(images / 'zone_2', 'flight_log_57L_UTM.txt', ['b.jpg'])
    out = tmp_path / 'merged'
    out.mkdir()
    with pytest.raises(ValueError, match='DISAGREEING'):
        merge_zones.build_union_flight_log(str(images), str(out), QUIET)


def test_a_consistent_utm_merge_still_builds(tmp_path):
    merge_zones = _merge_zones()
    images = tmp_path / 'batched'
    _zone_log(images / 'zone_1', 'flight_log_53N_UTM.txt', ['a.jpg'])
    _zone_log(images / 'zone_2', 'flight_log_53N_UTM.txt', ['b.jpg'])
    out = tmp_path / 'merged'
    out.mkdir()
    union, params = merge_zones.build_union_flight_log(
        str(images), str(out), QUIET)
    assert os.path.basename(union) == 'flight_log_53N_UTM.txt'
    assert 'epsg:32653' in open(params, encoding='utf-8').read()
    assert len(open(union, encoding='utf-8').read().splitlines()) == 3


def test_a_merge_of_untagged_logs_is_refused(tmp_path):
    """Logs that agree but carry no zone tag give the union log no
    coordinate system to be imported in."""
    merge_zones = _merge_zones()
    images = tmp_path / 'batched'
    _zone_log(images / 'zone_1', 'flight_log_UTM.txt', ['a.jpg'])
    _zone_log(images / 'zone_2', 'flight_log_UTM.txt', ['b.jpg'])
    out = tmp_path / 'merged'
    out.mkdir()
    with pytest.raises(ValueError, match='carries a UTM zone tag'):
        merge_zones.build_union_flight_log(str(images), str(out), QUIET)
    assert not list(out.iterdir()), 'nothing may be written for a refused merge'


def test_a_zero_row_union_log_is_refused(tmp_path):
    """It used to be written, imported, and -update'd against zero
    constraints - an ungeoreferenced merged component with
    workflow_success true."""
    merge_zones = _merge_zones()
    images = tmp_path / 'batched'
    _zone_log(images / 'zone_1', 'flight_log_53N_UTM.txt', ['a.jpg'])
    out = tmp_path / 'merged'
    out.mkdir()
    with pytest.raises(ValueError) as exc:
        merge_zones.build_union_flight_log(
            str(images), str(out), QUIET,
            only_basenames={'nothing_matches.jpg'})
    assert 'ZERO rows' in str(exc.value)
    assert not list(out.glob('flight_log*')), 'a useless log was written'


def test_the_frame_decision_uses_a_sorted_list():
    """The frame came from zone_logs[0] in os.walk order while the rows
    were read sorted() - two different orders over the same list."""
    source = open(os.path.join(REPO_ROOT, 'merge_zones.py'),
                  encoding='utf-8').read()
    body = source[source.index('def build_union_flight_log'):
                  source.index('def build_union_flight_log') + 4000]
    assert 'zone_logs = sorted(zone_logs)' in body
    assert 'assert_one_zone(zone_logs' in body
    assert 'for log_path in sorted(zone_logs)' not in body


# ------------------------------------------------------- merge exit codes

def test_auto_model_failures_fail_the_run():
    """run_models.py stops on the first model failure 'so evidence
    survives'; this loop logged every failure and returned 0."""
    source = open(os.path.join(REPO_ROOT, 'merge_zones.py'),
                  encoding='utf-8').read()
    assert 'model_failures = []' in source
    assert 'model_failures.append(comp_name)' in source
    tail = source[source.index('if model_failures:'):]
    assert tail.split('return')[1].strip().startswith('1')


def test_the_evaluation_gate_is_written_only_on_success():
    """EVALUATION_READY.txt names a project whose existence was never
    checked, and the census reads its presence as merge status 'done'."""
    source = open(os.path.join(REPO_ROOT, 'merge_zones.py'),
                  encoding='utf-8').read()
    failure_return = source.index("logger.error('Assembly workflow failed")
    gate_write = source.index("eval_path = os.path.join(output_dir, "
                              "'EVALUATION_READY.txt')")
    assert failure_return < gate_write, \
        'the gate is still written before the assembly result is checked'
    assert 'EVALUATION_BLOCKED.txt' in source
