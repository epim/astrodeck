# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""``state.group.set_aside`` carries ``kind`` and ``for_now`` on every record
(#573, #534 follow-up; backlog ruling D-07, owner-approved 2026-09-30,
WP-49).

Before this, `_group_state` (engine.py) published only ``{panel, reason}``,
so PANELS worded every set-aside "set aside tonight", whatever kind it was
and however soon the engine meant to retry it: a centring set-aside that may
still expire in 45 minutes (``group_rules.set_aside_expiry``) read exactly
like one that lasts the rest of the night. `_group_state` now adds
``kind`` (``run.set_aside_kind``'s word: "centring", "floor", "rejects",
"group", ...) and ``for_now`` (`_awaiting_expiry`'s answer), ALWAYS, so a
client reading no key never misreads a for-now panel as done for the night.

TWO CASES, chosen so each clears its own path through `_may_expire`
(``kind == CENTRING`` and no expiry yet tonight) without the other's
machinery:

* A streak of centring failures (`GroupRun._count_failure`), struck at its
  third visit: ``kind`` is ``"centring"`` and ``for_now`` is True, since
  nothing has expired yet tonight.
* A target below its own altitude floor (``on_floor="advance"``,
  `_enforce_altitude_floor`, engine ``kind="floor"``): a DIFFERENT kind,
  never a centring one, so ``for_now`` is False at once -- no waiting, no
  expiry to watch for.

The engine cases run the real `_run_scheduled` on the clocked simulator
(tests/_group_harness.py), as test_h4_centring_set_aside_expiry.py and
test_group_floor_stop_counted.py do.

Both mutants were applied in a private copy of server/ (scratchpad
w7-WP49-mut), restored from a byte backup and sha256-checked after each,
never in the shared tree (#254). The observed failure is quoted verbatim.
"""
from __future__ import annotations

import astrodeck.sequence.engine as engine_mod
from _group_harness import GROUP_NAME, Night, grid_plan, group_hub, group_store
from astrodeck.sequence.group_rules import CENTRING
from astrodeck.sequence.models import Schedule

MISSES = f"{GROUP_NAME} 2-2"
FLOORED = f"{GROUP_NAME} 1-2"


def _first_set_aside(night: Night, panel: str) -> dict:
    """The first published ``group.set_aside`` record naming ``panel``, as
    ``GET /api/sequence/state`` would have answered the moment it was made."""
    for s in night.states:
        for rec in (s.get("group") or {}).get("set_aside", []):
            if rec.get("panel") == panel:
                return rec
    raise AssertionError(f"{panel} was never published set aside: "
                         f"{[s.get('group') for s in night.states[-5:]]}")


# ------------------------------------------------------ a centring set-aside

def _misses_first_three(who: str, n: int, result: dict) -> dict:
    """2-2's first three centring attempts fail; every later one, and every
    other panel's, centres (the other panels' failures would confound which
    kind struck it)."""
    if who == MISSES and n <= 3:
        return {**result, "centered": False, "error_arcmin": None}
    return result


async def test_a_centring_set_aside_not_yet_expired_publishes_for_now_and_its_kind(
        group_hub, monkeypatch):
    """2-2 misses its first three visits and is struck at the pass boundary
    that closes them: a streak of centring failures and nothing else
    (`GroupRun._count_failure`), so `GroupRun.set_aside_kind` reads
    ``"centring"``. Nothing has expired tonight yet, so `_may_expire` says
    yes: the published record carries ``kind: "centring"`` and
    ``for_now: true``, read straight off `_awaiting_expiry`.

    RED under mutant "for_now always false" (`_group_state`'s ``"for_now"``
    key given the literal ``False`` instead of
    ``self._awaiting_expiry(run, p)``), observed:

        AssertionError: 2-2's set-aside must still say for_now
        assert False is True
    """
    night = Night(group_hub, monkeypatch, goto=_misses_first_three)
    try:
        done = await night.run(grid_plan(), wall_s=60.0)
    finally:
        await night.close()
    assert done, night.lines[-4:]
    record = _first_set_aside(night, "2-2")
    assert record["kind"] == CENTRING, record
    assert record["for_now"] is True, "2-2's set-aside must still say for_now"


# ---------------------------------------------------------- a floor set-aside

def _floor_after(night: Night, monkeypatch, frames: int) -> None:
    """``FLOORED`` reads 10 degrees to the frame loop's floor check once it
    has shot ``frames`` frames tonight, and its true (high) altitude before
    that (test_group_floor_stop_counted.py's own technique)."""
    real = engine_mod._frame_altitude

    def altitude(target, site, when):
        shot = len([f for t, f in night.shots() if t == FLOORED])
        if target.name == FLOORED and shot >= frames:
            return 10.0
        return real(target, site, when)

    monkeypatch.setattr(engine_mod, "_frame_altitude", altitude)


async def test_a_floor_set_aside_never_expires_and_names_its_kind(
        group_hub, monkeypatch):
    """1-2 carries its own 30 degree floor (``on_floor="advance"``) and
    drops to 10 degrees after its first frame; 1-1 centres and shoots
    normally throughout, so nothing else sets 1-2 or the group aside, and
    the D-03 held-pass escalation (test_group_floor_stop_counted.py's own
    cases) never comes into it. `_set_panel_aside` is called ``decided=True,
    kind="floor"`` (engine.py), which is never the one kind that expires:
    the published record carries ``kind: "floor"`` and ``for_now: false`` at
    once, with no wait.

    RED under mutant "kind always panel" (`_group_state`'s ``"kind"`` key
    given the literal ``"panel"`` instead of
    ``run.set_aside_kind.get(p, "panel")``), observed:

        AssertionError: ('panel', False)
        assert ('panel', False) == ('floor', False)
    """
    plan = grid_plan(rows=1, cols=2)
    floored = next(t for t in plan.targets if t.name == FLOORED)
    floored.schedule = Schedule(min_altitude_deg=30.0, on_floor="advance")
    night = Night(group_hub, monkeypatch)
    _floor_after(night, monkeypatch, frames=1)
    try:
        done = await night.run(plan, wall_s=60.0)
    finally:
        await night.close()
    assert done, night.lines[-4:]
    record = _first_set_aside(night, "1-2")
    assert (record["kind"], record["for_now"]) == ("floor", False), (
        record["kind"], record["for_now"])
