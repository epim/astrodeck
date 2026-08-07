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


# ------------------------------------------- the cross-session case, which is
# the shape the real rig is actually in

def test_a_guider_is_built_when_the_camera_and_mount_are_in_different_sessions(
        monkeypatch):
    """THE ACTUAL ROOT CAUSE on the rig. Every session accessor looks only at
    its OWN devices, and native drivers do not share one session: the ZWO ASI
    guide camera opens a ``zwo-asi`` session while the AM5 opens ``zwo-am5``
    (serial, COM3). Measured session contents on the rig were exactly::

        ('zwo-asi', None, None)   -> ['guide_camera']
        ('zwo-am5', 'COM3', None) -> []
        ('player-one', None, None)-> ['camera']
        ('zwo-usb', None, None)   -> ['focuser', 'rotator']

    So the guide camera's session found no telescope and correctly returned
    None — and no `guider` row would have helped, because no session had both.
    The orchestrator holds both devices and now builds the guider itself."""
    built = {}

    def fake_build(gcam, tel, *, shares_the_imaging_sensor=False):
        built["args"] = (gcam, tel, shares_the_imaging_sensor)
        return "GUIDER"
    import astrodeck.guide.native as gnative
    monkeypatch.setattr(gnative, "build_native_guider", fake_build)

    gcam_spec = _spec(backend="zwo-asi")
    resolved = {"guide_camera": gcam_spec, "telescope": _spec(backend="zwo-am5")}
    sessions = {_normalize(gcam_spec): _Session(None)}   # session declines
    rig = {"guide_camera": "GCAM", "telescope": "MOUNT"}

    assert _pick_guider(resolved, sessions, rig) == "GUIDER"
    assert built["args"] == ("GCAM", "MOUNT", False), built


def test_the_rig_fallback_never_reaches_for_the_imaging_camera(monkeypatch):
    """plan.guide defaults True, so a guider built on the imaging camera would
    have the first target try to guide through the sensor it is exposing with."""
    import astrodeck.guide.native as gnative
    monkeypatch.setattr(gnative, "build_native_guider",
                        lambda *a, **k: pytest.fail("must not build"))
    cam = _spec(backend="player-one")
    resolved = {"camera": cam, "telescope": _spec(backend="zwo-am5")}
    sessions = {_normalize(cam): _Session(None)}
    rig = {"camera": "IMAGING", "telescope": "MOUNT"}   # no guide_camera
    assert _pick_guider(resolved, sessions, rig) is None


def test_a_session_that_does_supply_a_guider_is_not_second_guessed(monkeypatch):
    """The sim rig and NINA both hand out their own guider from one session.
    The rig fallback must stay a LAST resort, or it would quietly replace them."""
    import astrodeck.guide.native as gnative
    monkeypatch.setattr(gnative, "build_native_guider",
                        lambda *a, **k: pytest.fail("must not build"))
    own = object()
    gcam = _spec(backend="sim")
    sessions = {_normalize(gcam): _Session(own)}
    resolved = {"guide_camera": gcam, "telescope": _spec(backend="sim")}
    rig = {"guide_camera": "GCAM", "telescope": "MOUNT"}
    assert _pick_guider(resolved, sessions, rig) is own
