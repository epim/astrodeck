"""A5 (P4-T1 ruling B + fix round, amended spec §3-A5): NativeGuider persists/
loads the PPEC model window beside the calibration — ``{"dumped_at": <epoch s>,
"window": [[t, m, v, c], ...]}`` — restores it through the engine's
retain-or-reset downtime gate, and clear_calibration removes BOTH files.

Stub tests cover the persistence-file mechanics with a fake engine; the
real-wheel test (review test-blind-spot #2) drives REAL PPEC frames through the
actual wheel: engine -> dump -> file -> restore -> engine, on both sides of the
gate. (The Rust dump/restore/gate math itself is covered by astro-guide
goldens; the restore->prediction quality e2e is the unattended-night gate.)"""
import json
import math
import time

import pytest

import astrodeck.config as configmod
from astrodeck.guide.native import NativeGuider
from astrodeck.providers import NATIVE_AVAILABLE


class _FakeEngine:
    def __init__(self, window, restore_result=True):
        self._window = window
        self._restore_result = restore_result
        self.restored = None

    def dump_gp_window(self):
        return self._window

    def restore_gp_window(self, points, downtime_s):
        self.restored = (points, downtime_s)
        return self._restore_result


def _guider(tmp_path, monkeypatch, window, restore_result=True):
    monkeypatch.setattr(configmod, "CONFIG_DIR", tmp_path)
    g = NativeGuider.__new__(NativeGuider)  # bypass __init__ (no devices needed)
    g.profile_id = "prof1"
    g._engine = _FakeEngine(window, restore_result)
    return g


def test_persist_then_load_roundtrip(tmp_path, monkeypatch):
    window = [[0.0, 0.1, 1.0, 0.0], [5.0, 0.2, 1.0, -0.05]]
    g = _guider(tmp_path, monkeypatch, window)
    before = time.time()
    g._persist_gp_window()
    p = tmp_path / "guider" / "prof1-gp.json"
    assert p.exists()
    saved = json.loads(p.read_text())
    assert saved["window"] == window
    assert before <= saved["dumped_at"] <= time.time()
    loaded = g._load_gp_window()
    assert loaded is not None
    dumped_at, points = loaded
    assert dumped_at == saved["dumped_at"]
    assert points == [(0.0, 0.1, 1.0, 0.0), (5.0, 0.2, 1.0, -0.05)]


def test_restore_passes_downtime_not_a_percentage(tmp_path, monkeypatch):
    # Fix round: the threshold lives in the Rust engine
    # (GpParams::retain_max_pct_period); Python passes ONLY the measured
    # downtime (now - dumped_at), which for an immediate restore is ~0 s —
    # not the old 40.0-percent constant.
    window = [[0.0, 0.1, 1.0, 0.0], [5.0, 0.2, 1.0, -0.05]]
    g = _guider(tmp_path, monkeypatch, window)
    g._persist_gp_window()
    g._restore_gp_window()
    assert g._engine.restored is not None
    points, downtime_s = g._engine.restored
    assert 0.0 <= downtime_s < 30.0, "downtime is wall seconds since the dump"
    assert points[0] == (0.0, 0.1, 1.0, 0.0)


def test_restore_gate_rejection_is_nonfatal(tmp_path, monkeypatch):
    # Engine returns False (downtime outside the retention window): the
    # restore path logs the fresh-start line and never raises.
    window = [[0.0, 0.1, 1.0, 0.0], [5.0, 0.2, 1.0, -0.05]]
    g = _guider(tmp_path, monkeypatch, window, restore_result=False)
    g._persist_gp_window()
    g._restore_gp_window()  # must not raise
    assert g._engine.restored is not None


def test_untrained_or_empty_window_not_persisted(tmp_path, monkeypatch):
    g = _guider(tmp_path, monkeypatch, [[0.0, 0.0, 0.0, 0.0]])  # 1 point (<2)
    g._persist_gp_window()
    assert not (tmp_path / "guider" / "prof1-gp.json").exists()


