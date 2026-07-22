"""A4 (P2-T3 review F2): native_guider() computes a real image_scale_arcsec
from Optics.guide_focal_length_mm + the guide camera's pixel_size_um, instead
of the 1.0 default that mis-scales the arcsec badge on a real rig. Needs no
native wheel (NATIVE_AVAILABLE is monkeypatched; NativeGuider constructs in
pure Python)."""
from types import SimpleNamespace

import pytest

import astrodeck.config as configmod
import astrodeck.devices.backends.native_backend as nb
from astrodeck.config import Optics


class _FakeCam:
    pixel_size_um = 4.0


class _FakeTel:
    pass


def _session_with_optics(monkeypatch, guide_fl):
    monkeypatch.setattr(nb, "NATIVE_AVAILABLE", True, raising=False)
    cfg = SimpleNamespace(optics=Optics(focal_length_mm=530.0,
                                        guide_focal_length_mm=guide_fl))
    monkeypatch.setattr(configmod.config_store, "cfg", lambda: cfg)
    # native_guider reads NATIVE_AVAILABLE via `from ...providers import ...`;
    # also patch the source of truth so the import inside native_guider sees True.
    import astrodeck.providers as providers
    monkeypatch.setattr(providers, "NATIVE_AVAILABLE", True, raising=False)
    sess = nb.NativeSession("127.0.0.1")
    sess._devices["guide_camera"] = _FakeCam()
    sess._devices["telescope"] = _FakeTel()
    return sess


def test_guide_scale_computed_from_focal_length(monkeypatch):
    sess = _session_with_optics(monkeypatch, guide_fl=200.0)
    guider = sess.native_guider()
    assert guider is not None
    # 206.265 * 4.0 / 200.0 * 1 (bin 1) = 4.1253 arcsec/px
    assert guider._image_scale == pytest.approx(206.265 * 4.0 / 200.0)


def test_guide_scale_falls_back_to_default_when_unset(monkeypatch):
    sess = _session_with_optics(monkeypatch, guide_fl=None)
    guider = sess.native_guider()
    assert guider is not None
    assert guider._image_scale == 1.0  # the documented default when no guide FL


# --- UX-15: is_arcsec / image_scale on the published GuideStats -------------

def test_backend_flags_scale_known_only_when_focal_length_set(monkeypatch):
    """The native backend marks the scale KNOWN only when a real guide-scope
    focal length feeds a genuine arcsec/px value (UX-15)."""
    known = _session_with_optics(monkeypatch, guide_fl=200.0).native_guider()
    assert known._image_scale_known is True
    # A fresh session with no guide FL: the 1:1 fallback is NOT arcsec.
    unset = _session_with_optics(monkeypatch, guide_fl=None).native_guider()
    assert unset._image_scale_known is False


class _FakeEngine:
    """Minimal engine stub returning pixel-space errors, like the Rust engine."""
    def stats(self):
        return {"guiding": True, "rms_ra": 0.5, "rms_dec": 0.4,
                "rms_total": 0.64, "snr": 30.0, "recent": [(1.0, 0.5, -0.4)]}


def _guider_with_engine(**cfg):
    from astrodeck.guide.native import NativeGuider
    g = NativeGuider(_FakeCam(), _FakeTel(), config=cfg)
    g._engine = _FakeEngine()
    g._active = True
    g._lost = False
    return g


def test_stats_reports_arcsec_when_scale_known():
    g = _guider_with_engine(image_scale_arcsec=2.0, image_scale_known=True)
    st = g.stats()
    assert st.is_arcsec is True
    assert st.image_scale == pytest.approx(2.0)
    # engine pixels are scaled to arcsec by 2.0
    assert st.rms_total == pytest.approx(0.64 * 2.0, abs=0.01)
    assert st.recent[0]["ra"] == pytest.approx(0.5 * 2.0, abs=0.001)


def test_stats_reports_pixels_when_scale_unknown():
    # image_scale_known defaults False → the 1:1 fallback stays PIXELS.
    g = _guider_with_engine(image_scale_arcsec=1.0)
    st = g.stats()
    assert st.is_arcsec is False
    assert st.image_scale == 0.0
    # raw engine pixels pass through unchanged (scale 1.0)
    assert st.rms_total == pytest.approx(0.64, abs=0.01)


# --- UX-23: calibration report -------------------------------------------

class _CalEngine:
    def __init__(self, cal, advisories=()):
        self._cal = cal
        self._adv = list(advisories)

    def dump_calibration(self):
        return self._cal

    def calibration_advisories(self):
        return self._adv


def _guider_with_cal(cal, advisories=()):
    from astrodeck.guide.native import NativeGuider
    g = NativeGuider(_FakeCam(), _FakeTel(), config={})
    g._engine = _CalEngine(cal, advisories)
    return g


def test_calibration_report_normalizes_geometry():
    import math
    cal = {"is_valid": True, "y_angle_error": math.radians(3.0),
           "declination": math.radians(41.0), "pier_side": "east", "binning": 1}
    rep = _guider_with_cal(cal, ["RA rate looks low"]).calibration_report()
    assert rep["is_valid"] is True
    assert rep["ortho_error_deg"] == pytest.approx(3.0, abs=0.01)  # radians → degrees
    assert rep["declination_deg"] == pytest.approx(41.0, abs=0.1)
    assert rep["pier_side"] == "east"
    assert rep["advisories"] == ["RA rate looks low"]
    assert rep["source"] == "native"


def test_calibration_report_hides_unknown_declination():
    # 997.0 rad is the UNKNOWN_DECLINATION sentinel — never shown as a real dec.
    cal = {"is_valid": True, "y_angle_error": 0.0, "declination": 997.0,
           "pier_side": "unknown", "binning": 2}
    assert _guider_with_cal(cal).calibration_report()["declination_deg"] is None


def test_calibration_report_none_without_engine():
    from astrodeck.guide.native import NativeGuider
    g = NativeGuider(_FakeCam(), _FakeTel(), config={})
    assert g.calibration_report() is None


# --- UX-24: native dither honors a settle-timeout override -----------------

async def test_native_dither_honors_settle_timeout_override():
    """A caller settle timeout replaces the (much longer) default wait, so a
    non-settling dither fails fast instead of hanging _SETTLE_TIMEOUT_S."""
    import asyncio
    import pytest as _pytest
    from astrodeck.guide.native import NativeGuider
    from astrodeck.devices.base import DeviceError

    class _NoSettleEngine:
        def dither(self, dx, dy):  # never signals settle
            pass

    g = NativeGuider(_FakeCam(), _FakeTel(), config={})
    g._engine = _NoSettleEngine()
    g._active = True
    # 0.4s override must fire well within this 5s guard; if it were ignored the
    # default timeout would blow past 5s and this raises TimeoutError instead.
    with _pytest.raises(DeviceError):
        await asyncio.wait_for(g.dither(3.0, {"timeout": 0.4}), timeout=5.0)
