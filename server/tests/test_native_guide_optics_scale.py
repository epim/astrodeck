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
