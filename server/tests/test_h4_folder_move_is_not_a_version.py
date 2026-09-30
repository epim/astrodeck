"""A folder move is not a version: renaming or deleting a flow's folder
leaves its ``updated_ts`` alone, so the replay notice does not ask for
edits nobody made (#512; #189 spec 5.9, S7 orchestrator ruling 1; H4).

THE NOTICE. An armed auto-resume replays the plan its dormant session
froze, so the editors print "the armed session will replay the version from
<date>; press CONTINUE to apply your edits" (``replayNotice``, #473) while
the progress route's session is ``armed`` and the flow record's
``updated_ts`` is later than the session's ``plan_saved_ts``, the saved time
``run_flow`` froze. Both sides are one field, read as "a new version of the
flow was saved".

THE DEFECT. ``FlowStore.rename_folder`` wrote ``{"folder": new,
"updated_ts": time.time()}`` into every flow it re-parented, and
``delete_folder`` goes through it, so a folder tidy-up passed each moved
flow's time over its session's and all three editor surfaces told the
operator to press CONTINUE for edits nobody made. A folder is a field on the
record, not a version of the graph, so the move now writes the folder and
nothing else (the issue's first option). The library's order by
``updated_ts`` stays put with it, which the issue reads as right as well.

THE HARNESS is ``test_flows_continue.py``'s ``rig``, as
``test_s7_progress_armed_replay.py`` uses it: the real app over ASGI and a
real ``SequenceEngine`` whose imaging loop alone is replaced, so the session
is made, frozen and finalized by the engine's own ``start`` and
``_finalize_report``, and the folder verbs are the product's routes.

``_notice`` is ``replayNotice``'s rule (``ui/src/components/flows/
replayNotice.ts``) on the route's two answers: armed exactly true, both
times finite, and the record's later than the session's.

Each mutant ran in a private copy of ``server/`` (scratchpad
``H4-FLOWS-mut``), ``store.py`` mutated from a byte backup and restored with
its sha256 checked, never in the shared tree.
"""
from __future__ import annotations

import asyncio
import math
import time

import pytest

from test_flows_continue import LR, rig  # noqa: F401 (fixture)


async def _session(rig, fid: str) -> dict:
    r = await rig.client.get(f"/api/flows/{fid}/progress")
    assert r.status_code == 200, r.text
    return r.json()["session"]


async def _record(rig, fid: str) -> dict:
    r = await rig.client.get(f"/api/flows/{fid}")
    assert r.status_code == 200, r.text
    return r.json()


async def _clock_past(ts: float) -> None:
    """Return once ``time.time()`` reads later than ``ts``: Windows ticks
    every 15.625 ms, so a re-stamp inside the tick of the save would write
    the same time and a mutant that re-stamps would pass unseen."""
    while time.time() <= ts:
        await asyncio.sleep(0.001)


def _notice(session: dict, record: dict) -> bool:
    """``replayNotice``'s rule: whether the editor prints the line."""
    frozen, saved = session.get("plan_saved_ts"), record.get("updated_ts")
    finite = all(isinstance(v, (int, float)) and math.isfinite(v)
                 for v in (frozen, saved))
    return session.get("armed") is True and finite and saved > frozen


async def _dormant_in(rig, folder: str) -> tuple[str, float]:
    """A flow saved into ``folder``, run, and its night ended short: the
    session dormant and armed. Returns the flow's id and saved time, once
    the clock has moved past it."""
    r = await rig.client.post("/api/flows", json={
        "flow": {"name": "tidy me", "folder": folder, "graph": LR}})
    assert r.status_code == 200, r.text
    fid = r.json()["id"]
    saved = (await _record(rig, fid))["updated_ts"]
    await rig.night_one(fid, [0])
    session = await _session(rig, fid)
    assert (session["status"], session["armed"]) == ("dormant", True), (
        f"premise: the night left the session dormant and armed: {session}")
    assert session["plan_saved_ts"] == saved, (
        "premise: the run froze the flow's saved time")
    await _clock_past(saved)
    return fid, saved


