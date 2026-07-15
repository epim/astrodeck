"""Task 2: Session entity + SessionStore (sessions spec §2).

Round-trip + effective-acceptance + done_map/remaining, prune policy (never
prunes dormant/active), boot sweep, and the one-shot legacy resume migration."""
import json
import time
from pathlib import Path

import pytest

import astrodeck.hub as hub_module
from astrodeck.persist import write_json_atomic
from astrodeck.sequence.models import ExposureStep, SequencePlan, Target
from astrodeck.sequence import session as session_mod
from astrodeck.sequence.session import (Session, SessionFrame,
                                        migrate_legacy_resume, session_store)


@pytest.fixture(autouse=True)
def capture_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path)
    return tmp_path


def _plan(counts=(3,)) -> SequencePlan:
    return SequencePlan(name="P", targets=[Target(
        name="M42", ra_hours=5.5881, dec_deg=-5.3911,
        steps=[ExposureStep(exposure_s=60, count=c) for c in counts])])


def _frame(plan: SequencePlan, *, accepted=True, override=None) -> SessionFrame:
    return SessionFrame(ts=time.time(), night="n1",
                        target_id=plan.targets[0].id,
                        step_id=plan.targets[0].steps[0].id,
                        path="/tmp/f.fits", metrics={"hfr": 2.1},
                        auto_accepted=accepted, override=override)


def test_round_trip_and_effective_acceptance():
    plan = _plan()
    s = Session(name="P", created_ts=1.0, status="dormant", plan=plan)
    s.frames = [_frame(plan), _frame(plan, accepted=False),
                _frame(plan, accepted=False, override="accept"),
                _frame(plan, accepted=True, override="reject")]
    session_store.save(s)
    loaded = session_store.load(s.id)
    assert loaded.name == "P" and loaded.status == "dormant"
    assert len(loaded.frames) == 4
    assert loaded.plan.targets[0].id == plan.targets[0].id
    # effective = override if set else auto_accepted
    assert [f.effective() for f in loaded.frames] == [True, False, True, False]
    sid = plan.targets[0].steps[0].id
    assert loaded.accepted(sid) == 2
    assert loaded.total_accepted() == 2
    # attempts mode (default): remaining/done count every RECORDED frame
    assert loaded.remaining() == {sid: 0}            # 4 recorded >= count 3
    tid = plan.targets[0].id
    assert loaded.done_map() == {f"{tid}:{sid}": 3}  # capped at step count


def test_load_unknown_or_hostile_id_raises_keyerror():
    with pytest.raises(KeyError):
        session_store.load("nope")
    with pytest.raises(KeyError):
        session_store.load("../escape")


def test_prune_only_complete_or_abandoned_oldest_first(monkeypatch):
    monkeypatch.setattr(session_mod, "MAX_SESSIONS", 3)
    ids = {}
    for i, status in enumerate(["complete", "abandoned", "dormant"]):
        s = Session(name=f"s{i}", created_ts=float(i), status=status, plan=_plan())
        session_store.save(s)
        s.updated_ts = float(i)                       # pin a deterministic age
        write_json_atomic(session_mod._sessions_dir() / f"{s.id}.json",
                          s.model_dump(), backup=False)
        ids[status] = s.id
    newest = Session(name="new", created_ts=99.0, status="active", plan=_plan())
    session_store.save(newest)                        # 4th file -> prune 1
    left = {s.id for s in session_store.load_all()}
    assert ids["complete"] not in left                # oldest prunable went first
    assert ids["abandoned"] in left and ids["dormant"] in left and newest.id in left


def test_boot_sweep_moves_active_to_dormant():
    a = Session(name="a", created_ts=1.0, status="active", plan=_plan())
    d = Session(name="d", created_ts=1.0, status="complete", plan=_plan())
    session_store.save(a)
    session_store.save(d)
    assert session_store.boot_sweep() == 1
    assert session_store.load(a.id).status == "dormant"
    assert session_store.load(d.id).status == "complete"


def test_recoverable_needs_dormant_with_frames():
    empty = Session(name="e", created_ts=1.0, status="dormant", plan=_plan())
    session_store.save(empty)
    assert session_store.recoverable() is None        # no frames -> not recoverable
    plan = _plan()
    s = Session(name="r", created_ts=1.0, status="dormant", plan=plan)
    s.frames = [_frame(plan)]
    session_store.save(s)
    assert session_store.recoverable().id == s.id


