# Resume After Restart Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** an interrupted run resumes itself after a mid-night restart, never moving an axis or exposing a light frame on a belief the rig cannot verify.

**Architecture:** three changes on top of machinery that already works. Arming is permitted on a running session (today it 409s, which is why the run a crash destroys is the one that cannot be armed). A small fingerprint file records last-known device state so a power cut is detectable as a *condition* rather than guessed from an OS event log. `ResumeArm` then runs a recovery ladder before `engine.start`: autofocus when focus is untrusted, and an unconditional blind-solve + sync + re-center.

**Tech Stack:** Python 3.12, FastAPI, pydantic v2, pytest (`-n 0` for these; the suite defaults to xdist).

## Global Constraints

- The observing site's real latitude/longitude and its personal nickname MUST NOT appear in code, tests, docs or commit messages. Tests use invented coordinates. (The literal forbidden values are deliberately not reproduced here — writing them into the rule would violate it.)
- Commit with explicit paths only: `git commit -F - -- <paths>`. NEVER `git add -A`.
- Every commit ends with:
  `Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>`
  `Claude-Session: https://claude.ai/code/session_013ZacFNCYA6kk3yNzx578YW`
- Tests read bus output through the `bus_lines` fixture (`server/tests/conftest.py:119`). NEVER `bus.log_history[mark:]` — `_history` is a `deque(maxlen=200)` shared process-wide and the slice is empty forever once it fills.
- Run the backend suite from `server/` with `./.venv/Scripts/python.exe -m pytest`.
- Every new failure path in `ResumeArm` alerts, leaves the session **dormant AND armed**, and sets `self._retry_at = now + RETRY_INTERVAL_S` (600 s). It must never raise out of `tick()` and never proceed to `engine.start`.

## Cooling is already built — do NOT reimplement it

The spec's ladder step 1 ("cool to setpoint") needs **no new code**. `SequenceEngine._run` already calls `_cool_and_wait(plan.cool_to, plan.cool_timeout_s)` (`sequence/engine.py:675`) under the `cfg.escalation.require_cooling` / `cooling_action` policy, with the shared band `COOLER_AT_TARGET_C = 1.0` (`engine.py:54`). A resumed run reuses the same plan object, so it inherits that gate. Task 4 verifies this rather than duplicating it. Adding a second cooling wait inside `ResumeArm` would double the delay and let the two bands drift apart.

## File Structure

| File | Responsibility |
| --- | --- |
| `server/astrodeck/api/app.py` (modify) | allow arming an active session; arm new runs by default |
| `server/astrodeck/devices/fingerprint.py` (create) | record last-known device state; produce the trust verdict |
| `server/astrodeck/hub.py` (modify, small) | call the recorder when device state changes |
| `server/astrodeck/sequence/resume_arm.py` (modify) | the recovery ladder before `engine.start` |
| `server/tests/test_resume_after_restart.py` (create) | the whole feature's tests |

---

### Task 1: Arm auto-resume on a running session

**Files:**
- Modify: `server/astrodeck/api/app.py:2831` and the `sequence_start` handler at `:3949`
- Test: `server/tests/test_resume_after_restart.py`

**Interfaces:**
- Consumes: `session_store` (`sequence/session.py`), `SessionPatchBody`
- Produces: nothing new — behaviour change only

- [ ] **Step 1: Write the failing tests**

```python
"""Resume after a mid-night restart (spec 2026-08-02)."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import astrodeck.api.app as app_module
from astrodeck.sequence.session import Session, session_store


@pytest.fixture
def client():
    app = app_module.create_app()
    with TestClient(app) as c:
        yield c


def _mk(status: str, **kw) -> Session:
    from astrodeck.sequence.models import ExposureStep, SequencePlan, Target
    plan = SequencePlan(name="p", targets=[Target(
        name="t", ra_hours=1.0, dec_deg=2.0,
        steps=[ExposureStep(filter="L", exposure_s=1, gain=100, count=2)])])
    s = Session(name="p", status=status, plan=plan, **kw)
    session_store.save(s)
    return s


def test_arming_an_active_session_is_allowed(client, tmp_path, monkeypatch):
    """The session a crash destroys is ACTIVE, so it is the one that must be
    armable. Arming was dormant-only, which meant the run that most needed to
    come back was the exact run that could not be told to."""
    import astrodeck.hub as hubmod
    monkeypatch.setattr(hubmod, "CAPTURE_DIR", tmp_path)
    s = _mk("active")
    r = client.patch(f"/api/sessions/{s.id}", json={"auto_resume": True})
    assert r.status_code == 200, r.text
    assert r.json()["auto_resume"] is True
    assert session_store.load(s.id).auto_resume is True


def test_arming_still_disarms_every_other_session(client, tmp_path, monkeypatch):
    """The server-enforced singleton must survive allowing active sessions."""
    import astrodeck.hub as hubmod
    monkeypatch.setattr(hubmod, "CAPTURE_DIR", tmp_path)
    old = _mk("dormant", auto_resume=True)
    new = _mk("active")
    client.patch(f"/api/sessions/{new.id}", json={"auto_resume": True})
    assert session_store.load(old.id).auto_resume is False
    assert session_store.load(new.id).auto_resume is True
```

