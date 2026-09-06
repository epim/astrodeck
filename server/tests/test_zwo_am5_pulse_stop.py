"""GN-02: a pulse's STOP command cannot be delayed by an event-loop stall.

WHAT WENT WRONG (rig, 2026-09-06). ``ZwoAm5Telescope.pulse_guide`` emulates a
pulse as "start moving, ``await asyncio.sleep(ms)``, stop moving". The mount
moves at 15 arcsec/s while that sleep runs, so the sleep is not a delay — it is
the LENGTH OF THE MOVE. Anything that blocks the loop thread (a synchronous
frame readout, a filter-wheel move, thumbnail generation) therefore lengthens
the move by exactly as long as it blocks: the guider recorded +83, +128 and
+35 arcsec jumps at 02:11:40 and 02:12:20, each decaying over 12-30 s as the
loop caught up (fixture ``ngc604_20260906/tail_Ha300.fits.gz``).

The fix has two halves and both are pinned here: the WHOLE pulse -- start, wait
and stop -- runs on one worker thread through ``SerialLink.request_sync``, so
the loop is not in the timing path at all (arming a timer FROM the loop was not
enough: the arming itself waits for the loop, leaving a window where the move is
running and no watchdog exists yet); and any single pulse is capped at
``_PULSE_MAX_MS`` so even a stop that fails outright costs 15 arcsec.
"""
from __future__ import annotations

import asyncio
import threading
import time

import pytest

from astrodeck.devices.serial_link import LinkError
import astrodeck.devices.backends.zwo_am5 as am5

from test_zwo_am5 import FakeLink, FIXED_UTC, _connect_script  # rootdir-relative


@pytest.fixture
def fixed_env(monkeypatch):
    monkeypatch.setattr(am5, "_utcnow", lambda: FIXED_UTC)
    monkeypatch.setattr(am5, "_site_latlon",
                        lambda: (40.0, -(100 + 30 / 60 + 30 / 3600)))


#: How long the test blocks the loop thread. Longer than any pulse it starts,
#: so a stop that waits for the loop is unmistakably late.
STALL_S = 2.0


async def _connected_tel(script, cls=FakeLink):
    fl = cls(script)
    tel = am5.ZwoAm5Telescope(fl)
    await tel.connect()
    fl.sent.clear()
    fl.sent_at.clear()
    fl.sync_sent.clear()
    return fl, tel


def _t(fl, cmd: str) -> float:
    """Monotonic timestamp of the FIRST send of ``cmd``."""
    for c, ts in fl.sent_at:
        if c == cmd:
            return ts
    raise AssertionError(f"{cmd!r} was never sent (sent={fl.sent})")


async def _wait_sent(fl, cmd: str) -> None:
    """Wait until ``cmd`` has left the driver (the pulse is now in flight).

    Real time, not ``sleep(0)``: the pulse runs on a worker thread, so a tight
    yield loop can finish before that thread has been scheduled at all."""
    for _ in range(400):
        if cmd in fl.sent:
            return
        await asyncio.sleep(0.005)
    raise AssertionError(f"{cmd!r} never went out (sent={fl.sent})")


# ------------------------------------------------------- the stall itself

async def test_east_stop_is_not_delayed_by_a_stalled_loop(fixed_env):
    """East suspends tracking (:Td#) and resumes it (:Te#). Two seconds of
    blocked loop between them is 30 arcsec of unwanted eastward drift."""
    fl, tel = await _connected_tel(_connect_script(GAT="1", Td="1", Te="1"))
    t0 = time.monotonic()
    task = asyncio.create_task(tel.pulse_guide("east", 200))
    await _wait_sent(fl, "Td")

    time.sleep(STALL_S)                 # the frame readout / wheel move

    await task
    elapsed = time.monotonic() - t0

    started = _t(fl, "Td")
    assert _t(fl, "Te") - started < 0.3, (
        f"the stop waited for the loop: Te at +{_t(fl, 'Te') - started:.2f}s")
    # A double stop is harmless (idempotent); a LATE one is the defect.
    assert 1 <= fl.sent.count("Te") <= 2, fl.sent
    for cmd, ts in fl.sent_at:
        if cmd == "Te":
            assert ts - started < 0.3, f"a later Te at +{ts - started:.2f}s"
    assert elapsed >= STALL_S, "the loop was not actually stalled"


