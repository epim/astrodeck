"""Two recorded answers the run-mode readers are graded on (#189 S5, spec
1.2, 2.6, 5.9, 5.10, 6.9; U-07).

S5's readers (``ui/src/components/flows/flowRunState.ts``) and the CONTINUE
button's copy are pure functions of two server answers: the progress route's
(``GET /api/flows/{id}/progress``) and the sequence state the engine
publishes (``GET /api/sequence/state``, the WS ``sequence`` event). A UI
test that grades them against literals typed into it goes on grading an
answer the server no longer gives, which is what #353 item 7 found in S3.
So both answers are recorded here, as files the UI tests READ, and this
test rebuilds each one and grades the file byte for byte:

* ``fixtures/flow_progress_continue.json``: the progress route's answer for
  the eighth Example ("M31 3x2, rotating", ``example-m31-mosaic``) with a
  dormant session of two runs and banked frames, read through the real
  route. The same case then presses CONTINUE (``POST /api/flows/{id}/run``)
  and checks that the night the route says it starts is the recorded
  ``nights`` plus one: the button's copy reads "night n" off that count
  (spec 5.9), so the two must agree.
* ``fixtures/sequence_state_mosaic.json``: sequence states the engine
  published on the clocked simulator (``_group_harness``) for a rotating 2x2
  in which one panel is set aside by repeated centring misses: a panel
  being shot, a meridian wait as served to an operator, and the same wait
  as served to a viewer, with ``panel`` and ``pass`` absent (spec 5.10,
  6.9). The night is built HERE, from the harness's own parts, and
  ``_group_harness.py`` is not edited.

THE NIGHT IS CHOSEN SO THAT NOTHING ELSE IN S5 CAN MOVE IT. Every member's
schedule is the default one (run now, no altitude floor), so no member waits
on an altitude gate; nothing follows the group, so there is no follower; and
the plan has no instruction. S5-ENG-SCHED's fixes are to those three paths,
and a recording one of them could move would go red for a change that is not
about what the readers read.

THE TWO FILES ARE JOINED ON ONE SESSION ID. The night runs under the id of
the progress file's dormant session, as a CONTINUE of that session would
(``engine.start(session=)``, what ``run_flow`` calls), so the UI can grade
"this flow's run is live" on the two files as recorded. The flows differ
(the Example is a 3x2, the night a 2x2 of the fixture mosaic), which is the
point of the group ids: both blocks are called "M31", and only the id says
which group is which block's.

To rewrite the files after a deliberate change to either answer:

    cd server && ASTRODECK_REWRITE_S5_FIXTURES=1 .venv/Scripts/python.exe -m pytest -q -p no:randomly -n0 tests/test_s5_recorded_state.py

(through pytest, so the suite's config isolation holds while it runs), then
read the diff and re-run ``flowRunState.test.ts``. Never hand-edit them.

Every test names the mutation it guards and quotes the failure it produced,
each run in a private copy of ``server/`` (scratchpad ``s5-feed-mut``, from
byte backups, hash-compared after), never in the shared tree.
"""
from __future__ import annotations

import json
import os
import uuid
from pathlib import Path

from _group_harness import (GROUP_NAME, Night, grid_plan,  # noqa: F401
                            group_hub, group_store)
from astrodeck import events
from astrodeck.api.app import _sequence_envelope
from astrodeck.api.redact import _redact_sequence_for
from astrodeck.auth import principal_for_role
from astrodeck.flows.compile import compile_plan
from astrodeck.flows.examples import examples
from astrodeck.flows.to_plan import to_sequence_plan
from astrodeck.persist import write_json_atomic
from astrodeck.sequence.models import Schedule, SequencePlan
from astrodeck.sequence.session import Session, SessionFrame, session_store
from test_flows_progress_route import (_Night, _restore_provider,  # noqa: F401
                                       rig)

FIXTURES = Path(__file__).parent / "fixtures"
CONTINUE_FIXTURE = FIXTURES / "flow_progress_continue.json"
STATE_FIXTURE = FIXTURES / "sequence_state_mosaic.json"
REWRITE = os.environ.get("ASTRODECK_REWRITE_S5_FIXTURES") == "1"

EXAMPLE = "example-m31-mosaic"
#: The dormant session's id: fixed, so both files are the same bytes on every
#: run, and minted the way a uuid4 id looks, so a reader that expects one is
#: not handed a sentinel.
SESSION_ID = uuid.uuid5(uuid.NAMESPACE_URL,
                        "astrodeck:s5-feed:dormant-session").hex
