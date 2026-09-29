"""The open-sky pause and the roof reopen publish "safe again" only once the
cooler gate has passed, and the gate's wait reads the safety monitor on its
own clock (#448, S4 review item 9; spec 5.8).

S4 item 11 put the cooler gate into both safety releases, and the gate reads
the sensor and nothing else: `_cool_and_wait` waits up to ``cool_timeout_s``
for the band and ``COOLER_SETTLE_MAX_S`` more to settle, 25 minutes at the
defaults, with no safety read. The open-sky pause (`_park_hold_pause`) said
"conditions safe again", recorded the resume, published the safe verdict
and reset the unsafe streak BEFORE that wait, so rain that came back while
a drifted sensor settled was never seen by the pause: every screen and alert
sink had been told the weather was safe, the report said the pause was over,
and nothing read the monitor until the next setup's pre-slew gate. The roof
reopen (`_await_safe_and_reopen`) already ran its gate under the closed roof
and read the weather once when it returned (S4 safety review), but inside
the wait it read nothing either: rain that came back and passed again within
the wait was never seen, and the roof opened on the one safe read after it,
where INVARIANT 4 asks for ``resume_safe_consecutive`` in a row. The "safety
rides value paths" class: the weather check rode the cooler's clock while
the hazard, the weather, runs on its own.

NOW, when a safety release runs it, the gate's wait reads the monitor every
``SAFETY_PAUSE_POLL_S`` on a clock of its own: the sensor is waited for on a
task of its own, and a temperature read that hangs for ``COOLER_CMD_TIMEOUT_S``
stretches nothing. The first read that is not safe ends the wait. The pause
publishes its close-out (the line, the report, the verdict, the streak) only
once the gate has passed; a read that is not safe inside the wait resets the
safe streak, publishes nothing, and the release waits as it did before the
streak, the pause still paused and the roof still closed.

THE HARNESS is test_idle_park_hold's clocked simulator with no run. The
release runs on a task of the test's that the driver clocks (``RELEASE``),
and the gate's cooling wait on a task of its own that the test hands to the
driver too (`_clock_the_cooling`), so both clocks are the fake one. The plan
asks for ``COOL_TO``. THE SENSOR is a script on the fake clock: until
``COOL_AT`` every temperature read hangs for ``COOLER_CMD_TIMEOUT_S`` and
times out, a sensor that has not come back; from then on it reads
``COOL_TO``. THE WEATHER is the monitor's cached reading, a script on the
fake clock, given per case as the spells that read unsafe. Every safety read,
every published safety verdict, every report event, every line (with its
source) and every published engine state is recorded with its fake time. The
site is a fixture, never the real one.

Each case names the mutant it was shown RED under, with the failure
observed, verbatim. Every mutant was applied in a private scratch copy of
server/ (S5-ENG-SAFE-mut, in the session scratchpad), never in the shared
tree (#254).
"""
from __future__ import annotations

import asyncio

import pytest

import astrodeck.sequence.engine as engine_mod
from astrodeck import events
from astrodeck.config import AppConfig, SafetyConfig
from astrodeck.devices.base import DomeShutterState, SafetyReading

from test_idle_park_hold import (_Clocked, _plan, _target,  # noqa: F401
                                 sim_hub, temp_store)

POLL_S = engine_mod.SAFETY_PAUSE_POLL_S
HANG_S = engine_mod.COOLER_CMD_TIMEOUT_S
STABLE_S = engine_mod.COOLER_STABLE_S
#: The gate's poll for the sensor's task, the tolerance on "at once".
STEP = engine_mod.IDLE_STOP_FINISH_POLL_S
COOL_TO = -5.0
#: When the sensor comes back into its band, in fake seconds after the
#: release began. Before it, every temperature read hangs for ``HANG_S``.
COOL_AT = 300.0
#: The task running the release.
RELEASE = "release"
PAUSE_WHY = "resumed after a safety pause"
REOPEN_WHY = "resumed after a roof reopen"