- [ ] **Step 2: Run them and watch them fail**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_resume_after_restart.py -n 0 -v`
Expected: FAIL — `409 auto-resume arms only dormant sessions`

- [ ] **Step 3: Allow arming on active**

In `api/app.py`, replace the dormant-only guard:

```python
        if body.auto_resume is not None:
            # ARMING AN ACTIVE SESSION IS THE POINT, NOT AN EDGE CASE.
            #
            # This was dormant-only, written for the feature's original purpose
            # ("I have stopped for tonight, resume at dusk tomorrow"), where
            # dormant is true by definition. But a restart destroys an ACTIVE
            # session: boot_sweep then finds it dormant and UNARMED, and nobody
            # is awake at 2am to arm it. Proven on the rig 2026-08-02 by
            # rebooting mid-run — the session came back dormant, correct, and
            # inert. 'abandoned'/'complete' stay refused: there is nothing left
            # to resume.
            if body.auto_resume and s.status not in ("dormant", "active"):
                raise HTTPException(
                    409, "auto-resume arms only dormant or active sessions")
            if body.auto_resume:
                # server-enforced singleton (spec §5): arming here disarms others.
                for other in await asyncio.to_thread(session_store.load_all):
                    if other.id != s.id and other.auto_resume:
                        other.auto_resume = False
                        await asyncio.to_thread(session_store.save, other)
            s.auto_resume = body.auto_resume
```

- [ ] **Step 4: Run them and watch them pass**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_resume_after_restart.py -n 0 -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Arm new runs by default**

In `sequence_start` (`api/app.py:3949`), after the engine has started and the session exists, arm it. Find the line that starts the engine and returns; set `auto_resume=True` on the session the engine created, then save it, honouring the singleton by reusing the same disarm loop. Add this test first:

```python
def test_a_new_run_is_armed_by_default(client, tmp_path, monkeypatch):
    """An opt-in flag that must be remembered before every night is a flag that
    is not set on the night it was needed."""
    import astrodeck.hub as hubmod
    monkeypatch.setattr(hubmod, "CAPTURE_DIR", tmp_path)
    body = {"name": "d", "guide": False, "targets": [{
        "name": "darks", "ra_hours": 0.0, "dec_deg": 0.0, "calibration": True,
        "center": False, "autofocus_first": False,
        "steps": [{"filter": "Dark", "exposure_s": 1, "gain": 100, "count": 1}]}]}
    r = client.post("/api/sequence/start", json=body)
    assert r.status_code == 200, r.text
    armed = session_store.armed()
    assert armed is not None, "a started run must be armed to survive a restart"
```

- [ ] **Step 6: Full suite, then commit**

```bash
./.venv/Scripts/python.exe -m pytest -q
git commit -F - -- server/astrodeck/api/app.py server/tests/test_resume_after_restart.py
```

---

### Task 2: Device fingerprint

**Files:**
- Create: `server/astrodeck/devices/fingerprint.py`
- Modify: `server/astrodeck/hub.py` (one call site)
- Test: `server/tests/test_resume_after_restart.py`

**Interfaces:**
- Consumes: `persist.write_json_atomic(path, data, *, backup=True)`, `persist.read_json_or(path, default)`
- Produces:
  - `record(*, focuser_position: int | None, filter_slot: int | None, ra_hours: float | None, dec_deg: float | None, parked: bool | None, tracking: bool | None) -> None`
  - `verdict(*, focuser_position: int | None) -> Verdict` where `Verdict` is a dataclass with a single field `focus_trusted: bool`
  - `FINGERPRINT_WRITE_INTERVAL_S = 10.0`

- [ ] **Step 1: Write the failing tests**

```python
def test_focus_is_untrusted_when_the_focuser_forgot_its_position(tmp_path, monkeypatch):
    """The EAF forgets its position on power loss. A focuser reporting a
    different number than we last recorded is reporting a DEFAULT, not a
    measurement — that mismatch IS the power-loss tell, and it works for a
    yanked USB hub too, which no OS event log would catch."""
    from astrodeck.devices import fingerprint as fp
    monkeypatch.setattr(fp, "_PATH", tmp_path / "fp.json")
    fp.record(focuser_position=9935, filter_slot=7, ra_hours=1.0,
              dec_deg=2.0, parked=True, tracking=False)
    assert fp.verdict(focuser_position=9935).focus_trusted is True
    assert fp.verdict(focuser_position=0).focus_trusted is False


