"""The engine clears the ``live`` meridian chip once there is nothing to
count down to (#422; spec 5.7 and 5.10 as built S5, S7-ENG-FLIP).

THE DEFECT. `SequenceEngine._set_state` added ``live`` to its state only
when `_live_block` returned one, and merged: a publish whose `_live_block`
came back empty brought no ``live`` of its own, so the merge kept the old
object. Past the crossing the flip is behind the mount and `_live_block`
builds no chip, but the last countdown the window published rode every
later state to the end of the run, and the UI's heads-up
(``ui/src/lib/scheduleStatus.ts``), which trusts the engine to attach
``meridian_eta_s`` only inside ``meridian_flip_warn_min``, went on
announcing "Meridian flip in" a few seconds for a flip already made.

THE FIX. `_set_state` pops ``live`` from its state when `_live_block`
returns None and the caller passed no ``live`` of its own, the explicit
clear it already makes for ``group`` and ``schedule``.

THE NIGHT is the crossing night #422 was found on: the golden flow plan
from the harness's ``T0``, on the clocked simulator (tests/_group_harness.py),
whose meridian the harness holds on the night's clock (#368). NGC 7331
enters the 15 min flip-warn window at 9960 s, flips at its retry
`MERIDIAN_SIDE_MARGIN_S` past the crossing, and runs on. The harness's
scripted capture leaves ``hub.last_frame`` unset, so no sensor temperature
rides ``live`` and the chip is the block's only field: exactly the case in
which nothing else replaced the stale object (#422's "Reach").

Each case names the mutant it was shown RED under, with the failure
observed, verbatim (long lines wrapped). The mutant was applied in a
private scratch copy of server/ (scratchpad S7-ENG-FLIP-mut, from a byte
backup), never in the shared tree (#254):

* "live merged, never popped": `_set_state`'s ``if live:
  kw.setdefault("live", live)`` restored alone, as the code was before
  #422, with no clear when `_live_block` returns None. #422 quoted 25
  states carrying 7 s; since #366 moved the retry and the flip it is 27
  states carrying 5 s, and test_group_harness_meridian_clock.py's
  ``test_past_the_crossing_no_chip_is_published`` goes red with it
  (observed).
* "cleared with the chip": the block published only while it holds
  ``meridian_eta_s``, the over-eager fix the control below is for.
"""
from __future__ import annotations

from types import SimpleNamespace

import astrodeck.catalog.coords as coords_mod
from _group_harness import (LON, T0, TERMINAL, Night, close_night_hub,
                            group_store, night_hub)  # noqa: F401
from astrodeck import events
from test_group_rotation import _golden_as_recorded

#: The plan's warn lead, the default the golden plan leaves in place.
WARN_MIN = 15.0


def _hours_to_flip(ra: float, t: float) -> float:
    """What the hub derives for a mount pointing at ``ra`` at ``t``, to its
    four places: minus the hour angle (`Hub._compute_meridian`)."""
    return round(-coords_mod.hour_angle_h(ra, LON, t), 4)


def _chip(ttf_h: float) -> int | None:
    """The countdown `_live_block` gives for ``ttf_h``: whole seconds while
    the flip is ahead and inside the warn lead, none otherwise."""
    if 0.0 < ttf_h and ttf_h * 60.0 <= WARN_MIN:
        return round(ttf_h * 3600.0)
    return None


async def _crossing_night(monkeypatch, *, temperature_past_the_crossing=None):
    """The crossing night on a fresh clocked hub. Every ``sequence`` state
    published up to the first terminal one is recorded with the night's
    clock, where the mount points and the whole ``live`` object, or None
    when the state has no ``live`` key at all. With
    ``temperature_past_the_crossing``, the first capture past the crossing
    gives ``hub.last_frame`` that sensor temperature, as a camera that
    reports one would. Returns ``(night, rows)``."""
    hub, popped = await night_hub(monkeypatch)
    try:
        night = Night(hub, monkeypatch, t0=T0)
        tel = hub.devices["telescope"]
        rows: list[dict] = []
        inner = events.bus.publish

        def publish(topic, **payload):
            if topic == "sequence" and not (rows and rows[-1]["state"]
                                            in TERMINAL):
                rows.append({"t": night.clock.t, "ra": tel.rig.ra_hours,
                             "state": payload.get("state"),
                             "live": (dict(payload["live"])
                                      if "live" in payload else None)})
            return inner(topic, **payload)

        monkeypatch.setattr(events.bus, "publish", publish)
        if temperature_past_the_crossing is not None:
            def on_capture(rec):
                if (getattr(hub, "last_frame", None) is None
                        and _hours_to_flip(tel.rig.ra_hours, rec["t"]) <= 0):
                    hub.last_frame = SimpleNamespace(
                        temperature_c=temperature_past_the_crossing,
                        data=None)
                    night.warmed_at = night.rel(rec["t"])
            night.on_capture = on_capture
        try:
            night.done = await night.run(_golden_as_recorded(), wall_s=120.0)
        finally:
            await night.close()
    finally:
        await close_night_hub(hub, popped)
    assert night.done, f"premise: the run ended: {night.trace[-3:]}"
    return night, rows


