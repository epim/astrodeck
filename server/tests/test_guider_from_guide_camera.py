"""A rig with a guide camera and a mount must end up with a guider.

Found 2026-08-06 on the real rig while preparing a first-light guide
calibration. The active profile assigned camera, telescope, focuser,
filterwheel, rotator and **guide_camera**, all native — and ``hub.guider`` was
``None``. Every guiding route 409s on that, so guiding could not be started,
calibrated, or even refused with a message that named the cause.

The reason was ``_pick_guider`` keying strictly on a ``guider`` ROLE. Nothing in
the product tells you that row has to exist, and the rig had everything the
native guider actually needs sitting in the same session: ``native_guider()``
builds itself from that session's ``guide_camera`` (or the imaging camera) plus
its ``telescope``. It was simply never asked.

The fallback is keyed on ``guide_camera`` and NOT on the imaging camera, which
is the part worth pinning: ``plan.guide`` defaults True, so inferring a guider
for every rig would have the first target try to guide through the camera it is
imaging with.
"""
from __future__ import annotations

import pytest

from astrodeck.devices.orchestrator import ConnSpec, _normalize, _pick_guider


class _Session:
    def __init__(self, guider=object()):
        self._guider = guider

    def native_guider(self):
        return self._guider


def _spec(backend: str = "native", **kw) -> ConnSpec:
    kw.pop("name", None)          # ConnSpec has no display name; label is for us
    kw.setdefault("host", "")
    kw.setdefault("port", 0)
    return ConnSpec(backend=backend, dev_type="", dev_num=0, **kw)


def test_a_guide_camera_alone_yields_a_guider():
    """The rig's exact shape: a guide_camera row, no guider row."""
    gcam = _spec(name="ZWO ASI camera")
    sessions = {_normalize(gcam): _Session()}
    resolved = {"camera": _spec(name="imaging"), "telescope": _spec(name="mount"),
                "guide_camera": gcam}
    assert _pick_guider(resolved, sessions) is not None


def test_an_explicit_guider_role_still_wins():
    """The fallback must not shadow an operator who said exactly what they
    wanted — a PHD2 or sim guider assigned deliberately."""
    chosen, other = object(), object()
    guider = _spec(backend="phd2", name="PHD2", host="127.0.0.1", port=4400)
    gcam = _spec(name="ZWO ASI camera")
    sessions = {_normalize(guider): _Session(chosen),
                _normalize(gcam): _Session(other)}
    resolved = {"guider": guider, "guide_camera": gcam,
                "telescope": _spec(name="mount")}
    assert _pick_guider(resolved, sessions) is chosen


def test_no_guide_camera_and_no_guider_role_yields_nothing():
    """The imaging camera must NOT be inferred into a guider. plan.guide
    defaults True, so a guider built on the imaging camera would have the first
    target try to guide through the camera it is exposing with."""
    cam = _spec(name="imaging")
    sessions = {_normalize(cam): _Session()}
    resolved = {"camera": cam, "telescope": _spec(name="mount")}
    assert _pick_guider(resolved, sessions) is None


def test_a_session_that_declines_to_build_one_is_respected():
    """``native_guider()`` returns None when the native engine is unavailable or
    a required device is missing. That is a real answer, not a failure to ask."""
    gcam = _spec(name="ZWO ASI camera")
    sessions = {_normalize(gcam): _Session(None)}
    resolved = {"guide_camera": gcam, "telescope": _spec(name="mount")}
    assert _pick_guider(resolved, sessions) is None


def test_a_guide_camera_whose_session_is_missing_does_not_raise():
    gcam = _spec(name="ZWO ASI camera")
    resolved = {"guide_camera": gcam}
    assert _pick_guider(resolved, {}) is None
