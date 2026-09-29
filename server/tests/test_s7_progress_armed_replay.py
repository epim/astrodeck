"""The progress route says what an armed auto-resume would replay (#473, S7
orchestrator ruling 1; spec 5.9, 6.9).

An armed auto-resume starts the flow's dormant session on the plan it froze,
not on the flow as the editor shows it, and spec 5.9 promises the editor says
so: "the armed session will replay the version from 2026-09-22; press
CONTINUE to apply your edits". The progress route's session carried neither
fact, so no client could say it. Now it carries two more keys:

* ``armed``: ResumeArm would start this session. ``Session.is_armed``, the
  rule ``SessionStore.armed`` picks by: dormant, with auto-resume on. A live
  run is always ``auto_resume`` true (``engine.start`` arms every run), and
  is not a session auto-resume will start.
* ``plan_saved_ts``: the flow record's saved time for the version the
  session froze, stored on the session (``Session.plan_saved_ts``, additive,
  ``SESSION_SCHEMA`` unchanged). ``run_flow`` writes it when it makes a
  session and when CONTINUE replaces the plan. A session that predates it,
  a shipped Example's (never saved) and one whose plan a PATCH replaced
  answer None, never a guessed time.

Both keys are on the route's allow-list, and the #19 site-move test still
reads byte-identical bodies (test_flows_progress_route.py, which seeds both
keys non-null for it).

THE HARNESS is ``test_flows_continue.py``'s ``rig``: the real app over ASGI
and a real ``SequenceEngine`` whose imaging loop alone is replaced, so every
session here is made, written and finalized by the engine's own ``start``,
ledger write and ``_finalize_report``.

MUTATIONS. Each was written over a byte backup of the file it changes in a
private copy of ``server/`` (scratchpad ``s7-session-mut``), only this file
was run there, and the copy was restored and SHA-256 compared after every
mutant. The failures are quoted as observed.
"""
from __future__ import annotations

import asyncio
import json
import time

from astrodeck.persist import write_json_atomic
from astrodeck.sequence.session import Session, session_store
from test_flows_continue import LR, _compiled, rig  # noqa: F401 (fixture)


async def _session(rig, fid: str) -> dict:
    r = await rig.client.get(f"/api/flows/{fid}/progress")
    assert r.status_code == 200, r.text
    return r.json()["session"]


async def _saved(rig, fid: str) -> float:
    """The flow record's ``updated_ts``, as ``GET /api/flows/{id}`` reads it."""
    r = await rig.client.get(f"/api/flows/{fid}")
    assert r.status_code == 200, r.text
    return r.json()["updated_ts"]


async def _clock_past(ts: float) -> None:
    """Return once ``time.time()`` reads later than ``ts``: Windows ticks
    every 15.625 ms, and a save inside the tick of the last one would stamp
    the same ``updated_ts``, so "a later save" would not be later."""
    while time.time() <= ts:
        await asyncio.sleep(0.001)


def _seed(fid: str, **raw) -> Session:
    """A dormant, armed session of flow ``fid`` written as a FILE, with the
    raw top-level keys ``raw`` names replaced (or removed, for a value of
    ``...``): the shape a build before S7, or a hand edit, leaves."""
    s = Session(name="seeded", created_ts=100.0, updated_ts=100.0,
                status="dormant", plan=_compiled(LR, fid), auto_resume=True,
                origin="flow", origin_id=fid)
    body = s.model_dump()
    for key, value in raw.items():
        if value is ...:
            body.pop(key, None)
        else:
            body[key] = value
    path = session_store._path(s.id)
    path.parent.mkdir(parents=True, exist_ok=True)
    write_json_atomic(path, body, backup=False)
    return s


