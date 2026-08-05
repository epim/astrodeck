"""Pause has to actually pause — the one brake the operator has mid-alignment.

Polar alignment is the one workflow where the user is crouched at the mount with
a hex key in one hand: Pause is how they stop the rig moving and exposing while
they think. Until 2026-08-04 it did almost none of that, and nothing here
covered it:

  * ``_publish`` merged whatever the driver said last, so the native driver's
    next ``state:"running"`` overwrote the ``state:"paused"`` ``pause()`` had
    just written. The button flipped back to Pause and the run looked live.
  * The native driver read the flag once per cadence interval, ahead of the 1 s
    sleep, so one more exposure always fired after Pause — and read it not at
    all before the 12 deg RA rotation, so one more slew did too.
  * The simulator never read the flag at all: it kept streaming errors labelled
    "paused" and ran on to "polar aligned".
  * ``pause()`` with no session published ``state:"paused"`` anyway, stranding
    the UI on controls with no driver behind them.

And one the 2026-08-04 fix made WORSE before it made it better: dropping the
driver's late ``state:"running"`` stopped Pause being overwritten, but it also
removed the flicker that used to (accidentally) tell the user the mount was
still turning. ``pause()`` is not a brake — the flag is only READ at the
checkpoints in ``wait_if_paused``, and a 12 deg RA slew already in flight runs
on for another 5-15 s. So there are two states, not one: "pausing" from the
moment the user asks, "paused" only once a driver has ARRIVED at the checkpoint
with nothing left in flight. The user is crouched at the mount with a hex key;
the difference is where their hands are.

The fake Rust engine below returns a constant error well above the done
threshold, so the adjusting loop keeps running for a test to pause it.
"""
from __future__ import annotations

import asyncio

import pytest

from astrodeck.events import bus
from astrodeck.hub import Hub
from astrodeck.polar import native as nat
from astrodeck.polar.session import wait_if_paused

# One fitted error. The spread flag + magnitude ride along so the same harness
# covers the "this fit is not trustworthy" caveat (audit finding #23).
_ERR = {
    "az_arcmin": 12.0, "alt_arcmin": 9.0, "total_arcmin": 15.0,
    "az_direction": "left_west", "alt_direction": "up",
    "flags": ["position_angle_spread_large"],
    "position_angle_spread_deg": 9.4,
}


class _FakeEngine:
    """Stands in for the Rust wheel so these tests run with or without it."""

    @staticmethod
    def tppa_from_three(solves, site, opts):
        return {"model": {}, "error": dict(_ERR)}

    @staticmethod
    def tppa_update(model, solve):
        return dict(_ERR)


class _Frame:
    timestamp = 1_700_000_000.0


class _Solve:
    """A plate solve 70 deg from the pole, so the pole guard lets it through."""
    dec_deg = 20.0
    rotation_deg = 0.0
    success = True

    def __init__(self, ra_hours: float):
        self.ra_hours = ra_hours


class _Cam:
    name = "cam"


class _Tel:
    def __init__(self, slews: list):
        self._slews = slews
        self._ra = 5.0

    async def get_position(self):
        return self._ra, 20.0

    async def slew(self, ra, dec):
        self._slews.append((ra, dec))
        self._ra = ra


class _Hub:
    mode = "sim"
    _motion_epoch = 0

    def __init__(self):
        self.slews: list = []
        self._tel = _Tel(self.slews)

    def require(self, role):
        return self._tel if role == "telescope" else _Cam()

    async def yield_camera_for(self, why):
        pass

    def _check_solar(self, ra, dec):
        pass


class _Rig:
    """The stubbed native run: hub + a REAL PolarAlignSession (so ``_publish``,
    ``pause()`` and ``running`` are the production code under test)."""

    def __init__(self, hub, session):
        self.hub = hub
        self.session = session
        self.captures: list = []
        self.pause_on_capture: int | None = None
        self.task: asyncio.Task | None = None

    def launch(self) -> asyncio.Task:
        self.task = asyncio.create_task(nat.run_native(self.session, self.hub))
        # What start() does for the real thing; ``pause()`` gates on it.
        self.session._task = self.task
        return self.task

    def assert_no_error(self):
        assert self.session.state.get("state") != "error", self.session.state