#: The two runs, as report ids in the shape the engine mints
#: (``_mint_report_id``): two runs on two nights, so the recorded ``nights``
#: means the same thing whether it counts runs or nights (it counts runs:
#: #430).
RUNS = ("m31-3x2-rotating-20260921-030412", "m31-3x2-rotating-20260922-024955")

#: Accepted subs banked on each panel, ``(row, col) -> (L, R, G, B)``: 1-1
#: finished (20 of each), the rest part-way, and two rejected subs that
#: accepted mode does not count (``REJECTED``).
BANKED = {(0, 0): (20, 20, 20, 20), (0, 1): (7, 6, 6, 6),
          (0, 2): (6, 6, 6, 6), (1, 2): (6, 6, 6, 5),
          (1, 1): (6, 6, 5, 5), (1, 0): (5, 5, 5, 5)}
REJECTED = {(0, 0): "L", (1, 1): "B"}


def _write(path: Path, about: str, body: dict) -> None:
    path.write_bytes(_text(about, body).encode("utf-8"))


def _text(about: str, body: dict) -> str:
    """The file's bytes: ``{"about", ...body}``, one-space indented, ASCII,
    keys in the order the answer has them."""
    return json.dumps({"about": about, **body}, indent=1,
                      ensure_ascii=True) + "\n"


def _on_disk(path: Path) -> str:
    """The recorded file as text, with its line ends read as ``\\n``.

    THIS REPOSITORY RUNS WITH ``core.autocrlf=true`` (``.gitattributes`` says
    so for the photosphere fixtures), so a fresh Windows clone or worktree
    checks these files out with CRLF, while ``_text`` builds them with LF: a
    raw byte compare then fails on every line of an answer that has not
    changed. CI's full server suite runs on ubuntu, so only a developer's
    checkout would show it (#445 names the class). Only the line ends are
    normalised; every other byte (key order, indent, each value) is still
    graded exactly."""
    return path.read_bytes().decode("utf-8").replace("\r\n", "\n")


class TestTheGradeReadsEitherLineEnd:
    def test_a_crlf_checkout_is_the_same_file_and_an_edit_still_shows(
            self, tmp_path):
        """Each recorded file, checked out with CRLF line ends as this
        repository's ``core.autocrlf=true`` writes it on Windows, reads as
        the same text; and the same checkout with one byte added still
        reads as a different one, so the normalisation blinds the grade to
        nothing but the line ends.

        RED under mutant "the grade reads the raw bytes" (``_on_disk``
        without its ``replace``), run in a private copy of ``server/``
        (scratchpad ``s5-feed-verify-mut``), observed:

            AssertionError: a CRLF checkout of flow_progress_continue.json
            reads as another file
            assert '{\\r\\n "about...\\n }\\r\\n}\\r\\n' == '{\\n
            "about":...n  }\\n }\\n}\\n'

        and the same mutant, with the fixtures themselves converted to CRLF
        (what a fresh Windows clone holds), turned both grades below red:
        ``the file is not the route's answer`` and ``the file is not what
        the engine served``.

        RED under mutant "the grade ignores whitespace" (``_on_disk``
        answering ``"".join(text.split())``), observed:

            AssertionError: an edit of flow_progress_continue.json was read
            as the file
        """
        for fixture in (CONTINUE_FIXTURE, STATE_FIXTURE):
            text = _on_disk(fixture)
            assert json.loads(text).get("about"), (
                f"premise: {fixture.name} is read")
            crlf = tmp_path / f"crlf-{fixture.name}"
            crlf.write_bytes(text.replace("\n", "\r\n").encode("utf-8"))
            assert _on_disk(crlf) == text, (
                f"a CRLF checkout of {fixture.name} reads as another file")
            # One space more after the first key, and nothing else: an edit
            # the byte grade must still see through the normalisation.
            edited = tmp_path / f"edited-{fixture.name}"
            edited.write_bytes(text.replace('": ', '":  ', 1).replace(
                "\n", "\r\n").encode("utf-8"))
            assert _on_disk(edited) != text, (
                f"an edit of {fixture.name} was read as the file")


# ============================================ the progress route's answer

CONTINUE_ABOUT = (
    "GET /api/flows/example-m31-mosaic/progress: the eighth Example with a "
    "dormant session of two runs on two nights, 1-1 finished and the other "
    "panels part-way, counted in accepted mode. Built and graded byte for "
    "byte by server/tests/test_s5_recorded_state.py, which also presses "
    "CONTINUE and checks its night is nights + 1. Read, never copied, by "
    "ui/src/components/flows/__tests__/flowRunState.test.ts. Never "
    "hand-edit it.")


