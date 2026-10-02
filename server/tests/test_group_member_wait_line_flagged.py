# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""A held member's wait line is flagged ``site_derived`` and never reaches a
viewer (#383; spec 1.6 S4 as-built paragraph, 6.9, #330, #166).

When a member's ``after_group`` holds its group (#330), `_follower_gate`'s
member branch says "M33: waits: after the M31 mosaic", once per pair
(``_group_gate_said``). When it is said follows the gating of the group it
waits for, which is a site computation, so by the rule of 6.9 the line
carries ``site_derived=True`` and every serving seam drops it for a principal
without ``view.site_derived`` (`api.redact`, held by
test_site_derived_log_lines.py).

Nothing held that flag in a night. test_group_follower_lines_are_flagged.py
covers a follower's lines only, test_group_member_after_group.py asks that
the member's line is said once and never that it is flagged, and
test_mosaic_spec_claims.py reads the source shape alone. S4-DOC3's verifier
removed the flag in a private copy and every one of those stayed green
(#383). This case runs the night and checks what it published, as the
follower file does: the line in ``Night.flagged`` (the ``site_derived``
keyword `bus.log` was handed) and absent from what a viewer's ring read
serves (`api.redact._redact_log_rows_for` over the night's events).

THE NIGHT is test_group_member_after_group.py's held-then-run night
(`_plan`): an upstream 2x2 "M31" whose window opens about ten minutes in, and
a downstream 1x2 "M33" after it whose panels both carry ``after_group``
naming M31, so M33 is held until M31 is complete and then runs.

The mutant was applied in a private scratch copy of server/
(scratchpad/S5-ENG-SCHED-mut), never in the shared tree (#254):

* "member branch drops site_derived=True": the ``site_derived=True`` of the
  one `bus.log` in `_follower_gate`'s ``member_of is not None`` branch
  removed.
"""
from __future__ import annotations

from _group_harness import group_hub, group_store  # noqa: F401
from astrodeck.api.redact import _redact_log_rows_for
from astrodeck.auth import principal_for_role
from test_group_member_after_group import _night, _plan

WAITS = "M33: waits: after the M31 mosaic"
#: A line said at a moment nothing computed: a panel's completion, timed by
#: its own frames.
DONE = "M31: panel 1-1 complete"


def _flagged(night, needle: str) -> list[str]:
    return [m for _t, _l, m in night.flagged if needle in m]


def _viewer_reads(night) -> list[str]:
    """Every log line a viewer's ring read serves from this night."""
    rows = [e for e in night.events if e["type"] == "log"]
    return [(r["data"] or {}).get("message", "")
            for r in _redact_log_rows_for(rows, principal_for_role("viewer"))]


def _events_saying(night, needle: str) -> list[dict]:
    """The published log events carrying ``needle``, as the WS lanes and the
    log ring serve them."""
    return [e["data"] for e in night.events if e["type"] == "log"
            and needle in str((e["data"] or {}).get("message", ""))]


async def test_a_held_members_wait_line_is_flagged_and_withheld(
        group_hub, monkeypatch):
    """"M33: waits: after the M31 mosaic" is published once, flagged
    ``site_derived``, and a viewer is not served it.

    The control: M31's panel completion, said at a moment its own frames
    set, is published unflagged and a viewer reads it, so the checks above
    do not pass on a filter that drops everything.

    MUTANT "member branch drops site_derived=True": RED (observed):
        AssertionError: said but not flagged site_derived: 'M33: waits: after
        the M31 mosaic'
        assert [] == ['M33: waits:...e M31 mosaic']
          Right contains one more item: 'M33: waits: after the M31 mosaic'
    """
    night = await _night(group_hub, monkeypatch, _plan())
    assert night.done, night.lines[-4:]
    assert night.said(WAITS) == [WAITS], (
        f"premise: the member's wait was said once: {night.said('waits:')}")
    assert _flagged(night, WAITS) == night.said(WAITS), (
        f"said but not flagged site_derived: {WAITS!r}")
    published = _events_saying(night, WAITS)
    assert [e.get("site_derived") for e in published] == [True], published
    assert not [m for m in _viewer_reads(night) if WAITS in m], (
        f"a viewer is served {WAITS!r}")
    assert night.said(DONE) and not _flagged(night, DONE), night.said(DONE)
    assert [m for m in _viewer_reads(night) if DONE in m], (
        f"a viewer is not served {DONE!r}: the filter drops everything")