class _Release:
    """One safety release on the clocked simulator: the engine with no run,
    a plan that asks for ``COOL_TO``, the drifted sensor, and the monitor's
    reading unsafe inside each ``(from_s, until_s)`` of ``rain`` (fake
    seconds after ``t0``) and safe outside them. ``roof``: the rig closes the
    roof on unsafe and reopens it when safe. The records are listed in the
    module docstring; ``gates`` holds (why, began, ended, result) of every
    cooler gate."""

    def __init__(self, hub, monkeypatch, *, rain: list[tuple[float, float]],
                 roof: bool, resume_n: int = 1):
        self.run = _Clocked(hub, monkeypatch, horizon_s=3600.0)
        run = self.run
        engine = self.engine = run.engine
        engine._cfg = AppConfig(safety=SafetyConfig(
            enabled=True, on_unsafe="pause", unsafe_consecutive=1,
            resume_safe_consecutive=resume_n, max_pause_min=0,
            sky_fallback_hold=False, close_dome_on_unsafe=roof,
            reopen_dome_when_safe=roof))
        plan = _plan(_target("Alpha", 0.0, 40.0))
        plan.cool_to = COOL_TO
        plan.cool_timeout_s = 900
        engine.plan = plan
        self.t0 = run.t0
        self.dome = hub.devices.get("dome")
        self.reads: list[tuple[float, bool]] = []
        self.published: list[tuple[float, dict]] = []
        self.reports: list[tuple[float, str, str]] = []
        self.lines: list[tuple[float, str, str]] = []
        self.states: list[tuple[float, str | None, str | None]] = []
        self.gates: list[list] = []
        self.probes: list[float] = []
        self.opened: list[float] = []

        async def safety_reading():
            t = run.clock.t - self.t0
            wet = any(a <= t < b for a, b in rain)
            self.reads.append((run.clock.t, not wet))
            if wet:
                return SafetyReading(is_safe=False, reason="rain sensor",
                                     source="script", ts=run.clock.t)
            return SafetyReading(is_safe=True, source="script",
                                 ts=run.clock.t)

        monkeypatch.setattr(hub, "safety_reading", safety_reading)
        cam = hub.devices["camera"]
        assert getattr(cam, "can_cool", False), "premise: the sim cools"

        async def get_temperature():
            self.probes.append(run.clock.t)
            if run.clock.t - self.t0 < COOL_AT:
                await run._park(HANG_S)
                raise asyncio.TimeoutError()
            return COOL_TO

        monkeypatch.setattr(cam, "get_temperature", get_temperature)

        def publish_safety(payload):
            self.published.append((run.clock.t, dict(payload)))

        monkeypatch.setattr(hub, "publish_safety", publish_safety)

        def record_safety(reason, action):
            self.reports.append((run.clock.t, reason, action))

        monkeypatch.setattr(engine, "_record_safety", record_safety)

        def log(level, message, source="hub", **_kw):
            self.lines.append((run.clock.t, message, source))

        monkeypatch.setattr(events.bus, "log", log)
        real_set_state = engine._set_state

        def set_state(**kw):
            real_set_state(**kw)
            self.states.append((run.clock.t, engine.state.get("state"),
                                engine.state.get("detail")))

        monkeypatch.setattr(engine, "_set_state", set_state)
        real_gate = engine._cooler_gate

        async def cooler_gate(why, **kw):
            rec = [why, run.clock.t, None, None]
            self.gates.append(rec)
            try:
                rec[3] = await real_gate(why, **kw)
                return rec[3]
            finally:
                rec[2] = run.clock.t

        monkeypatch.setattr(engine, "_cooler_gate", cooler_gate)
        _clock_the_cooling(self, monkeypatch)
        if self.dome is not None:
            real_open = self.dome.open_shutter

            async def open_shutter():
                self.opened.append(run.clock.t)
                await real_open()

            monkeypatch.setattr(self.dome, "open_shutter", open_shutter)

    async def release(self, coro) -> None:
        """Run the release on a task the driver clocks; returns once it has,
        bounded in real time."""
        run = self.run
        task = asyncio.get_running_loop().create_task(coro, name=RELEASE)
        run.also.add(task)
        try:
            await asyncio.wait_for(task, 30.0)
        finally:
            run.also.discard(task)

    def rel(self, t: float | None) -> float | None:
        return None if t is None else round(t - self.t0, 2)

    def safe_published(self) -> list[float]:
        return [self.rel(t) for t, p in self.published
                if p.get("is_safe") is True]

    def said(self, text: str) -> list[float]:
        return [self.rel(t) for t, m, _s in self.lines if m == text]

    def first_safe(self, after: float) -> float | None:
        """Fake seconds after ``t0`` of the first safe read at or after
        ``after``. The pause's reads do not fall on round seconds: its
        opening read-back of its own stop confirms a "not tracking" over
        ``TRACKING_CONFIRM_PROBES`` more reads (`_tracking_now`), which puts
        every poll after it that much later."""
        return next((self.rel(t) for t, ok in self.reads
                     if ok and t - self.t0 >= after), None)

    def read_gaps(self, lo: float, hi: float) -> list[float]:
        """The stretches without a safety read inside fake seconds [lo, hi]
        after ``t0``: from ``lo`` to the first read, between reads, and from
        the last read to ``hi``. A wait that read nothing is one stretch."""
        ts = [lo] + [self.rel(t) for t, _ok in self.reads
                     if lo < t - self.t0 <= hi + 1e-9] + [hi]
        return [round(b - a, 2) for a, b in zip(ts, ts[1:])]


