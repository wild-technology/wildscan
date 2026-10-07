"""align_inputs.json - per-zone alignment-input fingerprint. Content
identity, material-change diffs, nav-aware resume."""
import dataclasses
import hashlib
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules import calibration_sidecars, camera_registry
from modules.align_fingerprint import (
    FINGERPRINT_NAME, build_fingerprint, calibration_sidecar_hashes,
    diff_fingerprints, matches_current, read_fingerprint, write_fingerprint)


def _mk(tmp_path, name, content):
    p = tmp_path / name
    p.write_text(content, encoding="utf-8")
    return str(p)


def _inputs(tmp_path, nav="a;b;c\n1;2;3\n", settings="<x/>"):
    nav_p = _mk(tmp_path, "flight_log_19T_UTM.txt", nav)
    flp = _mk(tmp_path, "FlightLogParams.xml", "<utm/>")
    ap = _mk(tmp_path, "AlignmentParams.xml", settings)
    return nav_p, flp, ap


def test_roundtrip_and_material_identity(tmp_path):
    nav, flp, ap = _inputs(tmp_path)
    fp = build_fingerprint(nav, flp, ap, 50)
    out = tmp_path / "zone_1"
    out.mkdir()
    write_fingerprint(str(out), fp)
    back = read_fingerprint(str(out))
    assert back["schema"] == 1
    assert back["flight_log"]["sha256"] == fp["flight_log"]["sha256"]
    # identical inputs -> no material diffs, resume matches
    fp2 = build_fingerprint(nav, flp, ap, 50)
    assert diff_fingerprints(back, fp2) == []
    assert matches_current(str(out), fp2)


def test_nav_content_change_is_material_and_blocks_resume(tmp_path):
    nav, flp, ap = _inputs(tmp_path)
    out = tmp_path / "zone_1"
    out.mkdir()
    write_fingerprint(str(out), build_fingerprint(nav, flp, ap, 50))
    # edit the nav IN PLACE (nav edited after alignment must be detected)
    with open(nav, "a", encoding="utf-8") as fh:
        fh.write("4;5;6\n")
    fp2 = build_fingerprint(nav, flp, ap, 50)
    changes = diff_fingerprints(read_fingerprint(str(out)), fp2)
    assert any("navigation flight log" in c for c in changes)
    assert not matches_current(str(out), fp2)


def test_settings_change_is_material(tmp_path):
    nav, flp, ap = _inputs(tmp_path)
    old = build_fingerprint(nav, flp, ap, 50)
    ap2 = _mk(tmp_path, "AlignmentParams_variant.xml", "<x overlap='Low'/>")
    new = build_fingerprint(nav, flp, ap2, 50)
    changes = diff_fingerprints(old, new)
    assert any("alignment settings" in c for c in changes)


def test_renamed_identical_nav_is_not_material(tmp_path):
    nav, flp, ap = _inputs(tmp_path)
    old = build_fingerprint(nav, flp, ap, 50)
    nav2 = _mk(tmp_path, "flight_log_copy_19T_UTM.txt", "a;b;c\n1;2;3\n")
    new = build_fingerprint(nav2, flp, ap, 50)
    assert diff_fingerprints(old, new) == []


def test_frame_change_is_called_out(tmp_path):
    flp = _mk(tmp_path, "flp.xml", "<t/>")
    ap = _mk(tmp_path, "ap.xml", "<x/>")
    tagged = _mk(tmp_path, "flight_log_53N_UTM.txt", "n;x;y;a\n")
    old = build_fingerprint(None, flp, ap, 50)
    new = build_fingerprint(tagged, flp, ap, 50)
    assert old["frame"] is None and new["frame"] == "utm"
    assert any("FRAME changed" in c for c in diff_fingerprints(old, new))


def test_min_component_size_change_is_material(tmp_path):
    nav, flp, ap = _inputs(tmp_path)
    old = build_fingerprint(nav, flp, ap, 50)
    new = build_fingerprint(nav, flp, ap, 10)
    assert any("min_component_size" in c for c in diff_fingerprints(old, new))


def test_no_previous_fingerprint_means_no_diffs(tmp_path):
    nav, flp, ap = _inputs(tmp_path)
    fp = build_fingerprint(nav, flp, ap, 50)
    assert diff_fingerprints(None, fp) == []
    assert not matches_current(str(tmp_path), fp)  # nothing written yet