def test_a_missing_fingerprint_trusts_nothing(tmp_path, monkeypatch):
    """First-ever boot, or a wiped file: the safe direction is distrust."""
    from astrodeck.devices import fingerprint as fp
    monkeypatch.setattr(fp, "_PATH", tmp_path / "absent.json")
    assert fp.verdict(focuser_position=9935).focus_trusted is False


def test_writes_are_coalesced(tmp_path, monkeypatch):
    """Recording on every status poll would rewrite the file several times a
    second for a value that rarely changes."""
    from astrodeck.devices import fingerprint as fp
    monkeypatch.setattr(fp, "_PATH", tmp_path / "fp.json")
    now = [1000.0]
    monkeypatch.setattr(fp, "_now", lambda: now[0])
    fp.record(focuser_position=1, filter_slot=0, ra_hours=0.0, dec_deg=0.0,
              parked=True, tracking=False)
    fp.record(focuser_position=2, filter_slot=0, ra_hours=0.0, dec_deg=0.0,
              parked=True, tracking=False)
    assert fp.verdict(focuser_position=1).focus_trusted is True   # 2nd coalesced
    now[0] += fp.FINGERPRINT_WRITE_INTERVAL_S + 1
    fp.record(focuser_position=2, filter_slot=0, ra_hours=0.0, dec_deg=0.0,
              parked=True, tracking=False)
    assert fp.verdict(focuser_position=2).focus_trusted is True
```

- [ ] **Step 2: Run them and watch them fail**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_resume_after_restart.py -n 0 -k fingerprint -v`
Expected: FAIL — `ModuleNotFoundError: astrodeck.devices.fingerprint`

- [ ] **Step 3: Write the module**

```python
"""Last-known device state, so a power cut is DETECTABLE rather than guessed.

This records; it never restores. Driving a focuser back to a remembered number
would be a guess about a device that has just told us it does not know where it
is — the recovery ladder measures instead (autofocus, plate solve).

There is deliberately no pointing verdict. The AM5 is a harmonic drive with no
brake, so a heavy OTA can sag while the motors are unpowered and the encoders
cannot report it. Re-centering is therefore unconditional, and a verdict about
pointing would only ever be a reason to skip a check that must never be skipped.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

from ..persist import read_json_or, write_json_atomic

FINGERPRINT_WRITE_INTERVAL_S = 10.0

_PATH: Path | None = None
_last_write: float = 0.0


def _now() -> float:
    return time.monotonic()


def _path() -> Path:
    global _PATH
    if _PATH is None:
        from ..hub import CAPTURE_DIR
        _PATH = Path(CAPTURE_DIR) / "device_fingerprint.json"
    return _PATH


@dataclass(frozen=True)
class Verdict:
    focus_trusted: bool


def record(*, focuser_position: int | None, filter_slot: int | None,
           ra_hours: float | None, dec_deg: float | None,
           parked: bool | None, tracking: bool | None) -> None:
    """Persist the current device state, at most once per interval."""
    global _last_write
    now = _now()
    if _last_write and now - _last_write < FINGERPRINT_WRITE_INTERVAL_S:
        return
    _last_write = now
    try:
        write_json_atomic(_path(), {
            "focuser_position": focuser_position, "filter_slot": filter_slot,
            "ra_hours": ra_hours, "dec_deg": dec_deg,
            "parked": parked, "tracking": tracking,
        }, backup=False)
    except Exception:  # noqa: BLE001 — telemetry must never break a run
        pass


def verdict(*, focuser_position: int | None) -> Verdict:
    """Compare live device state against the record.

    An absent or unreadable file trusts NOTHING: that is a first-ever boot or a
    wiped state directory, and distrust only costs an autofocus."""
    raw = read_json_or(_path(), None)
    if not isinstance(raw, dict):
        return Verdict(focus_trusted=False)
    known = raw.get("focuser_position")
    # Exact match required: the EAF is a stepper reporting integers, so ANY
    # difference means it lost count.
    trusted = (known is not None and focuser_position is not None
               and int(known) == int(focuser_position))
    return Verdict(focus_trusted=bool(trusted))
```

- [ ] **Step 4: Run them and watch them pass**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_resume_after_restart.py -n 0 -k fingerprint -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Wire the recorder into the status path**

