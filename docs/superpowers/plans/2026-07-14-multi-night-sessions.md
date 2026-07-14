# Multi-Night Sessions Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Multi-night imaging sessions per the approved spec (`docs/superpowers/specs/2026-07-14-multi-night-sessions-design.md`): stable target/step IDs, a first-class uuid-keyed `Session` entity with a per-frame ledger that replaces the single-slot `.sequence_resume.json`, an accepted-frame quota mode with quality gates and runaway-reject guards, graceful id-keyed resume with per-night reports, thumbnails, a full `/api/sessions` surface with RBAC + path redaction, an opt-in auto-resume-at-dusk service, and the UI: quota plan settings, Sessions cards, a review drawer, and a plan-library panel.

**Architecture:** The server gains `sequence/session.py` (SessionFrame/Session models + a `PlanLibrary`-style `SessionStore` under `CAPTURE_DIR/sessions/`) and `sequence/resume_arm.py` (a lifespan asyncio service). `SequenceEngine.start()` owns Session lifecycle — every start creates or re-opens a session, `_record_frame` writes the ledger atomically per frame, and terminal transitions set `complete`/`dormant`. The UI keys everything off the new stable ids: a central `ensurePlanIds` backfill in the store plus explicit `uid()` at every create path, with new SequenceView panels (Sessions, Review drawer, Plan library) built on existing Panel/Toggle/confirmDialog/LogDrawer patterns.

**Tech Stack:** FastAPI + pydantic v2 + pytest(-asyncio, real sim `Hub`) on the server; React + zustand + Tailwind tokens + hand-rolled `npx tsx` test harness on the UI. No new dependencies.

## Global Constraints

- Work on `main` directly (project convention). Conventional commits. Do NOT push. EVERY commit message ends with:
  ```
  Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01M6wy4Lkm8yAoZWJWiyv6FL
  ```
- Server tests: from `server/`, `./.venv/Scripts/python.exe -m pytest -q` (baseline: 980 passed). Windows environment — paths use backslashes in shells but pathlib in code.
- Server test conventions: NO FakeHub mock — sequence tests use the real simulator `Hub().connect_sim()` with `CAPTURE_DIR`/`config_store` monkeypatched, polled via the existing bounded `wait_for(predicate, timeout)` helper (pattern: `server/tests/test_sequence.py:11-50`).
- UI tests: self-executing `npx tsx src/lib/__tests__/<name>.test.ts` from `ui/` (assert helper + PASS lines + `process.exit(1)`, pattern: `ui/src/lib/__tests__/planGroups.test.ts`). NEVER run by CI — implementers run them manually. `caps.test.ts` is known-broken standalone ("window is not defined") — ignore it.
- UI build: `npm run build` from `ui/` = `tsc -b && vite build`; strict `noUnusedLocals`/`noUnusedParameters`; `resolveJsonModule` OFF.
- Night-mode safety: UI uses existing tokens/classes only (`var(--...)` tokens, existing Tailwind classes: `text-accent`, `text-dim`, `text-warn`, `text-bad`, `text-good`, `border-line`, `bg-raise`, `bg-bg/50`, etc.); no new colors.
- RBAC: mutating session routes use `CAP_CONTROL_MOUNT` (`"control.mount"` — the same cap gating `POST /api/sequence/start`); reads use `CAP_VIEW_STATUS`; thumbs use `CAP_VIEW_PREVIEW`. Route pattern: `dependencies=[Depends(require(CAP))]` + `@declare(CAP)`.
- Persistence: ALL disk writes via `persist.write_json_atomic`; ids validated via `persist.safe_id_path`; sessions live under `CAPTURE_DIR/sessions/`.
- Do NOT touch: relay/site redaction beyond the frame-path strip (sub-project B), weather (C), `catalog/`, `native/`, tile-engine files.
- The spec is the requirements authority. Where the spec references UI elements that don't exist (plan library UI has ZERO client consumers today), this plan builds the minimal version described in its tasks.
- Do NOT edit `.superpowers/sdd/progress.md` — the controller owns the ledger.
- Harness note: long test runs sometimes get auto-backgrounded; re-run immediately and stay foreground. Never park waiting for a notification.

---

## File Structure

**Server — created**
| File | Responsibility |
|---|---|
| `server/astrodeck/sequence/session.py` | SessionFrame/Session models, SessionStore (list/load/save/delete/prune/boot_sweep/recoverable/armed), legacy resume migration, `session_store` singleton |
| `server/astrodeck/sequence/resume_arm.py` | ResumeArm asyncio service: 60s cadence, window-open check, retry/give-up, `resume_veto()` hook |
| `server/tests/test_session_ids.py` | Task 1 tests (id backfill/preserve) |
| `server/tests/test_session_store.py` | Task 2 tests (round-trip, prune, sweep, migration) |
| `server/tests/test_session_engine.py` | Task 3 tests (ledger, dormancy, recover routes) |
| `server/tests/test_session_quota.py` | Task 4 tests (gates, quota loop, guards, progress) |
| `server/tests/test_session_resume.py` | Task 5 tests (reorder resume, dawn-dormant resume, report-id mint) |
| `server/tests/test_session_thumbs.py` | Task 6 test (sim-frame thumbnail) |
| `server/tests/test_sessions_api.py` | Task 7 tests (routes, RBAC, redaction, id-merge, 409s) |
| `server/tests/test_resume_arm.py` | Task 8 tests (injected clock, retry, give-up, disarm) |

**Server — modified**
| File | Change |
|---|---|
| `server/astrodeck/sequence/models.py` | `id` on ExposureStep/Target (T1); quota fields on SequencePlan (T4) |
| `server/astrodeck/sequence/engine.py` | Session lifecycle in `start`/`_record_frame`/`_finalize_report`, retire resume file, id-keyed `_done` (T3); quota loop + gates + guards (T4); thumbs (T6); WS `session` sub-state (T7) |
| `server/astrodeck/api/app.py` | recover routes re-backed on the store + boot sweep/migration in lifespan (T3); `/api/sessions` routes (T7); ResumeArm wiring (T8) |
| `server/astrodeck/api/redact.py` | `_redact_session_for` frame-path strip (T7) |
| `server/tests/test_sequence.py` | `test_resume_after_abort` rewritten onto the session store (T3) |

**UI — created**
| File | Responsibility |
|---|---|
| `ui/src/lib/ids.ts` | `uid()`, `ensureStepId`, `ensureTargetIds`, `ensurePlanIds` |
| `ui/src/api/sessions.ts` | Typed wrappers for the `/api/sessions` surface |
| `ui/src/lib/sessions.ts` | `targetProgress`, `mergePreview` (pure card helpers) |
| `ui/src/lib/sessionReview.ts` | Verdicts, frame filtering, selection reducer, local override apply |
| `ui/src/lib/planFile.ts` | `parsePlanFile` import-file validation |
| `ui/src/components/sequence/SessionsPanel.tsx` | Session cards: resume / update-from-plan / auto-resume / delete (+ review from T12) |
| `ui/src/components/sequence/SessionReviewDrawer.tsx` | LogDrawer-pattern frame-grid review drawer |
| `ui/src/components/sequence/PlanLibraryPanel.tsx` | Save/load/delete/export/import saved plans |
| `ui/src/lib/__tests__/ids.test.ts`, `sessions.test.ts`, `sessionReview.test.ts`, `planFile.test.ts` | tsx tests |

**UI — modified**
| File | Change |
|---|---|
| `ui/src/types.ts` | `id?` on Target/ExposureStep, quota plan fields, Session/SessionFrame/SessionRow, `SequenceState.session`, `"quality"` end_reason (T9) |
| `ui/src/store.ts` | `ensurePlanIds` in `loadPlan`/`setPlan`; `defaultPlan()` mirrors new server defaults (T9) |
| `ui/src/views/SequenceView.tsx` | Quota advanced row, `uid()` at create paths, mounts SessionsPanel/PlanLibraryPanel (T10/T11/T13) |
| `ui/src/views/AtlasView.tsx` | `uid()` in `panelsToTargets` (T10) |
| `ui/src/lib/planGroups.ts` + its test | Fresh step ids on mosaic clones (T10) |

---

### Task 1: Server stable IDs on Target/ExposureStep

**Files:**
- Modify: `server/astrodeck/sequence/models.py` (ExposureStep :16-23, Target :45-57, imports :1-13)
- Test: `server/tests/test_session_ids.py` (create)

**Interfaces:**
- Consumes: nothing new.
- Produces: `ExposureStep.id: str` and `Target.id: str`, both `Field(default_factory=lambda: uuid4().hex)` — every later task keys sessions off these.

- [ ] **Step 1: Write the failing test**

Create `server/tests/test_session_ids.py`:

```python
"""Task 1: stable ids on Target/ExposureStep (sessions spec §1).

Pydantic backfills a uuid4 hex on every validation of an id-less plan (the
server-side backfill seam engine.start relies on); provided ids round-trip."""
from astrodeck.sequence.models import SequencePlan


def _plan_dict() -> dict:
    return {"name": "P", "targets": [{
        "name": "M42", "ra_hours": 5.5881, "dec_deg": -5.3911,
        "steps": [{"exposure_s": 60, "count": 3}],
    }]}


def test_idless_plan_gets_unique_backfilled_ids():
    a = SequencePlan.model_validate(_plan_dict())
    b = SequencePlan.model_validate(_plan_dict())
    assert a.targets[0].id and a.targets[0].steps[0].id
    assert len(a.targets[0].id) == 32          # uuid4().hex
    assert a.targets[0].id != b.targets[0].id  # fresh per validation
    assert a.targets[0].steps[0].id != b.targets[0].steps[0].id


def test_provided_ids_are_preserved():
    d = _plan_dict()
    d["targets"][0]["id"] = "tfixed"
    d["targets"][0]["steps"][0]["id"] = "sfixed"
    p = SequencePlan.model_validate(d)
    assert p.targets[0].id == "tfixed"
    assert p.targets[0].steps[0].id == "sfixed"


def test_model_dump_round_trip_keeps_ids():
    p = SequencePlan.model_validate(_plan_dict())
    q = SequencePlan.model_validate(p.model_dump())
    assert q.targets[0].id == p.targets[0].id
    assert q.targets[0].steps[0].id == p.targets[0].steps[0].id
```

- [ ] **Step 2: Run test to verify it fails**

```
cd server && ./.venv/Scripts/python.exe -m pytest -q tests/test_session_ids.py
```
Expected: 3 failures — `AttributeError`/`ValidationError`: `id` is not a field on `Target`/`ExposureStep` (pydantic v2 ignores the extra `"id"` key, so `p.targets[0].id` raises `AttributeError`).

- [ ] **Step 3: Implement**

In `server/astrodeck/sequence/models.py`, add the import and the two fields.

Replace the import block (line 13):
```python
from pydantic import BaseModel, Field
```
with:
```python
from uuid import uuid4

from pydantic import BaseModel, Field
```

Add as the FIRST field of `ExposureStep` (before `filter`, line 17):
```python
class ExposureStep(BaseModel):
    # stable identity for multi-night session ledgers (sessions spec §1).
    # Backfilled on every validation so an id-less legacy plan is never rejected.
    id: str = Field(default_factory=lambda: uuid4().hex)
    filter: str | None = None          # filter name, None = don't move wheel
```

Add as the FIRST field of `Target` (before `name`, line 46):
```python
class Target(BaseModel):
    # stable identity for multi-night session ledgers (sessions spec §1).
    id: str = Field(default_factory=lambda: uuid4().hex)
    name: str
```

- [ ] **Step 4: Run tests to verify pass**

```
cd server && ./.venv/Scripts/python.exe -m pytest -q tests/test_session_ids.py tests/test_sequence.py tests/test_plans.py
```
Expected: all pass (new 3 + existing sequence/plan suites unaffected — ids are additive defaults). Then the full suite:
```
cd server && ./.venv/Scripts/python.exe -m pytest -q
```
Expected: `983 passed` (980 baseline + 3), 0 failures.

- [ ] **Step 5: Commit**

```
git add server/astrodeck/sequence/models.py server/tests/test_session_ids.py
git commit -m "$(cat <<'EOF'
feat(sessions): stable uuid ids on Target/ExposureStep (spec §1)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01M6wy4Lkm8yAoZWJWiyv6FL
EOF
)"
```

---
### Task 2: Session models + SessionStore + boot sweep + legacy migration

**Files:**
- Create: `server/astrodeck/sequence/session.py`
- Test: `server/tests/test_session_store.py` (create)

**Interfaces:**
- Consumes: `Target.id`/`ExposureStep.id` (Task 1); `persist.write_json_atomic/safe_id_path/read_json_or/list_json`; `hub.CAPTURE_DIR` (lazily, via module attribute so test monkeypatching works — same trick as `engine._resume_file()`).
- Produces (all later server tasks depend on these EXACT names):
  - `SessionFrame(id, ts, night, target_id, step_id, path, thumb, metrics, auto_accepted, override)` + `effective() -> bool`
  - `Session(id, schema_version, name, created_ts, updated_ts, status, plan, nights, frames, auto_resume)` + `accepted_by_step() -> dict[str, int]`, `recorded_by_step() -> dict[str, int]`, `accepted(step_id: str) -> int`, `total_accepted() -> int`, `remaining() -> dict[str, int]`, `done_map() -> dict[str, int]` (keys `"<target_id>:<step_id>"`, mode-aware via `plan.count_mode`)
  - `SessionStore.load(session_id) -> Session` (raises `KeyError`), `.load_all() -> list[Session]`, `.save(session) -> None`, `.delete(session_id) -> None`, `.thumbs_dir(session_id) -> Path`, `.list() -> list[dict]`, `.boot_sweep() -> int`, `.recoverable() -> Session | None`, `.armed() -> Session | None`
  - `migrate_legacy_resume() -> Session | None`; `session_store: SessionStore` singleton; `MAX_SESSIONS = 200`; `_sessions_dir() -> Path`

- [ ] **Step 1: Write the failing test**

Create `server/tests/test_session_store.py`:

```python
"""Task 2: Session entity + SessionStore (sessions spec §2).

Round-trip + effective-acceptance + done_map/remaining, prune policy (never
prunes dormant/active), boot sweep, and the one-shot legacy resume migration."""
import json
import time

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
```

- [ ] **Step 2: Run test to verify it fails**

```
cd server && ./.venv/Scripts/python.exe -m pytest -q tests/test_session_store.py
```
Expected: collection error — `ModuleNotFoundError: No module named 'astrodeck.sequence.session'`.

- [ ] **Step 3: Implement**

Create `server/astrodeck/sequence/session.py`:

```python
"""Multi-night Session entity + store (sessions spec §2).

One JSON file per session under ``CAPTURE_DIR/sessions/<id>.json`` (written
atomically, no ``.bak`` — the file churns every frame like the retired resume
file); thumbnails under ``CAPTURE_DIR/sessions/<id>/thumbs/<frame_id>.jpg``.
``SessionStore`` mirrors ``plans.PlanLibrary``: ``safe_id_path`` escape guard,
soft quota with oldest complete/abandoned pruned first (dormant/active NEVER
pruned). ``migrate_legacy_resume`` folds the retired single-slot
``.sequence_resume.json`` into a dormant Session exactly once at boot.
"""
from __future__ import annotations

import json
import shutil
import time
from pathlib import Path
from uuid import uuid4

from pydantic import BaseModel, Field

from .. import hub as _hubmod
from ..persist import list_json, read_json_or, safe_id_path, write_json_atomic
from .models import SequencePlan

SESSION_SCHEMA = 1
MAX_SESSIONS = 200


def _sessions_dir() -> Path:
    # Resolved lazily (module-attribute lookup) so tests that monkeypatch
    # hub.CAPTURE_DIR are honored — same pattern as engine's old _resume_file().
    return _hubmod.CAPTURE_DIR / "sessions"


class SessionFrame(BaseModel):
    """One ledger entry (spec §2). ``metrics`` is an open float dict so a later
    PixInsight integration adds keys without a schema migration (spec §10)."""
    id: str = Field(default_factory=lambda: uuid4().hex)
    ts: float = 0.0
    night: str = ""                 # report_id this frame was captured under
    target_id: str = ""
    step_id: str = ""
    path: str = ""                  # saved FITS path ("" if unsaved)
    thumb: str | None = None        # relative thumb path under the session dir
    metrics: dict[str, float] = Field(default_factory=dict)
    auto_accepted: bool = True
    override: str | None = None     # "accept" | "reject" | None

    def effective(self) -> bool:
        """Effective acceptance = override if set else auto_accepted."""
        if self.override is not None:
            return self.override == "accept"
        return self.auto_accepted


class Session(BaseModel):
    id: str = Field(default_factory=lambda: uuid4().hex)
    schema_version: int = SESSION_SCHEMA
    name: str = ""                  # defaults to plan.name (set by the engine)
    created_ts: float = 0.0
    updated_ts: float = 0.0
    status: str = "active"          # active | dormant | complete | abandoned
    plan: SequencePlan = Field(default_factory=SequencePlan)  # frozen snapshot WITH ids
    nights: list[str] = Field(default_factory=list)           # report ids, in order
    frames: list[SessionFrame] = Field(default_factory=list)
    auto_resume: bool = False

    # ---- derived helpers (mode-aware per the FROZEN plan's count_mode) -------
    def accepted_by_step(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for f in self.frames:
            if f.effective():
                out[f.step_id] = out.get(f.step_id, 0) + 1
        return out

    def recorded_by_step(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for f in self.frames:
            out[f.step_id] = out.get(f.step_id, 0) + 1
        return out

    def accepted(self, step_id: str) -> int:
        return self.accepted_by_step().get(step_id, 0)

    def total_accepted(self) -> int:
        return sum(self.accepted_by_step().values())

    def _counts(self) -> dict[str, int]:
        # getattr default keeps this module valid before Task 4 adds count_mode.
        mode = getattr(self.plan, "count_mode", "attempts")
        return self.accepted_by_step() if mode == "accepted" else self.recorded_by_step()

    def remaining(self) -> dict[str, int]:
        """Per-step frames still owed (mode-aware; floored at 0)."""
        counts = self._counts()
        out: dict[str, int] = {}
        for t in self.plan.targets:
            for s in t.steps:
                out[s.id] = max(0, s.count - counts.get(s.id, 0))
        return out

    def done_map(self) -> dict[str, int]:
        """Engine seeding map ``"<target_id>:<step_id>" -> completed`` (mode-
        aware), capped at the step count so a resumed loop never starts past
        its range."""
        counts = self._counts()
        out: dict[str, int] = {}
        for t in self.plan.targets:
            for s in t.steps:
                out[f"{t.id}:{s.id}"] = min(s.count, counts.get(s.id, 0))
        return out


class SessionStore:
    """uuid-keyed session store; one ``sessions/<id>.json`` per session
    (mirrors plans.PlanLibrary)."""

    def _path(self, session_id: str) -> Path:
        return safe_id_path(_sessions_dir(), session_id)

    def load(self, session_id: str) -> Session:
        raw = read_json_or(self._path(session_id))
        if not isinstance(raw, dict):
            raise KeyError(session_id)
        return Session.model_validate(raw)

    def load_all(self) -> list[Session]:
        out: list[Session] = []
        for path in list_json(_sessions_dir()):
            raw = read_json_or(path)
            if not isinstance(raw, dict):
                continue
            try:
                out.append(Session.model_validate(raw))
            except Exception:
                continue                      # corrupt file: skip, never raise
        return out

    def save(self, session: Session) -> None:
        """Atomic write, no .bak (churns every frame). A NEW id triggers the
        prune sweep; upserting an existing id never prunes."""
        session.updated_ts = time.time()
        path = self._path(session.id)
        is_new = not path.exists()
        write_json_atomic(path, session.model_dump(), backup=False)
        if is_new:
            self._prune()

    def _prune(self) -> None:
        sessions = self.load_all()
        excess = len(sessions) - MAX_SESSIONS
        if excess <= 0:
            return
        prunable = sorted(
            (s for s in sessions if s.status in ("complete", "abandoned")),
            key=lambda s: s.updated_ts)
        # dormant/active are NEVER pruned — the store may exceed the soft cap
        # in the pathological all-live case (spec §2).
        for s in prunable[:excess]:
            self.delete(s.id)

    def delete(self, session_id: str) -> None:
        """Remove the session file + its thumbs directory. NEVER touches FITS."""
        path = self._path(session_id)          # validates the id (KeyError)
        if path.exists():
            path.unlink()
        side_dir = _sessions_dir() / session_id
        if side_dir.is_dir():
            shutil.rmtree(side_dir, ignore_errors=True)

    def thumbs_dir(self, session_id: str) -> Path:
        self._path(session_id)                 # id validation only (KeyError)
        return _sessions_dir() / session_id / "thumbs"

    def list(self) -> list[dict]:
        """Lightweight rows for GET /api/sessions (spec §6), newest first."""
        rows: list[dict] = []
        for s in self.load_all():
            rows.append({
                "id": s.id, "name": s.name, "status": s.status,
                "created_ts": s.created_ts, "updated_ts": s.updated_ts,
                "nights": len(s.nights), "accepted": s.total_accepted(),
                "total": s.plan.total_frames(), "auto_resume": s.auto_resume,
            })
        rows.sort(key=lambda r: r["updated_ts"], reverse=True)
        return rows

    def boot_sweep(self) -> int:
        """Power-cut orphans: any ``active`` session on disk at boot (the engine
        is never running at boot) -> ``dormant`` (spec §4). Returns the count."""
        n = 0
        for s in self.load_all():
            if s.status == "active":
                s.status = "dormant"
                self.save(s)
                n += 1
        return n

    def recoverable(self) -> Session | None:
        """Most recently updated dormant session WITH frames (recover routes)."""
        dormant = [s for s in self.load_all()
                   if s.status == "dormant" and s.frames]
        dormant.sort(key=lambda s: s.updated_ts, reverse=True)
        return dormant[0] if dormant else None

    def armed(self) -> Session | None:
        """The auto_resume-armed dormant session. PATCH enforces the singleton;
        most-recent wins defensively if files were hand-edited."""
        armed = [s for s in self.load_all()
                 if s.status == "dormant" and s.auto_resume]
        armed.sort(key=lambda s: s.updated_ts, reverse=True)
        return armed[0] if armed else None


session_store = SessionStore()


def migrate_legacy_resume() -> Session | None:
    """One-shot boot migration of the retired ``.sequence_resume.json`` (spec
    §2): positional ``"ti:si"`` counts map onto the ids pydantic backfills
    during plan validation (position-preserving), synthesized as N accepted
    placeholder frames per step so ``done_map()`` seeds a resume at the exact
    same counts. The legacy file is deleted afterward (even when unparseable —
    it is single-slot garbage either way)."""
    legacy = _hubmod.CAPTURE_DIR / ".sequence_resume.json"
    try:
        raw = json.loads(legacy.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        legacy.unlink(missing_ok=True)
        return None
    try:
        plan = SequencePlan.model_validate(raw.get("plan") or {})
    except Exception:
        legacy.unlink(missing_ok=True)
        return None
    report_id = str(raw.get("report_id") or "")
    now = time.time()
    s = Session(name=plan.name or "Tonight",
                created_ts=float(raw.get("ts") or now), updated_ts=now,
                status="dormant", plan=plan,
                nights=[report_id] if report_id else [])
    for key, count in (raw.get("done") or {}).items():
        try:
            ti, si = (int(x) for x in str(key).split(":", 1))
            target = plan.targets[ti]
            step = target.steps[si]
        except (ValueError, IndexError):
            continue
        for _ in range(int(count)):
            s.frames.append(SessionFrame(
                ts=now, night=report_id, target_id=target.id,
                step_id=step.id, path="", auto_accepted=True))
    session_store.save(s)
    try:
        legacy.unlink(missing_ok=True)
    except OSError:
        pass
    return s
```

