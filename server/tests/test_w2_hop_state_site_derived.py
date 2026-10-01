"""#166 item 1, the residual left after the T18/T8 fixes landed (backlog
WP-11, owner-approved 2026-09-30): ``GET /api/sequence/state`` withholds
``group.panel``/``pass`` across a meridian wait, and ``live.meridian_eta_s``,
but still served the panel a THIRD way -- in ``detail`` ("slewing to M31
1-2..."), ``target``, ``target_index`` and ``schedule`` -- at the hop that
ends the wait, since the merged state a GET poll reads carries no per-publish
flag, and ``group.meridian_wait`` alone cannot tell "waiting" (detail says
only "M31: waiting for the meridian", which names no panel) from "ending"
(detail/target/target_index now name the panel a known RA transit chose).

This file does not import from test_group_meridian.py or
test_s7_sim_meridian_straddle.py, so its mutant runs stay independent of
theirs (the project's own stated practice, see test_w2_follower_site_derived.py).

Engine mutant: the ``ending_the_wait`` flag `SequenceEngine._setup_target`
sets on ``self.state`` disabled (``if False and ending_the_wait:``, in a
private scratch copy of server/, restored byte-identically after -- sha256
compared before and after). RED, observed:

    AssertionError: premise: a publish marked as the hop that ends the wait
    assert []

(no publish ever carries the marker, so the test's own premise -- that the
engine marks the hop at all -- is what catches the mutant; the viewer-leak
assertion further down never gets to run, which is itself the point: this
test's premises are graded in the order the fix's pieces depend on each
other.)

Redact mutant: ``api/redact.py``'s ``out = _withhold_hop_timing(out)`` line
commented out of ``_redact_sequence_for`` (same discipline, restored
byte-identically): RED, observed:

    AssertionError: the hop's state still named the panel to a viewer:
    {'detail': 'slewing to M31 1-2', 'target': 'M31 1-2', 'target_index': 1}
"""
from __future__ import annotations

from _group_harness import (GROUP_NAME, LON, T0, Night, grid_plan, group_hub,  # noqa: F401
                            group_store, ra_at)

from astrodeck.api.redact import _redact_sequence_for
from astrodeck.auth import principal_for_role
from astrodeck.sequence import schedule

#: Hour angle runs this much faster than the clock (test_group_meridian.py's
#: own constant, duplicated rather than imported -- see the module docstring).
SIDEREAL = 1.0027379093

VIEWER = principal_for_role("viewer")
OPERATOR = principal_for_role("operator")


def _name(label: str) -> str:
    return f"{GROUP_NAME} {label}"


def crossing(ra: float) -> float:
    """The fake time at which ``ra`` crosses the fixture site's meridian."""
    return T0 + schedule.hours_to_meridian_flip(ra, LON, T0) * 3600.0 / SIDEREAL


def _plan():
    """A 1x2 mosaic near the meridian, flips on, the same shape
    test_group_meridian.py's straddle cases use: column 0 crosses first, the
    group waits, then the hop onto column 1 (already past the meridian)
    ends the wait."""
    return grid_plan(rows=1, cols=2, group_kw=None,
                     panel_kw={"filters": ("L",), "count": 30,
                              "ha_h": -0.4333, "ha_step_h": 0.1},
                     meridian_flip=True)


