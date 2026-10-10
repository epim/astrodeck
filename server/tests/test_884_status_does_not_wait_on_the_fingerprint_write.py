# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""``GET /api/status`` does not wait on the device-fingerprint disk write (#884).

``poll_status`` ends by recording the device fingerprint, and that record
holds a lock across a disk write. Moving the record to a worker thread (#97)
freed the event loop but not the request: during a slow write every concurrent
status call parked a default-executor worker on the lock until the write ended,
so status was slow exactly when the disk was slow, and the parked workers
starved every other ``to_thread`` route.

The write now belongs to one background writer thread. These tests stick that
write on an event and grade what the CALLERS see while it is stuck, never how
the lock is arranged.

MUTANT (the one the suite is graded against): in ``record_nowait``, replace
``_submit(staged)`` with ``_write(*staged)``, i.e. the status path writes the
file itself again. Observed under that mutant: the first status call enters the
stuck write on its own thread and the rest of the callers are still waiting
when the deadline passes, so the first test fails with "status calls waited
on the fingerprint write".
"""
from __future__ import annotations

import json
import threading
import time

import pytest
from fastapi.testclient import TestClient

import astrodeck.api.app as app_module
from astrodeck.config import ConfigStore
from astrodeck.devices import fingerprint

CALLERS = 8

#: How long a caller may take to answer while the write is stuck. The stuck
#: write is only released AFTER this wait, so a caller that needed the write to
#: finish cannot answer inside it however fast the machine is; the bound is
#: generous for a loaded runner and costs nothing when the code is right.
ANSWER_WITHIN_S = 10.0

SIX_KEYS = {"focuser_position", "filter_slot", "ra_hours", "dec_deg",
            "parked", "tracking"}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv(app_module.NO_AUTOCONNECT_ENV_VAR, "1")
    temp_store = ConfigStore(path=tmp_path / "astrodeck.json")
    import astrodeck.config as config_mod
    import astrodeck.hub as hub_mod
    from astrodeck.profiles import profiles as profile_lib
    monkeypatch.setattr(config_mod, "config_store", temp_store)
    monkeypatch.setattr(hub_mod, "config_store", temp_store)
    monkeypatch.setattr(app_module, "config_store", temp_store)
    monkeypatch.setattr(hub_mod, "CAPTURE_DIR", tmp_path / "captures")
    monkeypatch.setattr(profile_lib, "_dir", tmp_path / "profiles")

    app = app_module.create_app()
    with TestClient(app) as c:
        yield c


@pytest.fixture
def stuck_write(tmp_path, monkeypatch):
    """The fingerprint file under ``tmp_path``, written by a writer that parks
    on ``release`` until the test lets it go. Coalescing is off, so EVERY
    status call owes the disk a write and none can hide behind the interval."""
    path = tmp_path / "state" / "device_fingerprint.json"
    monkeypatch.setattr(fingerprint, "_PATH", path)
    monkeypatch.setattr(fingerprint, "FINGERPRINT_WRITE_INTERVAL_S", 0.0)
    fingerprint.reset_for_tests()

    entered = threading.Event()
    release = threading.Event()
    real_write = fingerprint.write_json_atomic

    def parked_write(target, payload, **kwargs):
        entered.set()
        release.wait(timeout=30)
        return real_write(target, payload, **kwargs)

    monkeypatch.setattr(fingerprint, "write_json_atomic", parked_write)
    try:
        yield path, entered, release
    finally:
        release.set()
        fingerprint.reset_for_tests()


def test_concurrent_status_calls_answer_while_the_fingerprint_write_is_stuck(
        client, stuck_write):
    path, entered, release = stuck_write
    answers: list[int] = []

    def ask() -> None:
        answers.append(client.get("/api/status").status_code)

    # One status call starts the write, and the write is stuck from then on.
    first = threading.Thread(target=ask)
    first.start()
    assert entered.wait(timeout=ANSWER_WITHIN_S), (
        "premise: a status poll started a fingerprint write")
    assert not release.is_set(), "premise: the write is stuck"

    # Now a burst of status calls, all of which would reach the same write.
    burst = [threading.Thread(target=ask) for _ in range(CALLERS)]
    for caller in burst:
        caller.start()
    deadline = time.monotonic() + ANSWER_WITHIN_S
    for caller in [first, *burst]:
        caller.join(timeout=max(0.0, deadline - time.monotonic()))
    waiting = [c for c in [first, *burst] if c.is_alive()]

    assert not waiting, (
        f"{len(waiting)} of {CALLERS + 1} status calls waited on the "
        "fingerprint write: status is slow exactly when the disk is slow, and "
        "each waiting call holds a default-executor worker the other to_thread "
        "routes need")
    assert answers == [200] * (CALLERS + 1)

    # The stuck write was never skipped, only taken off the request: let it go
    # and the file lands whole, with the same six fields as before.
    release.set()
    assert fingerprint.wait_idle(timeout=ANSWER_WITHIN_S), (
        "the background writer never finished the released write")
    assert set(json.loads(path.read_text(encoding="utf-8"))) == SIX_KEYS


def test_record_nowait_returns_while_the_write_is_stuck_and_the_file_lands_after(
        stuck_write):
    path, entered, release = stuck_write

    # The first record starts the write; it returns without it.
    fingerprint.record_nowait(focuser_position=11218, filter_slot=1,
                              ra_hours=1.0, dec_deg=2.0, parked=False,
                              tracking=True)
    assert entered.wait(timeout=ANSWER_WITHIN_S), "premise: the write started"

    done: list[int] = []

    def call(n: int) -> None:
        fingerprint.record_nowait(focuser_position=11218 + n, filter_slot=n,
                                  ra_hours=1.0, dec_deg=2.0, parked=False,
                                  tracking=True)
        done.append(n)

    callers = [threading.Thread(target=call, args=(n,))
               for n in range(CALLERS)]
    for caller in callers:
        caller.start()
    for caller in callers:
        caller.join(timeout=ANSWER_WITHIN_S)
    assert sorted(done) == list(range(CALLERS)), (
        "a record call queued behind the stuck write")
    assert not path.exists(), "premise: nothing has been written yet"

    release.set()
    assert fingerprint.wait_idle(timeout=ANSWER_WITHIN_S)
    written = json.loads(path.read_text(encoding="utf-8"))
    assert set(written) == SIX_KEYS
    # The latest reading wins; the ones it replaced were never worth a write.
    assert written["focuser_position"] == fingerprint._last_pos


def test_a_device_that_drops_and_returns_while_the_write_is_stuck_is_not_trusted(
        stuck_write):
    """Taking the write off the request must not take the OBSERVATION with it.

    The writer keeps only the latest sample, so a drop-out that was folded into
    "the latest" would leave the focuser looking continuous: the EAF forgets
    its count on power loss, and trusting a number it reports after a gap is
    the misplaced trust this module exists to prevent.
    """
    path, entered, release = stuck_write
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"focuser_position": 5000}), encoding="utf-8")
    fingerprint.reset_for_tests()

    fingerprint.record_nowait(focuser_position=5000, filter_slot=0,
                              ra_hours=1.0, dec_deg=2.0, parked=False,
                              tracking=True)
    assert entered.wait(timeout=ANSWER_WITHIN_S), "premise: the write is stuck"
    assert fingerprint._confirmed is True, (
        "premise: a focuser holding the recorded count is confirmed")

    # While the write is stuck: the focuser drops off, then comes back reading
    # a different count. Each call must be seen, not just the last.
    fingerprint.record_nowait(focuser_position=None, filter_slot=0,
                              ra_hours=1.0, dec_deg=2.0, parked=False,
                              tracking=True)
    fingerprint.record_nowait(focuser_position=7777, filter_slot=0,
                              ra_hours=1.0, dec_deg=2.0, parked=False,
                              tracking=True)

    # Judged after the writer has caught up: a gap that only the writer was
    # going to see would be gone by now, and the latest sample alone reads as
    # a focuser that was watched the whole way.
    release.set()
    assert fingerprint.wait_idle(timeout=ANSWER_WITHIN_S)
    assert fingerprint.verdict(focuser_position=7777).focus_trusted is False, (
        "a focuser that dropped off the bus and came back with another count "
        "was trusted because the gap fell inside one stuck write")


def test_a_failed_write_does_not_stop_the_next_one(tmp_path, monkeypatch):
    """The writer is one thread that comes and goes. A write that raised must
    leave it startable, or the fingerprint stops being written for the life of
    the process and nothing says so."""
    path = tmp_path / "state" / "device_fingerprint.json"
    monkeypatch.setattr(fingerprint, "_PATH", path)
    monkeypatch.setattr(fingerprint, "FINGERPRINT_WRITE_INTERVAL_S", 0.0)
    fingerprint.reset_for_tests()
    real_write = fingerprint.write_json_atomic
    attempts: list[int] = []

    def fails_once(target, payload, **kwargs):
        attempts.append(1)
        if len(attempts) == 1:
            raise OSError("disk went away")
        return real_write(target, payload, **kwargs)

    monkeypatch.setattr(fingerprint, "write_json_atomic", fails_once)
    try:
        fingerprint.record_nowait(focuser_position=11218, filter_slot=1,
                                  ra_hours=1.0, dec_deg=2.0, parked=False,
                                  tracking=True)
        assert fingerprint.wait_idle(timeout=ANSWER_WITHIN_S)
        assert not path.exists(), "premise: the first write failed"

        fingerprint.record_nowait(focuser_position=11218, filter_slot=1,
                                  ra_hours=1.0, dec_deg=2.0, parked=False,
                                  tracking=True)
        assert fingerprint.wait_idle(timeout=ANSWER_WITHIN_S)
        assert set(json.loads(path.read_text(encoding="utf-8"))) == SIX_KEYS
    finally:
        fingerprint.reset_for_tests()
