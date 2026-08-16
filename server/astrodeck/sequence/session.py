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
    # CONSECUTIVE CRASHES OF THIS SESSION, and it lives here rather than in
    # ResumeArm because a crash can take the process with it - a counter in
    # memory would reset on exactly the restart it is meant to be counting.
    #
    # Incremented when a run of this session ends with end_reason="error", reset
    # to 0 by ANY other ending. A weather veto, a recovery hold or a refusal to
    # start is NOT a crash and must never land here: those are the system
    # working, and counting them would park the mount three cloudy holds into a
    # night that was going to clear.
    crash_resumes: int = 0

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


def _safe_unlink(path: Path) -> None:
    """``path.unlink(missing_ok=True)`` that also swallows ``OSError`` (a
    locked/AV-held file on Windows) — legacy-file cleanup must never crash
    the boot migration, on any exit path."""
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def migrate_legacy_resume() -> Session | None:
    """One-shot boot migration of the retired ``.sequence_resume.json`` (spec
    §2): positional ``"ti:si"`` counts map onto the ids pydantic backfills
    during plan validation (position-preserving), synthesized as N accepted
    placeholder frames per step so ``done_map()`` seeds a resume at the exact
    same counts. The legacy file is deleted afterward on every exit path —
    even when unparseable — it is single-slot garbage either way, and this
    function must never raise on a corrupt legacy file. A bad top-level
    ``ts`` falls back to ``now()``; a bad individual ``done`` entry (bad key
    or non-numeric count) is skipped and the rest of the migration proceeds."""
    legacy = _hubmod.CAPTURE_DIR / ".sequence_resume.json"
    try:
        raw = json.loads(legacy.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        _safe_unlink(legacy)
        return None
    try:
        plan = SequencePlan.model_validate(raw.get("plan") or {})
    except Exception:
        _safe_unlink(legacy)
        return None
    report_id = str(raw.get("report_id") or "")
    now = time.time()
    try:
        created_ts = float(raw.get("ts") or now)
    except (TypeError, ValueError):
        created_ts = now
    s = Session(name=plan.name or "Tonight",
                created_ts=created_ts, updated_ts=now,
                status="dormant", plan=plan,
                nights=[report_id] if report_id else [])
    for key, count in (raw.get("done") or {}).items():
        try:
            ti, si = (int(x) for x in str(key).split(":", 1))
            target = plan.targets[ti]
            step = target.steps[si]
            n = int(count)
        except (TypeError, ValueError, IndexError):
            continue
        for _ in range(n):
            s.frames.append(SessionFrame(
                ts=now, night=report_id, target_id=target.id,
                step_id=step.id, path="", auto_accepted=True))
    session_store.save(s)
    _safe_unlink(legacy)
    return s
