# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""Set aside for tonight survives a crash, and only tonight (#208, #147 part
2, #189 S2; spec 3.4, 6.7).

A set-aside used to live in engine memory. A restart or an auto-resume the
same night is a new run on the same session, so it came back empty and shot,
all over again, the filter whose reject guard had just tripped, the target
that had just sunk below its floor, the panel the group driver had just
given up on. Now each is a ``Session.set_aside`` record carrying the night
key it was made under, saved the moment it is made; `start()` reads back
the records for tonight's key and a later night reads none.

Each case runs the real engine on the clocked simulator
(tests/_group_harness.py) for one night, then starts the SAME session again
on a fresh engine, as a restart does: an hour later (the same night) or a
day later (the next). The three kinds of set-aside, single targets
included: the per-step reject guard, a floor advance, a panel set aside by
the group driver.

T16 cites these tests by name. Every mutant was applied in a private scratch
copy of server/ (#254), and its observed failure is recorded verbatim
(pytest's own lines, long ones wrapped).
"""
from __future__ import annotations

import pytest

import astrodeck.sequence.engine as engine_mod
from _group_harness import (GROUP_NAME, T0, Night, grid_plan, group_hub,
                            group_store, single)
from astrodeck.events import night_key
from astrodeck.sequence.models import Schedule, SequencePlan
from astrodeck.sequence.session import session_store

LATER_TONIGHT = T0 + 3600.0
NEXT_NIGHT = T0 + 86400.0


@pytest.fixture(autouse=True)
def _nights_are_what_this_file_says():
    """Premise, on the machine's own time zone: an hour after ``T0`` is the
    same observing night, a day after is the next."""
    assert night_key(T0) == night_key(LATER_TONIGHT) != night_key(NEXT_NIGHT)


async def _night(hub, monkeypatch, plan, *, t0=T0, session=None,
                 on_capture=None, **kw) -> Night:
    night = Night(hub, monkeypatch, t0=t0, **kw)
    night.on_capture = on_capture
    try:
        night.done = await night.run(
            plan, **({"session": session} if session is not None else {}))
    finally:
        await night.close()
    night.stored = session_store.load(night.session_id)
    return night


# ------------------------------------------------------ the step reject guard

def _ha_plan() -> SequencePlan:
    """One accepted-mode cycle target, B, Ha and OIII, three of each, a step
    guard of 3; Ha's frames carry no stars and are rejected."""
    return SequencePlan(
        name="set-aside", guide=False, dither_every=0, autofocus_every=0,
        meridian_flip=False, park_when_done=False, warm_cooler_when_done=False,
        count_mode="accepted", min_stars=5, max_consecutive_rejects=3,
        max_consecutive_rejects_night=12,
        targets=[single("Solo", filters=("B", "Ha", "OIII"), count=3)])


def _no_stars_in_ha(who, filt):
    return 0 if filt == "Ha" else 50


async def _ha_set_aside(hub, monkeypatch) -> Night:
    """Night one: Ha is set aside after three rejects, B and OIII complete.
    Also checks the record was SAVED AT ONCE: the stored session holds it at
    the next frame, not only after the run's final save."""
    saved: list[list[dict]] = []

    def look(rec):
        if rec["filter"] == "OIII":
            saved.append(session_store.load(night.session_id).set_aside)

    night = Night(hub, monkeypatch, stars=_no_stars_in_ha)
    night.on_capture = look
    try:
        assert await night.run(_ha_plan())
    finally:
        await night.close()
    night.stored = session_store.load(night.session_id)
    assert night.shots().count(("Solo", "Ha")) == 3, night.shots()
    records = night.stored.set_aside
    assert [(r["target_id"], r["step_id"]) for r in records] == [
        ("solo", "solo-Ha")], records
    assert records[0]["night"] == night_key(T0), records
    assert saved and saved[-1] == records, (
        f"the record was not in the stored session while the run went on: "
        f"{saved[-1:]}")
    assert night.stored.status == "dormant"
    return night


async def test_step_set_aside_not_retried_same_night(group_hub, monkeypatch):
    """A restart an hour later, on the same session: Ha, set aside tonight
    by its reject guard, is not shot again, and since it is all the target
    still owes, the target is not even slewed to; the line says why.

    MUTANT "engine memory only" (`start()` no longer seeds ``_set_aside``
    and ``_set_aside_targets`` from ``Session.set_aside``): the restart
    retries it the same night. RED (observed):
        AssertionError: [('Solo', 'Ha'), ('Solo', 'Ha'), ('Solo', 'Ha')]
        assert [('Solo', 'Ha...'Solo', 'Ha')] == []
    MUTANT "not saved at once" (`_persist_set_aside` notes the record but
    does not save the session): RED on night one's check (observed):
        AssertionError: the record was not in the stored session while the run
        went on: [[]]
        assert ([[], [], []] and [] == [{'night': '2..._id': 'solo'}]
    """
    first = await _ha_set_aside(group_hub, monkeypatch)
    again = await _night(group_hub, monkeypatch, _ha_plan(), t0=LATER_TONIGHT,
                         session=first.stored, stars=_no_stars_in_ha)
    assert again.done
    assert again.shots() == [], again.shots()
    assert again.gotos == [], "the restart slewed to a target owing nothing tonight"
    assert again.said("Solo: set aside earlier tonight ("), again.lines[-4:]


async def test_step_set_aside_retried_next_night(group_hub, monkeypatch):
    """The next night, on the same session: the record is last night's, so
    Ha is tried again, and its guard starts afresh at three."""
    first = await _ha_set_aside(group_hub, monkeypatch)
    nxt = await _night(group_hub, monkeypatch, _ha_plan(), t0=NEXT_NIGHT,
                       session=first.stored, stars=_no_stars_in_ha)
    assert nxt.done
    assert nxt.shots() == [("Solo", "Ha")] * 3, nxt.shots()
    assert [r["night"] for r in nxt.stored.set_aside] == [
        night_key(T0), night_key(NEXT_NIGHT)], nxt.stored.set_aside


# ------------------------------------------------------------- a floor advance

def _floor_plan() -> SequencePlan:
    t = single("Sinker", filters=("L",), count=2)
    t.schedule = Schedule(min_altitude_deg=30.0, on_floor="advance")
    return SequencePlan(name="floor", guide=False, dither_every=0,
                        autofocus_every=0, meridian_flip=False,
                        park_when_done=False, warm_cooler_when_done=False,
                        targets=[t, single("Other", count=1, ha_h=-2.0)])


async def test_floor_set_aside_persisted_for_tonight(group_hub, monkeypatch):
    """A single target with ``on_floor = advance`` reads below its floor at
    its first frame: it is set aside, and the record, with tonight's night
    key and no step, is what a restart an hour later reads, so it is not
    slewed to again tonight. The next night it is taken up again.

    MUTANT "engine memory only": the restart slews to it and tries it the
    same night. RED (observed):
        AssertionError: [(1788317289.0, 'Sinker')]
        assert ['Sinker'] == []
    MUTANT "the floor is not recorded" (the `_persist_set_aside` call in the
    scheduler's FloorStop arm deleted): RED (observed):
        AssertionError: []
        assert [] == [('sinker', N...'2026-09-01')]
    """
    low = {"on": True}
    real = engine_mod._frame_altitude
    monkeypatch.setattr(
        engine_mod, "_frame_altitude",
        lambda t, site, when: (10.0 if low["on"] and t.name == "Sinker"
                               else real(t, site, when)))
    first = await _night(group_hub, monkeypatch, _floor_plan())
    assert first.done
    assert [t for t, _f in first.shots()] == ["Other"], first.shots()
    records = first.stored.set_aside
    assert [(r["target_id"], r["step_id"], r["night"]) for r in records] == [
        ("sinker", None, night_key(T0))], records

    again = await _night(group_hub, monkeypatch, _floor_plan(),
                         t0=LATER_TONIGHT, session=first.stored)
    assert again.done
    assert [who for _t, who in again.gotos] == [], again.gotos
    assert again.said("Sinker: set aside earlier tonight (sank below its own "
                      "altitude floor)"), again.lines[-4:]

    low["on"] = False
    nxt = await _night(group_hub, monkeypatch, _floor_plan(), t0=NEXT_NIGHT,
                       session=again.stored)
    assert nxt.done
    assert [t for t, _f in nxt.shots()] == ["Sinker", "Sinker"], nxt.shots()
    assert nxt.stored.owed() == 0


# ----------------------------------------------------------- a panel set aside

def _never_centres(label):
    def goto(who, n, result):
        if who == f"{GROUP_NAME} {label}":
            return {**result, "centered": False, "error_arcmin": None}
        return result
    return goto


async def test_panel_set_aside_not_retried_same_night(group_hub, monkeypatch):
    """Panel 2-2 never centres and is set aside after its third deferral; the
    other three complete. A restart an hour later reads the panel's record
    and does not hop to it again tonight: nothing is left to shoot, and the
    run ends at once. The next night, where it centres, it is shot.

    MUTANT "engine memory only": the restart hops to 2-2 three times more
    the same night. RED (observed):
        AssertionError: ['M31 2-2', 'M31 2-2', 'M31 2-2']
        assert [(1788317289....0, 'M31 2-2')] == []

    RE-PINNED FOR H4 (#534, H4 orchestrator ruling 2). 2-2's misses are its
    own (its neighbours centre), but a streak of centring misses now sets it
    aside "for now": the set-aside expires once a night, 45 minutes on,
    2-2 strikes out again, and only then is it set aside for the night. So
    the first night leaves two records for tonight, the first marked
    expired, where it left one before H4. The restart honours the second
    and hops nowhere, as before; what this case grades did not move.
    """
    first = await _night(group_hub, monkeypatch, grid_plan(),
                         goto=_never_centres("2-2"))
    assert first.done
    records = first.stored.set_aside
    assert [(r["target_id"], r["step_id"], r["night"], bool(r.get("expired")))
            for r in records] == [
        ("p11", None, night_key(T0), True),
        ("p11", None, night_key(T0), False)], records

    again = await _night(group_hub, monkeypatch, grid_plan(), t0=LATER_TONIGHT,
                         session=first.stored, goto=_never_centres("2-2"))
    assert again.done
    assert again.gotos == [], [who for _t, who in again.gotos]
    assert again.stored.status == "dormant"

    nxt = await _night(group_hub, monkeypatch, grid_plan(), t0=NEXT_NIGHT,
                       session=again.stored)
    assert nxt.done
    assert [who for _t, who in nxt.gotos] == [f"{GROUP_NAME} 2-2"] * 3, (
        nxt.gotos)
    assert nxt.stored.owed() == 0 and nxt.stored.status == "complete"
