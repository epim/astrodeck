"""Supervisor state machine: apply-on-92, health-probe, rollback, crash backoff.

Deterministic: the process + health + clock + sleep are all injected, so no real
subprocess or socket is touched."""
import json

from supervisor import protocol as P
from supervisor.supervisor import Supervisor


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
              health_timeout_s=3.0):
    it = iter(procs)
    launches = []

    def launch(version):
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
        root, launch_fn=launch, health_fn=health, sleep_fn=sleep, now_fn=now,
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
