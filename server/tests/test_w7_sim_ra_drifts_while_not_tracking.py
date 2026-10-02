# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""``SimRig.ra_hours`` while tracking is off (issue #519, the suggested shape
of its fix): a real GEM with tracking off holds its AXES still against the
ground, so its HOUR ANGLE is what stays constant, and the RA it reports
increases at the sidereal rate. Before this, ``ra_hours`` was a plain
attribute that just sat wherever the last slew left it, so a park-held
mount's hour angle visibly closed on the meridian with the clock while the
tube did not move.

Modeled only while NOT PARKED (``park``/``find_home`` leave the mount at the
pole, where RA is not a quantity a real mount's hour angle meaningfully
holds -- a separate case, covered below), and only with a SAVED site (issue
#24: the 0,0 default is the Gulf of Guinea, not a real hour angle to hold --
also covered below, and the reason this file reads the longitude through
``site_gate.site_lat_lon`` rather than off the config directly).

MUTANTS, each run from a byte backup of ``server/astrodeck/devices/sim.py``,
restored byte-identically after (sha256 compared) and grepped to confirm the
mutant text was gone.

1. The ``ra_hours`` getter's ``if self._drift_active():`` short-circuit
   deleted (``return self._ra_hours`` unconditional, the old behavior). Two
   of the five tests below went RED, observed:

       AssertionError: tracking off should drift the RA away from where it
       stood when tracking stopped: 5.0 == 5.0
       assert 5.0 != 5.0 +- 5.0e-06

       AssertionError: resuming tracking must not jump the RA back to where
       it was before the drift
       assert 5.1671229861385655 == 5.0 +- 5.0e-06

2. ``_site_longitude_deg`` put back to reading ``config_store.cfg().site.
   longitude`` directly (the issue #24 guard this file's module docstring
   names, deleted). RED here AND in
   ``test_every_site_consumer_asks_whether_there_is_a_site.py`` (the
   project's static half of the same guard), observed:

       AssertionError: no site saved, but RA drifted anyway:
       5.1671229861385655

       AssertionError: these read a site coordinate without asking whether a
       site is set, ...: devices/sim.py:_site_longitude_deg (lines [414])
"""
from __future__ import annotations

import time as real_time

import pytest

import astrodeck.catalog.coords as coords_mod
from astrodeck.config import AppConfig, config_store
from astrodeck.devices.sim import SimRig

#: Longitude for every case below -- made up, never the real site (project
#: rule).
LON = -110.0


class _FakeClock:
    """``coords.py``'s ``time``: ``time()``/``monotonic()`` read a value this
    test drives directly; everything else is the real module. Mirrors
    ``tests/_group_harness.py``'s ``_Clock`` so this file needs no part of
    that heavier harness to exercise a clock-driven sim property."""

    def __init__(self, real, t0: float):
        self._real = real
        self.t = t0

    def time(self) -> float:
        return self.t

    def monotonic(self) -> float:
        return self.t

    def __getattr__(self, name):
        return getattr(self._real, name)


@pytest.fixture
def clock(monkeypatch):
    c = _FakeClock(real_time, real_time.time())
    monkeypatch.setattr(coords_mod, "time", c)
    return c


@pytest.fixture
def sited(monkeypatch):
    """A saved, made-up site -- the drift getter falls back to RA held
    constant (the old behavior) with none configured, which the last test
    below checks on its own terms."""
    cfg = AppConfig()
    cfg.site.latitude, cfg.site.longitude = 45.0, LON
    cfg.site.is_default = False
    monkeypatch.setattr(config_store, "cfg", lambda: cfg)
    return cfg


def test_hour_angle_holds_and_ra_drifts_while_not_tracking(clock, sited):
    rig = SimRig()
    rig.ra_hours = 5.0
    ha_before = coords_mod.hour_angle_h(rig.ra_hours, LON)

    rig.tracking = False
    clock.t += 600.0  # ten minutes of (real, and so near-sidereal) time

    assert rig.ra_hours != pytest.approx(5.0), (
        "tracking off should drift the RA away from where it stood when "
        f"tracking stopped: {rig.ra_hours} == 5.0")
    ha_after = coords_mod.hour_angle_h(rig.ra_hours, LON)
    assert ha_after == pytest.approx(ha_before, abs=1e-6), (
        f"a GEM with tracking off holds its HOUR ANGLE, not its RA: "
        f"{ha_before} drifted to {ha_after}")


def test_resuming_tracking_settles_the_drift_and_stops_it(clock, sited):
    """Once tracking resumes, RA must stop moving on its own -- the next
    slew, sync or pulse has to start from where the clock actually left the
    mount pointing, not silently keep drifting underneath it."""
    rig = SimRig()
    rig.ra_hours = 5.0
    rig.tracking = False
    clock.t += 600.0
    drifted_ra = rig.ra_hours

    rig.tracking = True
    assert rig.ra_hours == pytest.approx(drifted_ra), (
        "resuming tracking must not jump the RA back to where it was "
        "before the drift")
    clock.t += 600.0
    assert rig.ra_hours == pytest.approx(drifted_ra), (
        f"RA kept moving after tracking resumed: {rig.ra_hours}")


def test_no_drift_while_parked(clock, sited):
    """``park``/``find_home`` leave the mount at the pole with tracking off
    too, but RA must stay exactly where the slew to the pole left it: the
    park position's RA is not a real hour angle to hold, and
    `SimTelescope.park`/`find_home`'s own tests read a fixed value back."""
    rig = SimRig()
    rig.ra_hours = 0.0
    rig.parked = True
    rig.tracking = False
    clock.t += 600.0

    assert rig.ra_hours == pytest.approx(0.0), (
        f"a parked mount's RA drifted: {rig.ra_hours}")


def test_no_saved_site_is_the_old_frozen_behavior_not_a_guess(clock, monkeypatch):
    """Issue #24: the 0,0 default is the Gulf of Guinea, not a real hour
    angle to hold. ``test_every_site_consumer_asks_whether_there_is_a_site.py``
    is the static half of this guard (every site-coordinate reader must ask);
    this is its runtime counterpart for the one new reader this file adds."""
    cfg = AppConfig()  # the 0,0 / is_default placeholder -- no site saved
    monkeypatch.setattr(config_store, "cfg", lambda: cfg)

    rig = SimRig()
    rig.ra_hours = 5.0
    rig.tracking = False
    clock.t += 600.0

    assert rig.ra_hours == pytest.approx(5.0), (
        f"no site saved, but RA drifted anyway: {rig.ra_hours}")


def test_an_unreadable_config_is_the_old_frozen_behavior_not_a_crash(
        clock, monkeypatch):
    """A sim must never fail a geometry query over a config read (the
    ``_side_for_ra`` precedent, same file): if ``config_store.cfg()`` itself
    raises, tracking off leaves RA exactly where it was, as it did before
    #519's fix, rather than propagating the error out of ``set_tracking``."""
    def _raises():
        raise RuntimeError("config store not initialized")
    monkeypatch.setattr(config_store, "cfg", _raises)

    rig = SimRig()
    rig.ra_hours = 5.0
    rig.tracking = False       # must not raise
    clock.t += 600.0

    assert rig.ra_hours == pytest.approx(5.0), (
        f"an unreadable config should hold RA, not drift it: {rig.ra_hours}")
