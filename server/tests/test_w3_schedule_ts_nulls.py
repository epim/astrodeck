"""``ui/src/types.ts``'s ``SequenceState.schedule`` admits the null the
server actually sends for ``start_ts`` and ``stop_ts`` (#552, backlog WP-23).

THE DEFECT. ``SequenceEngine._run_scheduled``'s earliest-waiter publish
(engine.py, the "none ready: at least one target is waiting" branch) carries
``start_ts``/``stop_ts`` straight from ``schedule.gating_status`` into the
published ``SequenceState.schedule`` block:

    schedule={"state": "waiting", "reason": gs.get("reason", ""),
              "eta_s": round(gs.get("eta_s", 0.0)),
              "start_ts": gs.get("start_ts"), "stop_ts": gs.get("stop_ts")}

``gating_status`` answers ``start_ts=None`` for a PRO-14 constraint wait
(moon separation/illumination or hour angle: step 4b sets ``start = None``
on purpose, "so the engine's earliest-waiter branch skips it and it rides
the bounded else-sleep instead of busy-spinning on a past start_ts"), and
``stop_ts=None`` for any target whose window has no stop bound at all
(``resolve_window``, ``stop_mode == "none"``, the schedule's own default).
Before this fix ``types.ts`` typed both ``number | undefined``, which admits
neither JSON null.

WHY IT MATTERS, as #552 found it: both of today's UI consumers happen to
guard for null already (``lib/scheduleStatus.ts`` asks ``start_ts != null``,
``VitalsBand.tsx`` asks truthiness), so nothing renders wrong yet. A
consumer written to the DECLARED type, not the real one, would not: it could
ask ``start_ts !== undefined``, see true for a null, and format ``null *
1000`` as the 1970 epoch.

THIS FILE, not test_types_mirror_status.py (out of scope for WP-23; its own
case over this is #552's own suggestion for a later pass). Taken from
``schedule.gating_status`` directly rather than a hand-built dict: it is the
one function that decides both nulls (schedule.py step 4b for ``start_ts``,
``resolve_window``'s ``stop_mode == "none"`` for ``stop_ts``), and the
engine's publish above carries its answer through unchanged, so a case
through it is graded against the real decision, not a value this test
invented. The parser (``_interface``, ``_members``) and the JSON-type check
(``_ts_admits``) are test_types_mirror_groups.py's own, reused rather than
copied, so their own guards (test_the_parser_reads_members_and_nothing_else
et al.) still cover them.

Every test here names the mutant it kills and quotes the failure that
mutant produced, observed in a private copy of the tree (a byte-for-byte
backup of ``ui/src/types.ts``, sha256-compared against the backup after).
"""
from __future__ import annotations

import time

import pytest

from astrodeck.catalog.coords import lst_hours
from astrodeck.sequence import schedule as sch
from astrodeck.sequence.models import Schedule, Target
from test_types_mirror_groups import TYPES_TS, _interface, _members, _ts_admits

pytestmark = pytest.mark.skipif(
    not TYPES_TS.exists(), reason="ui/ not present (server-only checkout)")

#: A made-up site (never the rig's), the one test_schedule.py and
#: test_constraint_gate.py already use for the same hour-angle math.
SITE = {"name": "Mid", "latitude": 40.0, "longitude": -74.0,
        "elevation_m": 0.0, "is_default": False}


def _target(ra: float, dec: float = 20.0, **sched_kw) -> Target:
    return Target(name="T", ra_hours=ra, dec_deg=dec,
                  schedule=Schedule(**sched_kw))


def _ra_at_ha(ha_h: float, now: float) -> float:
    """The RA (hours) whose hour angle at ``SITE``'s longitude is ``ha_h`` at
    ``now`` (test_constraint_gate.py's own helper: LST minus the wanted HA)."""
    return (lst_hours(SITE["longitude"], now) - ha_h) % 24.0


def _schedule_member_types() -> dict[str, str]:
    """The TS member types of ``SequenceState``'s nested ``schedule`` object
    (the same "strip the braces, re-run ``_members``" move
    test_types_mirror_groups.py uses for ``SequenceGroupState.set_aside``'s
    element type)."""
    body = _interface("SequenceState")["schedule"]
    return _members(body.strip()[1:-1])


def test_a_constraint_wait_answers_start_ts_null_with_a_real_stop_ts():
    """An hour-angle constraint 5h east of a 3h limit, window already open
    (``start_mode="now"``) and capped 3h out (``max_run_min=180``):
    ``gating_status`` reaches step 4b, and nulls ``start_ts`` on purpose
    while ``stop_ts`` stays the capped number -- exactly the shape
    `_run_scheduled` republishes verbatim.

    RED under mutant "start_ts not nullable in types.ts" (``start_ts?:
    number;``, the ``| null`` dropped from ``SequenceState.schedule``):

        E   AssertionError: types.ts types start_ts as 'number', which does
            not admit None
    """
    now = time.time()
    t = _target(_ra_at_ha(-5.0, now), dec=40.0,
               start_mode="now", stop_mode="none", max_run_min=180,
               max_hour_angle_h=3.0)
    gs = sch.gating_status(t, SITE, -12.0, now)
    assert gs["state"] == "waiting", gs            # premise: reached step 4b
    assert gs["start_ts"] is None, gs               # premise: the real null
    assert gs["stop_ts"] is not None, gs            # premise: a real number

    ts = _schedule_member_types()
    assert _ts_admits(gs["start_ts"], ts["start_ts"]), (
        f"types.ts types start_ts as {ts['start_ts']!r}, which does not "
        f"admit None")
    assert _ts_admits(gs["stop_ts"], ts["stop_ts"])


def test_a_window_with_no_stop_bound_answers_stop_ts_null_with_a_real_start():
    """A target due to start three hours from now, with no stop bound (the
    schedule's own default, ``stop_mode="none"``): ``resolve_window``
    answers ``stop_ts=None``, and ``gating_status`` stops at step 3 ("window
    not open yet") with the real future ``start_ts``.

    RED under mutant "stop_ts not nullable in types.ts" (``stop_ts?:
    number;``, the ``| null`` dropped from ``SequenceState.schedule``):

        E   AssertionError: types.ts types stop_ts as 'number', which does
            not admit None
    """
    now = time.time()
    future = time.strftime("%H:%M", time.localtime(now + 3 * 3600))
    t = _target(ra=1.0, dec=20.0, start_mode="time", start_time=future,
               stop_mode="none")
    gs = sch.gating_status(t, SITE, -12.0, now)
    assert gs["state"] == "waiting", gs             # premise: step 3
    assert gs["start_ts"] is not None and gs["start_ts"] > now, gs
    assert gs["stop_ts"] is None, gs                # premise: the real null

    ts = _schedule_member_types()
    assert _ts_admits(gs["start_ts"], ts["start_ts"])
    assert _ts_admits(gs["stop_ts"], ts["stop_ts"]), (
        f"types.ts types stop_ts as {ts['stop_ts']!r}, which does not "
        f"admit None")
