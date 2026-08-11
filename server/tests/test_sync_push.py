"""Sync Phase 2 — pushing frames off the rig while the run is still going.

The property under test throughout is the one Phase 1 was built on: **state is
derived, never remembered**. Every test that matters here kills a pass halfway,
corrupts a destination, or restarts the runner, and then asserts the NEXT pass
computes exactly the right work from nothing but what the two sides hold.
"""
from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from astrodeck.sync import manifest as m
from astrodeck.sync.destination import LocalDirDestination
from astrodeck.sync.push import (DEBOUNCE_S, FAIL_LOUD_AFTER, SWEEP_INTERVAL_S,
                                 PushState, due, push_once)


def _frame(root: Path, rel: str, payload: bytes = b"FITS-DATA" * 100,
           *, age_s: float = 60.0) -> Path:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(payload)
    old = time.time() - age_s
    os.utime(p, (old, old))          # settled: older than DEFAULT_SETTLE_S
    return p


@pytest.fixture
def rig(tmp_path):
    src = tmp_path / "captures"
    src.mkdir()
    return src


@pytest.fixture
def nas(tmp_path):
    return LocalDirDestination(tmp_path / "nas", label="nas")


class TestOnePass:
    def test_sends_what_the_destination_lacks(self, rig, nas):
        _frame(rig, "NGC 6946/a.fits")
        _frame(rig, "NGC 6946/b.fits")
        r = push_once(rig, nas)
        assert r.sent == 2 and r.failed == 0
        assert (nas.root / "NGC 6946/a.fits").exists()
        assert r.ok

    def test_is_idempotent(self, rig, nas):
        _frame(rig, "NGC 6946/a.fits")
        push_once(rig, nas)
        again = push_once(rig, nas)
        assert again.sent == 0
        assert again.already_there == 1

    def test_an_unsettled_frame_is_not_offered(self, rig, nas):
        """THE torn-frame guard. save_fits streams into the FINAL filename, so a
        file that appeared a moment ago may be half a frame."""
        _frame(rig, "NGC 6946/fresh.fits", age_s=0.0)
        r = push_once(rig, nas)
        assert r.sent == 0, "pushed a frame that might still be being written"
        assert not (nas.root / "NGC 6946/fresh.fits").exists()

    def test_a_settled_frame_after_the_window_is_offered(self, rig, nas):
        _frame(rig, "NGC 6946/fresh.fits", age_s=0.0)
        assert push_once(rig, nas).sent == 0
        # ...and once it has settled, the very same diff offers it. Nothing was
        # remembered in between.
        assert push_once(rig, nas, settle_s=0.0).sent == 1

    def test_a_rewritten_frame_is_re_sent(self, rig, nas):
        """A saved frame can still change: the WCS worker reopens it mode=update
        and merges astrometry in place. Hashing content means nobody has to
        predict that."""
        p = _frame(rig, "NGC 6946/a.fits")
        push_once(rig, nas)
        _frame(rig, "NGC 6946/a.fits", payload=b"FITS-DATA-WITH-WCS" * 100)
        r = push_once(rig, nas)
        assert r.sent == 1, "a rewritten frame was not re-sent"
        assert (nas.root / "NGC 6946/a.fits").read_bytes() == p.read_bytes()

    def test_a_truncated_destination_file_is_repaired(self, rig, nas):
        """The whole reason for hashing rather than trusting size+mtime."""
        _frame(rig, "NGC 6946/a.fits")
        push_once(rig, nas)
        (nas.root / "NGC 6946/a.fits").write_bytes(b"half")
        r = push_once(rig, nas)
        assert r.sent == 1, "a corrupt destination file was accepted as present"

    def test_extra_files_are_reported_and_never_deleted(self, rig, nas):
        _frame(rig, "NGC 6946/a.fits")
        push_once(rig, nas)
        keep = nas.root / "NGC 6946" / "processed_master.fits"
        keep.write_bytes(b"someone else's work")
        r = push_once(rig, nas)
        assert r.extra_at_destination >= 1
        assert keep.exists(), "the push deleted something at the destination"

    def test_limit_bounds_a_pass_and_the_rest_resume(self, rig, nas):
        for i in range(5):
            _frame(rig, f"NGC 6946/f{i}.fits")
        first = push_once(rig, nas, limit=2)
        assert first.sent == 2
        second = push_once(rig, nas)
        assert second.sent == 3, "the remainder did not resume without bookkeeping"

    def test_oldest_first(self, rig, nas):
        """A stacker wants a contiguous run, not a random 60% of the night."""
        for i, age in enumerate([300, 200, 100]):
            _frame(rig, f"NGC 6946/f{i}.fits", age_s=age)
        src = m.build(m.walk_facts(rig), root=rig, now=time.time())
        order = [e.relpath for e in m.diff(src, nas.manifest()).transfers]
        assert order == ["NGC 6946/f0.fits", "NGC 6946/f1.fits",
                         "NGC 6946/f2.fits"]


