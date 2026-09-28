"""The roof reopen's cooler gate waits UNDER THE CLOSED ROOF, and the roof
opens only on a safety verdict read after that wait (S4 safety review of item
11, spec 5.8).

THE DEFECT. Item 11 gave the auto-reopen release (`_await_safe_and_reopen`) a
cooler gate, and placed it just AFTER ``dome.open_shutter()``. The gate reads
the sensor and nothing else: `_cool_and_wait` makes no safety read, and waits
up to ``cool_timeout_s`` (600 s by default) for the band and
``COOLER_SETTLE_MAX_S`` (900 s) more to settle in it. Nothing else watches the
weather while it waits: the engine has no safety task beside the run, and the
setup's pre-slew gate comes only after it. So a sensor that drifted while the
roof was shut, which is exactly the case the gate was added for, held the
roof OPEN over a parked rig for up to 25 minutes with nobody reading the rain
sensor, and rain that came back in that time fell on the gear. That is the
dominant class this project keeps finding: a guard that rides the work's
clock (the cooler's) while the hazard (the weather) runs on its own.

NOW the gate runs while the roof is still closed, before the "reopening roof"
line, and the weather is read again when it returns: a verdict the wait
outlived is not the one the roof opens on. Unsafe by then, the roof stays
closed, the streak starts again, and the loop waits as it did before the
streak.

THE HARNESS is test_engine_dome_close.py's simulator hub, with its roof, and
the safety readings scripted once the roof has closed, as its debounce case
scripts them. The cooler gate is replaced by a spy that records whether the
roof was open while it ran and, in the rain case, turns the weather unsafe
for the two reads after it, as a sensor that took a while to settle would
have outlived a passing clear spell. The fixture site is 40 N 74 W, nobody's
rig.

Each case names the mutant it was shown RED under, with the failure observed,
verbatim. The mutant was applied in a private scratch copy of server/
(scratchpad s4safety-review-mut), never in the shared tree (#254).
"""
from __future__ import annotations

from astrodeck.devices.base import DomeShutterState, SafetyReading
from astrodeck.sequence import SequenceEngine
from test_engine_dome_close import (_spy_shutter, force_cached_unsafe,  # noqa: F401
                                    light_plan, set_safety, sim_hub,
                                    temp_store, wait_for)

SAFE = SafetyReading(is_safe=True, source="Sim Safety Monitor")
UNSAFE = SafetyReading(is_safe=False, reason="rain sensor",
                       source="Sim Safety Monitor")
WHY = "resumed after a roof reopen"


async def _closed_and_scripted(sim_hub, temp_store, *, rain_in_gate: int):
    """Start a run whose first safety read trips a roof close, wait until the
    roof is shut and the release loop is waiting, then script it: every read
    SAFE, except ``rain_in_gate`` reads UNSAFE straight after each cooler
    gate the release runs. Returns the engine, the shutter spy's events and
    the record: at each cooler gate, the shutter state and the number of
    safety reads taken so far; at the open, the reads taken and the last
    reading handed out."""
    dome = sim_hub.devices.get("dome")
    events = _spy_shutter(dome)
    set_safety(temp_store, enabled=True, on_unsafe="pause",
               unsafe_consecutive=1, resume_safe_consecutive=1,
               max_pause_min=0, min_alt_deg=0.0, close_dome_on_unsafe=True,
               reopen_dome_when_safe=True)
    force_cached_unsafe(sim_hub, "rain sensor")
    engine = SequenceEngine(sim_hub)
    engine.start(light_plan())
    assert await wait_for(lambda: "close" in events
                          and engine.state.get("state") == "paused"), (
        f"premise: the trip closed the roof and the release waits: "
        f"{events} {engine.state}")

    rec: dict = {"gates": [], "opened": None, "reads": 0, "last": None,
                 "rain": 0}

    async def scripted_read():
        rec["reads"] += 1
        if rec["rain"] > 0:
            rec["rain"] -= 1
            rec["last"] = UNSAFE
        else:
            rec["last"] = SAFE
        return rec["last"]

    async def cooler_gate(why, **_kw):
        rec["gates"].append((why, await dome.shutter_state(), rec["reads"]))
        if why == WHY and len(rec["gates"]) == 1:
            # The sensor took its time, and the weather turned meanwhile.
            rec["rain"] = rain_in_gate

    spy_open = dome.open_shutter

    async def open_shutter():
        rec["opened"] = (rec["reads"], rec["last"])
        await spy_open()

    dome.open_shutter = open_shutter
    engine._read_safety = scripted_read
    engine._cooler_gate = cooler_gate
    return engine, events, rec


