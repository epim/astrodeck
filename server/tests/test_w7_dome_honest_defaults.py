# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""WP-50 (c), #192 item 4, and D-16 (owner-approved 2026-09-30).

#192 found two claims in Tonight's copy that nothing in the engine keeps:
the brief's "opens the dome and binds it to the mount" (no open_shutter call
exists for a night's start, and DomePolicy.apply_binding has no caller), and
the STORY timeline's unconditional "dome closes" at dawn (gated on a SAFETY
SETTING, close_dome_when_done, which the compiled plan this function reads
cannot see). Item 4 of #192's suggested fix: until items 1-3 land (filed
separately), the brief and the timeline stop making these two claims.

D-16 builds the other half here: ``close_dome_when_done`` now defaults to
True (``config.py``), because leaving the roof open through the day is the
worse of the two failures #192 found. ``close_dome_on_unsafe`` is a
DIFFERENT, still-open owner question (#657) and is untouched.

A made-up site, not the observatory's.
"""
from __future__ import annotations

import datetime as _dt

from astrodeck.config import SafetyConfig
from astrodeck.flows.models import FlowEdge, FlowGraph, FlowNode
from astrodeck.flows.tonight import brief, resolve_tonight

SITE = {"latitude": 40.0, "longitude": -105.0, "elevation_m": 1600.0,
        "is_default": False}
TWILIGHT = -12.0
JUNE = _dt.datetime(2026, 6, 15, 20, 0, tzinfo=_dt.timezone.utc).timestamp()

M31 = {"name": "M31", "ra": "00h 42m 44s", "dec": "+41 16 09"}


def _n(nid, ntype, x=0.0, y=0.0, **params):
    return FlowNode(id=nid, type=ntype, x=x, y=y, params=params)


def _e(a, ap, b, bp):
    return FlowEdge(**{"from": a, "fromPort": ap, "to": b, "toPort": bp})


def _dome_flow() -> FlowGraph:
    """A DUSK WINDOW arming a DOME CONTROL node (wired "run", not
    countermanded by the flow) and M31 with one Ha capture."""
    return FlowGraph(nodes=[
        _n("d", "dusk", offset=-30, stop="Dawn", minAlt=30),
        _n("m", "dome", x=50),
        _n("t", "target", x=100, **M31),
        _n("c", "capture", x=200, filter="Ha", exposure=900, gain=100,
          bin="1", count=8, goal=2.0),
    ], edges=[
        _e("d", "window", "m", "run"),
        _e("d", "window", "t", "arm"),
        _e("t", "target", "c", "run"),
    ])


class TestTheBriefClaimsOnlyWhatTheCodeDoes:
    def test_the_brief_does_not_say_it_opens_or_binds_the_dome(self):
        """#192: nothing opens the shutter for a night's start and nothing
        binds the dome's azimuth to the mount - the brief must not say
        either happens.

        RED under mutant "the dome clause restored" (``brief``'s dusk
        sentence's dome ``if`` block put back), observed:
            AssertionError: the brief still claims the dome opens/binds:
            'This flow arms at astronomical dusk (−30 min), opens the
            dome and binds it to the mount. It then arms M31. ...'
        """
        text = brief(_dome_flow())
        assert "opens the dome" not in text and "binds" not in text, (
            f"the brief still claims the dome opens/binds: {text}")

    def test_the_brief_still_arms_at_dusk_with_no_dome_mention_missing_other_words(self):
        """CONTROL: removing the false dome clause must not silently eat the
        rest of the arm sentence (the offset, or the next clause)."""
        text = brief(_dome_flow())
        # The offset's sign is a real minus (U+2212, `_signed`), not a hyphen.
        assert text.startswith("This flow arms at astronomical dusk (−30 min)"), text
        assert "It then arms M31" in text, text


class TestTheStoryClaimsOnlyWhatTheCodeDoes:
    def test_the_dawn_row_does_not_claim_the_dome_closes(self):
        """#192: whether the dome closes at dawn depends on a SAFETY SETTING
        (close_dome_when_done) this function never reads, so the dawn row
        must not assert it either way.

        RED under mutant "the closer restored" (``_story``'s
        ``if automation.get("dome"): closers += ", dome closes"`` put back),
        observed:
            AssertionError: the dawn row claims a dome close:
            'Dawn: loop ends, mount parks, camera warms, dome closes'
        """
        out = resolve_tonight(_dome_flow(), SITE, now=JUNE,
                              twilight_deg=TWILIGHT)
        dawn_row = out["story"][-1]["msg"]
        assert "dome closes" not in dawn_row, (
            f"the dawn row claims a dome close: {dawn_row}")
        assert dawn_row.startswith("Dawn: loop ends, mount parks, camera warms"), (
            dawn_row)

    def test_the_dome_row_does_not_claim_it_opens_or_binds_but_keeps_the_true_half(self):
        """The "2. the dome" row's unsafe-close half is true for any flow
        that runs with a connected dome (to_plan refuses one otherwise), so
        it stays; the open/bind half does not.

        RED under mutant "the row's old text restored" (``_story``'s item-2
        sentence put back to "Dome shutter opens, azimuth bound to the
        mount - any unsafe..."), observed:
            AssertionError: Dome shutter opens, azimuth bound to the mount -
            any unsafe or stale safety reading closes it, whatever the flow
            is doing
            assert ('opens' not in ...
        """
        out = resolve_tonight(_dome_flow(), SITE, now=JUNE,
                              twilight_deg=TWILIGHT)
        dome_rows = [s["msg"] for s in out["story"]
                    if "whatever the flow is doing" in s["msg"]]
        (dome_row,) = dome_rows
        assert "opens" not in dome_row and "bound" not in dome_row, dome_row
        assert "closes it, whatever the flow is doing" in dome_row, dome_row


class TestD16CloseDomeWhenDoneDefaultsTrue:
    def test_a_bare_safety_config_closes_the_dome_when_done(self):
        """D-16: the default for a config that never set this flag is now
        True, not False - the opposite of close_dome_on_unsafe, which stays
        an open owner question (#657) and is untouched here.

        RED under mutant "the old default restored" (``close_dome_when_done:
        bool = True`` made ``= False``), observed:
            AssertionError: assert False is True
        """
        assert SafetyConfig().close_dome_when_done is True

    def test_close_dome_on_unsafe_is_unchanged_by_this_default(self):
        """CONTROL: D-16 only moves close_dome_when_done. close_dome_on_unsafe
        is #657's separate, still-open question."""
        assert SafetyConfig().close_dome_on_unsafe is False

    def test_an_explicit_false_still_wins(self):
        """An operator (or an already-saved config) who chose False keeps
        it - this is a DEFAULT, not a floor."""
        assert SafetyConfig(close_dome_when_done=False).close_dome_when_done is False
