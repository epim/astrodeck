# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""#619 (a): pre-flight asks the engine's own ceiling and pier-flip
questions, not just the floor.

Before this, ``_start_preflight`` (server/astrodeck/api/app.py) only ever
ran the horizon/floor check (``_horizon_block`` -> ``hub._check_horizon``).
A single target that cleared the floor but sat above the zenith keep-out
(``cfg.safety.max_alt_deg``), or that needed a pier flip with
``plan.meridian_flip`` off, passed pre-flight clean and was only caught by
the engine's own slew gate (``SequenceEngine._mount_floor_verdict`` /
``_altitude_limit_verdict``) on the FIRST SLEW -- after the run had already
started.

Both new checks (``_ceiling_block``, ``_pier_block``) call the engine's own
verdict functions rather than a second copy of their maths, so pre-flight
and the run cannot disagree about where either limit is.

THE CLOCK IS PINNED, NOT RACED. ``_altitude_limit_verdict`` reads
``time.time()`` when no ``at`` is given, so ``astrodeck.sequence.engine``'s
``time`` is monkeypatched to a fixed instant (FIXED_TS) and every target's
RA is computed FROM that same instant with ``catalog.coords.lst_hours`` --
a pure function of the timestamp, never the wall clock -- so the geometry
below is exact and never a race against how long the test takes to run
(see the project's own lesson on fixed-sleep clock races). SITE is a
made-up fixture, not the rig's (project site-privacy rule).
"""
from __future__ import annotations

import pytest

import astrodeck.api.app as app_module
import astrodeck.sequence.engine as engine_mod
from astrodeck.catalog import altaz
from astrodeck.catalog.coords import lst_hours
from astrodeck.config import ConfigStore, SafetyConfig, Site
from astrodeck.devices.base import PierSide

#: An arbitrary fixed instant (2027-01-15 ~00:00 UTC-ish) used only to pin
#: "now" for the altitude geometry below -- not a real clock and not a
#: schedule anchor.
FIXED_TS = 1_800_000_000.0

SITE = Site(name="w10-ceiling-pier-fixture", latitude=42.0, longitude=-71.0,
           is_default=False)


class _FakeClock:
    """Replaces ``astrodeck.sequence.engine.time`` for this module only, so
    ``_altitude_limit_verdict``'s ``time.time()`` reads FIXED_TS exactly --
    nothing else in the pre-flight path this test exercises (no
    ``engine.start()`` is ever called) reads any other ``time`` member."""

    @staticmethod
    def time() -> float:
        return FIXED_TS


@pytest.fixture
def client(tmp_path, monkeypatch):
    temp_store = ConfigStore(path=tmp_path / "astrodeck.json")
    import astrodeck.config as config_mod
    import astrodeck.hub as hub_mod
    monkeypatch.setattr(config_mod, "config_store", temp_store)
    monkeypatch.setattr(hub_mod, "config_store", temp_store)
    monkeypatch.setattr(app_module, "config_store", temp_store)
    monkeypatch.setattr(engine_mod, "time", _FakeClock())

    from astrodeck.plans import PlanLibrary
    from astrodeck.profiles import ProfileLibrary
    monkeypatch.setattr(app_module, "plan_library",
                        PlanLibrary(directory=tmp_path / "plans"))
    monkeypatch.setattr(app_module, "profiles",
                        ProfileLibrary(directory=tmp_path / "profiles"))

    from fastapi.testclient import TestClient
    app = app_module.create_app()
    with TestClient(app) as c:
        # Every start path stops here before touching a real rig: if
        # pre-flight let a refusal-worthy plan through, this is where it
        # would be caught, loudly, rather than silently "starting" nothing.
        def _must_not_start(plan, **kw):
            raise AssertionError("engine.start reached: pre-flight should "
                                 "have refused first")
        monkeypatch.setattr(app_module.engine, "start", _must_not_start)
        monkeypatch.setattr(app_module.hub, "require", lambda role: object())
        yield c, temp_store


def _plan_payload(ra_hours: float, dec_deg: float, *,
                  meridian_flip: bool = True) -> dict:
    return {
        "name": "ceiling-pier",
        "meridian_flip": meridian_flip,
        "targets": [{
            "name": "zenith-ish",
            "ra_hours": ra_hours, "dec_deg": dec_deg,
            "steps": [{"exposure_s": 1.0, "count": 1}],
        }],
    }


# ------------------------------------------------------------------ ceiling


def test_a_target_above_the_zenith_keepout_is_refused_before_the_slew(client):
    """#619 (a), leg 1: a target that clears the floor but sits above
    ``cfg.safety.max_alt_deg`` must 409 at pre-flight, not pass through to
    ``engine.start`` and get caught only on the first slew.

    GEOMETRY (computed, not guessed): dec = SITE.latitude, ra = LST at
    FIXED_TS puts the target at the zenith (hour angle 0) -- the self-check
    below confirms altitude > 85 deg before the real assertion runs, so a
    future change to the ephemeris maths fails loudly here rather than as
    an opaque assertion below. ceiling = 60 deg, comfortably under it.

    MUTANT (verified): dropping the ceiling pass from ``_start_preflight``
    (commenting out the ``for t in sky: ceiling = _ceiling_block(t)...``
    loop) turns this red with:
        AssertionError: engine.start reached: pre-flight should have
        refused first
    """
    c, store = client
    store.set_site_and_safety(SITE, SafetyConfig(max_alt_deg=60.0))

    ra_hours = lst_hours(SITE.longitude, FIXED_TS)
    dec_deg = SITE.latitude
    alt, _az = altaz(ra_hours, dec_deg, SITE.latitude, SITE.longitude, FIXED_TS)
    assert alt > 85.0, f"self-check: target is not near zenith ({alt:.1f} deg)"

    r = c.post("/api/sequence/start", json=_plan_payload(ra_hours, dec_deg))
    assert r.status_code == 409, r.text
    detail = r.json()["detail"]
    assert detail["code"] == "ceiling", detail
    assert "zenith keep-out" in detail["detail"], detail


def test_a_target_below_the_ceiling_is_not_refused_by_it(client):
    """Control: the SAME ceiling (60 deg) with a target comfortably below
    it must not 409 on the ceiling leg. (It still reaches the stubbed
    ``engine.start``, which raises on purpose -- caught here to prove the
    pre-flight itself let the request through rather than refusing it.)"""
    c, store = client
    store.set_site_and_safety(SITE, SafetyConfig(max_alt_deg=60.0))

    # Four hours of hour angle off the meridian, dec = latitude: comfortably
    # below 60 deg (self-checked) without needing a second geometry.
    ra_hours = (lst_hours(SITE.longitude, FIXED_TS) + 4.0) % 24.0
    dec_deg = SITE.latitude
    alt, _az = altaz(ra_hours, dec_deg, SITE.latitude, SITE.longitude, FIXED_TS)
    assert 0.0 < alt < 55.0, f"self-check: target not safely under the " \
                            f"ceiling ({alt:.1f} deg)"

    with pytest.raises(AssertionError, match="engine.start reached"):
        c.post("/api/sequence/start", json=_plan_payload(ra_hours, dec_deg))


# --------------------------------------------------------------------- pier


class _FakeTelFlipNeeded:
    """Reports a destination pier side opposite the one it is on now -- the
    exact #619 (a) leg-2 shape: a slew that would need a flip."""
    connected = True
    reports_destination_pier_side = True

    async def pier_side(self) -> PierSide:
        return PierSide.EAST

    async def destination_pier_side(self, ra_hours: float,
                                    dec_deg: float) -> PierSide:
        return PierSide.WEST


def test_a_flip_needing_target_is_refused_when_flips_are_off(client):
    """#619 (a), leg 2: a slew that would need a pier flip, with the plan's
    ``meridian_flip`` off, must 409 at pre-flight -- the same pier-collision
    guard the engine's slew gate asks mid-run
    (``SequenceEngine._mount_floor_verdict``), over the same bounded mount
    read.

    The target sits off the meridian (not at zenith) so neither the default
    ceiling (90 deg) nor the default floor (0 deg, disabled) can mask the
    pier refusal under test.

    MUTANT (verified): dropping the pier pass from ``_start_preflight``
    turns this red with:
        AssertionError: engine.start reached: pre-flight should have
        refused first
    """
    c, store = client
    store.set_site_and_safety(SITE, SafetyConfig(enforce_pier_limits=True))
    app_module.hub.devices["telescope"] = _FakeTelFlipNeeded()

    ra_hours = (lst_hours(SITE.longitude, FIXED_TS) + 1.0) % 24.0
    dec_deg = SITE.latitude
    alt, _az = altaz(ra_hours, dec_deg, SITE.latitude, SITE.longitude, FIXED_TS)
    assert 10.0 < alt < 89.0, f"self-check: target altitude {alt:.1f} deg " \
                             f"is not a clean mid-sky position"

    r = c.post("/api/sequence/start",
               json=_plan_payload(ra_hours, dec_deg, meridian_flip=False))
    assert r.status_code == 409, r.text
    detail = r.json()["detail"]
    assert detail["code"] == "pier_flip", detail
    assert "pier flip" in detail["detail"], detail


def test_the_same_flip_is_allowed_when_meridian_flip_is_on(client):
    """Control: the identical geometry and pier mismatch, but with
    ``meridian_flip`` left on (the plan default) -- the engine's own guard
    only refuses when flips are disabled, so this must reach the stubbed
    ``engine.start`` rather than 409."""
    c, store = client
    store.set_site_and_safety(SITE, SafetyConfig(enforce_pier_limits=True))
    app_module.hub.devices["telescope"] = _FakeTelFlipNeeded()

    ra_hours = (lst_hours(SITE.longitude, FIXED_TS) + 1.0) % 24.0
    dec_deg = SITE.latitude

    with pytest.raises(AssertionError, match="engine.start reached"):
        c.post("/api/sequence/start",
              json=_plan_payload(ra_hours, dec_deg, meridian_flip=True))
