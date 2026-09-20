"""Persistence durability: atomic write keeps a recoverable ``.bak`` *copy*, the
replace-retry count is exact (and raises the last error), and ``ConfigStore``
restores from ``.bak`` and fails closed rather than replacing corrupt security
state with defaults."""
import json
import os
import stat
import time

import pytest

import astrodeck.persist as persist
from astrodeck.config import AppConfig, ConfigStore, Site
from astrodeck.persist import (
    _REPLACE_RETRIES,
    read_json,
    write_json_atomic,
)


# ----------------------------------------------------------------- atomic write

def test_write_round_trips(tmp_path):
    p = tmp_path / "x.json"
    write_json_atomic(p, {"a": 1, "b": [2, 3]})
    assert read_json(p) == {"a": 1, "b": [2, 3]}


def test_backup_is_a_copy_not_a_move(tmp_path):
    """Overwriting an existing file leaves BOTH the new primary AND a ``.bak`` of
    the old contents — the primary is never momentarily absent (power-loss safe)."""
    p = tmp_path / "x.json"
    write_json_atomic(p, {"v": 1})
    write_json_atomic(p, {"v": 2})
    bak = p.with_suffix(p.suffix + ".bak")
    assert p.exists()                      # primary present (was a copy, not a move)
    assert bak.exists()
    assert read_json(p) == {"v": 2}        # new contents
    assert read_json(bak) == {"v": 1}      # previous contents preserved


def test_no_tmp_left_behind(tmp_path):
    p = tmp_path / "x.json"
    write_json_atomic(p, {"v": 1})
    assert not p.with_suffix(p.suffix + ".tmp").exists()


def test_first_write_has_no_backup(tmp_path):
    p = tmp_path / "x.json"
    write_json_atomic(p, {"v": 1})
    assert not p.with_suffix(p.suffix + ".bak").exists()


@pytest.mark.skipif(os.name != "posix", reason="POSIX mode bits only")
def test_persisted_json_and_backup_are_owner_only(tmp_path):
    p = tmp_path / "astrodeck.json"
    write_json_atomic(p, {"secret": "one"})
    write_json_atomic(p, {"secret": "two"})
    assert stat.S_IMODE(p.stat().st_mode) == 0o600
    assert stat.S_IMODE(p.with_suffix(".json.bak").stat().st_mode) == 0o600


@pytest.mark.skipif(os.name != "posix", reason="POSIX mode bits only")
def test_read_repairs_legacy_world_readable_json(tmp_path):
    p = tmp_path / "astrodeck.json"
    p.write_text('{"secret": "legacy"}', encoding="utf-8")
    p.chmod(0o644)
    assert read_json(p) == {"secret": "legacy"}
    assert stat.S_IMODE(p.stat().st_mode) == 0o600


# ------------------------------------------------------- replace-retry exactness

def test_replace_retry_attempts_exactly_n_times_then_raises(tmp_path, monkeypatch):
    """``_replace_with_retry`` makes exactly ``_REPLACE_RETRIES`` os.replace calls
    on persistent PermissionError, then re-raises the last one (no off-by-one 4th
    replace)."""
    calls = {"n": 0}

    def always_locked(src, dst):
        calls["n"] += 1
        raise PermissionError("locked by AV")

    monkeypatch.setattr(persist.os, "replace", always_locked)
    monkeypatch.setattr(persist.time, "sleep", lambda *_: None)  # no real backoff

    with pytest.raises(PermissionError):
        persist._replace_with_retry(tmp_path / "a", tmp_path / "b")
    assert calls["n"] == _REPLACE_RETRIES


def test_replace_retry_succeeds_after_transient_lock(tmp_path, monkeypatch):
    calls = {"n": 0}
    real_replace = os.replace

    def flaky(src, dst):
        calls["n"] += 1
        if calls["n"] < _REPLACE_RETRIES:
            raise PermissionError("transient")
        return real_replace(src, dst)

    monkeypatch.setattr(persist.os, "replace", flaky)
    monkeypatch.setattr(persist.time, "sleep", lambda *_: None)

    src = tmp_path / "src"
    dst = tmp_path / "dst"
    src.write_text("hi", encoding="utf-8")
    persist._replace_with_retry(src, dst)
    assert dst.read_text(encoding="utf-8") == "hi"
    assert calls["n"] == _REPLACE_RETRIES