def _clock_the_cooling(r: _Release, monkeypatch) -> None:
    """Hand the gate's cooling wait to the driver, whatever task it runs on.

    The harness clocks the engine's run and idle-stop tasks and the test's
    tasks in ``run.also``. The gate waits for the sensor on a task of its own
    beside the weather reads, so a sleep of that task's (its probe cadence,
    the hung read) would be a real one while the fake clock ran on under the
    reads. So while `_cool_and_wait` runs, its task is clocked. A gate that
    waits for the sensor on the release's own task (the mutants' shape) is
    clocked already, and adding it again changes nothing."""
    run = r.run
    inner = r.engine._cool_and_wait

    async def cool_and_wait(*a, **kw):
        me = asyncio.current_task()
        known = me in run.also
        run.also.add(me)
        try:
            return await inner(*a, **kw)
        finally:
            if not known:
                run.also.discard(me)

    monkeypatch.setattr(r.engine, "_cool_and_wait", cool_and_wait)


# ------------------------------------------------------- the open-sky pause

#: The pause's weather: rain until SAFE_AT, safe until RAIN_AGAIN (the
#: release begins its gate at SAFE_AT), rain again until CLEAR_AT.
SAFE_AT, RAIN_AGAIN, CLEAR_AT = 60.0, 150.0, 450.0


async def test_rain_in_the_pause_s_cooler_gate_is_seen_and_no_safe_published(
        sim_hub, temp_store, monkeypatch):
    """The pause's first safe read comes at ``SAFE_AT``, and its cooler gate
    begins, on a sensor that has not come back: each temperature read hangs
    for 30 s. The rain returns at ``RAIN_AGAIN``, inside that wait. The gate's
    wait reads the monitor every ``SAFETY_PAUSE_POLL_S`` all the same, hung
    reads or not, sees the rain within one cadence and ends; the pause says
    so, goes on paused, and publishes nothing safe. When the sky clears for
    good at ``CLEAR_AT`` the gate runs again, the sensor back in its band by
    then, and only after it passes does the pause close out: one "conditions
    safe again" line, one resume in the report, one safe verdict, all after
    ``CLEAR_AT``. The engine's own state says "paused" until then: a
    "running" state published inside the gate would tell every screen the
    pause was over.

    (The pause's reads fall 4 s past each 5 s mark, `first_safe` says why,
    so its first safe read is at 64 s.)

    Mutant "safe published before the cooler gate" (the pause's close-out,
    its line, its report, its publish and the streak reset, moved back ahead
    of the gate, as S4 built it): RED (observed) -
        AssertionError: the pause published a safe verdict at [64.0, 454.25]
        s, said 'conditions safe again' at [64.0, 454.25] s and reported a
        resume at [64.0, 454.25] s; the rain came back at 150 s and cleared
        at 450 s
    Mutant "cooler gate reads no safety" (``if weather:`` in `_cooler_gate`
    made ``if False:``, so the gate's wait is the plain `_cool_and_wait`
    again, the weather unread): RED (observed) -
        AssertionError: the rain that came back at 150 s was not seen within
        one 5 s cadence: the first gate ran from 64.0 s to 429.0 s (result
        True); safety reads inside it at gaps [365.0]
    Mutant "the monitor read on the sensor's clock" (one safety read per
    sensor probe, inside `_cool_and_wait`'s own loop on the release's task,
    in place of the watch beside it): RED (observed) -
        AssertionError: the rain that came back at 150 s was not seen within
        one 5 s cadence: the first gate ran from 64.0 s to 169.0 s (result
        False); safety reads inside it at gaps [35.0, 35.0, 35.0, 0.0]
    (each probe of the sensor that has not come back holds its read for
    30 s, and the read waits behind it.)
    Mutant "the gate's state is running" (`_cool_and_wait` publishing
    state "running" whatever it is asked): RED (observed) -
        AssertionError: the engine published state 'running' inside the
        pause at [64.0, 94.0, 129.0] s, before the pause had closed out at
        [454.25] s
    """
    r = _Release(sim_hub, monkeypatch, rain=[(0.0, SAFE_AT),
                                             (RAIN_AGAIN, CLEAR_AT)],
                 roof=False)
    try:
        await r.release(r.engine._park_hold_pause("rain sensor", None))
        gates = [g for g in r.gates if g[0] == PAUSE_WHY]
        assert gates, "premise: the pause ran its cooler gate"
        _why, began, ended, result = gates[0]
        assert r.rel(began) == r.first_safe(SAFE_AT), (
            f"premise: the first gate began at the first safe read, "
            f"{r.first_safe(SAFE_AT)} s: {r.rel(began)}")
        hung = [r.rel(t) for t in r.probes if began <= t < ended]
        assert len(hung) >= 2, (
            f"premise: the sensor's reads hung inside the first gate: "
            f"{hung}")
        gaps = r.read_gaps(r.rel(began), r.rel(ended))
        assert r.rel(ended) <= RAIN_AGAIN + POLL_S + STEP and \
            max(gaps) <= POLL_S + 1e-6, (
                f"the rain that came back at {RAIN_AGAIN:g} s was not seen "
                f"within one {POLL_S:g} s cadence: the first gate ran from "
                f"{r.rel(began)} s to {r.rel(ended)} s (result {result}); "
                f"safety reads inside it at gaps {gaps}")
        safe = r.safe_published()
        said = r.said("conditions safe again — resuming")
        resumed = [r.rel(t) for t, _why, act in r.reports if act == "resume"]
        assert all(t >= CLEAR_AT for t in safe + said + resumed) and len(
            safe) == len(said) == len(resumed) == 1, (
                f"the pause published a safe verdict at {safe} s, said "
                f"'conditions safe again' at {said} s and reported a resume "
                f"at {resumed} s; the rain came back at {RAIN_AGAIN:g} s and "
                f"cleared at {CLEAR_AT:g} s")
        assert result is False and len(gates) == 2, (
            f"the first gate did not end on the rain and the pause did not "
            f"gate the cooler again when it cleared: {gates}")
        last = gates[-1]
        assert r.rel(last[1]) >= CLEAR_AT and last[3] is True \
            and last[2] <= min(t for t, p in r.published
                               if p.get("is_safe") is True), (
                f"the safe verdict was not published after the last gate "
                f"passed: gate {last}, published {r.published}")
        turned = [r.rel(t) for t, m, src in r.lines
                  if "weather turned" in m and src == "safety"]
        assert len(turned) == 1 and RAIN_AGAIN <= turned[0] <= RAIN_AGAIN \
            + POLL_S + STEP, (
                f"the pause did not say the rain came back: {r.lines}")
        closed_out = said[0]
        running = [r.rel(t) for t, st, _d in r.states
                   if st == "running" and r.rel(t) < closed_out]
        assert running == [], (
            f"the engine published state 'running' inside the pause at "
            f"{running} s, before the pause had closed out at {said} s")
    finally:
        await r.run.close()


