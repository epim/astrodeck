"""The follower lines are flagged ``site_derived`` and never reach a viewer
(#189 S2, #318; spec 1.6, 6.9, 5.10; #166).

`_follower_gate` and `_visit_follower` say three things while a mosaic
waits: "every panel waits; shooting <follower> until the next panel is due",
"<follower>: back to the <mosaic> mosaic ...", and, for a follower that
waits for the mosaic (``after_group``), "<follower>: waits: after the
<mosaic> mosaic". Each is said at a moment when every panel waits, and in
these nights the wait is the meridian rule's, so the moment is a computed
crossing: the local sidereal time of a known RA, which is the longitude. The
engine logs each with ``site_derived=True``, and every serving seam drops a
flagged line for a principal without ``view.site_derived`` (`api.redact`,
held by test_site_derived_log_lines.py).

Nothing held the three flags. The S2 review removed each one in turn, in a
private scratch copy of server/ (#254), and every follower, meridian, reach,
rotation and site-derived test still passed (141 passed each time):
test_group_followers.py asks that the lines are SAID, never that they are
flagged. A flag nothing holds is the #19 class: the words are clean, and the
line still carries the site in its moment.

Each case runs the real engine on the clocked simulator
(tests/_group_harness.py) through test_group_followers.py's night: a
one-panel mosaic 8 min before transit, which waits for its crossing, and a
follower after it. What a viewer is served is checked through the ring
filter, `api.redact._redact_log_rows_for`, over the events the night
published.

MUTANTS, each the ``site_derived=True`` of one line removed (observed,
``-n0``):
    "every panel waits" unflagged (`_visit_follower`), RED on the bounded case:
        E   AssertionError: said but not flagged site_derived: 'M31: every
            panel waits; shooting Follower until the next '
        E   assert [] == ['M31: every ...panel is due']
    "back to the mosaic" unflagged (`_visit_follower`), RED on the bounded
    case:
        E   AssertionError: said but not flagged site_derived: 'Follower: back
            to the M31 mosaic; its frames are kept'
        E   assert [] == ['Follower: b...s or is done']
    "waits: after the mosaic" unflagged (`_follower_gate`), RED on the
    after_group case:
        E   AssertionError: said but not flagged site_derived: 'Follower:
            waits: after the M31 mosaic'
        E   assert [] == ['Follower: w...e M31 mosaic']
"""
from __future__ import annotations

from _group_harness import GROUP_ID, group_hub, group_store  # noqa: F401
from astrodeck.api.redact import _redact_log_rows_for
from astrodeck.auth import principal_for_role
from test_group_followers import _night, _plan

EVERY_PANEL_WAITS = "M31: every panel waits; shooting Follower until the next "
BACK = "Follower: back to the M31 mosaic; its frames are kept"
AFTER = "Follower: waits: after the M31 mosaic"


def _flagged(night, needle: str) -> list[str]:
    return [m for _t, _l, m in night.flagged if needle in m]


def _viewer_reads(night) -> list[str]:
    """Every log line a viewer's ring read serves from this night."""
    rows = [e for e in night.events if e["type"] == "log"]
    return [(r["data"] or {}).get("message", "")
            for r in _redact_log_rows_for(rows, principal_for_role("viewer"))]


async def test_a_bounded_followers_lines_are_flagged(group_hub, monkeypatch):
    night = await _night(group_hub, monkeypatch, _plan())
    assert night.done, night.trace[-3:]
    for needle in (EVERY_PANEL_WAITS, BACK):
        assert night.said(needle), f"premise: the night said {needle!r}"
        assert _flagged(night, needle) == night.said(needle), (
            f"said but not flagged site_derived: {needle!r}")
        assert not [m for m in _viewer_reads(night) if needle in m], (
            f"a viewer is served {needle!r}")
    # The control: a line said at a moment nothing computed (a panel's
    # completion, timed by its own frames) is not flagged, and a viewer
    # reads it, so the check above is not passing on a filter that drops
    # everything.
    done = "M31: panel 1-1 complete"
    assert night.said(done) and not _flagged(night, done)
    assert [m for m in _viewer_reads(night) if done in m]


async def test_an_after_group_followers_wait_line_is_flagged(group_hub,
                                                            monkeypatch):
    night = await _night(group_hub, monkeypatch,
                         _plan(follower_kw={"after_group": GROUP_ID}))
    assert night.done, night.trace[-3:]
    assert len(night.said(AFTER)) == 1, "premise: the wait was said once"
    assert _flagged(night, AFTER) == night.said(AFTER), (
        f"said but not flagged site_derived: {AFTER!r}")
    assert not [m for m in _viewer_reads(night) if AFTER in m], (
        f"a viewer is served {AFTER!r}")