@pytest.fixture
async def native_rig(monkeypatch):
    monkeypatch.setattr(nat, "NATIVE_AVAILABLE", True)
    monkeypatch.setattr(nat, "_native", _FakeEngine)
    monkeypatch.setattr(nat, "_site_dict", lambda hub: {})
    # Shorter cadence so the adjust-phase tests don't pay 1 s a tick.
    monkeypatch.setattr(nat, "_ADJUST_INTERVAL_S", 0.5)
    import astrodeck.providers as _pv
    monkeypatch.setattr(_pv, "pick_solver", lambda hub: object())

    h = Hub()                      # for a real PolarAlignSession to hang off
    rig = _Rig(_Hub(), h.polar)

    async def _fake_capture(hub, solver):
        rig.captures.append(len(rig.captures) + 1)
        if rig.pause_on_capture == len(rig.captures):
            await rig.session.pause()
        return _Frame(), _Solve(5.0 + 0.8 * len(rig.captures)), (1.55, 1024.0, 768.0)

    monkeypatch.setattr(nat, "_capture_and_solve", _fake_capture)
    yield rig
    if rig.task is not None and not rig.task.done():
        rig.task.cancel()
        try:
            await rig.task
        except (asyncio.CancelledError, Exception):
            pass


async def _wait(predicate, timeout=10.0, poll=0.01):
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(poll)
    return False


# ----------------------------------------------------- the publish-order defect

async def test_a_driver_cannot_downgrade_an_explicit_pause():
    """The bug in one assertion: a solve that was already in flight when the user
    hit Pause published ``state:"running"`` and the UI showed a live run."""
    h = Hub()
    sess = h.polar
    sess._task = asyncio.create_task(asyncio.sleep(30))
    try:
        sess._publish(state="running", source="native", progress=0.5)
        await sess.pause()
        assert sess.state["state"] == "pausing"

        sess._publish(state="running", progress=0.75, az_error=3.0, alt_error=4.0)
        assert sess.state["state"] == "pausing", "the driver overwrote the pause"
        # Only the state key is dropped — the reading still streams, so the
        # reticle stays truthful about where the error is while parked.
        assert sess.state["progress"] == 0.75
        assert sess.state["total_error"] == 5.0

        # ...and the same holds once the driver has actually stopped.
        sess._ack_pause_reached()
        assert sess.state["state"] == "paused"
        sess._publish(state="running", progress=0.9)
        assert sess.state["state"] == "paused", "the driver overwrote the pause"
        assert sess.state["progress"] == 0.9
    finally:
        sess._task.cancel()


async def test_pause_says_pausing_until_the_driver_reaches_the_checkpoint():
    """The order IS the fix. ``pause()`` only raises a flag; the drivers read it
    at the ``wait_if_paused`` checkpoints, and an RA slew already in flight runs
    on for 5-15 s first. Announcing "paused" on the POST — as it did until now —
    tells someone with a hand near the bolt that the mount has stopped when it
    has not."""
    h = Hub()
    sess = h.polar
    sess._task = asyncio.create_task(asyncio.sleep(30))
    q = bus.subscribe()
    try:
        sess._publish(state="running", source="native", progress=0.5)
        await sess.pause()
        assert sess.state["state"] == "pausing", "announced a stop that has not happened"

        # Stand in for the driver: it is still finishing the slew, so it has not
        # reached the checkpoint yet and the state must not improve on its own.
        await asyncio.sleep(0.25)
        assert sess.state["state"] == "pausing", sess.state

        # NOW it arrives — the real primitive, not a hand-written flag poke.
        driver = asyncio.create_task(wait_if_paused(sess))
        assert await _wait(lambda: sess.state["state"] == "paused"), sess.state
        assert not driver.done(), "wait_if_paused returned while still paused"

        seen = [ev.data["state"] for ev in _drain(q) if ev.type == "polar"]
        assert seen == ["running", "pausing", "paused"], seen

        await sess.resume()
        assert await _wait(lambda: driver.done())
        assert sess.state["state"] == "running"
    finally:
        bus.unsubscribe(q)
        sess._task.cancel()


