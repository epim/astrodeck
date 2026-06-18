"""Move-axis deadman + touch rate clamp + STOP-zeroes-both-axes (Batch 3, 3C).

The single move-axis watchdog (master plan §A.5 / §C-Risk-5) is the safety
backstop for touch manual slew: a manual ``move_axis`` followed by a dropped
client keepalive must auto-halt the mount within ``MOVE_DEADMAN_MS``, capped to
``TOUCH_MAX_RATE_DEG_S`` so worst-case uncommanded travel stays well under a
degree. These tests exercise:

* the watchdog auto-halts both axes after a stale keepalive (kill keepalive
  mid-move → axes zeroed within the window);
* a refreshed keepalive keeps the move alive (no spurious halt);
* the watchdog never fires when the move was already a stop (rate 0);
* the ``/api/mount/move`` endpoint clamps the rate to ±TOUCH_MAX_RATE and arms
  the deadman via ``hub.note_move``;
* STOP zeroes BOTH MoveAxis axes — on the sim (inherits base.stop) and on an
  Alpaca-shaped mock (abortslew + move_axis 0/0).
"""
from __future__ import annotations

import asyncio
import time

import httpx
import pytest
from fastapi.testclient import TestClient

import astrodeck.api.app as app_module
from astrodeck.devices.base import DeviceError, Telescope
from astrodeck.devices.sim import TOUCH_MAX_RATE_DEG_S as SIM_TOUCH_MAX
from astrodeck.devices.sim import SimRig, SimTelescope
from astrodeck.hub import MOVE_DEADMAN_MS, TOUCH_MAX_RATE_DEG_S, Hub


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #

async def _wait_until(predicate, timeout: float = 3.0, poll: float = 0.02) -> bool:
    """Poll ``predicate`` until true or timeout. Returns whether it became true."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        await asyncio.sleep(poll)
    return predicate()


class _RecordingTelescope(Telescope):
    """Alpaca-shaped mock: records every ``move_axis`` call and ``abortslew`` so
    a test can prove ``stop()`` zeroes BOTH axes (not just abortslew)."""

    def __init__(self) -> None:
        super().__init__("Mock Mount")
        self.connected = True
        self.moves: list[tuple[str, float]] = []
        self.aborted = 0

    async def connect(self) -> None:
        self.connected = True

    async def disconnect(self) -> None:
        self.connected = False

    async def get_position(self):
        return 0.0, 0.0

    async def slew(self, ra_hours, dec_deg):  # pragma: no cover - unused
        pass

    async def sync(self, ra_hours, dec_deg):  # pragma: no cover - unused
        pass

    async def set_tracking(self, on):  # pragma: no cover - unused
        pass

    async def get_tracking(self):  # pragma: no cover - unused
        return True

    async def park(self):  # pragma: no cover - unused
        pass

    async def unpark(self):  # pragma: no cover - unused
        pass

    async def is_parked(self):
        return False

    async def is_slewing(self):
        return False

    async def move_axis(self, axis: str, rate_deg_s: float) -> None:
        self.moves.append((axis, rate_deg_s))

    async def stop(self) -> None:
        # Mirror AlpacaTelescope.stop(): abort + zero both axes.
        self.aborted += 1
        await self.move_axis("ra", 0)
        await self.move_axis("dec", 0)


class _FlakyStopTelescope(_RecordingTelescope):
    """A mount whose ``stop()`` raises the first ``fail_times`` calls (simulating
    the dropped-network condition that trips the deadman AND breaks the stop HTTP
    call), then succeeds. Lets a test prove the watchdog RETRIES instead of
    silently disarming after one failed halt (F-A3 / S1+S5)."""

    def __init__(self, fail_times: int) -> None:
        super().__init__()
        self.fail_times = fail_times
        self.stop_calls = 0

    async def stop(self) -> None:
        self.stop_calls += 1
        if self.stop_calls <= self.fail_times:
            raise DeviceError("simulated stop failure (network dropped)")
        await super().stop()


# --------------------------------------------------------------------------- #
# watchdog (deadman) — direct hub tests
# --------------------------------------------------------------------------- #

async def test_watchdog_auto_halts_both_axes_on_stale_keepalive():
    """A manual move with NO further keepalive must be auto-halted within the
    deadman window, and BOTH axes zeroed."""
    hub = Hub()
    tel = SimTelescope(SimRig())
    await tel.connect()
    hub.devices["telescope"] = tel

    # Issue a manual move on RA and arm the deadman, then stop refreshing it.
    await tel.move_axis("ra", 0.5)
    hub.note_move("ra", 0.5)
    assert hub._move_watchdog_task is not None

    # Within ~MOVE_DEADMAN_MS + a watchdog tick, the move must be auto-zeroed.
    budget = MOVE_DEADMAN_MS / 1000.0 + 0.6
    halted = await _wait_until(
        lambda: tel._move_rates["ra"] == 0 and tel._move_rates["dec"] == 0,
        timeout=budget)
    assert halted, "watchdog did not zero the axes within the deadman window"
    assert hub._move_rates_seen == {"ra": 0.0, "dec": 0.0}

    await hub.disconnect_all()


async def test_watchdog_fires_within_deadman_window():
    """The auto-halt must land AFTER the deadman window opens but not absurdly
    late — bounding worst-case uncommanded travel."""
    hub = Hub()
    tel = SimTelescope(SimRig())
    await tel.connect()
    hub.devices["telescope"] = tel

    await tel.move_axis("dec", 0.5)
    hub.note_move("dec", 0.5)
    armed_at = time.monotonic()

    halted = await _wait_until(lambda: tel._move_rates["dec"] == 0,
                               timeout=MOVE_DEADMAN_MS / 1000.0 + 0.6)
    elapsed_ms = (time.monotonic() - armed_at) * 1000.0
    assert halted
    # Must not fire before the window (allow a small scheduling tolerance).
    assert elapsed_ms >= MOVE_DEADMAN_MS - 100
    # And must fire reasonably promptly after it (window + a couple of ticks).
    assert elapsed_ms <= MOVE_DEADMAN_MS + 700

    await hub.disconnect_all()


async def test_watchdog_not_tripped_while_keepalive_refreshes():
    """A live 1Hz-style keepalive (re-stamping last_move_ts) must keep the move
    alive — the deadman only fires on a STALE keepalive."""
    hub = Hub()
    tel = SimTelescope(SimRig())
    await tel.connect()
    hub.devices["telescope"] = tel

    await tel.move_axis("ra", 0.5)
    hub.note_move("ra", 0.5)

    # Refresh faster than the deadman for longer than one window; the move must
    # never be zeroed during that time.
    deadline = time.monotonic() + (MOVE_DEADMAN_MS / 1000.0) + 0.8
    while time.monotonic() < deadline:
        hub.note_move("ra", 0.5)          # keepalive re-stamp
        assert tel._move_rates["ra"] == 0.5
        await asyncio.sleep(0.2)

    assert tel._move_rates["ra"] == 0.5, "live keepalive must not be halted"

    await hub.disconnect_all()


async def test_watchdog_idle_when_rate_is_zero():
    """A stop (rate 0) stamps the deadman but must never trigger a halt — there
    is nothing moving to halt."""
    hub = Hub()
    tel = _RecordingTelescope()
    hub.devices["telescope"] = tel

    hub.note_move("ra", 0.0)
    hub.note_move("dec", 0.0)

    # Wait past a full deadman window; stop() must never have been called.
    await asyncio.sleep(MOVE_DEADMAN_MS / 1000.0 + 0.5)
    assert tel.aborted == 0
    assert tel.moves == []

    await hub.disconnect_all()


async def test_disconnect_cancels_watchdog():
    """disconnect_all must tear the watchdog task down and reset deadman state so
    a stale ts can't fire against a torn-down rig."""
    hub = Hub()
    tel = SimTelescope(SimRig())
    await tel.connect()
    hub.devices["telescope"] = tel

    await tel.move_axis("ra", 0.5)
    hub.note_move("ra", 0.5)
    task = hub._move_watchdog_task
    assert task is not None and not task.done()

    await hub.disconnect_all()
    assert hub._move_watchdog_task is None
    assert hub.last_move_ts is None
    assert hub._move_rates_seen == {"ra": 0.0, "dec": 0.0}
    # the task is actually finished/cancelled
    assert await _wait_until(lambda: task.done(), timeout=1.0)


# --------------------------------------------------------------------------- #
# STOP zeroes both axes (sim + alpaca-mock)
# --------------------------------------------------------------------------- #