def _example_plan():
    """The Example's plan as ``/run`` and the progress route compile it:
    the stored graph, the flow's own id, so the ids the ledger counts by."""
    rec = next(e for e in examples() if e.id == EXAMPLE)
    compiled = compile_plan(rec.graph, rec.name)
    plan, _ = to_sequence_plan(compiled, rec.graph, flow_id=EXAMPLE)
    return plan


def _seed_dormant_session() -> Session:
    """The Example's dormant session, written as ``SessionStore.save``
    writes it, with the frames ``BANKED`` and ``REJECTED`` say, split over
    the two runs."""
    plan = _example_plan()
    assert [g.mode for g in plan.groups] == ["rotate"], (
        "premise: the Example is one rotating group")
    frames: list[SessionFrame] = []
    for t in plan.targets:
        cell = (t.panel_row, t.panel_col)
        for step, n in zip(t.steps, BANKED[cell]):
            frames += [SessionFrame(night=RUNS[i % 2], target_id=t.id,
                                    step_id=step.id, auto_accepted=True)
                       for i in range(n)]
            if REJECTED.get(cell) == step.filter:
                frames.append(SessionFrame(night=RUNS[1], target_id=t.id,
                                           step_id=step.id,
                                           auto_accepted=False))
    s = Session(id=SESSION_ID, name=plan.name, created_ts=1789959852.0,
                updated_ts=1790052600.0, status="dormant",
                plan=plan.model_copy(update={"count_mode": "accepted"}),
                nights=list(RUNS), frames=frames, origin="flow",
                origin_id=EXAMPLE)
    path = session_store._path(s.id)
    path.parent.mkdir(parents=True, exist_ok=True)
    write_json_atomic(path, s.model_dump(), backup=False)
    return s


class TestTheContinueFixture:
    async def test_the_file_is_the_routes_answer_and_continue_is_night_three(
            self, rig):
        """The progress route's answer for the Example and its dormant
        session, re-dumped the way the file is written, IS the file, byte
        for byte. Then CONTINUE on the same flow continues that session and
        says it starts night ``nights + 1``: the night the button's copy
        will print from the file's ``nights`` (spec 5.9).

        RED under mutant "fixture edited" (the file's 1-2 L ``banked`` 7
        made 8 by hand, nothing else touched), observed:

            AssertionError: the file is not the route's answer; rewrite it
            (see the module docstring)
            assert '{\\n "about":...n  }\\n }\\n}\\n' == '{\\n "about":...n
            }\\n }\\n}\\n'
              Skipping 2217 identical leading characters in diff, use -v
              to show
              - "banked": 7,
              + "banked": 8,

        RED under mutant "the route counts every sub taken" (``_counts``
        in ``flows/progress.py`` counting every frame, whatever the
        session's mode: 2-2's rejected B sub is then banked), observed:

            AssertionError: the file is not the route's answer; rewrite it
            (see the module docstring)
              Skipping 705 identical leading characters in diff, use -v to
              show
              - anked": 195,
              + anked": 194,

        RED under mutant "night is the run count" (``_continue_flow_session``
        in ``api/app.py`` answering ``night = len(s.nights)``), observed:

            AssertionError: CONTINUE starts night 2, and the file says 2
            runs so far
            assert 2 == (2 + 1)

        ``nights`` counts RUNS (one report id per ``engine.start``), not
        observing nights; the two runs here are on two nights, so this file
        holds whichever way that is settled (#430).
        """
        api, engine, _night = rig
        s = _seed_dormant_session()

        r = await api.progress(EXAMPLE)
        assert r.status_code == 200, r.text
        answer = json.loads(r.content)
        assert answer["session"] == {"id": s.id, "status": "dormant",
                                     "nights": 2, "count_mode": "accepted"}, (
            "premise: the route read the seeded session")
        (block,) = answer["blocks"]
        assert block["group_id"] == engine_group_id(), (
            "premise: the block names the plan's group")
        text = _text(CONTINUE_ABOUT, {"response": answer})
        if REWRITE:
            _write(CONTINUE_FIXTURE, CONTINUE_ABOUT, {"response": answer})
        assert _on_disk(CONTINUE_FIXTURE) == text, (
            "the file is not the route's answer; rewrite it (see the module "
            "docstring)")

        run = await api.client.post(f"/api/flows/{EXAMPLE}/run", json={})
        assert run.status_code == 200, run.text
        out = run.json()["session"]
        assert (out["id"], out["continued"]) == (s.id, True), (
            f"premise: CONTINUE continued the recorded session: {out}")
        recorded = json.loads(CONTINUE_FIXTURE.read_bytes())["response"]
        assert out["night"] == recorded["session"]["nights"] + 1, (
            f"CONTINUE starts night {out['night']}, and the file says "
            f"{recorded['session']['nights']} runs so far")
        assert engine._session.id == s.id