async def test_control_a_steady_sky_publishes_safe_once_the_sensor_settles(
        sim_hub, temp_store, monkeypatch):
    """CONTROL for the watch: the sky stays safe from ``SAFE_AT`` on. The
    gate's wait reads the monitor every ``SAFETY_PAUSE_POLL_S`` and finds it
    safe every time, so it cuts nothing: the gate waits exactly as long as
    the sensor takes, back in its band at the first probe past ``COOL_AT``
    and held there for ``COOLER_STABLE_S``, as it did before #448 (the
    watch's one cost is a poll of up to ``IDLE_STOP_FINISH_POLL_S`` for the
    sensor's task to be seen done), and passes. The pause's close-out comes
    then, at the gate's end, once, and not at the safe read that began the
    gate.

    Mutant "safe published before the cooler gate" (as above): RED
    (observed) -
        AssertionError: the pause closed out at [64.0] s (line), [64.0] s
        (published), before its cooler gate passed at 429.0 s
    Mutant "the watch ends the gate on a safe read" (the watch's test made
    ``if (await self._read_safety()) is not None:``, so any reading ends
    the wait): RED (observed) -
        AssertionError: the gate on a steady sky did not wait for the sensor:
        it ran from 64.0 s to 69.25 s (result False); the sensor settled at
        430 s
    (430, not 429: the settle time is read off the probes, and under this
    mutant every gate was cut at its first read and begun again, so the
    probes fall elsewhere.) Mutant "cooler gate reads no safety" (as above):
    RED here too (observed) -
        AssertionError: the gate's wait on a steady sky went 365 s without a
        safety read; its cadence is 5 s: gaps [365.0]
    """
    r = _Release(sim_hub, monkeypatch, rain=[(0.0, SAFE_AT)], roof=False)
    try:
        await r.release(r.engine._park_hold_pause("rain sensor", None))
        gates = [g for g in r.gates if g[0] == PAUSE_WHY]
        assert gates, "premise: the pause ran its cooler gate"
        _why, began, ended, result = gates[0]
        # The sensor's first read in its band is the first probe at or past
        # COOL_AT, and the hold runs from that probe.
        first_in_band = min(r.rel(t) for t in r.probes
                            if t - r.t0 >= COOL_AT)
        settled = first_in_band + STABLE_S
        assert len(gates) == 1 and result is True and \
            settled <= r.rel(ended) <= settled + STEP + 1e-6, (
                f"the gate on a steady sky did not wait for the sensor: it "
                f"ran from {r.rel(began)} s to {r.rel(ended)} s (result "
                f"{result}); the sensor settled at {settled:g} s")
        said = r.said("conditions safe again — resuming")
        safe = r.safe_published()
        assert said == safe and len(said) == 1 and said[0] >= r.rel(ended), (
            f"the pause closed out at {said} s (line), {safe} s "
            f"(published), before its cooler gate passed at "
            f"{r.rel(ended)} s")
        gaps = r.read_gaps(r.rel(began), r.rel(ended))
        assert max(gaps) <= POLL_S + 1e-6, (
            f"the gate's wait on a steady sky went {max(gaps):g} s without "
            f"a safety read; its cadence is {POLL_S:g} s: gaps {gaps}")
    finally:
        await r.run.close()