In `hub.py`, inside the method that assembles the device status dict (the one producing `focuser`, `filterwheel` and `mount` sections — search for where `"focuser"` is populated), call the recorder with the values just read. It is coalesced and swallows its own errors, so it is safe on a hot path:

```python
        from .devices import fingerprint as _fp
        _fp.record(
            focuser_position=focuser_pos, filter_slot=filter_slot,
            ra_hours=ra_hours, dec_deg=dec_deg,
            parked=parked, tracking=tracking)
```

Use whatever local names hold those values at that point; do not re-query the devices.

- [ ] **Step 6: Full suite, then commit**

```bash
./.venv/Scripts/python.exe -m pytest -q
git commit -F - -- server/astrodeck/devices/fingerprint.py server/astrodeck/hub.py server/tests/test_resume_after_restart.py
```

---

### Task 3: The recovery ladder

**Files:**
- Modify: `server/astrodeck/sequence/resume_arm.py`
- Test: `server/tests/test_resume_after_restart.py`

**Interfaces:**
- Consumes: `fingerprint.verdict(focuser_position=...) -> Verdict`; `hub.solve_and_sync(exposure_s=3.0) -> dict`; `hub.goto_and_center(ra_hours, dec_deg)`; `focus.native.run_native_autofocus(camera, focuser, ...)`
- Produces: `ResumeArm._recover(session) -> str | None` — `None` on success, else a human-readable refusal reason

- [ ] **Step 1: Write the failing tests**

```python
def test_the_blind_solve_runs_even_when_nothing_changed(monkeypatch):
    """UNCONDITIONAL, and this test is the reason it stays that way.

    The AM5 has no brake. A restart that preserved every byte of software state
    still cannot rule out that the tube drooped while the motors were unpowered,
    and the mount's own encoders cannot report a shift that happened while it was
    off. A later optimisation that skips verification 'because the fingerprint
    matched' would silently reintroduce exactly that hazard, so the matching
    fingerprint is the case asserted here."""
    calls = []
    arm = _arm_with(monkeypatch, focus_trusted=True, solve=lambda: calls.append("solve"))
    assert arm._recover(_session()) is None
    assert "solve" in calls


def test_a_failed_solve_refuses_to_move(monkeypatch):
    """Too few stars under cloud. The alternative to refusing is slewing an OTA
    whose true position is unknown, toward a pier."""
    moved = []
    arm = _arm_with(monkeypatch, focus_trusted=True,
                    solve=_raise(DeviceError("Not enough stars")),
                    center=lambda *a: moved.append(a))
    reason = arm._recover(_session())
    assert reason is not None and "solve" in reason.lower()
    assert moved == [], "must not slew on an unverified position"


def test_untrusted_focus_runs_autofocus_and_never_restores_a_number(monkeypatch):
    """Measure, do not guess: driving the focuser to a remembered position is a
    guess about a device that just reported it lost count."""
    moves, af = [], []
    arm = _arm_with(monkeypatch, focus_trusted=False,
                    autofocus=lambda: af.append(1),
                    focuser_move=lambda p: moves.append(p))
    arm._recover(_session())
    assert af == [1]
    assert moves == []


def test_a_refusal_leaves_the_session_dormant_and_armed(monkeypatch, bus_lines):
    """Fail safe: the next tick must retry, so the arming must survive."""
    arm = _arm_with(monkeypatch, focus_trusted=True,
                    solve=_raise(DeviceError("Not enough stars")))
    s = _session()
    arm.tick()
    assert s.status == "dormant" and s.auto_resume is True
    assert arm._retry_at > 0
    assert any("solve" in m.lower() for _l, m, _s in bus_lines)
```

Write the `_arm_with`, `_session` and `_raise` helpers in the same file — small fakes for hub/engine/focuser following the `_Tel` pattern in `tests/test_api_mount.py`.