@pytest.mark.parametrize("direction, move, stop", [
    ("west", "Mw", "Qw"),
    ("north", "Mn", "Qn"),
    ("south", "Ms", "Qs"),
])
async def test_west_and_dec_stops_are_not_delayed(fixed_env, direction, move,
                                                  stop):
    fl, tel = await _connected_tel(_connect_script())
    t0 = time.monotonic()
    task = asyncio.create_task(tel.pulse_guide(direction, 200))
    await _wait_sent(fl, move)

    time.sleep(STALL_S)

    await task
    started = _t(fl, move)
    assert _t(fl, stop) - started < 0.3, (
        f"{stop} waited for the loop: +{_t(fl, stop) - started:.2f}s")
    assert 1 <= fl.sent.count(stop) <= 2, fl.sent
    assert time.monotonic() - t0 >= STALL_S


# ------------------------------------------------------------------ the cap

async def test_pulse_is_capped_at_one_second(fixed_env, bus_lines, monkeypatch):
    """Even a stall that outlives the watchdog costs at most 15 arcsec, because
    the pulse itself is bounded. The warning is rate-limited: a guider asking
    for oversized pulses would otherwise write one log line per correction."""
    monkeypatch.setattr(am5, "_cap_warn_last", 0.0)
    fl, tel = await _connected_tel(_connect_script(GAT="1", Td="1", Te="1"))

    t0 = time.monotonic()
    await tel.pulse_guide("east", 2500)
    elapsed = time.monotonic() - t0

    assert am5._PULSE_MAX_MS == 1000
    assert 0.9 <= elapsed <= 1.5, f"a 2500 ms request ran for {elapsed:.2f}s"
    capped = [m for _lvl, m, _src in bus_lines if "capped" in m]
    assert len(capped) == 1, capped
    assert "2500" in capped[0] and "1000" in capped[0], capped[0]

    await tel.pulse_guide("north", 4000)        # second oversize, same minute
    assert [m for _lvl, m, _src in bus_lines if "capped" in m] == capped


# ------------------------------------------------------------- the fallbacks

class _StopFailLink(FakeLink):
    """The move goes out; the STOP hits a port that died mid-pulse."""

    STOPS = ("Te", "Qe", "Qw", "Qn", "Qs")

    def __init__(self, script=None):
        super().__init__(script)
        self.sync_attempts: list[str] = []

    def request_sync(self, cmd, *, reply="hash", timeout=1.5):
        self.sync_attempts.append(cmd)
        if cmd in self.STOPS:
            raise LinkError("the link was dropped after a stalled exchange "
                            "and has not been reopened")
        out = self._exchange(cmd, reply)
        self.sync_sent.append(cmd)
        return out


async def test_a_stop_the_thread_could_not_write_goes_out_on_the_loop(
        fixed_env, bus_lines):
    """THE RELINK DOOR. The pulse thread cannot reopen a dropped port -- only
    ``_request``/``_relink`` can -- so a stop it failed to write has to come
    back to the loop rather than leave the mount running."""
    fl, tel = await _connected_tel(_connect_script(GAT="1", Td="1", Te="1"),
                                   cls=_StopFailLink)
    task = asyncio.create_task(tel.pulse_guide("east", 200))
    await _wait_sent(fl, "Td")

    time.sleep(1.0)

    await task
    assert fl.sync_attempts == ["Td", "Te"], "the thread never tried the stop"
    assert fl.sync_sent == ["Td"]                     # the stop never landed
    assert fl.sent.count("Te") == 1, fl.sent          # the async path sent it
    assert any("could not stop" in m for _lvl, m, _src in bus_lines), bus_lines


async def test_a_healthy_pulse_sends_exactly_the_three_commands(fixed_env):
    """The overwhelming majority of pulses run with nothing wrong. Those send
    one rate, one move and one stop -- no duplicate stop, no thread left behind
    (a guider fires several of these a second, all night)."""
    fl, tel = await _connected_tel(_connect_script())

    await tel.pulse_guide("north", 50)

    assert fl.sent == ["R1", "Mn", "Qn"]
    assert fl.sync_sent == ["R1", "Mn", "Qn"]         # all three off the loop
    # A pulse BORROWS a pooled worker (``asyncio.to_thread``); it must never
    # spawn one of its own, or a night of guiding is a hundred thousand
    # threads. Pin the pool at two and fire six pulses: a per-pulse thread
    # would push the count straight past it.
    from concurrent.futures import ThreadPoolExecutor
    loop = asyncio.get_running_loop()
    pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="pulse-pool")
    loop.set_default_executor(pool)
    try:
        base = threading.active_count()
        for _ in range(6):
            fl.sent.clear()
            fl.sync_sent.clear()
            await tel.pulse_guide("north", 20)
            assert fl.sent == ["R1", "Mn", "Qn"]
        assert threading.active_count() <= base + 2, "a thread per pulse"
    finally:
        loop.set_default_executor(ThreadPoolExecutor())
        pool.shutdown(wait=True)


