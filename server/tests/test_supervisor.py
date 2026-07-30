"""Supervisor state machine: apply-on-92, health-probe, rollback, crash backoff.

Deterministic: the process + health + clock + sleep are all injected, so no real
subprocess or socket is touched."""
import json

import pytest

from supervisor import protocol as P
from supervisor.supervisor import ROLLBACK_FREEZE_FILE, Supervisor, _health_url


class FakeProc:
    """A scripted process. ``poll()`` reports alive (None) until ``wait()`` or
    ``terminate()``; ``wait()`` returns the scripted exit code."""

    def __init__(self, wait_code, alive=True):
        self.wait_code = wait_code
        self.alive = alive
        self.terminated = False

    def poll(self):
        return None if (self.alive and not self.terminated) else self.wait_code

    def wait(self):
        self.alive = False
        return self.wait_code

    def terminate(self):
        self.terminated = True


def _make_sup(root, procs, health, *, sleeps=None, initial_version=None,
              health_timeout_s=3.0, launch_fn=None, restore_fn=None):
    it = iter(procs)
    launches = []

    def default_launch(version):
        launches.append(version)
        return next(it)

    clock = {"t": 0.0}

    def now():
        clock["t"] += 1.0
        return clock["t"]

    def sleep(s):
        if sleeps is not None:
            sleeps.append(s)

    sup = Supervisor(
        root, launch_fn=launch_fn or default_launch, health_fn=health,
        restore_fn=restore_fn, sleep_fn=sleep, now_fn=now,
        health_timeout_s=health_timeout_s, health_poll_s=0.0, max_backoff_s=8.0,
        initial_version=initial_version, log=lambda m: None)
    sup.launches = launches
    return sup


def _stage(root, version):
    P.Layout(root).release(version).mkdir(parents=True, exist_ok=True)


def _write_pending(root, version):
    lay = P.Layout(root)
    lay.ensure()
    P.write_json_atomic(lay.pending, {"version": version, "path": str(lay.release(version)), "ts": 1.0})


def _result(root):
    return json.loads(P.Layout(root).result.read_text())


# ------------------------------------------------------------ pointer reading

def test_pointers_tolerate_a_byte_order_mark(tmp_path):
    """A BOM in `current` must not become part of the version.

    Cost a live install 2026-07-30: the pointer was rewritten with PowerShell's
    `Set-Content -Encoding utf8`, which emits EF BB BF. utf-8 decodes that to
    U+FEFF and `strip()` leaves it (a BOM is not whitespace), so the release
    path became releases/<BOM>0.2.21 — a directory that does not exist. The
    launcher's PYTHONPATH then pointed at nothing and the server imported the
    stale astrodeck sitting in site-packages: running, answering /healthz, and
    serving a DIFFERENT version than the pointer claimed."""
    lay = P.Layout(tmp_path)
    lay.ensure()
    for path in (lay.current, lay.last_good):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"\xef\xbb\xbf0.2.21\n")
    assert lay.read_current() == "0.2.21"
    assert lay.read_last_good() == "0.2.21"


def test_pointers_still_read_plain_utf8(tmp_path):
    lay = P.Layout(tmp_path)
    lay.ensure()
    lay.set_current("0.3.0")
    lay.set_last_good("0.3.0")
    assert lay.read_current() == "0.3.0"
    assert lay.read_last_good() == "0.3.0"


def test_an_empty_or_bom_only_pointer_reads_as_absent(tmp_path):
    """A pointer holding nothing but a BOM is not a version — it must read as
    absent so the supervisor falls back to --initial-version rather than trying
    to launch a release named after an invisible character."""
    lay = P.Layout(tmp_path)
    lay.ensure()
    lay.current.write_bytes(b"\xef\xbb\xbf\n")
    assert lay.read_current() is None


# ---------------------------------------------------------------- scenarios

def test_clean_stop_seeds_current_and_last_good(tmp_path):
    sup = _make_sup(tmp_path, [FakeProc(P.EXIT_STOP)], health=lambda v: True,
                    initial_version="0.1.0")
    kind, ver = sup.run_forever(max_iterations=4)
    assert kind == "stopped" and ver == "0.1.0"
    lay = P.Layout(tmp_path)
    assert lay.read_current() == "0.1.0"
    assert lay.read_last_good() == "0.1.0"
    assert sup.launches == ["0.1.0"]