# ------------------------------------------------------------ the reopen

#: The reopen's weather: rain until SAFE_AT, then two safe reads begin the
#: gate (``resume_safe_consecutive`` 2); the rain comes back at RAIN_AGAIN
#: and passes at RAIN_PASSES, both inside the time the sensor takes.
RAIN_PASSES = 250.0


async def test_rain_that_passes_in_the_reopen_s_cooler_gate_keeps_it_shut(
        sim_hub, temp_store, monkeypatch):
    """The roof is closed over the parked mount, and the reopen waits for
    two safe reads in a row (INVARIANT 4). They come at ``SAFE_AT`` and one
    cadence later, and the gate begins under the closed roof, on a sensor
    that has not come back. The rain returns at ``RAIN_AGAIN`` and passes at
    ``RAIN_PASSES``, both before the sensor would have settled. The gate's
    wait sees the rain within one cadence and ends; the roof stays shut; the
    streak starts again, and only after two more safe reads, both after the
    rain passed, does the gate run again, and the roof open once it has
    passed, on a safe read, with the safe verdict published after the open.

    Mutant "cooler gate reads no safety" (as above): RED (observed) -
        AssertionError: the roof opened at [430.0] s after a gate that began
        at 65.0 s, before the rain came back at 150 s: one safe read after
        rain that passed at 250 s opened it, where INVARIANT 4 asks for 2 in
        a row
    Mutant "the monitor read on the sensor's clock" (as above): RED
    (observed) -
        AssertionError: the first gate did not begin at the second safe read,
        65 s, and end on the rain within one 5 s cadence of 150 s: it ran
        from 65.0 s to 170.0 s (result False), safety reads inside it at gaps
        [35.0, 35.0, 35.0, 0.0]
    """
    tel = sim_hub.devices["telescope"]
    await tel.park()
    r = _Release(sim_hub, monkeypatch, rain=[(0.0, SAFE_AT),
                                             (RAIN_AGAIN, RAIN_PASSES)],
                 roof=True, resume_n=2)
    assert r.dome is not None and r.dome.connected, "premise: the sim roof"
    await r.dome.close_shutter()
    assert await r.dome.shutter_state() is DomeShutterState.CLOSED
    try:
        await r.release(r.engine._await_safe_and_reopen(
            r.dome, "rain sensor", target=None))
        gates = [g for g in r.gates if g[0] == REOPEN_WHY]
        assert gates, "premise: the reopen ran its cooler gate"
        opened = [r.rel(t) for t in r.opened]
        before_open = [g for g in gates if opened and r.rel(g[1]) <= opened[0]]
        last = before_open[-1] if before_open else gates[-1]
        assert opened and r.rel(last[1]) >= RAIN_PASSES + POLL_S, (
            f"the roof opened at {opened} s after a gate that began at "
            f"{r.rel(last[1])} s, before the rain came back at "
            f"{RAIN_AGAIN:g} s: one safe read after rain that passed at "
            f"{RAIN_PASSES:g} s opened it, where INVARIANT 4 asks for 2 in a "
            f"row")
        first = gates[0]
        gaps = r.read_gaps(r.rel(first[1]), r.rel(first[2]))
        assert r.rel(first[1]) == SAFE_AT + POLL_S and first[3] is False \
            and r.rel(first[2]) <= RAIN_AGAIN + POLL_S + STEP \
            and max(gaps) <= POLL_S + 1e-6, (
                f"the first gate did not begin at the second safe read, "
                f"{SAFE_AT + POLL_S:g} s, and end on the rain within one "
                f"{POLL_S:g} s cadence of {RAIN_AGAIN:g} s: it ran from "
                f"{r.rel(first[1])} s to {r.rel(first[2])} s (result "
                f"{first[3]}), safety reads inside it at gaps {gaps}")
        assert len(opened) == 1 and last[3] is True \
            and r.rel(last[2]) <= opened[0], (
                f"the roof did not open once, after the last gate passed: "
                f"opened at {opened} s, gates "
                f"{[(g[0], r.rel(g[1]), r.rel(g[2]), g[3]) for g in gates]}")
        safe = r.safe_published()
        assert len(safe) == 1 and safe[0] >= opened[0], (safe, opened)
        shut = [r.rel(t) for t, m, src in r.lines
                if "the roof stays closed" in m and src == "safety"]
        assert len(shut) == 1 and RAIN_AGAIN <= shut[0] <= RAIN_AGAIN \
            + POLL_S + STEP, (
                f"the reopen did not say once, within one cadence of the "
                f"rain, that the roof stays closed: at {shut} s")
        assert await r.dome.shutter_state() is DomeShutterState.OPEN
    finally:
        await r.run.close()