async def _rename(rig, fid: str) -> None:
    r = await rig.client.post("/api/flows/folders",
                              json={"name": "Winter", "new_name": "Spring"})
    assert r.status_code == 200 and r.json()["moved"] == 1, r.text


async def _delete(rig, fid: str) -> None:
    r = await rig.client.delete("/api/flows/folders/Winter")
    assert r.status_code == 200 and r.json()["moved"] == 1, r.text


MOVES = {"rename": (_rename, "Spring"), "delete": (_delete, "My flows")}


class TestAFolderMoveIsNotAVersion:
    @pytest.mark.parametrize("move", list(MOVES))
    async def test_no_replay_notice_after_the_folder_moves(self, rig, move):
        """Save and run a flow in Winter, end the night dormant and armed,
        then rename Winter (or delete it, which re-parents to My flows).
        The flow is in its new folder, its saved time has not moved, the
        session's ``plan_saved_ts`` is not earlier than it, and the editor
        prints no line.

        RED under the store.py mutant "rename re-stamps updated_ts"
        (``rename_folder``'s raw edit given back ``"updated_ts":
        time.time()``), on both moves, delete_folder going through it,
        observed (the rename, then the delete):

            E       AssertionError: the folder move re-stamped the flow's
                    saved time
            E       assert 1790690074.1204376 == 1790690073.89727

            E       assert 1790690074.3545327 == 1790690074.2343009

        The unfixed store failed the same way, before the change.
        """
        fid, saved = await _dormant_in(rig, "Winter")
        do, folder = MOVES[move]
        await do(rig, fid)
        record = await _record(rig, fid)
        assert record["folder"] == folder, "premise: the flow moved"
        assert record["updated_ts"] == saved, (
            "the folder move re-stamped the flow's saved time")
        session = await _session(rig, fid)
        assert session["plan_saved_ts"] >= record["updated_ts"]
        assert not _notice(session, record), (
            "the editor asks for edits nobody made")

    async def test_the_library_card_keeps_its_time(self, rig):
        """The card's ``updated_ts`` (the library's sort key) is the
        record's, so it stays put too.

        RED under "rename re-stamps updated_ts", observed:

            E       AssertionError: assert ('Spring', 1790690074.6202173) ==
                    ('Spring', 1790690074.482296)
            E         At index 1 diff: 1790690074.6202173 != 1790690074.482296
        """
        fid, saved = await _dormant_in(rig, "Winter")
        await _rename(rig, fid)
        r = await rig.client.get("/api/flows")
        (card,) = [c for c in r.json() if c["id"] == fid]
        assert (card["folder"], card["updated_ts"]) == ("Spring", saved)


class TestASaveIsStillAVersion:
    async def test_control_a_graph_save_moves_the_time_and_the_notice_shows(
            self, rig):
        """CONTROL. A real save of the graph, after the same night and the
        same rename, moves ``updated_ts`` past the session's frozen time,
        and the editor prints the line: the fix is to the folder verbs,
        not to the notice.

        RED under the store.py mutant "a save keeps the old time"
        (``save_and_report``'s ``"updated_ts": now`` removed from the
        record's update, so the record keeps the time it was sent with,
        the client's copy of the first save's), observed:

            E       AssertionError: a graph save did not move the flow's
                    saved time
            E       assert 1790690079.9973621 > 1790690079.9973621
        """
        fid, saved = await _dormant_in(rig, "Winter")
        await _rename(rig, fid)
        record = await _record(rig, fid)
        r = await rig.client.put(f"/api/flows/{fid}", json={"flow": record})
        assert r.status_code == 200, r.text
        after = await _record(rig, fid)
        assert after["updated_ts"] > saved, (
            "a graph save did not move the flow's saved time")
        session = await _session(rig, fid)
        assert session["plan_saved_ts"] == saved
        assert _notice(session, after), (
            "a saved edit no longer raises the replay line")