def _ahead(rows):
    return [r for r in rows if _hours_to_flip(r["ra"], r["t"]) > 0]


def _past(rows):
    return [r for r in rows if _hours_to_flip(r["ra"], r["t"]) <= 0]


async def test_past_the_crossing_no_state_carries_the_live_block(
        group_store, monkeypatch):
    """Past the crossing `_live_block` builds nothing, so no state published
    there carries a ``live`` key at all: not the last chip, and not an
    empty object either. The CONTROL is the countdown itself: every state
    published inside the warn window before the crossing carries ``live``
    with exactly the chip the night's clock gives (820 s at 9960 s down to
    5 s at 10772.28 s), and every state before the window carries none.

    MUTANT "live merged, never popped": RED (observed), the last chip, 5 s,
    riding all 27 states past the crossing:
        AssertionError: 27 states published past the crossing carried a
        live block, the first (t, live): [(10777.28, {'meridian_eta_s':
        5}), (10782.28, {'meridian_eta_s': 5}), (10785.603,
        {'meridian_eta_s': 5})]
    """
    night, rows = await _crossing_night(monkeypatch)
    assert night.said("meridian flip complete"), "premise: the night flips"
    past = _past(rows)
    assert past, "premise: the night runs on past the crossing"
    carried = [(night.rel(r["t"]), r["live"]) for r in past
               if r["live"] is not None]
    assert carried == [], (
        f"{len(carried)} states published past the crossing carried a live "
        f"block, the first (t, live): {carried[:3]}")

    ahead = _ahead(rows)
    inside = [r for r in ahead if _chip(_hours_to_flip(r["ra"], r["t"]))]
    before = [r for r in ahead if not _chip(_hours_to_flip(r["ra"], r["t"]))]
    assert inside and before, "premise: the night enters the window"
    wrong = [(night.rel(r["t"]), r["live"]) for r in inside
             if r["live"] != {"meridian_eta_s":
                              _chip(_hours_to_flip(r["ra"], r["t"]))}]
    assert wrong == [], (
        f"control: {len(wrong)} states inside the warn window did not "
        f"carry the countdown, the first (t, live): {wrong[:3]}")
    early = [(night.rel(r["t"]), r["live"]) for r in before
             if r["live"] is not None]
    assert early == [], f"control: a live block before the window: {early[:3]}"
    assert (night.rel(inside[0]["t"]), inside[0]["live"]) == (
        9960.0, {"meridian_eta_s": 820})
    assert (night.rel(inside[-1]["t"]), inside[-1]["live"]) == (
        10772.28, {"meridian_eta_s": 5})


async def test_a_live_block_with_a_temperature_still_rides_past_the_crossing(
        group_store, monkeypatch):
    """CONTROL. The clear is keyed on `_live_block` returning None, not on
    the chip or the crossing: once the camera reports a sensor temperature
    (from the first capture past the crossing), every later state carries
    ``live`` with that temperature, and no chip.

    GREEN under "live merged, never popped" (observed): a block with a
    temperature replaces the whole object, which is how a rig whose camera
    reports one masked #422. This case holds that the fix did not trade the
    stale chip for dropping a block that has something in it.
    MUTANT "cleared with the chip" (``if live and "meridian_eta_s" in
    live:``, so a block is published only while it holds a chip, and
    cleared otherwise): RED (observed), all 23 states after the temperature
    arrived carrying no block:
        AssertionError: [(10965.603, None), (10965.603, None), (10965.603,
        None)]
        assert [(10965.603, ...3, None), ...] == []
          Left contains 23 more items, first extra item: (10965.603, None)
    """
    night, rows = await _crossing_night(monkeypatch,
                                        temperature_past_the_crossing=-10.0)
    assert getattr(night, "warmed_at", None) is not None, (
        "premise: a capture past the crossing gave the camera a temperature")
    after = [r for r in _past(rows) if night.rel(r["t"]) > night.warmed_at]
    assert after, "premise: states were published after the temperature"
    wrong = [(night.rel(r["t"]), r["live"]) for r in after
             if r["live"] != {"sensor_temp_c": -10.0}]
    assert wrong == [], wrong[:3]
