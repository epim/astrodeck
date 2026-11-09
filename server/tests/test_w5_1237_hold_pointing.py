# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""WP 5.5 (#1237): the hub notes the pre-hold pointing itself.

While a meridian flip's hold is open a viewer is served the last
``mount.ra_hours``, ``dec_deg`` and ``tracking`` seen before it (#1067). Those
were recorded only by the viewer redaction pass, so a viewer whose FIRST poll
fell inside a hold read null until the hold ended. The status build notes them
beside ``slewing`` (#1037), whether or not anybody was polling.

WHAT IS GRADED. The sim hub's real ``poll_status`` runs once before the hold
and once inside it (the mount having moved), and the redaction pass runs for
the first time inside the hold.

MUTANT "noted by the redaction pass only" (the three ``note_flip_hold_value``
calls removed from the mount block of ``Hub.poll_status``): RED, observed -
    AssertionError: a viewer whose first poll is inside the hold read
    ra_hours None
"""
from __future__ import annotations

import pytest

from _simhub import sim_hub  # noqa: F401

from astrodeck import events
from astrodeck.api.redact import _redact_site_for
from astrodeck.auth import principal_for_role, reset_active_provider

VIEWER = principal_for_role("viewer")
OPERATOR = principal_for_role("operator")


@pytest.fixture(autouse=True)
def _clean_hold():
    events.end_flip_hold()
    events._FLIP_HOLD_LAST.clear()
    yield
    reset_active_provider()
    events.end_flip_hold()
    events._FLIP_HOLD_LAST.clear()


async def test_a_viewer_whose_first_poll_is_inside_the_hold_reads_the_pre_hold_pointing(
        sim_hub, monkeypatch):
    tel = sim_hub.devices["telescope"]
    before = await sim_hub.poll_status()
    pre = before["mount"]
    assert pre["ra_hours"] is not None, "precondition: the sim mount reports"

    # The flip opens and the mount slews: the live values are no longer the
    # pre-hold ones.
    events.begin_flip_hold()
    live_ra, live_dec = (pre["ra_hours"] + 6.0) % 24.0, -12.5

    async def moved():
        return live_ra, live_dec

    async def not_tracking():
        return False

    monkeypatch.setattr(tel, "get_position", moved)
    monkeypatch.setattr(tel, "get_tracking", not_tracking)
    inside = await sim_hub.poll_status()
    assert inside["mount"]["ra_hours"] != pre["ra_hours"], (
        "precondition: the mount moved inside the hold")

    # No viewer has been served a frame yet: this is the first redaction pass.
    viewer = _redact_site_for(dict(inside, mount=dict(inside["mount"])), VIEWER)
    assert viewer["mount"]["ra_hours"] == pre["ra_hours"], (
        f"a viewer whose first poll is inside the hold read ra_hours "
        f"{viewer['mount']['ra_hours']}")
    assert viewer["mount"]["dec_deg"] == pre["dec_deg"]
    assert viewer["mount"]["tracking"] == pre["tracking"]

    operator = _redact_site_for(dict(inside, mount=dict(inside["mount"])),
                                OPERATOR)
    assert operator["mount"]["dec_deg"] == pytest.approx(live_dec, abs=0.01)
    assert operator["mount"]["tracking"] is False
