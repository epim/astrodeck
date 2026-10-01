"""DUSK WINDOW's Start and Stop choices reach the compiled schedule (#191).

THE DEFECT. The compile wrote ``start_mode: "dusk"`` for every DUSK WINDOW
whatever its Start said, and mapped every Stop but the literal string
"Dawn" to ``stop_mode: "none"`` - so a Stop of "Clock time" ran with none at
all, silently, and a Stop of "None" gave no hint that the run would keep
imaging into daylight and never park.

THE FIX (``compile._dusk_schedule``). "Clock time" for either Start or Stop
now reaches its own ``Schedule`` mode ("time") with the card's own "HH:MM"
text as ``start_time``/``stop_time``. A Stop of "None" (or anything this
build does not recognise) still compiles to ``stop_mode: "none"``, but now
with a compile warning naming the consequence, in ``compiled["notes"]``.

THE RESIDUAL IS NOW CLOSED (backlog WP-09, #191, 2026-09-30). The three
sun-based Start choices (Astro/Nautical/Civil dusk) used to all compile to
the one rig-wide ``start_mode: "dusk"``, indistinguishable from each other,
because telling them apart needed a per-target twilight angle on
``Schedule`` (sequence/models.py) that did not exist yet.
``test_sun_based_starts_still_share_one_dusk_mode`` was this file's own
control, pinned on purpose so the day someone added the field and started
distinguishing them, that test would be the one to rewrite. That day is
this one: ``Schedule.twilight_deg`` now carries a PER-TARGET Sun altitude
(None means the rig's own ``safety.twilight_deg``, so a plan or a saved
flow from before this field existed is unaffected), ``_dusk_schedule``
compiles it from the Start choice (astro -18, nautical -12, civil -6), and
``schedule.resolve_window`` resolves this target's dusk AND dawn boundaries
at its own angle in place of the rig's. The control below is replaced by
``test_the_three_sun_based_starts_now_resolve_three_different_angles``,
which proves the opposite of what it did: that the three choices now
compile three DIFFERENT ``twilight_deg`` values and, through
``schedule.resolve_window``, three different dusk instants.

Mutants were run from a byte backup inside this worktree (never in the
shared tree), and each failure is quoted verbatim.
"""
from __future__ import annotations

from astrodeck.flows.compile import compile_plan
from astrodeck.flows.models import FlowGraph, FlowNode


def _dusk_graph(start="Astro dusk", stop="Dawn", *, offset=-30, min_alt=30,
               start_clock="", stop_clock=""):
    return FlowGraph(nodes=[FlowNode(id="d1", type="dusk", params={
        "start": start, "offset": offset, "startClock": start_clock,
        "stop": stop, "stopClock": stop_clock, "minAlt": min_alt,
    })])


def _schedule(graph: FlowGraph) -> dict:
    return compile_plan(graph)["schedule"]


# ------------------------------------------------------------------- START

def test_clock_time_start_reaches_start_mode_and_a_start_time():
    """Before this fix EVERY Start compiled to ``start_mode: "dusk"``, so
    picking "Clock time" silently kept the sun-based arming and there was no
    field to even hold a time.

    Mutant "Start match dropped" (``_dusk_schedule``'s ``start_mode``
    always ``"dusk"``, the code before this fix): RED, observed:
        assert 'dusk' == 'time'
    """
    sched = _schedule(_dusk_graph(start="Clock time", start_clock="20:15"))
    assert sched["start_mode"] == "time"
    assert sched["start_time"] == "20:15"


def test_the_three_sun_based_starts_now_resolve_three_different_angles():
    """Backlog WP-09 (#191, 2026-09-30) replaces
    ``test_sun_based_starts_still_share_one_dusk_mode``: Astro, Nautical and
    Civil dusk now compile three different ``twilight_deg`` values, the
    standard astronomical figures, and each resolves its OWN dusk instant
    through ``schedule.resolve_window`` (later at night for astro than for
    civil, since the sun must sink further), not the one rig-wide instant
    every choice silently shared before.

    RED under mutant "one angle for all three" (``_DUSK_START_TWILIGHT_DEG``
    made a single float instead of a per-choice table), observed:

        AssertionError: assert -12.0 == -18.0
    """
    astro = _schedule(_dusk_graph(start="Astro dusk"))
    nautical = _schedule(_dusk_graph(start="Nautical dusk"))
    civil = _schedule(_dusk_graph(start="Civil dusk"))
    assert astro["start_mode"] == nautical["start_mode"] == civil[
        "start_mode"] == "dusk"
    assert (astro["twilight_deg"], nautical["twilight_deg"],
            civil["twilight_deg"]) == (-18.0, -12.0, -6.0)

    from astrodeck.sequence.models import Schedule
    from astrodeck.sequence.schedule import resolve_window
    site = {"latitude": 40.0, "longitude": -74.0, "is_default": False}
    t_now = 1_800_000_000.0
    starts = {}
    for choice, sched in (("astro", astro), ("nautical", nautical),
                          ("civil", civil)):
        target_sched = Schedule(
            start_mode=sched["start_mode"],
            start_offset_min=sched["start_offset_min"],
            twilight_deg=sched["twilight_deg"],
            stop_mode=sched["stop_mode"],
            min_altitude_deg=sched["min_altitude_deg"])
        start_ts, _stop_ts = resolve_window(target_sched, site, -12.0, t_now)
        starts[choice] = start_ts
    # Astro dusk (sun -18) falls after nautical (-12), which falls after
    # civil (-6): the sun keeps sinking, so each later choice's instant is
    # later in the evening.
    assert starts["civil"] < starts["nautical"] < starts["astro"]