# ----------------------------------------------------- ConfigStore .bak recovery

def _good_config_dict() -> dict:
    return AppConfig(site=Site(name="Backyard", latitude=42.0, longitude=-71.0,
                               is_default=False)).model_dump()


def test_config_restores_from_bak_when_primary_missing(tmp_path):
    """If the live config file vanishes (power-loss between backup and replace)
    but a valid ``.bak`` exists, ``_load`` restores it instead of resetting to
    the lat0/lon0 defaults — and re-establishes the primary."""
    path = tmp_path / "astrodeck.json"
    bak = path.with_suffix(path.suffix + ".bak")
    bak.write_text(json.dumps(_good_config_dict()), encoding="utf-8")

    store = ConfigStore(path=path)
    cfg = store.cfg()
    assert cfg.site.name == "Backyard"
    assert cfg.site.latitude == 42.0
    assert cfg.site.is_default is False
    assert path.exists()                   # primary re-established from the bak


def test_config_restores_from_bak_when_primary_corrupt(tmp_path):
    """A corrupt primary with a valid ``.bak`` recovers the backup (reading the
    ``.bak`` BEFORE _recover_from_corrupt would overwrite it)."""
    path = tmp_path / "astrodeck.json"
    bak = path.with_suffix(path.suffix + ".bak")
    path.write_text("{ not valid json", encoding="utf-8")
    bak.write_text(json.dumps(_good_config_dict()), encoding="utf-8")

    store = ConfigStore(path=path)
    cfg = store.cfg()
    assert cfg.site.name == "Backyard"
    assert cfg.site.is_default is False
    # primary now parses again
    assert read_json(path)["site"]["name"] == "Backyard"
    # the known-good .bak must NOT have been clobbered by the corrupt primary
    assert read_json(bak)["site"]["name"] == "Backyard"


def test_new_config_store_falls_back_to_defaults(tmp_path):
    """No primary and no backup means a genuine first run, so defaults are safe."""
    path = tmp_path / "astrodeck.json"

    store = ConfigStore(path=path)
    cfg = store.cfg()
    assert cfg.site.is_default is True
    assert cfg.site.latitude == 0.0
    assert path.exists()


@pytest.mark.parametrize("primary", ["nonsense", "[]"])
def test_corrupt_or_invalid_config_without_backup_fails_closed(tmp_path, primary):
    path = tmp_path / "astrodeck.json"
    path.write_text(primary, encoding="utf-8")

    with pytest.raises(RuntimeError, match=r"configuration.*(corrupt|invalid)"):
        ConfigStore(path=path).cfg()

    assert path.read_text(encoding="utf-8") == primary


def test_corrupt_primary_and_backup_fail_closed_without_overwrite(tmp_path):
    path = tmp_path / "astrodeck.json"
    bak = path.with_suffix(path.suffix + ".bak")
    path.write_text("nonsense", encoding="utf-8")
    bak.write_text("also nonsense", encoding="utf-8")

    with pytest.raises(RuntimeError, match="backup"):
        ConfigStore(path=path).cfg()

    assert path.read_text(encoding="utf-8") == "nonsense"
    assert bak.read_text(encoding="utf-8") == "also nonsense"


# ------------------------------------ concurrent fingerprint writes (issue #97)


def _fingerprint_payload(position: int) -> dict:
    return {"focuser_position": position, "filter_slot": 1, "ra_hours": 1.0,
            "dec_deg": 2.0, "parked": False, "tracking": True}


