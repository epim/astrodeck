# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""The two lines a flip-point deadline times are flagged ``site_derived`` and
never reach a viewer (#189 S2, #318; spec 5.3, 5.7, 6.9; #166).

A mosaic panel let through before the meridian carries its flip point as its
visit's deadline (`_meridian_now`), and two lines are said AT that deadline:
`_run_visit`'s "<panel>: the visit ends here; its next frame would not
finish before the visit's deadline", and `_visit_panel`'s "<mosaic>: <panel>
reached its flip point before a frame could fit; not counted as a visit".
The flip point is the plan's lead before a computed meridian crossing, so
the moment either line lands is the local sidereal time of a known RA, which
is the longitude. The engine logs both with ``site_derived=True``, and every
serving seam drops a flagged line for a principal without
``view.site_derived`` (`api.redact`, test_site_derived_log_lines.py).

Nothing held either flag. The S2 review removed each in turn, in a private
scratch copy of server/ (#254), and every meridian, follower, reach,
rotation and site-derived test still passed (141 passed each time):
test_group_meridian.py asks that the lines are SAID, never that they are
flagged.

Each case is test_group_meridian.py's own night for that line, on the
clocked simulator (tests/_group_harness.py), and reads what a viewer is
served through the ring filter, `api.redact._redact_log_rows_for`.

MUTANTS, each the ``site_derived=True`` of one line removed (observed,
``-n0``):
    "the visit ends here" unflagged (`_run_visit`), RED on the first case:
        E   AssertionError: said but not flagged site_derived: 'the visit ends
            here; its next frame would not finish'
        E   assert [] == ["M31 1-1: th...t's deadline"]
    "not counted as a visit" unflagged (`_visit_panel`), RED on the second:
        E   AssertionError: said but not flagged site_derived: 'reached its
            flip point before a frame could fit'
        E   assert [] == ['M31: 1-1 re...d as a visit']
"""
from __future__ import annotations

from _group_harness import group_hub, group_store  # noqa: F401
from astrodeck.api.redact import _redact_log_rows_for
from astrodeck.auth import principal_for_role
from test_group_meridian import _night, meridian_plan

VISIT_ENDS = "the visit ends here; its next frame would not finish"
NOT_A_VISIT = "reached its flip point before a frame could fit"


def _flagged(night, needle: str) -> list[str]:
    return [m for _t, _l, m in night.flagged if needle in m]


def _viewer_reads(night) -> list[str]:
    rows = [e for e in night.events if e["type"] == "log"]
    return [(r["data"] or {}).get("message", "")
            for r in _redact_log_rows_for(rows, principal_for_role("viewer"))]


def _held(night, needle: str) -> None:
    said = night.said(needle)
    assert said, f"premise: the night said {needle!r}: {night.lines[-6:]}"
    assert _flagged(night, needle) == said, (
        f"said but not flagged site_derived: {needle!r}")
    assert not [m for m in _viewer_reads(night) if needle in m], (
        f"a viewer is served {needle!r}")


async def test_the_line_a_visit_ends_on_at_its_flip_point_is_flagged(
        group_hub, monkeypatch):
    """test_group_meridian's sequential one-panel mosaic, 30 min before
    transit: its first visit ends at the frame boundary before its flip
    point, and says so."""
    plan = meridian_plan(cols=1, ha_h=-0.5, ha_step_h=0.0, count=60,
                         group_kw={"mode": "sequential"})
    night = await _night(group_hub, monkeypatch, plan)
    assert night.done, night.trace[-3:]
    _held(night, VISIT_ENDS)
    # The control: the mosaic's completion is timed by its own frames, is
    # not flagged, and a viewer reads it.
    done = "M31: panel 1-1 complete"
    assert night.said(done) and not _flagged(night, done)
    assert [m for m in _viewer_reads(night) if done in m]


async def test_the_line_a_hop_that_outran_its_estimate_says_is_flagged(
        group_hub, monkeypatch):
    """test_group_meridian's one-panel mosaic 15 min before transit whose hop
    takes 400 s: the flip point comes before its first frame, and the visit
    is said not to count."""
    def setup(night):
        real = group_hub.goto_and_center

        async def slow_goto(*a, **kw):
            result = await real(*a, **kw)
            await night._park(400.0)
            return result

        night.mp.setattr(group_hub, "goto_and_center", slow_goto)

    plan = meridian_plan(cols=1, ha_h=-0.25, ha_step_h=0.0, count=2,
                         filters=("L", "R"))
    night = await _night(group_hub, monkeypatch, plan, before_run=setup)
    assert night.done, night.trace[-3:]
    _held(night, NOT_A_VISIT)