async def test_sim_stop_zeroes_both_axes_and_exits_move_loop():
    """SimTelescope must NOT override stop(): the inherited base.stop() zeroes
    both axes, so the _move_loop exits."""
    # It must inherit Telescope.stop (not define its own) — the spec's guard.
    assert "stop" not in SimTelescope.__dict__

    tel = SimTelescope(SimRig())
    await tel.connect()
    await tel.move_axis("ra", 0.5)
    await tel.move_axis("dec", 0.5)
    assert tel._move_task is not None

    await tel.stop()
    assert tel._move_rates == {"ra": 0.0, "dec": 0.0}
    # the background move loop winds down once rates are zero
    assert await _wait_until(lambda: tel._move_task.done(), timeout=1.0)


async def test_alpaca_shaped_stop_zeroes_both_axes():
    """An Alpaca-shaped stop() must abort AND issue move_axis 0 on both axes
    (AbortSlew alone does not reliably stop a MoveAxis on real drivers)."""
    tel = _RecordingTelescope()
    await tel.move_axis("ra", 0.5)
    tel.moves.clear()

    await tel.stop()
    assert tel.aborted == 1
    assert ("ra", 0) in tel.moves
    assert ("dec", 0) in tel.moves


# --------------------------------------------------------------------------- #
# rate clamp + note_move via the real /api/mount/move endpoint
# --------------------------------------------------------------------------- #

@pytest.fixture
def client(monkeypatch):
    # Disarm the W1.10 sun-exclusion cone so the sequence-start force tests below
    # are date-independent: their dummy (0,0) target sits ~0.4 deg from the Sun
    # near the vernal equinox and would otherwise 409 with sun_exclusion instead
    # of the horizon code they assert. The cone is covered by test_sun_guard.py;
    # the move tests here don't depend on it. monkeypatch restores it afterward.
    from astrodeck.config import config_store as _cs
    monkeypatch.setattr(_cs.cfg().safety, "solar_avoidance", False)
    app = app_module.create_app()
    with TestClient(app) as c:
        yield c


def test_move_endpoint_clamps_rate_and_arms_deadman(client, monkeypatch):
    """/api/mount/move clamps the requested rate to ±TOUCH_MAX_RATE_DEG_S and
    calls hub.note_move with the CLAMPED rate (so the deadman sees the real
    commanded rate, not the over-spec request)."""
    seen: list[tuple[str, float]] = []
    notes: list[tuple[str, float]] = []

    class _Tel:
        connected = True

        async def move_axis(self, axis, rate):
            seen.append((axis, rate))

    monkeypatch.setattr(app_module.hub, "require", lambda role: _Tel())
    monkeypatch.setattr(app_module.hub, "note_move",
                        lambda axis, rate: notes.append((axis, rate)))

    # Request 4.0°/s — must be clamped to the cap.
    r = client.post("/api/mount/move", json={"axis": "ra", "rate_deg_s": 4.0})
    assert r.status_code == 200, r.text
    assert seen == [("ra", TOUCH_MAX_RATE_DEG_S)]
    assert notes == [("ra", TOUCH_MAX_RATE_DEG_S)]

    # Negative over-spec clamps to the negative cap.
    seen.clear(); notes.clear()
    r = client.post("/api/mount/move", json={"axis": "dec", "rate_deg_s": -9.0})
    assert r.status_code == 200, r.text
    assert seen == [("dec", -TOUCH_MAX_RATE_DEG_S)]
    assert notes == [("dec", -TOUCH_MAX_RATE_DEG_S)]

    # An in-range rate passes through unchanged.
    seen.clear(); notes.clear()
    r = client.post("/api/mount/move", json={"axis": "ra", "rate_deg_s": 0.0334})
    assert r.status_code == 200, r.text
    assert seen == [("ra", 0.0334)]
    assert notes == [("ra", 0.0334)]


def test_sim_and_hub_touch_caps_agree():
    """The sim's defensive clamp must mirror the authoritative hub/server cap so
    there is one effective limit across the program (§A.7)."""
    assert SIM_TOUCH_MAX == TOUCH_MAX_RATE_DEG_S