def test_successful_update_commits_new_version(tmp_path):
    P.Layout(tmp_path).set_current("0.1.0")
    _stage(tmp_path, "0.2.0")
    _write_pending(tmp_path, "0.2.0")
    procs = [FakeProc(P.EXIT_APPLY_UPDATE), FakeProc(P.EXIT_STOP)]
    sup = _make_sup(tmp_path, procs, health=lambda v: v == "0.2.0")
    kind, ver = sup.run_forever(max_iterations=6)
    assert kind == "stopped" and ver == "0.2.0"
    lay = P.Layout(tmp_path)
    assert lay.read_current() == "0.2.0"
    assert lay.read_last_good() == "0.2.0"
    res = _result(tmp_path)
    assert res["ok"] is True and res["version"] == "0.2.0" and res["from"] == "0.1.0"
    assert sup.launches == ["0.1.0", "0.2.0"]
    assert not lay.is_failed("0.2.0")


def test_unhealthy_update_rolls_back_and_marks_failed(tmp_path):
    P.Layout(tmp_path).set_current("0.1.0")
    _stage(tmp_path, "0.2.0")
    _write_pending(tmp_path, "0.2.0")
    # v1 -> request update; v2 probe never healthy (rolled back + terminated); v1 -> stop
    procs = [FakeProc(P.EXIT_APPLY_UPDATE), FakeProc(0, alive=True), FakeProc(P.EXIT_STOP)]
    sup = _make_sup(tmp_path, procs, health=lambda v: False, health_timeout_s=3.0)
    kind, ver = sup.run_forever(max_iterations=8)
    assert kind == "stopped" and ver == "0.1.0"
    lay = P.Layout(tmp_path)
    assert lay.read_current() == "0.1.0"
    assert lay.read_last_good() == "0.1.0"
    assert lay.is_failed("0.2.0")
    res = _result(tmp_path)
    assert res["ok"] is False and res["version"] == "0.2.0"
    assert sup.launches == ["0.1.0", "0.2.0", "0.1.0"]
    assert procs[1].terminated is True


def test_failed_version_is_never_retried(tmp_path):
    lay = P.Layout(tmp_path)
    lay.set_current("0.1.0")
    _stage(tmp_path, "0.2.0")
    lay.ensure()
    lay.mark_failed("0.2.0", "earlier failure")
    _write_pending(tmp_path, "0.2.0")
    procs = [FakeProc(P.EXIT_APPLY_UPDATE), FakeProc(P.EXIT_STOP)]
    sup = _make_sup(tmp_path, procs, health=lambda v: True)
    kind, ver = sup.run_forever(max_iterations=6)
    assert kind == "stopped" and ver == "0.1.0"
    assert lay.read_current() == "0.1.0"
    assert sup.launches == ["0.1.0", "0.1.0"]  # never launched the failed 0.2.0


def test_apply_skipped_when_release_not_staged(tmp_path):
    P.Layout(tmp_path).set_current("0.1.0")
    _write_pending(tmp_path, "0.2.0")  # pending, but releases/0.2.0 does NOT exist
    procs = [FakeProc(P.EXIT_APPLY_UPDATE), FakeProc(P.EXIT_STOP)]
    sup = _make_sup(tmp_path, procs, health=lambda v: True)
    kind, ver = sup.run_forever(max_iterations=6)
    assert kind == "stopped" and ver == "0.1.0"
    assert sup.launches == ["0.1.0", "0.1.0"]


def test_apply_rejects_unsafe_version(tmp_path):
    P.Layout(tmp_path).set_current("0.1.0")
    lay = P.Layout(tmp_path)
    lay.ensure()
    P.write_json_atomic(lay.pending, {"version": "../evil", "path": "x", "ts": 1.0})
    procs = [FakeProc(P.EXIT_APPLY_UPDATE), FakeProc(P.EXIT_STOP)]
    sup = _make_sup(tmp_path, procs, health=lambda v: True)
    kind, ver = sup.run_forever(max_iterations=6)
    assert kind == "stopped" and ver == "0.1.0"
    assert sup.launches == ["0.1.0", "0.1.0"]


def test_crash_triggers_backoff_relaunch(tmp_path):
    P.Layout(tmp_path).set_current("0.1.0")
    sleeps = []
    procs = [FakeProc(7), FakeProc(P.EXIT_STOP)]  # crash, then clean stop
    sup = _make_sup(tmp_path, procs, health=lambda v: True, sleeps=sleeps)
    kind, ver = sup.run_forever(max_iterations=6)
    assert kind == "stopped" and ver == "0.1.0"
    assert sup.launches == ["0.1.0", "0.1.0"]
    assert sleeps and sleeps[0] > 0  # backed off before relaunch


# --------------------------------------------------- health-probe host (finding)

@pytest.mark.parametrize("host,expect", [
    ("0.0.0.0", "http://127.0.0.1:8800/healthz"),
    ("", "http://127.0.0.1:8800/healthz"),
    ("*", "http://127.0.0.1:8800/healthz"),
    ("::", "http://[::1]:8800/healthz"),
    ("127.0.0.1", "http://127.0.0.1:8800/healthz"),
    ("192.168.1.5", "http://192.168.1.5:8800/healthz"),
    ("::1", "http://[::1]:8800/healthz"),
])
def test_health_url_normalizes_wildcard_bind(host, expect):
    # A wildcard bind (0.0.0.0/::) is not connectable as a peer (WSAEADDRNOTAVAIL
    # on Windows); the probe must dial loopback or every update rolls back.
    assert _health_url(host, 8800) == expect


