# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""Two device reads WP-164 left unbounded are bounded (WP-176, #934 and #935).

WP-164 bounded every device read in the status poll (#814) and every wheel
await in the plate solve's borrow and return (#815). Two reads of the same two
classes were left out.

* #934. ``Hub.from_mount_frame`` runs in the mount block of every status poll
  and in every solve hint, and on an Alpaca mount it calls
  ``Hub._mount_expects_jnow``, which awaited ``tel._get("equatorialsystem")``
  with no bound on the first poll after the mount connects and again every
  ``_JNOW_REPROBE_HOLDOFF_S`` while the probe got no definite answer. A mount
  whose property never returns (a COM driver behind the comhost's serialised
  STA thread, an Alpaca server that accepts and never answers) held the whole
  status frame, and the ``/api/status`` caller, for the transport timeout on
  each reprobe.
* #935. ``Hub._narrowband_filter_loaded`` awaited ``fw.get_position()`` with no
  bound, between the borrow and the exposure of every centring solve, rotate,
  rotator sync and guide offset, and of the engine's sky precheck. A native
  wheel whose SDK read stalls in USB never trips a transport timeout, so the
  solve waited for ever with the wheel on the solve filter.

THE FIX. The read path passes ``STATUS_DEVICE_READ_TIMEOUT_S`` to the probe,
which bounds ONLY the ``equatorialsystem`` read: a probe that runs out of time
is a probe that got no definite answer, so it takes the hold-off path an HTTP
500 already takes (JNOW, not cached, not asked again for the hold-off). The
precession after it is not under the bound, and a slew or a sync (``to_mount_
frame``) passes none, because it must get the definite answer. The wheel's
label read is bounded by ``SOLVE_WHEEL_MOVE_TIMEOUT_S`` and a read that does
not return answers None, "no name, no claim", and says so.

Named mutants, each run from a byte backup of ``hub.py`` and restored with a
byte copy (md5 compared). The first failing assertion is quoted with the case
that raised it.

* "probe unbounded" (the ``if bound is not None:`` guard in ``_mount_expects_
  jnow`` made ``if False:``, which is the unfixed read) -> ``test_a_stalled_
  frame_probe_does_not_hold_up_the_read_path[poll_status]``: Failed, "the
  status frame waited on a mount whose EquatorialSystem never returned" (and
  the ``[from_mount_frame]`` case and both hold-off cases).
* "a slew is bounded too" (the guard made ``if True:`` with ``bound or
  STATUS_DEVICE_READ_TIMEOUT_S``) -> ``test_a_slew_or_a_sync_still_waits_for_
  the_definite_answer``: AssertionError, "a slew's frame probe gave up at the
  status bound and sent a J2000 mount a precessed target".
* "the whole conversion is bounded" (``poll_status``'s ``await self.from_mount_
  frame(tel, ra, dec)`` wrapped in ``asyncio.wait_for(..., STATUS_DEVICE_READ_
  TIMEOUT_S)``, the shape the issue rules out) -> ``test_the_precession_after_
  the_probe_is_not_under_the_bound``: AssertionError, "the mount block was
  dropped because the precession outlasted the status bound" (and the
  ``[poll_status]`` and hold-off cases).
* "a timeout is not held off" (an ``except asyncio.TimeoutError: return True``
  ahead of the broad ``except Exception``) -> ``test_a_timed_out_probe_is_held_
  off_like_a_failed_one``: AssertionError, "a status poll inside the hold-off
  asked the mount again" (three asks).
* "label unbounded" (``fw.get_position()`` in ``_narrowband_filter_loaded``
  awaited without ``asyncio.wait_for``, which is the unfixed read) -> ``test_a_
  wheel_whose_position_never_returns_does_not_hang_the_label``: Failed, "the
  label read waited on a wheel whose position never returned" (and the rotate
  case).
* "timeout unsaid" (the ``except asyncio.TimeoutError`` clause made a bare
  ``return None``) -> the same case: AssertionError, no line about the position
  read (``assert (0 == 1)``).

