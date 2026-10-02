# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
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
  dormant, armed session of two runs on two nights and banked frames, read
  through the real route. The same case then presses CONTINUE (``POST
  /api/flows/{id}/run``) on a later night and checks that the night the
  route says it starts is the recorded ``nights`` plus one: the button's
  copy reads "night n" off that count (spec 5.9), so the two must agree.
  Since S7 the session also carries ``armed`` (true) and ``plan_saved_ts``
  (null: an Example is never saved, and this session predates the key),
  and ``nights`` counts observing nights (#430, #473). Since H4 it carries
  ``continue_night`` (#511), the night CONTINUE would start, which the
  button prints; the file records the answer at ``CONTINUE_AT`` (night 3)
  and, as ``same_night_session``, the session the same route answered at
  ``SAME_NIGHT_AT``, on the second recorded night itself (night 2), and
  CONTINUE is pressed at both, each answering the recorded number.
* ``fixtures/sequence_state_mosaic.json``: sequence states the engine
  published on the clocked simulator (``_group_harness``) for a rotating 2x2
  in which one panel is set aside by repeated centring misses: a panel
  being shot, a meridian wait as served to an operator, and the same wait
  as served to a viewer, with ``panel`` and ``pass`` absent (spec 5.10,
  6.9). The night is built HERE, from the harness's own parts, and
  ``_group_harness.py`` is not edited. Since S7 (#451) the file also holds
  three states of a second night of the same plan, each with the group on
  a panel: the run paused, the run holding for cloud, and the run winding
  down from an Abort (``runIsLive``'s three live states that are not
  ``running``). The first night's three states did not move for them.

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

import asyncio
import json
import os
import time
import uuid
from pathlib import Path

from _group_harness import (GROUP_NAME, Night, close_night_hub,  # noqa: F401
                            grid_plan, group_hub, group_store, night_hub)
import astrodeck.api.app as app_module
import astrodeck.sequence.engine as engine_module
from astrodeck import events
from astrodeck.api.app import _sequence_envelope
from astrodeck.api.redact import _redact_sequence_for
from astrodeck.auth import principal_for_role
from astrodeck.config import SafetyConfig
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
#: (``_mint_report_id``): two runs on two nights, the small hours of the
#: nights of 2026-09-20 and 2026-09-21, so the recorded ``nights`` is 2
#: whether it counts runs or nights. It counts observing nights since S7
#: (#430), and the file did not change for it.
RUNS = ("m31-3x2-rotating-20260921-030412", "m31-3x2-rotating-20260922-024955")
#: When CONTINUE is pressed: 21:00 local on the evening after the second
#: recorded night, a night the session has not run, built from local
#: wall-clock fields so it is that evening in any zone (the dev box is
#: Pacific, CI is UTC).
CONTINUE_AT = time.mktime((2026, 9, 22, 21, 0, 0, 0, 0, -1))
#: A CONTINUE pressed on a night the session HAS run (#511, H4): 04:00 local
#: on 2026-09-22, an hour after the second recorded run started (02:49:55)
#: and before that day's noon rollover, so the same observing night, the
#: run stopped and pressed again. The progress route answers night 2 here,
#: and ``nights + 1`` would have printed 3.
SAME_NIGHT_AT = time.mktime((2026, 9, 22, 4, 0, 0, 0, 0, -1))

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
    "panels part-way, counted in accepted mode. 'response' is the answer at "
    "21:00 local on the evening after the second night; 'same_night_session' "
    "is the session the same route answered at 04:00 local on the second "
    "night itself, the rest of that answer being 'response''s. Built and "
    "graded byte for byte by server/tests/test_s5_recorded_state.py, which "
    "also presses CONTINUE at both moments and checks each answers the "
    "recorded continue_night (#511). Read, never copied, by "
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
    # ARMED, as a dormant session is after a night that ended short of its
    # quota (``engine.start`` arms every run and the finalize keeps it), so
    # the file records ``armed`` true (S7, #473). No ``plan_saved_ts``: an
    # Example is never saved, so ``run_flow`` freezes none for one either.
    s = Session(id=SESSION_ID, name=plan.name, created_ts=1789959852.0,
                updated_ts=1790052600.0, status="dormant",
                plan=plan.model_copy(update={"count_mode": "accepted"}),
                nights=list(RUNS), frames=frames, origin="flow",
                origin_id=EXAMPLE, auto_resume=True)
    path = session_store._path(s.id)
    path.parent.mkdir(parents=True, exist_ok=True)
    write_json_atomic(path, s.model_dump(), backup=False)
    return s


class TestTheContinueFixture:
    async def test_the_file_is_the_routes_answer_and_continue_is_night_three(
            self, rig, monkeypatch):
        """The progress route's answer for the Example and its dormant
        session, re-dumped the way the file is written, IS the file, byte
        for byte. Then CONTINUE on the same flow continues that session and
        says it starts night ``nights + 1``: the night the button's copy
        will print from the file's ``continue_night`` (spec 5.9).

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

        (observed before S7, with the message the assertion had then).

        SINCE S7 (#430, #473) ``nights`` counts observing nights, and the two
        runs here are on two nights, so the recorded 2 did not move. The
        session gained ``armed`` and ``plan_saved_ts``, recorded additively
        (true, and null for an Example), and CONTINUE's night is the nights
        so far plus one only when tonight is not among them, so it is
        pressed at ``CONTINUE_AT``, on an evening the session has not run,
        where it must be ``nights + 1``. The four-key premise against the
        new route, observed:

            AssertionError: premise: the route read the seeded session
            assert {'armed': Tru...ghts': 2, ...} == {'count_mode'...s':
            'dormant'}
              Left contains 2 more items:
              {'armed': True, 'plan_saved_ts': None}

        RED under mutant "tonight is always counted" (``Session.night_at``
        answering ``len(nights)``, never adding tonight), observed:

            AssertionError: CONTINUE starts night 2, and the file says 2
            nights so far, tonight not among them
            assert 2 == (2 + 1)

        and the same under mutant "calendar date without the noon rollover",
        which dates the recorded runs by their small-hours calendar dates,
        2026-09-21 and 2026-09-22, the second of which is tonight's night.

        RED under mutant "a guessed time" (``replay_facts`` answering the
        session's ``created_ts`` when it has none), observed:

            AssertionError: premise: the route read the seeded session
              Differing items:
              {'plan_saved_ts': 1789959852.0} != {'plan_saved_ts': None}

        DELIBERATE RE-RECORDING IN H4 (#511): the session carries
        ``continue_night``, so the file moved, rewritten with
        ``ASTRODECK_REWRITE_S5_FIXTURES=1`` and read as a diff: the key
        added to ``response``'s session (3), and ``same_night_session``
        added beside ``response`` (the session answered at
        ``SAME_NIGHT_AT``, 2), nothing else. Both reads are made with the
        app's clock pinned, so the file is the same bytes whenever it is
        graded. The S7 test against the H4 route, run in scratchpad
        ``H4-ROUTES-B-mut``, observed:

            AssertionError: premise: the route read the seeded session
            assert {'armed': Tru...fa83396', ...} == {'armed': Tru...ghts':
            2, ...}
              Left contains 1 more item:
              {'continue_night': 3}

        CONTINUE is now pressed twice, at each recorded moment, and each
        press must answer the night the file records for that moment: the
        number the button prints (``runCopy``). The two recorded numbers are
        themselves held to the rule, 2 on the second night and 3 the evening
        after, so a rule that moves either is red before the file is graded
        (and a rewrite under it could not pass). Run in scratchpad
        ``H4-ROUTES-B-mut``: mutant "continue_night = nights + 1"
        (``progress.continue_night`` answering
        ``len(session.observing_nights()) + 1``), observed:

            AssertionError: premise: the same-night answer is the second
            night's
            assert 3 == 2

        and mutant "tonight is never among them"
        (``progress.continue_night`` answering
        ``len(session.observing_nights())``), observed:

            AssertionError: premise: the route read the seeded session
              Differing items:
              {'continue_night': 2} != {'continue_night': 3}
        """
        api, engine, night = rig
        s = _seed_dormant_session()
        clock = _PinnedTime(SAME_NIGHT_AT)
        monkeypatch.setattr(app_module, "time", clock)
        # The engine's too: the CONTINUE pressed at SAME_NIGHT_AT mints its
        # report id from it, and so counts on the second recorded night.
        monkeypatch.setattr(engine_module, "time", clock)

        r = await api.progress(EXAMPLE)
        assert r.status_code == 200, r.text
        same = json.loads(r.content)
        clock.t = CONTINUE_AT
        r = await api.progress(EXAMPLE)
        assert r.status_code == 200, r.text
        answer = json.loads(r.content)
        assert answer["session"] == {"id": s.id, "status": "dormant",
                                     "nights": 2, "count_mode": "accepted",
                                     "armed": True, "plan_saved_ts": None,
                                     "continue_night": 3}, (
            "premise: the route read the seeded session")
        assert same["session"]["continue_night"] == 2, (
            "premise: the same-night answer is the second night's")
        assert ({**same, "session": None},
                {**same["session"], "continue_night": None}) == (
            {**answer, "session": None},
            {**answer["session"], "continue_night": None}), (
            "premise: the two answers differ in continue_night alone")
        (block,) = answer["blocks"]
        assert block["group_id"] == engine_group_id(), (
            "premise: the block names the plan's group")
        body = {"response": answer, "same_night_session": same["session"]}
        text = _text(CONTINUE_ABOUT, body)
        if REWRITE:
            _write(CONTINUE_FIXTURE, CONTINUE_ABOUT, body)
        assert _on_disk(CONTINUE_FIXTURE) == text, (
            "the file is not the route's answer; rewrite it (see the module "
            "docstring)")
        recorded = json.loads(CONTINUE_FIXTURE.read_bytes())

        # On the second recorded night: the run route's night is the one
        # the file records for that moment, and not nights + 1.
        clock.t = SAME_NIGHT_AT
        run = await api.client.post(f"/api/flows/{EXAMPLE}/run", json={})
        assert run.status_code == 200, run.text
        out = run.json()["session"]
        assert (out["id"], out["continued"]) == (s.id, True), (
            f"premise: CONTINUE continued the recorded session: {out}")
        said = recorded["same_night_session"]["continue_night"]
        assert out["night"] == said, (
            f"CONTINUE on the second night starts night {out['night']}, and "
            f"the file says the button prints {said}")
        assert out["night"] != recorded["same_night_session"]["nights"] + 1, (
            "premise: here nights + 1 is not the run route's night")
        night.end("incomplete")
        await engine._task
        assert session_store.load(s.id).status == "dormant", (
            "premise: the same-night run left the session dormant again")

        # The evening after: a night the session has not run.
        clock.t = CONTINUE_AT
        run = await api.client.post(f"/api/flows/{EXAMPLE}/run", json={})
        assert run.status_code == 200, run.text
        out = run.json()["session"]
        assert (out["id"], out["continued"]) == (s.id, True), (
            f"premise: CONTINUE continued the recorded session: {out}")
        assert events.night_key(CONTINUE_AT) not in {
            events.night_key(time.mktime(time.strptime(r[-15:],
                                                       "%Y%m%d-%H%M%S")))
            for r in RUNS}, "premise: CONTINUE is pressed on a new night"
        said = recorded["response"]["session"]["continue_night"]
        assert out["night"] == said, (
            f"CONTINUE starts night {out['night']}, and the file says the "
            f"button prints {said}")
        assert out["night"] == recorded["response"]["session"]["nights"] + 1, (
            f"CONTINUE starts night {out['night']}, and the file says "
            f"{recorded['response']['session']['nights']} nights so far, "
            f"tonight not among them")
        assert engine._session.id == s.id


class _PinnedTime:
    """``time`` as the app reads it, with ``time()`` pinned: the tonight
    CONTINUE's night is asked about. Every other name is the real one."""

    def __init__(self, t: float) -> None:
        self.t = t

    def time(self) -> float:
        return self.t

    def __getattr__(self, name: str):
        return getattr(time, name)


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
    "is served it and as a viewer is (panel and pass absent). 'paused', "
    "'holding' and 'aborting' are the first state of each kind whose group "
    "names a panel, as an operator is served it, from a second night of the "
    "same plan with safety armed, no monitor assigned and the frames' own "
    "cloud verdict standing in for one: paused and resumed as the first "
    "frame after 2-2 is set aside opens, the sky shut from the next frame, "
    "and an Abort pressed at the cloud hold's first probe. Built and "
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
    served = _record(night, monkeypatch)
    try:
        done = await night.run(plan, wall_s=120.0, session=_session(night, plan))
    finally:
        await night.close()
    assert done, f"premise: the night ended: {night.trace[-3:]}"
    return served


def _record(night: Night, monkeypatch) -> list[dict]:
    """The list every sequence publish of ``night`` is appended to, as
    ``GET /api/sequence/state`` would have answered at that moment, up to
    the harness's first terminal publish."""
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
    return served


def _session(night: Night, plan: SequencePlan) -> Session:
    """The progress file's session, as CONTINUE hands it to the engine."""
    return Session(id=SESSION_ID, name=plan.name, created_ts=night.clock.t,
                   status="dormant", plan=plan, origin="flow",
                   origin_id=EXAMPLE)


#: What the harness's capture answer carries once the held night's sky has
#: shut: the per-frame verdict the hub's preview writes into ``info["cloud"]``
#: (``CloudResult.to_dict``), read by the engine's own ``verdict_from_info``
#: and debounced by its own ``CloudState`` (two frames agree, then the state
#: turns). The numbers are a starless frame's; only ``cloudy``, ``score`` and
#: ``reason`` are read.
SHUT_SKY = {"cloudy": True, "score": 0.93, "bright_stars": 1,
            "bright_density": 0.51, "contrast": 3.2,
            "reason": "cloudy: 1 bright stars, low contrast (3x noise)"}


async def _held_states(store, monkeypatch) -> list[dict]:
    """A second night of the same plan under the same session, on a fresh
    hub, that pauses, holds for cloud and is aborted, each while the group
    is on a panel (#451): every state it published, as `_served_states`
    records them. Run after the first night, which it cannot move.

    THE PAUSE is the operator's (``engine.pause()``, what POST
    /api/sequence/pause calls), pressed as the first frame after 2-2 is set
    aside opens, and released at once. The state it publishes is the one a
    pause publishes whenever it is pressed; releasing it in the same
    instant keeps the night's clock where it was (the harness advances no
    time while the run waits on a person, so a pause held open would stop
    the night until someone let go of it).

    THE CLOUD HOLD is the engine's own, entered the way the rig entered one
    on 2026-09-23 (test_cloud_hold_watch.py's harness does the same):
    safety armed with no monitor assigned, and the frames' own verdict
    standing in for one. ``hub.mode`` is not "sim" (a simulated frame is no
    sky, and the fallback ignores it), and from the frame after the pause
    each capture's answer carries ``SHUT_SKY``. Nothing in the engine or
    the cloud state is replaced: two cloudy frames turn the debounced
    verdict, and the next panel's gate opens the hold.

    THE ABORT is pressed as the hold's first probe exposure is asked for
    (``save=False``, taken while the state is "holding"): the abort's first
    step, which publishes "aborting", runs while the capture yields to the
    loop without parking the run on the night's clock, so the state it
    publishes is taken at one instant on every run, and the cancellation
    lands in that capture."""
    store.set_safety(SafetyConfig(enabled=True, sky_fallback_hold=True))
    hub, popped = await night_hub(monkeypatch)
    try:
        hub.mode = "native"                 # a real rig: its frames are a sky
        hub.devices.pop("safety", None)     # ...with no monitor assigned
        plan = _night_plan()
        assert plan.safety_check, "premise: the plan honours the safety gate"
        night = Night(hub, monkeypatch, goto=_misses_2_2)
        engine = night.engine
        served = _record(night, monkeypatch)
        marks = {"paused": False, "shut": False, "abort": None}
        harness_capture = hub.capture

        def on_capture(rec):
            if marks["paused"]:
                marks["shut"] = True
            elif (rec["group"] or {}).get("set_aside"):
                marks["paused"] = True
                engine.pause()
                engine.resume()

        async def capture(exposure_s, *a, **kw):
            if (kw.get("save") is False and marks["abort"] is None
                    and engine.state.get("state") == "holding"):
                marks["abort"] = asyncio.get_running_loop().create_task(
                    engine.abort())
                while not engine._aborting:
                    await asyncio.sleep(0)
            info = await harness_capture(exposure_s, *a, **kw)
            if marks["shut"]:
                info = {**info, "cloud": dict(SHUT_SKY)}
            return info

        night.on_capture = on_capture
        monkeypatch.setattr(hub, "capture", capture)
        try:
            done = await night.run(plan, wall_s=120.0,
                                   session=_session(night, plan))
            if marks["abort"] is not None:
                await marks["abort"]
        finally:
            await night.close()
        assert done, f"premise: the held night ended: {night.trace[-3:]}"
        assert marks["abort"] is not None, (
            f"premise: the held night was aborted in its cloud hold: "
            f"{night.trace[-3:]}")
        return served
    finally:
        await close_night_hub(hub, popped)


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


#: The held night's three states, by the run's state the file names them for.
HELD = ("paused", "holding", "aborting")


def _pick_held(served: list[dict]) -> dict:
    """The held night's three states: the first of each run state whose
    group names a panel, as an operator is served it."""
    out = {}
    for kind in HELD:
        s = next((s for s in served if s.get("state") == kind
                  and _group(s).get("panel")), None)
        assert s is not None, (
            f"the held night served no {kind} state whose group names a "
            f"panel: {[(x.get('state'), _group(x).get('panel')) for x in served][-8:]}")
        out[kind] = _redact_sequence_for(s, OPERATOR)
    return out


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
                                                      group_store,
                                                      monkeypatch):
        """The night re-run, the three states picked by the file's rules and
        served as each principal is served, then the held night's three:
        the file, byte for byte. The premises say the recording is what its
        ``about`` claims: a panel shot with 2-2 set aside for its centring,
        a meridian wait whose group an operator is served with its panel and
        pass, and a viewer without them and without anything else taken;
        and a run paused, a run holding for cloud and a run winding down
        from an Abort, each with its group on a panel and 2-2 set aside
        (#451).

        The held states were added in S7 (S7-URUNHOLD) after the first
        three, which did not move: the file's first three states are the
        bytes S5 recorded, and only the ``about`` and the comma after the
        viewer's wait are new above them.

        REWRITTEN FOR H4 by the H4 integration, and read as a diff. Two
        deliberate changes moved it, and nothing else: (1) a centring miss is
        now counted at the pass boundary and not where the visit raises it
        (#534, H4 orchestrator ruling 2), so 2-2's set-aside, which is what
        picks the shooting state and every held state, lands one pass
        later: those four states each read one pass on (``pass`` +1, three
        more frames done and accepted, the clocks 90 s later), while the
        meridian waits did not move; and (2) #513 is fixed (H4-ENG-B), so
        the aborting state carries ``hold: null`` and a sky that is not
        holding. The premise below that pinned #513 as it stood was flipped
        with the file.

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

        The held night's mutants, run in a private copy of ``server/``
        (scratchpad ``S7-URUNHOLD-mut``, each file mutated from a byte
        backup and put back from it, hash-compared after), 2026-09-28:

        RED under mutant "group only while running" (``_set_state`` in
        ``sequence/engine.py`` publishing ``group`` only when the state it
        publishes is "running"), observed:

            AssertionError: the held night served no paused state whose
            group names a panel: [('running', '2-1'), ('running', '2-1'),
            ('running', '1-1'), ('holding', None), ('holding', None),
            ('holding', None), ('aborting', None), ('aborted', None)]

        RED under mutant "the sim's frames are no sky" (`_held_states`
        leaving ``hub.mode`` at "sim", so the fallback never reads a
        frame's verdict and no hold opens; the night then runs to its end),
        observed:

            AssertionError: premise: the held night was aborted in its
            cloud hold: [[3162.304, 'state', {... 'state': 'running',
            'detail': 'M31 2-1: L 30s  [30/30]', ...}], ..., [3162.304,
            'state', {... 'state': 'complete', ...}]]

        RED under mutant "the abort clears the hold" (``abort()`` in
        ``sequence/engine.py`` publishing ``hold=None`` with "aborting", one
        shape of a fix for #513), observed before H4, when this premise
        pinned #513 as it stood:

            AssertionError: the aborting state no longer carries the hold's
            key; #513 is fixed, so rewrite the file and this premise
            together
            assert None == 'clouds'

        (A clear in `_hold_for_clear`'s ``finally`` instead survives: the
        aborting state is published before the cancellation reaches the
        hold, so only the terminal state, which is not recorded, moves.)

        Since H4 #513 is fixed and the premise is flipped. RED under
        H4-ENG-B's mutant "the abort keeps the hold" (``abort()``'s
        "aborting" publish without its ``**self._hold_cleared()``), re-run
        by the H4 integration in a private copy of ``server/`` (scratchpad
        ``H4-INTEG-mut``), observed:

            AssertionError: the aborting state still carries the cloud hold
            (#513): 'clouds', {'cloudy': True, 'age_s': 120, 'score': 0.93,
            ...}
            assert ('clouds', True) == (None, False)

        RED under mutant "fixture edited" (the file's aborting state's
        ``"state": "aborting"`` made "running" by hand), observed:

            AssertionError: the file is not what the engine served; rewrite
            it (see the module docstring)
              Skipping 9016 identical leading characters in diff, use -v to
              show
              - "state": "aborting",
              + "state": "running",
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
        # #166 item 1, FIXED (W2 integration): ``live.meridian_eta_s`` -- the
        # countdown ``redact`` strips from ``meridian.hours_to_flip`` -- is
        # now withheld here too, through ``live`` joining ``_DERIVED_NODES``
        # (api/redact.py). This recorded state is the WAIT'S "waiting" phase
        # (the first publish whose group waits at all, picked by `_pick`
        # above), not the hop that ENDS it, so ``target`` ("M31 2-1", the
        # same panel the group's own ``panel`` key already named to the
        # operator -- the last one actually visited, not the one the
        # crossing will choose) and ``detail`` are still the same for both
        # principals: naming the LAST panel, before any wait began, reveals
        # no transit (spec 6.9's accepted residual). #166 item 1's remaining
        # fix -- withholding ``detail``/``target``/``target_index``/
        # ``schedule``/``session.target`` too -- applies only at the hop
        # that ends the wait (``_hop_site_derived``, `api.redact
        # ._withhold_hop_timing`), which this recorded night's picked "wait"
        # state is not; that case is graded on the clocked simulator by
        # test_w2_hop_state_site_derived.py, not by this byte-for-byte file.
        assert op.get("live", {}).get("meridian_eta_s") and not vw.get(
            "live", {}).get("meridian_eta_s"), (
            f"premise: the operator's live countdown is withheld from the "
            f"viewer: operator {op.get('live')}, viewer {vw.get('live')}")
        assert {k: v for k, v in op.items() if k not in ("group", "live")} == {
            k: v for k, v in vw.items() if k not in ("group", "live")}, (
            "the viewer's state differs outside the group, the live chip "
            "and #166 item 1's hop-timing fields, which today's redaction "
            "does not do")

        held = _pick_held(await _held_states(group_store, monkeypatch))
        paused, holding, aborting = (held[k] for k in HELD)
        for kind, s in held.items():
            assert s["session"]["id"] == SESSION_ID, (
                f"premise: the held night's {kind} state is the progress "
                f"file's session")
            hg = s["group"]
            assert hg["id"] == g["id"] and hg["meridian_wait"] is False, (
                f"premise: the {kind} state's group is the mosaic, on a "
                f"panel: {hg}")
            assert [a["panel"] for a in hg["set_aside"]] == ["2-2"], (
                f"premise: 2-2 is set aside in the {kind} state: {hg}")
        assert paused.get("paused") is True and paused.get("hold") is None, (
            f"premise: the operator's pause: {paused['detail']!r}")
        assert holding.get("hold") == "clouds" and holding["sky"]["holding"], (
            f"premise: a cloud hold: {holding.get('hold')!r}, {holding['sky']}")
        # The Abort lands inside the hold. Until H4 the hold's ``hold:
        # "clouds"`` rode the aborting state through `_set_state`'s merge
        # (#513), and this premise pinned it so. H4-ENG-B fixed #513: abort's
        # "aborting" publish takes the hold off (`_hold_cleared`), before its
        # cancellation reaches the hold, so the wind-down never reads as a
        # hold. Flipped with the file, by the H4 integration.
        assert (aborting.get("hold"), aborting["sky"]["holding"]) == (
            None, False), (
            f"the aborting state still carries the cloud hold (#513): "
            f"{aborting.get('hold')!r}, {aborting['sky']}")
        assert (paused["group"]["panel"], holding["group"]["panel"],
                aborting["group"]["panel"]) == ("1-1", "1-1", "1-1"), (
            "premise: the held night's states are all on 1-1")
        states.update(held)

        body = {"states": states}
        if REWRITE:
            _write(STATE_FIXTURE, STATE_ABOUT, body)
        assert _on_disk(STATE_FIXTURE) == _text(STATE_ABOUT, body), (
            "the file is not what the engine served; rewrite it (see the "
            "module docstring)")