async def test_sim_move_axis_clamps_defensively():
    """A direct (endpoint-bypassing) over-spec move_axis on the sim must still be
    clamped to the touch cap — defense in depth."""
    tel = SimTelescope(SimRig())
    await tel.connect()
    await tel.move_axis("ra", 5.0)
    assert tel._move_rates["ra"] == TOUCH_MAX_RATE_DEG_S
    await tel.move_axis("dec", -5.0)
    assert tel._move_rates["dec"] == -TOUCH_MAX_RATE_DEG_S
    await tel.stop()


# --------------------------------------------------------------------------- #
# F-A3 — DURABLE HALT: a failed tel.stop() must RETRY, never silently disarm
# (S1 + S5 — the one fail-UNSAFE backstop defect in the batch).
# --------------------------------------------------------------------------- #

async def test_watchdog_retries_after_failed_stop():
    """When the deadman fires but ``tel.stop()`` raises (the dropped-network
    condition that trips the deadman is exactly what breaks the stop HTTP call),
    the watchdog MUST leave the seen-rates non-zero and ``last_move_ts`` stale so
    the next 250ms tick RETRIES — instead of disarming while the axis may still
    be driving."""
    hub = Hub()
    tel = _FlakyStopTelescope(fail_times=2)   # first 2 stop() calls raise
    hub.devices["telescope"] = tel

    # Arm a non-zero move and let the keepalive go stale immediately.
    hub.note_move("ra", 0.5)

    # The watchdog must keep retrying until a stop() finally succeeds (3rd call),
    # at which point — and only then — the rates are zeroed and the deadman
    # disarms. If the buggy behavior (disarm-on-first-failure) were present, the
    # rates would zero after exactly ONE stop() call and stop_calls would never
    # exceed 1.
    ok = await _wait_until(
        lambda: tel.stop_calls >= 3 and hub._move_rates_seen["ra"] == 0.0,
        timeout=MOVE_DEADMAN_MS / 1000.0 + 3.0)
    assert ok, (f"watchdog did not retry to a successful stop "
                f"(stop_calls={tel.stop_calls}, seen={hub._move_rates_seen})")
    # It retried (more than one attempt) and only disarmed once stop() succeeded.
    assert tel.stop_calls >= 3
    assert hub._move_rates_seen == {"ra": 0.0, "dec": 0.0}
    # The successful stop zeroed both axes (abort + move 0/0).
    assert ("ra", 0) in tel.moves and ("dec", 0) in tel.moves
    # F-watchdog-disarm: a clean disarm clears last_move_ts too.
    assert hub.last_move_ts is None

    await hub.disconnect_all()


async def test_watchdog_does_not_disarm_while_stop_keeps_failing():
    """Hard fail-UNSAFE guard: while ``stop()`` keeps raising, the deadman must
    NEVER reach the disarmed state (rates stay non-zero, ts stays stale) — the
    backstop must not give up in the exact network-loss scenario it exists for."""
    hub = Hub()
    tel = _FlakyStopTelescope(fail_times=10_000)   # never succeeds in this test
    hub.devices["telescope"] = tel

    hub.note_move("dec", 0.5)

    # Give the watchdog several windows + ticks to (wrongly) disarm if buggy.
    await asyncio.sleep(MOVE_DEADMAN_MS / 1000.0 + 1.2)
    assert tel.stop_calls >= 2, "watchdog should have retried at least twice"
    # Still armed: the deadman has not given up.
    assert hub._move_rates_seen["dec"] != 0.0
    assert hub.last_move_ts is not None

    await hub.disconnect_all()


async def test_alpaca_stop_zeroes_axes_even_when_abortslew_raises_transport():
    """F-A3 (alpaca): a transport-level failure of abortslew must NOT skip the
    MoveAxis 0/0 zeroing — AbortSlew alone does not reliably stop a MoveAxis on
    real drivers. The 0/0 must still be attempted, and the original error
    re-raised so the hub watchdog retries."""
    from astrodeck.devices.alpaca import AlpacaTelescope

    class _StubConn:
        host = "127.0.0.1"
        port = 11111

        def __init__(self) -> None:
            self.puts: list[tuple[str, dict]] = []

        async def put(self, dev_type, dev_num, method, **params):
            self.puts.append((method, params))
            if method == "abortslew":
                # httpx transport failure (the dropped-network case).
                raise httpx.ConnectError("connection reset")
            # moveaxis succeeds

    conn = _StubConn()
    tel = AlpacaTelescope(conn, 0, "Mock")

    with pytest.raises((httpx.HTTPError, DeviceError)):
        await tel.stop()

    methods = [m for m, _ in conn.puts]
    # abortslew was attempted AND, despite its failure, both axes were zeroed.
    assert "abortslew" in methods
    moveaxis = [p for m, p in conn.puts if m == "moveaxis"]
    rates = {(p["Axis"], p["Rate"]) for p in moveaxis}
    assert (0, 0) in rates, f"RA axis not zeroed: {moveaxis}"
    assert (1, 0) in rates, f"DEC axis not zeroed: {moveaxis}"