def engine_group_id() -> str:
    """The Example's one plan group's id, from the compile itself."""
    (group,) = _example_plan().groups
    return group.id


# ======================================= the sequence states of one night

STATE_ABOUT = (
    "GET /api/sequence/state as the engine served it on the clocked "
    "simulator (server/tests/_group_harness.py) for a rotating 2x2 of the "
    "fixture mosaic near the meridian, flips on, whose 2-2 never centres "
    "and is set aside at its third visit; the night runs under the session "
    "id flow_progress_continue.json records. 'shooting' is the first state "
    "with a panel set aside and an exposure in flight; the meridian wait is "
    "the first state whose group waits on the meridian rule, as an operator "
    "is served it and as a viewer is (panel and pass absent). Built and "
    "graded byte for byte by server/tests/test_s5_recorded_state.py. Read, "
    "never copied, by ui/src/components/flows/__tests__/flowRunState.test.ts."
    " Never hand-edit it.")

#: The fixture mosaic's first column 36 min of hour angle east of the
#: meridian at the night's start and its second 30 min: the second runs out
#: of room before its flip point first (the plan's 10 min lead, the 150 s
#: hop seed and a frame), the first about 6 min later, 22.5 min in, and the
#: group then waits for the second column's crossing, 30 min in (spec 5.7).
#: Thirty 30 s frames of L a panel, one a visit.
NIGHT_PANELS = {"filters": ("L",), "count": 30, "ha_h": -0.6,
                "ha_step_h": 0.1}
#: The panel that never centres.
MISSES = f"{GROUP_NAME} 2-2"


def _night_plan() -> SequencePlan:
    return grid_plan(rows=2, cols=2, panel_kw=dict(NIGHT_PANELS),
                     meridian_flip=True)


def _misses_2_2(who: str, n: int, result: dict) -> dict:
    """Every centring of 2-2 fails, as a solve that never lands does; the
    other panels centre (the harness's own answer)."""
    if who == MISSES:
        return {**result, "centered": False, "error_arcmin": None}
    return result


async def _served_states(hub, monkeypatch) -> list[dict]:
    """Every state ``GET /api/sequence/state`` would have answered at each
    of the night's sequence publishes, taken the moment the publish was
    made: ``_sequence_envelope`` over the engine, as the route builds it,
    before any principal's redaction.

    This night crosses the meridian, so the hub's meridian block feeds the
    engine's ``live`` chip and the finish clock's flip cost (the 90 s flip
    in ``events_cost_s``). The harness holds that block on the night's
    clock (``Night._hold_the_meridian``, #368), so it is the same on every
    run and at every hour of the wall clock, as a file compared byte for
    byte needs."""
    plan = _night_plan()
    night = Night(hub, monkeypatch, goto=_misses_2_2)
    served: list[dict] = []
    harness_publish = events.bus.publish

    def publish(topic, **payload):
        recording = night._recording
        out = harness_publish(topic, **payload)
        if topic == "sequence" and recording:
            served.append(json.loads(json.dumps(
                _sequence_envelope(night.engine))))
        return out

    monkeypatch.setattr(events.bus, "publish", publish)
    session = Session(id=SESSION_ID, name=plan.name,
                      created_ts=night.clock.t, status="dormant", plan=plan,
                      origin="flow", origin_id=EXAMPLE)
    try:
        done = await night.run(plan, wall_s=120.0, session=session)
    finally:
        await night.close()
    assert done, f"premise: the night ended: {night.trace[-3:]}"
    return served


def _group(state: dict) -> dict:
    return state.get("group") or {}


def _pick(served: list[dict]) -> dict:
    """The three recorded states, by the rules the file's ``about`` names."""
    shooting = next(
        (s for s in served
         if _group(s).get("set_aside")
         and (s.get("progress") or {}).get("frame_started_at_ms") is not None
         and not _group(s).get("meridian_wait")), None)
    assert shooting is not None, (
        "the night served no state with a panel set aside and an exposure "
        "in flight")
    wait = next((s for s in served if _group(s).get("meridian_wait")), None)
    assert wait is not None, (
        "the night served no state whose group waits on the meridian rule")
    return {"shooting": _redact_sequence_for(shooting, OPERATOR),
            "meridian_wait_operator": _redact_sequence_for(wait, OPERATOR),
            "meridian_wait_viewer": _redact_sequence_for(wait, VIEWER)}


OPERATOR = principal_for_role("operator")
VIEWER = principal_for_role("viewer")


