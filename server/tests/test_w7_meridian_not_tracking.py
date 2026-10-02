# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""D-11 (backlog ruling, owner-approved 2026-09-30): while the mount reports
tracking off, ``Hub._compute_meridian`` gives no countdown -- status
``"not tracking"``, ``hours_to_flip`` null -- rather than counting down to a
flip a mount that is not moving cannot reach (issue #519).

``engine.py`` needs no change for this: ``_live_block`` already drops
``live.meridian_eta_s`` whenever ``hours_to_flip`` is None (it only sets the
chip under ``if ttf_h is not None and ttf_h > 0``), so a hub that publishes
the ruling above is enough to clear the chip end to end.

MUTANT, run from a byte backup of ``server/astrodeck/hub.py``, restored
byte-identically after (sha256 compared) and grepped to confirm the mutant
text was gone: the ``if tracking is False:`` branch in ``_compute_meridian``
deleted (its two assignments removed, so the function falls straight through
to the ``_is_gem``-driven chain as it did before the fix). Both of the first
two tests below went RED, observed:

    AssertionError: a park-held mount's meridian block: {'status':
    'counting', 'hours_to_flip': 0.05, 'flip_enabled': True, 'pier_side':
    'east', ...}
    assert 'counting' == 'not tracking'

    AssertionError: flip-disabled, tracking off: {'status': 'flip_disabled',
    'hours_to_flip': 0.05, 'flip_enabled': False, 'pier_side': 'east', ...}
    assert 'flip_disabled' == 'not tracking'
"""
from __future__ import annotations

import pytest

from astrodeck.devices.base import PierSide

# NOT applied at module level: `_hub` below is pure setup, no await in it.
_async = pytest.mark.asyncio


class _Tel:
    """Just enough of ``Telescope`` for ``_compute_meridian``: a known pier
    side (so the mount reads as a GEM), no device-reported flip ETA (the
    non-NINA path, which derives ``ttf`` from the hour angle), and the
    tracking flag the fix reads."""

    def __init__(self, tracking: bool | None, *, side=PierSide.EAST):
        self._tracking = tracking
        self._side = side

    async def time_to_meridian_flip(self):
        return None

    async def pier_side(self):
        return self._side

    async def get_tracking(self):
        if self._tracking is None:
            raise RuntimeError("serial read timed out")
        return self._tracking


class _EngineWithFlipPlan:
    """Just enough engine for ``_plan_flip_enabled`` to answer True."""

    flip_owed = False

    def __init__(self, *, meridian_flip: bool = True):
        from astrodeck.sequence import SequencePlan
        self.plan = SequencePlan(targets=[], meridian_flip=meridian_flip)


def _hub(monkeypatch, *, meridian_flip: bool = True):
    """A real ``Hub`` with only the fields ``_compute_meridian`` reads, on a
    MADE-UP site -- never the real one (project rule)."""
    from astrodeck.config import AppConfig, config_store
    from astrodeck.hub import Hub

    cfg = AppConfig()
    cfg.site.latitude, cfg.site.longitude = 45.0, -110.0
    # A SAVED site, not the 0,0/``is_default`` placeholder: `_compute_meridian`
    # derives its own `ttf` from the hour angle only once `site_is_set` is
    # true (issue #24), and the premise in each test below needs that path.
    cfg.site.is_default = False
    monkeypatch.setattr(config_store, "cfg", lambda: cfg)

    h = Hub.__new__(Hub)
    h.engine = _EngineWithFlipPlan(meridian_flip=meridian_flip)
    h._pier_side_seen = None
    return h


def _ra_a_few_minutes_east_of_the_meridian(longitude_deg: float) -> float:
    """An RA whose hour angle, right now, is a small negative number (east of
    the meridian, closing on it) -- so a TRACKING mount here is inside any
    reasonable warn window, which the first assertion in each test below
    checks before trusting the tracking-off branch."""
    from astrodeck.catalog.coords import lst_hours
    return (lst_hours(longitude_deg) + 0.05) % 24.0  # ~3 min of HA east


@_async
async def test_a_park_held_mount_inside_the_warn_window_publishes_no_chip(
        monkeypatch):
    """The clocked case the backlog plan names for this WP: a GEM a few
    minutes from its flip, with tracking reported off. RED under the mutant
    named in this file's docstring."""
    h = _hub(monkeypatch)
    ra = _ra_a_few_minutes_east_of_the_meridian(-110.0)

    tracking = await h._compute_meridian(_Tel(True), ra, 20.0)
    assert tracking["status"] == "counting" and tracking["hours_to_flip"] > 0, (
        f"premise: a tracking mount here counts down, or this test would "
        f"prove nothing: {tracking}")

    not_tracking = await h._compute_meridian(_Tel(False), ra, 20.0)
    assert not_tracking["status"] == "not tracking", (
        f"a park-held mount's meridian block: {not_tracking}")
    assert not_tracking["hours_to_flip"] is None, (
        f"a stopped mount published a countdown it cannot keep: {not_tracking}")


@_async
async def test_not_tracking_beats_flip_disabled(monkeypatch):
    """``flip_disabled`` deliberately keeps the countdown riding along (the
    one case where the strip is the only warning against a flip nobody will
    act on) -- but only because the mount is still tracking toward it. With
    tracking off there is no motion for that warning to be about, so "not
    tracking" must win here too."""
    h = _hub(monkeypatch, meridian_flip=False)
    ra = _ra_a_few_minutes_east_of_the_meridian(-110.0)

    m = await h._compute_meridian(_Tel(False), ra, 20.0)
    assert m["status"] == "not tracking", (
        f"flip-disabled, tracking off: {m}")
    assert m["hours_to_flip"] is None, f"flip-disabled, tracking off: {m}"


@_async
async def test_an_unanswered_tracking_read_is_not_treated_as_stopped(
        monkeypatch):
    """A mount that declines to say whether it is tracking is unknown, not
    stopped -- the same caution ``_note_pier_side`` already takes for a timed
    -out ``:Gm#`` (test_pier_side_is_published.py). The existing ttf-driven
    branches still decide ``status`` in this case."""
    h = _hub(monkeypatch)
    ra = _ra_a_few_minutes_east_of_the_meridian(-110.0)

    m = await h._compute_meridian(_Tel(None), ra, 20.0)
    assert m["status"] != "not tracking", (
        f"an unanswered tracking read was treated as a stopped mount: {m}")
    assert m["status"] == "counting" and m["hours_to_flip"] > 0, (
        f"an unanswered tracking read should fall through to the ttf-driven "
        f"branches unchanged: {m}")