# --------------------------------------- a shower only the gate's watch reads

#: A shower shorter than one cadence, placed so that its one wet read is a
#: read of the first gate's watch and no read of the release's own falls in
#: it: the pause's gate begins at its second safe read, 69 s, and reads at
#: 149 s; the reopen's begins at 65 s and reads at 150 s. Each ends before
#: the first read after that gate (the reopen's post-gate read comes one
#: reap poll later, 0.25 s).
PAUSE_BLIP = (147.0, 149.1)
REOPEN_BLIP = (149.0, 150.1)


def _wet_reads(r: _Release, blip: tuple[float, float]) -> list[float]:
    return [r.rel(t) for t, ok in r.reads
            if not ok and blip[0] <= t - r.t0 < blip[1]]


async def test_a_shower_only_the_pause_s_gate_reads_starts_its_streak_again(
        sim_hub, temp_store, monkeypatch):
    """The pause asks for two safe reads in a row (``resume_safe_consecutive``
    2). Its gate begins at the second, and a shower that lasts less than one
    cadence is read by the gate's watch alone. The gate ends on it; the
    pause says so, publishes nothing safe, and STARTS ITS SAFE STREAK AGAIN:
    two fresh safe reads, one cadence apart, before the gate is run again,
    as the streak before the first gate was earned. Only once that second
    gate passes does the pause close out. (Verifier's case: the streak reset
    on a turned gate is #448's, and no case above could tell it from the
    reads that follow the gate, which there are wet and reset it anyway. Its
    mutants were run in the private scratch copy S5-ENG-SAFE-verify-mut.)

    Mutant "the pause's streak not reset on a turned gate" (the
    ``self._safe_streak = 0`` after the pause's "the weather turned" line
    deleted): RED (observed) -
        AssertionError: the pause ran its gate again 5 s after a gate the
        shower ended at 149.25 s: gates [('resumed after a safety pause',
        69.0, 149.25, False), ('resumed after a safety pause', 154.25,
        449.25, True)]; two safe reads in a row are owed first, 10 s
    Mutant "safe published before the cooler gate" (as above): RED
    (observed) -
        AssertionError: the pause published a safe verdict at [69.0, 159.25]
        s and said 'conditions safe again' at [69.0, 159.25] s; its last
        gate passed at 454.25 s
    Mutant "the pause ignores a turned gate" (its ``is not False`` made
    ``is not False or True``, so every gate passes): RED (observed) -
        AssertionError: the first gate did not end on the shower, or the
        pause did not gate the cooler once more: [('resumed after a safety
        pause', 69.0, 149.25, False)]
    """
    r = _Release(sim_hub, monkeypatch, rain=[(0.0, SAFE_AT), PAUSE_BLIP],
                 roof=False, resume_n=2)
    try:
        await r.release(r.engine._park_hold_pause("rain sensor", None))
        gates = [g for g in r.gates if g[0] == PAUSE_WHY]
        wet = _wet_reads(r, PAUSE_BLIP)
        assert gates and len(wet) == 1 and \
            gates[0][1] <= wet[0] + r.t0 <= gates[0][2], (
                f"premise: the shower was read once, inside the first gate: "
                f"wet reads at {wet} s, gates "
                f"{[(g[0], r.rel(g[1]), r.rel(g[2]), g[3]) for g in gates]}")
        first, last = gates[0], gates[-1]
        shown = [(g[0], r.rel(g[1]), r.rel(g[2]), g[3]) for g in gates]
        assert first[3] is False and len(gates) == 2, (
            f"the first gate did not end on the shower, or the pause did not "
            f"gate the cooler once more: {shown}")
        again = round(r.rel(last[1]) - r.rel(first[2]), 2)
        assert again >= 2 * POLL_S, (
            f"the pause ran its gate again {again:g} s after a gate the "
            f"shower ended at {r.rel(first[2])} s: gates {shown}; two safe "
            f"reads in a row are owed first, {2 * POLL_S:g} s")
        safe = r.safe_published()
        said = r.said("conditions safe again — resuming")
        assert last[3] is True and len(safe) == len(said) == 1 and all(
            t >= r.rel(last[2]) for t in safe + said), (
                f"the pause published a safe verdict at {safe} s and said "
                f"'conditions safe again' at {said} s; its last gate passed "
                f"at {r.rel(last[2])} s")
    finally:
        await r.run.close()