class TestArmed:
    async def test_armed_only_while_dormant_and_off_after_a_disarm(self, rig):
        """Live: not armed, though ``engine.start`` armed it. Dormant after
        the night: armed, the session the next ResumeArm tick would start.
        PATCH ``auto_resume`` false: not armed.

        RED under mutant "armed ignores status" (``Session.is_armed``
        answering ``self.auto_resume``), at the live read, observed:

            AssertionError: assert ('active', True) == ('active', False)
              At index 1 diff: True != False

        RED under mutant "no replay facts on the route" (the route's
        ``replay_facts`` update removed, the session back to four keys),
        observed:

            KeyError: 'armed'

        A DISARM IS NOT A NEW VERSION. The PATCH changes whether the
        session will replay, not what it would replay, so ``plan_saved_ts``
        stays the flow's saved time: the operator who re-arms it must still
        be told which version comes back. RED under mutant "any patch clears
        it" (``patch_session``'s ``s.plan_saved_ts = None`` moved out of the
        ``body.plan`` branch, so every PATCH clears it), observed (verifier,
        scratchpad ``s7-session-verify-mut``):

            AssertionError: a disarm wiped the frozen version's time
            assert None == 1790650946.2817183
        """
        fid = await rig.save_flow(LR)
        saved = await _saved(rig, fid)
        r = await rig.run(fid)
        assert r.status_code == 200, r.text
        sid = r.json()["session"]["id"]
        assert session_store.load(sid).auto_resume is True, (
            "premise: engine.start armed the live run")
        live = await _session(rig, fid)
        assert (live["status"], live["armed"]) == ("active", False)

        await rig.end_night("incomplete")
        dormant = await _session(rig, fid)
        assert (dormant["status"], dormant["armed"]) == ("dormant", True)
        assert session_store.armed().id == sid, (
            "premise: the store's own rule names the same session")

        r = await rig.client.patch(f"/api/sessions/{sid}",
                                   json={"auto_resume": False})
        assert r.status_code == 200, r.text
        off = await _session(rig, fid)
        assert (off["status"], off["armed"]) == ("dormant", False)
        assert session_store.armed() is None
        assert off["plan_saved_ts"] == saved, (
            "a disarm wiped the frozen version's time")