def test_corrupt_gp_file_is_ignored(tmp_path, monkeypatch):
    g = _guider(tmp_path, monkeypatch, [])
    d = tmp_path / "guider"
    d.mkdir(parents=True, exist_ok=True)
    (d / "prof1-gp.json").write_text("{not json", encoding="utf-8")
    assert g._load_gp_window() is None  # logged + fresh model, never raises
    # Legacy/foreign shapes (bare array — the pre-fix-round format — or a
    # dict without "window") are equally ignored.
    (d / "prof1-gp.json").write_text(
        json.dumps([[0.0, 0.1, 1.0, 0.0]]), encoding="utf-8")
    assert g._load_gp_window() is None
    (d / "prof1-gp.json").write_text(json.dumps({"dumped_at": 1.0}),
                                     encoding="utf-8")
    assert g._load_gp_window() is None


def test_clear_calibration_removes_both_files(tmp_path, monkeypatch):
    g = _guider(tmp_path, monkeypatch,
                [[0.0, 0.1, 1.0, 0.0], [5.0, 0.2, 1.0, 0.0]])
    d = tmp_path / "guider"
    d.mkdir(parents=True, exist_ok=True)
    (d / "prof1.json").write_text("{}", encoding="utf-8")
    g._persist_gp_window()
    assert (d / "prof1-gp.json").exists()
    assert g.clear_calibration() is True
    assert not (d / "prof1.json").exists()
    assert not (d / "prof1-gp.json").exists()


# --- the id is interpolated into a path (#24 rider) -------------------------
#
# Until #24 these five sites read `CONFIG_DIR/guider/{self.profile_id}.json`
# with a literal f-string, and the id was a hardcoded None or the sim's "sim",
# so nothing hostile could ever reach them. Real profile ids now flow in. They
# are uuid4-derived (profiles.py:76) and therefore safe TODAY — the point of
# routing them through the same `safe_id_path` the profile store itself uses is
# that they stay safe when the next feature lets someone name a profile.


@pytest.mark.parametrize("hostile", ["../victim", "..\\victim", "sub/victim"])
def test_a_profile_id_can_never_address_a_file_outside_the_guider_dir(
        tmp_path, monkeypatch, hostile):
    """`clear_calibration` UNLINKS what the id resolves to. A decoy one level up
    is the concrete stake: `../victim` deleted `CONFIG_DIR/victim.json`."""
    g = _guider(tmp_path, monkeypatch,
                [[0.0, 0.1, 1.0, 0.0], [5.0, 0.2, 1.0, 0.0]])
    g.profile_id = hostile
    victim = tmp_path / "victim.json"
    victim.write_text("{}", encoding="utf-8")

    g._persist_gp_window()
    assert g.clear_calibration() is False
    assert victim.exists(), "a profile id must not address a file it does not own"
    assert g._load_gp_window() is None
    assert g._load_persisted_calibration() is None
    # ...and nothing was written under the id either, on any platform: a
    # separator the RUNNING os does not recognise is a literal filename here,
    # which is containment but not the refusal the guard promises.
    assert not list(tmp_path.rglob("*victim*.json"))[1:]


def test_an_ordinary_profile_id_still_round_trips(tmp_path, monkeypatch):
    """The guard must not cost the normal case. uuid4 ids have hyphens in them
    and hyphens are the one thing the `-gp.json` suffix also uses."""
    g = _guider(tmp_path, monkeypatch,
                [[0.0, 0.1, 1.0, 0.0], [5.0, 0.2, 1.0, 0.0]])
    g.profile_id = "7f3a1c2e-9b40-4d51-8a6f-2c0d5e7b1a94"
    g._persist_gp_window()
    assert (tmp_path / "guider" /
            "7f3a1c2e-9b40-4d51-8a6f-2c0d5e7b1a94-gp.json").exists()
    assert g._load_gp_window() is not None
    assert g.clear_calibration() is True