class TestFailureIsData:
    def test_an_unreachable_destination_does_not_raise(self, rig):
        _frame(rig, "NGC 6946/a.fits")

        class Broken:
            label = "broken"
            def manifest(self):
                raise OSError("network path not found")
            def put(self, rel, src):      # pragma: no cover
                raise AssertionError("must not be reached")

        r = push_once(rig, Broken())
        assert not r.ok and "network path not found" in r.error

    def test_one_bad_file_does_not_stop_the_pass(self, rig, nas, monkeypatch):
        for i in range(3):
            _frame(rig, f"NGC 6946/f{i}.fits")
        real = nas.put
        def flaky(rel, src):
            if rel.endswith("f1.fits"):
                raise OSError("disk full")
            return real(rel, src)
        monkeypatch.setattr(nas, "put", flaky)
        r = push_once(rig, nas)
        assert r.sent == 2 and r.failed == 1
        assert not r.ok
        # ...and the failed one comes back next pass, with nothing remembered.
        monkeypatch.setattr(nas, "put", real)
        assert push_once(rig, nas).sent == 1

    def test_a_partial_write_never_appears_at_the_final_name(self, rig, nas,
                                                             monkeypatch):
        """A reader on the far end must never see half a frame."""
        _frame(rig, "NGC 6946/a.fits")
        import shutil as _sh
        def die_midway(s, d):
            Path(d).write_bytes(b"half a frame")
            raise OSError("link went down")
        monkeypatch.setattr(_sh, "copyfile", die_midway)
        r = push_once(rig, nas)
        assert r.failed == 1
        assert not (nas.root / "NGC 6946/a.fits").exists(), \
            "a torn frame appeared at its final name"
        assert not list(nas.root.rglob("*.part")), "left a .part behind"


class TestCadence:
    def test_a_burst_costs_one_pass_not_five(self):
        # last_attempt_at set so the SWEEP branch is not what answers here —
        # otherwise this would pass for the wrong reason (a fresh state is
        # always sweep-due, which is correct and is the next test's subject).
        st = PushState()
        now = 1000.0
        st.last_attempt_at = now
        assert not due(st, pending_event=True, now=now, last_event_at=now),             "a pass started on the first frame of a burst"
        assert due(st, pending_event=True, now=now + DEBOUNCE_S,
                   last_event_at=now), "the burst never resolved into a pass"

    def test_a_fresh_runner_is_immediately_due(self):
        """On startup the destination may be hours behind — a brand-new state
        must sweep at once rather than wait out a full interval."""
        assert due(PushState(), pending_event=False, now=1000.0)

    def test_the_sweep_does_not_depend_on_an_event(self):
        """What makes a dropped event, a restart, or an offline hour heal."""
        st = PushState()
        st.last_attempt_at = 1000.0
        assert not due(st, pending_event=False, now=1000.0 + 10)
        assert due(st, pending_event=False, now=1000.0 + SWEEP_INTERVAL_S)


class TestStateIsObservationOnly:
    def test_failures_escalate_then_clear(self):
        st = PushState()
        bad = push_once.__globals__["PushResult"](error="nope")
        for _ in range(FAIL_LOUD_AFTER):
            st.record(bad)
        assert st.should_alarm
        good = push_once.__globals__["PushResult"]()
        st.record(good)
        assert not st.should_alarm and st.consecutive_failures == 0

    def test_a_wrong_state_cannot_make_the_sync_wrong(self, rig, nas):
        """State is for the UI. The work list always comes from a fresh diff."""
        _frame(rig, "NGC 6946/a.fits")
        st = PushState()
        st.total_sent = 999
        st.last_ok_at = time.time()
        r = push_once(rig, nas)
        assert r.sent == 1, "a stale state field suppressed a real transfer"

    def test_payload_is_json_safe(self, rig, nas):
        import json
        _frame(rig, "NGC 6946/a.fits")
        st = PushState()
        st.record(push_once(rig, nas))
        json.dumps(st.payload())


class TestTheTransportOwnsNoRules:
    def test_destination_has_no_diff_logic(self):
        """The moment a direction owns a copy of the reconciliation rules the
        two directions drift and only one gets the next fix.

        Checks CODE, not prose: the first version of this test grepped the whole
        module and failed on its own docstring, which legitimately uses the
        words "settle" and "missing" to explain why they are not implemented
        here. A guard that cannot tell an explanation from an implementation is
        not a guard.
        """
        import ast
        import inspect
        from astrodeck.sync import destination

        tree = ast.parse(inspect.getsource(destination))
        for node in ast.walk(tree):          # strip every docstring
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                                 ast.AsyncFunctionDef)):
                body = node.body
                if body and isinstance(body[0], ast.Expr) \
                        and isinstance(body[0].value, ast.Constant) \
                        and isinstance(body[0].value.value, str):
                    node.body = body[1:] or [ast.Pass()]
        code = ast.unparse(tree)

        # A comparison of hashes, a settle-window decision, or its own diff would
        # each mean the transport had started deciding WHAT to send.
        for banned in ("sha256 !=", "sha256 ==", "def diff", "is_final",
                       "settle_s=DEFAULT", "unlink(target"):
            assert banned not in code, (
                f"destination.py implements reconciliation ({banned!r}) — it "
                f"must only list what it holds and accept bytes")
        # And the positive half: it reconciles by CALLING the shared builder.
        assert "_manifest.build(" in code, \
            "destination stopped using the shared manifest builder"

    def test_push_reuses_the_shared_diff(self):
        import inspect
        from astrodeck.sync import push
        src = inspect.getsource(push.push_once)
        assert "_manifest.diff(" in src, "push grew its own comparison"
        assert "_manifest.build(" in src, "push grew its own manifest builder"
