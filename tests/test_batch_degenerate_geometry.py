#!/usr/bin/env python3
"""Batch Directory on positions with no spatial extent.

A Wild Sync run whose navigation fix never moved gives every image the
same X/Y. Clustering that into two zones left the second one empty: a
folder holding only a flight log, which alignment then counted as a failed
zone. The zone outline code also hit Qhull on the zero-area geometry.

Offline: real zoning and plotting on a tiny fixture, no RealityScan.
"""
from __future__ import annotations

import logging
import os
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
sys.path.insert(0, REPO_ROOT)

pytest.importorskip('pandas')
pytest.importorskip('geopandas')
matplotlib = pytest.importorskip('matplotlib')
matplotlib.use('Agg')

from module_base.parameter import Parameter
from modules.image_batcher.batch_directory import BatchDirectory

LOGGER_NAME = 'batch-degenerate-test'

HEADER = ('filename;X (East);Y (North);Alt;X Accuracy;Y Accuracy;'
          'Alt Accuracy;Yaw;Pitch;Roll;Yaw Accuracy;Pitch Accuracy;'
          'Roll Accuracy')


class FakeStore:
    def get(self, section, key, fallback=None):
        return fallback

    def set(self, section, key, value):
        pass


def _names(frames: int) -> list[str]:
    names = []
    for i in range(frames):
        stamp = f'20260820_1925{42 + i // 2:02d}.{"42" if i % 2 == 0 else "92"}'
        names += [f'Cam1_{stamp}.card.JPG', f'Cam2_{stamp}.card.JPG']
    return names


def _run(tmp_path, monkeypatch, positions):
    """BatchDirectory.run() end to end on one image per position row."""
    names = _names(len(positions) // 2)
    source = tmp_path / 'ws' / 'raw_images'
    source.mkdir(parents=True)
    rows = [HEADER]
    for name, (x, y) in zip(names, positions):
        (source / name).write_bytes(b'j')
        rows.append(f'{name};{x:.6f};{y:.6f};0;1000;1000;1;15;1;-10;15;15;15')
    log = source / 'flight_log_19T_UTM.txt'
    log.write_text('\n'.join(rows) + '\n', encoding='utf-8')

    logger = logging.getLogger(LOGGER_NAME)
    module = BatchDirectory(logger)
    module.settings = FakeStore()
    params = {'output_dir': Parameter('output_dir', None, 'output_dir', str,
                                      None, prompt_user=False)}
    params['output_dir'].set_value(str(tmp_path / 'ws'))
    for name, value in (('batch_target_images_per_zone', 3000),
                        ('batch_min_zone_size', 100),
                        ('batch_max_zone_size', 5000),
                        ('batch_initial_overlap_percent', 10.0),
                        ('batch_density_weight', 0.0),
                        ('batch_kde_bandwidth', 0.0),
                        ('batch_overlap_max_distance_m', 0.0),
                        ('batch_input_image_dir', str(source)),
                        ('batch_flight_log_path', str(log))):
        p = Parameter(name, None, name, type(value), value, prompt_user=False)
        p.set_value(value)
        params[name] = p
    module.params = params

    def eof(*_a, **_k):
        raise EOFError
    monkeypatch.setattr('builtins.input', eof)
    monkeypatch.delenv('RS_SHOW_PLOTS', raising=False)
    os.makedirs(tmp_path / 'ws' / 'batched_images_by_zone')
    return module.run(), tmp_path / 'ws' / 'batched_images_by_zone'


def _zones(batched):
    return sorted(p for p in batched.iterdir() if p.is_dir())


def _images(zone):
    return [p for p in zone.rglob('*.JPG')]


def _records(caplog, level):
    return [r for r in caplog.records
            if r.name == LOGGER_NAME and r.levelno == level]


def test_a_static_position_gives_one_zone_and_no_hull_error(tmp_path,
                                                            monkeypatch,
                                                            caplog):
    caplog.set_level(logging.DEBUG, logger=LOGGER_NAME)
    result, batched = _run(tmp_path, monkeypatch,
                           [(294952.55, 4588707.83)] * 12)
    assert result['Success'] is True
    zones = _zones(batched)
    assert [z.name for z in zones] == ['zone_1']
    assert len(_images(zones[0])) == 12
    assert result['Number of Zones'] == 1
    text = '\n'.join(r.getMessage() for r in caplog.records)
    assert 'hull' not in text.lower() and 'qhull' not in text.lower()
    assert not _records(caplog, logging.ERROR)
    warnings = [r.getMessage() for r in _records(caplog, logging.WARNING)]
    assert len(warnings) == 1, warnings
    assert 'one position' in warnings[0]


def test_collinear_positions_zone_without_a_hull_error(tmp_path,
                                                       monkeypatch, caplog):
    caplog.set_level(logging.DEBUG, logger=LOGGER_NAME)
    positions = [(294952.55, 4588707.83 + 0.5 * i) for i in range(12)]
    result, batched = _run(tmp_path, monkeypatch, positions)
    assert result['Success'] is True
    zones = _zones(batched)
    assert zones and all(_images(z) for z in zones)
    text = '\n'.join(r.getMessage() for r in caplog.records)
    assert 'qhull' not in text.lower()
    assert 'could not generate convex hull' not in text.lower()
    assert not _records(caplog, logging.ERROR)
    assert not _records(caplog, logging.WARNING)
    infos = [r.getMessage() for r in _records(caplog, logging.INFO)
             if 'positions are collinear' in r.getMessage()]
    assert len(infos) == 1, infos


def test_spread_positions_still_zone_and_draw_outlines(tmp_path, monkeypatch,
                                                       caplog):
    caplog.set_level(logging.DEBUG, logger=LOGGER_NAME)
    positions = [(294952.55 + 3.0 * (i % 4), 4588707.83 + 2.0 * (i // 4))
                 for i in range(12)]
    result, batched = _run(tmp_path, monkeypatch, positions)
    assert result['Success'] is True
    zones = _zones(batched)
    assert zones and all(_images(z) for z in zones)
    text = '\n'.join(r.getMessage() for r in caplog.records)
    assert 'one position' not in text
    assert 'positions are collinear' not in text
    assert not _records(caplog, logging.WARNING)


def test_more_clusters_than_distinct_positions_leave_no_empty_zone():
    """k-means asked for more clusters than there are distinct positions
    returns empty clusters; none of them may survive as a zone."""
    import geopandas as gpd
    from shapely.geometry import Point

    module = BatchDirectory(logging.getLogger(LOGGER_NAME))
    module.params = {}
    xy = [(0.0, 0.0)] * 6 + [(10.0, 0.0)] * 6
    gdf = gpd.GeoDataFrame({'filename': _names(6), 'density': [1.0] * 12},
                           geometry=[Point(x, y) for x, y in xy])
    out, count = module._BatchDirectory__adaptive_zone_creation(
        gdf, target_size=4, min_size=1, max_size=100, density_weight=0.0)
    sizes = [int((out['cluster'] == i).sum()) for i in range(count)]
    assert sizes and all(sizes), sizes
    assert sum(sizes) == 12