def test_write_is_atomic_shaped(tmp_path):
    nav, flp, ap = _inputs(tmp_path)
    out = tmp_path / "z"
    out.mkdir()
    p = write_fingerprint(str(out), build_fingerprint(nav, flp, ap, 50))
    assert os.path.basename(p) == FINGERPRINT_NAME
    assert not os.path.exists(p + ".tmp")
    json.load(open(p, encoding="utf-8"))


# ---------------------------------------- calibration sidecar content

CALIBRATION = {"ilx_left": "prior", "ilx_right": "groups"}
FOCALS = {"ilx_right": 24.0}


def test_fingerprint_records_the_sidecar_text_per_sidecar_camera(tmp_path):
    nav, flp, ap = _inputs(tmp_path)
    fp = build_fingerprint(nav, flp, ap, 50, calibration=CALIBRATION,
                           starting_focals=FOCALS)
    hashes = fp["calibration_sidecar_sha256"]
    assert set(hashes) == {"ilx_left", "ilx_right"}
    assert hashes == calibration_sidecar_hashes(CALIBRATION, FOCALS)
    assert all(len(h) == 64 for h in hashes.values())
    # An off camera writes no sidecar and gets no entry.
    off = build_fingerprint(nav, flp, ap, 50,
                            calibration={"ilx_left": "off", "ilx_right": "groups"},
                            starting_focals=FOCALS)
    assert set(off["calibration_sidecar_sha256"]) == {"ilx_right"}
    # Without calibration delivery the key is absent, as the others are.
    assert "calibration_sidecar_sha256" not in build_fingerprint(nav, flp, ap, 50)


def test_a_cameras_json_prior_change_is_material(tmp_path, monkeypatch):
    """Same mode, same focal, different prior: only the sidecar text
    changes, and the fingerprint must say so."""
    nav, flp, ap = _inputs(tmp_path)
    old = build_fingerprint(nav, flp, ap, 50, calibration=CALIBRATION,
                            starting_focals=FOCALS)
    assert diff_fingerprints(old, build_fingerprint(
        nav, flp, ap, 50, calibration=CALIBRATION, starting_focals=FOCALS)) == []
    left = camera_registry.CAMERAS["ilx_left"]
    edited = dict(camera_registry.CAMERAS)
    edited["ilx_left"] = dataclasses.replace(
        left, principal_point_u=left.principal_point_u + 0.01)
    monkeypatch.setattr(camera_registry, "CAMERAS", edited)
    new = build_fingerprint(nav, flp, ap, 50, calibration=CALIBRATION,
                            starting_focals=FOCALS)
    changes = diff_fingerprints(old, new)
    assert len(changes) == 1, changes
    assert changes[0].startswith("calibration sidecar text of ilx_left CHANGED: ")
    assert "cameras.json" in changes[0]
    assert old["calibration_sidecar_sha256"]["ilx_left"] in changes[0]
    assert new["calibration_sidecar_sha256"]["ilx_left"] in changes[0]
    assert "ilx_right" not in changes[0]


def test_a_fingerprint_written_before_sidecar_hashes_is_reported_as_changed(tmp_path):
    """An align_inputs.json from before this key existed must read as a
    different run, not crash and not pass as identical."""
    nav, flp, ap = _inputs(tmp_path)
    new = build_fingerprint(nav, flp, ap, 50, calibration=CALIBRATION,
                            starting_focals=FOCALS)
    old = dict(new)
    del old["calibration_sidecar_sha256"]
    out = tmp_path / "zone_1"
    out.mkdir()
    write_fingerprint(str(out), old)
    changes = diff_fingerprints(read_fingerprint(str(out)), new)
    assert [c.split(":")[0] for c in changes] == [
        "calibration sidecar text of ilx_left CHANGED",
        "calibration sidecar text of ilx_right CHANGED"]
    assert all("not recorded ->" in c for c in changes)
    assert not matches_current(str(out), new)
    # The other way round (a zone aligned without sidecars, then with) is
    # a change of the delivery and of every sidecar.
    assert any("calibration delivery changed" in c
               for c in diff_fingerprints(build_fingerprint(nav, flp, ap, 50), new))


def test_sidecar_hashes_follow_the_sidecar_writer():
    expected = hashlib.sha256(calibration_sidecars.sidecar_xmp(
        camera_registry.CAMERAS["ilx_right"], "groups", 24.0).encode("utf-8")
    ).hexdigest()
    assert calibration_sidecar_hashes({"ilx_right": "groups"}, FOCALS) == {
        "ilx_right": expected}
    with pytest.raises(ValueError, match="starting 35 mm"):
        calibration_sidecar_hashes({"ilx_right": "groups"}, None)