async def test_the_cap_is_published_for_the_guider(fixed_env):
    """A guider has to be able to ask how long one pulse may be, or it will
    keep computing corrections the mount silently truncates."""
    from astrodeck.devices.base import Telescope
    assert Telescope.max_pulse_ms is None              # unknown by default
    assert am5.ZwoAm5Telescope.max_pulse_ms == am5._PULSE_MAX_MS


# ------------------------------------------------- a refused stop is a failure

class _RefusingLink(FakeLink):
    """The :Te# is ANSWERED, with a refusal ('0'): the mount would not resume
    tracking. The old loop-side path raised on that, and moving the send to a
    thread must not turn it into a silent success."""

    def request_sync(self, cmd, *, reply="hash", timeout=1.5):
        self.sync_sent.append(cmd)
        self.sent.append(cmd)
        self.sent_at.append((cmd, time.monotonic()))
        if reply != "ack":
            return None
        return "0" if cmd == "Te" else "1"      # the suspend is accepted


async def test_a_refused_resume_is_raised_not_swallowed(fixed_env,
                                                       bus_lines):
    """Stalled loop, the pulse thread sends :Te#, the mount answers '0'. The
    thread cannot raise, so the refusal is carried back and raised by the
    coroutine -- the guider must learn tracking is OFF instead of guiding a
    star that is drifting east at sidereal rate."""
    from astrodeck.devices.base import DeviceError
    fl, tel = await _connected_tel(_connect_script(GAT="1", Td="1", Te="0"),
                                   cls=_RefusingLink)
    task = asyncio.create_task(tel.pulse_guide("east", 200))
    await _wait_sent(fl, "Td")

    time.sleep(1.0)

    with pytest.raises(DeviceError, match="rejected"):
        await task
    # The WHOLE pulse ran on the thread: the suspend as well as the resume.
    assert fl.sync_sent == ["Td", "Te"], "the pulse thread never tried"
    # ONE :Te# on the wire: the thread already got the mount's answer, so a
    # re-send would only ask a question that has been answered.
    assert fl.sent.count("Te") == 1, fl.sent
    assert any("could not stop" in m for _lvl, m, _src in bus_lines), bus_lines


# ------------------------------------- end to end over the real SerialLink

class _StampingSerial:
    """pyserial stand-in that timestamps every write (thread-safe)."""

    def __init__(self):
        self._lk = threading.Lock()
        self.writes: list[tuple[bytes, float]] = []

    def reset_input_buffer(self):
        pass

    def write(self, b):
        with self._lk:
            self.writes.append((bytes(b), time.monotonic()))

    def read(self, n=1):
        time.sleep(0.005)
        return b""

    def close(self):
        pass


async def test_stop_leaves_through_the_real_serial_link_while_stalled(fixed_env):
    """The two halves together: the driver's watchdog and SerialLink's thread
    door, with the loop blocked. A north pulse needs no replies (R1, Mn, Qn are
    fire-and-forget), so the handshake is skipped and the port is planted."""
    from astrodeck.devices.serial_link import SerialLink
    ser = _StampingSerial()
    link = SerialLink("COM-TEST")
    link._ser = ser
    tel = am5.ZwoAm5Telescope(link)
    tel._connected = True

    task = asyncio.create_task(tel.pulse_guide("north", 200))
    for _ in range(200):
        if any(w == b":Mn#" for w, _ in ser.writes):
            break
        await asyncio.sleep(0.01)
    else:
        raise AssertionError(f"Mn never went out: {ser.writes}")

    time.sleep(STALL_S)

    await task
    t_move = next(ts for w, ts in ser.writes if w == b":Mn#")
    t_stop = next(ts for w, ts in ser.writes if w == b":Qn#")
    assert t_stop - t_move < 0.3, f"Qn waited for the loop: +{t_stop - t_move:.2f}s"