async def test_a_shower_only_the_reopen_s_gate_reads_keeps_the_roof_shut(
        sim_hub, temp_store, monkeypatch):
    """The reopen's twin of the case above. The shower is read by the
    gate's watch alone, and has passed by the time the reopen could read the
    weather again. The roof opens on the gate's verdict, not on that read:
    it stays shut, the streak starts again, and the roof opens only after
    two fresh safe reads and a second gate that passed. (Verifier's case:
    the reopen skips its post-gate read after a turned gate, and no case
    above could tell that from reading it, since there that read is wet.
    Its mutants were run in the private scratch copy
    S5-ENG-SAFE-verify-mut.)

    Mutant "the reopen ignores a turned gate" (its ``is False`` made
    ``is None``, so the post-gate read alone decides): RED (observed) -
        AssertionError: the roof opened at [150.25] s, on a read after a
        gate that the shower ended: gates [('resumed after a roof reopen',
        65.0, 150.25, False)]
    Mutant "the reopen's streak not reset on a turned gate" (its
    ``self._safe_streak = 0`` after "the roof stays closed" deleted, S4's
    line): RED (observed) -
        AssertionError: the reopen ran its gate again 5 s after a gate the
        shower ended at 150.25 s: gates [('resumed after a roof reopen',
        65.0, 150.25, False), ('resumed after a roof reopen', 155.25,
        450.25, True)]; two safe reads in a row are owed first, 10 s
    """
    tel = sim_hub.devices["telescope"]
    await tel.park()
    r = _Release(sim_hub, monkeypatch, rain=[(0.0, SAFE_AT), REOPEN_BLIP],
                 roof=True, resume_n=2)
    assert r.dome is not None and r.dome.connected, "premise: the sim roof"
    await r.dome.close_shutter()
    try:
        await r.release(r.engine._await_safe_and_reopen(
            r.dome, "rain sensor", target=None))
        gates = [g for g in r.gates if g[0] == REOPEN_WHY]
        wet = _wet_reads(r, REOPEN_BLIP)
        shown = [(g[0], r.rel(g[1]), r.rel(g[2]), g[3]) for g in gates]
        assert gates and len(wet) == 1 and \
            gates[0][1] <= wet[0] + r.t0 <= gates[0][2], (
                f"premise: the shower was read once, inside the first gate: "
                f"wet reads at {wet} s, gates {shown}")
        opened = [r.rel(t) for t in r.opened]
        first, last = gates[0], gates[-1]
        assert opened and first[3] is False and last[3] is True and \
            len(gates) == 2 and r.rel(last[2]) <= opened[0], (
                f"the roof opened at {opened} s, on a read after a gate that "
                f"the shower ended: gates {shown}")
        again = round(r.rel(last[1]) - r.rel(first[2]), 2)
        assert again >= 2 * POLL_S, (
            f"the reopen ran its gate again {again:g} s after a gate the "
            f"shower ended at {r.rel(first[2])} s: gates {shown}; two safe "
            f"reads in a row are owed first, {2 * POLL_S:g} s")
        safe = r.safe_published()
        assert len(opened) == 1 and len(safe) == 1 and safe[0] >= opened[0], (
            f"the roof opened at {opened} s and the safe verdict was "
            f"published at {safe} s")
    finally:
        await r.run.close()


