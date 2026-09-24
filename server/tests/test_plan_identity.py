"""Plan identity at the start paths (#156; spec 3.5, 6.13, S0 item 4).

The session ledger counts frames by step id alone (``Session.accepted_by_step``)
and a ``run_target``/``skip_target`` rule resolves the FIRST target with the
name it carries (``engine.py`` ``_apply_jump``). So a plan whose step ids repeat
lets one step's frames count for every copy - in accepted mode copies 2 to N
read complete the moment the first finishes - and a rule that names a repeated
target acts on whichever copy happens to come first.

``plan_identity_errors`` refuses those plans on all five ``engine.start``
paths. It is deliberately a start-path check and never a pydantic validator:
``SessionStore.load_all`` and ``active`` skip a session that fails validation
without a word (``session.py``), so a validator would make stored sessions
vanish on upgrade, and a refused resume must keep the session listed and say
why.

Every test that guards a branch names the mutant it kills and quotes the
failure that mutant produced, observed by running it from a byte-for-byte
backup of the file under test (in a copy of ``server/``, so no other suite saw
the mutant) and restoring the file byte-identical afterwards.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import astrodeck.api.app as app_module
import astrodeck.hub as hub_module
from astrodeck.flows import wizard as flow_wizard
from astrodeck.persist import write_json_atomic
from astrodeck.sequence.models import (ExposureStep, Instruction, SequencePlan,
                                       Target, duplicate_name_warning,
                                       plan_identity_errors)
from astrodeck.sequence.resume_arm import RETRY_INTERVAL_S, ResumeArm
from astrodeck.sequence.session import Session, SessionFrame, session_store


# ------------------------------------------------------------------ builders

def _target(name: str, *, tid: str | None = None,
            step_ids: tuple[str | None, ...] = (None,),
            ra: float = 5.5, dec: float = -5.0) -> Target:
    """A light target whose ids are minted fresh unless the test pins one."""
    steps = []
    for sid in step_ids:
        kw = {"id": sid} if sid else {}
        steps.append(ExposureStep(filter="L", exposure_s=1.0, count=2, **kw))
    kw = {"id": tid} if tid else {}
    return Target(name=name, ra_hours=ra, dec_deg=dec, center=False,
                  autofocus_first=False, steps=steps, **kw)


def _plan(*targets: Target, rules: tuple[Instruction, ...] = ()) -> SequencePlan:
    return SequencePlan(name="identity", guide=False, dither_every=0,
                        meridian_flip=False, targets=list(targets),
                        instructions=list(rules))


def _jump(action: str, name: str, **kw) -> Instruction:
    return Instruction(trigger="on_target_complete", action=action,
                       target_arg=name, **kw)


def _gate(name: str) -> Instruction:
    return Instruction(trigger="on_frame_rejected", action="notify",
                       only_target=name)


def _repeated_step_id() -> SequencePlan:
    """Two targets whose first steps share an id: a clone, a hand edit, or a
    future deterministic-id bug. Names are distinct, so ONLY the step id is
    wrong - a test on this plan watches the step-id rule and nothing else."""
    return _plan(_target("M42", step_ids=("s-dup",)),
                 _target("M31", step_ids=("s-dup",), ra=0.7, dec=41.3))


def _repeated_name() -> SequencePlan:
    """The classic Plan's routine case: the same object appended twice
    (``store.ts`` ``addTargetsToPlan`` appends single frames). Fresh ids."""
    return _plan(_target("M42"), _target("M42"))


# ------------------------------------------------------- the pure functions

def test_control_a_clean_plan_has_nothing_to_say():
    """CONTROL: distinct ids and names, and a rule naming one of them."""
    plan = _plan(_target("M42"), _target("M31", ra=0.7, dec=41.3),
                 rules=(_jump("run_target", "M31"), _gate("M42")))
    assert plan_identity_errors(plan) == []
    assert duplicate_name_warning(plan) is None


def test_repeated_target_ids_are_refused():
    """RED under mutant "check step ids only" (the target-id loop deleted):

        E   assert 0 == 1
        E    +  where 0 = len([])
    """
    plan = _plan(_target("M42", tid="t-dup"),
                 _target("M31", tid="t-dup", ra=0.7, dec=41.3))
    errors = plan_identity_errors(plan)
    assert len(errors) == 1
    assert "'t-dup'" in errors[0]
    assert "'M42'" in errors[0] and "'M31'" in errors[0]


def test_repeated_step_ids_are_refused_across_the_whole_plan():
    """The ledger is plan-wide, so two targets' steps collide as surely as two
    steps of one target. Both shapes, one test.

    RED under mutant "compare step ids within a target only":

        E   assert [] == ["step id 's-...tep id alone"]
        E     Right contains one more item: "step id 's-dup' is used by 2
              steps ('M42' step 1, 'M31' step 1), and frames are counted by
              step id alone"
    """
    assert plan_identity_errors(_repeated_step_id()) == [
        "step id 's-dup' is used by 2 steps ('M42' step 1, 'M31' step 1), "
        "and frames are counted by step id alone"]
    one = _plan(_target("M42", step_ids=("a", "a")))
    assert plan_identity_errors(one) == [
        "step id 'a' is used by 2 steps ('M42' step 1, 'M42' step 2), "
        "and frames are counted by step id alone"]


@pytest.mark.parametrize("rule", [
    pytest.param(_jump("run_target", "M42"), id="run_target"),
    pytest.param(_jump("skip_target", "M42"), id="skip_target"),
    pytest.param(_gate("M42"), id="only_target"),
    # The engine strips a jump destination before it looks the name up
    # (`_apply_jump`, `_dispatch_actions`), so " M42 " reaches the first M42.
    pytest.param(_jump("run_target", " M42 "), id="padded_destination"),
])
def test_a_repeated_name_that_a_rule_names_is_refused(rule):
    """RED under mutant "never refuse names" (``if name in named`` made
    ``if False``), on every parameter:

        E   assert 0 == 1
        E    +  where 0 = len([])

    RED under mutant "compare the destination unstripped" (``target_arg`` added
    to the named set as written), on ``padded_destination`` only; the other
    three stay green:

        E   assert 0 == 1
        E    +  where 0 = len([])
    """
    plan = _plan(_target("M42"), _target("M42"), rules=(rule,))
    errors = plan_identity_errors(plan)
    assert len(errors) == 1
    assert "'M42'" in errors[0] and "instruction names it" in errors[0]
    assert duplicate_name_warning(plan) is None, (
        "a refused name was also offered as a warning")


def test_control_a_repeated_name_no_rule_names_is_only_a_warning():
    """CONTROL (the classic Plan): the same object appended twice, no rule.
    Refusing this is what would strand dormant classic sessions (Appendix B,
    "duplicate-name refusal strands classic sessions").

    RED under mutant "refuse names always" (``if name in named`` made
    ``if True``):

        E   assert ["target name...l them apart"] == []
        E     Left contains one more item: "target name 'M42' is used by 2
              targets and an instruction names it, so the rule cannot tell
              them apart"
    """
    plan = _repeated_name()
    assert plan_identity_errors(plan) == []
    assert duplicate_name_warning(plan) == (
        "targets share a name ('M42' x2); they run and count separately, "
        "but an instruction could not name just one of them")


def test_control_a_rule_naming_another_target_leaves_the_repeat_a_warning():
    """CONTROL: a rule that names M31 cannot be confused by two M42s, so they
    are not a reason to refuse the night.

    RED under mutant "refuse every repeated name once any rule names a target"
    (``if named`` in place of ``if name in named``; nothing else goes red):

        E   assert ["target name...l them apart"] == []
        E     Left contains one more item: "target name 'M42' is used by 2
              targets and an instruction names it, so the rule cannot tell
              them apart"
    """
    plan = _plan(_target("M42"), _target("M42"),
                 _target("M31", ra=0.7, dec=41.3),
                 rules=(_jump("run_target", "M31"),))
    assert plan_identity_errors(plan) == []
    assert "'M42' x2" in (duplicate_name_warning(plan) or "")


def test_control_a_disabled_rule_names_nothing():
    """CONTROL: ``evaluate_instructions`` skips a disabled rule before it looks
    at a name, so it cannot act on the wrong M42.

    RED under mutant "count disabled rules" (the ``enabled`` test removed;
    nothing else goes red):

        E   assert ["target name...l them apart"] == []
        E     Left contains one more item: "target name 'M42' is used by 2
              targets and an instruction names it, so the rule cannot tell
              them apart"
    """
    plan = _plan(_target("M42"), _target("M42"),
                 rules=(_jump("run_target", "M42", enabled=False),))
    assert plan_identity_errors(plan) == []
    assert duplicate_name_warning(plan) is not None


def test_several_problems_come_back_in_one_answer():
    """An operator fixing a plan one refusal at a time pays a round trip per
    problem; the check reports them all.

    RED under mutant "return at the first problem" (``if errors: return
    errors`` after the target-id loop):

        E   assert 1 == 3
        E    +  where 1 = len(["target id 't-dup' is used by 2 targets
              ('M42', 'M42')"])
    """
    plan = _plan(_target("M42", tid="t-dup", step_ids=("s-dup",)),
                 _target("M42", tid="t-dup", step_ids=("s-dup",)),
                 rules=(_jump("skip_target", "M42"),))
    errors = plan_identity_errors(plan)
    assert len(errors) == 3
    assert errors[0].startswith("target id 't-dup'")
    assert errors[1].startswith("step id 's-dup'")
    assert errors[2].startswith("target name 'M42'")


def test_it_is_not_a_model_validator():
    """The plan the start paths refuse must still CONSTRUCT, or every stored
    session holding one disappears from the store (see the store test below).

    RED under mutant "model validator" (a ``@model_validator(mode="after")``
    on ``SequencePlan`` raising ``ValueError`` on ``plan_identity_errors``):

        E   pydantic_core._pydantic_core.ValidationError: 1 validation error
            for SequencePlan
        E     Value error, step id 's-dup' is used by 2 steps ('M42' step 1,
              'M31' step 1), and frames are counted by step id alone
              [type=value_error, input_value={'name': 'identity', 'gui...))],
              'instructions': []}, input_type=dict]
    """
    plan = _repeated_step_id()
    assert SequencePlan.model_validate(plan.model_dump()).targets[1].name == "M31"


# ------------------------------------------------------------ the store

def _write_raw_session(status: str) -> Session:
    """A session written before this check existed: its second target repeats
    the first one's name AND first step id. The file is written as raw JSON,
    so the test never asks the model to accept the plan; the returned session
    is the clean one it was derived from, and carries the file's id."""
    clean = Session(name="old", created_ts=1.0, status=status,
                    plan=_plan(_target("M42"), _target("M31", ra=0.7, dec=41.3)))
    raw = clean.model_dump(mode="json")
    first, second = raw["plan"]["targets"]
    second["name"] = first["name"]
    second["steps"][0]["id"] = first["steps"][0]["id"]
    write_json_atomic(session_store._path(clean.id), raw, backup=False)
    return clean


def test_a_stored_session_with_repeated_ids_is_still_found(tmp_path, monkeypatch):
    """A session written before this check existed, carrying the plan every
    start path now refuses, is still LISTED: ``load_all`` finds it and, while
    it is the running session, ``active`` returns it.

    RED under mutant "model validator" (a ``@model_validator(mode="after")``
    on ``SequencePlan`` raising ``ValueError`` on ``plan_identity_errors``; the
    session vanishes, silently):

        E   AssertionError: the session vanished from load_all
        E   assert 'f9ba50e0158148fe955f886976236304' in []
    """
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path)
    clean = _write_raw_session("active")

    ids = [s.id for s in session_store.load_all()]
    assert clean.id in ids, "the session vanished from load_all"
    live = session_store.active()
    assert live is not None and live.id == clean.id, "active() lost it"
    # ...and it is exactly the plan the start paths refuse.
    assert plan_identity_errors(live.plan)
    assert duplicate_name_warning(live.plan)


# ------------------------------------------------------------ the routes

@pytest.fixture
def api(tmp_path, monkeypatch):
    """The real app with the camera, the sun check and ``engine.start``
    stubbed (``test_session_quota``'s isolation, plus the flow store for
    ``/run``). A default site never blocks the horizon pre-flight, so the only
    thing between these requests and ``engine.start`` is the guard stack."""
    from astrodeck.config import ConfigStore
    import astrodeck.config as config_mod
    from astrodeck.flows.store import FlowStore
    from astrodeck.plans import PlanLibrary
    from astrodeck.profiles import ProfileLibrary
    temp_store = ConfigStore(path=tmp_path / "astrodeck.json")
    monkeypatch.setattr(config_mod, "config_store", temp_store)
    monkeypatch.setattr(hub_module, "config_store", temp_store)
    monkeypatch.setattr(app_module, "config_store", temp_store)
    monkeypatch.setattr(config_mod, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path / "captures")
    monkeypatch.delenv(app_module.AUTH_ENV_VAR, raising=False)
    monkeypatch.setattr(app_module, "plan_library",
                        PlanLibrary(directory=tmp_path / "plans"))
    monkeypatch.setattr(app_module, "profiles",
                        ProfileLibrary(directory=tmp_path / "profiles"))
    monkeypatch.setattr(app_module, "flow_store", FlowStore(tmp_path / "flows"))
    app = app_module.create_app()
    with TestClient(app) as c:
        monkeypatch.setattr(app_module.hub, "_check_solar", lambda *a, **kw: None)
        monkeypatch.setattr(app_module.hub, "require", lambda role: object())
        starts: list[SequencePlan] = []
        monkeypatch.setattr(app_module.engine, "start",
                            lambda plan, **kw: starts.append(plan))
        c.starts = starts
        yield c


def _stored_session(plan: SequencePlan) -> Session:
    """A dormant session WITH a frame, so it is both resumable by id and the
    one ``/api/sequence/recover`` picks."""
    s = Session(name="stored", created_ts=1.0, status="dormant", plan=plan)
    s.frames.append(SessionFrame(ts=1.0, night="n1",
                                 target_id=plan.targets[0].id,
                                 step_id=plan.targets[0].steps[0].id))
    session_store.save(s)
    return s


def _request(api, monkeypatch, path: str, plan: SequencePlan, *,
             force: bool = False):
    """Put ``plan`` through one of the four HTTP start paths. ``force`` sends
    every override the route has: ``force`` on /start, ``force`` and
    ``accept_unmapped`` on /run."""
    if path == "start":
        return api.post("/api/sequence/start",
                        json={**plan.model_dump(mode="json"), "force": force})
    if path == "run":
        # A normal flow compiles uuid4 ids and can never reach this branch, so
        # the compile's output is replaced after the graph has passed its own
        # structural checks. The route looks the name up in the app module's
        # globals at call time, so this is the function it calls.
        saved = api.post("/api/flows", json={"flow": {
            "name": "identity", "graph": flow_wizard.quick(
                {"name": "M42", "ra": "05h 35m 17s", "dec": "-05 23 28"},
                subs_per_filter=2, filters=["L"], wheel=["L"],
            ).graph.model_dump(by_alias=True)}})
        assert saved.status_code == 200, saved.text
        monkeypatch.setattr(app_module, "to_sequence_plan",
                            lambda *a, **kw: (plan, []))
        return api.post(f"/api/flows/{saved.json()['id']}/run",
                        json={"force": force, "accept_unmapped": force})
    s = _stored_session(plan)
    if path == "resume":
        return api.post(f"/api/sessions/{s.id}/resume")
    assert path == "recover"
    return api.post("/api/sequence/recover")


PATHS = [pytest.param("start", 422, id="sequence_start"),
         pytest.param("run", 422, id="flow_run"),
         pytest.param("resume", 409, id="session_resume"),
         pytest.param("recover", 409, id="sequence_recover")]


@pytest.mark.parametrize("path, status", PATHS)
def test_repeated_step_ids_are_refused_on_every_route(api, monkeypatch,
                                                      path, status):
    """422 where the request carries the plan, 409 where a stored session
    does, and ``engine.start`` is never reached.

    Each mutant below deletes the ``_refuse_plan_identity`` call on ONE route
    and turns exactly that parameter red; the other three stay green.

    Mutant "drop the check on /start" (``sequence_start``):

        E   AssertionError: {"started":true,"frames":4}
        E   assert 200 == 422

    Mutant "drop the check on /run" (``flow_run``; this is the reachability
    proof for the patched compile):

        E   AssertionError: {"started":true,"flow_id":"8629eab5664e4a3d97f32d
            ac09bc2830","frames":4,"unmapped":[]}
        E   assert 200 == 422

    Mutant "drop the check on resume" (``session_resume``):

        E   AssertionError: {"resumed":true,"remaining":1}
        E   assert 200 == 409

    Mutant "drop the check on recover" (``sequence_recover``):

        E   AssertionError: {"resumed":true,"frames_remaining":1}
        E   assert 200 == 409

    The two resume answers show #156 itself: this plan owes 3 frames (2 + 2,
    one banked) and the ledger says 1, because ``Session.remaining`` keys by
    step id and the two copies collapse into one entry.
    """
    r = _request(api, monkeypatch, path, _repeated_step_id())
    assert r.status_code == status, r.text
    detail = r.json()["detail"]
    assert detail["code"] == "plan_identity"
    assert "step id 's-dup' is used by 2 steps" in detail["detail"]
    assert api.starts == [], "engine.start was reached"


def test_a_refused_resume_keeps_the_session_listed_and_fixable(api):
    """Appendix B, "duplicate-name refusal strands classic sessions", end to
    end. A dormant session saved before this check existed is listed, its
    resume is refused with the reason, it is STILL listed and dormant, and
    once the plan is fixed (PATCH, dormant-only) the same session resumes.
    Written raw, so nothing here needs the model to accept the plan.

    RED under mutant "model validator" (the session is gone before the
    operator can even try):

        E   AssertionError: the stored session is not listed
        E   assert [] == ['dormant']

    RED under mutant "drop the check on resume" (#156 in one line: the plan
    owes 4 frames with none banked, and the ledger says 2):

        E   AssertionError: {"resumed":true,"remaining":2}
        E   assert 200 == 409

    RED under mutant "refuse names always", at the resume AFTER the fix: the
    fixed plan still repeats a name no rule names, the classic Plan's case,
    and the session would stay stranded:

        E   AssertionError: {"detail":{"detail":"target name 'M42' is used by
            2 targets and an instruction names it, so the rule cannot tell
            them apart","code":"plan_identity"}}
        E   assert 409 == 200
    """
    s = _write_raw_session("dormant")

    def listed() -> list[str]:
        return [row["status"] for row in api.get("/api/sessions").json()["sessions"]
                if row["id"] == s.id]

    assert listed() == ["dormant"], "the stored session is not listed"
    r = api.post(f"/api/sessions/{s.id}/resume")
    assert r.status_code == 409, r.text
    assert r.json()["detail"]["code"] == "plan_identity"
    assert listed() == ["dormant"], "the refused resume unlisted the session"
    assert api.starts == []

    plan = api.get(f"/api/sessions/{s.id}").json()["plan"]
    plan["targets"][1]["steps"][0]["id"] = "fixed-step"
    fixed = api.patch(f"/api/sessions/{s.id}", json={"plan": plan})
    assert fixed.status_code == 200, fixed.text
    r = api.post(f"/api/sessions/{s.id}/resume")
    assert r.status_code == 200, r.text
    assert [[st.id for t in p.targets for st in t.steps][1]
            for p in api.starts] == ["fixed-step"]


def test_repeated_target_ids_are_refused_at_start(api):
    """The same rule end to end, on the path a client can hand ids to.

    RED under mutant "check step ids only", and under "drop the check on
    /start":

        E   AssertionError: {"started":true,"frames":4}
        E   assert 200 == 422
    """
    plan = _plan(_target("M42", tid="t-dup"),
                 _target("M31", tid="t-dup", ra=0.7, dec=41.3))
    r = api.post("/api/sequence/start", json=plan.model_dump(mode="json"))
    assert r.status_code == 422, r.text
    assert "target id 't-dup'" in r.json()["detail"]["detail"]
    assert api.starts == []


def test_a_repeated_name_a_run_target_rule_names_is_refused_at_start(api):
    """RED under mutant "never refuse names", and under "drop the check on
    /start":

        E   AssertionError: {"started":true,"frames":4}
        E   assert 200 == 422
    """
    plan = _plan(_target("M42"), _target("M42"),
                 rules=(_jump("run_target", "M42"),))
    r = api.post("/api/sequence/start", json=plan.model_dump(mode="json"))
    assert r.status_code == 422, r.text
    assert "target name 'M42'" in r.json()["detail"]["detail"]
    assert api.starts == []


def test_the_refusal_carries_every_problem(api):
    """The route hands the operator the whole list, not the first sentence.

    RED under mutant "report only the first" (``errors[0]`` in place of
    ``"; ".join(errors)`` in ``_refuse_plan_identity``):

        E   assert "target id 't...'M42', 'M42')" == "target id 't...ll them
            apart"
        E     Skipping 42 identical leading characters in diff, use -v to show
        E     - 42', 'M42'); step id 's-dup' is used by 2 steps ('M42' step 1,
              'M42' step 1), and frames are counted by step id alone; target
              name 'M42' is used by 2 targets and an instruction names it, so
              the rule cannot tell them apart
        E     + 42', 'M42')
    """
    plan = _plan(_target("M42", tid="t-dup", step_ids=("s-dup",)),
                 _target("M42", tid="t-dup", step_ids=("s-dup",)),
                 rules=(_jump("skip_target", "M42"),))
    r = api.post("/api/sequence/start", json=plan.model_dump(mode="json"))
    assert r.status_code == 422, r.text
    assert r.json()["detail"]["detail"] == "; ".join(plan_identity_errors(plan))
    assert r.json()["detail"]["detail"].count("; ") == 2


@pytest.mark.parametrize("path", [pytest.param("start", id="sequence_start"),
                                  pytest.param("run", id="flow_run")])
def test_no_override_reaches_past_the_check(api, monkeypatch, path):
    """``force`` overrides the horizon pre-flight and ``accept_unmapped`` the
    unmapped list. Neither says anything about the plan's shape, so neither
    gets a repeated step id past the check. Both call sites say so in a
    comment; without this test nothing held them to it.

    The forced CONTROL goes first and must start, so the refusal is watched
    on a request that could have reached ``engine.start``.

    RED under mutant "force bypasses /start" (``if not force:`` in front of
    the check), on ``sequence_start`` only:

        E   AssertionError: {"started":true,"frames":4}
        E   assert 200 == 422

    RED under mutant "force bypasses /run" (``if not body.force:`` in front
    of the check), on ``flow_run`` only:

        E   AssertionError: {"started":true,"flow_id":"f0ea58d31cf44fd3a90ab0b5
            1b42edb0","frames":4,"unmapped":[]}
        E   assert 200 == 422
    """
    control = _request(api, monkeypatch, path, _repeated_name(), force=True)
    assert control.status_code == 200, control.text
    r = _request(api, monkeypatch, path, _repeated_step_id(), force=True)
    assert r.status_code == 422, r.text
    assert r.json()["detail"]["code"] == "plan_identity"
    assert len(api.starts) == 1, "the forced refusal reached engine.start"


@pytest.mark.parametrize("path, _status", PATHS)
def test_control_repeated_names_start_on_every_route_with_a_warning(
        api, monkeypatch, bus_lines, path, _status):
    """CONTROL: the classic Plan's repeated name, no rule naming it. Every
    route carries on to ``engine.start`` and logs the warning through the bus.

    RED under mutant "refuse names always", on every parameter:

        E   AssertionError: {"detail":{"detail":"target name 'M42' is used by
            2 targets and an instruction names it, so the rule cannot tell
            them apart","code":"plan_identity"}}
        E   assert 422 == 200        (409 == 200 on resume and recover)

    RED under mutant "drop the warning" (the ``bus.log`` in
    ``_refuse_plan_identity`` made ``pass``), on every parameter, and under
    each "drop the check on <route>" mutant on that route's parameter:

        E   AssertionError: the duplicate-name warning was not logged
        E   assert []
    """
    r = _request(api, monkeypatch, path, _repeated_name())
    assert r.status_code == 200, r.text
    assert len(api.starts) == 1
    warned = [m for lvl, m, src in bus_lines
              if lvl == "warning" and "targets share a name ('M42' x2)" in m]
    assert warned, "the duplicate-name warning was not logged"


# ------------------------------------------------------------ ResumeArm

class _Engine:
    """What ``tick`` reads of the engine: ``running``, and ``start`` itself."""
    running = False

    def __init__(self) -> None:
        self.starts: list[tuple[SequencePlan, dict]] = []

    def start(self, plan, **kw) -> None:
        self.starts.append((plan, kw))


class _Hub:
    site: dict = {}
    dusk_arm = None

    def require(self, role):
        return object()


@pytest.fixture
def arm(tmp_path, monkeypatch):
    """A real ``ResumeArm.tick`` with the sky open, the devices ready and the
    recovery ladder replaced by a spy. The ladder is where the mount MOVES
    (a blind solve and a re-centring slew), so a refusal that lands after it
    has already done the damage it exists to prevent."""
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path)
    recovers: list[str] = []

    async def spy_recover(self, session):
        recovers.append(session.id)
        return None

    monkeypatch.setattr(ResumeArm, "_window_open", lambda self, s, t: True)
    monkeypatch.setattr(ResumeArm, "_devices_ready", lambda self: True)
    monkeypatch.setattr(ResumeArm, "_recover", spy_recover)
    engine = _Engine()
    now = {"t": 1_700_000_000.0}
    return SimpleNamespace(arm=ResumeArm(engine, _Hub(), clock=lambda: now["t"]),
                           engine=engine, recovers=recovers, now=now)