def test_a_start_with_no_clock_time_set_compiles_to_blank_text():
    # "Clock time" chosen but the card left blank: no crash, no invented
    # time - `_resolve_event_ts`'s mode "time" already treats an empty
    # string as unresolved (the same as a polar dusk).
    sched = _schedule(_dusk_graph(start="Clock time", start_clock=""))
    assert sched["start_mode"] == "time"
    assert sched["start_time"] == ""


# -------------------------------------------------------------------- STOP

def test_dawn_stop_is_unchanged():
    sched = _schedule(_dusk_graph(stop="Dawn"))
    assert sched["stop_mode"] == "dawn"
    assert "stop_time" not in sched


def test_clock_time_stop_reaches_stop_mode_and_a_stop_time():
    """BEFORE THIS FIX a Stop of "Clock time" fell through the same branch
    as "None" (only the literal string "Dawn" was ever matched), so a flow
    the operator gave a real stop time ran with no stop at all.

    Mutant "Stop match narrowed to just Dawn" (the code before this fix,
    ``"dawn" if stop == "Dawn" else "none"``): RED, observed:
        assert 'none' == 'time'
    """
    sched = _schedule(_dusk_graph(stop="Clock time", stop_clock="05:30"))
    assert sched["stop_mode"] == "time"
    assert sched["stop_time"] == "05:30"


def test_none_stop_gets_a_compile_warning():
    """#191's other half: a Stop of "None" compiles to no stop, as it always
    has, but now says so instead of leaving the operator to find out at
    dawn that the run is still imaging.

    Mutant "the warning is never appended" (``_dusk_schedule``'s
    ``notes.append`` call deleted): RED, observed:
        assert [] == [{'node_id': 'd1', 'level': 'warn', ...}]
    """
    compiled = compile_plan(_dusk_graph(stop="None"))
    assert compiled["schedule"]["stop_mode"] == "none"
    notes = compiled.get("notes") or []
    warn = [n for n in notes if n["node_id"] == "d1"]
    assert len(warn) == 1, notes
    assert warn[0]["level"] == "warn"
    assert "does not park at dawn" in warn[0]["text"]


def test_dawn_and_clock_time_stops_get_no_warning():
    for stop, kw in (("Dawn", {}), ("Clock time", {"stop_clock": "05:30"})):
        compiled = compile_plan(_dusk_graph(stop=stop, **kw))
        notes = compiled.get("notes") or []
        assert not [n for n in notes if n["node_id"] == "d1"], (stop, notes)


# --------------------------------------------------------------- key order

def test_a_flow_using_neither_clock_choice_compiles_the_old_four_keys_in_order():
    """test_flows_compile.py's byte-identical guard over the Examples
    depends on the first half of this: a DUSK WINDOW that never picks
    "Clock time" keeps the same four keys in the same order, with no
    ``start_time``/``stop_time`` key at all (absent, not blank - the same
    reading ``mosaic`` gives a 1x1 TARGET).

    Mutant "start_time always written" (``_dusk_schedule`` sets
    ``start_time`` unconditionally): RED, observed:
        assert ['start_mode', 'start_offset_min', 'start_time', 'stop_mode',
        'min_altitude_deg'] == ['start_mode', 'start_offset_min',
        'stop_mode', 'min_altitude_deg']

    A SUN-BASED START NO LONGER STOPS AT FOUR (backlog WP-09, #191,
    2026-09-30): Astro dusk's own ``twilight_deg`` is a fifth key, appended
    after the four, so the "old four keys" promise now holds only for a
    Start this build does not resolve to an angle at all (a hand-edited
    file with an unrecognised value) - the CONTROL below.
    """
    sched = _schedule(_dusk_graph(start="Astro dusk", stop="Dawn"))
    assert list(sched.keys()) == [
        "start_mode", "start_offset_min", "stop_mode", "min_altitude_deg",
        "twilight_deg"]
    assert "start_time" not in sched
    assert "stop_time" not in sched

    # CONTROL: a Start this build does not recognise (a hand-edited file)
    # still compiles the bare four keys, no `twilight_deg` guessed for it.
    unknown = _schedule(_dusk_graph(start="Some future choice", stop="Dawn"))
    assert list(unknown.keys()) == [
        "start_mode", "start_offset_min", "stop_mode", "min_altitude_deg"]


# ------------------------------------------------------------- no dusk node

def test_a_flow_with_no_dusk_node_still_runs_now():
    graph = FlowGraph(nodes=[])
    assert compile_plan(graph)["schedule"] == {"start_mode": "now"}
