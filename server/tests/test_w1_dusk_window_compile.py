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

A KNOWN RESIDUAL, DELIBERATELY NOT CLOSED HERE (documented, not silently
dropped - the project's own convention for a gap a WP cannot reach, e.g.
nodeDefs.test.ts's "KNOWN PROTOTYPE BUG, reproduced on purpose"). The three
sun-based Start choices (Astro/Nautical/Civil dusk) still all compile to the
one rig-wide ``start_mode: "dusk"``: telling them apart needs a per-target
twilight angle on ``Schedule`` (sequence/models.py), a file outside this
work package's list. ``test_sun_based_starts_still_share_one_dusk_mode``
below is that control, pinned on purpose so the day someone adds the field
and starts distinguishing them, this test is the one that says so.

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


def test_sun_based_starts_still_share_one_dusk_mode():
    """KNOWN RESIDUAL (see module docstring): Astro, Nautical and Civil dusk
    all compile identically, because ``Schedule`` has no per-target
    twilight angle yet. This is the issue's own "test that shows it red"
    for the FULL fix; WP-09 closes the Clock-time and Stop halves only, not
    this one, since the field it needs lives in a file this WP does not
    own (sequence/models.py). Pinned so the day that field exists, this
    test - not a silent drift - is what asks to be rewritten."""
    astro = _schedule(_dusk_graph(start="Astro dusk"))
    nautical = _schedule(_dusk_graph(start="Nautical dusk"))
    civil = _schedule(_dusk_graph(start="Civil dusk"))
    assert astro == nautical == civil
    assert astro["start_mode"] == "dusk"


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
    depends on this: a DUSK WINDOW that never picks "Clock time" must
    compile to the exact dict shape it always did, in the same key order,
    with no ``start_time``/``stop_time`` key at all (absent, not blank -
    the same reading ``mosaic`` gives a 1x1 TARGET).

    Mutant "start_time always written" (``_dusk_schedule`` sets
    ``start_time`` unconditionally): RED, observed:
        assert ['start_mode', 'start_offset_min', 'start_time', 'stop_mode',
        'min_altitude_deg'] == ['start_mode', 'start_offset_min',
        'stop_mode', 'min_altitude_deg']
    """
    sched = _schedule(_dusk_graph(start="Astro dusk", stop="Dawn"))
    assert list(sched.keys()) == [
        "start_mode", "start_offset_min", "stop_mode", "min_altitude_deg"]
    assert "start_time" not in sched
    assert "stop_time" not in sched


# ------------------------------------------------------------- no dusk node

def test_a_flow_with_no_dusk_node_still_runs_now():
    graph = FlowGraph(nodes=[])
    assert compile_plan(graph)["schedule"] == {"start_mode": "now"}
