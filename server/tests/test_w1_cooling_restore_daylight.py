"""WP-10 (backlog ruling, owner-approved 2026-09-30): restore_cooling tells
the truth and leaves daylight alone.

(a) #557 -- a daytime restart used to re-cool the camera to its night setpoint
regardless of the clock. On 2026-09-29 a morning deploy commanded a -10 C target
nine hours before the next run, and an operator had to switch the cooler off
by hand. restore_cooling now asks the same daylight question the dawn-warm
path asks (dawn_park.park_threshold_deg + sun_altaz) and, in daylight with
nothing due, leaves the cooler as found.

(b) #143 -- a reconnect logged "cooling restored to -10 C" while the cooler
actually read off. restore_cooling now reads the cooler back before it claims
"restored".

SITE below is a made-up test fixture, not the rig's (see the project's
absolute site-privacy rule): its own numbers, distinct from every other
fixture's fake site in this suite, chosen only so ``sun_altaz`` has something
to compute against.
"""
from __future__ import annotations

import pytest

import astrodeck.hub as hub_module
from astrodeck.config import Site, config_store
from astrodeck.catalog.coords import sun_altaz
from astrodeck.dawn_park import park_threshold_deg
from astrodeck.devices.base import Camera, CameraFrame, DeviceError
from astrodeck.hub import Hub
from astrodeck.sequence import schedule
from astrodeck.sequence.models import ExposureStep, SequencePlan, Target
from astrodeck.sequence.session import Session, session_store

pytestmark = pytest.mark.asyncio

# A made-up site, distinct from the other fake sites this suite already uses
# (_simhub.py's 40N/-74W, test_the_cooler_stays_cold_for_an_armed_resume.py's
# 40N/-105W) so a copy-paste of the wrong constant would stand out in a diff.
SITE = Site(name="w1-fixture", latitude=15.0, longitude=60.0, is_default=False)

#: A fixed reference instant (2027-03-01 12:00 UTC) used only to COMPUTE a
#: guaranteed-day and a guaranteed-night timestamp for SITE below -- never
#: hand-derived (see the project memory note "Plan numbers must be computed").
_REF_NOON = 1_803_902_400.0


def _site_dict() -> dict:
    return {"latitude": SITE.latitude, "longitude": SITE.longitude,
            "is_default": False}


def _threshold() -> float:
    return park_threshold_deg(config_store.cfg())


def _alt(t: float) -> float:
    alt, _az = sun_altaz(SITE.latitude, SITE.longitude, t)
    return alt


def _night_midpoint() -> float:
    """The middle of the current-or-imminent night at SITE, anchored to
    ``_REF_NOON`` -- computed via the same library call the rest of the
    codebase uses for "when is tonight", not guessed."""
    dusk, dawn = schedule.observing_night(_site_dict(), None, _REF_NOON)
    return (dusk + dawn) / 2


DAY_TS = _REF_NOON
NIGHT_TS = _night_midpoint()

# Self-check: fail here, loudly, rather than have an unrelated assertion below
# fail for an opaque reason if the ephemeris ever disagrees with this file's
# assumptions about SITE and these two timestamps.
assert _alt(DAY_TS) >= _threshold(), "DAY_TS is not actually daylight at SITE"
assert _alt(NIGHT_TS) < _threshold(), "NIGHT_TS is not actually dark at SITE"


class _Cam(Camera):
    """A cooled fake camera whose ``get_cooler`` reports exactly what
    ``set_cooler`` was last told -- the honest readback #143 wants asked."""

    def __init__(self, *, mute: bool = False):
        super().__init__("Fake Cooled Cam")
        self.connected = True
        self.can_cool = True
        self._on = False
        self._target: float | None = None
        # A driver that ACCEPTS the call but does not actually do it: the
        # #143 shape (a claim nothing keeps), one layer below "raises".
        self._mute = mute
        self.calls: list[tuple[bool, float | None]] = []

    async def expose(self, *a, **kw) -> CameraFrame:      # pragma: no cover
        raise DeviceError("not used")

    async def abort_exposure(self) -> None:               # pragma: no cover
        return None

    async def connect(self) -> None:                      # pragma: no cover
        self.connected = True

    async def disconnect(self) -> None:                   # pragma: no cover
        self.connected = False

    async def set_cooler(self, on: bool, target_c: float | None = None) -> None:
        self.calls.append((on, target_c))
        if self._mute:
            return
        self._on = bool(on)
        if on:
            self._target = target_c

    async def get_cooler(self) -> dict | None:
        return {"on": self._on, "target_c": self._target}


def _calibration_plan(name: str = "darks") -> SequencePlan:
    return SequencePlan(name=name, targets=[Target(
        name="dark-frames", ra_hours=0.0, dec_deg=0.0, calibration=True,
        steps=[ExposureStep(filter="L", exposure_s=1.0, count=1)])])


def _arm_calibration_session(name: str = "darks") -> Session:
    """An auto-resume-armed dormant session whose only target is calibration,
    so ``resume_arm.window_open`` says its window is open at ANY hour (darks
    and flats are shot in daylight on purpose) -- this is what lets the test
    below exercise the "armed" half of #557's fix without depending on the
    process-wide twilight setting."""
    s = Session(name=name, status="dormant", plan=_calibration_plan(name),
               auto_resume=True)
    session_store.save(s)
    return s