async def test_a_nina_run_still_gets_a_plain_paused():
    """NINA's TPPA plugin runs its own alignment loop and never reaches
    ``wait_if_paused``, so nothing would ever promote a "pausing" — the UI would
    sit on a disabled "Stopping…" for the rest of the session. We have no
    observation point on that side, so it keeps the old best-effort claim: an
    honest state we could never leave would be worse than the imprecise one."""
    h = Hub()
    sess = h.polar
    sent: list[str] = []

    class _Ws:
        async def send(self, payload):
            sent.append(payload)

    sess._ws = _Ws()
    sess._task = asyncio.create_task(asyncio.sleep(30))
    try:
        sess._publish(state="running", source="nina", progress=0.4)
        await sess.pause()
        assert sess.state["state"] == "paused", sess.state
        assert any("pause-alignment" in p for p in sent), sent
    finally:
        sess._ws = None
        sess._task.cancel()


async def test_a_second_pause_does_not_demote_an_observed_stop():
    """A REST client (or a double-tap through a slow round trip) can call Pause
    twice. The driver is parked INSIDE ``wait_if_paused`` and will never
    re-enter it, so re-arming "pausing" here would strand the UI on
    "Stopping…" over a mount that has been stationary for a minute."""
    h = Hub()
    sess = h.polar
    sess._task = asyncio.create_task(asyncio.sleep(30))
    try:
        await sess.pause()
        sess._ack_pause_reached()
        assert sess.state["state"] == "paused"
        await sess.pause()
        assert sess.state["state"] == "paused", sess.state
    finally:
        sess._task.cancel()


@pytest.mark.parametrize("terminal", ["done", "error"])
@pytest.mark.parametrize("reached", [False, True], ids=["pausing", "paused"])
async def test_terminal_states_still_land_while_paused(terminal, reached):
    """Suppressing every state would be worse than the bug: a run that finishes
    (or fails) while paused would stick on "paused" with no way out. Both halves
    of the pause need the escape hatch — a run that ends during the "pausing"
    window (the slew that was in flight was the last thing it had to do) has
    nobody left to reach the checkpoint and promote it."""
    h = Hub()
    sess = h.polar
    sess._task = asyncio.create_task(asyncio.sleep(30))
    try:
        await sess.pause()
        if reached:
            sess._ack_pause_reached()
        sess._publish(state=terminal, progress=1.0, message="over")
        assert sess.state["state"] == terminal
    finally:
        sess._task.cancel()


async def test_resume_clears_the_flag_and_the_state():
    h = Hub()
    sess = h.polar
    sess._task = asyncio.create_task(asyncio.sleep(30))
    try:
        await sess.pause()
        await sess.resume()
        assert sess.state["state"] == "running"
        assert sess._native_paused is False
        sess._publish(state="running", progress=0.9)
        assert sess.state["state"] == "running"
    finally:
        sess._task.cancel()


async def test_pause_and_resume_with_no_session_do_not_strand_the_ui():
    """PolarView derives "a run is live" from the state string, so publishing
    "paused" with no driver lights Resume/Stop over nothing and disables Start
    with nothing left to clear it."""
    h = Hub()
    q = bus.subscribe()
    try:
        await h.polar.pause()
        assert h.polar.state["state"] == "idle"
        assert h.polar._native_paused is False
        await h.polar.resume()
        assert h.polar.state["state"] == "idle"
        assert not h.polar.running
        published = [ev for ev in _drain(q) if ev.type == "polar"]
        assert published == [], published
    finally:
        bus.unsubscribe(q)


def _drain(q) -> list:
    out = []
    while not q.empty():
        out.append(q.get_nowait())
    return out