async def test_the_reopen_cooler_waits_under_the_closed_roof(
        sim_hub, temp_store):
    """Rain comes back while the sensor settles. The gate ran with the roof
    CLOSED, and when it returned the weather was read again, found unsafe,
    and the roof stayed shut: no open until the reads were safe again, when
    the release ran the gate once more (the roof still closed) and opened on
    a safe read taken after it. The run then re-acquires and completes.

    Mutant "the cooler gate after the roof opens" (S4's order, exactly as
    item 11 first built it: the ``_cooler_gate`` call after the reopen's
    record and publish, and no safety read after it): RED (observed) -
        AssertionError: the cooler gate ran with the roof open, where no
        safety read is taken for up to cool_timeout_s + COOLER_SETTLE_MAX_S:
        [('resumed after a roof reopen', <DomeShutterState.OPEN: 'open'>,
        1), ('resumed after a roof reopen', <DomeShutterState.OPEN:
        'open'>, 4)]
    The second entry is the same rain seen from the open roof: it was
    caught only by the setup's pre-slew gate after the wait, which shut the
    roof and reopened it a second time.
    """
    dome = sim_hub.devices.get("dome")
    engine, events, rec = await _closed_and_scripted(
        sim_hub, temp_store, rain_in_gate=2)
    assert await wait_for(lambda: engine.state.get("state") == "complete",
                          timeout=40), engine.state
    gates = [g for g in rec["gates"] if g[0] == WHY]
    assert gates, f"premise: the reopen ran its cooler gate: {rec['gates']}"
    open_during = [g for g in gates if g[1] is not DomeShutterState.CLOSED]
    assert open_during == [], (
        f"the cooler gate ran with the roof open, where no safety read is "
        f"taken for up to cool_timeout_s + COOLER_SETTLE_MAX_S: "
        f"{open_during}")
    assert len(gates) == 2, (
        f"the rain after the first gate must send the release back to "
        f"waiting, and the next safe streak gates the cooler again: {gates}")
    reads_at_open, last = rec["opened"]
    assert last is SAFE, (
        f"the roof opened on {last}, not on a safe read taken after the "
        f"cooler gate")
    assert reads_at_open > gates[-1][2], (
        f"the roof opened on a read taken before the last cooler gate "
        f"(reads {reads_at_open}, gate at {gates[-1][2]})")
    assert events == ["close", "open"], events
    assert await dome.shutter_state() is DomeShutterState.OPEN


async def test_control_a_steady_sky_reopens_once_after_one_gate(
        sim_hub, temp_store):
    """CONTROL. The sky stays safe through the gate, so the order changes
    nothing that can be seen from outside: one cooler gate, one open, on a
    safe read, within one read of the gate, and the run completes. It passes
    under the mutant above as well (observed), which is what makes the case
    above a test of the order and not of the reopen: only rain inside the
    gate tells the two apart. The reorder costs one safety read, no poll.
    """
    dome = sim_hub.devices.get("dome")
    engine, events, rec = await _closed_and_scripted(
        sim_hub, temp_store, rain_in_gate=0)
    assert await wait_for(lambda: engine.state.get("state") == "complete",
                          timeout=40), engine.state
    gates = [g for g in rec["gates"] if g[0] == WHY]
    assert len(gates) == 1, gates
    reads_at_open, last = rec["opened"]
    assert last is SAFE and abs(reads_at_open - gates[0][2]) <= 1, (
        f"the roof must open on the read beside the one gate: reads "
        f"{reads_at_open}, gate at {gates[0][2]}, last {last}")
    assert events == ["close", "open"], events
    assert await dome.shutter_state() is DomeShutterState.OPEN