async def test_alpaca_stop_succeeds_when_both_calls_ok():
    """The happy path still works: abortslew + both MoveAxis(0) succeed, no
    raise."""
    from astrodeck.devices.alpaca import AlpacaTelescope

    class _OkConn:
        host = "127.0.0.1"
        port = 11111

        def __init__(self) -> None:
            self.puts: list[str] = []

        async def put(self, dev_type, dev_num, method, **params):
            self.puts.append(method)

    conn = _OkConn()
    tel = AlpacaTelescope(conn, 0, "Mock")
    await tel.stop()   # must not raise
    assert "abortslew" in conn.puts
    assert conn.puts.count("moveaxis") == 2


# --------------------------------------------------------------------------- #
# F-A1 / axis-switch — arm RA, then DEC, drop keepalive → BOTH axes halt
# --------------------------------------------------------------------------- #

async def test_axis_switch_then_stale_keepalive_halts_both():
    """Arm RA, then switch to DEC (a real two-finger / axis-change sequence),
    then drop the keepalive: the single deadman must auto-halt BOTH axes —
    neither the abandoned RA nor the live DEC may keep driving."""
    hub = Hub()
    tel = SimTelescope(SimRig())
    await tel.connect()
    hub.devices["telescope"] = tel

    # Arm RA first.
    hub.note_move("ra", 0.5)
    await tel.move_axis("ra", 0.5)
    # Then arm DEC (RA's seen-rate is still non-zero — both are "armed").
    hub.note_move("dec", 0.5)
    await tel.move_axis("dec", 0.5)
    assert hub._move_rates_seen == {"ra": 0.5, "dec": 0.5}

    # Drop the keepalive entirely; within the deadman window both axes go to 0.
    budget = MOVE_DEADMAN_MS / 1000.0 + 0.8
    halted = await _wait_until(
        lambda: tel._move_rates["ra"] == 0 and tel._move_rates["dec"] == 0,
        timeout=budget)
    assert halted, f"both axes not halted: {tel._move_rates}"
    assert hub._move_rates_seen == {"ra": 0.0, "dec": 0.0}

    await hub.disconnect_all()


# --------------------------------------------------------------------------- #
# F-A1 — the deadman is armed BEFORE the move await
# --------------------------------------------------------------------------- #

def test_move_endpoint_arms_deadman_before_move(client, monkeypatch):
    """F-A1: note_move (the only writer of last_move_ts/_move_rates_seen) must be
    called BEFORE tel.move_axis, so a cancel/raise in move_axis can never leave a
    moving axis without watchdog coverage."""
    order: list[str] = []

    class _Tel:
        connected = True

        async def move_axis(self, axis, rate):
            order.append("move_axis")

    monkeypatch.setattr(app_module.hub, "require", lambda role: _Tel())
    monkeypatch.setattr(app_module.hub, "note_move",
                        lambda axis, rate: order.append("note_move"))

    r = client.post("/api/mount/move", json={"axis": "ra", "rate_deg_s": 0.3})
    assert r.status_code == 200, r.text
    assert order == ["note_move", "move_axis"], order


# --------------------------------------------------------------------------- #
# F-S6 — the move axis is constrained to ra|dec (422 on anything else)
# --------------------------------------------------------------------------- #