The controls pass on the unfixed tree as well, which is what makes them
controls: the precession outlasting the bound, a slew's slow probe, and a wheel
that answers.
"""
from __future__ import annotations

import asyncio
import time

import pytest

import astrodeck.events as events_mod
import astrodeck.hub as hub_module
from _simhub import sim_hub  # noqa: F401 (fixture import)

L_SLOT, HA_SLOT = 0, 5
NARROWBAND = [False, False, False, False, True, True, True, False]

#: What the stand-in precession adds to the mount's own report. A J2000 mount
#: is read as it reported (no shift), a JNOW one through this shift.
SHIFT_RA_H, SHIFT_DEC_DEG = -0.25, -1.0


@pytest.fixture
def short_bound(monkeypatch):
    """The status bound shortened so a stall does not wait out the real one.
    Read at call time. ``raising=False``: on code without the bound a case
    fails on what it grades (a frame that never comes), not on a missing
    attribute."""
    monkeypatch.setattr(hub_module, "STATUS_DEVICE_READ_TIMEOUT_S", 0.25,
                        raising=False)


@pytest.fixture
def short_wheel_bound(monkeypatch):
    monkeypatch.setattr(hub_module, "SOLVE_WHEEL_MOVE_TIMEOUT_S", 0.2,
                        raising=False)


def _record_logs(monkeypatch) -> list[tuple[str, str, str]]:
    lines: list[tuple[str, str, str]] = []
    real = events_mod.bus.log

    def record(level, message, source="hub", **kw):
        lines.append((level, message, source))
        return real(level, message, source, **kw)
    monkeypatch.setattr(events_mod.bus, "log", record)
    return lines


# ----------------------------------------------------------------- #934


def _alpaca_mount(hub, monkeypatch, get, *, precess_sleep_s: float = 0.0):
    """The simulator's mount, standing as a JNOW-capable Alpaca mount whose
    ``EquatorialSystem`` property is answered by ``get`` (``tel._get``), at
    10 h +40 deg. The precession is a fixed shift, so a case can tell a
    converted position from a raw one without astropy; ``precess_sleep_s``
    makes it as slow as astropy can be on a Pi. Returns the mount and the
    list of positions the precession was asked about."""
    tel = hub.devices["telescope"]
    tel.rig.ra_hours, tel.rig.dec_deg = 10.0, 40.0
    monkeypatch.setattr(tel, "backend", "alpaca", raising=False)
    monkeypatch.setattr(tel, "_get", get, raising=False)
    monkeypatch.setattr(hub, "_mount_wants_jnow", None)
    monkeypatch.setattr(hub, "_mount_jnow_reprobe", None, raising=False)
    monkeypatch.setattr(hub, "_precess_memo", None)
    seen: list[tuple[float, float]] = []

    def precess(ra_hours, dec_deg):
        seen.append((ra_hours, dec_deg))
        if precess_sleep_s:
            time.sleep(precess_sleep_s)        # on the worker thread
        return ra_hours + SHIFT_RA_H, dec_deg + SHIFT_DEC_DEG

    monkeypatch.setattr(hub_module, "precess_jnow_to_j2000", precess)
    return tel, seen


class _Probe:
    """``tel._get``: the Nth ``equatorialsystem`` call (1-based) in ``hang_on``
    never returns until ``release`` is set; every other one answers ``answer``
    (1 = topocentric JNOW, 2 = J2000). Every call is recorded."""

    def __init__(self, *, hang_on=(1,), answer: int = 1):
        self.hang_on = set(hang_on)
        self.answer = answer
        self.asked: list[str] = []
        self.release = asyncio.Event()

    async def __call__(self, method, **params):
        self.asked.append(method)
        if len(self.asked) in self.hang_on:
            await self.release.wait()
        return self.answer


@pytest.mark.parametrize("path", ["poll_status", "from_mount_frame"])
async def test_a_stalled_frame_probe_does_not_hold_up_the_read_path(
        sim_hub, short_bound, monkeypatch, path):
    """The mount's EquatorialSystem never returns. The status poll (and the
    conversion every solve hint and capture snapshot makes) comes anyway,
    with the position brought back to J2000 on the JNOW default, and the
    rest of the mount block is there."""
    probe = _Probe(hang_on=(1,))
    tel, seen = _alpaca_mount(sim_hub, monkeypatch, probe)
    try:
        if path == "poll_status":
            try:
                status = await asyncio.wait_for(sim_hub.poll_status(), 2.5)
            except asyncio.TimeoutError:
                pytest.fail("the status frame waited on a mount whose "
                            "EquatorialSystem never returned")
            mount = status.get("mount")
            assert mount is not None, "the mount block was dropped"
            assert mount["tracking"] is not None, mount
            assert "parked" in mount and "slewing" in mount, mount
            got = (mount["ra_hours"], mount["dec_deg"])
        else:
            try:
                got = await asyncio.wait_for(
                    sim_hub.from_mount_frame(tel, 10.0, 40.0), 2.5)
            except asyncio.TimeoutError:
                pytest.fail("the conversion waited on a mount whose "
                            "EquatorialSystem never returned")
        assert probe.asked == ["equatorialsystem"], probe.asked
        assert got == (pytest.approx(10.0 + SHIFT_RA_H, abs=1e-3),
                       pytest.approx(40.0 + SHIFT_DEC_DEG, abs=1e-3)), (
            f"a probe with no answer is JNOW, so the report is precessed: "
            f"{got}")
    finally:
        probe.release.set()


async def test_the_precession_after_the_probe_is_not_under_the_bound(
        sim_hub, short_bound, monkeypatch):
    """The probe answers at once and the precession takes longer than the
    bound (astropy on a Pi can). The mount block is still on the frame, with
    the precessed position: a bound on the whole conversion would drop the
    block or publish the raw JNOW position as J2000."""
    probe = _Probe(hang_on=(), answer=1)
    _tel, seen = _alpaca_mount(sim_hub, monkeypatch, probe,
                               precess_sleep_s=0.6)

    status = await asyncio.wait_for(sim_hub.poll_status(), 5.0)

    mount = status.get("mount")
    assert mount is not None, (
        "the mount block was dropped because the precession outlasted the "
        "status bound")
    assert (mount["ra_hours"], mount["dec_deg"]) == (
        pytest.approx(seen[0][0] + SHIFT_RA_H),
        pytest.approx(seen[0][1] + SHIFT_DEC_DEG)), (
        "the position was published without its precession")


async def test_a_timed_out_probe_is_held_off_like_a_failed_one(
        sim_hub, short_bound, monkeypatch):
    """A probe that ran out of time is a probe with no definite answer (#861
    N6): the next poll inside the hold-off does not ask the mount again, so a
    stalled mount costs the frame one bound a minute and not one every poll."""
    probe = _Probe(hang_on=(1, 2, 3))
    _tel, _seen = _alpaca_mount(sim_hub, monkeypatch, probe)
    try:
        for _ in range(3):
            status = await asyncio.wait_for(sim_hub.poll_status(), 2.5)
            assert "mount" in status
        assert probe.asked == ["equatorialsystem"], (
            f"a status poll inside the hold-off asked the mount again: "
            f"{probe.asked}")
    finally:
        probe.release.set()


async def test_a_timed_out_probe_is_asked_again_and_the_answer_kept(
        sim_hub, short_bound, monkeypatch):
    """With the hold-off over, the read path asks again. The first probe's
    timeout was not cached as an answer (the mount is J2000, and the second
    probe says so), and the definite answer is cached, so a third poll asks
    nothing."""
    monkeypatch.setattr(hub_module, "_JNOW_REPROBE_HOLDOFF_S", 0.0)
    probe = _Probe(hang_on=(1,), answer=2)
    _tel, seen = _alpaca_mount(sim_hub, monkeypatch, probe)
    try:
        first = await asyncio.wait_for(sim_hub.poll_status(), 2.5)
        assert first["mount"]["ra_hours"] == pytest.approx(
            10.0 + SHIFT_RA_H, abs=1e-3)

        second = await asyncio.wait_for(sim_hub.poll_status(), 2.5)
        assert probe.asked == ["equatorialsystem"] * 2, probe.asked
        assert second["mount"]["ra_hours"] == pytest.approx(10.0, abs=1e-3), (
            "a J2000 mount was precessed after it said it was J2000")
        assert sim_hub._mount_wants_jnow is False

        await asyncio.wait_for(sim_hub.poll_status(), 2.5)
        assert probe.asked == ["equatorialsystem"] * 2, (
            "the definite answer was not kept")
    finally:
        probe.release.set()


async def test_a_slew_or_a_sync_still_waits_for_the_definite_answer(
        sim_hub, short_bound, monkeypatch):
    """CONTROL, the other path. ``to_mount_frame`` (every slew and sync) must
    get the answer: a probe slower than the status bound that says J2000 is
    taken as J2000, and the target goes out unprecessed. Were the slew held to
    the status bound, a J2000 mount whose first answer was slow would be sent
    a target precessed 0.38 deg away."""
    async def slow(method, **params):
        await asyncio.sleep(0.6)               # longer than the 0.25 s bound
        return 2

    _tel, _seen = _alpaca_mount(sim_hub, monkeypatch, slow)

    got = await sim_hub.to_mount_frame(sim_hub.devices["telescope"], 10.0, 40.0)

    assert got == (10.0, 40.0), (
        f"a slew's frame probe gave up at the status bound and sent a J2000 "
        f"mount a precessed target: {got}")
    assert sim_hub._mount_wants_jnow is False


# ----------------------------------------------------------------- #935


def _wheel_reads(hub, monkeypatch, *, hang_when):
    """The sim wheel on Ha with its narrowband slots marked. ``hang_when`` is
    called with the 1-based number of the ``get_position`` call and the number
    of moves made so far; a call it answers True for never returns until the
    returned event is set. Moves are instant. Returns the wheel, the event and
    the list of every call."""
    fw = hub.devices["filterwheel"]
    fw.filter_narrowband = list(NARROWBAND)
    hub.sim_rig.filter_slot = HA_SLOT
    release = asyncio.Event()
    calls: list[tuple[int, int]] = []
    moves = {"n": 0}
    real_get = fw.get_position

    async def set_position(slot):
        moves["n"] += 1
        hub.sim_rig.filter_slot = int(slot)

    async def get_position():
        calls.append((len(calls) + 1, moves["n"]))
        if hang_when(len(calls), moves["n"]):
            await release.wait()               # a USB stall inside the SDK
        return await real_get()

    monkeypatch.setattr(fw, "set_position", set_position)
    monkeypatch.setattr(fw, "get_position", get_position)
    return fw, release, calls


async def test_a_wheel_that_answers_names_its_narrowband_filter(
        sim_hub, monkeypatch, short_wheel_bound):
    """CONTROL. The premise the cases below depend on: an answering wheel on
    Ha is named, so None is the answer to the stall and not to the wheel."""
    fw, _release, _calls = _wheel_reads(sim_hub, monkeypatch,
                                        hang_when=lambda n, m: False)

    assert await sim_hub._narrowband_filter_loaded() == fw.filter_names[HA_SLOT]


async def test_a_wheel_whose_position_never_returns_does_not_hang_the_label(
        sim_hub, monkeypatch, short_wheel_bound):
    """The wheel's position read never returns. The label read gives up at the
    bound and answers None (no name, no claim), as a read that raised does, and
    says why: a bare timeout has no text of its own."""
    logs = _record_logs(monkeypatch)
    _fw, release, calls = _wheel_reads(sim_hub, monkeypatch,
                                       hang_when=lambda n, m: True)
    try:
        try:
            got = await asyncio.wait_for(sim_hub._narrowband_filter_loaded(),
                                         3.0)
        except asyncio.TimeoutError:
            pytest.fail("the label read waited on a wheel whose position "
                        "never returned")
        assert got is None, got
        assert len(calls) == 1, calls
        said = [ln for ln in logs if "position read" in ln[1]]
        assert len(said) == 1 and said[0][0] == "warning", logs
        assert "no answer within 0.2 s" in said[0][1], said[0][1]
        assert "()" not in said[0][1], said[0][1]
    finally:
        release.set()


async def test_a_stalled_label_read_does_not_hold_up_a_rotate(
        sim_hub, monkeypatch, short_wheel_bound):
    """Through a real caller. ``rotate_to_pa`` solves twice, each frame between
    a borrow and a return. The first read of the wheel after the borrow's move
    (the label read) never returns; the rotate goes on, solves, and the wheel
    ends where the run left it."""
    sim_hub.sim_rig.rotator_pa_offset_deg = 20.0
    sim_hub.sim_rig.rotator_mech_deg = 10.0
    seen_stall: list[int] = []

    def hang_once(_n, moves):
        if moves >= 1 and not seen_stall:
            seen_stall.append(moves)
            return True
        return False

    _fw, release, _calls = _wheel_reads(sim_hub, monkeypatch,
                                        hang_when=hang_once)
    try:
        try:
            result = await asyncio.wait_for(sim_hub.rotate_to_pa(40.0), 15.0)
        except asyncio.TimeoutError:
            pytest.fail("the rotate waited on a wheel whose position read "
                        "never returned")
        assert seen_stall, "premise: the label read was reached and stalled"
        assert result["rotated"] is True, result
        assert sim_hub.sim_rig.filter_slot == HA_SLOT, (
            f"the wheel was left on slot {sim_hub.sim_rig.filter_slot}")
    finally:
        release.set()