def test_legacy_resume_migration_positional_to_ids(capture_dir):
    legacy = {"plan": {"name": "L", "targets": [
                  {"name": "A", "ra_hours": 1.0, "dec_deg": 1.0,
                   "steps": [{"exposure_s": 60, "count": 5},
                             {"exposure_s": 30, "count": 4}]},
                  {"name": "B", "ra_hours": 2.0, "dec_deg": 2.0,
                   "steps": [{"exposure_s": 10, "count": 2}]}]},
              "done": {"0:0": 3, "0:1": 1, "1:0": 2},
              "ts": 123.0, "report_id": "L-20260701-010101"}
    path = capture_dir / ".sequence_resume.json"
    path.write_text(json.dumps(legacy), encoding="utf-8")
    s = migrate_legacy_resume()
    assert s is not None and s.status == "dormant"
    assert not path.exists()                          # one-shot: file deleted
    assert s.nights == ["L-20260701-010101"]
    # positional counts landed on the backfilled ids, by position
    t0, t1 = s.plan.targets
    dm = s.done_map()
    assert dm[f"{t0.id}:{t0.steps[0].id}"] == 3
    assert dm[f"{t0.id}:{t0.steps[1].id}"] == 1
    assert dm[f"{t1.id}:{t1.steps[0].id}"] == 2
    assert len(s.frames) == 6                         # 3+1+2 synthesized frames
    assert migrate_legacy_resume() is None            # second call: nothing left


def test_legacy_resume_migration_bad_count_skipped_no_raise(capture_dir):
    """A non-numeric ``done`` count must never raise (review finding #1):
    the bad entry is skipped, the rest of the migration proceeds, and the
    legacy file is still deleted."""
    legacy = {"plan": {"name": "L", "targets": [
                  {"name": "A", "ra_hours": 1.0, "dec_deg": 1.0,
                   "steps": [{"exposure_s": 60, "count": 5},
                             {"exposure_s": 30, "count": 4}]}]},
              "done": {"0:0": "not-an-int", "0:1": 2},
              "ts": 123.0, "report_id": "L-20260701-010101"}
    path = capture_dir / ".sequence_resume.json"
    path.write_text(json.dumps(legacy), encoding="utf-8")
    s = migrate_legacy_resume()                       # must not raise
    assert s is not None and s.status == "dormant"
    assert not path.exists()                           # deleted despite the bad count
    t0 = s.plan.targets[0]
    dm = s.done_map()
    assert dm[f"{t0.id}:{t0.steps[0].id}"] == 0        # bad entry skipped -> 0 synthesized
    assert dm[f"{t0.id}:{t0.steps[1].id}"] == 2        # good entry still processed
    assert len(s.frames) == 2


def test_legacy_resume_migration_bad_ts_falls_back_to_now(capture_dir):
    """A non-numeric top-level ``ts`` must never raise (review finding #1):
    ``created_ts`` falls back to ``now()`` and the file is still deleted."""
    legacy = {"plan": {"name": "L", "targets": [
                  {"name": "A", "ra_hours": 1.0, "dec_deg": 1.0,
                   "steps": [{"exposure_s": 60, "count": 5}]}]},
              "done": {"0:0": 2},
              "ts": "not-a-float", "report_id": "L-20260701-010101"}
    path = capture_dir / ".sequence_resume.json"
    path.write_text(json.dumps(legacy), encoding="utf-8")
    before = time.time()
    s = migrate_legacy_resume()                       # must not raise
    after = time.time()
    assert s is not None
    assert not path.exists()
    assert before - 1 <= s.created_ts <= after + 1     # fell back to now()


def test_legacy_resume_migration_bad_json_unlink_oserror_swallowed(capture_dir, monkeypatch):
    """Review finding #2: the early-exit unlink on unparseable JSON must be
    guarded like the success-path unlink — a locked/AV-held file must not
    propagate an OSError."""
    path = capture_dir / ".sequence_resume.json"
    path.write_text("{not valid json", encoding="utf-8")

    def _boom(self, missing_ok=False):
        raise OSError("locked")
    monkeypatch.setattr(Path, "unlink", _boom)
    assert migrate_legacy_resume() is None             # must not raise


def test_legacy_resume_migration_bad_plan_unlink_oserror_swallowed(capture_dir, monkeypatch):
    """Review finding #2: the early-exit unlink on an unparseable ``plan``
    must be guarded like the success-path unlink."""
    legacy = {"plan": {"targets": "not-a-list"}, "done": {}, "ts": 1.0}
    path = capture_dir / ".sequence_resume.json"
    path.write_text(json.dumps(legacy), encoding="utf-8")

    def _boom(self, missing_ok=False):
        raise OSError("locked")
    monkeypatch.setattr(Path, "unlink", _boom)
    assert migrate_legacy_resume() is None             # must not raise