# ------------------------------------------------------------- native TPPA rig

async def test_pause_during_measuring_blocks_the_next_ra_slew(native_rig):
    """The one irreversible thing this loop does. Pausing left the tube 12 deg
    from where the user pointed it, after they asked the rig to stop."""
    rig = native_rig
    rig.pause_on_capture = 1        # Pause lands while the first solve is in flight
    rig.launch()
    assert await _wait(lambda: rig.session.state["state"] == "paused")
    await asyncio.sleep(0.4)        # ample time for the rotation to have fired
    rig.assert_no_error()
    assert rig.hub.slews == [], "rotated the mount after the user hit Pause"
    assert not rig.task.done()


async def test_the_native_run_reports_paused_only_once_it_is_parked(native_rig):
    """"paused" has to mean the mount has stopped, and the only thing that knows
    that is the driver arriving at the checkpoint. Here the pause lands mid-solve
    and the state stays "pausing" across the rest of that solve."""
    rig = native_rig
    rig.pause_on_capture = 1
    q = bus.subscribe()
    try:
        rig.launch()
        assert await _wait(lambda: rig.session.state["state"] == "paused")
        seen = [ev.data["state"] for ev in _drain(q) if ev.type == "polar"]
        assert "pausing" in seen, seen
        assert seen.index("pausing") < seen.index("paused"), seen
        # The solve that was already in flight publishes BETWEEN the two — that
        # is the window the old code lied through, and it must not read as
        # "running" either (the 0.2.44 drop) or as "paused" (this fix).
        between = seen[seen.index("pausing") + 1:seen.index("paused")]
        assert "running" not in between, seen
    finally:
        bus.unsubscribe(q)


async def test_pause_in_the_adjust_cadence_starts_no_further_exposure(native_rig):
    """Pause almost always lands DURING the 1 s cadence sleep, which is exactly
    when the old single pre-sleep check could not see it — one more shutter
    fired every time."""
    rig = native_rig
    rig.launch()
    assert await _wait(lambda: rig.session.state.get("phase") == "adjusting"), \
        rig.session.state
    taken = len(rig.captures)
    assert taken == 3, rig.captures
    # The loop is inside asyncio.sleep(_ADJUST_INTERVAL_S) by now: it passed its
    # pre-sleep check the instant the fit published. Pause mid-sleep.
    await asyncio.sleep(0.15)
    await rig.session.pause()
    await asyncio.sleep(1.6)        # three cadence intervals
    rig.assert_no_error()
    assert len(rig.captures) == taken, "kept exposing after Pause"
    assert rig.session.state["state"] == "paused", rig.session.state


async def test_resume_picks_the_native_run_back_up(native_rig):
    rig = native_rig
    rig.launch()
    assert await _wait(lambda: rig.session.state.get("phase") == "adjusting")
    await asyncio.sleep(0.15)
    await rig.session.pause()
    await asyncio.sleep(1.1)
    taken = len(rig.captures)
    await rig.session.resume()
    assert await _wait(lambda: len(rig.captures) > taken), "resume did not restart"
    assert rig.session.state["state"] == "running"


async def test_stop_while_paused_still_unwinds(native_rig):
    """The pause wait's sleep is the cancellation point. If it ever becomes a
    busy loop, a paused session can never be stopped."""
    rig = native_rig
    rig.pause_on_capture = 1
    rig.launch()
    assert await _wait(lambda: rig.session.state["state"] == "paused")
    await asyncio.wait_for(rig.session.stop(), timeout=5.0)
    assert not rig.session.running
    assert rig.session.state["state"] == "idle"