- [ ] **Step 4: Run tests to verify pass**

```
cd server && ./.venv/Scripts/python.exe -m pytest -q tests/test_session_store.py
```
Expected: `6 passed`. Then full suite:
```
cd server && ./.venv/Scripts/python.exe -m pytest -q
```
Expected: `989 passed`, 0 failures.

- [ ] **Step 5: Commit**

```
git add server/astrodeck/sequence/session.py server/tests/test_session_store.py
git commit -m "$(cat <<'EOF'
feat(sessions): Session/SessionFrame models + SessionStore, boot sweep, legacy resume migration (spec §2)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01M6wy4Lkm8yAoZWJWiyv6FL
EOF
)"
```

---
### Task 3: Engine ledger integration — Session lifecycle, retire the resume file, re-back recover routes

**Files:**
- Modify: `server/astrodeck/sequence/engine.py` (imports :27-45, `_resume_file` :115-116, `__init__` :132-196, `start()` :199-246, `load_resume` :404-412, `_persist`/`_clear_resume` :481-506, `_run` :540-624, `_finalize_report` :626-644, `_target_complete` :786-789, `_run_calibration` :928-956, `_run_step` :958-1018, `_handle_reject` :1045-1110, `_record_frame` :1482-1506)
- Modify: `server/astrodeck/api/app.py` (lifespan :101-199, recoverable/recover routes :2403-2430, imports :42-74)
- Modify: `server/tests/test_sequence.py` (`test_resume_after_abort` :163-182)
- Test: `server/tests/test_session_engine.py` (create)

**Interfaces:**
- Consumes: `Session`, `SessionFrame`, `session_store`, `migrate_legacy_resume` (Task 2); `SessionReporter(plan, *, report_id, started_at)` + `SessionReporter._make_id(plan_name, started_at)` (existing `report.py`).
- Produces (later tasks rely on these EXACT signatures):
  - `SequenceEngine.start(self, plan: SequencePlan, *, session: Session | None = None) -> None` (old `resume_done`/`report_id` params are GONE)
  - `SequenceEngine._session: Session | None` instance attr
  - `SequenceEngine._record_frame(self, key: str, i: int, target: Target, step, info: dict, *, accepted: bool = True) -> None`
  - `SequenceEngine._record_session_frame(self, target: Target, step, info: dict, *, auto_accepted: bool) -> SessionFrame | None`
  - module fn `_mint_report_id(plan_name: str, started_at: float, taken: list[str]) -> str`
  - `_done` keys are now `"<target_id>:<step_id>"` (spec §1)
  - `SequenceEngine.load_resume` / `_persist` / `_clear_resume` / `_resume_file` DELETED
  - Routes: `GET /api/sequence/recoverable` and `POST /api/sequence/recover` re-backed on `session_store.recoverable()` (paths unchanged for UI compatibility)

- [ ] **Step 1: Write the failing test**

Create `server/tests/test_session_engine.py`:

```python
"""Task 3: engine <-> session ledger integration (sessions spec §2/§4).

Real sim hub (repo convention: NO FakeHub), CAPTURE_DIR monkeypatched, state
polled via the bounded wait_for helper."""
import asyncio

import pytest
from fastapi.testclient import TestClient

import astrodeck.api.app as app_module
import astrodeck.hub as hub_module
from astrodeck.hub import Hub
from astrodeck.sequence import SequenceEngine, SequencePlan
from astrodeck.sequence.models import ExposureStep, Target
from astrodeck.sequence.session import Session, SessionFrame, session_store


@pytest.fixture
async def sim_hub(tmp_path, monkeypatch):
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path)
    _safety = hub_module.config_store.cfg().safety
    monkeypatch.setattr(_safety, "solar_avoidance", False)
    h = Hub()
    await h.connect_sim()
    yield h
    await h.disconnect_all()


async def wait_for(predicate, timeout=30.0):
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.1)
    return False


def _plan(count=4) -> SequencePlan:
    return SequencePlan(name="ledger", guide=False, dither_every=0,
                        autofocus_every=0, meridian_flip=False, targets=[Target(
                            name="M42", ra_hours=5.5881, dec_deg=-5.3911,
                            center=False, autofocus_first=False,
                            steps=[ExposureStep(filter="L", exposure_s=0.05,
                                                count=count)])])


async def test_run_produces_session_ledger(sim_hub):
    plan = _plan(3)
    engine = SequenceEngine(sim_hub)
    engine.start(plan)
    sid = engine._session.id
    assert await wait_for(lambda: engine.state.get("state") == "complete")
    s = session_store.load(sid)
    assert s.status == "complete"
    assert len(s.nights) == 1                       # one report for the night
    assert len(s.frames) == 3
    step = plan.targets[0].steps[0]
    assert s.accepted(step.id) == 3
    assert all(f.target_id == plan.targets[0].id for f in s.frames)
    assert all(f.night == s.nights[0] for f in s.frames)
    assert all("hfr" in f.metrics or f.metrics == {} for f in s.frames)
    # the retired single-slot resume file must never come back
    assert not (hub_module.CAPTURE_DIR / ".sequence_resume.json").exists()


async def test_abort_leaves_dormant_session_and_active_on_disk_midrun(sim_hub):
    plan = _plan(6)
    engine = SequenceEngine(sim_hub)
    engine.start(plan)
    sid = engine._session.id
    assert await wait_for(lambda: engine._frames_done >= 2)
    assert session_store.load(sid).status == "active"   # crash would orphan this
    await engine.abort()
    s = session_store.load(sid)
    assert s.status == "dormant"
    assert 1 <= len(s.frames) < 6


async def test_recover_routes_read_session_store(tmp_path, monkeypatch):
    # isolated app (mirrors tests/test_rbac_enforcement.py::_make_client)
    from astrodeck.config import ConfigStore
    import astrodeck.config as config_mod
    temp_store = ConfigStore(path=tmp_path / "astrodeck.json")
    monkeypatch.setattr(config_mod, "config_store", temp_store)
    monkeypatch.setattr(hub_module, "config_store", temp_store)
    monkeypatch.setattr(app_module, "config_store", temp_store)
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path / "captures")
    monkeypatch.delenv(app_module.AUTH_ENV_VAR, raising=False)
    app = app_module.create_app()
    with TestClient(app) as c:
        assert c.get("/api/sequence/recoverable").json() == {"recoverable": False}
        plan = _plan(4)
        s = Session(name="ledger", created_ts=1.0, status="dormant", plan=plan)
        s.frames.append(SessionFrame(ts=1.0, night="n1",
                                     target_id=plan.targets[0].id,
                                     step_id=plan.targets[0].steps[0].id,
                                     auto_accepted=True))
        session_store.save(s)
        r = c.get("/api/sequence/recoverable").json()
        assert r["recoverable"] is True
        assert r["session_id"] == s.id and r["name"] == "ledger"
        assert r["frames_done"] == 1 and r["frames_total"] == 4
        # recover with no camera connected -> DeviceError -> 4xx, session intact
        rr = c.post("/api/sequence/recover")
        assert rr.status_code >= 400
        assert session_store.load(s.id).status == "dormant"
```

- [ ] **Step 2: Run test to verify it fails**

```
cd server && ./.venv/Scripts/python.exe -m pytest -q tests/test_session_engine.py
```
Expected: `test_run_produces_session_ledger` fails with `AttributeError: 'SequenceEngine' object has no attribute '_session'`; the recover-route test fails on `KeyError: 'session_id'` (route still reads the resume file).

- [ ] **Step 3: Implement**

**3a — `server/astrodeck/sequence/engine.py`.**

Add to the imports (after `from .report import FrameRecord, SessionReporter`, line 45):
```python
from .session import Session, SessionFrame, session_store
```

DELETE `_resume_file()` (lines 115-116) and its now-unused reference comment. Add in its place the report-id mint helper (pure — Task 5 unit-tests it):
```python
def _mint_report_id(plan_name: str, started_at: float, taken: list[str]) -> str:
    """Collision-safe per-night report id (spec §4): ``slug-YYYYMMDD-HHMMSS``,
    suffixed ``-2``/``-3``/... when this session already minted that id (same
    plan name resumed within the same second)."""
    rid = SessionReporter._make_id(plan_name or "Tonight", started_at)
    if rid not in taken:
        return rid
    n = 2
    while f"{rid}-{n}" in taken:
        n += 1
    return f"{rid}-{n}"
```

In `__init__` (line 146), replace:
```python
        self._done: dict[str, int] = {}   # "ti:si" -> frames completed
```
with:
```python
        self._done: dict[str, int] = {}   # "targetId:stepId" -> frames completed
        self._session: Session | None = None   # live ledger (sessions spec §2)
```

Replace `start()` (lines 199-246) ENTIRELY with:
```python
    def start(self, plan: SequencePlan, *, session: Session | None = None) -> None:
        """Start a run. EVERY start owns a Session (spec §2): a fresh one when
        ``session`` is None (ids were backfilled by pydantic during plan
        validation — the server-side backfill seam), or a re-opened dormant one
        on resume. Resume seeds ``_done`` from the ledger's id-keyed
        ``done_map()`` and appends a FRESH report to ``session.nights`` — one
        immutable report per night (spec §4); windows re-resolve naturally
        because resume is a new run."""
        if self.running:
            raise DeviceError("a sequence is already running")
        self.plan = plan
        resume = session is not None
        if session is None:
            session = Session(name=plan.name or "Tonight",
                              created_ts=time.time(), status="active", plan=plan)
        else:
            session.status = "active"
        self._session = session
        self._done = dict(session.done_map()) if resume else {}
        self._frames_done = sum(self._done.values())
        self._frames_since_dither = 0
        self._frames_since_focus = 0
        self._last_focus_temp = None
        self._recent_hfr = []
        self._rejected = 0
        self._flip_armed = False
        self._paused.set()
        self._started_at = time.time()
        self._paused_accum_s = 0.0
        self._pause_started_at = None
        self._cur_exposure_s = 0.0
        self._frame_started_at = 0.0
        self._last_frame_done = 0.0
        self._frame_had_event = False
        self._active_step = None
        self._overhead_ema = DEFAULT_OVERHEAD_S
        self._overhead_samples = 0
        self._event_costs = {}
        # --- automation / safety run state (snapshot config ONCE at run start) ---
        self._cfg = config_store.cfg()
        rid = _mint_report_id(plan.name or "Tonight", self._started_at,
                              session.nights)
        self.reporter = SessionReporter(plan, report_id=rid,
                                        started_at=self._started_at)
        session.nights.append(self.reporter.id)
        session_store.save(session)
        self._report_finalized = False
        self._unsafe_streak = 0
        self._safe_streak = 0
        self._last_frame_at = self._started_at
        self._progress_expected = False
        self._frozen = {}
        self._retakes_per_target = {}
        self._dawn_cutoff = False
        self._task = asyncio.create_task(self._run())
```

DELETE `load_resume()` (lines 404-412), `_persist()` and `_clear_resume()` (lines 481-506) entirely.

In `_run()` (lines 540-624), remove the three `self._clear_resume()` call sites verbatim (cooling-skip path :404-area `self._clear_resume()` after the skip `_set_state`, dawn path after `bus.log`, complete path after `bus.log`) — no replacement; `_finalize_report` now owns session finalization.

In `_finalize_report()` (:626-644), append this block at the very END of the existing method body (AFTER the existing report-event publish, keeping everything above unchanged — the `_report_finalized` early-return guarantees this runs exactly once per run):
```python
        # ---- session terminal transition (spec §4) ---------------------------
        # 'complete' ONLY when the run finished naturally with no unmet quota
        # (in accepted mode); EVERY other cause — dawn_cutoff / window_closed /
        # max_run (both surface as dawn_cutoff here) / aborted / error / unsafe
        # / cooling_skip / quality — leaves unmet work -> dormant + resumable.
        if self._session is not None:
            quota = getattr(self._session.plan, "count_mode", "attempts") == "accepted"
            unmet = quota and any(v > 0 for v in self._session.remaining().values())
            self._session.status = ("complete"
                                    if reason == "complete" and not unmet
                                    else "dormant")
            try:
                session_store.save(self._session)
            except Exception as e:
                bus.log("warning", f"session save failed: {e}", "sequence")
            self._session = None
```

In `_target_complete()` (:786-789), replace the positional key with the id key:
```python
    def _target_complete(self, ti: int, target: Target) -> bool:
        total = sum(s.count for s in target.steps)
        done = sum(self._done.get(f"{target.id}:{s.id}", 0) for s in target.steps)
        return total > 0 and done >= total
```

In `_run_calibration()` (:938), replace `key = f"{ti}:{si}"` with `key = f"{target.id}:{step.id}"`, and its two record calls (:953, :956) with:
```python
                if accepted:
                    self._record_frame(key, i, target, step, info)
                else:
                    if not await self._handle_reject(info, key, i, target, step):
                        self._record_frame(key, i, target, step, info, accepted=False)
```

In `_run_step()` (:961), replace `key = f"{ti}:{si}"` with `key = f"{target.id}:{step.id}"`, and the record calls at the loop tail (:1012-1018) with:
```python
            accepted = self._check_quality(info)
            self._reporter_record(target, step, info, accepted=accepted)
            if accepted:
                self._record_frame(key, i, target, step, info)
            else:
                # _handle_reject returns True when it consumed the slot (discard /
                # retake-then-discard); False to fall through to a normal record
                # (warn — frame is kept).
                if not await self._handle_reject(info, key, i, target, step):
                    self._record_frame(key, i, target, step, info, accepted=False)
```

In `_handle_reject()` retake-success path (:1056 `self._record_frame(key, i)`), replace with:
```python
            if accepted:
                self._record_frame(key, i, target, step, new_info)
                return True
```

Replace `_record_frame()` (:1482-1506) ENTIRELY with:
```python
    def _record_frame(self, key: str, i: int, target: Target, step, info: dict,
                      *, accepted: bool = True) -> None:
        now = time.time()
        # Per-frame overhead EMA: cadence minus exposure, EXCLUDING any frame that
        # carried a dither/AF/flip (those are accounted analytically, so folding
        # them into the per-frame overhead would double-count and whipsaw the
        # finish clock). α=0.1 keeps one cloud-slowed frame from whipsawing it
        # (spec §5.2).
        if self._last_frame_done > 0 and self._cur_exposure_s > 0 \
                and not getattr(self, "_frame_had_event", False):
            overhead = (now - self._last_frame_done) - self._cur_exposure_s
            if overhead > 0:
                self._overhead_ema = ((1 - OVERHEAD_EMA_ALPHA) * self._overhead_ema
                                      + OVERHEAD_EMA_ALPHA * overhead)
                self._overhead_samples += 1
        # reset the flag only AFTER reading it above, so the dither/AF/flip set
        # earlier this frame is honored for this frame's overhead and cleared for
        # the next (P2-1). Final order: begin → set-flag → capture → read → reset.
        self._frame_had_event = False
        self._last_frame_done = now
        self._last_frame_at = now              # watchdog progress stamp (§1.9-F)
        self._frame_started_at = 0.0   # frame complete — no longer in flight
        self._done[key] = i + 1
        self._frames_done += 1
        # ledger append + atomic session save replaces the retired resume-file
        # _persist (same per-frame write cost — sessions spec §3).
        self._record_session_frame(target, step, info, auto_accepted=accepted)
        self._set_state()

    def _record_session_frame(self, target: Target, step, info: dict,
                              *, auto_accepted: bool) -> SessionFrame | None:
        """Append one ledger entry (metrics: hfr / stars / guide_rms /
        sensor_temp_c — floats only, absent when unmeasured) + save the session
        atomically. Best-effort: a ledger hiccup must never break capture."""
        if self._session is None:
            return None
        metrics: dict[str, float] = {}
        if isinstance(info, dict):
            if info.get("hfr") is not None:
                metrics["hfr"] = float(info["hfr"])
            if info.get("stars") is not None:
                metrics["stars"] = float(info["stars"])
        try:
            if self.hub.guider and self.hub.guider.connected:
                rms = getattr(self.hub.guider.stats(), "rms_total", None)
                if rms is not None:
                    metrics["guide_rms"] = float(rms)
        except Exception:
            pass
        frame = getattr(self.hub, "last_frame", None)
        temp = getattr(frame, "temperature_c", None) if frame is not None else None
        if temp is not None:
            metrics["sensor_temp_c"] = float(temp)
        saved = info.get("saved_path") if isinstance(info, dict) else None
        sf = SessionFrame(ts=time.time(),
                          night=self.reporter.id if self.reporter else "",
                          target_id=target.id, step_id=step.id,
                          path=str(saved) if saved else "", metrics=metrics,
                          auto_accepted=auto_accepted)
        try:
            self._session.frames.append(sf)
            session_store.save(self._session)
        except Exception as e:
            bus.log("warning", f"session ledger write failed: {e}", "sequence")
        return sf
```

Also remove the now-dead `from ..persist import write_json_atomic` import IF nothing else in engine.py uses it (grep first; it was only used by `_persist`).

**3b — `server/astrodeck/api/app.py`.**

Add to imports (near `from ..sequence.report import SessionReporter, _slug`, line 73):
```python
from ..sequence.session import migrate_legacy_resume, session_store
```

In the lifespan `_lifespan` (after the auth-provider install block, before `task = asyncio.create_task(dispatcher.run())`, line ~115), add:
```python
    # Multi-night sessions (spec §2/§4): migrate the retired single-slot resume
    # file ONCE, then sweep power-cut orphans (active -> dormant) so they are
    # manually resumable + ResumeArm-eligible. Never raises out of boot.
    try:
        migrate_legacy_resume()
        swept = session_store.boot_sweep()
        if swept:
            bus.log("info", f"boot sweep: {swept} orphaned session(s) -> dormant",
                    "sequence")
    except Exception as e:  # noqa: BLE001 - degrade, never crash boot
        bus.log("error", f"session boot sweep failed: {e}", "sequence")
```

Replace the recoverable/recover routes (:2403-2430) ENTIRELY with:
```python
    @app.get("/api/sequence/recoverable", dependencies=[Depends(require(CAP_VIEW_STATUS))])
    @declare(CAP_VIEW_STATUS)
    async def sequence_recoverable():
        # Re-backed on the session store (spec §2): a dormant session WITH
        # frames is recoverable. Route path unchanged for UI compatibility.
        s = session_store.recoverable()
        if s is None:
            return {"recoverable": False}
        return {"recoverable": True, "session_id": s.id, "name": s.name,
                "frames_done": sum(s.done_map().values()),
                "frames_total": s.plan.total_frames(), "ts": s.updated_ts}

    @app.post("/api/sequence/recover", dependencies=[Depends(require(CAP_CONTROL_MOUNT))])
    @declare(CAP_CONTROL_MOUNT, reaches={"SequenceEngine.start"})
    async def sequence_recover():
        s = session_store.recoverable()
        if s is None:
            raise HTTPException(404, "no resumable sequence found")
        try:
            hub.require("camera")
            engine.start(s.plan, session=s)
        except DeviceError as e:
            raise _err(e)
        return {"resumed": True,
                "frames_remaining": sum(s.remaining().values())}
```

**3c — `server/tests/test_sequence.py`.** Replace `test_resume_after_abort` (:163-182) with the session-backed version:
```python
async def test_resume_after_abort(sim_hub, tmp_path):
    plan = SequencePlan(name="r", guide=False, dither_every=0, autofocus_every=0,
                        meridian_flip=False, targets=[Target(
                            name="M42", ra_hours=5.5881, dec_deg=-5.3911, center=False,
                            autofocus_first=False,
                            steps=[ExposureStep(filter="L", exposure_s=0.05, count=6)])])
    engine = SequenceEngine(sim_hub)
    engine.start(plan)
    assert await wait_for(lambda: engine._frames_done >= 2)
    await engine.abort()
    from astrodeck.sequence.session import session_store
    s = session_store.recoverable()
    assert s is not None and s.status == "dormant"
    done_before = sum(s.done_map().values())
    assert 1 <= done_before < 6

    engine2 = SequenceEngine(sim_hub)
    engine2.start(s.plan, session=s)
    assert await wait_for(lambda: engine2.state.get("state") == "complete")
    assert engine2._frames_done == 6              # resumed, did not redo all 6
    assert session_store.recoverable() is None    # nothing dormant remains
    assert session_store.load(s.id).status == "complete"
    assert len(session_store.load(s.id).nights) == 2   # one report per night
```

- [ ] **Step 4: Run tests to verify pass**

```
cd server && ./.venv/Scripts/python.exe -m pytest -q tests/test_session_engine.py tests/test_sequence.py tests/test_session_store.py
```
Expected: all pass. Then the FULL suite (any other test that referenced `load_resume`/`_persist` must be fixed the same way — grep says only `test_sequence.py:163-182` did):
```
cd server && ./.venv/Scripts/python.exe -m pytest -q
```
Expected: `992 passed` (989 + 3 new), 0 failures.

- [ ] **Step 5: Commit**

```
git add server/astrodeck/sequence/engine.py server/astrodeck/api/app.py server/tests/test_session_engine.py server/tests/test_sequence.py
git commit -m "$(cat <<'EOF'
feat(sessions): engine session lifecycle + per-frame ledger; retire .sequence_resume.json; recover routes on the session store (spec §2/§4)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01M6wy4Lkm8yAoZWJWiyv6FL
EOF
)"
```

