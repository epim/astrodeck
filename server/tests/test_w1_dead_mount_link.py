# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""WP-02: the dead mount link (#133) and the park-state claim it hid (#138).

THE NIGHT. 2026-09-23: a USB re-enumeration left the AM5's serial handle dead
at 01:36:38. The write raised pyserial's ``SerialException`` -- an ``OSError``
subclass -- and ``SerialLink`` let it through unchanged, so ``is_open``
(``_ser is not None``) stayed True and every caller's ``connected`` (derived
from it) stayed True for the next eight-plus hours. Dawn park's own reopen,
written for the 2026-08-09 outage, could only run when ``connected`` read
False, so it called the identical failing ``tel.park()`` more than 180 times
without once trying the one command that would have fixed it. On the same
morning, dawn park also logged "the mount is unparked" on the very tick it had
just logged that it could not read the mount's park state -- a claim nobody
measured (#138).

This file pins both fixes:
  (a) ``serial_link.SerialLink`` turns a write/read ``OSError`` (including
      ``SerialException``) into ``LinkError`` and abandons the handle, and
      ``dawn_park.DawnPark`` forces a reopen after
      ``PARK_FAILURES_BEFORE_FORCED_REOPEN`` consecutive park failures even
      when the telescope still claims ``connected`` -- not only when that
      flag is already False.
  (b) ``DawnPark._is_parked`` answers ``None``, not a claimed ``False``, when
      the read itself failed, so the intent line says "could not be read"
      rather than asserting "unparked".

All coordinates below are fictional (site privacy) -- the same made-up mid-
latitude site ``test_dawn_park.py`` uses, not this rig's own.
"""
from __future__ import annotations

import asyncio

import pytest
import serial

from astrodeck import dawn_park as dawn_park_mod
from astrodeck.catalog.coords import sun_altaz
from astrodeck.config import AppConfig
from astrodeck.dawn_park import DawnPark, park_threshold_deg
from astrodeck.devices.serial_link import LinkError, SerialLink

# ==================================================================
# (a) serial_link.py: a write/read OSError must abandon the handle.
# ==================================================================

_WRITEFILE_ERROR = (
    "WriteFile failed (PermissionError(13, 'The device does not recognize "
    "the command.', None, 22))")


class _DeadSerial:
    """A pyserial stand-in whose write or read raises the EXACT exception
    class the AM5 produced on 2026-09-23: ``serial.SerialException``, which
    is an ``OSError`` subclass, not a ``LinkError``. ``SerialLink`` is the one
    place both routes (``request`` and ``request_sync``) share, so a fix
    there covers the guider's pulse thread as well as every ordinary command.
    """

    def __init__(self, *, fail_on: str) -> None:
        self.fail_on = fail_on
        self.closed = False

    def reset_input_buffer(self) -> None:
        if self.fail_on == "reset_input_buffer":
            raise serial.SerialException(_WRITEFILE_ERROR)

    def write(self, _b) -> None:
        if self.fail_on == "write":
            raise serial.SerialException(_WRITEFILE_ERROR)

    def read(self, _n=1):
        if self.fail_on == "read":
            raise serial.SerialException("ReadFile failed (device gone)")
        return b""

    def close(self) -> None:
        self.closed = True


async def test_a_write_oserror_abandons_the_link_and_raises_link_error():
    """The exact 2026-09-23 failure. Before this fix, ``SerialException``
    (an ``OSError``) passed through ``_raw_exchange`` unchanged: ``_ser`` was
    left in place, so ``is_open`` stayed True and every caller derived
    ``connected`` from it -- which is why nothing ever reopened the port.

    Named mutant: ``serial_link.py``'s ``except OSError as exc:`` in
    ``_raw_exchange`` changed to ``except LinkError as exc:`` (a
    ``SerialException`` is not a ``LinkError``, so it is no longer caught
    here). FAILS: the ``await link.request("GR")`` raises
    ``serial.SerialException`` instead of the expected ``LinkError``, and
    ``pytest.raises(LinkError)`` reports it as an unhandled exception rather
    than a match.
    """
    link = SerialLink("COM-TEST")
    link._ser = _DeadSerial(fail_on="write")

    with pytest.raises(LinkError):
        await link.request("GR")

    assert link.is_open is False, (
        "a write that raises must drop the handle -- every caller's "
        "`connected` is DERIVED from `is_open`, and a handle left in place "
        "is exactly what let this stay 'connected' for eight-plus hours")
    assert link.needs_reopen is True, (
        "and it must be flagged as a fault to recover from, not a deliberate "
        "close -- that is the only signal a driver's reopen can key on")


async def test_a_read_oserror_also_abandons_the_link():
    """A read can fail the same way a write does (the device vanishes mid-
    exchange), and the 2026-09-23 mechanism does not care which call raised
    -- both go through the one ``_raw_exchange`` this fix covers."""
    link = SerialLink("COM-TEST")
    link._ser = _DeadSerial(fail_on="read")

    with pytest.raises(LinkError):
        await link.request("GR")

    assert link.is_open is False
    assert link.needs_reopen is True


def test_request_sync_gets_the_same_treatment():
    """GN-02's pulse thread calls ``request_sync``, never ``request`` -- a fix
    that only covered the async door would leave a guide pulse's stop command
    reporting a raw ``SerialException`` while the mount looked 'connected'."""
    link = SerialLink("COM-TEST")
    link._ser = _DeadSerial(fail_on="write")

    with pytest.raises(LinkError):
        link.request_sync("Qn", reply="none")

    assert link.is_open is False
    assert link.needs_reopen is True


# ======================================================================
# (a) dawn_park.py: N consecutive park failures force a reopen even when
#     `connected` never drops -- the belt for serial_link's suspenders.
# ======================================================================

# A site that is emphatically not the developer's (matches test_dawn_park.py).
SITE_LAT, SITE_LON = 45.0, -110.0
JUNE_TS = 1780272000.0            # 2026-06-01T00:00:00Z


def _find_sky(lat, lon, pred, start, span_h=48.0, step_s=60.0):
    t = start
    end = start + span_h * 3600.0
    while t < end:
        alt, _az = sun_altaz(lat, lon, t)
        if pred(alt):
            return t, alt
        t += step_s
    raise AssertionError("no such sky within the scan window")


def _daytime(above: float = 2.0):
    return _find_sky(SITE_LAT, SITE_LON, lambda a: a > above, JUNE_TS)


class FakeTel:
    """Minimal telescope double. ``connected`` is a PLAIN flag here (as it
    would appear to dawn_park once serial_link's own fix is in place, if the
    flag ever lags reality) -- this file is testing dawn_park's GATE, not the
    AM5's derivation of `connected`, which test_zwo_am5.py already owns."""

    def __init__(self, *, park_raises: Exception | None = None,
                 is_parked_raises: Exception | None = None,
                 fixed_by_reopen: bool = True) -> None:
        self.connected = True
        self.parked = False
        self.park_calls = 0
        self.connect_calls = 0
        self._park_raises = park_raises
        self._is_parked_raises = is_parked_raises
        #: Whether a reopen actually fixes the fault. True for a genuinely
        #: dropped link (2026-09-23); False models a mount that is broken for
        #: some OTHER reason, where a reopen is a harmless no-op and the
        #: ordinary failure heartbeat is what has to keep the log legible.
        self._fixed_by_reopen = fixed_by_reopen

    async def connect(self) -> None:
        self.connect_calls += 1
        if self._fixed_by_reopen:
            self._park_raises = None

    async def is_parked(self) -> bool:
        if self._is_parked_raises is not None:
            raise self._is_parked_raises
        return self.parked

    async def park(self) -> None:
        self.park_calls += 1
        if self._park_raises is not None:
            raise self._park_raises
        self.parked = True


class FakeHub:
    def __init__(self, tel) -> None:
        self.site = {"name": "Ridge Test Site", "latitude": SITE_LAT,
                     "longitude": SITE_LON, "elevation_m": 0.0,
                     "is_default": False, "horizon_min_deg": 10.0}
        self.devices = {"telescope": tel}
        self._motion_lock = asyncio.Lock()

    def bump_motion_epoch(self) -> None:
        pass

    def busy_lanes(self) -> list[str]:
        return []


class FakeEngine:
    running = False


@pytest.fixture(autouse=True)
def nothing_armed(monkeypatch):
    """Same isolation test_dawn_park.py uses: a dormant armed session on the
    developer's own disk must not change what these tests see."""
    import astrodeck.sequence.session as sess
    monkeypatch.setattr(sess.session_store, "armed", lambda: None)


@pytest.fixture
def cfg(monkeypatch):
    conf = AppConfig()
    monkeypatch.setattr(dawn_park_mod, "config_store",
                        type("_S", (), {"cfg": staticmethod(lambda: conf)})())
    return conf


def _said(lines, needle: str) -> bool:
    return any(needle in message for _level, message, _source in lines)


async def test_dawn_park_forces_a_reopen_after_repeated_failures_even_when_connected(
        cfg, bus_lines):
    """#133, fix shape (a) (plan 2026-09-30-open-issue-backlog.md, WP-02):
    'After N consecutive park failures dawn_park reopens the link, instead of
    only when connected is False.' On 2026-09-23 `connected` stayed True for
    the whole outage, so the ONLY existing route to a reopen never ran.

    Named mutant: dawn_park.py's
    ``elif self._fail_count >= PARK_FAILURES_BEFORE_FORCED_REOPEN and not
    self._forced_reopen_tried:`` changed to ``elif False:`` (restoring "not
    connected" as the only route to a reopen). FAILS:
    ``assert tel.connect_calls == 1`` -- with the branch dead, dawn park
    calls the identical failing ``tel.park()`` forever and ``connect()`` is
    never called, exactly as on 2026-09-23.
    """
    tel = FakeTel(park_raises=OSError(_WRITEFILE_ERROR))
    hub = FakeHub(tel)
    ts, alt = _daytime()
    assert alt > park_threshold_deg(cfg), "precondition: the Sun is really up"

    d = DawnPark(hub, FakeEngine(), clock=lambda: ts)
    for _ in range(dawn_park_mod.PARK_FAILURES_BEFORE_FORCED_REOPEN):
        await d.tick()
        assert tel.connected is True, "precondition: the flag never drops"
    assert tel.connect_calls == 0, (
        "precondition: nothing has forced a reopen yet -- the streak has not "
        "reached the threshold")

    await d.tick()

    assert tel.connect_calls == 1, (
        f"after {dawn_park_mod.PARK_FAILURES_BEFORE_FORCED_REOPEN} consecutive "
        f"park failures with `connected` still True, dawn park must reopen "
        f"the link anyway")
    assert tel.parked is True, (
        "and the very next park -- now that the reopen fixed the mount -- "
        "must actually succeed, not merely be attempted")


async def test_the_forced_reopen_does_not_cost_an_extra_log_line(cfg, bus_lines):
    """#210's budget still applies to the belt as much as the original net:
    the forced reopen is a guess, not a diagnosis, and must not out-shout the
    ordinary DAWN PARK FAILED heartbeat this net already has. A mount that is
    broken for some OTHER reason (a reopen fixes nothing here) keeps failing
    afterwards exactly as before -- this is the same shape as
    test_dawn_park.py::test_a_failing_net_stays_legible_instead_of_flooding_the_log,
    which pins the other half of the same budget and must also still pass
    unmodified."""
    tel = FakeTel(park_raises=RuntimeError("mount says no"),
                  fixed_by_reopen=False)
    hub = FakeHub(tel)
    ts, alt = _daytime()
    assert alt > park_threshold_deg(cfg), "precondition"

    d = DawnPark(hub, FakeEngine(), clock=lambda: ts)
    for _ in range(dawn_park_mod.FAIL_LOG_EVERY - 1):
        await d.tick()

    assert tel.park_calls == dawn_park_mod.FAIL_LOG_EVERY - 1, (
        "precondition: it really did keep trying every tick")
    assert tel.connect_calls == 1, (
        "the belt fires exactly once per streak, not once per tick")
    # One intent line, one DAWN PARK FAILED line -- the forced reopen itself
    # is silent (see DawnPark._force_reopen_quietly) and changed nothing, so
    # the ordinary heartbeat cadence (next due at attempt 30) still governs.
    assert len(bus_lines) <= 3, bus_lines


async def test_dawn_park_does_not_claim_unparked_when_the_read_failed(
        cfg, bus_lines):
    """#138. ``_is_parked`` folding a failed read into a bare False let
    ``tick``'s intent line report "the mount is unparked" on the same tick it
    had just logged that it could not read the park state at all -- stating a
    measurement the net never took, and throwing away the read failure that
    was 2026-09-23's first direct evidence of the dead link.

    Named mutant: ``_is_parked``'s ``except`` clause changed from
    ``return None`` to ``return False``. FAILS:
    ``assert not _said(bus_lines, "the mount is unparked")`` -- the intent
    line goes back to claiming a state nobody measured.
    """
    tel = FakeTel(is_parked_raises=OSError(_WRITEFILE_ERROR))
    hub = FakeHub(tel)
    ts, alt = _daytime()
    assert alt > park_threshold_deg(cfg), "precondition"

    await DawnPark(hub, FakeEngine(), clock=lambda: ts).tick()

    assert not _said(bus_lines, "the mount is unparked"), bus_lines
    assert _said(bus_lines, "park state could not be read"), bus_lines
    assert tel.park_calls == 1, (
        "an unreadable park state must still park -- idempotence is what "
        "makes the honest 'unknown' wording safe to act on")
