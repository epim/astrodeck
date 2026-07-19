"""A5 (P4-T1 ruling B): NativeGuider persists/loads the PPEC model window
beside the calibration, and clear_calibration removes BOTH files. File-level
round-trip with a fake engine stub (the Rust dump/restore math is covered by
astro-guide goldens; the real engine restore->prediction e2e is the unattended
night gate)."""
import json

import astrodeck.config as configmod
from astrodeck.guide.native import NativeGuider


class _FakeEngine:
    def __init__(self, window):
        self._window = window
        self.restored = None

    def dump_gp_window(self):
        return self._window

    def restore_gp_window(self, points, retain_pct):
        self.restored = (points, retain_pct)


def _guider(tmp_path, monkeypatch, window):
    monkeypatch.setattr(configmod, "CONFIG_DIR", tmp_path)
    g = NativeGuider.__new__(NativeGuider)  # bypass __init__ (no devices needed)
    g.profile_id = "prof1"
    g._engine = _FakeEngine(window)
    return g


def test_persist_then_load_roundtrip(tmp_path, monkeypatch):
    window = [[0.0, 0.1, 1.0, 0.0], [5.0, 0.2, 1.0, -0.05]]
    g = _guider(tmp_path, monkeypatch, window)
    g._persist_gp_window()
    p = tmp_path / "guider" / "prof1-gp.json"
    assert p.exists()
    assert json.loads(p.read_text()) == window
    loaded = g._load_gp_window()
    assert loaded == [(0.0, 0.1, 1.0, 0.0), (5.0, 0.2, 1.0, -0.05)]


def test_restore_calls_engine_with_retain_pct(tmp_path, monkeypatch):
    window = [[0.0, 0.1, 1.0, 0.0], [5.0, 0.2, 1.0, -0.05]]
    g = _guider(tmp_path, monkeypatch, window)
    g._persist_gp_window()
    g._restore_gp_window()
    assert g._engine.restored is not None
    points, pct = g._engine.restored
    assert pct == 40.0
    assert points[0] == (0.0, 0.1, 1.0, 0.0)


def test_untrained_or_empty_window_not_persisted(tmp_path, monkeypatch):
    g = _guider(tmp_path, monkeypatch, [[0.0, 0.0, 0.0, 0.0]])  # seed-only (<2)
    g._persist_gp_window()
    assert not (tmp_path / "guider" / "prof1-gp.json").exists()


def test_corrupt_gp_file_is_ignored(tmp_path, monkeypatch):
    g = _guider(tmp_path, monkeypatch, [])
    d = tmp_path / "guider"
    d.mkdir(parents=True, exist_ok=True)
    (d / "prof1-gp.json").write_text("{not json", encoding="utf-8")
    assert g._load_gp_window() is None  # logged + fresh model, never raises


def test_clear_calibration_removes_both_files(tmp_path, monkeypatch):
    g = _guider(tmp_path, monkeypatch, [[0.0, 0.1, 1.0, 0.0], [5.0, 0.2, 1.0, 0.0]])
    d = tmp_path / "guider"
    d.mkdir(parents=True, exist_ok=True)
    (d / "prof1.json").write_text("{}", encoding="utf-8")
    g._persist_gp_window()
    assert (d / "prof1-gp.json").exists()
    assert g.clear_calibration() is True
    assert not (d / "prof1.json").exists()
    assert not (d / "prof1-gp.json").exists()