---
### Task 4: Quota engine — count_mode, quality gates, runaway-reject guards

**Files:**
- Modify: `server/astrodeck/sequence/models.py` (SequencePlan :60-83)
- Modify: `server/astrodeck/sequence/engine.py` (exception classes :104-114 area, `__init__`/`start` night-counter, `_run` :540-624 new except arm, `_run_calibration` :950, `_run_step` full rewrite :958-1018, `_check_quality` :1725-1764)
- Test: `server/tests/test_session_quota.py` (create)

**Interfaces:**
- Consumes: `Session.accepted(step_id)`, `_record_session_frame`, `_record_frame(key, i, target, step, info, *, accepted)` (Tasks 2-3).
- Produces:
  - `SequencePlan.count_mode: str = "attempts"`, `.min_stars: int = 0`, `.max_guide_rms: float = 0.0`, `.max_consecutive_rejects: int = 10`, `.max_consecutive_rejects_night: int = 20`
  - `class NightQualityStop(Exception)` (module level, engine.py)
  - `SequenceEngine._check_quality(self, info: dict, *, record: bool = True, calibration: bool = False) -> bool`
  - `SequenceEngine._guide_rms(self) -> float | None`
  - `SequenceEngine._night_rejects: int` instance attr
  - Terminal `end_reason="quality"` on the WS state for the per-night guard

- [ ] **Step 1: Write the failing test**

Create `server/tests/test_session_quota.py`:

```python
"""Task 4: accepted-frame quota engine (sessions spec §3).

Gates tested directly on _check_quality (crafted info dicts + a monkeypatched
_guide_rms); loop/guard behavior tested through real sim-hub runs with a
scripted quality gate (monkeypatching OUR OWN engine method, not the hub)."""
import asyncio
from pathlib import Path

import pytest

import astrodeck.hub as hub_module
from astrodeck.hub import Hub
from astrodeck.sequence import SequenceEngine, SequencePlan
from astrodeck.sequence.models import ExposureStep, Target
from astrodeck.sequence.session import session_store


@pytest.fixture
async def sim_hub(tmp_path, monkeypatch):
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path)
    _safety = hub_module.config_store.cfg().safety
    monkeypatch.setattr(_safety, "solar_avoidance", False)
    h = Hub()
    await h.connect_sim()
    yield h
    await h.disconnect_all()


async def wait_for(predicate, timeout=30.0):
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.1)
    return False


def _plan(count=3, targets=1, **plan_kw) -> SequencePlan:
    return SequencePlan(name="q", guide=False, dither_every=0, autofocus_every=0,
                        meridian_flip=False, count_mode="accepted", **plan_kw,
                        targets=[Target(
                            name=f"T{n}", ra_hours=5.5881, dec_deg=-5.3911,
                            center=False, autofocus_first=False,
                            steps=[ExposureStep(filter="L", exposure_s=0.05,
                                                count=count)])
                            for n in range(targets)])


def _script_gate(monkeypatch, verdicts):
    """Scripted quality gate: pops the next verdict per capture, True after."""
    seq = list(verdicts)

    def fake(self, info, *, record=True, calibration=False):
        return seq.pop(0) if seq else True
    monkeypatch.setattr(SequenceEngine, "_check_quality", fake)


# ---------------------------------------------------------------- gate logic

async def test_quality_gates_and_together(sim_hub, monkeypatch):
    eng = SequenceEngine(sim_hub)
    eng.plan = SequencePlan(name="g", min_stars=50, max_guide_rms=1.5)
    monkeypatch.setattr(eng, "_guide_rms", lambda: None)
    assert eng._check_quality({"hfr": 2.0, "stars": 60}) is True
    assert eng._check_quality({"hfr": 2.0, "stars": 10}) is False   # star floor
    monkeypatch.setattr(eng, "_guide_rms", lambda: 2.0)
    assert eng._check_quality({"hfr": 2.0, "stars": 60}) is False   # RMS ceiling
    monkeypatch.setattr(eng, "_guide_rms", lambda: 1.0)
    assert eng._check_quality({"hfr": 2.0, "stars": 60}) is True
    # calibration frames skip the star/RMS gates entirely
    monkeypatch.setattr(eng, "_guide_rms", lambda: 9.9)
    assert eng._check_quality({"stars": 0}, calibration=True) is True
    # both gates 0 = off (default): nothing rejects
    eng2 = SequenceEngine(sim_hub)
    eng2.plan = SequencePlan(name="off")
    assert eng2._check_quality({"hfr": 9.0, "stars": 0}) is True


async def test_hfr_median_gate_still_works_with_new_gates_off(sim_hub):
    eng = SequenceEngine(sim_hub)
    eng.plan = SequencePlan(name="h", hfr_reject_factor=1.5)
    for _ in range(4):
        assert eng._check_quality({"hfr": 2.0}) is True   # seed the median
    assert eng._check_quality({"hfr": 9.0}) is False      # spike rejected
    assert eng._recent_hfr == [2.0, 2.0, 2.0, 2.0]        # reject NOT folded in


# ------------------------------------------------------------- quota loop

async def test_quota_mode_shoots_until_accepted_and_keeps_rejects(sim_hub, monkeypatch):
    plan = _plan(count=3)
    _script_gate(monkeypatch, [False, False, True, True, True])
    eng = SequenceEngine(sim_hub)
    eng.start(plan)
    sid = eng._session.id
    assert await wait_for(lambda: eng.state.get("state") == "complete")
    s = session_store.load(sid)
    assert s.status == "complete"
    step = plan.targets[0].steps[0]
    assert len(s.frames) == 5                      # 2 rejects + 3 accepted
    assert s.accepted(step.id) == 3
    rejected = [f for f in s.frames if not f.auto_accepted]
    assert len(rejected) == 2
    # accepted mode NEVER unlinks a rejected frame (regrading needs the file)
    for f in rejected:
        if f.path:
            assert Path(f.path).exists()
    # progress counted accepted-only (frames_done advances on accept only)
    assert eng.state["progress"]["frames_done"] == 3
    assert eng.state["progress"]["frames_total"] == 3


async def test_per_step_guard_skips_step_and_leaves_dormant(sim_hub, monkeypatch):
    plan = _plan(count=3, max_consecutive_rejects=2,
                 max_consecutive_rejects_night=0)      # night guard OFF
    _script_gate(monkeypatch, [False] * 50)
    eng = SequenceEngine(sim_hub)
    eng.start(plan)
    sid = eng._session.id
    assert await wait_for(lambda: eng.state.get("state") == "complete")
    s = session_store.load(sid)
    # quota unmet -> dormant even though the scheduler exhausted naturally
    assert s.status == "dormant"
    step = plan.targets[0].steps[0]
    assert s.accepted(step.id) == 0
    assert len(s.frames) == 2                      # guard tripped at 2


async def test_per_night_guard_crosses_targets_sets_quality(sim_hub, monkeypatch):
    plan = _plan(count=2, targets=2, max_consecutive_rejects=2,
                 max_consecutive_rejects_night=3)
    _script_gate(monkeypatch, [False] * 50)
    eng = SequenceEngine(sim_hub)
    eng.start(plan)
    sid = eng._session.id
    assert await wait_for(lambda: eng.state.get("end_reason") == "quality")
    s = session_store.load(sid)
    assert s.status == "dormant"
    # target 1 tripped the per-step guard at 2; target 2's first reject made 3
    # ACROSS the boundary -> night guard -> early dormancy
    assert len(s.frames) == 3


async def test_night_counter_resets_on_accept(sim_hub, monkeypatch):
    plan = _plan(count=2, max_consecutive_rejects=0,
                 max_consecutive_rejects_night=3)
    _script_gate(monkeypatch, [False, False, True, False, False, False])
    eng = SequenceEngine(sim_hub)
    eng.start(plan)
    sid = eng._session.id
    assert await wait_for(lambda: eng.state.get("end_reason") == "quality")
    s = session_store.load(sid)
    # 2 rejects, then an accept RESET the counter, then 3 more tripped it
    assert len(s.frames) == 6
    assert s.total_accepted() == 1


async def test_attempts_mode_unchanged_by_default(sim_hub):
    plan = _plan(count=3)
    plan.count_mode = "attempts"
    eng = SequenceEngine(sim_hub)
    eng.start(plan)
    sid = eng._session.id
    assert await wait_for(lambda: eng.state.get("state") == "complete")
    s = session_store.load(sid)
    assert s.status == "complete"
    assert len(s.frames) == 3                      # exactly count attempts
```

- [ ] **Step 2: Run test to verify it fails**

```
cd server && ./.venv/Scripts/python.exe -m pytest -q tests/test_session_quota.py
```
Expected: immediate failures — `ValidationError`/`TypeError`: `SequencePlan` has no field `count_mode`/`min_stars`; gate tests fail with `TypeError: _check_quality() got an unexpected keyword argument 'calibration'`.

- [ ] **Step 3: Implement**

**4a — `server/astrodeck/sequence/models.py`.** Add to `SequencePlan`, after `hfr_reject_factor` (line 76):
```python
    # --- multi-night quota mode (sessions spec §3; defaults preserve behavior) ---
    count_mode: str = "attempts"             # "attempts" | "accepted"
    min_stars: int = 0                       # star-count floor (0 = off)
    max_guide_rms: float = 0.0               # guide-RMS ceiling, arcsec (0 = off)
    max_consecutive_rejects: int = 10        # per-STEP consecutive guard (0 = off)
    max_consecutive_rejects_night: int = 20  # per-NIGHT guard, crosses targets (0 = off)
```

**4b — `server/astrodeck/sequence/engine.py`.**

Add after the `StopTarget` class (:111-114):
```python
class NightQualityStop(Exception):
    """Per-night consecutive-reject guard tripped (spec §3): end the night
    early -> session dormant, ``end_reason="quality"``. Caught in ``_run`` as
    its own terminal arm (like SafetyAbort) — never treated as an error."""
```

In `__init__`, after `self._rejected = 0` (:137), add:
```python
        self._night_rejects = 0   # per-night consecutive-reject counter (spec §3)
```
In `start()`, after `self._rejected = 0`, add:
```python
        self._night_rejects = 0
```

In `_run_calibration()` (:950), change the gate call to skip the Light-frame gates:
```python
                accepted = self._check_quality(info, calibration=True)
```

Replace `_check_quality()` (:1725-1764) ENTIRELY with:
```python
    def _guide_rms(self) -> float | None:
        """Current total guide RMS (arcsec) or None when unguided/unreadable."""
        try:
            if self.hub.guider and self.hub.guider.connected:
                rms = getattr(self.hub.guider.stats(), "rms_total", None)
                return None if rms is None else float(rms)
        except Exception:
            pass
        return None

    def _check_quality(self, info: dict, *, record: bool = True,
                       calibration: bool = False) -> bool:
        """Return whether a frame is ACCEPTED per the auto gate (spec §3): the
        existing HFR running-median factor AND an optional star floor
        (``min_stars``) AND an optional guide-RMS ceiling (``max_guide_rms``) —
        all three AND together; 0 disables each. Star/RMS gates skip
        calibration frames (darks/bias/flats have no stars and no guiding).

        Preserves the legacy HFR logic exactly: gate against the median of the
        ACCEPTED window only, fold this frame's HFR in only when accepted and
        ``record`` (a rejected/cloudy HFR must never drift the median).
        ``record=False`` is a pure read-only check."""
        plan = self.plan
        factor = plan.hfr_reject_factor
        hfr = info.get("hfr") if isinstance(info, dict) else None
        accepted = True
        if factor and hfr is not None:
            window = self._recent_hfr
            if len(window) >= 4:
                med = median(window)
                if med > 0 and hfr > med * factor:
                    self._rejected += 1
                    bus.log("warning", f"frame HFR {hfr:.2f} >> median {med:.2f} — "
                                       "possible cloud / poor frame", "sequence")
                    accepted = False
        if accepted and not calibration and plan.min_stars > 0:
            stars = info.get("stars") if isinstance(info, dict) else None
            if stars is not None and int(stars) < plan.min_stars:
                self._rejected += 1
                bus.log("warning", f"frame stars {int(stars)} below floor "
                                   f"{plan.min_stars}", "sequence")
                accepted = False
        if accepted and not calibration and plan.max_guide_rms > 0:
            rms = self._guide_rms()
            if rms is not None and rms > plan.max_guide_rms:
                self._rejected += 1
                bus.log("warning", f'guide RMS {rms:.2f}" above ceiling '
                                   f'{plan.max_guide_rms:.2f}"', "sequence")
                accepted = False
        if accepted and record and factor and hfr is not None:
            self._recent_hfr.append(float(hfr))
            self._recent_hfr = self._recent_hfr[-12:]
        return accepted
```

Replace `_run_step()` (:958-1018) ENTIRELY with:
```python
    async def _run_step(self, ti: int, si: int, target: Target, step) -> None:
        plan = self.plan
        assert plan is not None
        key = f"{target.id}:{step.id}"
        # accepted-frame quota mode (spec §3): the predicate is the LEDGER's
        # effective-accepted count, not the attempt index. Attempts are
        # unbounded within the night; window/dawn/max_run still end it.
        quota = plan.count_mode == "accepted" and not target.calibration

        def _quota_met() -> bool:
            return (self._session is not None
                    and self._session.accepted(step.id) >= step.count)

        if quota:
            if _quota_met():
                return
        elif self._done.get(key, 0) >= step.count:
            return
        await self._apply_filter(step)

        step_rejects = 0                 # per-step consecutive guard (spec §3)
        i = self._done.get(key, 0)
        while (not _quota_met()) if quota else (i < step.count):
            await self._checkpoint()
            # stop the target the instant its FROZEN window closes (dawn / stop_mode
            # time / max_run_min) — checked between frames so the in-flight exposure
            # finishes but no new one starts into daylight (§1.6). Raises StopTarget
            # → the scheduler skips ahead / finalizes the night at dawn.
            self._enforce_stop_boundary(target)
            await self._safety_gate(context="frame", target=target)
            # §1.9-F: ping the external dead-man's-switch + heartbeat each frame.
            await self._frame_alerts_tick()
            await self._maybe_meridian_flip(target, step.exposure_s)
            await self._maybe_recover_guiding()

            if plan.dither_every and self._frames_since_dither >= plan.dither_every \
                    and self.hub.guider and self.hub.guider.connected:
                self._set_state(detail="dithering")
                _t0 = time.time()
                try:
                    await _bounded(self.hub.guider.dither(plan.dither_pixels),
                                   GUIDE_OP_TIMEOUT_S, "dither")
                    self._frames_since_dither = 0
                    self._record_event_cost("dither", time.time() - _t0)
                    self._frame_had_event = True
                except SafetyAbort:
                    raise
                except Exception as e:
                    bus.log("warning", f"dither failed: {e}", "sequence")

            if await self._refocus_due():
                await self._autofocus("refocus")
                self._frame_had_event = True

            self._begin_frame(ti, si, step.exposure_s)
            shown = (self._session.accepted(step.id) + 1
                     if quota and self._session is not None else i + 1)
            self._set_state(state="running",
                            detail=f"{target.name}: {step.filter or 'no filter'} "
                                   f"{step.exposure_s:g}s  [{shown}/{step.count}]")
            info = await self._capture(step, target)
            self._frames_since_dither += 1
            self._frames_since_focus += 1
            # quality-before-record (§1.9-D, C2-8): decide accept BEFORE _done
            # advances. EVERY frame goes in the report.
            accepted = self._check_quality(info)
            self._reporter_record(target, step, info, accepted=accepted)
            if accepted:
                step_rejects = 0
                self._night_rejects = 0            # resets on ANY accepted frame
                self._record_frame(key, i, target, step, info)
                i += 1
                continue
            if quota:
                # accepted mode (spec §3): rejects are ALWAYS kept on disk and
                # ledger-recorded (regrading needs the file) — escalation's
                # hfr_reject_action applies to attempts mode ONLY. _done does
                # not advance; the frame still needs cadence bookkeeping so it
                # can't pollute the next frame's overhead sample.
                self._record_session_frame(target, step, info,
                                           auto_accepted=False)
                self._end_discarded_frame()
                step_rejects += 1
                self._night_rejects += 1
                if plan.max_consecutive_rejects_night \
                        and self._night_rejects >= plan.max_consecutive_rejects_night:
                    raise NightQualityStop(
                        f"{self._night_rejects} consecutive rejects across targets")
                if plan.max_consecutive_rejects \
                        and step_rejects >= plan.max_consecutive_rejects:
                    bus.log("warning",
                            f"{target.name}: {step_rejects} consecutive rejects — "
                            "skipping to the next step (shortfall stays in the "
                            "ledger for another night)", "sequence")
                    return
                continue
            # attempts mode: legacy escalation path (warn / discard / retake).
            if not await self._handle_reject(info, key, i, target, step):
                self._record_frame(key, i, target, step, info, accepted=False)
            i += 1
```

In `_run()` (:540-624), add a terminal arm for the night guard AFTER the `except SafetyAbort` block and BEFORE `except asyncio.CancelledError`:
```python
        except NightQualityStop as e:
            # per-night reject guard (spec §3): end the night early — the same
            # complete+end_reason terminal shape as dawn_cutoff, alerting via
            # the existing log→dispatcher path (error level reaches all sinks).
            bus.log("error", f"sequence '{plan.name}' stopped early — {e} "
                             "(clouds?)", "sequence")
            self._set_state(state="complete",
                            detail="stopped early: consecutive quality rejects",
                            end_reason="quality", schedule=None)
            self._finalize_report("quality")
            await self._wind_down(plan.park_when_done, plan.warm_cooler_when_done)
```

No `_set_state`/`compute_eta` surgery is needed for quota progress: in accepted
mode `_record_frame` fires ONLY on accepted frames, so `_frames_done` (and the
`_done` map `compute_eta` reads through `total - _frames_done`) already count
accepted frames — the ETA is "remaining accepted × exposure", optimistic under
rejection exactly as the spec documents. The quota test asserts
`progress.frames_done == 3` to pin this.

- [ ] **Step 4: Run tests to verify pass**

```
cd server && ./.venv/Scripts/python.exe -m pytest -q tests/test_session_quota.py tests/test_session_engine.py tests/test_sequence.py
```
Expected: all pass (8 new + prior suites — attempts-mode default behavior byte-compatible). Full suite:
```
cd server && ./.venv/Scripts/python.exe -m pytest -q
```
Expected: `1000 passed`, 0 failures.

- [ ] **Step 5: Commit**

```
git add server/astrodeck/sequence/models.py server/astrodeck/sequence/engine.py server/tests/test_session_quota.py
git commit -m "$(cat <<'EOF'
feat(sessions): accepted-frame quota mode, star/RMS gates, per-step + per-night reject guards (spec §3)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01M6wy4Lkm8yAoZWJWiyv6FL
EOF
)"
```

---

### Task 5: Graceful resume — id-keyed seeding, per-night reports, collision-safe ids

**Files:**
- Test: `server/tests/test_session_resume.py` (create — the resume MECHANISM shipped in Task 3; this task locks its semantics with dedicated tests and fixes anything they flush out)

**Interfaces:**
- Consumes: `SequenceEngine.start(plan, *, session=)`, `Session.done_map()`, `_mint_report_id(plan_name, started_at, taken)` (Task 3); `Schedule` model (existing).
- Produces: no new names — behavioral guarantees later tasks assume: resume = full normal start path; `_done` seeded from `done_map()`; reordered-plan resume does NOT misattribute; per-night report ids never collide.

- [ ] **Step 1: Write the failing test**

Create `server/tests/test_session_resume.py`:

```python
"""Task 5: graceful resume semantics (sessions spec §4).

Resume re-enters the FULL normal start path; _done seeds from the id-keyed
ledger so an edited/reordered plan never misattributes; a closed-window target
dormants the night (dawn analogue) and resumes at exact remaining counts; and
report-id minting is collision-safe across a session's nights."""
import asyncio
import time

import pytest

import astrodeck.hub as hub_module
from astrodeck.hub import Hub
from astrodeck.sequence import SequenceEngine, SequencePlan
from astrodeck.sequence.engine import _mint_report_id
from astrodeck.sequence.models import ExposureStep, Schedule, Target
from astrodeck.sequence.session import session_store


@pytest.fixture
async def sim_hub(tmp_path, monkeypatch):
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path)
    _safety = hub_module.config_store.cfg().safety
    monkeypatch.setattr(_safety, "solar_avoidance", False)
    h = Hub()
    await h.connect_sim()
    yield h
    await h.disconnect_all()


async def wait_for(predicate, timeout=30.0):
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.1)
    return False


def _target(name, count) -> Target:
    return Target(name=name, ra_hours=5.5881, dec_deg=-5.3911, center=False,
                  autofocus_first=False,
                  steps=[ExposureStep(filter="L", exposure_s=0.05, count=count)])


def test_mint_report_id_collision_safe():
    ts = time.time()
    first = _mint_report_id("My Plan", ts, [])
    assert first.startswith("My_Plan-")
    second = _mint_report_id("My Plan", ts, [first])
    assert second == f"{first}-2"
    third = _mint_report_id("My Plan", ts, [first, second])
    assert third == f"{first}-3"


async def test_reordered_plan_resume_does_not_misattribute(sim_hub):
    plan = SequencePlan(name="re", guide=False, dither_every=0, autofocus_every=0,
                        meridian_flip=False,
                        targets=[_target("A", 3), _target("B", 3)])
    a_step = plan.targets[0].steps[0]
    b_step = plan.targets[1].steps[0]
    eng = SequenceEngine(sim_hub)
    eng.start(plan)
    sid = eng._session.id
    # let target A record at least one frame, then abort mid-run
    assert await wait_for(lambda: eng._frames_done >= 1)
    await eng.abort()
    s = session_store.load(sid)
    a_before = s.accepted(a_step.id)
    assert a_before >= 1
    # EDIT: reorder targets (B first) — ids survive the shuffle
    s.plan = SequencePlan.model_validate(
        {**s.plan.model_dump(),
         "targets": [s.plan.targets[1].model_dump(),
                     s.plan.targets[0].model_dump()]})
    session_store.save(s)
    eng2 = SequenceEngine(sim_hub)
    eng2.start(s.plan, session=s)
    assert await wait_for(lambda: eng2.state.get("state") == "complete")
    done = session_store.load(sid)
    # id-keyed: each step ends at EXACTLY its count — no double-shot, no skip
    assert done.accepted(a_step.id) == 3
    assert done.accepted(b_step.id) == 3
    assert done.status == "complete"


async def test_window_dormant_then_resume_exact_remaining(sim_hub):
    # target B's window closed an hour ago -> skipped -> dawn_cutoff -> dormant
    past = time.strftime("%H:%M", time.localtime(time.time() - 3600))
    b = _target("B", 2)
    b.schedule = Schedule(start_mode="now", stop_mode="time", stop_time=past)
    plan = SequencePlan(name="dawnish", guide=False, dither_every=0,
                        autofocus_every=0, meridian_flip=False,
                        targets=[_target("A", 2), b])
    a_step = plan.targets[0].steps[0]
    b_step = plan.targets[1].steps[0]
    eng = SequenceEngine(sim_hub)
    eng.start(plan)
    sid = eng._session.id
    assert await wait_for(lambda: eng.state.get("state") == "complete")
    assert eng.state.get("end_reason") == "dawn_cutoff"
    s = session_store.load(sid)
    assert s.status == "dormant"                    # unmet work -> resumable
    assert s.accepted(a_step.id) == 2
    assert s.accepted(b_step.id) == 0
    assert s.remaining()[b_step.id] == 2
    # night 2: open B's window (id-safe plan edit), resume
    s.plan.targets[1].schedule = Schedule()
    session_store.save(s)
    eng2 = SequenceEngine(sim_hub)
    eng2.start(s.plan, session=s)
    assert await wait_for(lambda: eng2.state.get("state") == "complete")
    done = session_store.load(sid)
    assert done.status == "complete"
    assert done.accepted(a_step.id) == 2            # A was NOT reshot
    assert done.accepted(b_step.id) == 2            # B got exactly its remainder
    assert len(done.nights) == 2 and done.nights[0] != done.nights[1]
```