async def test_the_hop_that_ends_the_wait_is_withheld_from_a_viewer(
        group_hub, monkeypatch):
    """Every ``state.group`` publish from the wait's first state to the first
    exposure after it is captured (``self.engine.state`` snapshotted at each
    ``sequence`` publish, the same technique test_s5_recorded_state.py's
    ``_record`` uses, since the marker this redacts is state-only and never
    rides the published payload). Among them is the hop's own publish --
    ``group.meridian_wait`` is true and ``group.panel``/``pass`` already name
    the NEW panel to an operator -- and for a VIEWER that publish must carry
    none of ``detail``, ``target``, ``target_index`` or ``schedule`` either.
    Every "waiting" publish before the hop keeps them unchanged from today
    (spec 6.9's accepted residual: ``group.panel`` there still names the
    PREVIOUS panel, the last one actually visited, not the one the wait is
    for, so it names no transit a viewer could not already have read before
    the wait began).

    MUTANT "the hop that ends the wait is not flagged" (`_setup_target`'s
    ``ending_the_wait`` assignment to ``self.state`` disabled, in a private
    scratch copy of server/, restored byte-identically after -- sha256
    compared before and after): RED, observed:

        AssertionError: premise: a publish marked as the hop that ends the
        wait
        assert []
    """
    import astrodeck.events as events_mod

    snapshots: list[dict] = []
    real_publish = events_mod.bus.publish

    def publish(topic, **payload):
        out = real_publish(topic, **payload)
        if topic == "sequence":
            snapshots.append(dict(night.engine.state))
        return out

    monkeypatch.setattr(events_mod.bus, "publish", publish)

    night = Night(group_hub, monkeypatch, coords_clock=True)
    try:
        done = await night.run(_plan(), wall_s=90.0)
    finally:
        await night.close()
    assert done, night.trace[-3:]

    waits = [s for s in snapshots if (s.get("group") or {}).get("meridian_wait")]
    assert waits, "premise: the group waited on the meridian rule"

    # premise: the fixture's first column really does cross before the hop
    # the test grades -- ties this file to test_group_meridian.py's own
    # proven shape without importing it (see the module docstring).
    assert crossing(ra_at(-0.4333)) > T0, "premise: a crossing exists tonight"

    # The HOP is marked by the engine's own ``_hop_site_derived``: state-only,
    # never published (``_set_state`` pops it from the payload before
    # ``bus.publish``), which is exactly why each snapshot above is read
    # straight off ``night.engine.state`` rather than off the published event
    # itself. Every other "waiting" publish, before the hop, carries no such
    # marker.
    hop_states = [s for s in waits if s.get("_hop_site_derived")]
    waiting_states = [s for s in waits if not s.get("_hop_site_derived")]
    assert waiting_states, "premise: a waiting publish before the hop"
    assert hop_states, "premise: a publish marked as the hop that ends the wait"

    hop = hop_states[0]
    assert hop.get("target") and hop.get("detail"), (
        f"premise: the operator's state names the panel in target/detail: {hop}")
    assert hop.get("target") != waiting_states[-1].get("target"), (
        "premise: the hop's target is a different panel from the last "
        f"waiting publish's: {hop.get('target')!r}")

    op = _redact_sequence_for(dict(hop), OPERATOR)
    vw = _redact_sequence_for(dict(hop), VIEWER)
    assert "_hop_site_derived" not in op, (
        "the engine's internal marker must never reach any principal, "
        f"operator included: {op}")
    assert op["target"] == hop["target"] and op["detail"] == hop["detail"], (
        "premise: an operator still reads the hop's panel")
    leaked = {k: vw[k] for k in ("detail", "target", "target_index", "schedule")
             if k in vw}
    assert leaked == {}, f"the hop's state still named the panel to a viewer: {leaked}"
    assert "panel" not in vw["group"] and "pass" not in vw["group"], (
        f"premise: the group panel/pass stay withheld too: {vw['group']}")

    # The "waiting" publishes before the hop are UNCHANGED (spec 6.9's
    # accepted residual): a viewer reads the same detail an operator does,
    # since it names only the mosaic, never a panel.
    waiting = waiting_states[0]
    assert "_hop_site_derived" not in waiting, (
        f"premise: a waiting publish carries no hop marker: {waiting}")
    op_w = _redact_sequence_for(dict(waiting), OPERATOR)
    vw_w = _redact_sequence_for(dict(waiting), VIEWER)
    assert op_w.get("detail") == vw_w.get("detail") == waiting.get("detail"), (
        f"a waiting publish's detail should still reach a viewer unchanged: "
        f"operator {op_w.get('detail')!r}, viewer {vw_w.get('detail')!r}")
