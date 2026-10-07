# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""The status poll records the device fingerprint BEFORE its guide-camera probe
waits, and says an unplug in every field of the frame that noticed it (wave 15
integration; WP-103, #16 job 2c).

WHAT WENT WRONG. WP-103 put the probe's bounded wait (``await asyncio.wait({probe},
timeout=5.0)``) in the middle of ``Hub.poll_status``: after the focuser and the
mount had been read into the frame, and before ``fingerprint.record`` wrote them
down at the end of the method. That wait is an await point (a worker-thread hop,
and up to five seconds when the camera stalls), so the sample the poll recorded
could be five seconds older than the moment it was recorded.

Why that matters: the recovery ladder's ``fingerprint.vouch`` adopts a position a
sweep has just MEASURED, and ``record`` overwrites the trusted reading with
whatever the device last said (the module's own docstring: "with ``_confirmed``
true it assigns the device's LIVE reading to ``_known``"). A poll that read the
focuser BEFORE the sweep and recorded AFTER the vouch therefore put the
forgotten position back over the measurement, the next tick distrusted the
focuser, and the ladder swept AGAIN: exactly the repeat the vouch exists to
prevent. ``test_s7_recovery_vouch_on_success`` caught it on the merged tree.

THE FIX. The record is made right after the reads, ahead of the probe's wait, so
the wait cannot age the sample (the window between sample and record is what it
was before WP-103). The probe stays where WP-103 put it: before the guide-camera
descriptor, so the frame that notices an unplug says so. Its ``connected`` map
was built at the top of the method, before the probe could have changed
anything, so the guide camera's entry is refreshed after the wait: the frame says
the unplug in every field, not only in the descriptor.

NOT FIXED HERE: ``record`` still lets ANY poll that sampled before a vouch and
writes after it overwrite the vouch (the window is now the reads and one thread
hop, as it was before WP-103). Time-stamping samples against the vouch inside
``devices/fingerprint.py`` closes it for good; reported, not built.

Named mutants, each run from a byte backup of ``hub.py`` and restored
byte-identically (sha256 compared, the mutant text grepped absent); the case that
failed and its first assertion, verbatim:

* "the record after the probe" (the fingerprint block moved back to the end of
  ``poll_status``, WP-103's order) ->
  ``test_a_stalled_guide_probe_does_not_age_the_sample_the_fingerprint_records``,
  ``AssertionError: the poll that waited on the guide camera wrote the focuser
  position it read BEFORE the vouch over the measurement: the fingerprint trusts
  11022: False``, and ``test_s7_recovery_vouch_on_success`` fails with it
  (``sweeps began at [0, 11022]``).
* "connected is not refreshed" (the ``_listed["guide_camera"] = gcam.describe()``
  block removed) ->
  ``test_the_frame_that_notices_an_unplug_says_so_in_every_field``,
  ``AssertionError: the frame that noticed the unplug still lists the guide
  camera as a connected device: True``.
"""
from __future__ import annotations

import asyncio
import threading
import time

import pytest

from _simhub import sim_hub  # noqa: F401  (fixture import)
from astrodeck.devices import fingerprint as fp_mod

from test_w15_the_status_poll_probes_the_guide_camera import (
    _Adapter, _with_guide_camera)

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


async def test_a_stalled_guide_probe_does_not_age_the_sample_the_fingerprint_records(
        fingerprint_file, sim_hub):
    """A poll is waiting on a stalled guide camera. While it waits, a sweep
    finds focus and the ladder vouches for where it left the drawtube. When the
    camera answers, the poll finishes: it must not write a reading it took
    before the sweep over the vouch, or the vouch is gone and the next tick
    sweeps again."""
    release = threading.Event()
    adapter = _Adapter(block=release)
    try:
        await _with_guide_camera(sim_hub, adapter)
        focuser = sim_hub.devices["focuser"]
        focuser.rig.focuser_pos = FORGOT_AT
        assert fp_mod.verdict(focuser_position=FORGOT_AT).focus_trusted is False, (
            "premise: a focuser that reads a position the record does not hold "
            "is not trusted")

        poll = asyncio.ensure_future(sim_hub.poll_status())
        # The probe is out and blocked inside the camera's SDK call. Polled
        # against a wall-clock deadline (#669): the worker thread enters the
        # call on its own schedule.
        deadline = time.monotonic() + 10.0
        while adapter.temperature_reads < 1 and time.monotonic() < deadline:
            await asyncio.sleep(0.01)
        assert adapter.temperature_reads == 1, "premise: the poll's probe is out"
        assert not poll.done(), "premise: the poll is waiting on the stalled probe"

        # The sweep finds focus and the ladder vouches, while the poll waits.
        focuser.rig.focuser_pos = SWEEP_POSITION
        fp_mod.vouch(focuser_position=SWEEP_POSITION)
        assert fp_mod.verdict(focuser_position=SWEEP_POSITION).focus_trusted is True, (
            "premise: the vouch made the measured position trusted")

        release.set()
        await asyncio.wait_for(poll, 10.0)

        trusted = fp_mod.verdict(focuser_position=SWEEP_POSITION).focus_trusted
        assert trusted is True, (
            "the poll that waited on the guide camera wrote the focuser "
            "position it read BEFORE the vouch over the measurement: the "
            f"fingerprint trusts {SWEEP_POSITION}: {trusted}")
    finally:
        release.set()


async def test_the_frame_that_notices_an_unplug_says_so_in_every_field(sim_hub):
    """The probe notices an idle unplug, and the frame it noticed it in says so
    in the connected map as well as in the guide-camera descriptor, where it used
    to name the camera connected a field above a descriptor that said it was
    not."""
    adapter = _Adapter(gone=True)
    cam = await _with_guide_camera(sim_hub, adapter)

    status = await sim_hub.poll_status()

    assert cam.connected is False, "precondition: the probe noticed the unplug"
    assert status["guide_camera"]["connected"] is False
    listed = status["connected"]["guide_camera"]["connected"]
    assert listed is False, (
        "the frame that noticed the unplug still lists the guide camera as a "
        f"connected device: {listed!r}")


async def test_a_healthy_guide_camera_stays_listed_connected(sim_hub):
    """CONTROL: the refresh leaves a camera that answers exactly as it was."""
    cam = await _with_guide_camera(sim_hub, _Adapter())

    status = await sim_hub.poll_status()

    assert cam.connected is True
    assert status["connected"]["guide_camera"]["connected"] is True
    assert status["guide_camera"]["connected"] is True