class TestPlanSavedTs:
    async def test_a_fresh_run_freezes_the_flows_saved_time(self, rig):
        """A fresh run's session carries the flow's ``updated_ts``, while it
        runs, after it banks a frame and after it ends.

        RED under mutant "written to the file, not the run's session"
        (``_freeze_saved_version`` loading the session from disk, setting
        the field on that copy and saving it, instead of setting it on
        ``engine._session``): the engine's own copy has None and its first
        ledger write puts None back. Observed, at the read after the frames:

            AssertionError: the run's ledger write lost the frozen version's
            time
            assert None == 1790646737.1838946

        RED under mutant "never frozen on a fresh run" (the
        ``_freeze_saved_version`` call removed from ``run_flow``), observed:

            assert None == 1790646743.8648295
        """
        fid = await rig.save_flow(LR)
        saved = await _saved(rig, fid)
        r = await rig.run(fid)
        assert r.status_code == 200, r.text
        assert r.json()["session"]["continued"] is False
        assert (await _session(rig, fid))["plan_saved_ts"] == saved

        rig.bank([0, 1])
        assert (await _session(rig, fid))["plan_saved_ts"] == saved, (
            "the run's ledger write lost the frozen version's time")
        await rig.end_night("incomplete")
        assert (await _session(rig, fid))["plan_saved_ts"] == saved

    async def test_a_later_save_leaves_it_older_and_continue_moves_it(
            self, rig):
        """Saved again after night one, the flow's ``updated_ts`` moves and
        the session's does not: the session still holds the version it
        froze, which is what the editor's notice needs to say. CONTINUE
        replaces the plan with the new version, and ``plan_saved_ts`` moves
        to it.

        RED under mutant "plan_saved_ts rewritten on every save" (the route
        answering the flow record's own ``updated_ts`` for the session's
        ``plan_saved_ts``), at the read after the save, observed:

            assert 1790646703.5233178 == 1790646703.3517373

        RED under mutant "CONTINUE keeps the old version's time" (the
        ``s.plan_saved_ts = plan_saved_ts`` line removed from
        ``_continue_flow_session``), observed:

            assert 1790646803.2894917 == 1790646803.42887

        Mutants "written to the file, not the run's session" and "never
        frozen on a fresh run" fail here too, at the read after the save:
        ``assert None == 1790646737.7029753``.
        """
        fid = await rig.save_flow(LR)
        first = await _saved(rig, fid)
        await rig.night_one(fid, [0])

        await _clock_past(first)
        await rig.put_flow(fid, LR)
        second = await _saved(rig, fid)
        assert second > first, "premise: the save moved the flow's time"
        held = await _session(rig, fid)
        assert held["plan_saved_ts"] == first
        assert held["plan_saved_ts"] < second

        r = await rig.run(fid)
        assert r.status_code == 200, r.text
        assert r.json()["session"]["continued"] is True
        assert (await _session(rig, fid))["plan_saved_ts"] == second
        await rig.end_night("incomplete")
        after = await _session(rig, fid)
        assert (after["plan_saved_ts"], after["armed"]) == (second, True)

    async def test_a_session_that_predates_it_answers_none(self, rig):
        """A session written before S7 has no ``plan_saved_ts`` in its file.
        It loads, and says None: not its ``created_ts``, not the flow's
        time, not any time.

        RED under mutant "a guessed time" (``replay_facts`` answering the
        session's ``created_ts`` when it has no ``plan_saved_ts``),
        observed:

            assert (100.0, True) == (None, True)
              At index 0 diff: 100.0 != None

        RED under mutant "plan_saved_ts rewritten on every save", observed:

            assert (1790646703.8329527, True) == (None, True)
        """
        fid = await rig.save_flow(LR)
        s = _seed(fid, plan_saved_ts=...)
        raw = json.loads(session_store._path(s.id).read_text("utf-8"))
        assert "plan_saved_ts" not in raw, "premise: the file predates it"
        got = await _session(rig, fid)
        assert got["id"] == s.id
        assert (got["plan_saved_ts"], got["armed"]) == (None, True)

    async def test_a_time_that_is_not_a_number_answers_none(self, rig):
        """A hand-edited file can hold NaN, which the server never writes.
        The card still answers, with no time: NaN in the body would fail the
        route's JSON rendering for every reader.

        RED under mutant "no finite check" (``replay_facts`` passing the
        stored value through), the transport re-raising the route's
        rendering error, observed:

            ValueError: Out of range float values are not JSON compliant: nan
        """
        fid = await rig.save_flow(LR)
        s = _seed(fid, plan_saved_ts=float("nan"))
        assert s.id == (await _session(rig, fid))["id"]
        assert (await _session(rig, fid))["plan_saved_ts"] is None

    async def test_a_shipped_example_freezes_no_time(self, rig):
        """An Example is never saved: its ``updated_ts`` is the moment the
        record was built for that read, so every read of the flow is
        "newer" than any time frozen from it, and the editor would claim
        edits the session lacks on a flow nobody can edit. Its run freezes
        None.

        RED under mutant "an Example freezes its read time" (``run_flow``'s
        ``plan_saved_ts`` read as ``rec.updated_ts`` whatever the record),
        observed:

            assert 1790646829.451409 is None

        Mutants "a guessed time" and "plan_saved_ts rewritten on every save"
        fail here the same way (``assert 1790646810.3897555 is None``).
        """
        r = await rig.run("example-m31-mosaic")
        assert r.status_code == 200, r.text
        assert r.json()["session"]["continued"] is False
        got = await _session(rig, "example-m31-mosaic")
        assert got["status"] == "active", "premise: the Example's run is live"
        assert got["plan_saved_ts"] is None

    async def test_a_plan_patch_clears_it(self, rig):
        """``PATCH /api/sessions/{id}`` with a plan replaces the session's
        plan with one no flow save produced; the session no longer holds the
        version its time names, so the time goes.

        RED under mutant "kept across a plan patch" (the ``s.plan_saved_ts =
        None`` line removed from ``patch_session``), observed:

            assert 1790646838.2996726 is None

        Mutant "never frozen on a fresh run" fails the premise instead,
        observed:

            AssertionError: premise: the run froze the flow's time
            assert None == 1790646745.1576073
        """
        fid = await rig.save_flow(LR)
        saved = await _saved(rig, fid)
        one = await rig.night_one(fid, [0])
        assert (await _session(rig, fid))["plan_saved_ts"] == saved, (
            "premise: the run froze the flow's time")
        r = await rig.client.patch(f"/api/sessions/{one.id}", json={
            "plan": one.plan.model_dump(mode="json")})
        assert r.status_code == 200, r.text
        assert (await _session(rig, fid))["plan_saved_ts"] is None