async def test_the_position_angle_spread_reaches_the_operator(native_rig, bus_lines):
    """Finding #23: the engine raised the flag, and both channels dropped it —
    the payload carried a boolean nothing rendered, and the log said nothing at
    all. A REST/headless operator has only the log."""
    rig = native_rig
    rig.launch()
    assert await _wait(lambda: rig.session.state.get("phase") == "adjusting")
    st = rig.session.state
    assert "position_angle_spread_large" in st["flags"]
    # The NUMBER, not just the boolean: "the geometry was off" is not actionable.
    assert st["position_angle_spread_deg"] == pytest.approx(9.4)
    warned = [m for lvl, m, _src in bus_lines
              if lvl == "warning" and "9.4" in m]
    assert warned, bus_lines
    assert "meridian" in warned[0], warned[0]


async def test_a_small_spread_is_not_logged(native_rig, bus_lines, monkeypatch):
    """Warning fatigue: every three-point run has SOME spread."""
    small = dict(_ERR, flags=[], position_angle_spread_deg=0.6)
    monkeypatch.setattr(nat, "_native", type("E", (), {
        "tppa_from_three": staticmethod(lambda s, si, o: {"model": {}, "error": dict(small)}),
        "tppa_update": staticmethod(lambda m, s: dict(small)),
    }))
    rig = native_rig
    rig.launch()
    assert await _wait(lambda: rig.session.state.get("phase") == "adjusting")
    assert [m for lvl, m, _s in bus_lines if lvl == "warning"] == []


# ---------------------------------------------------------------- sim provider

async def test_sim_pause_stops_the_stream_and_resume_finishes(monkeypatch):
    """The sim used to ignore the flag entirely: it kept streaming errors while
    labelled "paused" and ran on to "polar aligned" without the user."""
    from astrodeck.polar import session as sess_mod
    # The suite's fast path collapses every sim sleep to 0, which runs the whole
    # convergence inside one tick — there'd be no live phase left to pause.
    monkeypatch.setattr(sess_mod, "_sim_delay", lambda s: 0.12)

    h = Hub()                       # mode "none" -> sim polar driver
    q = bus.subscribe()
    try:
        await h.polar.start()
        assert h.polar.state["source"] == "sim"
        # Into the convergence phase: a fitted error is streaming.
        assert await _wait(lambda: h.polar.state["total_error"] > 0, poll=0.005), \
            h.polar.state
        await h.polar.pause()
        assert h.polar.state["state"] == "pausing"
        # The sim is inside its convergence sleep; "paused" only when it lands
        # back on wait_if_paused.
        assert await _wait(lambda: h.polar.state["state"] == "paused", poll=0.005), \
            h.polar.state

        _drain(q)
        await asyncio.sleep(0.12 * 8)       # eight ticks of silence
        streamed = [ev.data for ev in _drain(q) if ev.type == "polar"]
        assert streamed == [], streamed
        assert h.polar.state["state"] == "paused"
        assert h.polar.running, "the sim finished the alignment while paused"

        await h.polar.resume()
        assert await _wait(lambda: h.polar.state["state"] == "done", timeout=30.0), \
            h.polar.state
        assert h.polar.state["total_error"] < 1.0
    finally:
        bus.unsubscribe(q)
        await h.polar.stop()


async def test_sim_pause_during_the_measuring_points_holds(monkeypatch):
    from astrodeck.polar import session as sess_mod
    monkeypatch.setattr(sess_mod, "_sim_delay", lambda s: 0.12)
    h = Hub()
    try:
        await h.polar.start()
        assert await _wait(lambda: "measuring point" in h.polar.state["message"],
                           poll=0.005), h.polar.state
        # Snapshot BEFORE the pause: pause/paused own the message from here on,
        # so the proxy for "the sim walked on anyway" is the progress bar and the
        # fitted error, not the message string.
        at_pause = dict(h.polar.state)
        await h.polar.pause()
        await asyncio.sleep(0.12 * 6)
        assert h.polar.state["state"] == "paused"
        # Unsabotaged, the sim holds here; before the fix it walked the remaining
        # points and arrived in the convergence phase ("adjust the mount").
        assert h.polar.state["progress"] == at_pause["progress"], h.polar.state
        assert "measuring point" in at_pause["message"], at_pause
        assert h.polar.state["total_error"] == 0.0
    finally:
        await h.polar.stop()