def _armed(plan: SequencePlan) -> Session:
    s = Session(name="armed", status="dormant", plan=plan, auto_resume=True)
    session_store.save(s)
    return s


async def test_control_resume_arm_reaches_the_ladder_and_starts(arm, bus_lines):
    """CONTROL and reachability proof: in this same harness a plan with only a
    repeated name (no rule) goes through the ladder to ``engine.start`` and
    logs the warning - so the refusal test below watches a tick that COULD
    have started.

    RED under mutant "refuse names always" (the ladder never ran):

        E   AssertionError: assert [] == ['055a4af4c92...10e66e5827cd']
        E     Right contains one more item: '055a4af4c92b4300a46810e66e5827cd'

    RED under mutant "drop the ResumeArm warning" (its ``bus.log`` made
    ``pass``):

        E   AssertionError: the duplicate-name warning was not logged
        E   assert []
    """
    s = _armed(_repeated_name())
    await arm.arm.tick()
    assert arm.recovers == [s.id]
    assert len(arm.engine.starts) == 1
    assert arm.arm.hold is None
    warned = [m for lvl, m, _src in bus_lines
              if lvl == "warning" and "targets share a name ('M42' x2)" in m]
    assert warned, "the duplicate-name warning was not logged"


async def test_resume_arm_refuses_before_the_ladder_moves_the_mount(arm, bus_lines):
    """Refused beside the ``quota_unbounded`` refusal: a warning, a hold whose
    reason names the duplicate, the ten-minute retry, and neither the ladder
    nor ``engine.start``. The session stays dormant AND armed, and its crash
    counter stays at zero.

    RED under mutant "drop the ResumeArm check" (``identity = []``):

        E   AssertionError: the recovery ladder ran for a plan that cannot
            start
        E   assert ['e96d33dd69a...f54c632b0ed6'] == []
        E     Left contains one more item: 'e96d33dd69ad4d9daee5f54c632b0ed6'

    RED under mutant "check after the ladder" (the same refusal moved below
    ``_recover``: it still holds and never starts, but only after the mount
    has moved):

        E   AssertionError: the recovery ladder ran for a plan that cannot
            start
        E   assert ['507af2ec2a7...fe76f931b6c0'] == []
        E     Left contains one more item: '507af2ec2a74467e8918fe76f931b6c0'

    RED under mutant "the refusal counts as a crash" (``armed.crash_resumes
    += 1`` and a save, ahead of the hold):

        E   AssertionError: assert ('dormant', True, 1) == ('dormant', True, 0)
        E     At index 2 diff: 1 != 0
    """
    s = _armed(_repeated_step_id())
    await arm.arm.tick()
    assert arm.recovers == [], (
        "the recovery ladder ran for a plan that cannot start")
    assert arm.engine.starts == []
    assert "step id 's-dup' is used by 2 steps" in arm.arm.hold["reason"]
    assert arm.arm._retry_at == arm.now["t"] + RETRY_INTERVAL_S
    refused = [m for lvl, m, _src in bus_lines
               if lvl == "warning" and m.startswith("auto-resume refused")]
    assert len(refused) == 1 and "'s-dup'" in refused[0], refused
    stored = session_store.load(s.id)
    # A refusal is not a crash: RESUME_GIVE_UP_AFTER counted refusals would
    # park, warm and DISARM the rig half an hour in, over a plan an operator
    # can fix in one PATCH.
    assert (stored.status, stored.auto_resume, stored.crash_resumes) == (
        "dormant", True, 0)

    # The retry: nothing inside the backoff, another refusal after it.
    arm.now["t"] += RETRY_INTERVAL_S / 2
    await arm.arm.tick()
    arm.now["t"] += RETRY_INTERVAL_S / 2 + 1
    await arm.arm.tick()
    refused = [m for lvl, m, _src in bus_lines
               if lvl == "warning" and m.startswith("auto-resume refused")]
    assert len(refused) == 2
    assert arm.recovers == [] and arm.engine.starts == []
    stored = session_store.load(s.id)
    assert (stored.status, stored.auto_resume, stored.crash_resumes) == (
        "dormant", True, 0)
