# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""#1071: the tracking-truth probe (``Telescope.probe_ra_drift``) on the AM5
driver, over a fake serial link whose RA follows the real clock.

``:GAT#`` can say tracking on while the RA drive is slow or stopped. The
probe reads the mount's own RA twice, with nothing moving it in between, and
returns the DIFFERENCE as a rate: about 0 for a tracking mount, about 1
sidereal (15.041 arcsec per second) for a stopped one. The fake link here is
the ``test_zwo_am5`` FakeLink with a ``:GR#`` that answers HH:MM:SS from a
model, so the quantisation the real mount has (one second of time) is in the
test and not assumed away. Every RA below is fictional.

Each test names the mutant of ``zwo_am5.py`` (or ``tracking_probe.py``) it was
shown RED under, from a byte backup restored afterwards.
"""
from __future__ import annotations

import asyncio
import math as _math
import time

import pytest

import astrodeck.devices.backends.zwo_am5 as am5
from astrodeck.devices.base import Telescope
from astrodeck.devices.sim import SimRig, SimTelescope
from astrodeck.tracking_probe import probe_line, run_probe

from test_zwo_am5 import FIXED_UTC, FakeLink, _connect_script

#: Sidereal seconds of time per second of clock time.
SIDEREAL = 1.0027379
PROBE_S = 2.0


@pytest.fixture
def fixed_env(monkeypatch):
    monkeypatch.setattr(am5, "_utcnow", lambda: FIXED_UTC)
    monkeypatch.setattr(am5, "_site_latlon",
                        lambda: (40.0, -(100 + 30 / 60 + 30 / 3600)))


class RaLink(FakeLink):
    """A link whose ``:GR#`` is a model: RA starts at ``start_s`` seconds of
    time on the first read after ``arm()`` and then moves ``rate`` seconds of
    time per clock second, printed as the real mount prints it (HH:MM:SS, the
    seconds floored)."""

    def __init__(self, start_s: float, rate: float, *, gat: str = "1",
                 pier: str = "E"):
        super().__init__(_connect_script(
            GAT=gat, Gm=pier, Gps="0", Td="1", Te="1", GD="+10*00:00"))
        self.start_s, self.rate = start_s, rate
        self.t_ref: float | None = None
        self.script["GR"] = self._gr

    def arm(self) -> None:
        self.t_ref = None

    def _gr(self, _cmd: str) -> str:
        now = time.monotonic()
        if self.t_ref is None:
            self.t_ref = now
        total = (self.start_s + self.rate * (now - self.t_ref)) % 86400.0
        h, rem = divmod(int(total), 3600)
        m, s = divmod(rem, 60)
        return f"{h:02d}:{m:02d}:{s:02d}"


class VClock:
    """A monotonic clock that only the driver's sleeps advance. ``hooks`` maps
    a sleep's ordinal to a coroutine function run right after that sleep, so a
    test can put a command inside the probe window."""

    def __init__(self) -> None:
        self.t = 5000.0
        self.sleeps = 0
        self.hooks: dict = {}

    def monotonic(self) -> float:
        return self.t


@pytest.fixture
def vclock(monkeypatch):
    clock = VClock()
    real_time, real_asyncio = am5.time, am5.asyncio

    class _Time:
        def __getattr__(self, name):
            return getattr(real_time, name)

        monotonic = staticmethod(clock.monotonic)

    class _Asyncio:
        def __getattr__(self, name):
            return getattr(real_asyncio, name)

        @staticmethod
        async def sleep(s, *a):
            clock.t += s
            clock.sleeps += 1
            hook = clock.hooks.get(clock.sleeps)
            if hook is not None:
                await hook()
            await real_asyncio.sleep(0)

    monkeypatch.setattr(am5, "time", _Time())
    monkeypatch.setattr(am5, "asyncio", _Asyncio())
    return clock


def _hms(total: float, decimals: int) -> str:
    scale = 10 ** decimals
    u = int(_math.floor((total % 86400.0) * scale + 1e-9))
    h, rem = divmod(u, 3600 * scale)
    m, su = divmod(rem, 60 * scale)
    s = f"{su // scale:02d}" + (f".{su % scale:0{decimals}d}" if decimals else "")
    return f"{h:02d}:{m:02d}:{s}"


class ClockedLink(RaLink):
    """RA advances ``rate`` seconds of time per second of the virtual clock
    from ``start_s``; Dec moves ``dec_rate`` arcsec per second from
    ``dec_start`` degrees. ``ra_decimals`` is the digits after the seconds."""

    def __init__(self, clock: VClock, start_s: float, rate: float, *,
                 ra_decimals: int = 0, dec_start: float = 12.5,
                 dec_rate: float = 0.0, **kw):
        super().__init__(start_s, rate, **kw)
        self.clock = clock
        self.ra_decimals = ra_decimals
        self.dec_start, self.dec_rate = dec_start, dec_rate
        self.script["GR"] = self._gr_clock
        self.script["GD"] = self._gd_clock

    def arm(self) -> None:
        self.t_ref = self.clock.t

    def _gr_clock(self, _cmd: str) -> str:
        return _hms(self.start_s + self.rate * (self.clock.t - self.t_ref),
                    self.ra_decimals)

    def _gd_clock(self, _cmd: str) -> str:
        deg = self.dec_start + self.dec_rate * (self.clock.t - self.t_ref) / 3600.0
        sign = "-" if deg < 0 else "+"
        total = int(_math.floor(abs(deg) * 3600.0 + 1e-9))
        d, rem = divmod(total, 3600)
        m, s = divmod(rem, 60)
        return f"{sign}{d:02d}*{m:02d}:{s:02d}"



async def _tel(link: RaLink) -> am5.ZwoAm5Telescope:
    tel = am5.ZwoAm5Telescope(link)
    await tel.connect()
    link.arm()
    link.sent.clear()
    return tel


async def test_a_tracking_mount_reads_zero(fixed_env):
    """RA holds still: a drift of 0, the flags and the pier side as the
    mount said, the resolution one second of time over the interval.

    MUTANT 1 (the fit divided by nothing): not graded here; see the sidereal
    case."""
    fl = RaLink(10 * 3600 + 5, 0.0)
    tel = await _tel(fl)
    r = await tel.probe_ra_drift(PROBE_S)
    assert r["ra_drift_arcsec_s"] == 0.0 and r["sidereal_fraction"] == 0.0
    assert r["tracking_flag_before"] is True and r["tracking_flag_after"] is True
    assert r["pier_side"] == "east"
    assert PROBE_S <= r["seconds"] < PROBE_S + 0.5
    # 15 arcsec (one second of time) over the interval; "seconds" is rounded
    # to 0.01 s, so the quotient is good to about 0.05.
    assert r["resolution_arcsec_s"] == pytest.approx(15.0 / r["seconds"],
                                                     abs=0.1)
    assert 6.5 < r["resolution_arcsec_s"] <= 7.5
    assert r["disturbed"] is False and r["cut_short"] is False


async def test_a_stopped_drive_that_still_says_tracking_reads_one_sidereal(
        fixed_env, vclock):
    """The case the probe exists for: ``:GAT#`` answers 1 (tracking on) while
    the reported RA advances at the sidereal rate. The flag and the rate
    disagree, and the result carries both.

    MUTANT 1 (the fit replaced by ``ra_y[-1]`` without the division by
    ``elapsed``): RED, a fraction of ~10. MUTANT 2 (``SIDEREAL_ARCSEC_S`` set
    to 1.0): RED, a fraction of ~15. (Round 2: virtual clock, a 10 s window.)"""
    fl = ClockedLink(vclock, 10 * 3600 + 5, SIDEREAL, gat="1")
    tel = await _tel(fl)
    r = await tel.probe_ra_drift(10.0)
    assert r["tracking_flag_before"] is True
    assert 0.95 < r["sidereal_fraction"] < 1.05, r
    assert 14.3 < r["ra_drift_arcsec_s"] < 15.8, r


async def test_the_24_hour_wrap_is_handled(fixed_env, vclock):
    """A stopped mount crossing 24:00:00 between the two reads: 23:59:59 then
    00:00:01 is two seconds of time forward, not 24 hours back.

    MUTANT 3 (the ``(... + 12.0) % 24.0 - 12.0`` wrap removed from the sample
    list in ``probe_ra_drift``): RED, a fraction of about -4000."""
    fl = ClockedLink(vclock, 86399.0, SIDEREAL)
    tel = await _tel(fl)
    r = await tel.probe_ra_drift(10.0)
    assert 0.95 < r["sidereal_fraction"] < 1.05, r


async def test_nothing_but_reads_goes_on_the_wire_in_the_window(fixed_env):
    """No motion command and no tracking command inside the probe: it reads
    ``:GAT#``, then ``:GR#`` and ``:GD#`` in pairs about every quarter second
    (round 2: sampled, not two endpoint reads), then ``:GAT#`` and the pier
    side.

    MUTANT 4 (a ``:Q#`` sent before the last sample): RED, ``Q`` in the
    list."""
    fl = RaLink(10 * 3600, 0.0)
    tel = await _tel(fl)
    await tel.probe_ra_drift(PROBE_S)
    assert fl.sent[0] == "GAT" and fl.sent[-2:] == ["GAT", "Gm"], fl.sent
    middle = fl.sent[1:-2]
    assert set(middle) == {"GR", "GD"}, fl.sent
    assert middle[0::2] == ["GR"] * (len(middle) // 2)
    assert middle[1::2] == ["GD"] * (len(middle) // 2)


async def test_a_pulse_waits_for_the_window(fixed_env):
    """The probe holds ``_pulse_park_lock``: a pulse asked for while it runs
    puts its first byte on the wire only after the probe's last read.

    MUTANT 5 (``async with self._pulse_park_lock:`` removed from
    ``probe_ra_drift``): RED, ``R2`` sent while the probe is waiting."""
    fl = RaLink(10 * 3600, 0.0)
    tel = await _tel(fl)
    probe = asyncio.create_task(tel.probe_ra_drift(PROBE_S))
    while "GR" not in fl.sent:
        await asyncio.sleep(0.01)
    pulse = asyncio.create_task(tel.pulse_guide("west", 50))
    await asyncio.gather(probe, pulse)
    assert fl.sent.index("R2") > fl.sent.index("Gm"), fl.sent


async def test_a_waiting_park_gets_the_lock_back_at_once(fixed_env):
    """A park is the safety path and must not wait out a ten second probe: a
    park waiting for the lock cuts the window short, the reads still happen,
    and the result says ``cut_short``.

    MUTANT 6 (the ``_park_waiting`` test removed from the wait loop): RED,
    the probe takes the full 5 s and ``cut_short`` is False."""
    fl = RaLink(10 * 3600, 0.0)
    tel = await _tel(fl)
    probe = asyncio.create_task(tel.probe_ra_drift(5.0))
    while "GR" not in fl.sent:
        await asyncio.sleep(0.01)
    t0 = time.monotonic()

    async def park_like():
        async with tel._park_lock():
            return time.monotonic()

    got = await asyncio.wait_for(park_like(), 3.0)
    r = await probe
    assert got - t0 < 1.0, f"the park waited {got - t0:.2f} s for the lock"
    assert r["cut_short"] is True
    assert r["seconds"] < 2.0
    assert tel._park_waiting == 0


async def test_a_slew_in_the_window_marks_the_result_disturbed(fixed_env):
    """A goto does not take the pulse/park lock, so a slew inside the window
    is seen by the flag and the rate is marked as not valid.

    MUTANT 7 (``disturbed`` always False): RED."""
    fl = RaLink(10 * 3600, SIDEREAL)
    tel = await _tel(fl)
    tel._slewing = True
    r = await tel.probe_ra_drift(PROBE_S)
    assert r["disturbed"] is True
    assert "not valid" in probe_line("t", r, None)


async def test_a_mount_that_cannot_probe_answers_none_and_says_nothing(
        bus_lines):
    """The default, and the sim: None, no wait, no line.

    MUTANT 8 (``can_probe_ra_drift`` True on the base class): RED, the sim
    is asked and ``run_probe`` would log."""
    tel = SimTelescope(SimRig())
    await tel.connect()
    assert Telescope.can_probe_ra_drift is False
    assert await tel.probe_ra_drift(1.0) is None
    assert await run_probe(tel, "x") is None
    assert bus_lines == []


async def test_a_probe_that_fails_is_logged_and_ignored(fixed_env, bus_lines):
    """The mount stops answering ``:GR#``: ``run_probe`` returns None and logs
    one warning; nothing is raised into the guide loop or the frame loop.

    MUTANT 9 (``except Exception`` in ``run_probe`` narrowed to
    ``except LinkError``): RED, DeviceError propagates."""
    fl = RaLink(10 * 3600, 0.0)
    tel = await _tel(fl)
    del fl.script["GR"]
    assert await run_probe(tel, "t", seconds=1.0) is None
    warns = [m for lv, m, _s in bus_lines if lv == "warning"]
    assert len(warns) == 1 and "tracking probe (t) failed" in warns[0]


async def test_no_ra_string_reaches_the_bus_log_or_the_result(fixed_env,
                                                               bus_lines):
    """The two readings are 23:59:59 and 00:00:01. Neither string, nor either
    as hours (23.9997, 0.0003) or as degrees, appears in any log line the
    probe causes or in the result dict.

    MUTANT 10 (the result carries ``"ra_before": ra0``): RED."""
    fl = RaLink(86399.0, SIDEREAL)
    tel = await _tel(fl)
    r = await run_probe(tel, "privacy", seconds=PROBE_S, since_flip_min=12.0)
    assert r is not None
    text = "\n".join(m for _lv, m, _s in bus_lines) + repr(r)
    for needle in ("23:59:59", "00:00:01", "23.99", "0.0003", "359.9",
                   "86399"):
        assert needle not in text, (needle, text)
    assert len(bus_lines) == 1
    line = bus_lines[0][1]
    assert line.startswith("tracking probe (privacy): RA drift +")
    assert "12 min since the last flip" in line and "pier side east" in line