- [ ] **Step 2: Run test to verify it fails**

```
cd server && ./.venv/Scripts/python.exe -m pytest -q tests/test_session_resume.py
```
Expected: if Tasks 3-4 are correct these may already pass — that is the point of a lock-in task; any failure here is a REAL resume-semantics bug that must be fixed in `engine.py`/`session.py` before proceeding (likely spots: `done_map()` capping, `_target_complete` id keys, dawn-path finalize ordering). Run first, observe, fix, re-run.

- [ ] **Step 3: Implement**

No planned production diff. If Step 2 flushed out a defect, fix it minimally in `server/astrodeck/sequence/engine.py` / `session.py` (keeping every signature from Tasks 2-4 unchanged) and note the fix in the commit body.

- [ ] **Step 4: Run tests to verify pass**

```
cd server && ./.venv/Scripts/python.exe -m pytest -q tests/test_session_resume.py
```
Expected: `3 passed`. Full suite:
```
cd server && ./.venv/Scripts/python.exe -m pytest -q
```
Expected: `1003 passed`, 0 failures.

- [ ] **Step 5: Commit**

```
git add server/tests/test_session_resume.py
git commit -m "$(cat <<'EOF'
test(sessions): lock graceful-resume semantics — id-keyed reorder safety, window-dormancy resume, report-id mint (spec §4)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01M6wy4Lkm8yAoZWJWiyv6FL
EOF
)"
```

---
### Task 6: Thumbnails — best-effort ~512px JPEG per ledger record

**Files:**
- Modify: `server/astrodeck/sequence/engine.py` (imports :27-45; `_record_session_frame` from Task 3; two new methods)
- Test: `server/tests/test_session_thumbs.py` (create)

