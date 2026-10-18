# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""#1071 round 2: what the probe's window measures, and when it says it cannot.

The AM5's reported coordinates are the mount's own MODEL of where it points,
so each number the probe returns has to mean what it says:

* a sync, a goto, a halt or a tracking change inside the window moves the
  model and makes the rate garbage; the driver counts those commands and the
  result names them (``disturbed_by``) (F1);
* two endpoint reads of ``:GR#`` resolve one 15 arcsec count, a tenth of the
  sidereal rate over ten seconds, so the rate is the fitted slope of reads
  taken about every quarter second, and the quantum is read off the reply's
  own format, never assumed (F2);
* Dec is sampled in the same loop, because the 2026-08-21 walk was in Dec (F3).

TIME IS VIRTUAL. The sleeps in ``zwo_am5`` advance a clock the fake link reads,
so a 20 s window takes milliseconds and the staircase a whole-second reply
makes is the same on every run and under any load (the real-clock tests of
round 1 are phase dependent by construction).

Each test names the mutant of the production code it was shown RED under, from
a byte backup restored afterwards (sha256 checked).
"""
from __future__ import annotations

import asyncio
import math

import pytest

import astrodeck.devices.backends.zwo_am5 as am5
from astrodeck.devices import lx200
from astrodeck.tracking_probe import probe_line

from test_1071_probe_ra_drift import (ClockedLink, RaLink, VClock,  # noqa: F401
                                      vclock)
from test_zwo_am5 import (FIXED_UTC, FakeLink, _Am5Model, _connect_script,
                          fixed_env)  # noqa: F401 (fixture import)

SIDEREAL = 1.0027379


def _target_cmds(ra: float, dec: float) -> dict:
    return {f"Sr{lx200.format_ra(ra)}": "1", f"Sd{lx200.format_dec(dec)}": "1"}


async def _tel(link) -> am5.ZwoAm5Telescope:
    tel = am5.ZwoAm5Telescope(link)
    await tel.connect()
    link.arm()
    link.sent.clear()
    return tel


# ------------------------------------------------------------------ F1


async def _idle_tel(vclock, fixed_env):
    fl = ClockedLink(vclock, 10 * 3600.0, 0.0)
    return fl, await _tel(fl)


async def test_an_undisturbed_window_says_so(vclock, fixed_env):
    """No command in the window: ``disturbed`` False and ``disturbed_by``
    an empty list, not missing.

    MUTANT F1-a (``"disturbed": bool(by)`` -> ``True``): RED."""
    _fl, tel = await _idle_tel(vclock, fixed_env)
    r = await tel.probe_ra_drift(10.0)
    assert r["disturbed"] is False
    assert r["disturbed_by"] == []
    assert "disturbed" not in probe_line("t", r, None)


@pytest.mark.parametrize("what", ["sync", "halt", "goto", "set_tracking",
                                  "set_tracking_rate", "unpark"])
async def test_a_command_that_changes_the_model_inside_the_window_is_named(
        vclock, fixed_env, what):
    """After a discard the engine's recovery plate-solves and syncs, and a
    sync inside the window jumps the reported RA. A halt, a goto that starts
    and ends inside the window, a tracking change and an unpark are the same:
    the counter moves, the result is ``disturbed`` and names the command, and
    the log line says so.

    MUTANT per case (the matching ``_note_model_change(...)`` call removed
    from ``sync``, ``_note_halt``, ``slew``, ``set_tracking``,
    ``set_tracking_rate`` or ``unpark``): RED for that case only."""
    if what == "sync":
        fl = _Am5Model(_connect_script(GAT="1", Gm="E", Gps="0", Td="1",
                                       Te="1"), pos=(9.5, 20.0))
        fl.clock = vclock
        fl.arm = lambda: None
    elif what == "unpark":
        fl = ClockedLink(vclock, 10 * 3600.0, 0.0)
        fl.script["Gps"] = "2"
        fl.script["Spu"] = "1"
    else:
        fl = ClockedLink(vclock, 10 * 3600.0, 0.0)
        fl.script.update(_target_cmds(11.25, 40.5))
        fl.script["MS"] = "0"
        fl.script["Td"] = "1"
        fl.script["Te"] = "1"
    tel = await _tel(fl)
    tasks = []

    async def act():
        if what == "sync":
            await tel.sync(10.5, 22.0)
        elif what == "halt":
            await tel.stop()
        elif what == "goto":
            async def go():
                try:
                    await tel.slew(11.25, 40.5)
                except Exception:       # noqa: BLE001 - it never arrives here
                    pass
            tasks.append(asyncio.create_task(go()))
        elif what == "set_tracking":
            await tel.set_tracking(False)
        elif what == "set_tracking_rate":
            await tel.set_tracking_rate("lunar")
        elif what == "unpark":
            await tel.unpark()

    vclock.hooks[12] = act
    r = await tel.probe_ra_drift(10.0)
    for t in tasks:
        t.cancel()
        await asyncio.gather(t, return_exceptions=True)
    assert r["disturbed"] is True, r
    assert what in r["disturbed_by"], r["disturbed_by"]
    assert what in probe_line("t", r, None)
    assert "not valid" in probe_line("t", r, None)


async def test_a_command_before_or_after_the_window_does_not_disturb_it(
        vclock, fixed_env):
    """The counter is read before the first probe read and after the last, so
    a halt sent before the probe started and one sent after it ended are not
    its business.

    MUTANT F1-b (``mark = self._model_changes`` set to 0): RED, the earlier
    halt is reported."""
    fl, tel = await _idle_tel(vclock, fixed_env)
    await tel.stop()
    r = await tel.probe_ra_drift(10.0)
    await tel.stop()
    assert r["disturbed"] is False and r["disturbed_by"] == []


async def test_a_slew_seen_at_the_start_is_named_slewing(vclock, fixed_env):
    """A goto already running when the window opens bumps no counter inside
    it; the flag at the start still marks the result.

    MUTANT F1-c (the ``slewing`` term dropped from ``by``): RED."""
    fl, tel = await _idle_tel(vclock, fixed_env)
    tel._slewing = True
    r = await tel.probe_ra_drift(10.0)
    assert r["disturbed"] is True and r["disturbed_by"] == ["slewing"]


async def test_pulses_are_not_counted_as_model_changes(vclock, fixed_env):
    """Pulses cannot land inside the window (the lock), and a pulse is not a
    model change: ``pulse_guide`` leaves the counter alone, so the guider's
    hundred pulses a night do not mark anything.

    MUTANT F1-d (``pulse_guide`` calling ``_note_model_change``): RED."""
    fl, tel = await _idle_tel(vclock, fixed_env)
    mark = tel._model_changes
    await tel.pulse_guide("west", 20)
    await tel.pulse_guide("north", 20)
    assert tel._model_changes == mark


async def test_the_name_list_is_bounded_but_the_counter_is_not(vclock,
                                                               fixed_env):
    """A thousand halts remember only the last few names; the counter still
    says the window was disturbed.

    MUTANT F1-e (``maxlen`` removed from ``_model_log``): RED."""
    fl, tel = await _idle_tel(vclock, fixed_env)
    for _ in range(1000):
        tel._note_model_change("halt")
    assert len(tel._model_log) <= 64
    assert tel._model_changes_since(0) == ["halt"]
    assert tel._model_changes >= 1000


# ------------------------------------------------------------------ F2

#: A start phase 0.05 s into a count: the whole-second staircase then reads
#: 5 counts in a 20 s window at 0.29 sidereal, 0.2493 by endpoints, 0.04 low.
PHASE_S = 0.05
WINDOW_S = 20.0


async def test_a_slow_drive_is_read_by_the_slope_not_the_endpoints(
        vclock, fixed_env):
    """RA advancing at 0.29 sidereal, printed in whole seconds: the fitted
    fraction is within 0.02 of 0.29 while the two-read value is off by more.

    MUTANT F2-a (the fit replaced by the endpoint value: ``fitted = endpoint``):
    RED, the fraction is 0.249. MUTANT F2-b (samples taken only at the ends,
    ``PROBE_SAMPLE_S`` = 1000): RED, two samples."""
    fl = ClockedLink(vclock, 10 * 3600.0 + PHASE_S, 0.29 * SIDEREAL)
    tel = await _tel(fl)
    r = await tel.probe_ra_drift(WINDOW_S)
    assert r["samples"] == 81
    assert abs(r["sidereal_fraction"] - 0.29) < 0.02, r
    endpoint_fraction = r["ra_drift_endpoint_arcsec_s"] / am5.SIDEREAL_ARCSEC_S
    assert abs(endpoint_fraction - 0.29) > 0.02, r
    assert abs(r["ra_drift_arcsec_s"] / am5.SIDEREAL_ARCSEC_S
               - r["sidereal_fraction"]) < 1e-3


async def test_a_static_ra_reads_exactly_zero(vclock, fixed_env):
    """No negative zero, no residue.

    MUTANT F2-d (the endpoint value computed as ``(ra_y[-1] + 1.0) / elapsed``,
    a residue): RED, 0.1 is not 0.0."""
    fl = ClockedLink(vclock, 10 * 3600.0 + 0.4, 0.0)
    tel = await _tel(fl)
    r = await tel.probe_ra_drift(10.0)
    assert r["ra_drift_arcsec_s"] == 0.0 and r["sidereal_fraction"] == 0.0
    assert math.copysign(1.0, r["ra_drift_arcsec_s"]) == 1.0
    assert r["ra_drift_endpoint_arcsec_s"] == 0.0
    assert r["samples"] == 41


async def test_the_wrap_is_unwrapped_against_the_first_sample(vclock,
                                                              fixed_env):
    """A stopped mount crossing 24:00:00 in the middle of the window: the
    samples after the wrap are unwrapped the short way round, so the fitted
    rate is sidereal, not minus 24 hours.

    MUTANT F2-e (the ``(... + 12.0) % 24.0 - 12.0`` removed from the sample
    list): RED, a fraction of about -4000."""
    fl = ClockedLink(vclock, 86400.0 - 5.0, SIDEREAL)
    tel = await _tel(fl)
    r = await tel.probe_ra_drift(10.0)
    assert 0.95 < r["sidereal_fraction"] < 1.05, r
    assert 0.95 < r["ra_drift_endpoint_arcsec_s"] / am5.SIDEREAL_ARCSEC_S < 1.05


async def test_the_wrap_the_other_way_round_is_signed(vclock, fixed_env):
    """RA running BACKWARDS across 00:00:00 reads negative.

    MUTANT F2-f (the unwrap's ``+ 12.0`` -> ``+ 0.0``): RED."""
    fl = ClockedLink(vclock, 5.0, -SIDEREAL)
    tel = await _tel(fl)
    r = await tel.probe_ra_drift(10.0)
    assert -1.05 < r["sidereal_fraction"] < -0.95, r


async def test_whole_second_replies_have_the_15_arcsec_quantum(vclock,
                                                               fixed_env):
    """``HH:MM:SS``: one count is a second of time, 15 arcsec, and the
    resolution is that over the measured interval.

    MUTANT F2-g (the quantum fixed at 1.5 whatever the reply): RED."""
    fl = ClockedLink(vclock, 10 * 3600.0, 0.0, ra_decimals=0)
    tel = await _tel(fl)
    r = await tel.probe_ra_drift(10.0)
    assert r["resolution_arcsec_s"] == pytest.approx(15.0 / r["seconds"],
                                                     abs=0.01)


async def test_fractional_second_replies_give_the_finer_quantum(vclock,
                                                                fixed_env):
    """``HH:MM:SS.s`` parses (``lx200.parse_ra`` takes a float second), and
    its quantum is 1.5 arcsec, read from the reply and not assumed. The slope
    at 0.29 sidereal then lands within 0.005.

    MUTANT F2-h (``_reply_decimals`` always 0): RED, 15 over the interval."""
    fl = ClockedLink(vclock, 10 * 3600.0 + PHASE_S, 0.29 * SIDEREAL,
                     ra_decimals=1)
    tel = await _tel(fl)
    r = await tel.probe_ra_drift(10.0)
    assert r["resolution_arcsec_s"] == pytest.approx(1.5 / r["seconds"],
                                                     abs=0.01)
    assert abs(r["sidereal_fraction"] - 0.29) < 0.005, r


def test_the_quantum_is_read_from_the_replys_decimals():
    """The helpers, directly, including a reply with its terminator.

    MUTANT F2-i (``_reply_decimals`` counting the digits before the point):
    RED."""
    assert am5._ra_quantum_arcsec("12:34:56#") == 15.0
    assert am5._ra_quantum_arcsec("12:34:56.7#") == pytest.approx(1.5)
    assert am5._ra_quantum_arcsec("12:34:56.78") == pytest.approx(0.15)
    assert am5._dec_quantum_arcsec("+12*34:56#") == 1.0
    assert am5._dec_quantum_arcsec("-12*34:56.5#") == pytest.approx(0.1)


def test_the_fit_is_a_least_squares_slope():
    """A line is recovered exactly; a staircase is not the line but is close;
    one sample, or no spread in time, is 0.

    MUTANT F2-j (``sxx`` computed from ``ys``): RED."""
    ts = [10.0 + 0.25 * k for k in range(9)]
    assert am5._fit_slope(ts, [3.0 * (t - 10.0) for t in ts]) == pytest.approx(3.0)
    assert am5._fit_slope(ts, [7.0] * 9) == 0.0
    assert am5._fit_slope([1.0], [5.0]) == 0.0
    assert am5._fit_slope([2.0, 2.0, 2.0], [1.0, 2.0, 3.0]) == 0.0


# ------------------------------------------------------------------ F3


async def test_a_dec_that_walks_gives_the_walk_rate(vclock, fixed_env):
    """The 2026-08-21 walk was in Dec, +12.9 arcmin/min = 12.9 arcsec/s. A
    model whose Dec walks at 12.9 arcsec/s reads 12.9 within the 1 arcsec
    staircase's error, positive and negative.

    MUTANT F3-a (Dec read but ``dec_y`` built from the RA samples): RED.
    MUTANT F3-b (the sign of ``dec_y`` flipped): RED for the negative case."""
    for rate in (12.9, -10.3):
        fl = ClockedLink(vclock, 10 * 3600.0, 0.0, dec_start=20.0,
                         dec_rate=rate)
        tel = await _tel(fl)
        r = await tel.probe_ra_drift(10.0)
        assert r["dec_drift_arcsec_s"] == pytest.approx(rate, abs=0.4), (rate, r)
        assert r["ra_drift_arcsec_s"] == 0.0


async def test_a_static_dec_reads_exactly_zero(vclock, fixed_env):
    """A model that is not being moved holds its Dec exactly.

    MUTANT F3-c (``dec_drift_arcsec_s`` taken from the RA ``fitted`` slope):
    RED, 14.9 is not 0.0 (the RA here runs at the sidereal rate)."""
    fl = ClockedLink(vclock, 10 * 3600.0, SIDEREAL, dec_start=-33.3)
    tel = await _tel(fl)
    r = await tel.probe_ra_drift(10.0)
    assert r["dec_drift_arcsec_s"] == 0.0
    assert r["ra_drift_arcsec_s"] != 0.0


async def test_the_dec_quantum_is_one_arcsec_over_the_interval(vclock,
                                                               fixed_env):
    """``sDD*MM:SS`` is 1 arcsec.

    MUTANT F3-e (``_dec_quantum_arcsec`` returning 15.0): RED."""
    fl = ClockedLink(vclock, 10 * 3600.0, 0.0)
    tel = await _tel(fl)
    r = await tel.probe_ra_drift(10.0)
    assert r["dec_resolution_arcsec_s"] == pytest.approx(1.0 / r["seconds"],
                                                         abs=0.01)


async def test_an_unparseable_dec_is_a_fixed_words_error_without_the_reply(
        vclock, fixed_env):
    """A garbled ``:GD#`` raises with the size and not the bytes (#863).

    MUTANT F3-f (the reply interpolated into the message): RED."""
    fl = ClockedLink(vclock, 10 * 3600.0, 0.0)
    tel = await _tel(fl)
    fl.script["GD"] = "+99xx58"
    with pytest.raises(am5.DeviceError) as ei:
        await tel.probe_ra_drift(10.0)
    assert "+99xx58" not in str(ei.value) and "bytes" in str(ei.value)


async def test_the_wire_is_reads_only_in_the_window(vclock, fixed_env):
    """Flag, then alternating ``GR``/``GD`` pairs, then the flag and the pier
    side: nothing that moves anything.

    MUTANT F3-g (a ``:Q#`` between samples): RED."""
    fl = ClockedLink(vclock, 10 * 3600.0, 0.0)
    tel = await _tel(fl)
    await tel.probe_ra_drift(10.0)
    sent = fl.sent
    assert sent[0] == "GAT" and sent[-2:] == ["GAT", "Gm"]
    middle = sent[1:-2]
    assert middle == ["GR", "GD"] * 41
