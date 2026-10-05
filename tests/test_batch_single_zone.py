"""Batch Directory: datasets below b_single_zone_below are one zone.

Fewer than 4000 images (the default) never need batching: they go into a
single zone with no clustering, no splitting and no overlap copies. Fewer
than 100 images still run, with a warning. At or above the threshold the
zoning is unchanged.
"""
from __future__ import annotations

import logging

import numpy as np

from modules.image_batcher import batch_directory
from tests.test_batch_degenerate_geometry import (
    LOGGER_NAME,
    _images,
    _records,
    _run,
    _zones,
)

DEFAULTS = {'batch_single_zone_below': 4000,
            'batch_min_zone_size': 1000,
            'batch_max_zone_size': 4000,
            'batch_initial_overlap_percent': 20.0}


def _spread(n: int) -> list[tuple[float, float]]:
    """Positions with real extent: a 200 m x 80 m survey area."""
    rng = np.random.default_rng(7)
    xy = rng.uniform((0.0, 0.0), (200.0, 80.0), size=(n, 2))
    return [(294900.0 + x, 4588700.0 + y) for x, y in xy]


def _stubbed(monkeypatch) -> dict:
    """Record the zones handed to the copy step instead of copying."""
    seen: dict = {}

    def create(self, output_dir, zones, input_dir, flight_log_path=None):
        seen['zones'] = [list(z) for z in zones]
        return sum(len(set(z)) for z in zones), 0
    monkeypatch.setattr(batch_directory.BatchDirectory,
                        '_BatchDirectory__create_batch_folders', create)
    monkeypatch.setattr(batch_directory.BatchDirectory,
                        '_BatchDirectory__plot_results',
                        lambda *a, **k: None)
    return seen


def test_the_threshold_is_a_parameter_defaulting_to_4000():
    module = batch_directory.BatchDirectory(logging.getLogger(LOGGER_NAME))
    param = module.get_parameters()['batch_single_zone_below']
    assert param.cli_long == 'b_single_zone_below'
    assert param.get_default_value() == 4000
    assert batch_directory.MIN_EXPECTED_IMAGES == 100


def test_672_spread_images_form_one_zone_with_every_image(tmp_path,
                                                         monkeypatch, caplog):
    caplog.set_level(logging.DEBUG, logger=LOGGER_NAME)
    result, batched = _run(tmp_path, monkeypatch, _spread(672),
                           extra=DEFAULTS)
    assert result['Success'] is True
    zones = _zones(batched)
    assert [z.name for z in zones] == ['zone_1']
    images = _images(zones[0])
    assert len(images) == 672
    assert len({p.name for p in images}) == 672
    assert list(zones[0].glob('flight_log_*_UTM.txt'))
    assert result['Total Images in Batches'] == 672
    infos = [r.getMessage() for r in _records(caplog, logging.INFO)
             if 'one zone holds them all' in r.getMessage()]
    assert len(infos) == 1
    assert not _records(caplog, logging.WARNING)


def test_3999_images_form_one_zone(tmp_path, monkeypatch):
    seen = _stubbed(monkeypatch)
    result, _batched = _run(tmp_path, monkeypatch, _spread(3999),
                            extra=DEFAULTS, files=False)
    assert result['Success'] is True
    assert len(seen['zones']) == 1
    assert len(seen['zones'][0]) == 3999
    assert len(set(seen['zones'][0])) == 3999


def test_4000_images_take_the_zoning_path(tmp_path, monkeypatch, caplog):
    caplog.set_level(logging.DEBUG, logger=LOGGER_NAME)
    seen = _stubbed(monkeypatch)
    result, _batched = _run(tmp_path, monkeypatch, _spread(4000),
                            extra=DEFAULTS, files=False)
    assert result['Success'] is True
    zones = seen['zones']
    assert len(zones) >= 2
    assert sum(len(zone) for zone in zones) > 4000, \
        'zoning adds overlap copies between zones'
    assert not any('one zone holds them all' in r.getMessage()
                   for r in caplog.records)


def test_12_images_form_one_zone_with_a_warning(tmp_path, monkeypatch,
                                                caplog):
    caplog.set_level(logging.DEBUG, logger=LOGGER_NAME)
    result, batched = _run(tmp_path, monkeypatch, _spread(12),
                           extra=DEFAULTS)
    assert result['Success'] is True
    zones = _zones(batched)
    assert [z.name for z in zones] == ['zone_1']
    assert len(_images(zones[0])) == 12
    warnings = [r.getMessage() for r in _records(caplog, logging.WARNING)]
    assert len(warnings) == 1, warnings
    assert 'below the 100' in warnings[0]