class TestTheSequenceStateFixture:
    def test_the_night_has_no_waiter_no_follower_and_no_instruction(self):
        """The night is kept off the three paths S5-ENG-SCHED changes (see
        the module docstring): no instruction, every target a member of the
        group (so nothing follows it), and every member's schedule the
        default one (run now, no altitude floor, no constraint), so no
        member waits on an altitude gate.

        RED under mutant "a follower after the group" (``_night_plan``
        passing ``after=(single("NGC 7331"),)`` to ``grid_plan``), observed:

            AssertionError: a target outside the group: ['NGC 7331']
            assert ['NGC 7331'] == []
        """
        plan = _night_plan()
        assert plan.instructions == [], "the night has an instruction"
        (group,) = plan.groups
        outside = [t.name for t in plan.targets if t.mosaic_group != group.id]
        assert outside == [], f"a target outside the group: {outside}"
        gated = [t.name for t in plan.targets if t.schedule != Schedule()]
        assert gated == [], f"a member with a schedule: {gated}"
        assert group.mode == "rotate", "premise: a rotating group"

    async def test_the_file_is_what_the_engine_served(self, group_hub,
                                                      monkeypatch):
        """The night re-run, the three states picked by the file's rules and
        served as each principal is served: the file, byte for byte. The
        premises say the recording is what its ``about`` claims: a panel
        shot with 2-2 set aside for its centring, a meridian wait whose
        group an operator is served with its panel and pass, and a viewer
        without them and without anything else taken.

        RED under mutant "the viewer is served the panel"
        (``api.redact._withhold_group_timing`` returning the state it was
        handed), observed:

            AssertionError: a viewer was served the panel or the pass
            across the meridian wait: {'id': 'm31-mosaic', 'name': 'M31',
            'mode': 'rotate', 'pass': 17, 'panel': '2-1', ...,
            'meridian_wait': True}

        RED under mutant "meridian_wait never set" (``_group_state`` in
        ``sequence/engine.py`` publishing ``meridian_wait: False``),
        observed:

            AssertionError: the night served no state whose group waits on
            the meridian rule

        RED under mutant "fixture edited" (the file's viewer wait
        ``panels_total`` 4 made 3 by hand), observed:

            AssertionError: the file is not what the engine served; rewrite
            it (see the module docstring)
              Skipping 4931 identical leading characters in diff, use -v to
              show
              - s_total": 4,
              + s_total": 3,
        """
        served = await _served_states(group_hub, monkeypatch)
        states = _pick(served)
        shooting = states["shooting"]
        op, vw = (states["meridian_wait_operator"],
                  states["meridian_wait_viewer"])

        assert shooting["state"] == "running"
        assert shooting["session"]["id"] == SESSION_ID, (
            "premise: the night runs under the progress file's session")
        g = shooting["group"]
        assert g["panel"] != "2-2" and g["meridian_wait"] is False
        (aside,) = g["set_aside"]
        assert aside["panel"] == "2-2" and aside["reason"].startswith(
            "centring failed on 2-2"), f"premise: 2-2 set aside: {aside}"

        assert op["group"]["meridian_wait"] is True
        assert op["group"].get("panel") and op["group"].get("pass"), (
            f"premise: an operator is served the panel and the pass: "
            f"{op['group']}")
        assert "panel" not in vw["group"] and "pass" not in vw["group"], (
            f"a viewer was served the panel or the pass across the meridian "
            f"wait: {vw['group']}")
        assert {k: v for k, v in op["group"].items()
                if k not in ("panel", "pass")} == vw["group"], (
            "the viewer lost more of the group than the panel and the pass")
        # What this pins is the redaction AS IT STANDS, not a rule that a
        # viewer must see everything else: outside the group the GET route
        # withholds nothing, so the viewer is still served ``target`` ("M31
        # 2-1", the panel the group withholds), ``detail`` and
        # ``live.meridian_eta_s`` (the countdown ``redact`` strips from
        # ``meridian.hours_to_flip``), and #166 records all three as the
        # site-timing residual. When #166 withholds them, this premise and
        # the file change together, on purpose.
        assert {k: v for k, v in op.items() if k != "group"} == {
            k: v for k, v in vw.items() if k != "group"}, (
            "the viewer's state differs outside the group, which today's "
            "redaction does not do (see #166 before rewriting the file)")

        body = {"states": states}
        if REWRITE:
            _write(STATE_FIXTURE, STATE_ABOUT, body)
        assert _on_disk(STATE_FIXTURE) == _text(STATE_ABOUT, body), (
            "the file is not what the engine served; rewrite it (see the "
            "module docstring)")
