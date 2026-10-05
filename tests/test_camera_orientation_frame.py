"""The RealityScan orientation frame: pitch 0 = nadir, 90 = horizontal.

This conversion decides which way every camera is claimed to point in the
flight log. ``modules.flight_logs.realityscan_orientation`` is the one
implementation; these tests pin it.

The pitch convention is RealityScan's, not a house choice:
`-renderMeshFromCustomPositionYPR` documents a camera at `(0,0,150)` with
`yaw=pitch=roll=0` looking **down**, so **pitch 0 is nadir** on a scale where
90 is horizontal. [OFFICIAL: appbasics/allcommands; docs/rs-reference/13 6.4]

    yaw   = (heading + declination + yaw_offset) mod 360
    pitch = 90 + (vehicle_pitch - mount_down_tilt)
    roll  = vehicle_roll

Read the pitch as: start horizontal (90), tilt the camera down by its mount
angle, then add whatever the vehicle itself is doing.
"""
from __future__ import annotations

import os
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

from modules.flight_logs import realityscan_orientation  # noqa: E402


def convert(heading=0.0, vehicle_pitch=0.0, roll=0.0, tilt=0.0, decl=0.0,
            yaw_offset=0.0):
    return realityscan_orientation(heading, vehicle_pitch, roll,
                                   down_tilt_deg=tilt, declination_deg=decl,
                                   yaw_offset_deg=yaw_offset)


# --------------------------------------------------------------------------
# the convention itself
# --------------------------------------------------------------------------

@pytest.mark.parametrize('tilt,expected,what', [
    (0.0, 90.0, 'a camera looking straight along the vehicle axis is HORIZONTAL'),
    (90.0, 0.0, 'a camera tilted 90 deg down is NADIR - pitch 0'),
    (10.0, 80.0, '10 deg down from horizontal'),
    (45.0, 45.0, 'half way down'),
    (70.0, 20.0, 'steeply down'),
])
def test_mount_tilt_maps_onto_the_nadir_scale(tilt, expected, what):
    _yaw, pitch, _roll = convert(vehicle_pitch=0.0, tilt=tilt)
    assert pitch == pytest.approx(expected), what


def test_pitch_is_referenced_to_nadir_not_to_horizontal():
    """If this were measured FROM horizontal, a nadir camera would come out
    at 90 rather than 0."""
    _y, nadir, _r = convert(tilt=90.0)
    _y, horizontal, _r = convert(tilt=0.0)
    assert nadir == pytest.approx(0.0)
    assert horizontal == pytest.approx(90.0)
    assert nadir < horizontal, 'the scale is inverted'


def test_vehicle_attitude_composes_with_the_mount():
    """The mount is an offset FROM the vehicle's own pitch, not a
    replacement for it."""
    # Vehicle nose-up 5 deg lifts the camera 5 deg on the nadir scale.
    assert convert(vehicle_pitch=5.0, tilt=10.0)[1] == pytest.approx(85.0)
    # Vehicle nose-down 5 deg pushes it toward nadir.
    assert convert(vehicle_pitch=-5.0, tilt=10.0)[1] == pytest.approx(75.0)


def test_a_nadir_mount_passes_the_vehicle_pitch_through():
    for vehicle_pitch in (-7.5, 0.0, 3.25):
        assert convert(vehicle_pitch=vehicle_pitch, tilt=90.0)[1] == \
            pytest.approx(vehicle_pitch)


def test_yaw_is_true_heading_and_wraps():
    assert convert(heading=350.0, decl=20.0)[0] == pytest.approx(10.0)
    assert convert(heading=10.0, decl=-20.0)[0] == pytest.approx(350.0)
    assert 0.0 <= convert(heading=359.9, decl=0.5)[0] < 360.0


def test_yaw_offset_turns_the_image_top_relative_to_the_heading():
    assert convert(heading=90.0, yaw_offset=90.0)[0] == pytest.approx(180.0)
    assert convert(heading=10.0, yaw_offset=-30.0)[0] == pytest.approx(340.0)
    assert convert(heading=350.0, decl=5.0, yaw_offset=15.0)[0] == \
        pytest.approx(10.0)


def test_roll_passes_through_untouched():
    assert convert(roll=-3.25)[2] == pytest.approx(-3.25)


# --------------------------------------------------------------------------
# absent inputs must produce no prior, never a zero
# --------------------------------------------------------------------------

def test_missing_vehicle_pitch_yields_no_pitch_prior():
    assert convert(vehicle_pitch=None, tilt=90.0)[1] is None


def test_missing_mount_tilt_yields_no_pitch_prior():
    """Whatever the caller decided about mounts, a None tilt must not
    become 0."""
    assert convert(vehicle_pitch=0.0, tilt=None)[1] is None


def test_missing_heading_yields_no_yaw():
    assert convert(heading=None, tilt=90.0)[0] is None


def test_missing_roll_yields_no_roll():
    assert convert(roll=None)[2] is None


@pytest.mark.parametrize('bad', [float('nan'), float('inf'), float('-inf')])
def test_non_finite_inputs_yield_no_prior(bad):
    assert convert(heading=bad, vehicle_pitch=bad, roll=bad, tilt=90.0) == \
        (None, None, None)


def test_the_angles_are_independent():
    """One missing angle does not drop the others."""
    yaw, pitch, roll = convert(heading=None, vehicle_pitch=1.0, roll=2.0,
                               tilt=90.0)
    assert (yaw, pitch, roll) == (None, pytest.approx(1.0), pytest.approx(2.0))


def test_declination_and_yaw_offset_must_be_numbers():
    with pytest.raises(ValueError, match='declination'):
        convert(decl=None)
    with pytest.raises(ValueError, match='yaw offset'):
        convert(yaw_offset=float('nan'))