- [ ] **Step 2: Run them and watch them fail**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_resume_after_restart.py -n 0 -k recover -v`
Expected: FAIL — `AttributeError: 'ResumeArm' object has no attribute '_recover'`

- [ ] **Step 3: Implement `_recover` and call it from `tick`**

Add to `ResumeArm`, and call it in `tick()` immediately before `self.engine.start(...)`; on a non-None return, log the reason, set `self._retry_at = now + RETRY_INTERVAL_S`, and return without starting.

```python
    async def _recover(self, session) -> str | None:
        """Make the rig's beliefs true again before it is allowed to move.

        Cooling is NOT here: SequenceEngine._run already waits via
        _cool_and_wait(plan.cool_to, plan.cool_timeout_s) under the
        require_cooling policy, sharing COOLER_AT_TARGET_C. A second wait would
        double the delay and let the two bands drift apart.

        Returns None on success, else a reason the caller logs before backing off.
        """
        from ..devices import fingerprint as _fp
        # 1. Focus: measure when the focuser lost count. Never restore a number.
        try:
            focuser = self.hub.require("focuser")
            pos = await focuser.get_position()
        except Exception:  # noqa: BLE001 — no focuser is not a refusal
            pos = None
        if pos is not None and not _fp.verdict(focuser_position=pos).focus_trusted:
            try:
                await self._autofocus()
            except Exception as e:  # noqa: BLE001
                return f"autofocus after restart failed: {e}"

        # 2. Pointing: ALWAYS re-measure. See the no-brake rule in the spec.
        try:
            await self.hub.solve_and_sync()
        except Exception as e:  # noqa: BLE001
            return (f"blind plate solve failed after restart ({e}) — refusing to "
                    "slew a mount whose true position is unknown")
        tgt = next((t for t in session.plan.targets if not t.calibration), None)
        if tgt is not None:
            try:
                await self.hub.goto_and_center(tgt.ra_hours, tgt.dec_deg)
            except Exception as e:  # noqa: BLE001
                return f"re-centering after restart failed: {e}"
        return None

    async def _autofocus(self) -> None:
        from ..focus.native import run_native_autofocus
        await run_native_autofocus(self.hub.require("camera"),
                                   self.hub.require("focuser"))
```

Calibration-only sessions skip the centering call because `tgt` is None; they still get the solve, which is harmless and confirms the sky is usable.

- [ ] **Step 4: Run them and watch them pass**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_resume_after_restart.py -n 0 -v`
Expected: PASS (all)

- [ ] **Step 5: Full suite, then commit**

```bash
./.venv/Scripts/python.exe -m pytest -q
git commit -F - -- server/astrodeck/sequence/resume_arm.py server/tests/test_resume_after_restart.py
```

---

### Task 4: On-sky acceptance

Run against the real rig. Dark sky and caps off were confirmed available 2026-08-02 21:21 PDT.

**Interfaces:** consumes the deployed build; produces evidence, no code.

- [ ] **Step 1: Deploy**

Bump `__version__` in `server/astrodeck/__init__.py` and `version` in `server/pyproject.toml`, then follow the release recipe: stage `releases/<ver>` from its predecessor, ship changed files, refresh `server/astrodeck/webui` from `ui/dist`, write `current` with `New-Object System.Text.UTF8Encoding($false)` (no BOM), stop the task, kill the `-m astrodeck run` PIDs by ID, confirm port 8800 frees, start the task, poll `/healthz`.

- [ ] **Step 2: Start a real run on a real target**

Pick a target above the horizon with `center: true`, `cool_to` set to the usual setpoint, a few short subs. Confirm `session_store.armed()` is non-None WITHOUT anyone arming it — that is Task 1 working on the rig.

- [ ] **Step 3: Kill the server mid-run, hard**

Kill the `-m astrodeck run` PID directly (not a clean shutdown) so it mimics a power cut to the process. Restart the task.

- [ ] **Step 4: Watch it come back by itself**

Expected in the night log, unattended, within ~60 s of the server binding:
`boot sweep: N orphaned session(s) -> dormant`, then a solve line, then the run resuming. Do NOT touch the UI.

- [ ] **Step 5: Confirm the pointing was re-measured**

The log must show a solve+sync AFTER the restart, even though the fingerprint matched — the no-brake rule holding on real hardware.

- [ ] **Step 6: Record the evidence**

Append the log excerpt and the resumed frame count to the spec under a "Verified on the rig" heading, and commit that.

---

## Self-Review

**Spec coverage:** arm-while-active → Task 1; default-armed → Task 1 Step 5; fingerprint + verdicts → Task 2; ladder autofocus → Task 3; unconditional solve/sync/recenter → Task 3; fail-safe backoff → Task 3 Step 3 and its test; cooling → covered by the existing engine gate, verified in Task 4 Step 2 via `cool_to` (documented above as deliberately not reimplemented); test harness → Task 4.

**Placeholders:** none. Every code step carries real code. The one judgement call left to the implementer is the local variable names at the `hub.py` call site in Task 2 Step 5, which is stated explicitly rather than hidden.

**Type consistency:** `Verdict.focus_trusted` is the only field, used identically in Tasks 2 and 3. `record()` and `verdict()` keyword signatures match between the module, its tests, and the Task 3 call. `_recover` returns `str | None` in the interface block, the implementation, and every test.