# --- real-wheel round-trip (fix round, review test-blind-spot #2) -----------

def _star_frame(cx, cy, w=64, h=64, amp=4000.0, sg=1.6, bg=100):
    import numpy as np
    yy, xx = np.mgrid[0:h, 0:w]
    g = amp * np.exp(-(((xx - cx) ** 2 + (yy - cy) ** 2) / (2 * sg * sg)))
    return np.clip(bg + g, 0, 65535).astype(np.uint16)


_IDENT_CAL = {"x_rate": 0.01, "y_rate": 0.01, "x_angle": 0.0,
              "y_angle": math.pi / 2, "y_angle_error": 0.0,
              "declination": 0.0, "pier_side": "west",
              "ra_parity": "even", "dec_parity": "even",
              "rotator_angle": 0.0, "binning": 1, "is_valid": True}


def _real_guider(tmp_path, engine):
    g = NativeGuider.__new__(NativeGuider)
    g.profile_id = "prof1"
    g._engine = engine
    return g


@pytest.mark.skipif(not NATIVE_AVAILABLE, reason="native wheel absent")
def test_real_wheel_roundtrip_engine_dump_file_restore_engine(
        tmp_path, monkeypatch):
    """engine -> dump -> file -> restore -> engine through the REAL wheel —
    the exact path the fake-engine stubs cannot see (the A-T5 review's central
    finding hid there: a real dump previously ended with the pending
    ``(0,0,0,c)`` row, making the retention decision inert)."""
    import astrodeck_native as native
    monkeypatch.setattr(configmod, "CONFIG_DIR", tmp_path)

    eng = native.GuideEngine(
        {"ra_algorithm": "ppec", "image_scale_arcsec": 2.0})
    eng.load_calibration(dict(_IDENT_CAL))
    eng.begin_guiding()
    t = 0.0
    eng.process(_star_frame(32.0, 32.0), t, 5.0)  # establishes lock
    for i in range(1, 13):  # 12 accepted guide frames -> 12 real GP points
        t += 5.0
        eng.process(_star_frame(32.0 + 0.3 * math.sin(i / 3.0), 32.0), t, 5.0)

    window = eng.dump_gp_window()
    assert len(window) >= 2, "real frames trained a real window"
    ts = [row[0] for row in window]
    assert ts == sorted(ts) and ts[-1] > 0.0, (
        "monotone timestamps ending on a real measurement -> no trailing "
        "pending (0,0,0,c) row in a REAL dump")

    _real_guider(tmp_path, eng)._persist_gp_window()
    saved = json.loads((tmp_path / "guider" / "prof1-gp.json").read_text())
    assert saved["window"] == window and saved["dumped_at"] > 0

    # Within the gate: an immediate restore (downtime ~0 s << 40% of 200 s)
    # brings the ENTIRE window back — the fresh engine's dump equals the file.
    eng2 = native.GuideEngine(
        {"ra_algorithm": "ppec", "image_scale_arcsec": 2.0})
    _real_guider(tmp_path, eng2)._restore_gp_window()
    assert eng2.dump_gp_window() == window, (
        "engine -> dump -> file -> restore -> engine round-trips exactly")

    # Beyond the gate: age the file 1000 s (> 80 s threshold) -> fresh model.
    saved["dumped_at"] -= 1000.0
    (tmp_path / "guider" / "prof1-gp.json").write_text(json.dumps(saved),
                                                       encoding="utf-8")
    eng3 = native.GuideEngine(
        {"ra_algorithm": "ppec", "image_scale_arcsec": 2.0})
    _real_guider(tmp_path, eng3)._restore_gp_window()
    assert eng3.dump_gp_window() == [], (
        "past-gate restore leaves a fresh (untrained -> empty-dump) model")