def test_two_threads_are_never_inside_a_fingerprint_write_together(
    tmp_path, monkeypatch
):
    """``record`` serialises its writers.

    It became multi-threaded when the status poll started dispatching it with
    ``asyncio.to_thread``: ``poll_status`` runs both from ``_status_loop`` and
    from the ``/api/status`` route, so two worker threads can be inside it at
    once, each running an atomic write over the same path and the same staging
    directory.

    The rendezvous is what makes this deterministic rather than a race the test
    would only sometimes lose.  The patched writer waits on a barrier sized for
    every caller: serialised, exactly one thread is ever inside it, so the wait
    times out and breaks the barrier; unserialised, all of them are inside it
    together and the barrier trips.  Coalescing is switched off for this case so
    that every caller really does reach the write.

    MUTATION: replace the ``with _write_lock:`` that wraps the coalescing latch
    and the write in ``record`` with ``if True:``.  Observed under that
    mutation: ``overlapped`` holds 8 entries and the first assertion fails with
    "8 threads were inside the fingerprint write at once".
    """
    import threading

    from astrodeck.devices import fingerprint

    threads_n = 8
    path = tmp_path / "state" / "device_fingerprint.json"
    monkeypatch.setattr(fingerprint, "_PATH", path)
    monkeypatch.setattr(fingerprint, "FINGERPRINT_WRITE_INTERVAL_S", 0.0)
    fingerprint.reset_for_tests()

    barrier = threading.Barrier(threads_n)
    overlapped: list[int] = []
    writes: list[int] = []
    real_write = fingerprint.write_json_atomic

    def rendezvous_write(target, payload, **kwargs):
        writes.append(threading.get_ident())
        try:
            barrier.wait(timeout=1.0)
        except threading.BrokenBarrierError:
            pass                      # alone in here, which is the requirement
        else:
            overlapped.append(threading.get_ident())
        return real_write(target, payload, **kwargs)

    monkeypatch.setattr(fingerprint, "write_json_atomic", rendezvous_write)

    def call(position: int) -> None:
        fingerprint.record(focuser_position=position, filter_slot=1,
                           ra_hours=1.0, dec_deg=2.0, parked=False,
                           tracking=True)

    workers = [threading.Thread(target=call, args=(11218,))
               for _ in range(threads_n)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=60)
    assert not any(worker.is_alive() for worker in workers), "record deadlocked"

    assert overlapped == [], (
        f"{len(overlapped)} threads were inside the fingerprint write at once"
    )
    assert len(writes) == threads_n, "premise: every caller reached the write"
    # Serialised or not, the last writer must leave a complete file behind.
    assert json.loads(path.read_text(encoding="utf-8")) == (
        _fingerprint_payload(11218))

    fingerprint.reset_for_tests()


