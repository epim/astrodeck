# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""The #851 bench walk script (``tools/bench_am5_pulse_walk.py``), graded
without hardware: its arithmetic, that it prints no position, that it refuses
an over-long pulse or a missing precondition check, and that the one raw
tracking suspend it makes always ends with tracking resumed.

The telescope is a fake at the driver's public interface (``get_position``,
``pulse_guide``, ``set_tracking``, ``get_tracking``, ``set_tracking_rate``),
at fictional coordinates. Every mutant named below was applied to a byte copy
of the script, run under the suite's normal command, and the file restored
from the copy with its sha256 checked.
"""
from __future__ import annotations

import asyncio

import pytest

from tools import bench_am5_pulse_walk as bench

RA0, DEC0 = 5.123456, 35.4321          # fictional


class _Tel:
    """A fake AM5. ``counts`` True: an east pulse advances the reported RA
    by the suspended time at the sidereal rate (the report counts it);
    False: the report never moves."""

    def __init__(self, *, counts: bool, tracking: bool = True,
                 dec: float = DEC0) -> None:
        self.counts = counts
        self.ra = RA0
        self.dec = dec
        self.tracking = tracking
        self.commands: list[str] = []
        self.pulses: list[tuple[str, int]] = []
        self.tracking_reads = 0

    async def get_position(self):
        return self.ra, self.dec

    async def pulse_guide(self, direction: str, ms: int) -> None:
        self.pulses.append((direction, ms))
        if self.counts and direction.lower().startswith("e"):
            self.ra = (self.ra + ms / 1000.0 * 15.041 / 54000.0) % 24.0

    async def set_tracking(self, on: bool) -> None:
        self.commands.append(":Te#" if on else ":Td#")
        self.tracking = on

    async def get_tracking(self) -> bool:
        self.tracking_reads += 1
        return self.tracking

    async def set_tracking_rate(self, rate: str) -> None:
        self.commands.append({"lunar": ":TL#", "sidereal": ":TQ#"}[rate])

    async def disconnect(self) -> None:
        self.commands.append(":Q#")


async def _no_wait(_s: float) -> None:
    return None


async def test_the_walk_reports_deltas_that_match_the_motion():
    """40 east pulses of 500 ms are 20 s of suspend: +300.8" on a report
    that counts them, ~0" on one that does not.

    MUTANT "the wrong unit" (``SIDEREAL_ARCSEC_PER_S = 15.0 * 15``): RED -
        AssertionError: assert 'ignored' == 'counted'
    """
    counted = await bench.run_walk(_Tel(counts=True), direction="east",
                                   pulse_ms=500, count=40, gap_s=1.5,
                                   sleep=_no_wait)
    assert counted["expected_arcsec"] == pytest.approx(300.82, abs=0.01)
    assert counted["d_ra_arcsec"] == pytest.approx(300.82, abs=0.5)
    assert counted["verdict"] == "counted"
    ignored = await bench.run_walk(_Tel(counts=False), direction="east",
                                   pulse_ms=500, count=40, gap_s=1.5,
                                   sleep=_no_wait)
    assert ignored["d_ra_arcsec"] == pytest.approx(0.0, abs=0.01)
    assert ignored["verdict"] == "ignored"


def test_ra_wraps_at_24h():
    """0.002 h across midnight is +108", not -86292".

    MUTANT "no wrap" (``d = (ra1_h - ra0_h) % 24.0`` made ``d = ra1_h -
    ra0_h`` with the ``if d > 12.0`` fold removed): RED -
        AssertionError: assert -1295892.0 == 108.0 ± 1.1e-04
    """
    assert bench.ra_delta_arcsec(23.999, 0.001) == pytest.approx(108.0)
    assert bench.ra_delta_arcsec(0.001, 23.999) == pytest.approx(-108.0)


def test_the_output_carries_no_position():
    """The whole run, every block, through ``main``: only labelled deltas.
    No form of the fake's absolute RA or Dec appears.

    MUTANT "a debug print" (``out(f"start {_ra} {dec}")`` added after the
    first read in ``_bench``): RED -
        AssertionError: a position reached the output: '5.123'
    """
    lines: list[str] = []
    tel = _Tel(counts=True)

    async def opener(port):
        return tel

    code = bench.main(["--port", "X", "--i-checked", "--count", "4",
                       "--long-east", "--lunar"],
                      open_mount=opener, out=lines.append, sleep=_no_wait)
    assert code == 0, lines
    text = "\n".join(lines)
    for needle in ("5.123", "5.12", "05:07", "5h07", "76.85", "35.43",
                   "35:25", "35*25", "+35.4"):
        assert needle not in text, f"a position reached the output: {needle!r}"
    assert lines[-1].endswith(bench.FINAL_ADVICE), lines
    assert len([ln for ln in lines if "ratio" in ln]) == 6, lines


@pytest.mark.parametrize("argv", [
    ["--port", "X", "--i-checked", "--pulse-ms", "1500"],
    ["--port", "X"],
], ids=["a 1500 ms pulse", "no --i-checked"])
def test_long_pulses_and_missing_checks_are_refused(argv):
    """Nothing is opened, nothing is pulsed.

    MUTANT "no cap" (the ``args.pulse_ms > PULSE_CAP_MS or`` test removed
    from ``main``, and ``run_walk``'s own cap check removed): RED, "a 1500
    ms pulse" -
        AssertionError: assert 0 != 0
    """
    opened: list = []
    tel = _Tel(counts=True)

    async def opener(port):
        opened.append(port)
        return tel

    lines: list[str] = []
    code = bench.main(argv, open_mount=opener, out=lines.append,
                      sleep=_no_wait)
    assert code != 0
    assert tel.pulses == [] and opened == [], (tel.pulses, opened)
    assert lines and lines[0].startswith("refused"), lines


@pytest.mark.parametrize("argv, said", [
    (["--count", "10000", "--pulse-ms", "1000"], "is 10000 s of motion"),
    (["--count", "121", "--pulse-ms", "500"], "is 60 s of motion"),
], ids=["10000 x 1000 ms", "121 x 500 ms"])
def test_a_block_past_a_minute_of_motion_is_refused(argv, said):
    """The per-pulse cap does not bound the total: 10000 x 1000 ms would
    drive the west block alone about 42 degrees at the scope. A block over
    ``MAX_BLOCK_MOTION_S`` (60 s, 15' at sidereal) is refused in words before
    anything is opened; 121 x 500 ms is 60.5 s, just past it.

    MUTANT "no block limit" (the ``block_s > MAX_BLOCK_MOTION_S`` test
    removed from ``main`` and ``run_walk``'s block check removed): RED, both -
        assert 0 != 0
    """
    assert bench.MAX_BLOCK_MOTION_S == 60.0
    opened: list = []
    tel = _Tel(counts=True)

    async def opener(port):
        opened.append(port)
        return tel

    lines: list[str] = []
    code = bench.main(["--port", "X", "--i-checked", *argv],
                      open_mount=opener, out=lines.append, sleep=_no_wait)
    assert code != 0
    assert tel.pulses == [] and opened == [], (tel.pulses, opened)
    assert len(lines) == 1 and lines[0].startswith("refused: a block of"), lines
    assert said in lines[0], lines


async def test_control_a_block_of_exactly_a_minute_runs():
    """CONTROL: 120 x 500 ms is exactly 60 s, inside the limit, and runs."""
    r = await bench.run_walk(_Tel(counts=True), direction="east",
                             pulse_ms=500, count=120, gap_s=0.0,
                             sleep=_no_wait)
    assert r["pulses"] == 120 and r["verdict"] == "counted"


def test_the_bench_place_must_be_away_from_the_pole():
    """A Dec readback outside +20..+70 (or no tracking) refuses before any
    pulse, in words with no figure."""
    tel = _Tel(counts=True, dec=88.0)

    async def opener(port):
        return tel

    lines: list[str] = []
    code = bench.main(["--port", "X", "--i-checked"], open_mount=opener,
                      out=lines.append, sleep=_no_wait)
    assert code == 2 and tel.pulses == []
    assert lines == ["refused: too near the pole or not tracking; see the "
                     "preconditions"], lines


@pytest.mark.parametrize("exc", [KeyboardInterrupt, asyncio.CancelledError])
def test_the_long_suspend_always_resumes_tracking(exc):
    """The 10 s suspend is interrupted mid-wait: tracking is resumed (``:Te#``
    is the last command) and read back, and the interrupt still propagates.
    Driven by stepping the coroutine, so no event loop has to survive a
    KeyboardInterrupt.

    MUTANT "no finally" (``try: await sleep(hold_s) finally: ...`` made the
    plain sequence ``await sleep(hold_s)`` then the resume): RED, both -
        AssertionError: assert ':Td#' == ':Te#'
    """
    tel = _Tel(counts=False)

    async def interrupted(_s):
        raise exc()

    coro = bench.long_east(tel, hold_s=10.0, sleep=interrupted)
    with pytest.raises(exc):
        coro.send(None)
    assert tel.commands[-1] == ":Te#", tel.commands
    assert tel.commands.count(":Td#") == 1
    assert tel.tracking_reads >= 1, "tracking was never read back"