@pytest.fixture
def rig(tmp_path, monkeypatch):
    """A Hub at a made-up, non-default site, with an isolated config file and
    session store, and a standing cooling request already on record --
    exactly the state ``restore_cooling`` finds on a reconnect.

    Patches ``config_store``'s OWN ``_path``/``_cfg`` (not the module-level
    name) so ``resume_arm``'s and ``session.py``'s own ``from ..config import
    config_store`` bindings see the same isolated store -- replacing the
    module attribute instead would leave those two modules holding the old,
    real singleton (see test_isolation_of_module_singletons in project
    memory). ``monkeypatch`` reverts both attributes afterward, so nothing
    leaks into the next test.
    """
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path)
    monkeypatch.setattr(config_store, "_path", tmp_path / "astrodeck.json")
    monkeypatch.setattr(config_store, "_cfg", None)
    cfg = config_store.cfg()
    cfg.site = SITE
    config_store.set_cooling_setpoint(-10.0)
    h = Hub()
    h.devices["camera"] = _Cam()
    return h


# ------------------------------------------------------------- (a) daylight


async def test_daylight_with_nothing_due_leaves_the_cooler_off(rig, bus_lines):
    """The #557 shape itself: a daytime connect with no run armed or due must
    not touch the cooler at all.

    MUTANT-A (verified): ``if alt < park_threshold_deg(...) or True:`` in
    ``Hub._cooling_restore_allowed`` -- reintroducing the unconditional
    restore -- turns this red with ``assert True is False`` (restore_cooling
    returned True instead of leaving the cooler off in daylight)."""
    cam = rig.devices["camera"]
    assert await rig.restore_cooling(DAY_TS) is False
    assert cam.calls == [], "it commanded a cooler nobody is about to use"
    said = [m for lv, m, _s in bus_lines if lv == "info" and "left off" in m]
    assert len(said) == 1, said
    assert "daylight" in said[0] and "-10" in said[0], said[0]


async def test_darkness_restores_the_cooler(rig, bus_lines):
    """The night-time control #557 asks for: the same connect, in the dark,
    restores exactly as it always has."""
    cam = rig.devices["camera"]
    assert await rig.restore_cooling(NIGHT_TS) is True
    assert cam.calls == [(True, -10.0)]
    assert any("cooling restored to -10" in m for _lv, m, _s in bus_lines)


async def test_an_armed_calibration_session_restores_in_daylight(rig, bus_lines):
    """#557's second clause: a run due (here, armed and always-open
    calibration) keeps the cooler on even though the clock says daylight.

    MUTANT-B (verified): ``if session is not None and False:`` in
    ``Hub._cooling_restore_allowed`` -- disabling the armed-session override --
    turns this red with ``assert False is True`` (restore_cooling returned
    False even though a calibration session was armed and its window open)."""
    _arm_calibration_session()
    cam = rig.devices["camera"]
    assert await rig.restore_cooling(DAY_TS) is True
    assert cam.calls == [(True, -10.0)]
    assert not [m for lv, m, _s in bus_lines if "left off" in m]


async def test_an_unset_site_still_restores_regardless_of_clock(tmp_path,
                                                                monkeypatch):
    """The one place this deliberately does NOT refuse: with no site
    configured this cannot judge daylight at all, and the behaviour it
    replaces restored unconditionally -- so "we cannot tell" must not become
    a new refusal (see test_cooling_restore.py's own reconnect test, which
    makes the same assumption on the default site)."""
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path)
    monkeypatch.setattr(config_store, "_path", tmp_path / "astrodeck.json")
    monkeypatch.setattr(config_store, "_cfg", None)
    config_store.set_cooling_setpoint(-10.0)
    h = Hub()
    h.devices["camera"] = _Cam()
    assert h.site.get("is_default", False) is True
    assert await h.restore_cooling(DAY_TS) is True, (
        "an unset site must fail OPEN, not add a new daylight refusal")


# --------------------------------------------------------------- (b) readback


async def test_a_mismatched_readback_is_not_claimed_restored(rig, bus_lines):
    """#143 itself: the write is accepted but the camera's own state
    disagrees, so this must say so instead of claiming "restored".

    MUTANT-C (verified): ``if confirmed is False and False:`` in
    ``Hub.restore_cooling`` -- ignoring the readback mismatch -- turns this
    red with ``assert True is False`` (restore_cooling logged "cooling
    restored" and returned True even though the camera's own get_cooler()
    readback disagreed)."""
    rig.devices["camera"] = _Cam(mute=True)   # accepts the call, does nothing
    assert await rig.restore_cooling(NIGHT_TS) is False
    errs = [m for lv, m, _s in bus_lines if lv == "error"]
    assert errs, "a restore that the camera did not actually perform said nothing"
    assert "NOT confirmed restored" in errs[0], errs[0]
    assert not [m for _lv, m, _s in bus_lines if "cooling restored to" in m], (
        "the log claimed a restore the camera never confirmed")


async def test_a_camera_with_no_readback_still_trusts_the_write(rig, bus_lines):
    """Most sim/fake cameras in the suite implement only ``set_cooler`` (no
    ``get_cooler``) -- absent readback is not a disagreement, so the old
    behaviour (trust the write) must be unchanged for them."""
    class _NoReadback(_Cam):
        get_cooler = None   # not callable -- getattr(cam, "get_cooler", None)

    rig.devices["camera"] = _NoReadback()
    assert await rig.restore_cooling(NIGHT_TS) is True
    assert any("cooling restored to -10" in m for _lv, m, _s in bus_lines)