def test_move_endpoint_rejects_unknown_axis(client, monkeypatch):
    """F-S6 (safety-of-motion): a mistyped axis must 422 at the schema before any
    device call — never silently command Alpaca DEC (axis != 'ra') while arming
    the deadman against a name the watchdog can't zero."""
    called = {"move": 0, "note": 0}

    class _Tel:
        connected = True

        async def move_axis(self, axis, rate):
            called["move"] += 1

    monkeypatch.setattr(app_module.hub, "require", lambda role: _Tel())
    monkeypatch.setattr(app_module.hub, "note_move",
                        lambda axis, rate: called.__setitem__("note", called["note"] + 1))

    for bad in ("alt", "az", "RA", "x", ""):
        r = client.post("/api/mount/move", json={"axis": bad, "rate_deg_s": 0.3})
        assert r.status_code == 422, (bad, r.text)
    # Neither the device nor the deadman was touched for any invalid axis.
    assert called == {"move": 0, "note": 0}

    # The two valid axes still pass.
    for good in ("ra", "dec"):
        r = client.post("/api/mount/move", json={"axis": good, "rate_deg_s": 0.1})
        assert r.status_code == 200, (good, r.text)


# --------------------------------------------------------------------------- #
# F-S5b — an explicit /api/mount/stop disarms the deadman cleanly
# --------------------------------------------------------------------------- #

def test_mount_stop_disarms_deadman(client, monkeypatch):
    """F-S5b: after an explicit STOP zeroes the axes, the deadman must be disarmed
    (note_move ra 0 + dec 0) so no redundant watchdog fire+log follows."""
    notes: list[tuple[str, float]] = []

    class _Tel:
        connected = True

        async def stop(self):
            pass

    monkeypatch.setattr(app_module.hub, "require", lambda role: _Tel())
    monkeypatch.setattr(app_module.hub, "note_move",
                        lambda axis, rate: notes.append((axis, rate)))

    r = client.post("/api/mount/stop")
    assert r.status_code == 200, r.text
    assert ("ra", 0.0) in notes and ("dec", 0.0) in notes


# --------------------------------------------------------------------------- #
# F-P1.6 — force in the sequence-start BODY actually bypasses the horizon 409
# (cross-lane contract: FIX-D POSTs `{ ...plan, force }`).
# --------------------------------------------------------------------------- #

def _below_horizon_light_plan() -> dict:
    """A single below-horizon LIGHT target (not calibration) so the preflight is
    actually exercised."""
    return {
        "name": "lights",
        "guide": False,
        "targets": [{
            "name": "M-below", "ra_hours": 0.0, "dec_deg": 0.0,
            "calibration": False,
            "steps": [{"exposure_s": 1.0, "count": 2, "frame_type": "Light"}],
        }],
    }


def test_sequence_start_force_in_body_bypasses_horizon_409(client, monkeypatch):
    """F-P1.6: the UI posts `{ ...plan, force }` (flat body). Without force the
    below-horizon target is 409'd; with `force: true` in the BODY the run starts.
    Proves force is read from the body, not a now-removed query param."""
    # Make the horizon check reject the dummy coords deterministically.
    def reject(ra, dec, *, force=False):
        raise DeviceError("target is below the visible horizon (alt -42°)")
    monkeypatch.setattr(app_module.hub, "_check_horizon", reject)

    started = {"n": 0}
    monkeypatch.setattr(app_module.engine, "start",
                        lambda plan, **kw: started.__setitem__("n", started["n"] + 1))
    monkeypatch.setattr(app_module.hub, "require", lambda role: object())

    plan = _below_horizon_light_plan()

    # No force → blocked.
    r = client.post("/api/sequence/start", json=plan)
    assert r.status_code == 409, r.text
    assert r.json()["detail"]["code"] == "below_horizon"
    assert started["n"] == 0

    # force in the BODY (flat, alongside the plan fields) → bypasses the 409.
    r = client.post("/api/sequence/start", json={**plan, "force": True})
    assert r.status_code == 200, r.text
    assert r.json() == {"started": True, "frames": 2}
    assert started["n"] == 1


def test_sequence_start_force_false_query_param_is_ignored(client, monkeypatch):
    """A leftover `?force=true` query string must NOT bypass the gate (force is a
    body field now). Belt-and-braces that the query param was actually removed."""
    def reject(ra, dec, *, force=False):
        raise DeviceError("target is below the visible horizon (alt -42°)")
    monkeypatch.setattr(app_module.hub, "_check_horizon", reject)
    monkeypatch.setattr(app_module.engine, "start", lambda plan, **kw: None)
    monkeypatch.setattr(app_module.hub, "require", lambda role: object())

    plan = _below_horizon_light_plan()
    r = client.post("/api/sequence/start?force=true", json=plan)
    assert r.status_code == 409, r.text
