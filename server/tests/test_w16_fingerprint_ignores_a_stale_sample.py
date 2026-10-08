# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""The fingerprint ignores a reading taken before the last vouch (WP-143, #760).

WHAT WENT WRONG. ``fingerprint.vouch`` adopts a position an autofocus has just
MEASURED as the trusted reference; ``record`` then assigns the device's LIVE
reading to that reference on every status poll once the focuser is confirmed.
Nothing said WHEN a reading was taken. A status poll that read the focuser
before the recovery ladder's sweep and called ``record`` after the ladder's
``vouch`` therefore put the position the focuser had FORGOTTEN back over the
measurement, the next tick distrusted the focuser, and the ladder swept again.
Wave 15 shrank the window by recording before the guide-camera probe's wait
(``test_w15_status_record_precedes_the_probe_wait``) and said in so many words
that the race itself remained. The window is every await between the poll's
focuser read and its ``record``: the filter wheel, the dome, the rotator, the
imaging camera's temperature and cooler reads, and a worker-thread hop.

THE FIX. ``poll_status`` takes a sample stamp BEFORE it reads the focuser
(``fingerprint.new_sample_stamp``), ``vouch`` takes its own, and ``record``
ignores a reading whose stamp is older than the last vouch's. Ignoring is free:
the next poll, two seconds later, samples again and is newer.

THE STAMP IS A COUNTER, NOT A CLOCK. ``time.monotonic`` on Windows reads
``GetTickCount64`` and ticks every 15.625 ms, so a poll and a vouch that land in
one tick would tie, and neither ``<`` nor ``<=`` is right for a tie (the first
accepts a stale sample, the second throws away a fresh one). A counter is
strictly ordered: the stamp taken first is smaller, always.

Named mutants, each run from a byte backup inside this worktree and restored
byte-identically (sha256 compared, the mutant text grepped absent). The first
failing assertion is quoted verbatim, with the case that raised it.

* "the stale sample is accepted" (``_stale`` made ``return False``) ->
  ``test_a_sample_taken_before_a_vouch_does_not_overwrite_it``,
  ``AssertionError: a reading taken before the vouch was recorded over it: the
  fingerprint no longer trusts the measured position``; and
  ``test_a_status_poll_that_straddles_a_vouch_does_not_overwrite_it`` fails with
  ``AssertionError: the poll that read the focuser before the vouch recorded
  that reading over the measurement: the fingerprint no longer trusts the
  measured position``, and the stale-gap case fails too.
* "the vouch is not stamped" (``vouch``'s ``_last_vouch_stamp = next(_stamps)``
  made ``pass``) -> the same three cases, the same text.
* "the poll does not pass its stamp" (``hub.poll_status`` records with
  ``sample_stamp=None``, which ``record`` reads as a reading taken as it is
  observed, after the vouch) ->
  ``test_a_status_poll_that_straddles_a_vouch_does_not_overwrite_it``, the
  poll text above (the unit cases pass: they hand ``record`` its stamp).
* "the stamp is a clock" (``new_sample_stamp`` returns
  ``int(time.monotonic() * 1000)``) ->
  ``test_stamps_never_tie``, ``AssertionError: two stamps taken one after the
  other were not strictly increasing``; the straddling cases fail too, because
  a vouch and a stamp inside one millisecond tie and the tie is read as fresh.
* "a stale gap still counts" (``record``'s ``if not _stale(sample_stamp):`` made
  ``if not _stale(sample_stamp) or focuser_position is None:``) ->
  ``test_a_stale_disconnected_reading_does_not_open_a_gap``,
  ``AssertionError: a move seen after the measurement was not recorded: a
  reading from before the vouch had opened a gap``.