# --------------------------------------------------- venv rollback (finding)

def _write_freeze(root, text="httpx==0.27.0\n"):
    lay = P.Layout(root)
    lay.ensure()
    (lay.state / ROLLBACK_FREEZE_FILE).write_text(text, encoding="utf-8")


def test_rollback_restores_venv_and_clears_snapshot(tmp_path):
    P.Layout(tmp_path).set_current("0.1.0")
    _stage(tmp_path, "0.2.0")
    _write_pending(tmp_path, "0.2.0")
    _write_freeze(tmp_path, "httpx==0.27.0\n")
    restored = []
    procs = [FakeProc(P.EXIT_APPLY_UPDATE), FakeProc(0, alive=True), FakeProc(P.EXIT_STOP)]
    sup = _make_sup(tmp_path, procs, health=lambda v: False, health_timeout_s=3.0,
                    restore_fn=lambda text: restored.append(text))
    kind, ver = sup.run_forever(max_iterations=8)
    assert kind == "stopped" and ver == "0.1.0"
    assert restored == ["httpx==0.27.0\n"]  # venv synced back to pre-update snapshot
    assert not (P.Layout(tmp_path).state / ROLLBACK_FREEZE_FILE).exists()


def test_commit_clears_snapshot_without_restoring(tmp_path):
    P.Layout(tmp_path).set_current("0.1.0")
    _stage(tmp_path, "0.2.0")
    _write_pending(tmp_path, "0.2.0")
    _write_freeze(tmp_path)
    restored = []
    procs = [FakeProc(P.EXIT_APPLY_UPDATE), FakeProc(P.EXIT_STOP)]
    sup = _make_sup(tmp_path, procs, health=lambda v: v == "0.2.0",
                    restore_fn=lambda text: restored.append(text))
    kind, ver = sup.run_forever(max_iterations=6)
    assert kind == "stopped" and ver == "0.2.0"
    assert restored == []  # committed: no venv revert
    assert not (P.Layout(tmp_path).state / ROLLBACK_FREEZE_FILE).exists()


# --------------------------------------------------- rejected apply result (finding)

def test_rejected_failed_version_writes_result(tmp_path):
    lay = P.Layout(tmp_path)
    lay.set_current("0.1.0")
    _stage(tmp_path, "0.2.0")
    lay.ensure()
    lay.mark_failed("0.2.0", "earlier failure")
    _write_pending(tmp_path, "0.2.0")
    procs = [FakeProc(P.EXIT_APPLY_UPDATE), FakeProc(P.EXIT_STOP)]
    sup = _make_sup(tmp_path, procs, health=lambda v: True)
    kind, ver = sup.run_forever(max_iterations=6)
    assert kind == "stopped" and ver == "0.1.0"
    res = _result(tmp_path)  # server surfaces the failure instead of a silent no-op
    assert res["ok"] is False and res["version"] == "0.2.0"
    assert "previously failed" in res["reason"]


def test_rejected_unstaged_version_writes_result(tmp_path):
    P.Layout(tmp_path).set_current("0.1.0")
    _write_pending(tmp_path, "0.2.0")  # pending, but releases/0.2.0 not staged
    procs = [FakeProc(P.EXIT_APPLY_UPDATE), FakeProc(P.EXIT_STOP)]
    sup = _make_sup(tmp_path, procs, health=lambda v: True)
    kind, ver = sup.run_forever(max_iterations=6)
    assert kind == "stopped" and ver == "0.1.0"
    res = _result(tmp_path)
    assert res["ok"] is False and res["version"] == "0.2.0"
    assert "not staged" in res["reason"]


# --------------------------------------------------- loop containment (finding)

def test_loop_survives_transient_launch_error(tmp_path):
    P.Layout(tmp_path).set_current("0.1.0")
    sleeps = []
    procs = iter([FakeProc(P.EXIT_STOP)])
    calls = {"n": 0}

    def flaky_launch(version):
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError("WinError 32: state file held open by scanner")
        return next(procs)

    sup = _make_sup(tmp_path, [], health=lambda v: True, sleeps=sleeps,
                    launch_fn=flaky_launch)
    kind, ver = sup.run_forever(max_iterations=6)
    # the watchdog did NOT die on the transient OSError; it backed off and recovered
    assert kind == "stopped" and ver == "0.1.0"
    assert calls["n"] == 2 and sleeps and sleeps[0] > 0