# ----------------------------------- a required cooling that fails in the gate

#: The cooling budget for the case below: the sensor never comes back, so
#: the gate's wait fails at the first probe past it.
FAIL_TIMEOUT_S = 120


@pytest.mark.parametrize("action", ["abort", "warn"])
async def test_a_required_cooling_that_fails_in_the_pause_s_gate_still_ends_it(
        sim_hub, temp_store, monkeypatch, action):
    """The gate waits for the sensor on a task of its own since #448, and
    what that wait raises is still the release's to raise: with
    ``require_cooling`` and ``cooling_action`` "abort", a sensor that never
    comes back fails the wait at ``cool_timeout_s`` with a SafetyAbort, and
    the pause raises it, so the night ends through the park wind-down as it
    did when the gate awaited the wait itself. Nothing safe is published and
    the pause says nothing of being safe again. CONTROL, "warn": the same
    failure continues, as it always has; the gate passes when the wait gives
    up and the pause closes out then. (Verifier's case: `_cool_watching_
    the_weather`'s ``cooling.result()`` was the only thing that carried the
    SafetyAbort across the task, and no test reached it. Its mutant was run
    in the private scratch copy S5-ENG-SAFE-verify-mut.)

    Mutant "the gate swallows what the sensor's wait raised" (the
    ``cooling.result()`` before the gate's ``return True`` deleted): RED
    (observed), the "abort" case -
        AssertionError: a required cooling that failed in the pause's gate
        did not end the night: raised None; safe published at [204.0] s;
        'conditions safe again' said at [204.0] s
    Mutant "the gate aborts on any failed cooling" (a ``cooling.result()``
    of False made a SafetyAbort, whatever ``cooling_action`` says): RED
    (observed), the "warn" control -
        AssertionError: CONTROL: a cooling failure under 'warn' did not
        continue to the close-out once the gate gave up: raised
        SafetyAbort('cooling required but it failed'); gate 64.0 s to 204.0 s
        (None); safe published at [] s, said at [] s
    """
    r = _Release(sim_hub, monkeypatch, rain=[(0.0, SAFE_AT)], roof=False)
    engine, run = r.engine, r.run
    engine._cfg.escalation.require_cooling = True
    engine._cfg.escalation.cooling_action = action
    plan = engine.plan
    plan.cool_timeout_s = FAIL_TIMEOUT_S
    engine.plan = plan              # the policy is re-derived on assignment
    assert engine._policy.cool_timeout_s == FAIL_TIMEOUT_S, (
        "premise: the plan's cooling budget is the policy's")
    cam = sim_hub.devices["camera"]

    async def never_back():
        r.probes.append(run.clock.t)
        await run._park(HANG_S)
        raise asyncio.TimeoutError()

    monkeypatch.setattr(cam, "get_temperature", never_back)
    raised: BaseException | None = None
    try:
        await r.release(engine._park_hold_pause("rain sensor", None))
    except engine_mod.SafetyAbort as e:
        raised = e
    finally:
        await run.close()
    gates = [g for g in r.gates if g[0] == PAUSE_WHY]
    assert len(gates) == 1, f"premise: one gate: {gates}"
    _why, began, ended, result = gates[0]
    assert r.rel(began) == r.first_safe(SAFE_AT) and ended is not None, (
        f"premise: the gate began at the first safe read and ended: {gates}")
    gaps = r.read_gaps(r.rel(began), r.rel(ended))
    assert max(gaps) <= POLL_S + 1e-6, (
        f"premise: the gate read the weather through its wait: gaps {gaps}")
    safe = r.safe_published()
    said = r.said("conditions safe again — resuming")
    if action == "abort":
        assert isinstance(raised, engine_mod.SafetyAbort) and \
            "cooling required" in str(raised) and safe == [] and said == [], (
                f"a required cooling that failed in the pause's gate did not "
                f"end the night: raised {raised!r}; safe published at {safe} "
                f"s; 'conditions safe again' said at {said} s")
        assert result is None and r.rel(ended) >= r.rel(began) \
            + FAIL_TIMEOUT_S, (r.rel(began), r.rel(ended), result)
    else:
        assert raised is None and result is True and len(safe) == 1 and \
            said == safe and safe[0] >= r.rel(ended) >= r.rel(began) \
            + FAIL_TIMEOUT_S, (
                f"CONTROL: a cooling failure under 'warn' did not continue "
                f"to the close-out once the gate gave up: raised {raised!r}; "
                f"gate {r.rel(began)} s to {r.rel(ended)} s ({result}); safe "
                f"published at {safe} s, said at {said} s")