**Interfaces:**
- Consumes: `to_jpeg(data, *, black=None, mid=None, white=None, max_width=1400, quality=85) -> tuple[bytes, int, int]` from `astrodeck.imaging.processing` (auto-stretch when no levels — `processing.py:142-153`); `session_store.thumbs_dir(session_id)`; `hub.last_frame.data` (numpy array on sim/Alpaca; `None`/absent on NINA — thumb skipped).
- Produces:
  - `SequenceEngine._spawn_thumb(self, sf: SessionFrame) -> None`
  - `SequenceEngine._render_thumb(self, session: Session, sf: SessionFrame, data) -> None` (async)
  - Thumb path convention `CAPTURE_DIR/sessions/<sid>/thumbs/<frame_id>.jpg`; `SessionFrame.thumb = "thumbs/<frame_id>.jpg"` (relative, matching the spec + Task 7's route)

- [ ] **Step 1: Write the failing test**

Create `server/tests/test_session_thumbs.py`:

```python
"""Task 6: per-frame review thumbnails (sessions spec §3) — sim frame."""
import asyncio

import pytest

import astrodeck.hub as hub_module
from astrodeck.hub import Hub
from astrodeck.sequence import SequenceEngine, SequencePlan
from astrodeck.sequence.models import ExposureStep, Target
from astrodeck.sequence import session as session_mod
from astrodeck.sequence.session import session_store


@pytest.fixture
async def sim_hub(tmp_path, monkeypatch):
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path)
    _safety = hub_module.config_store.cfg().safety
    monkeypatch.setattr(_safety, "solar_avoidance", False)
    h = Hub()
    await h.connect_sim()
    yield h
    await h.disconnect_all()


async def wait_for(predicate, timeout=30.0):
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.1)
    return False


async def test_sim_frame_gets_thumbnail(sim_hub):
    plan = SequencePlan(name="thumb", guide=False, dither_every=0,
                        autofocus_every=0, meridian_flip=False, targets=[Target(
                            name="M42", ra_hours=5.5881, dec_deg=-5.3911,
                            center=False, autofocus_first=False,
                            steps=[ExposureStep(filter="L", exposure_s=0.05,
                                                count=1)])])
    eng = SequenceEngine(sim_hub)
    eng.start(plan)
    sid = eng._session.id
    assert await wait_for(lambda: eng.state.get("state") == "complete")

    def thumb_done():
        s = session_store.load(sid)
        return bool(s.frames) and s.frames[0].thumb is not None
    # the render task is fire-and-forget: poll until it lands
    assert await wait_for(thumb_done, timeout=15.0)
    s = session_store.load(sid)
    assert s.frames[0].thumb == f"thumbs/{s.frames[0].id}.jpg"
    p = session_mod._sessions_dir() / sid / s.frames[0].thumb
    assert p.exists() and p.stat().st_size > 0
    assert p.read_bytes()[:2] == b"\xff\xd8"       # JPEG magic
```

- [ ] **Step 2: Run test to verify it fails**

```
cd server && ./.venv/Scripts/python.exe -m pytest -q tests/test_session_thumbs.py
```
Expected: `1 failed` — `wait_for(thumb_done)` returns `False` (thumb stays `None`; nothing renders it yet).

- [ ] **Step 3: Implement**

In `server/astrodeck/sequence/engine.py`:

Add to the imports (after the existing `from ..focus import run_autofocus`):
```python
from ..imaging.processing import to_jpeg
```

At the END of `_record_session_frame` (Task 3), change the tail from:
```python
        try:
            self._session.frames.append(sf)
            session_store.save(self._session)
        except Exception as e:
            bus.log("warning", f"session ledger write failed: {e}", "sequence")
        return sf
```
to:
```python
        try:
            self._session.frames.append(sf)
            session_store.save(self._session)
        except Exception as e:
            bus.log("warning", f"session ledger write failed: {e}", "sequence")
        self._spawn_thumb(sf)
        return sf
```

Add the two methods right below `_record_session_frame`:
```python
    def _spawn_thumb(self, sf: SessionFrame | None) -> None:
        """Fire-and-forget ~512px review thumbnail (spec §3). Best-effort by
        design: NINA frames carry no raw array (data is None) and any render
        failure simply leaves ``thumb=None`` — capture is never blocked."""
        if self._session is None or sf is None:
            return
        frame = getattr(self.hub, "last_frame", None)
        data = getattr(frame, "data", None)
        if data is None:
            return
        try:
            asyncio.create_task(self._render_thumb(self._session, sf, data))
        except RuntimeError:
            pass                              # no running loop (defensive)

    async def _render_thumb(self, session: Session, sf: SessionFrame,
                            data) -> None:
        """Encode + write the thumb off-thread, then stamp the relative path
        onto the ledger frame and re-save the session (event-loop-serialized
        with the capture loop's own saves, so writes never interleave)."""
        try:
            jpeg, _w, _h = await asyncio.to_thread(
                to_jpeg, data, max_width=512)  # auto-stretch, ~512px long edge
            tdir = session_store.thumbs_dir(session.id)
            await asyncio.to_thread(tdir.mkdir, parents=True, exist_ok=True)
            path = tdir / f"{sf.id}.jpg"
            await asyncio.to_thread(path.write_bytes, jpeg)
            sf.thumb = f"thumbs/{sf.id}.jpg"
            session_store.save(session)
        except Exception as e:
            bus.log("warning", f"thumb render failed: {e}", "sequence")
```

- [ ] **Step 4: Run tests to verify pass**

```
cd server && ./.venv/Scripts/python.exe -m pytest -q tests/test_session_thumbs.py tests/test_session_engine.py tests/test_session_quota.py
```
Expected: all pass. Full suite:
```
cd server && ./.venv/Scripts/python.exe -m pytest -q
```
Expected: `1004 passed`, 0 failures.

- [ ] **Step 5: Commit**

```
git add server/astrodeck/sequence/engine.py server/tests/test_session_thumbs.py
git commit -m "$(cat <<'EOF'
feat(sessions): best-effort off-thread ~512px thumbnails per ledger record (spec §3)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01M6wy4Lkm8yAoZWJWiyv6FL
EOF
)"
```

---

### Task 7: Sessions API — routes, RBAC, redaction, WS `session` sub-state

**Files:**
- Modify: `server/astrodeck/api/redact.py` (`_redact_drivers_for` :100-130 is the pattern; add `_redact_session_for` below it)
- Modify: `server/astrodeck/api/app.py` (imports :40-41 + :73; body models near PlanSaveBody :443-455; new routes after the plans routes :1696; )
- Modify: `server/astrodeck/sequence/engine.py` (`_set_state` :416-455; the six terminal `_set_state` calls in `_run`/`abort`/NightQualityStop arm)
- Test: `server/tests/test_sessions_api.py` (create)

**Interfaces:**
- Consumes: `session_store` (Task 2), `engine.start(plan, session=)` (Task 3), `Session.remaining()`, `safe_id_path`, `_redact_drivers_for` pattern, `require`/`declare`, `FileResponse` (already imported in app.py :14).
- Produces:
  - `_redact_session_for(payload: dict, principal: Principal | None) -> dict` (redact.py)
  - Routes (spec §6): `GET /api/sessions` -> `{"sessions": [rows]}`; `GET /api/sessions/{session_id}` (path-stripped for non-config.backend); `POST /api/sessions/{session_id}/resume`; `PATCH /api/sessions/{session_id}` body `SessionPatchBody{auto_resume?, status?, plan?}` -> `{id, status, auto_resume, remaining, merge?}`; `PATCH /api/sessions/{session_id}/frames/{frame_id}` body `FramePatchBody{override?, metrics?}` -> `{frame, remaining}`; `DELETE /api/sessions/{session_id}`; `GET /api/sessions/{session_id}/frames/{frame_id}/thumb`
  - WS: `SequenceState.session = {id, name, count_mode, accepted, target}` sub-state with schedule-style explicit-None clear

- [ ] **Step 1: Write the failing test**

Create `server/tests/test_sessions_api.py`:

```python
"""Task 7: /api/sessions surface (sessions spec §6/§8) — RBAC per route, 409s,
id-merge, frame regrade + metrics merge, path redaction, thumb cap.

Harness mirrors tests/test_rbac_enforcement.py (FakeAuthProvider + TestClient)."""
import pytest
from fastapi.testclient import TestClient

import astrodeck.api.app as app_module
import astrodeck.hub as hub_module
from astrodeck.auth import principal_for_role, reset_active_provider, set_active_provider
from astrodeck.config import ConfigStore
from astrodeck.sequence.models import ExposureStep, SequencePlan, Target
from astrodeck.sequence.session import Session, SessionFrame, session_store


class FakeAuthProvider:
    name = "fake"

    def __init__(self, principal):
        self._principal = principal

    async def resolve(self, request):
        return self._principal


@pytest.fixture(autouse=True)
def _reset_provider_after():
    yield
    reset_active_provider()


@pytest.fixture
def client(tmp_path, monkeypatch):
    temp_store = ConfigStore(path=tmp_path / "astrodeck.json")
    import astrodeck.config as config_mod
    monkeypatch.setattr(config_mod, "config_store", temp_store)
    monkeypatch.setattr(hub_module, "config_store", temp_store)
    monkeypatch.setattr(app_module, "config_store", temp_store)
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path / "captures")
    monkeypatch.delenv(app_module.AUTH_ENV_VAR, raising=False)
    monkeypatch.setattr(app_module, "configure_provider_from_auth",
                        lambda _auth: app_module.get_active_provider())
    reset_active_provider()
    app = app_module.create_app()
    with TestClient(app) as c:
        yield c


def _plan(count=2, mode="accepted") -> SequencePlan:
    return SequencePlan(name="api", count_mode=mode, targets=[Target(
        name="M42", ra_hours=5.5881, dec_deg=-5.3911,
        steps=[ExposureStep(filter="L", exposure_s=60, count=count)])])


def _session(status="dormant", count=2) -> Session:
    plan = _plan(count=count)
    s = Session(name="api", created_ts=1.0, status=status, plan=plan)
    step = plan.targets[0].steps[0]
    for _ in range(2):
        s.frames.append(SessionFrame(ts=1.0, night="n1",
                                     target_id=plan.targets[0].id,
                                     step_id=step.id, path="C:/secret/f.fits",
                                     metrics={"hfr": 2.0}, auto_accepted=True))
    session_store.save(s)
    return s


def test_list_and_detail_admin_sees_paths(client):
    s = _session()
    rows = client.get("/api/sessions").json()["sessions"]
    assert [r["id"] for r in rows] == [s.id]
    assert rows[0]["accepted"] == 2 and rows[0]["total"] == 2
    detail = client.get(f"/api/sessions/{s.id}").json()
    assert detail["frames"][0]["path"] == "C:/secret/f.fits"   # open default = admin


def test_viewer_reads_but_paths_stripped_and_mutations_403(client):
    s = _session()
    set_active_provider(FakeAuthProvider(principal_for_role("viewer")))
    detail = client.get(f"/api/sessions/{s.id}").json()
    assert "path" not in detail["frames"][0]                   # frame-path strip
    assert client.get("/api/sessions").status_code == 200
    assert client.post(f"/api/sessions/{s.id}/resume").status_code == 403
    assert client.patch(f"/api/sessions/{s.id}",
                        json={"auto_resume": True}).status_code == 403
    assert client.delete(f"/api/sessions/{s.id}").status_code == 403
    fid = s.frames[0].id
    assert client.patch(f"/api/sessions/{s.id}/frames/{fid}",
                        json={"override": "reject"}).status_code == 403


def test_operator_cannot_resume(client):
    s = _session()
    set_active_provider(FakeAuthProvider(principal_for_role("operator")))
    assert client.post(f"/api/sessions/{s.id}/resume").status_code == 403


def test_patch_plan_dormant_only_with_id_merge(client):
    s = _session()
    kept_step = s.plan.targets[0].steps[0]
    new_plan = _plan().model_dump()
    new_plan["targets"][0]["steps"] = [kept_step.model_dump(),
                                       {"exposure_s": 30, "count": 5}]
    r = client.patch(f"/api/sessions/{s.id}", json={"plan": new_plan}).json()
    assert kept_step.id in r["merge"]["kept"]
    assert len(r["merge"]["new"]) == 1
    assert r["merge"]["dropped"] == []
    # dropping the frame-bearing step reports it
    r2 = client.patch(f"/api/sessions/{s.id}",
                      json={"plan": _plan().model_dump()}).json()
    assert kept_step.id in r2["merge"]["dropped"]
    # frames stay recorded even though their step id no longer exists
    assert len(session_store.load(s.id).frames) == 2
    # 409 while not dormant
    active = _session(status="active")
    assert client.patch(f"/api/sessions/{active.id}",
                        json={"plan": _plan().model_dump()}).status_code == 409


def test_auto_resume_singleton_and_abandon(client):
    a, b = _session(), _session()
    client.patch(f"/api/sessions/{a.id}", json={"auto_resume": True})
    client.patch(f"/api/sessions/{b.id}", json={"auto_resume": True})
    assert session_store.load(a.id).auto_resume is False      # disarmed by b
    assert session_store.load(b.id).auto_resume is True
    r = client.patch(f"/api/sessions/{a.id}", json={"status": "abandoned"})
    assert r.json()["status"] == "abandoned"
    assert client.patch(f"/api/sessions/{a.id}",
                        json={"status": "complete"}).status_code == 422


def test_frame_patch_override_flips_remaining_and_metrics_merge(client):
    s = _session()                                   # accepted mode, count=2, 2 accepted
    step_id = s.frames[0].step_id
    fid = s.frames[0].id
    r = client.patch(f"/api/sessions/{s.id}/frames/{fid}",
                     json={"override": "reject"}).json()
    assert r["remaining"][step_id] == 1              # regrade adjusts the quota
    r = client.patch(f"/api/sessions/{s.id}/frames/{fid}",
                     json={"override": None}).json() # explicit null clears
    assert r["frame"]["override"] is None
    assert r["remaining"][step_id] == 0
    r = client.patch(f"/api/sessions/{s.id}/frames/{fid}",
                     json={"metrics": {"fwhm": 3.2}}).json()
    assert r["frame"]["metrics"] == {"hfr": 2.0, "fwhm": 3.2}   # float-merge
    # running session: regrade refused
    active = _session(status="active")
    assert client.patch(
        f"/api/sessions/{active.id}/frames/{active.frames[0].id}",
        json={"override": "reject"}).status_code == 409


def test_delete_removes_files_never_fits(client, tmp_path):
    s = _session()
    tdir = session_store.thumbs_dir(s.id)
    tdir.mkdir(parents=True)
    (tdir / "x.jpg").write_bytes(b"\xff\xd8xx")
    fits = tmp_path / "captures" / "keep.fits"
    fits.parent.mkdir(parents=True, exist_ok=True)
    fits.write_bytes(b"SIMPLE")
    assert client.delete(f"/api/sessions/{s.id}").json() == {"deleted": s.id}
    with pytest.raises(KeyError):
        session_store.load(s.id)
    assert not tdir.exists()
    assert fits.exists()                             # FITS never touched
    active = _session(status="active")
    assert client.delete(f"/api/sessions/{active.id}").status_code == 409


def test_thumb_route_serves_jpeg_with_preview_cap(client):
    s = _session()
    fid = s.frames[0].id
    tdir = session_store.thumbs_dir(s.id)
    tdir.mkdir(parents=True)
    (tdir / f"{fid}.jpg").write_bytes(b"\xff\xd8fake")
    r = client.get(f"/api/sessions/{s.id}/frames/{fid}/thumb")
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/jpeg"
    assert client.get(f"/api/sessions/{s.id}/frames/nope/thumb").status_code == 404
```

- [ ] **Step 2: Run test to verify it fails**

```
cd server && ./.venv/Scripts/python.exe -m pytest -q tests/test_sessions_api.py
```
Expected: every test fails with `404 Not Found` on `/api/sessions...` (routes don't exist).

- [ ] **Step 3: Implement**

**7a — `server/astrodeck/api/redact.py`.** Add below `_redact_drivers_for` (:130):
```python
def _redact_session_for(payload: dict, principal: "Principal | None") -> dict:
    """Strip the filesystem ``path`` from every session-ledger frame unless the
    caller holds ``config.backend`` (sessions spec §8) — the same holder rule
    as ``_redact_drivers_for``: endpoint identity == filesystem identity.
    Copies rows; never mutates ``payload`` in place."""
    if principal is not None and principal.has(CAP_CONFIG_BACKEND):
        return payload
    if not isinstance(payload, dict):
        return payload
    frames = payload.get("frames")
    if not isinstance(frames, list):
        return payload
    scrubbed = []
    for row in frames:
        if isinstance(row, dict):
            row = dict(row)
            row.pop("path", None)
        scrubbed.append(row)
    return {**payload, "frames": scrubbed}
```
(`CAP_CONFIG_BACKEND` and `Principal` are already imported at the top of redact.py for `_redact_drivers_for`; verify and extend the import if not.)

**7b — `server/astrodeck/api/app.py`.**

Extend the redact import (:40-41) with `_redact_session_for`, and add `safe_id_path`:
```python
from .redact import (WS_AUTH_RECHECK_S, _redact_drivers_for,  # re-exported at module scope
                     _redact_session_for, _redact_site_for, _redact_ws_event)
from ..persist import safe_id_path
```

Add the body models next to `PlanSaveBody` (:443-455):
```python
class SessionPatchBody(BaseModel):
    auto_resume: bool | None = None
    status: str | None = None            # only "abandoned" is accepted
    plan: SequencePlan | None = None     # dormant-only full replacement (spec §4)


class FramePatchBody(BaseModel):
    override: str | None = None          # "accept" | "reject" | null (clear)
    metrics: dict[str, float] | None = None
```

Add the routes inside `create_app()` immediately AFTER the plans routes (`import_plan`, :1696):
```python
    # ------------------------------------------------ multi-night sessions (§6)

    @app.get("/api/sessions", dependencies=[Depends(require(CAP_VIEW_STATUS))])
    @declare(CAP_VIEW_STATUS)
    async def list_sessions():
        return {"sessions": await asyncio.to_thread(session_store.list)}

    @app.get("/api/sessions/{session_id}")
    @declare(CAP_VIEW_STATUS)
    async def get_session(session_id: str,
                          principal: Principal = Depends(require(CAP_VIEW_STATUS))):
        try:
            s = await asyncio.to_thread(session_store.load, session_id)
        except KeyError:
            raise HTTPException(404, "session not found")
        return _redact_session_for(s.model_dump(), principal)

    @app.post("/api/sessions/{session_id}/resume",
              dependencies=[Depends(require(CAP_CONTROL_MOUNT))])
    @declare(CAP_CONTROL_MOUNT, reaches={"SequenceEngine.start"})
    async def resume_session(session_id: str):
        try:
            s = await asyncio.to_thread(session_store.load, session_id)
        except KeyError:
            raise HTTPException(404, "session not found")
        if s.status != "dormant":
            raise HTTPException(409, f"session is {s.status}, not dormant")
        try:
            hub.require("camera")
            engine.start(s.plan, session=s)
        except DeviceError as e:
            raise _err(e)
        return {"resumed": True, "remaining": sum(s.remaining().values())}

    @app.patch("/api/sessions/{session_id}",
               dependencies=[Depends(require(CAP_CONTROL_MOUNT))])
    @declare(CAP_CONTROL_MOUNT)
    async def patch_session(session_id: str, body: SessionPatchBody):
        try:
            s = await asyncio.to_thread(session_store.load, session_id)
        except KeyError:
            raise HTTPException(404, "session not found")
        merge = None
        if body.plan is not None:
            # id-safe plan edit (spec §4): DORMANT only; running never editable.
            if s.status != "dormant":
                raise HTTPException(409, "plan edits require a dormant session")
            old_ids = {st.id for t in s.plan.targets for st in t.steps}
            new_ids = {st.id for t in body.plan.targets for st in t.steps}
            with_frames = {f.step_id for f in s.frames}
            merge = {"kept": sorted(old_ids & new_ids),
                     "new": sorted(new_ids - old_ids),
                     "dropped": sorted((old_ids - new_ids) & with_frames)}
            s.plan = body.plan
            s.name = body.plan.name or s.name
        if body.status is not None:
            if body.status != "abandoned":
                raise HTTPException(422, "status can only be set to 'abandoned'")
            if s.status == "active":
                raise HTTPException(409, "cannot abandon a running session")
            s.status = "abandoned"
            s.auto_resume = False
        if body.auto_resume is not None:
            if body.auto_resume and s.status != "dormant":
                raise HTTPException(409, "auto-resume arms only dormant sessions")
            if body.auto_resume:
                # server-enforced singleton (spec §5): arming here disarms others.
                for other in await asyncio.to_thread(session_store.load_all):
                    if other.id != s.id and other.auto_resume:
                        other.auto_resume = False
                        await asyncio.to_thread(session_store.save, other)
            s.auto_resume = body.auto_resume
        await asyncio.to_thread(session_store.save, s)
        out = {"id": s.id, "status": s.status, "auto_resume": s.auto_resume,
               "remaining": s.remaining()}
        if merge is not None:
            out["merge"] = merge
        return out

    @app.patch("/api/sessions/{session_id}/frames/{frame_id}",
               dependencies=[Depends(require(CAP_CONTROL_MOUNT))])
    @declare(CAP_CONTROL_MOUNT)
    async def patch_session_frame(session_id: str, frame_id: str,
                                  body: FramePatchBody):
        try:
            s = await asyncio.to_thread(session_store.load, session_id)
        except KeyError:
            raise HTTPException(404, "session not found")
        if s.status == "active":
            # regrade is a between-nights operation (spec: manual regrade UI
            # between nights); also prevents store-vs-engine copy divergence.
            raise HTTPException(409, "session is running — regrade between nights")
        frame = next((f for f in s.frames if f.id == frame_id), None)
        if frame is None:
            raise HTTPException(404, "frame not found")
        if "override" in body.model_fields_set:      # omitted ≠ explicit null
            if body.override not in ("accept", "reject", None):
                raise HTTPException(422, "override must be 'accept', 'reject' or null")
            frame.override = body.override
        if body.metrics is not None:
            # float-merge: the external-grader write path (spec §10). pydantic
            # already coerced values to float (non-numeric -> 422).
            frame.metrics.update({k: float(v) for k, v in body.metrics.items()})
        await asyncio.to_thread(session_store.save, s)
        return {"frame": frame.model_dump(), "remaining": s.remaining()}

    @app.delete("/api/sessions/{session_id}",
                dependencies=[Depends(require(CAP_CONTROL_MOUNT))])
    @declare(CAP_CONTROL_MOUNT)
    async def delete_session(session_id: str):
        try:
            s = await asyncio.to_thread(session_store.load, session_id)
        except KeyError:
            raise HTTPException(404, "session not found")
        if s.status == "active":
            raise HTTPException(409, "cannot delete a running session")
        # session file + thumbs only — NEVER the FITS frames (spec §6).
        await asyncio.to_thread(session_store.delete, session_id)
        return {"deleted": session_id}

    @app.get("/api/sessions/{session_id}/frames/{frame_id}/thumb",
             dependencies=[Depends(require(CAP_VIEW_PREVIEW))])
    @declare(CAP_VIEW_PREVIEW)
    async def session_frame_thumb(session_id: str, frame_id: str):
        try:
            tdir = session_store.thumbs_dir(session_id)
            path = safe_id_path(tdir, frame_id, suffix=".jpg")
        except KeyError:
            raise HTTPException(404, "not found")
        if not path.exists():
            raise HTTPException(404, "no thumbnail")
        return FileResponse(path, media_type="image/jpeg")
```

**7c — `server/astrodeck/sequence/engine.py`, WS `session` sub-state.**

In `_set_state()` (:416-455), directly BEFORE the existing `schedule`-clear block (`if "schedule" in kw and kw["schedule"] is None:`), add:
```python
        # session sub-state (spec §6): {id, name, count_mode, accepted, target},
        # cleared with the same explicit-None semantics as `schedule`.
        if "session" in kw and kw["session"] is None:
            kw = {k: v for k, v in kw.items() if k != "session"}
            self.state.pop("session", None)
        elif self._session is not None and self.plan is not None:
            kw.setdefault("session", {
                "id": self._session.id,
                "name": self._session.name,
                "count_mode": getattr(self.plan, "count_mode", "attempts"),
                "accepted": self._session.total_accepted(),
                "target": kw.get("target", self.state.get("target")),
            })
```

Then add `session=None` to every TERMINAL `_set_state` call so the sub-state never outlives the run (six sites):
- cooling-skip: `self._set_state(state="complete", detail="skipped: camera did not reach target temp", end_reason="cooling_skip", schedule=None, session=None)`
- dawn: `self._set_state(state="complete", detail="stopped at dawn (windows closed)", end_reason="dawn_cutoff", schedule=None, session=None)`
- complete: `self._set_state(state="complete", detail="all targets complete", schedule=None, session=None)`
- SafetyAbort: `self._set_state(state="aborted", detail=str(e), end_reason="unsafe", schedule=None, session=None)`
- error: `self._set_state(state="error", detail=str(e), schedule=None, session=None)`
- NightQualityStop arm (Task 4): add `session=None` to its `_set_state`
- `abort()` (:394): `self._set_state(state="aborted", detail="sequence aborted", schedule=None, session=None)`

- [ ] **Step 4: Run tests to verify pass**

```
cd server && ./.venv/Scripts/python.exe -m pytest -q tests/test_sessions_api.py tests/test_rbac_enforcement.py tests/test_session_engine.py
```
Expected: all pass (the create_app boot-time `assert_route_capabilities` also validates every new route's declare/require pairing). Full suite:
```
cd server && ./.venv/Scripts/python.exe -m pytest -q
```
Expected: `1013 passed`, 0 failures.

- [ ] **Step 5: Commit**

```
git add server/astrodeck/api/app.py server/astrodeck/api/redact.py server/astrodeck/sequence/engine.py server/tests/test_sessions_api.py
git commit -m "$(cat <<'EOF'
feat(sessions): /api/sessions surface — RBAC, frame-path redaction, id-merge PATCH, thumb route, WS session sub-state (spec §6/§8)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01M6wy4Lkm8yAoZWJWiyv6FL
EOF
)"
```

---
### Task 8: ResumeArm — auto-resume-at-dusk service

**Files:**
- Create: `server/astrodeck/sequence/resume_arm.py`
- Modify: `server/astrodeck/api/app.py` (module wiring after `engine.dispatcher = dispatcher` :1284; lifespan start/stop :101-199)
- Test: `server/tests/test_resume_arm.py` (create)

**Interfaces:**
- Consumes: `session_store.armed()` (Task 2), `engine.start(plan, session=)` + `engine.running` (Task 3), `schedule.resolve_window(sched, site, twilight_deg, now)` (existing), `hub.site`, `config_store.cfg().safety.twilight_deg`, `hub.require("camera")`.
- Produces:
  - `ResumeArm(engine, hub, *, clock=time.time)` with `.start() -> None`, `.stop() -> None` (async), `.tick() -> None` (async, injectable-clock testable), `.resume_veto() -> str | None` (v1: always None — sub-project C's weather-gate hook), `._window_open(session, now) -> bool`
  - `CHECK_INTERVAL_S = 60.0`, `RETRY_INTERVAL_S = 600.0`
  - app.py module global `resume_arm = ResumeArm(engine, hub)`

- [ ] **Step 1: Write the failing test**

Create `server/tests/test_resume_arm.py`:

```python
"""Task 8: ResumeArm auto-resume service (sessions spec §5) — injected clock
(monkeypatched time source / _window_open, schedule-test precedent), real sim
hub + engine (no FakeHub)."""
import asyncio

import pytest

import astrodeck.hub as hub_module
from astrodeck.devices.base import DeviceError
from astrodeck.hub import Hub
from astrodeck.sequence import SequenceEngine, SequencePlan
from astrodeck.sequence.models import ExposureStep, Target
from astrodeck.sequence.resume_arm import RETRY_INTERVAL_S, ResumeArm
from astrodeck.sequence.session import session_store


@pytest.fixture
async def sim_hub(tmp_path, monkeypatch):
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path)
    _safety = hub_module.config_store.cfg().safety
    monkeypatch.setattr(_safety, "solar_avoidance", False)
    h = Hub()
    await h.connect_sim()
    yield h
    await h.disconnect_all()


async def wait_for(predicate, timeout=30.0):
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.1)
    return False


def _plan(count=6) -> SequencePlan:
    return SequencePlan(name="arm", guide=False, dither_every=0,
                        autofocus_every=0, meridian_flip=False, targets=[Target(
                            name="M42", ra_hours=5.5881, dec_deg=-5.3911,
                            center=False, autofocus_first=False,
                            steps=[ExposureStep(filter="L", exposure_s=0.05,
                                                count=count)])])


async def _dormant_armed(sim_hub, engine) -> str:
    """Real dormant session: start, get a frame in, abort, arm."""
    engine.start(_plan())
    sid = engine._session.id
    assert await wait_for(lambda: engine._frames_done >= 1)
    await engine.abort()
    s = session_store.load(sid)
    assert s.status == "dormant"
    s.auto_resume = True
    session_store.save(s)
    return sid


async def test_tick_noop_when_window_closed(sim_hub, monkeypatch):
    engine = SequenceEngine(sim_hub)
    await _dormant_armed(sim_hub, engine)
    now = {"t": 1_700_000_000.0}
    arm = ResumeArm(engine, sim_hub, clock=lambda: now["t"])
    monkeypatch.setattr(ResumeArm, "_window_open", lambda self, s, t: False)
    await arm.tick()
    assert not engine.running


async def test_tick_resumes_when_window_open(sim_hub, monkeypatch):
    engine = SequenceEngine(sim_hub)
    sid = await _dormant_armed(sim_hub, engine)
    now = {"t": 1_700_000_000.0}
    arm = ResumeArm(engine, sim_hub, clock=lambda: now["t"])
    monkeypatch.setattr(ResumeArm, "_window_open", lambda self, s, t: True)
    await arm.tick()
    assert engine.running
    assert await wait_for(lambda: engine.state.get("state") == "complete")
    assert session_store.load(sid).status == "complete"


async def test_refusal_retries_after_10_minutes(sim_hub, monkeypatch):
    engine = SequenceEngine(sim_hub)
    await _dormant_armed(sim_hub, engine)
    now = {"t": 1_700_000_000.0}
    arm = ResumeArm(engine, sim_hub, clock=lambda: now["t"])
    monkeypatch.setattr(ResumeArm, "_window_open", lambda self, s, t: True)
    calls = {"n": 0}

    def refuse(role):
        calls["n"] += 1
        raise DeviceError("no camera")
    monkeypatch.setattr(sim_hub, "require", refuse)
    await arm.tick()                                  # refusal
    assert calls["n"] == 1 and not engine.running
    assert arm._retry_at == now["t"] + RETRY_INTERVAL_S
    now["t"] += 300
    await arm.tick()                                  # still backing off
    assert calls["n"] == 1
    now["t"] += 301                                   # past the 10-min mark
    await arm.tick()
    assert calls["n"] == 2


async def test_give_up_when_window_closes_mid_retry(sim_hub, monkeypatch):
    engine = SequenceEngine(sim_hub)
    sid = await _dormant_armed(sim_hub, engine)
    now = {"t": 1_700_000_000.0}
    arm = ResumeArm(engine, sim_hub, clock=lambda: now["t"])
    window = {"open": True}
    monkeypatch.setattr(ResumeArm, "_window_open",
                        lambda self, s, t: window["open"])
    monkeypatch.setattr(sim_hub, "require",
                        lambda role: (_ for _ in ()).throw(DeviceError("no cam")))
    await arm.tick()                                  # refusal -> backoff armed
    window["open"] = False                            # dawn passed
    now["t"] += RETRY_INTERVAL_S + 1
    await arm.tick()
    assert arm._gave_up_for == sid                    # one give-up, no attempt
    assert not engine.running


async def test_disarm_and_running_engine_stop_interest(sim_hub, monkeypatch):
    engine = SequenceEngine(sim_hub)
    sid = await _dormant_armed(sim_hub, engine)
    s = session_store.load(sid)
    s.auto_resume = False                             # UI disarm
    session_store.save(s)
    now = {"t": 1_700_000_000.0}
    arm = ResumeArm(engine, sim_hub, clock=lambda: now["t"])
    monkeypatch.setattr(ResumeArm, "_window_open", lambda self, s, t: True)
    await arm.tick()
    assert not engine.running                         # nothing armed -> no-op
```

- [ ] **Step 2: Run test to verify it fails**

```
cd server && ./.venv/Scripts/python.exe -m pytest -q tests/test_resume_arm.py
```
Expected: collection error — `ModuleNotFoundError: No module named 'astrodeck.sequence.resume_arm'`.

- [ ] **Step 3: Implement**

Create `server/astrodeck/sequence/resume_arm.py`:

```python
"""Auto-resume-at-dusk service (sessions spec §5).

One asyncio task started with the app (pattern: the AlertDispatcher lifespan
task), 60s cadence. Arms via ``Session.auto_resume`` (the PATCH route enforces
the singleton). When the engine is idle, exactly one armed dormant session
exists, and tonight's window for any of its targets has opened (reusing
``schedule.resolve_window``), it attempts the SAME code path as a manual
resume — every existing safety gate (sun avoidance, safety monitor, horizon
preflight) runs inside ``engine.start`` / the run itself. A refusal alerts and
retries every 10 minutes; when the window closes mid-backoff (dawn) it alerts
one give-up and stays quiet until the window reopens (the next night). The
run_start alert on success comes free from the AlertDispatcher's sequence
state machine. ``resume_veto()`` is the sub-project-C weather-gate hook — v1
always returns None."""
from __future__ import annotations

import asyncio
import time

from ..config import config_store
from ..events import bus
from . import schedule
from .session import Session, session_store

CHECK_INTERVAL_S = 60.0
RETRY_INTERVAL_S = 600.0


class ResumeArm:
    def __init__(self, engine, hub, *, clock=time.time):
        self.engine = engine
        self.hub = hub
        self._clock = clock
        self._task: asyncio.Task | None = None
        self._retry_at: float = 0.0        # refusal backoff: no attempt before this
        self._gave_up_for: str | None = None   # session id we give-up-alerted on

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None

    async def _run(self) -> None:
        while True:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception as e:      # noqa: BLE001 — service must never die
                bus.log("warning", f"resume-arm tick failed: {e}", "sequence")
            await asyncio.sleep(CHECK_INTERVAL_S)

    def resume_veto(self) -> str | None:
        """Veto hook (spec §5). v1: no veto — sub-project C plugs the
        cloud/precip forecast gate in here. Non-None = human-readable reason."""
        return None

    def _window_open(self, session: Session, now: float) -> bool:
        """True when tonight's window for ANY of the session's targets is open
        (calibration targets shoot any time). Reuses schedule.resolve_window —
        the same resolution a run's scheduler freezes at start."""
        cfg = config_store.cfg()
        site = self.hub.site
        twilight = cfg.safety.twilight_deg if cfg else -12.0
        for t in session.plan.targets:
            if t.calibration:
                return True
            start, stop = schedule.resolve_window(t.schedule, site, twilight, now)
            if start is not None and start <= now and (stop is None or now < stop):
                return True
        return False

    async def tick(self) -> None:
        now = self._clock()
        if self.engine.running:
            return                          # anything running = no interest
        armed = session_store.armed()
        if armed is None:
            self._retry_at = 0.0            # disarmed from the UI: stop instantly
            self._gave_up_for = None
            return
        if not self._window_open(armed, now):
            if self._retry_at and self._gave_up_for != armed.id:
                # the window closed while we were mid-backoff: dawn beat us.
                self._gave_up_for = armed.id
                self._retry_at = 0.0
                bus.log("error", f"auto-resume gave up for tonight: "
                                 f"'{armed.name}' window closed before a "
                                 "successful start", "sequence")
            return
        self._gave_up_for = None            # window open (again): fresh night
        if now < self._retry_at:
            return
        veto = self.resume_veto()
        if veto is not None:
            bus.log("warning", f"auto-resume vetoed: {veto} — retrying in "
                               f"{int(RETRY_INTERVAL_S / 60)} min", "sequence")
            self._retry_at = now + RETRY_INTERVAL_S
            return
        try:
            self.hub.require("camera")
            self.engine.start(armed.plan, session=armed)
        except Exception as e:              # noqa: BLE001 — refusal, not a crash
            bus.log("warning", f"auto-resume refused: {e} — retrying in "
                               f"{int(RETRY_INTERVAL_S / 60)} min", "sequence")
            self._retry_at = now + RETRY_INTERVAL_S
            return
        self._retry_at = 0.0
        bus.log("info", f"auto-resume: '{armed.name}' resumed at dusk", "sequence")
```

Wire it in `server/astrodeck/api/app.py`. After `engine.dispatcher = dispatcher` (:1284):
```python
from ..sequence.resume_arm import ResumeArm

# Auto-resume-at-dusk service (sessions spec §5). Started in the lifespan,
# like the AlertDispatcher; a disarm or any manual start stops its interest.
resume_arm = ResumeArm(engine, hub)
```
(Place the import with the other `..sequence` imports at the top of the file, not inline.)

In `_lifespan`, after `task = asyncio.create_task(dispatcher.run())`:
```python
    resume_arm.start()
```
and in the `finally:` block, before `await dispatcher.stop()`:
```python
        await resume_arm.stop()
```

- [ ] **Step 4: Run tests to verify pass**

```
cd server && ./.venv/Scripts/python.exe -m pytest -q tests/test_resume_arm.py tests/test_sessions_api.py
```
Expected: all pass. Full suite:
```
cd server && ./.venv/Scripts/python.exe -m pytest -q
```
Expected: `1018 passed`, 0 failures. (Server work complete — record the count for Task 14.)

- [ ] **Step 5: Commit**

```
git add server/astrodeck/sequence/resume_arm.py server/astrodeck/api/app.py server/tests/test_resume_arm.py
git commit -m "$(cat <<'EOF'
feat(sessions): ResumeArm auto-resume-at-dusk service with retry/give-up + resume_veto hook (spec §5)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01M6wy4Lkm8yAoZWJWiyv6FL
EOF
)"
```

---

### Task 9: UI types, id backfill lib, store integration, session API wrappers

**Files:**
- Modify: `ui/src/types.ts` (ExposureStep :349-357, Target :359-373, SequencePlan :375-395, SequenceState :237-260; new Session types near PlanRow :1106)
- Create: `ui/src/lib/ids.ts`, `ui/src/api/sessions.ts`
- Modify: `ui/src/store.ts` (imports :229-234, defaultPlan :117-162, loadPlan :271-285, setPlan :691-698)
- Test: `ui/src/lib/__tests__/ids.test.ts` (create)

**Interfaces:**
- Consumes: server shapes from Tasks 2/4/7; `api` client (`ui/src/api.ts`).
- Produces (Tasks 10-13 rely on these EXACT names):
  - types: `ExposureStep.id?: string`, `Target.id?: string`; `SequencePlan.count_mode?/min_stars?/max_guide_rms?/max_consecutive_rejects?/max_consecutive_rejects_night?`; `SessionFrame`, `Session`, `SessionRow`; `SequenceState.session?: { id: string; name: string; count_mode: string; accepted: number; target?: string }`; `end_reason` union gains `"quality"`
  - `lib/ids.ts`: `uid(): string`, `ensureStepId(s: ExposureStep): ExposureStep`, `ensureTargetIds(t: Target): Target`, `ensurePlanIds(p: SequencePlan): SequencePlan` (reference-preserving)
  - `api/sessions.ts`: `listSessions(): Promise<SessionRow[]>`, `getSession(id: string): Promise<Session>`, `resumeSession(id: string)`, `patchSession(id: string, body: SessionPatch)`, `patchFrame(id: string, frameId: string, body: { override?: "accept" | "reject" | null; metrics?: Record<string, number> })`, `deleteSession(id: string)`, `interface SessionPatch { auto_resume?: boolean; status?: "abandoned"; plan?: SequencePlan }`, `interface MergeSummary { kept: string[]; new: string[]; dropped: string[] }`

- [ ] **Step 1: Write the failing test**

Create `ui/src/lib/__tests__/ids.test.ts` (planGroups.test.ts harness pattern):

```ts
// ids.test.ts — pure tests for lib/ids.ts (sessions spec §1 client-side
// id generation + backfill). Inline-assert harness; runs via `npx tsx`.
import { ensurePlanIds, uid } from "../ids";
import type { SequencePlan, Target } from "../../types";

let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; } catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }

const target = (over: Partial<Target> = {}): Target => ({
  name: "M42", ra_hours: 5.6, dec_deg: -5.4, center: true, autofocus_first: true,
  calibration: false,
  steps: [{ filter: "L", exposure_s: 120, gain: 100, offset: 30, binning: 1, count: 10, frame_type: "Light" }],
  ...over,
});

const plan = (targets: Target[]): SequencePlan => ({
  name: "P", targets, guide: true, dither_every: 3, dither_pixels: 3,
  autofocus_every: 0, cool_to: null, cool_timeout_s: 600,
  apply_filter_offsets: true, refocus_on_temp_delta_c: 0, meridian_flip: true,
  recover_guiding: true, hfr_reject_factor: 0, park_when_done: false,
  warm_cooler_when_done: false,
});

test("uid() is 32 hex chars and unique", () => {
  const a = uid(); const b = uid();
  assert(/^[0-9a-f]{32}$/.test(a), `uid shape: ${a}`);
  assert(a !== b, "two uids differ");
});

test("backfills missing target AND step ids", () => {
  const out = ensurePlanIds(plan([target()]));
  assert(!!out.targets[0].id, "target id backfilled");
  assert(!!out.targets[0].steps[0].id, "step id backfilled");
});

test("preserves existing ids", () => {
  const t = target({ id: "tkeep" });
  t.steps[0].id = "skeep";
  const out = ensurePlanIds(plan([t]));
  assert(out.targets[0].id === "tkeep", "target id kept");
  assert(out.targets[0].steps[0].id === "skeep", "step id kept");
});

test("reference-preserving when nothing is missing", () => {
  const complete = ensurePlanIds(plan([target()]));
  const again = ensurePlanIds(complete);
  assert(again === complete, "same object back when all ids present");
});

test("only touched targets are re-created (React re-render scoping)", () => {
  const done = ensurePlanIds(plan([target()])).targets[0];
  const missing = target();
  const out = ensurePlanIds(plan([done, missing]));
  assert(out.targets[0] === done, "complete target reference-equal");
  assert(out.targets[1] !== missing && !!out.targets[1].id, "incomplete target re-created with id");
});

console.log(`ids.test.ts: ${passed} passed, ${failed} failed`);
if (failed) {
  failures.forEach((f) => console.error(f));
  (globalThis as unknown as { process?: { exit(code: number): void } }).process?.exit(1);
}
```

- [ ] **Step 2: Run test to verify it fails**

```
cd ui && npx tsx src/lib/__tests__/ids.test.ts
```
Expected: module-resolution failure — `Cannot find module '../ids'`.

- [ ] **Step 3: Implement**

**9a — `ui/src/types.ts`.**

`ExposureStep` (:349): add as first member:
```ts
export interface ExposureStep {
  // stable identity for multi-night session ledgers (sessions spec §1).
  // Optional: legacy localStorage plans lack it; store backfills via ensurePlanIds.
  id?: string;
  filter: string | null;
```
`Target` (:359): add as first member:
```ts
export interface Target {
  // stable identity for multi-night session ledgers (sessions spec §1).
  id?: string;
  name: string;
```
`SequencePlan` (:375): add after `hfr_reject_factor: number;`:
```ts
  // --- multi-night quota (sessions spec §3; additive — server defaults apply) ---
  count_mode?: "attempts" | "accepted";
  min_stars?: number;                     // star floor (0 = off)
  max_guide_rms?: number;                 // guide-RMS ceiling, arcsec (0 = off)
  max_consecutive_rejects?: number;       // per-step guard (0 = off)
  max_consecutive_rejects_night?: number; // per-night guard (0 = off)
```
`SequenceState` (:237-260): extend `end_reason` and add the sub-state after `live?`:
```ts
  // Multi-night session sub-state (sessions spec §6): present while a session
  // is running; cleared with explicit-None semantics like `schedule`.
  session?: { id: string; name: string; count_mode: string; accepted: number; target?: string };
  // Terminal reason — drives the run-complete Badge + Report end-reason icon.
  end_reason?: "complete" | "aborted" | "error" | "unsafe" | "dawn_cutoff" | "cooling_skip" | "quality";
```
Add the session types directly ABOVE `PlanRow` (:1106):
```ts
// -------------------------------------------------------- multi-night sessions
// Mirrors server sequence/session.py (sessions spec §2/§6).
export interface SessionFrame {
  id: string;
  ts: number;
  night: string;                       // report_id captured under
  target_id: string;
  step_id: string;
  path?: string;                       // ABSENT for principals w/o config.backend (§8)
  thumb: string | null;
  metrics: Record<string, number>;     // hfr, stars, guide_rms, sensor_temp_c, ...
  auto_accepted: boolean;
  override: "accept" | "reject" | null;
}

export interface Session {
  id: string;
  schema_version: number;
  name: string;
  created_ts: number;
  updated_ts: number;
  status: "active" | "dormant" | "complete" | "abandoned";
  plan: SequencePlan;                  // frozen snapshot WITH ids
  nights: string[];
  frames: SessionFrame[];
  auto_resume: boolean;
}

export interface SessionRow {
  id: string;
  name: string;
  status: "active" | "dormant" | "complete" | "abandoned";
  created_ts: number;
  updated_ts: number;
  nights: number;
  accepted: number;
  total: number;
  auto_resume: boolean;
}
```

**9b — Create `ui/src/lib/ids.ts`:**
```ts
// ids.ts — client-side stable-id generation + backfill (sessions spec §1).
// Every create path assigns ids at creation time; ensurePlanIds is the safety
// net in loadPlan/setPlan that backfills legacy localStorage plans.
import type { ExposureStep, SequencePlan, Target } from "../types";

/** 32-char lowercase hex (matches server uuid4().hex). crypto.randomUUID when
 *  available; Math.random fallback for old WebViews. */
export function uid(): string {
  const c = (globalThis as { crypto?: { randomUUID?: () => string } }).crypto;
  if (c?.randomUUID) return c.randomUUID().replace(/-/g, "");
  let out = "";
  for (let i = 0; i < 32; i++) out += "0123456789abcdef"[Math.floor(Math.random() * 16)];
  return out;
}

export function ensureStepId(s: ExposureStep): ExposureStep {
  return s.id ? s : { ...s, id: uid() };
}

export function ensureTargetIds(t: Target): Target {
  const steps = t.steps.map(ensureStepId);
  const changed = !t.id || steps.some((s, i) => s !== t.steps[i]);
  return changed ? { ...t, id: t.id ?? uid(), steps } : t;
}

/** Backfill missing target/step ids. Reference-preserving: returns the SAME
 *  object when nothing was missing (no spurious re-renders / storage churn). */
export function ensurePlanIds(p: SequencePlan): SequencePlan {
  const targets = p.targets.map(ensureTargetIds);
  const changed = targets.some((t, i) => t !== p.targets[i]);
  return changed ? { ...p, targets } : p;
}
```

**9c — Create `ui/src/api/sessions.ts`:**
```ts
// api/sessions.ts — typed wrappers for the multi-night session routes
// (sessions spec §6). Cookie auth is automatic; ApiError on non-2xx.
import { api } from "../api";
import type { SequencePlan, Session, SessionFrame, SessionRow } from "../types";

export interface SessionPatch {
  auto_resume?: boolean;
  status?: "abandoned";
  plan?: SequencePlan;
}

export interface MergeSummary {
  kept: string[];
  new: string[];
  dropped: string[];
}

export interface SessionPatchResult {
  id: string;
  status: string;
  auto_resume: boolean;
  remaining: Record<string, number>;
  merge?: MergeSummary;
}

export const listSessions = (): Promise<SessionRow[]> =>
  api.get<{ sessions: SessionRow[] }>("/api/sessions").then((r) => r.sessions);

export const getSession = (id: string): Promise<Session> =>
  api.get<Session>(`/api/sessions/${id}`);

export const resumeSession = (id: string): Promise<{ resumed: boolean; remaining: number }> =>
  api.post<{ resumed: boolean; remaining: number }>(`/api/sessions/${id}/resume`);

export const patchSession = (id: string, body: SessionPatch): Promise<SessionPatchResult> =>
  api.patch<SessionPatchResult>(`/api/sessions/${id}`, body);

export const patchFrame = (
  id: string,
  frameId: string,
  body: { override?: "accept" | "reject" | null; metrics?: Record<string, number> },
): Promise<{ frame: SessionFrame; remaining: Record<string, number> }> =>
  api.patch<{ frame: SessionFrame; remaining: Record<string, number> }>(
    `/api/sessions/${id}/frames/${frameId}`, body);

export const deleteSession = (id: string): Promise<{ deleted: string }> =>
  api.del<{ deleted: string }>(`/api/sessions/${id}`);
```

**9d — `ui/src/store.ts`.**

Add the import next to the other lib imports (:229-234):
```ts
import { ensurePlanIds } from "./lib/ids";
```
In `defaultPlan()` (:117-162), after `hfr_reject_factor: 0,` add (the comment block there already explains WHY defaults must mirror `models.SequencePlan` exactly):
```ts
    // multi-night quota (sessions spec §3) — MUST mirror models.SequencePlan
    // defaults or a UI-started run silently changes quota/guard behavior.
    count_mode: "attempts",
    min_stars: 0,
    max_guide_rms: 0,
    max_consecutive_rejects: 10,
    max_consecutive_rejects_night: 20,
```
In `loadPlan()` (:271-285), wrap the backfilled return:
```ts
      // Backfill `schedule` (C1-27) AND stable ids (sessions spec §1) onto
      // legacy plans. Default spreads FIRST so a PRESENT schedule wins.
      return ensurePlanIds({ ...parsed, targets: parsed.targets.map((t) => ({ schedule: defaultSchedule(), ...t })) });
```
In `setPlan` (:691-698), backfill before persisting:
```ts
  setPlan: (p, dirty = true) => {
    const withIds = ensurePlanIds(p);   // safety net: every write path carries ids
    try {
      localStorage.setItem(PLAN_KEY, JSON.stringify(withIds));
    } catch {
      /* quota / unavailable — keep in-memory */
    }
    set({ plan: withIds, editorDirty: dirty });
  },
```
No WS-handler change is needed: `case "sequence"` stores the whole event object (`set({ sequence: seq, ... })`), so the new `session` sub-state flows through as-is once typed.

- [ ] **Step 4: Run tests to verify pass**

```
cd ui && npx tsx src/lib/__tests__/ids.test.ts
```
Expected: `ids.test.ts: 5 passed, 0 failed`.
```
cd ui && npm run build
```
Expected: `tsc -b && vite build` completes with zero errors.

- [ ] **Step 5: Commit**

```
git add ui/src/types.ts ui/src/lib/ids.ts ui/src/api/sessions.ts ui/src/store.ts ui/src/lib/__tests__/ids.test.ts
git commit -m "$(cat <<'EOF'
feat(ui/sessions): session types, uid/ensurePlanIds backfill in store, typed /api/sessions wrappers (spec §1/§6)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01M6wy4Lkm8yAoZWJWiyv6FL
EOF
)"
```

---
### Task 10: UI plan settings advanced row + id generation at every create path

**Files:**
- Modify: `ui/src/views/SequenceView.tsx` (imports :1-24, `addTarget` :296-303, "+ step" button :741-744, Automation panel :744-835)
- Modify: `ui/src/views/AtlasView.tsx` (`panelsToTargets` :463-476)
- Modify: `ui/src/lib/planGroups.ts` (whole file — 19 lines)
- Modify: `ui/src/lib/__tests__/planGroups.test.ts` (add fresh-id test)

**Interfaces:**
- Consumes: `uid()` (Task 9), `SequencePlan` quota fields (Task 9), existing `Toggle`/`InfoDot`/`num()` in SequenceView.
- Produces: `applyStepsToGroup(targets, group, sourceSteps)` — same signature, clones now carry FRESH step ids; every target/step created in the UI carries an `id` at creation time (spec §1).

- [ ] **Step 1: Write the failing test**

Append to `ui/src/lib/__tests__/planGroups.test.ts`, before the final `console.log`:

```ts
// --- 5. sessions spec §1: clones must get FRESH step ids — a copied id would
//     alias two steps in the session ledger and double-count their quota.
test("clones get FRESH unique step ids (never the source's)", () => {
  const src = [step(), step()];
  src[0].id = "src-a";
  src[1].id = "src-b";
  const p1 = target({ name: "A", mosaic_group: "G", steps: [] });
  const p2 = target({ name: "B", mosaic_group: "G", steps: [] });
  const out = applyStepsToGroup([p1, p2], "G", src);

  const ids = out.flatMap((t) => t.steps.map((s) => s.id));
  assert(ids.every((id) => !!id), "every clone has an id");
  assert(!ids.includes("src-a") && !ids.includes("src-b"), "source ids not reused");
  assert(new Set(ids).size === ids.length, "all clone ids unique");
});
```

- [ ] **Step 2: Run test to verify it fails**

```
cd ui && npx tsx src/lib/__tests__/planGroups.test.ts
```
Expected: `planGroups.test.ts: 4 passed, 1 failed` — `x clones get FRESH unique step ids ...: source ids not reused` (the `{ ...s }` clone copies the id).

- [ ] **Step 3: Implement**

**10a — `ui/src/lib/planGroups.ts`.** Replace the whole helper body:
```ts
// planGroups.ts — pure helpers for mosaic-group plan editing (Plan panel
// "apply to all panels" — see docs/superpowers/specs/2026-07-14-mosaic-apply-
// steps-design.md). No server calls, no store access: takes/returns plain
// Target arrays so SequenceView can wrap the result in a single setPlan.
import type { ExposureStep, Target } from "../types";
import { uid } from "./ids";

/** Copy sourceSteps into every target of `group` (deep-cloned per member).
 *  Clones get FRESH step ids (sessions spec §1 — a copied id would alias two
 *  steps in a session ledger). Targets outside the group are returned
 *  untouched (same references). */
export function applyStepsToGroup(
  targets: Target[],
  group: string,
  sourceSteps: ExposureStep[],
): Target[] {
  return targets.map((t) =>
    t.mosaic_group === group
      ? { ...t, steps: sourceSteps.map((s) => ({ ...s, id: uid() })) }
      : t,
  );
}
```

**10b — `ui/src/views/SequenceView.tsx`.**

Add to the imports (:1-24):
```ts
import { uid } from "../lib/ids";
```
In `addTarget` (:296-303), give the new target + step ids:
```ts
      setPlan({
        ...plan,
        targets: [...plan.targets, {
          id: uid(),
          name: e.id, ra_hours: e.ra_hours, dec_deg: e.dec_deg,
          center: true, autofocus_first: true, calibration: false,
          steps: [{ ...DEFAULT_STEP, id: uid() }],
          schedule: defaultSchedule(),
        }],
      });
```
"+ step" button (:741-744):
```ts
                  <button className="btn tap min-h-[44px] !px-3 !text-[11px]" disabled={running}
                    onClick={() => patchTarget(ti, { steps: [...t.steps, { ...DEFAULT_STEP, id: uid() }] })}>
                    + step
                  </button>
```
In the Automation panel (:744-835), insert the advanced quota row directly after the warm-camera label (the LAST `<label>` before `</div></Panel>`):
```tsx
            {/* --- multi-night quota + reject guards (sessions spec §3/§7).
                Each numeric guard is individually disable-able; the 0 state is
                labeled "off" EXPLICITLY (user requirement). --- */}
            <div className="border-t border-line pt-3 flex flex-col gap-3">
              <label className="flex items-center justify-between gap-2">
                <span className="text-dim inline-flex items-center gap-1">
                  count = accepted frames
                  <InfoDot
                    label="About accepted-frame counting"
                    content="Each step's count becomes a quota of ACCEPTED frames: rejected frames don't count and the step keeps shooting — across nights if needed — until the quota is met. Rejected frames are kept on disk for regrading. Off = classic attempt counting."
                  />
                </span>
                <Toggle checked={(plan.count_mode ?? "attempts") === "accepted"}
                  onChange={(v) => setPlan({ ...plan, count_mode: v ? "accepted" : "attempts" })} />
              </label>
              <label className="flex items-center justify-between gap-2">
                <span className="text-dim">min stars per frame</span>
                <span className="inline-flex items-center gap-2">
                  {(plan.min_stars ?? 0) === 0 && (
                    <span className="text-[10px] uppercase tracking-widest text-dim">off</span>
                  )}
                  <input className="field !w-16 !py-1" value={plan.min_stars ?? 0}
                    onChange={(e) => setPlan({ ...plan, min_stars: Math.max(0, Math.round(num(e.target.value, plan.min_stars ?? 0))) })} />
                </span>
              </label>
              <label className="flex items-center justify-between gap-2">
                <span className="text-dim">max guide RMS (arcsec)</span>
                <span className="inline-flex items-center gap-2">
                  {(plan.max_guide_rms ?? 0) === 0 && (
                    <span className="text-[10px] uppercase tracking-widest text-dim">off</span>
                  )}
                  <input className="field !w-16 !py-1" value={plan.max_guide_rms ?? 0}
                    onChange={(e) => setPlan({ ...plan, max_guide_rms: Math.max(0, num(e.target.value, plan.max_guide_rms ?? 0)) })} />
                </span>
              </label>
              <label className="flex items-center justify-between gap-2">
                <span className="text-dim inline-flex items-center gap-1">
                  skip step after N rejects
                  <InfoDot
                    label="About the per-step reject guard"
                    content="Accepted-count mode only: after N consecutive rejected frames on one step, skip to the next step/target. The shortfall stays in the session ledger for another night."
                  />
                </span>
                <span className="inline-flex items-center gap-2">
                  {(plan.max_consecutive_rejects ?? 10) === 0 && (
                    <span className="text-[10px] uppercase tracking-widest text-dim">off</span>
                  )}
                  <input className="field !w-16 !py-1" value={plan.max_consecutive_rejects ?? 10}
                    onChange={(e) => setPlan({ ...plan, max_consecutive_rejects: Math.max(0, Math.round(num(e.target.value, plan.max_consecutive_rejects ?? 10))) })} />
                </span>
              </label>
              <label className="flex items-center justify-between gap-2">
                <span className="text-dim inline-flex items-center gap-1">
                  end night after N rejects
                  <InfoDot
                    label="About the per-night reject guard"
                    content="Accepted-count mode only: after N consecutive rejects ACROSS targets (counter resets on any accepted frame), end the night early and leave the session resumable — the proto cloud detector."
                  />
                </span>
                <span className="inline-flex items-center gap-2">
                  {(plan.max_consecutive_rejects_night ?? 20) === 0 && (
                    <span className="text-[10px] uppercase tracking-widest text-dim">off</span>
                  )}
                  <input className="field !w-16 !py-1" value={plan.max_consecutive_rejects_night ?? 20}
                    onChange={(e) => setPlan({ ...plan, max_consecutive_rejects_night: Math.max(0, Math.round(num(e.target.value, plan.max_consecutive_rejects_night ?? 20))) })} />
                </span>
              </label>
            </div>
```

**10c — `ui/src/views/AtlasView.tsx`.** Add `import { uid } from "../lib/ids";` to the imports, then in `panelsToTargets` (:463-476):
```ts
  const panelsToTargets = (panels: MosaicPanel[]): Target[] => {
    const baseName = target?.id ?? target?.name ?? "Sky";
    return panels.map((p) => ({
      id: uid(),                          // stable identity (sessions spec §1)
      name: panelCount > 1 ? `${baseName} ${p.row + 1}-${p.col + 1}` : baseName,
      ra_hours: p.ra_hours, // already %24-wrapped (server emits ra % 24)
      dec_deg: p.dec_deg,
      center: true,
      autofocus_first: p.row === 0 && p.col === 0,
      calibration: false,
      rotation_deg,
      mosaic_group: panelCount > 1 ? groupId : undefined,
      steps: [{ ...ATLAS_DEFAULT_STEP, id: uid() }],
    }));
  };
```
(Any other create path — e.g. a catalog send that funnels through `setPlan` — is covered by the Task 9 `ensurePlanIds` safety net in `setPlan`; grep `setPlan\(\{` across `ui/src` and add explicit `uid()` wherever a NEW target/step literal is built, following the three edits above.)

- [ ] **Step 4: Run tests to verify pass**

```
cd ui && npx tsx src/lib/__tests__/planGroups.test.ts
```
Expected: `planGroups.test.ts: 5 passed, 0 failed`.
```
cd ui && npm run build
```
Expected: zero errors.

- [ ] **Step 5: Commit**

```
git add ui/src/views/SequenceView.tsx ui/src/views/AtlasView.tsx ui/src/lib/planGroups.ts ui/src/lib/__tests__/planGroups.test.ts
git commit -m "$(cat <<'EOF'
feat(ui/sessions): quota + reject-guard plan settings (explicit off labels); ids at every create path; fresh clone ids (spec §1/§7)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01M6wy4Lkm8yAoZWJWiyv6FL
EOF
)"
```

---

### Task 11: UI Sessions section — cards, resume, update-from-plan, auto-resume, delete

**Files:**
- Create: `ui/src/lib/sessions.ts`, `ui/src/components/sequence/SessionsPanel.tsx`
- Modify: `ui/src/views/SequenceView.tsx` (imports; mount after the Automation `</Panel>`)
- Test: `ui/src/lib/__tests__/sessions.test.ts` (create)

**Interfaces:**
- Consumes: `listSessions/getSession/resumeSession/patchSession/deleteSession` + `SessionRow/Session` (Task 9), `ensurePlanIds` (Task 9), `confirmDialog` (`ui/src/components/ConfirmDialog.tsx`), `useCanControlMount` (`ui/src/lib/caps.ts`), store slices `plan`/`safety`/`sequence`/`showToast`, `Panel`/`Toggle` (`components/ui.tsx`), `Icon` (existing names only: `play`, `refresh`, `trash`, `alert`).
- Produces:
  - `lib/sessions.ts`: `interface TargetProgress { target_id: string; name: string; accepted: number; total: number }`, `targetProgress(plan: SequencePlan, frames: SessionFrame[]): TargetProgress[]`, `interface MergePreview { kept: number; added: number; dropped: number }`, `mergePreview(session: Session, next: SequencePlan): MergePreview`
  - `SessionsPanel` default-export component (Task 12 extends it with the Review button + drawer)

- [ ] **Step 1: Write the failing test**

Create `ui/src/lib/__tests__/sessions.test.ts`:

```ts
// sessions.test.ts — pure tests for lib/sessions.ts (session cards math).
// Inline-assert harness; runs via `npx tsx`.
import { mergePreview, targetProgress } from "../sessions";
import type { SequencePlan, Session, SessionFrame, Target } from "../../types";

let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; } catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }

const target = (id: string, stepIds: string[], count = 4): Target => ({
  id, name: `T-${id}`, ra_hours: 0, dec_deg: 0, center: true,
  autofocus_first: true, calibration: false,
  steps: stepIds.map((sid) => ({
    id: sid, filter: "L", exposure_s: 60, gain: 100, offset: 30, binning: 1,
    count, frame_type: "Light",
  })),
});

const plan = (targets: Target[]): SequencePlan => ({
  name: "P", targets, guide: true, dither_every: 3, dither_pixels: 3,
  autofocus_every: 0, cool_to: null, cool_timeout_s: 600,
  apply_filter_offsets: true, refocus_on_temp_delta_c: 0, meridian_flip: true,
  recover_guiding: true, hfr_reject_factor: 0, park_when_done: false,
  warm_cooler_when_done: false,
});

const frame = (stepId: string, over: Partial<SessionFrame> = {}): SessionFrame => ({
  id: `f-${Math.random()}`, ts: 0, night: "n1", target_id: "t1", step_id: stepId,
  thumb: null, metrics: {}, auto_accepted: true, override: null, ...over,
});

test("targetProgress counts EFFECTIVE accepted per target, capped at count", () => {
  const p = plan([target("t1", ["s1"], 3)]);
  const frames = [
    frame("s1"),                                        // accepted
    frame("s1", { auto_accepted: false }),              // rejected
    frame("s1", { auto_accepted: false, override: "accept" }),  // regraded in
    frame("s1", { override: "reject" }),                // regraded out
    frame("s1"), frame("s1"),                           // 2 more accepted (4 total)
  ];
  const [tp] = targetProgress(p, frames);
  assert(tp.accepted === 3, `capped at count: ${tp.accepted}`);
  assert(tp.total === 3, "total = step count");
  assert(tp.name === "T-t1", "carries the target name");
});

test("mergePreview: kept / added / dropped-with-frames", () => {
  const s: Session = {
    id: "sess", schema_version: 1, name: "P", created_ts: 0, updated_ts: 0,
    status: "dormant", plan: plan([target("t1", ["s1", "s2"])]),
    nights: ["n1"], frames: [frame("s1")], auto_resume: false,
  };
  // next plan keeps s1, drops s2 (no frames -> not counted), adds s3
  const next = plan([target("t1", ["s1", "s3"])]);
  const d = mergePreview(s, next);
  assert(d.kept === 1, `kept: ${d.kept}`);
  assert(d.added === 1, `added: ${d.added}`);
  assert(d.dropped === 0, "s2 had no frames -> not dropped-with-progress");
  // dropping the frame-bearing step reports it
  const d2 = mergePreview(s, plan([target("t1", ["s2"])]));
  assert(d2.dropped === 1, `dropped: ${d2.dropped}`);
});

console.log(`sessions.test.ts: ${passed} passed, ${failed} failed`);
if (failed) {
  failures.forEach((f) => console.error(f));
  (globalThis as unknown as { process?: { exit(code: number): void } }).process?.exit(1);
}
```

- [ ] **Step 2: Run test to verify it fails**

```
cd ui && npx tsx src/lib/__tests__/sessions.test.ts
```
Expected: `Cannot find module '../sessions'`.

- [ ] **Step 3: Implement**

**11a — Create `ui/src/lib/sessions.ts`:**
```ts
// sessions.ts — pure helpers for the Sessions cards (sessions spec §7).
// No store/server access: plain data in, plain data out (tsx-testable).
import type { SequencePlan, Session, SessionFrame } from "../types";

export interface TargetProgress {
  target_id: string;
  name: string;
  accepted: number;   // effective-accepted, capped per step at its count
  total: number;
}

/** Per-target accepted/total bars off the frozen session plan + ledger. */
export function targetProgress(plan: SequencePlan, frames: SessionFrame[]): TargetProgress[] {
  const byStep = new Map<string, number>();
  for (const f of frames) {
    const ok = f.override != null ? f.override === "accept" : f.auto_accepted;
    if (ok) byStep.set(f.step_id, (byStep.get(f.step_id) ?? 0) + 1);
  }
  return plan.targets.map((t) => ({
    target_id: t.id ?? t.name,
    name: t.name,
    accepted: t.steps.reduce(
      (a, s) => a + Math.min(s.count, byStep.get(s.id ?? "") ?? 0), 0),
    total: t.steps.reduce((a, s) => a + s.count, 0),
  }));
}

export interface MergePreview {
  kept: number;     // step ids present in BOTH plans (progress survives)
  added: number;    // new step ids (start at zero)
  dropped: number;  // old frame-BEARING step ids no longer in the plan
}

/** kept/added/dropped step-id diff shown BEFORE "Update from Plan" (spec §4/§7). */
export function mergePreview(session: Session, next: SequencePlan): MergePreview {
  const oldIds = new Set(session.plan.targets.flatMap((t) => t.steps.map((s) => s.id ?? "")));
  const newIds = new Set(next.targets.flatMap((t) => t.steps.map((s) => s.id ?? "")));
  const withFrames = new Set(session.frames.map((f) => f.step_id));
  let kept = 0;
  let added = 0;
  let dropped = 0;
  for (const id of newIds) {
    if (oldIds.has(id)) kept++;
    else added++;
  }
  for (const id of oldIds) {
    if (!newIds.has(id) && withFrames.has(id)) dropped++;
  }
  return { kept, added, dropped };
}
```

**11b — Create `ui/src/components/sequence/SessionsPanel.tsx`:**
```tsx
// SessionsPanel.tsx — multi-night session cards (sessions spec §7): name +
// status chip + per-target accepted/total bars; Resume (dormant), Update from
// Plan (dormant, id-safe with kept/new/dropped confirm), auto-resume arm (with
// the no-safety-monitor confirm + persistent warning chip), delete
// (confirm-then-delete, no undo — server state). Night-mode safe: existing
// tokens/classes only.
import { useCallback, useEffect, useState } from "react";
import { useStore } from "../../store";
import { Panel, Toggle } from "../ui";
import { Icon } from "../icons";
import { confirmDialog } from "../ConfirmDialog";
import { useCanControlMount } from "../../lib/caps";
import { ensurePlanIds } from "../../lib/ids";
import { mergePreview, targetProgress } from "../../lib/sessions";
import {
  deleteSession, getSession, listSessions, patchSession, resumeSession,
} from "../../api/sessions";
import type { Session, SessionRow } from "../../types";

function StatusChip({ status }: { status: SessionRow["status"] }) {
  const cls = status === "active" ? "text-good blink"
    : status === "dormant" ? "text-warn"
    : status === "complete" ? "text-accent" : "text-dim";
  return <span className={`text-[10px] tracking-widest uppercase ${cls}`}>{status}</span>;
}

export default function SessionsPanel() {
  const plan = useStore((s) => s.plan);
  const safety = useStore((s) => s.safety);
  const sequence = useStore((s) => s.sequence);
  const showToast = useStore((s) => s.showToast);
  const canControl = useCanControlMount();
  const [rows, setRows] = useState<SessionRow[]>([]);
  const [details, setDetails] = useState<Record<string, Session>>({});

  const refresh = useCallback(async () => {
    try {
      const all = (await listSessions()).filter((r) => r.status !== "abandoned");
      setRows(all);
      const loaded = await Promise.all(
        all.map((r) => getSession(r.id).catch(() => null)));
      const map: Record<string, Session> = {};
      loaded.forEach((s) => { if (s) map[s.id] = s; });
      setDetails(map);
    } catch {
      /* additive surface — a fetch failure just leaves the panel empty */
    }
  }, []);

  // on mount + whenever the run state changes (start/dormant/complete all
  // change what the cards should show), + after every action below.
  useEffect(() => { void refresh(); }, [refresh, sequence.state]);

  const noMonitor = !safety || !safety.connected;

  const act = async (label: string, fn: () => Promise<unknown>) => {
    try {
      await fn();
      await refresh();
    } catch (e) {
      showToast("error", `${label} failed: ${(e as Error).message}`);
    }
  };

  const onUpdateFromPlan = async (r: SessionRow) => {
    const s = details[r.id];
    if (!s) return;
    const next = ensurePlanIds(plan);
    const d = mergePreview(s, next);
    const ok = await confirmDialog({
      title: `Update "${r.name}" from the current plan?`,
      body: `${d.kept} step${d.kept === 1 ? "" : "s"} keep recorded progress · ` +
        `${d.added} new start at zero · ${d.dropped} with recorded frames dropped ` +
        `(their frames stay in the ledger but stop counting toward any quota).`,
      tone: d.dropped > 0 ? "danger" : "warn",
      mode: "confirm",
      confirmLabel: "Update session",
    });
    if (ok) await act("Update", () => patchSession(r.id, { plan: next }));
  };

  const onArm = async (r: SessionRow, v: boolean) => {
    if (v && noMonitor) {
      const ok = await confirmDialog({
        title: "Arm auto-resume without a safety monitor?",
        body: "No safety monitor is connected — the rig may start unattended in bad weather. A persistent warning stays on this card while armed.",
        tone: "danger",
        mode: "confirm",
        confirmLabel: "Arm anyway",
      });
      if (!ok) return;
    }
    await act("Auto-resume", () => patchSession(r.id, { auto_resume: v }));
  };

  const onDelete = async (r: SessionRow) => {
    const ok = await confirmDialog({
      title: `Delete session "${r.name}"?`,
      body: "Removes the session ledger and thumbnails. Saved FITS frames are NOT deleted. This cannot be undone.",
      tone: "danger",
      mode: "confirm",
      confirmLabel: "Delete",
    });
    if (ok) await act("Delete", () => deleteSession(r.id));
  };

  if (rows.length === 0) return null;

  return (
    <Panel title="Sessions">
      <div className="flex flex-col gap-3 text-xs">
        {rows.map((r) => {
          const s = details[r.id];
          return (
            <div key={r.id} className="border border-line bg-bg/50 p-3 flex flex-col gap-2">
              <div className="flex items-center gap-2 flex-wrap">
                <span className="font-display font-semibold text-accent tracking-wider">{r.name}</span>
                <StatusChip status={r.status} />
                <span className="mono text-[11px] text-dim">
                  {r.accepted}/{r.total} · {r.nights} night{r.nights === 1 ? "" : "s"}
                </span>
                <div className="flex-1" />
                {canControl && r.status === "dormant" && (
                  <button className="btn tap min-h-[44px] inline-flex items-center gap-1 !px-3 !text-[11px]"
                    onClick={() => void act("Resume", () => resumeSession(r.id))}>
                    <Icon name="play" size={12} /> resume
                  </button>
                )}
                {canControl && r.status === "dormant" && (
                  <button
                    className="tap min-h-[44px] inline-flex items-center gap-1 !px-3 !text-[11px]
                      border border-line2 text-dim hover:text-accent hover:border-accent/50"
                    title="Replace this session's plan with the current Plan panel (id-safe)"
                    onClick={() => void onUpdateFromPlan(r)}>
                    <Icon name="refresh" size={12} /> update from plan
                  </button>
                )}
                {canControl && r.status !== "active" && (
                  <button
                    className="tap min-h-[44px] min-w-[44px] inline-flex items-center justify-center
                      border border-bad/60 text-bad hover:bg-bad/10"
                    aria-label={`Delete session ${r.name}`}
                    title={`Delete ${r.name}`}
                    onClick={() => void onDelete(r)}>
                    <Icon name="trash" size={14} />
                  </button>
                )}
              </div>
              {s && targetProgress(s.plan, s.frames).map((tp) => (
                <div key={tp.target_id} className="flex items-center gap-2 text-[11px]">
                  <span className="text-dim w-28 truncate">{tp.name}</span>
                  <div className="flex-1 h-1.5 bg-line2/60 overflow-hidden">
                    <div className="h-full bg-accent/70"
                      style={{ width: `${tp.total ? Math.min(100, (100 * tp.accepted) / tp.total) : 0}%` }} />
                  </div>
                  <span className="mono text-dim">{tp.accepted}/{tp.total}</span>
                </div>
              ))}
              {canControl && r.status === "dormant" && (
                <label className="flex items-center justify-between gap-2">
                  <span className="text-dim">auto-resume at dusk</span>
                  <Toggle checked={r.auto_resume} onChange={(v) => void onArm(r, v)} />
                </label>
              )}
              {r.auto_resume && noMonitor && (
                <span className="text-[11px] text-warn inline-flex items-center gap-1">
                  <Icon name="alert" size={12} />
                  auto-resume armed without a safety monitor — rig may start in bad weather
                </span>
              )}
            </div>
          );
        })}
      </div>
    </Panel>
  );
}
```

**11c — `ui/src/views/SequenceView.tsx`.** Add the import:
```ts
import SessionsPanel from "../components/sequence/SessionsPanel";
```
and mount it right AFTER the Automation panel's closing `</Panel>` (the element ending at :835, i.e. the last child of the right-hand column div):
```tsx
        <SessionsPanel />
```

- [ ] **Step 4: Run tests to verify pass**

```
cd ui && npx tsx src/lib/__tests__/sessions.test.ts
```
Expected: `sessions.test.ts: 2 passed, 0 failed`.
```
cd ui && npm run build
```
Expected: zero errors (strict unused checks pass — every import in SessionsPanel is used).

- [ ] **Step 5: Commit**

```
git add ui/src/lib/sessions.ts ui/src/components/sequence/SessionsPanel.tsx ui/src/views/SequenceView.tsx ui/src/lib/__tests__/sessions.test.ts
git commit -m "$(cat <<'EOF'
feat(ui/sessions): Sessions cards — resume, id-safe update-from-plan with diff confirm, auto-resume arm + no-monitor warning, delete (spec §7)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01M6wy4Lkm8yAoZWJWiyv6FL
EOF
)"
```

---
### Task 12: UI Review drawer — frame grid, filters, bulk regrade

**Files:**
- Create: `ui/src/lib/sessionReview.ts`, `ui/src/components/sequence/SessionReviewDrawer.tsx`
- Modify: `ui/src/components/sequence/SessionsPanel.tsx` (Review button + drawer mount)
- Test: `ui/src/lib/__tests__/sessionReview.test.ts` (create)

**Interfaces:**
- Consumes: `getSession`/`patchFrame` (Task 9), thumb route `GET /api/sessions/{id}/frames/{fid}/thumb` (Task 7), `BASE` (`ui/src/lib/base.ts`), LogDrawer pattern (`ui/src/components/LogDrawer.tsx` — docked column lg+ / bottom sheet below, focus trap, Escape, focus return), `Icon` (existing names: `eye`, `check`, `x`).
- Produces:
  - `lib/sessionReview.ts`: `type Verdict = "accepted" | "rejected" | "overridden"`, `effectiveAccepted(f: SessionFrame): boolean`, `verdictOf(f: SessionFrame): Verdict`, `interface FrameFilters { target_id?: string; night?: string; verdict?: Verdict }`, `filterFrames(frames: SessionFrame[], flt: FrameFilters): SessionFrame[]`, `toggleSel(sel: string[], id: string): string[]`, `withOverride(frames: SessionFrame[], id: string, override: "accept" | "reject" | null): SessionFrame[]`
  - `SessionReviewDrawer({ id, onClose }: { id: string | null; onClose: () => void })` default-export component

- [ ] **Step 1: Write the failing test**

Create `ui/src/lib/__tests__/sessionReview.test.ts`:

```ts
// sessionReview.test.ts — pure tests for lib/sessionReview.ts (review drawer
// filtering / selection / local override). Inline-assert harness via `npx tsx`.
import {
  effectiveAccepted, filterFrames, toggleSel, verdictOf, withOverride,
} from "../sessionReview";
import type { SessionFrame } from "../../types";

let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; } catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }

let n = 0;
const frame = (over: Partial<SessionFrame> = {}): SessionFrame => ({
  id: `f${n++}`, ts: 0, night: "n1", target_id: "t1", step_id: "s1",
  thumb: null, metrics: { hfr: 2 }, auto_accepted: true, override: null, ...over,
});

test("verdict + effective acceptance", () => {
  assert(verdictOf(frame()) === "accepted", "auto accepted");
  assert(verdictOf(frame({ auto_accepted: false })) === "rejected", "auto rejected");
  assert(verdictOf(frame({ override: "reject" })) === "overridden", "override badge wins");
  assert(effectiveAccepted(frame({ auto_accepted: false, override: "accept" })), "override accept counts");
  assert(!effectiveAccepted(frame({ override: "reject" })), "override reject discounts");
});

test("filterFrames: target / night / verdict semantics", () => {
  const fs = [
    frame({ target_id: "t1", night: "n1" }),                          // accepted
    frame({ target_id: "t2", night: "n2", auto_accepted: false }),    // rejected
    frame({ target_id: "t1", night: "n2", override: "reject" }),      // overridden (eff. rejected)
  ];
  assert(filterFrames(fs, { target_id: "t1" }).length === 2, "target filter");
  assert(filterFrames(fs, { night: "n2" }).length === 2, "night filter");
  assert(filterFrames(fs, { verdict: "accepted" }).length === 1, "accepted = EFFECTIVE");
  assert(filterFrames(fs, { verdict: "rejected" }).length === 2, "rejected = EFFECTIVE (incl. override)");
  assert(filterFrames(fs, { verdict: "overridden" }).length === 1, "overridden = has override");
  assert(filterFrames(fs, {}).length === 3, "no filters = all");
});

test("toggleSel is a pure toggle", () => {
  let sel: string[] = [];
  sel = toggleSel(sel, "a");
  assert(sel.includes("a"), "added");
  const before = sel;
  sel = toggleSel(sel, "a");
  assert(!sel.includes("a") && before.includes("a"), "removed, input untouched");
});

test("withOverride touches only the matching frame", () => {
  const fs = [frame(), frame()];
  const out = withOverride(fs, fs[0].id, "reject");
  assert(out[0].override === "reject", "target frame updated");
  assert(out[1] === fs[1], "other frame reference-equal");
  assert(fs[0].override === null, "input not mutated");
});

console.log(`sessionReview.test.ts: ${passed} passed, ${failed} failed`);
if (failed) {
  failures.forEach((f) => console.error(f));
  (globalThis as unknown as { process?: { exit(code: number): void } }).process?.exit(1);
}
```

- [ ] **Step 2: Run test to verify it fails**

```
cd ui && npx tsx src/lib/__tests__/sessionReview.test.ts
```
Expected: `Cannot find module '../sessionReview'`.

- [ ] **Step 3: Implement**

**12a — Create `ui/src/lib/sessionReview.ts`:**
```ts
// sessionReview.ts — pure helpers for the review drawer (sessions spec §7):
// verdicts, grid filtering, bulk-selection reducer, local override apply.
import type { SessionFrame } from "../types";

export type Verdict = "accepted" | "rejected" | "overridden";

/** Effective acceptance = override if set else auto_accepted (spec §2). */
export function effectiveAccepted(f: SessionFrame): boolean {
  return f.override != null ? f.override === "accept" : f.auto_accepted;
}

/** Badge verdict: an override always shows as "overridden". */
export function verdictOf(f: SessionFrame): Verdict {
  if (f.override != null) return "overridden";
  return f.auto_accepted ? "accepted" : "rejected";
}

export interface FrameFilters {
  target_id?: string;
  night?: string;
  verdict?: Verdict;
}

/** Filter semantics: accepted/rejected filter by EFFECTIVE acceptance;
 *  overridden = any frame carrying an override. */
export function filterFrames(frames: SessionFrame[], flt: FrameFilters): SessionFrame[] {
  return frames.filter((f) => {
    if (flt.target_id && f.target_id !== flt.target_id) return false;
    if (flt.night && f.night !== flt.night) return false;
    if (flt.verdict === "accepted" && !effectiveAccepted(f)) return false;
    if (flt.verdict === "rejected" && effectiveAccepted(f)) return false;
    if (flt.verdict === "overridden" && f.override == null) return false;
    return true;
  });
}

/** Toggle `id` in the selection (pure — returns a new array). */
export function toggleSel(sel: string[], id: string): string[] {
  return sel.includes(id) ? sel.filter((x) => x !== id) : [...sel, id];
}

/** Apply an override locally after a successful PATCH (pure; untouched frames
 *  stay reference-equal for React re-render scoping). */
export function withOverride(
  frames: SessionFrame[], id: string,
  override: "accept" | "reject" | null,
): SessionFrame[] {
  return frames.map((f) => (f.id === id ? { ...f, override } : f));
}
```

**12b — Create `ui/src/components/sequence/SessionReviewDrawer.tsx`:**
```tsx
// SessionReviewDrawer.tsx — frame-grid review for one session (sessions spec
// §7). LogDrawer pattern: lg+ docked right column, bottom sheet below lg;
// role="dialog" aria-modal="false", Escape, focus trap, focus return.
// Thumbs + metrics only (no full-size preview — out of scope v1).
import { useEffect, useRef, useState } from "react";
import { Icon } from "../icons";
import { BASE } from "../../lib/base";
import { getSession, patchFrame } from "../../api/sessions";
import { filterFrames, toggleSel, verdictOf, withOverride } from "../../lib/sessionReview";
import type { FrameFilters } from "../../lib/sessionReview";
import type { Session } from "../../types";

export default function SessionReviewDrawer({ id, onClose }: {
  id: string | null;
  onClose: () => void;
}) {
  const [session, setSession] = useState<Session | null>(null);
  const [flt, setFlt] = useState<FrameFilters>({});
  const [sel, setSel] = useState<string[]>([]);
  const [remaining, setRemaining] = useState<Record<string, number> | null>(null);
  const [busy, setBusy] = useState(false);
  const deskRef = useRef<HTMLDivElement>(null);
  const sheetRef = useRef<HTMLDivElement>(null);
  const returnFocusRef = useRef<HTMLElement | null>(null);

  useEffect(() => {
    if (!id) {
      setSession(null);
      setSel([]);
      setRemaining(null);
      setFlt({});
      return;
    }
    void getSession(id).then(setSession).catch(() => setSession(null));
  }, [id]);

  useEffect(() => {
    if (!id) return;
    returnFocusRef.current = (document.activeElement as HTMLElement) ?? null;
    const isVisible = (el: HTMLElement | null) => !!el && el.getClientRects().length > 0;
    const node = isVisible(deskRef.current) ? deskRef.current : sheetRef.current;
    const focusables = () =>
      node
        ? Array.from(
            node.querySelectorAll<HTMLElement>(
              'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])',
            ),
          ).filter((el) => !el.hasAttribute("disabled"))
        : [];
    focusables()[0]?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        onClose();
        return;
      }
      if (e.key !== "Tab") return;
      const items = focusables();
      if (items.length === 0) return;
      const first = items[0];
      const last = items[items.length - 1];
      const active = document.activeElement;
      if (e.shiftKey && active === first) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && active === last) {
        e.preventDefault();
        first.focus();
      }
    };
    document.addEventListener("keydown", onKey, true);
    return () => {
      document.removeEventListener("keydown", onKey, true);
      returnFocusRef.current?.focus?.();
    };
  }, [id, onClose]);

  if (!id || !session) return null;

  const frames = filterFrames(session.frames, flt);
  const nights = [...new Set(session.frames.map((f) => f.night))];
  const targets = session.plan.targets;
  const filterOf = (stepId: string): string => {
    for (const t of targets) {
      for (const s of t.steps) if (s.id === stepId) return s.filter ?? "—";
    }
    return "—";
  };
  const targetName = (tid: string): string =>
    targets.find((t) => t.id === tid)?.name ?? tid.slice(0, 8);
  const remainingTotal = remaining
    ? Object.values(remaining).reduce((a, b) => a + b, 0)
    : null;

  const bulk = async (override: "accept" | "reject") => {
    if (busy || sel.length === 0) return;
    setBusy(true);
    let frames2 = session.frames;
    let rem: Record<string, number> | null = null;
    try {
      for (const fid of sel) {
        const res = await patchFrame(session.id, fid, { override });
        frames2 = withOverride(frames2, fid, override);
        rem = res.remaining;
      }
    } catch {
      /* partial bulk: keep what applied — header stays truthful below */
    }
    setSession({ ...session, frames: frames2 });
    if (rem) setRemaining(rem);   // live per-step remaining from the PATCH
    setSel([]);
    setBusy(false);
  };

  const header = (
    <div className="flex items-center gap-2 pb-2 flex-wrap">
      <span className="font-display font-semibold text-accent tracking-wider">
        Review · {session.name}
      </span>
      {remainingTotal != null && (
        <span className="mono text-[11px] text-dim">{remainingTotal} remaining</span>
      )}
      <div className="flex-1" />
      <button
        className="tap min-h-[44px] min-w-[44px] inline-flex items-center justify-center text-dim hover:text-accent"
        aria-label="Close review" onClick={onClose}>
        <Icon name="x" size={16} />
      </button>
    </div>
  );

  const body = (
    <>
      <div className="flex items-center gap-2 pb-2 flex-wrap text-[11px]">
        <select className="field !py-1 !w-28" value={flt.target_id ?? ""}
          aria-label="Filter by target"
          onChange={(e) => setFlt({ ...flt, target_id: e.target.value || undefined })}>
          <option value="">all targets</option>
          {targets.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
        </select>
        <select className="field !py-1 !w-32" value={flt.night ?? ""}
          aria-label="Filter by night"
          onChange={(e) => setFlt({ ...flt, night: e.target.value || undefined })}>
          <option value="">all nights</option>
          {nights.map((nx) => <option key={nx} value={nx}>{nx}</option>)}
        </select>
        <select className="field !py-1 !w-28" value={flt.verdict ?? ""}
          aria-label="Filter by verdict"
          onChange={(e) => setFlt({ ...flt, verdict: (e.target.value || undefined) as FrameFilters["verdict"] })}>
          <option value="">all verdicts</option>
          <option value="accepted">accepted</option>
          <option value="rejected">rejected</option>
          <option value="overridden">overridden</option>
        </select>
        <div className="flex-1" />
        <button className="btn tap min-h-[44px] !px-3 !text-[11px]"
          disabled={busy || sel.length === 0} onClick={() => void bulk("accept")}>
          <Icon name="check" size={12} /> mark accepted ({sel.length})
        </button>
        <button
          className="tap min-h-[44px] !px-3 !text-[11px] border border-bad/60 text-bad hover:bg-bad/10 disabled:opacity-40"
          disabled={busy || sel.length === 0} onClick={() => void bulk("reject")}>
          <Icon name="x" size={12} /> mark rejected
        </button>
      </div>
      <div className="grid grid-cols-2 sm:grid-cols-3 gap-2 overflow-y-auto flex-1">
        {frames.map((f) => {
          const v = verdictOf(f);
          const selected = sel.includes(f.id);
          return (
            <button key={f.id}
              className={`text-left border p-1 flex flex-col gap-1 ${selected ? "border-accent" : "border-line"}`}
              aria-pressed={selected}
              aria-label={`Select frame ${f.id}`}
              onClick={() => setSel(toggleSel(sel, f.id))}>
              {f.thumb ? (
                <img src={`${BASE}/api/sessions/${session.id}/frames/${f.id}/thumb`}
                  alt="" className="w-full aspect-video object-cover" loading="lazy" />
              ) : (
                <div className="w-full aspect-video bg-line2/40 flex items-center justify-center text-dim text-[10px]">
                  no thumb
                </div>
              )}
              <span className="mono text-[10px] text-dim">
                {targetName(f.target_id)} · {filterOf(f.step_id)} · {f.night.slice(-6)}
              </span>
              <span className="mono text-[10px] text-dim">
                HFR {f.metrics.hfr != null ? f.metrics.hfr.toFixed(2) : "—"} ·
                ★{f.metrics.stars != null ? Math.round(f.metrics.stars) : "—"} ·
                RMS {f.metrics.guide_rms != null ? f.metrics.guide_rms.toFixed(2) : "—"}
              </span>
              <span className={`text-[10px] uppercase tracking-widest ${
                v === "rejected" ? "text-bad" : v === "overridden" ? "text-warn" : "text-good"}`}>
                {v}{f.override ? ` (${f.override})` : ""}
              </span>
            </button>
          );
        })}
      </div>
    </>
  );

  return (
    <>
      {/* lg+ docked right column (LogDrawer precedent) */}
      <aside ref={deskRef} role="dialog" aria-modal="false" aria-label="Session review"
        className="hidden lg:flex flex-col w-[420px] border-l border-line bg-raise/60
          backdrop-blur p-3 overflow-y-auto shrink-0 fixed right-0 top-0 bottom-0 z-40">
        {header}
        {body}
      </aside>
      {/* below lg: bottom sheet; tap-catcher above only, no scrim */}
      <div className="lg:hidden">
        <div className="fixed inset-x-0 top-0 bottom-[60vh] z-30" onClick={onClose}
          aria-hidden="true" />
        <div ref={sheetRef} role="dialog" aria-modal="false" aria-label="Session review"
          className="fixed inset-x-0 bottom-0 z-40 h-[60vh] flex flex-col
            border-t border-line2 bg-raise/95 backdrop-blur p-3 sheet-enter">
          {header}
          {body}
        </div>
      </div>
    </>
  );
}
```

**12c — `ui/src/components/sequence/SessionsPanel.tsx`.** Wire the drawer in:
- Add imports:
```ts
import SessionReviewDrawer from "./SessionReviewDrawer";
```
- Add state next to `details`:
```ts
  const [reviewId, setReviewId] = useState<string | null>(null);
```
- Add a Review button in the card's button row, BEFORE the delete button (available for every status — reads are safe; regrades 409 server-side while running):
```tsx
                <button
                  className="tap min-h-[44px] inline-flex items-center gap-1 !px-3 !text-[11px]
                    border border-line2 text-dim hover:text-accent hover:border-accent/50"
                  title={`Review frames of ${r.name}`}
                  onClick={() => setReviewId(r.id)}>
                  <Icon name="eye" size={12} /> review
                </button>
```
- Wrap the return in a fragment and mount the drawer (two exact edits to the Task 11 component — the card list between them is byte-unchanged). Replace the opening:
```tsx
  return (
    <Panel title="Sessions">
```
with:
```tsx
  return (
    <>
      <Panel title="Sessions">
```
and replace the component's closing:
```tsx
    </Panel>
  );
}
```
with:
```tsx
      </Panel>
      <SessionReviewDrawer id={reviewId}
        onClose={() => { setReviewId(null); void refresh(); }} />
    </>
  );
}
```
(re-indent the Panel's children one level to match; `refresh` on close so regrades update the card bars.)

- [ ] **Step 4: Run tests to verify pass**

```
cd ui && npx tsx src/lib/__tests__/sessionReview.test.ts
```
Expected: `sessionReview.test.ts: 4 passed, 0 failed`.
```
cd ui && npm run build
```
Expected: zero errors.

- [ ] **Step 5: Commit**

```
git add ui/src/lib/sessionReview.ts ui/src/components/sequence/SessionReviewDrawer.tsx ui/src/components/sequence/SessionsPanel.tsx ui/src/lib/__tests__/sessionReview.test.ts
git commit -m "$(cat <<'EOF'
feat(ui/sessions): review drawer — thumb grid, target/night/verdict filters, bulk regrade with live remaining (spec §7)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01M6wy4Lkm8yAoZWJWiyv6FL
EOF
)"
```

---

### Task 13: UI plan library panel (greenfield)

**Files:**
- Create: `ui/src/lib/planFile.ts`, `ui/src/components/sequence/PlanLibraryPanel.tsx`
- Modify: `ui/src/views/SequenceView.tsx` (import + mount after `<SessionsPanel />`)
- Test: `ui/src/lib/__tests__/planFile.test.ts` (create)

**Interfaces:**
- Consumes: existing server routes `GET/POST /api/plans`, `GET /api/plans/{id}`, `DELETE /api/plans/{id}`, `GET /api/plans/{id}/export`, `POST /api/plans/import` (app.py :1634-1703 — save body `{plan, id?, overwrite?}`, 409 `code:"name_collision"`); `PlanRow` type (types.ts :1106); `api`/`ApiError`; `confirmDialog`; `useCanControlCapture` (POST/DELETE are `control.capture`); `BASE`; store `plan`/`setPlan`/`showToast` (setPlan runs `ensurePlanIds`, so a loaded library plan gets ids client-side even before its next save).
- Produces: `parsePlanFile(text: string): Record<string, unknown>` (throws `Error` with a friendly message); `PlanLibraryPanel` default-export component.

- [ ] **Step 1: Write the failing test**

Create `ui/src/lib/__tests__/planFile.test.ts`:

```ts
// planFile.test.ts — pure tests for lib/planFile.ts (plan-library import
// parsing). Inline-assert harness via `npx tsx`.
import { parsePlanFile } from "../planFile";

let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; } catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }

test("parses a valid export envelope", () => {
  const raw = parsePlanFile('{"schema_version": 1, "name": "P", "plan": {"name": "P"}}');
  assert(raw.name === "P", "envelope fields readable");
});

test("rejects non-JSON with a friendly message", () => {
  let msg = "";
  try { parsePlanFile("not json {"); } catch (e) { msg = (e as Error).message; }
  assert(msg === "not a JSON file", `message: ${msg}`);
});

test("rejects arrays and primitives", () => {
  for (const bad of ["[1,2]", "42", '"str"', "null"]) {
    let threw = false;
    try { parsePlanFile(bad); } catch { threw = true; }
    assert(threw, `rejected: ${bad}`);
  }
});

console.log(`planFile.test.ts: ${passed} passed, ${failed} failed`);
if (failed) {
  failures.forEach((f) => console.error(f));
  (globalThis as unknown as { process?: { exit(code: number): void } }).process?.exit(1);
}
```

- [ ] **Step 2: Run test to verify it fails**

```
cd ui && npx tsx src/lib/__tests__/planFile.test.ts
```
Expected: `Cannot find module '../planFile'`.

- [ ] **Step 3: Implement**

**13a — Create `ui/src/lib/planFile.ts`:**
```ts
// planFile.ts — plan-library import-file parsing (sessions spec §7). The
// server does the real schema validation (POST /api/plans/import → 422 with
// version_too_new/invalid codes); this only guards the obvious non-files so
// the user gets an instant, friendly error without a round-trip.
export function parsePlanFile(text: string): Record<string, unknown> {
  let raw: unknown;
  try {
    raw = JSON.parse(text);
  } catch {
    throw new Error("not a JSON file");
  }
  if (raw === null || typeof raw !== "object" || Array.isArray(raw)) {
    throw new Error("not an AstroDeck plan file");
  }
  return raw as Record<string, unknown>;
}
```

**13b — Create `ui/src/components/sequence/PlanLibraryPanel.tsx`:**
```tsx
// PlanLibraryPanel.tsx — save/list/load/delete/export/import for the server
// plan library (sessions spec §7). GREENFIELD: /api/plans had zero client
// consumers before this panel — minimal compact section, no new patterns.
import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError } from "../../api";
import { useStore } from "../../store";
import { Panel } from "../ui";
import { Icon } from "../icons";
import { confirmDialog } from "../ConfirmDialog";
import { BASE } from "../../lib/base";
import { parsePlanFile } from "../../lib/planFile";
import { useCanControlCapture } from "../../lib/caps";
import type { PlanRow, SequencePlan } from "../../types";

export default function PlanLibraryPanel() {
  const plan = useStore((s) => s.plan);
  const setPlan = useStore((s) => s.setPlan);
  const showToast = useStore((s) => s.showToast);
  const canWrite = useCanControlCapture();
  const [rows, setRows] = useState<PlanRow[]>([]);
  const fileRef = useRef<HTMLInputElement>(null);

  const refresh = useCallback(async () => {
    try {
      setRows(await api.get<PlanRow[]>("/api/plans"));
    } catch {
      /* list is non-critical; panel just shows empty */
    }
  }, []);
  useEffect(() => { void refresh(); }, [refresh]);

  const save = async () => {
    try {
      await api.post("/api/plans", { plan });
      showToast("info", `Saved plan '${plan.name}'`);
      await refresh();
    } catch (e) {
      // surface the server's name-collision code (spec §7) with a way through
      if (e instanceof ApiError && e.code === "name_collision") {
        const ok = await confirmDialog({
          title: `A plan named '${plan.name}' already exists`,
          body: "Save anyway as a second copy with the same name?",
          tone: "warn",
          mode: "confirm",
          confirmLabel: "Save anyway",
        });
        if (!ok) return;
        try {
          await api.post("/api/plans", { plan, overwrite: true });
          await refresh();
        } catch (e2) {
          showToast("error", (e2 as Error).message);
        }
        return;
      }
      showToast("error", (e as Error).message);
    }
  };

  const load = async (row: PlanRow) => {
    const ok = await confirmDialog({
      title: `Load '${row.name}'?`,
      body: "Replaces the current Plan panel contents.",
      tone: "warn",
      mode: "confirm",
      confirmLabel: "Load",
    });
    if (!ok) return;
    try {
      setPlan(await api.get<SequencePlan>(`/api/plans/${row.id}`));
    } catch (e) {
      showToast("error", (e as Error).message);
    }
  };

  const del = async (row: PlanRow) => {
    const ok = await confirmDialog({
      title: `Delete saved plan '${row.name}'?`,
      tone: "danger",
      mode: "confirm",
      confirmLabel: "Delete",
    });
    if (!ok) return;
    try {
      await api.del(`/api/plans/${row.id}`);
      await refresh();
    } catch (e) {
      showToast("error", (e as Error).message);
    }
  };

  const exportRow = (row: PlanRow) => {
    // anchor download: Content-Disposition names the file; cookie auth rides
    // along on the same-origin navigation.
    const a = document.createElement("a");
    a.href = `${BASE}/api/plans/${row.id}/export`;
    a.download = "";
    document.body.appendChild(a);
    a.click();
    a.remove();
  };

  const importFile = async (file: File) => {
    try {
      const raw = parsePlanFile(await file.text());
      await api.post("/api/plans/import", raw);
      showToast("info", "Plan imported");
      await refresh();
    } catch (e) {
      // ApiError carries the server's version_too_new/invalid detail verbatim
      showToast("error", `Import failed: ${(e as Error).message}`);
    }
  };

  return (
    <Panel title="Plan library" right={canWrite ? (
      <div className="inline-flex items-center gap-1.5">
        <button className="btn tap min-h-[44px] !px-3 !text-[11px]"
          onClick={() => void save()}>
          save current
        </button>
        <button
          className="tap min-h-[44px] !px-3 !text-[11px] border border-line2 text-dim
            hover:text-accent hover:border-accent/50"
          onClick={() => fileRef.current?.click()}>
          import
        </button>
        <input ref={fileRef} type="file" accept=".json,application/json"
          className="hidden" aria-label="Import a plan file"
          onChange={(e) => {
            const f = e.target.files?.[0];
            e.target.value = "";
            if (f) void importFile(f);
          }} />
      </div>
    ) : undefined}>
      {rows.length === 0 ? (
        <p className="text-[11px] text-dim">No saved plans yet.</p>
      ) : (
        <div className="flex flex-col gap-1.5 text-xs">
          {rows.map((r) => (
            <div key={r.id}
              className="flex items-center gap-2 border border-line bg-bg/50 px-2 py-1.5">
              <span className="text-ink truncate">{r.name}</span>
              <span className="mono text-[10px] text-dim">
                {r.targets}t · {r.frames}f · {Math.round(r.integration_min)}m
              </span>
              <div className="flex-1" />
              <button className="tap min-h-[44px] !px-2 !text-[11px] text-dim hover:text-accent"
                onClick={() => void load(r)}>load</button>
              <button className="tap min-h-[44px] !px-2 !text-[11px] text-dim hover:text-accent"
                onClick={() => exportRow(r)}>export</button>
              {canWrite && (
                <button
                  className="tap min-h-[44px] min-w-[44px] inline-flex items-center justify-center text-bad hover:bg-bad/10"
                  aria-label={`Delete plan ${r.name}`} onClick={() => void del(r)}>
                  <Icon name="trash" size={13} />
                </button>
              )}
            </div>
          ))}
        </div>
      )}
    </Panel>
  );
}
```

**13c — `ui/src/views/SequenceView.tsx`.** Add the import:
```ts
import PlanLibraryPanel from "../components/sequence/PlanLibraryPanel";
```
and mount directly after `<SessionsPanel />` (Task 11's anchor):
```tsx
        <SessionsPanel />
        <PlanLibraryPanel />
```

- [ ] **Step 4: Run tests to verify pass**

```
cd ui && npx tsx src/lib/__tests__/planFile.test.ts
```
Expected: `planFile.test.ts: 3 passed, 0 failed`.
```
cd ui && npm run build
```
Expected: zero errors.

- [ ] **Step 5: Commit**

```
git add ui/src/lib/planFile.ts ui/src/components/sequence/PlanLibraryPanel.tsx ui/src/views/SequenceView.tsx ui/src/lib/__tests__/planFile.test.ts
git commit -m "$(cat <<'EOF'
feat(ui/plans): plan library panel — save/load/delete + export download + import with error surfacing (spec §7)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01M6wy4Lkm8yAoZWJWiyv6FL
EOF
)"
```

---

### Task 14: Final verification — full builds + full suites

**Files:** none created/modified unless a failure is found (fixes go to the file that owns the defect, committed separately).

**Interfaces:**
- Consumes: everything above.
- Produces: a green tree — the definition of done for this plan.

- [ ] **Step 1: Write the failing test** — N/A (verification task; the "tests" are the full suites below).

- [ ] **Step 2: Run the UI build**
```
cd ui && npm run build
```
Expected: `tsc -b && vite build` — zero TypeScript errors, bundle emitted.

- [ ] **Step 3: Run ALL tsx lib tests** (manually — never CI; `caps.test.ts` is known-broken standalone, skip it)
```
cd ui && npx tsx src/lib/__tests__/ids.test.ts
cd ui && npx tsx src/lib/__tests__/planGroups.test.ts
cd ui && npx tsx src/lib/__tests__/sessions.test.ts
cd ui && npx tsx src/lib/__tests__/sessionReview.test.ts
cd ui && npx tsx src/lib/__tests__/planFile.test.ts
```
Expected, in order: `5 passed`, `5 passed`, `2 passed`, `4 passed`, `3 passed` — all `0 failed`.

- [ ] **Step 4: Run the FULL server suite**
```
cd server && ./.venv/Scripts/python.exe -m pytest -q
```
Expected: `1018 passed` (980 baseline + 38 new across Tasks 1-8), 0 failures, 0 errors. The run takes ~12-13 minutes — if the harness auto-backgrounds it, re-run immediately in the foreground; do not park. If ANY test fails: fix the owning file, re-run the affected file then the full suite, and commit the fix as `fix(sessions): <what> found in final verification` with the standard trailer.

- [ ] **Step 5: Commit** — only if Step 2-4 produced fixes (each already committed per Step 4's instruction). Otherwise nothing to commit; the tree is already green and fully committed. Verify:
```
git status --short
git log --oneline -14
```
Expected: clean tree; the 13 task commits (+ any fix commits) on `main`. Do NOT push.

---

## Spec coverage sweep (authority: docs/superpowers/specs/2026-07-14-multi-night-sessions-design.md)

| Spec § | Requirement | Task(s) |
|---|---|---|
| §1 stable IDs | server fields + backfill; client generation at create paths + loadPlan/setPlan backfill; fresh clone ids; `_done` re-keyed `targetId:stepId`; plan-library files carry ids (pydantic defaults) | 1, 3, 9, 10 |
| §2 session entity | SessionFrame/Session verbatim fields; store list/load/save/delete; MAX_SESSIONS=200 prune policy; every start creates a Session; retire `.sequence_resume.json` + migration; recover routes re-backed | 2, 3 |
| §3 quota engine | count_mode; star floor + RMS ceiling AND HFR factor; rejects kept+recorded in accepted mode; hfr_reject_action attempts-only; per-step + per-night guards (reset-on-accept, cross-target, end_reason="quality", alert); thumbnails per record; per-frame atomic session save; accepted-based progress/ETA | 4, 6 |
| §4 boundaries/dormancy/resume | terminal complete-vs-dormant cause mapping; manual resume via full start path; done_map seeding; fresh report per night; windows re-resolve; boot sweep; id-safe dormant plan edits (PATCH, 409 running, merge semantics); graceful resume after any interruption (record-time-only ledger writes) | 3, 5, 7 |
| §5 auto-resume | per-session auto_resume, server-enforced singleton; ResumeArm 60s service; window-open via schedule resolve; refusal alert + 10-min retry until dawn + give-up alert; success alert (run_start edge); disarm stops interest; no-safety-monitor confirm + persistent chip; resume_veto() hook (v1 None) | 7, 8, 11 |
| §6 API surface | all 7 routes exactly incl. caps; metrics float-merge returns remaining; DELETE never touches FITS; WS session sub-state with explicit-None clear | 7 |
| §7 UI | Sessions section (cards, bars, Resume, Update-from-Plan diff, auto-resume toggle+warn, Review, delete confirm); review drawer (grid, thumb, HFR/stars/RMS, night, filter, verdict badge, filters, bulk overrides, live remaining); plan settings advanced row w/ explicit off labels; plan library export/import; progress components unchanged (server computes) | 10, 11, 12, 13 |
| §8 privacy/RBAC | frame-path strip for non-config.backend; thumbs under preview cap; no location data added | 7 |
| §9 testing | every listed server test exists (store/backfill/quota/guards/dormancy/sweep/resume-reorder/PATCH merge/metrics/ResumeArm-injected-clock/RBAC/redaction/thumb-cap); UI pure-helper tsx tests (quota math via sessions.ts, review filtering + selection reducer, id backfill) | 1-8, 9-13 |
| §10 PixInsight fwd-compat | open metrics dict; override as external write point via frame PATCH; path in ledger (redacted per §8) | 2, 7 |
| Out of scope | running-session plan edits (409), full-size preview, session export/import, eccentricity, B/C/D | respected throughout |
