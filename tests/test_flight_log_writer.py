"""The 13-column flight-log writer (modules/flight_logs.py).

Pins the exact header, separator, number format, empty cells for missing
values, file naming and the refusals, and checks the header against the
import format the repository ships (flightlogs.xml, format
{B438A617-2434-5A24-C1B7-58980F28345A}) and against FlightLogParams.xml.

Offline and standard-library only.

Run:  py -3.13 -m pytest tests/test_flight_log_writer.py
"""
from __future__ import annotations

import os
import re
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
sys.path.insert(0, REPO_ROOT)

from modules.flight_logs import (FLIGHT_LOG_FORMAT_ID,  # noqa: E402
                                 FLIGHT_LOG_HEADER, FlightLogRow,
                                 find_flight_log, flight_log_name,
                                 format_flight_log_row, require_utm_zone,
                                 write_flight_log)

HEADER = ('filename;X (East);Y (North);Alt;X Accuracy;Y Accuracy;Alt Accuracy;'
          'Yaw;Pitch;Roll;Yaw Accuracy;Pitch Accuracy;Roll Accuracy')


def _row(name='Cam1_20260820_192542.42.card.JPG', **values):
    fields = dict(x=301234.5, y=4587654.25, alt=-2.0, x_accuracy=10.0,
                  y_accuracy=10.0, alt_accuracy=1.0, yaw=123.456789,
                  pitch=1.5, roll=-0.25, yaw_accuracy=15.0,
                  pitch_accuracy=15.0, roll_accuracy=15.0)
    fields.update(values)
    return FlightLogRow(name, **fields)


def test_header_is_the_pipeline_header():
    assert FLIGHT_LOG_HEADER == HEADER
    assert len(HEADER.split(';')) == 13


def test_header_matches_the_shipped_import_format():
    """flightlogs.xml maps the same 13 columns, image name first."""
    text = open(os.path.join(REPO_ROOT, 'flightlogs.xml'), encoding='utf-8').read()
    assert FLIGHT_LOG_FORMAT_ID in text
    indices = dict(re.findall(r'<(\w+) index="(\d+)"', text))
    order = sorted(indices, key=lambda k: int(indices[k]))
    assert order == ['Image', 'X', 'Y', 'Altitude', 'XAccuracy', 'YAccuracy',
                     'AltitudeAccuracy', 'Yaw', 'Pitch', 'Roll',
                     'YawAccuracy', 'PitchAccuracy', 'RollAccuracy']
    params = open(os.path.join(REPO_ROOT, 'modules', 'realityscan_interface',
                               'RS_CLI', 'Metadata', 'FlightLogParams.xml'),
                  encoding='utf-8').read()
    assert f'value="{FLIGHT_LOG_FORMAT_ID}"' in params


def test_row_format_six_decimals_and_empty_cells():
    assert format_flight_log_row(_row()) == (
        'Cam1_20260820_192542.42.card.JPG;301234.500000;4587654.250000;'
        '-2.000000;10.000000;10.000000;1.000000;123.456789;1.500000;'
        '-0.250000;15.000000;15.000000;15.000000')
    assert format_flight_log_row(_row(yaw=None, pitch=None, roll=None)) == (
        'Cam1_20260820_192542.42.card.JPG;301234.500000;4587654.250000;'
        '-2.000000;10.000000;10.000000;1.000000;;;;15.000000;15.000000;15.000000')


def test_write_flight_log_exact_file(tmp_path):
    path = tmp_path / 'raw_images' / flight_log_name(19, 'T')
    rows = [_row('Cam1_a.card.JPG', yaw=None),
            _row('Cam2_a.card.JPG', x=1, y=2, alt=0)]
    assert write_flight_log(str(path), rows) == 2
    assert path.read_bytes() == (
        HEADER + '\n'
        'Cam1_a.card.JPG;301234.500000;4587654.250000;-2.000000;10.000000;'
        '10.000000;1.000000;;1.500000;-0.250000;15.000000;15.000000;15.000000\n'
        'Cam2_a.card.JPG;1.000000;2.000000;0.000000;10.000000;10.000000;'
        '1.000000;123.456789;1.500000;-0.250000;15.000000;15.000000;15.000000\n'
    ).encode('utf-8')
    assert find_flight_log(str(path.parent)) == str(path)
    assert require_utm_zone(str(path)) == (19, 'T')


def test_an_empty_log_is_just_the_header(tmp_path):
    path = tmp_path / 'flight_log_19T_UTM.txt'
    assert write_flight_log(str(path), []) == 0
    assert path.read_text(encoding='utf-8') == HEADER + '\n'


@pytest.mark.parametrize('zone,band,name', [
    (19, 'T', 'flight_log_19T_UTM.txt'),
    ('7', 'c', 'flight_log_7C_UTM.txt'),
    (60, 'X', 'flight_log_60X_UTM.txt'),
])
def test_flight_log_name_carries_the_zone_tag(zone, band, name):
    assert flight_log_name(zone, band) == name
    assert require_utm_zone(name) == (int(zone), band.upper())


@pytest.mark.parametrize('zone,band', [(0, 'T'), (61, 'T'), (19, 'I'), (19, 'Z')])
def test_flight_log_name_refuses_an_impossible_zone(zone, band):
    with pytest.raises(ValueError, match='invalid UTM zone'):
        flight_log_name(zone, band)


def test_a_duplicate_image_name_is_refused_and_nothing_written(tmp_path):
    path = tmp_path / 'flight_log_19T_UTM.txt'
    with pytest.raises(ValueError, match='appears twice'):
        write_flight_log(str(path), [_row('Cam1_a.jpg'), _row('CAM1_A.JPG')])
    assert not path.exists()


@pytest.mark.parametrize('value', [float('nan'), float('inf'), True, '1.0'])
def test_a_non_number_is_refused(value):
    with pytest.raises(ValueError, match='finite number or None'):
        format_flight_log_row(_row(pitch=value))


@pytest.mark.parametrize('name', ['', '  ', 'a;b.jpg', 'a\n.jpg'])
def test_an_unwritable_image_name_is_refused(name):
    with pytest.raises(ValueError, match='image name'):
        format_flight_log_row(_row(name))
