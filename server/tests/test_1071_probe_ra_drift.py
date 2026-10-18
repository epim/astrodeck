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


async def _tel(link: RaLink) -> am5.ZwoAm5Telescope:
    tel = am5.ZwoAm5Telescope(link)
    await tel.connect()
    link.arm()
    link.sent.clear()
    return tel


async def test_a_tracking_mount_reads_zero(fixed_env):
    """RA holds still: a drift of 0, the flags and the pier side as the
    mount said, the resolution one second of time over the interval.

    MUTANT 1 (``drift`` not divided by ``elapsed``, i.e. ``/ 1.0``): not
    graded here; see the sidereal case, which reads 30 instead of 14.9."""
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
        fixed_env):
    """The case the probe exists for: ``:GAT#`` answers 1 (tracking on) while
    the reported RA advances at the sidereal rate. The flag and the rate
    disagree, and the result carries both.

    MUTANT 1 (``drift = d_hours * 15.0 * 3600.0`` with the ``/ elapsed``
    removed): RED, a fraction of 2.0 (30 arcsec over 2 s read as 30 arcsec/s).
    MUTANT 2 (``SIDEREAL_ARCSEC_S`` set to 1.0): RED, a fraction of ~15."""
    fl = RaLink(10 * 3600 + 5, SIDEREAL, gat="1")
    tel = await _tel(fl)
    r = await tel.probe_ra_drift(PROBE_S)
    assert r["tracking_flag_before"] is True
    assert 0.9 < r["sidereal_fraction"] < 1.1, r
    assert 13.5 < r["ra_drift_arcsec_s"] < 16.5, r


async def test_the_24_hour_wrap_is_handled(fixed_env):
    """A stopped mount crossing 24:00:00 between the two reads: 23:59:59 then
    00:00:01 is two seconds of time forward, not 24 hours back.

    MUTANT 3 (the ``(... + 12.0) % 24.0 - 12.0`` wrap removed from
    ``probe_ra_drift``): RED, a fraction of about -43000."""
    fl = RaLink(86399.0, SIDEREAL)
    tel = await _tel(fl)
    r = await tel.probe_ra_drift(PROBE_S)
    assert 0.9 < r["sidereal_fraction"] < 1.1, r


async def test_nothing_but_reads_goes_on_the_wire_in_the_window(fixed_env):
    """No motion command and no tracking command inside the probe: it reads
    ``:GAT#``, ``:GR#``, waits, reads ``:GR#``, ``:GAT#`` and the pier side.

    MUTANT 4 (a ``:Q#`` sent before the second read): RED, ``Q`` in the
    list."""
    fl = RaLink(10 * 3600, 0.0)
    tel = await _tel(fl)
    await tel.probe_ra_drift(PROBE_S)
    assert fl.sent == ["GAT", "GR", "GR", "GAT", "Gm"], fl.sent


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