def test_concurrent_fingerprint_records_coalesce_to_one_readable_file(
    tmp_path, monkeypatch
):
    """Several threads inside one interval leave one complete file, and the
    next interval writes exactly once more.

    MUTATION: change the latch in ``record`` from
    ``now - _last_write < FINGERPRINT_WRITE_INTERVAL_S`` to
    ``now - _last_write < 0``.  Observed under that mutation: the first round
    writes 8 times instead of once and the first assertion fails with
    "8 threads wrote in one interval; expected one".
    """
    import threading

    from astrodeck.devices import fingerprint

    threads_n = 8
    path = tmp_path / "state" / "device_fingerprint.json"
    monkeypatch.setattr(fingerprint, "_PATH", path)
    fingerprint.reset_for_tests()

    writes: list[int] = []
    real_write = fingerprint.write_json_atomic

    def counting_write(target, payload, **kwargs):
        writes.append(threading.get_ident())
        return real_write(target, payload, **kwargs)

    monkeypatch.setattr(fingerprint, "write_json_atomic", counting_write)

    offset = 0.0
    monkeypatch.setattr(fingerprint, "_now", lambda: time.monotonic() + offset)

    def call(position: int) -> None:
        fingerprint.record(focuser_position=position, filter_slot=1,
                           ra_hours=1.0, dec_deg=2.0, parked=False,
                           tracking=True)

    def fan_out(position: int) -> None:
        workers = [threading.Thread(target=call, args=(position,))
                   for _ in range(threads_n)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(timeout=60)
        assert not any(w.is_alive() for w in workers), "record deadlocked"

    fan_out(11218)
    assert len(writes) == 1, (
        f"{len(writes)} threads wrote in one interval; expected one")
    assert json.loads(path.read_text(encoding="utf-8")) == (
        _fingerprint_payload(11218))

    written_so_far = len(writes)
    fan_out(11218)
    assert len(writes) == written_so_far, (
        "the coalescing interval was not honoured: "
        f"{len(writes) - written_so_far} extra writes inside one interval"
    )

    offset = fingerprint.FINGERPRINT_WRITE_INTERVAL_S + 1.0
    fan_out(11300)
    assert len(writes) == written_so_far + 1, (
        f"{len(writes) - written_so_far} writes after the interval elapsed; "
        "expected exactly one"
    )
    assert json.loads(path.read_text(encoding="utf-8")) == (
        _fingerprint_payload(11300))

    fingerprint.reset_for_tests()


def test_a_slow_fingerprint_write_announces_itself_to_the_loop(
    tmp_path, monkeypatch
):
    """The cost of this write was invisible for months because nothing timed it.

    The notice is recorded by the worker and published by the loop, never by
    ``record`` itself: ``EventBus.publish`` hands events to
    ``asyncio.Queue.put_nowait``, which is loop-affine (the same rule the ZWO
    pulse watchdog thread follows).

    The notice goes to the bus, so it reaches the WS stream, the /api/logs ring
    (CAP_VIEW_STATUS, the lowest capability) and the durable night log. It must
    therefore carry NO absolute path: see the owner ruling at the top of
    ``test_no_absolute_paths_externally.py``, and note that CAPTURE_DIR names
    the operator's Windows account.

    MUTATION A: delete the ``if elapsed >= FINGERPRINT_SLOW_WRITE_S:`` block in
    ``record``.  Observed under that mutation: ``take_slow_write_notice()``
    returns None after the slow write and the first assertion fails.
    MUTATION B: put the directory back, i.e. end the message with
    ``f"(state directory {path.parent})"``.  Observed: the notice carries the
    absolute state directory and the no-absolute-path assertion fails.
    """
    import os as _os

    from astrodeck.devices import fingerprint

    path = tmp_path / "state" / "device_fingerprint.json"
    monkeypatch.setattr(fingerprint, "_PATH", path)
    monkeypatch.setattr(fingerprint, "FINGERPRINT_SLOW_WRITE_S", 0.02)
    fingerprint.reset_for_tests()
    real_write = fingerprint.write_json_atomic

    def slow_write(target, payload, **kwargs):
        time.sleep(0.05)
        return real_write(target, payload, **kwargs)

    monkeypatch.setattr(fingerprint, "write_json_atomic", slow_write)
    fingerprint.record(focuser_position=11218, filter_slot=1, ra_hours=1.0,
                       dec_deg=2.0, parked=False, tracking=True)

    notice = fingerprint.take_slow_write_notice()
    assert notice is not None, "a slow fingerprint write said nothing"
    assert "device fingerprint write took" in notice
    assert path.name in notice, "the notice does not say what was slow"
    # No absolute path leaves this process, for anybody.
    assert str(path.parent) not in notice, notice
    assert _os.sep not in notice, notice
    assert "/" not in notice, notice
    # Handed over once; the loop must not republish it on every poll.
    assert fingerprint.take_slow_write_notice() is None

    # A write inside the budget is silent.
    monkeypatch.setattr(fingerprint, "write_json_atomic", real_write)
    fingerprint.reset_for_tests()
    fingerprint.record(focuser_position=11219, filter_slot=1, ra_hours=1.0,
                       dec_deg=2.0, parked=False, tracking=True)
    assert fingerprint.take_slow_write_notice() is None

    fingerprint.reset_for_tests()


def test_a_slow_fingerprint_write_that_then_fails_still_announces_itself(
    tmp_path, monkeypatch
):
    """The 7 s case on the rig was a propagating SetSecurityInfo, and that path
    can end in ``PrivatePermissionsError`` rather than returning.

    Timed only on success, those seconds would be swallowed by the error
    swallow in complete silence -- the same invisibility the self-report was
    added to end, and the failure mode is strictly worse than the slow success
    because nothing was written either.

    MUTATION: move the timing and the notice out of the ``finally`` and back
    onto the success path (time after ``write_json_atomic`` returns).
    Observed under that mutation: ``take_slow_write_notice()`` returns None and
    the first assertion fails.
    """
    from astrodeck.devices import fingerprint

    path = tmp_path / "state" / "device_fingerprint.json"
    monkeypatch.setattr(fingerprint, "_PATH", path)
    monkeypatch.setattr(fingerprint, "FINGERPRINT_SLOW_WRITE_S", 0.02)
    fingerprint.reset_for_tests()

    class DeliberateWriteFailure(RuntimeError):
        pass

    def slow_then_fail(_target, _payload, **_kwargs):
        time.sleep(0.05)
        raise DeliberateWriteFailure("private DACL unavailable")

    monkeypatch.setattr(fingerprint, "write_json_atomic", slow_then_fail)
    # Still swallowed: bookkeeping must never break a run.
    fingerprint.record(focuser_position=11218, filter_slot=1, ra_hours=1.0,
                       dec_deg=2.0, parked=False, tracking=True)

    notice = fingerprint.take_slow_write_notice()
    assert notice is not None, "a slow write that failed said nothing at all"
    assert "failed after" in notice, notice
    assert path.name in notice
    assert str(path.parent) not in notice, notice
    assert not path.exists(), "premise: nothing was written"

    fingerprint.reset_for_tests()


def test_poll_status_dispatches_the_fingerprint_write_off_the_loop(
    tmp_path, monkeypatch
):
    """The status poll neither blocks on the write nor drops its warning.

    MUTATION A: change the call in ``poll_status`` back to a direct
    ``_fp.record(...)``.  Observed: ``writer_threads`` holds the MainThread and
    the off-the-loop assertion fails.
    MUTATION B: delete the ``take_slow_write_notice`` / ``bus.log`` block that
    follows it.  Observed: ``logged`` is empty and the warning assertion fails.
    """
    import asyncio
    import threading

    from astrodeck import hub as hub_module
    from astrodeck.devices import fingerprint

    path = tmp_path / "state" / "device_fingerprint.json"
    monkeypatch.setattr(fingerprint, "_PATH", path)
    monkeypatch.setattr(fingerprint, "FINGERPRINT_SLOW_WRITE_S", 0.02)
    fingerprint.reset_for_tests()

    writer_threads: list[threading.Thread] = []
    real_write = fingerprint.write_json_atomic

    def slow_write(target, payload, **kwargs):
        writer_threads.append(threading.current_thread())
        time.sleep(0.05)
        return real_write(target, payload, **kwargs)

    monkeypatch.setattr(fingerprint, "write_json_atomic", slow_write)

    logged: list[tuple] = []
    recorder = type("Recorder", (), {
        "log": staticmethod(lambda *args: logged.append(args)),
        "publish": staticmethod(lambda *args, **kwargs: None),
    })()
    monkeypatch.setattr(hub_module, "bus", recorder)

    async def drive():
        loop_thread = threading.current_thread()
        await hub_module.Hub().poll_status()
        return loop_thread

    loop_thread = asyncio.run(drive())

    assert writer_threads, "premise: the status poll wrote the fingerprint"
    assert all(t is not loop_thread for t in writer_threads), (
        "the fingerprint write ran on the event loop thread; on the rig that "
        "write was measured at 7 s"
    )
    assert [a for a in logged if a[0] == "warning"
            and "device fingerprint write took" in a[1]], (
        f"the slow write was never published to the bus: {logged}"
    )

    fingerprint.reset_for_tests()


def test_a_measured_vouch_is_not_overwritten_by_a_worker_mid_observe(
    tmp_path, monkeypatch
):
    """``vouch`` and ``record`` write the same trio from different threads.

    ``record`` runs on a worker now, and ``_observe`` advances ``_known`` to
    whatever the device last reported whenever ``_confirmed`` is true.
    ``vouch`` runs on the loop and adopts a MEASURED position -- an autofocus
    result -- as the trusted reference. Interleaved so that ``_observe``
    resolves after ``vouch``, the stale device reading lands on top of the
    measurement, ``verdict`` then answers focus_trusted=False, and the resume
    ladder re-runs a full autofocus on every ten-minute retry: the exact waste
    ``vouch`` exists to prevent.

    The park inside ``_observe`` is what makes this deterministic. Serialised,
    ``vouch`` cannot enter until ``record`` has left ``_observe``, so the
    measurement is applied last and survives; unserialised, it lands first and
    is overwritten.

    MUTATION A: replace ``with _state_lock:`` in ``vouch`` with ``if True:``.
    MUTATION B: replace the ``with _state_lock:`` that wraps
    ``_observe(focuser_position)`` in ``record`` with ``if True:``.
    Observed under each: ``_known`` is 999 rather than 11218 and the assertion
    fails with "a worker's stale device reading overwrote a measured
    position".
    """
    import threading

    from astrodeck.devices import fingerprint

    measured, stale = 11218, 999
    path = tmp_path / "state" / "device_fingerprint.json"
    monkeypatch.setattr(fingerprint, "_PATH", path)
    fingerprint.reset_for_tests()
    # A watched device holding a known count: _observe will now advance it.
    fingerprint.vouch(focuser_position=100)
    assert fingerprint._confirmed is True, "premise: the device is confirmed"

    in_section = threading.Event()
    vouch_done = threading.Event()
    real_observe = fingerprint._observe

    def parked_observe(position):
        in_section.set()
        vouch_done.wait(timeout=1.0)
        return real_observe(position)

    monkeypatch.setattr(fingerprint, "_observe", parked_observe)

    worker = threading.Thread(target=lambda: fingerprint.record(
        focuser_position=stale, filter_slot=1, ra_hours=1.0, dec_deg=2.0,
        parked=False, tracking=True))
    worker.start()
    assert in_section.wait(timeout=30), "premise: the worker reached _observe"
    fingerprint.vouch(focuser_position=measured)
    vouch_done.set()
    worker.join(timeout=30)
    assert not worker.is_alive(), "record deadlocked"

    assert fingerprint._known == measured, (
        "a worker's stale device reading overwrote a measured position; the "
        "resume ladder will re-autofocus every ten minutes"
    )
    assert fingerprint.verdict(
        focuser_position=measured).focus_trusted is True

    fingerprint.reset_for_tests()


def test_verdict_never_compares_the_boot_against_a_file_this_boot_just_wrote(
    tmp_path, monkeypatch
):
    """``verdict`` re-takes its decision AFTER the disk read, not before it.

    The in-process branch is chosen when nothing has recorded yet -- the
    un-restarted case, where the file has not been touched since the last
    process wrote it. Decide that, release the lock, then read, and a worker's
    ``record`` can complete both ``_ensure_boot`` and its ``os.replace`` in
    between. What comes back is then a file THIS boot has just written, and the
    comparison is the device against itself: focus_trusted=True after a power
    cut that moved the focuser, the resume ladder skips the autofocus, and the
    night is soft. That is the dangerous direction, and it is the exact
    "comparing the boot against itself" hazard the ``_boot`` snapshot exists to
    prevent.

    The park inside the read is what makes it deterministic: the worker runs
    entirely inside the window, so the file on disk really has changed under
    the reader by the time it returns.

    MUTATION: delete the second ``with _state_lock:`` block in ``verdict`` and
    judge ``raw`` directly after the read.  Observed under that mutation:
    focus_trusted is True and the assertion fails with "verdict trusted a
    focuser position it had just written itself".
    """
    import threading

    from astrodeck.devices import fingerprint

    pre_boot_pos, device_pos = 5000, 11218
    path = tmp_path / "state" / "device_fingerprint.json"
    write_json_atomic(path, {"focuser_position": pre_boot_pos,
                             "filter_slot": 1, "ra_hours": 1.0, "dec_deg": 2.0,
                             "parked": False, "tracking": True}, backup=False)
    monkeypatch.setattr(fingerprint, "_PATH", path)
    fingerprint.reset_for_tests()

    reader = threading.current_thread()
    parked = threading.Event()
    worker_done = threading.Event()
    real_read = fingerprint.read_json_or

    def parked_read(target, default=None):
        # Only the verdict's own read parks; _ensure_boot's read runs on the
        # worker thread and must go straight through.
        if threading.current_thread() is reader and not parked.is_set():
            parked.set()
            worker_done.wait(timeout=30)
        return real_read(target, default)

    monkeypatch.setattr(fingerprint, "read_json_or", parked_read)

    def worker():
        assert parked.wait(timeout=30), "premise: the reader parked"
        fingerprint.record(focuser_position=device_pos, filter_slot=1,
                           ra_hours=1.0, dec_deg=2.0, parked=False,
                           tracking=True)
        worker_done.set()

    thread = threading.Thread(target=worker)
    thread.start()
    got = fingerprint.verdict(focuser_position=device_pos)
    thread.join(timeout=30)
    assert not thread.is_alive(), "record deadlocked"

    # Premises: the worker really did overwrite the file inside the window, and
    # the pre-boot number really is the only honest basis left.
    assert json.loads(path.read_text(encoding="utf-8"))[
        "focuser_position"] == device_pos
    assert fingerprint._known == pre_boot_pos

    assert got.focus_trusted is False, (
        "verdict trusted a focuser position it had just written itself; the "
        "resume ladder will skip the autofocus a power cut made necessary"
    )

    fingerprint.reset_for_tests()