An UNSTAMPED ``record`` (every caller before #760) is taken as of the moment it
is observed, under ``_state_lock``, and is never stale. That keeps
``test_persist.py::test_a_measured_vouch_is_not_overwritten_by_a_worker_mid_
observe`` meaningful: a stamp taken at the call would have made that case pass
with the lock removed. Checked: that case still fails under its own mutation B
(the ``with _state_lock:`` around the observation in ``record`` made ``if
True:``), ``AssertionError: a worker's stale device reading overwrote a
measured position``.
"""
from __future__ import annotations

import asyncio

import pytest

from _simhub import sim_hub  # noqa: F401  (fixture import)
from astrodeck.devices import fingerprint as fp_mod

#: Where the focuser stood when the record was written, before the "restart".
RECORDED_AT = 9935
#: What it reads after a power cut: an EAF that forgot its count reads 0.
FORGOT_AT = 0
#: Where a sweep that found focus leaves it.
SWEEP_POSITION = 11022


@pytest.fixture
def fingerprint_file(tmp_path, monkeypatch):
    """The REAL fingerprint on a file under ``tmp_path``, holding a record from
    before a restart that the focuser no longer matches. Requested BEFORE
    ``sim_hub`` so the hub's own polls never write the box's real file."""
    monkeypatch.setattr(fp_mod, "_PATH", tmp_path / "fingerprint.json")
    fp_mod.reset_for_tests()
    fp_mod.record(focuser_position=RECORDED_AT, filter_slot=0, ra_hours=1.0,
                  dec_deg=2.0, parked=False, tracking=True)
    fp_mod.reset_for_tests()                 # the restart
    yield
    fp_mod.reset_for_tests()


def _record(position, **kw):
    fp_mod.record(focuser_position=position, filter_slot=0, ra_hours=1.0,
                  dec_deg=2.0, parked=False, tracking=True, **kw)


def test_a_sample_taken_before_a_vouch_does_not_overwrite_it(fingerprint_file):
    """The focuser is read (it says 0, the forgotten count), a sweep then finds
    focus and the ladder vouches for where it left the drawtube, and only then
    does the reading reach ``record``. The measurement stands."""
    stamp = fp_mod.new_sample_stamp()              # the poll samples ...
    fp_mod.vouch(focuser_position=SWEEP_POSITION)  # ... a sweep is vouched ...
    _record(FORGOT_AT, sample_stamp=stamp)         # ... and the sample lands

    trusted = fp_mod.verdict(focuser_position=SWEEP_POSITION).focus_trusted
    assert trusted is True, (
        "a reading taken before the vouch was recorded over it: the "
        "fingerprint no longer trusts the measured position")


def test_a_sample_taken_after_a_vouch_is_a_real_move(fingerprint_file):
    """CONTROL. A reading stamped AFTER the vouch is the device moving since the
    measurement, and the fingerprint follows it as it always has."""
    fp_mod.vouch(focuser_position=SWEEP_POSITION)
    stamp = fp_mod.new_sample_stamp()
    _record(SWEEP_POSITION + 40, sample_stamp=stamp)

    assert fp_mod.verdict(
        focuser_position=SWEEP_POSITION + 40).focus_trusted is True, (
        "a move seen after the measurement was not recorded")
    assert fp_mod.verdict(
        focuser_position=SWEEP_POSITION).focus_trusted is False


def test_a_sample_with_no_stamp_is_taken_as_of_the_call(fingerprint_file):
    """CONTROL, and the compatibility rule: a caller that passes no stamp (every
    caller before this) is sampled when ``record`` is called, so a record made
    after a vouch is newer than it, exactly as before."""
    fp_mod.vouch(focuser_position=SWEEP_POSITION)
    _record(SWEEP_POSITION + 7)

    assert fp_mod.verdict(
        focuser_position=SWEEP_POSITION + 7).focus_trusted is True, (
        "an unstamped record after a vouch was ignored")


def test_a_stale_disconnected_reading_does_not_open_a_gap(fingerprint_file):
    """A poll sampled while the focuser was off the bus (it reads None), the
    ladder vouched for a sweep, and the stale None lands. Not seeing a device is
    a gap, and a gap that predates a measurement must not open after it: the
    next poll's real move has to be followed, not held to the vouched number."""
    stamp = fp_mod.new_sample_stamp()
    fp_mod.vouch(focuser_position=SWEEP_POSITION)
    _record(None, sample_stamp=stamp)
    _record(SWEEP_POSITION + 12, sample_stamp=fp_mod.new_sample_stamp())

    assert fp_mod.verdict(
        focuser_position=SWEEP_POSITION + 12).focus_trusted is True, (
        "a move seen after the measurement was not recorded: a reading from "
        "before the vouch had opened a gap")


def test_stamps_never_tie():
    """A thousand stamps in a row are strictly increasing, which no reading of
    ``time.monotonic`` can promise on a 15.625 ms Windows clock."""
    stamps = [fp_mod.new_sample_stamp() for _ in range(1000)]
    assert all(b > a for a, b in zip(stamps, stamps[1:])), (
        "two stamps taken one after the other were not strictly increasing")


async def test_a_status_poll_that_straddles_a_vouch_does_not_overwrite_it(
        fingerprint_file, sim_hub):
    """The whole path, with the interleaving FORCED rather than hoped for. The
    poll has read the focuser (0, forgotten) and is waiting on the filter wheel
    read that follows it; while it waits, a sweep finds focus and the ladder
    vouches. When the wheel answers, the poll finishes and records what it read
    before the vouch. It must not win."""
    focuser = sim_hub.devices["focuser"]
    focuser.rig.focuser_pos = FORGOT_AT
    assert fp_mod.verdict(focuser_position=FORGOT_AT).focus_trusted is False, (
        "premise: a focuser that reads a position the record does not hold is "
        "not trusted")

    fw = sim_hub.devices["filterwheel"]
    real_get_position = fw.get_position
    reached = asyncio.Event()
    release = asyncio.Event()

    async def get_position():
        reached.set()
        await release.wait()
        return await real_get_position()

    fw.get_position = get_position
    poll = asyncio.ensure_future(sim_hub.poll_status())
    try:
        await asyncio.wait_for(reached.wait(), 10.0)
        assert not poll.done(), "premise: the poll is parked at the wheel read"

        # The sweep finds focus and the ladder vouches, while the poll waits.
        focuser.rig.focuser_pos = SWEEP_POSITION
        fp_mod.vouch(focuser_position=SWEEP_POSITION)
        assert fp_mod.verdict(
            focuser_position=SWEEP_POSITION).focus_trusted is True, (
            "premise: the vouch made the measured position trusted")

        release.set()
        await asyncio.wait_for(poll, 10.0)
    finally:
        release.set()
        if not poll.done():
            poll.cancel()

    trusted = fp_mod.verdict(focuser_position=SWEEP_POSITION).focus_trusted
    assert trusted is True, (
        "the poll that read the focuser before the vouch recorded that reading "
        "over the measurement: the fingerprint no longer trusts the measured "
        "position")


async def test_the_next_poll_after_a_vouch_is_followed(fingerprint_file,
                                                       sim_hub):
    """CONTROL. A poll that STARTS after the vouch is newer than it, and a real
    move it sees is recorded: ignoring stale samples must not freeze the
    fingerprint on the vouched number for good."""
    focuser = sim_hub.devices["focuser"]
    focuser.rig.focuser_pos = SWEEP_POSITION
    fp_mod.vouch(focuser_position=SWEEP_POSITION)

    await sim_hub.poll_status()                     # reads SWEEP_POSITION
    focuser.rig.focuser_pos = SWEEP_POSITION + 25   # a real move
    await sim_hub.poll_status()

    assert fp_mod.verdict(
        focuser_position=SWEEP_POSITION + 25).focus_trusted is True, (
        "a move seen by a poll that began after the vouch was not recorded")
