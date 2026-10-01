"""Target gains its centring and hop-focus fields (#170 U-03, #189 U-05; spec
3.4, 5.6 steps 3 and 6, S1 item 1).

NULL MEANS TODAY'S CALL. The engine has never passed a tolerance or an attempt
count to ``hub.goto_and_center`` (#170), so every night so far has centred to
the hub's own defaults: 0.02 deg (1.2 arcmin) and 3 attempts. ``_setup_target``
is to pass each kwarg only when the target sets it (S1), which makes ``None``
the one value that reproduces the call every saved plan makes today. A default of
3 attempts looks harmless because it equals the hub's number, but it would set
the kwarg on every plan ever saved, and "exactly today's arguments when unset"
could never be true again.

THE BOUNDS ARE THE HUB'S FAILURE MODES. The centring loop succeeds on
``err <= tolerance_deg`` and calls a mount stuck when the residual is above
five tolerances and moved under 0.5 arcmin between attempts (``hub.py``
``goto_and_center``). A tolerance of 0 can never be met, and the stuck rule
then reads a CONVERGED mount as one refusing its slews. Zero attempts never
enter the loop at all and return ``error_arcmin`` 0: a zero residual from a
centring that never took a frame.

Every test that guards a branch names the mutant it kills and quotes the
failure that mutant produced, observed by running it from a byte-for-byte
backup of ``models.py`` in a copy of ``server/`` (so no other suite saw the
mutant) and confirming the file byte-identical afterwards. Before the fields
existed, all nineteen cases here were red (observed with the three field
lines removed from ``models.py``, which also turns both golden tests red).
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

import astrodeck.api.app as app_module
import astrodeck.hub as hub_module
from astrodeck.persist import write_json_atomic
from astrodeck.sequence.models import SequencePlan, Target
from astrodeck.sequence.session import Session, SessionStore

#: The three fields and the value each takes when nothing sets it.
DEFAULTS = {"center_tolerance_arcmin": None, "center_attempts": None,
            "autofocus_skip_if_fresh": False}

#: Each value the model must refuse, and why it is not a centring.
REFUSED = [
    pytest.param("center_tolerance_arcmin", 0, id="tolerance-0"),
    pytest.param("center_tolerance_arcmin", 30.5, id="tolerance-30.5"),
    pytest.param("center_attempts", 0, id="attempts-0"),
    pytest.param("center_attempts", 11, id="attempts-11"),
    # A count, not a measure: the hub loops ``range(1, max_attempts + 1)``,
    # which raises TypeError on 2.5, so a fractional count saved tonight would
    # crash the target's setup the night S1-12 starts passing it.
    pytest.param("center_attempts", 2.5, id="attempts-2.5"),
]

#: The edges the bounds must still let through. A bound tightened by one step
#: (``le`` to ``lt``, ``ge`` to ``gt``) refuses one of these.
ACCEPTED = [
    pytest.param("center_tolerance_arcmin", 0.1, id="tolerance-0.1"),
    pytest.param("center_tolerance_arcmin", 30, id="tolerance-30"),
    pytest.param("center_attempts", 1, id="attempts-1"),
    pytest.param("center_attempts", 10, id="attempts-10"),
]


def _fields(target: Target) -> dict:
    return {k: getattr(target, k) for k in DEFAULTS}


def _target(**kw) -> Target:
    return Target(name="M42", ra_hours=5.5, dec_deg=-5.0, center=False,
                  autofocus_first=False,
                  steps=[{"filter": "L", "exposure_s": 1.0, "count": 2}], **kw)


def _plan(**kw) -> SequencePlan:
    return SequencePlan(name="centring", guide=False, dither_every=0,
                        meridian_flip=False, targets=[_target(**kw)])


# ------------------------------------------------------ the defaults, and back-compat

def test_control_a_target_that_sets_nothing_is_todays_target():
    """THE DEFAULTS CASE. S1-12 is to pass ``tolerance_deg`` and
    ``max_attempts`` only when these are set, so their default IS the call the
    engine makes.

    RED under mutant "center_attempts default 3":

        E   AssertionError: assert {'autofocus_s...arcmin': None} ==
            {'autofocus_s...arcmin': None}
        E     Omitting 2 identical items, use -vv to show
        E     Differing items:
        E     {'center_attempts': 3} != {'center_attempts': None}

    RED under mutant "autofocus_skip_if_fresh default True":

        E     Differing items:
        E     {'autofocus_skip_if_fresh': True} != {'autofocus_skip_if_fresh':
              False}

    Each also turns the two back-compat controls below red with its own
    differing item, and both golden tests in
    ``test_a_clean_flow_is_not_ten_warnings``.
    """
    assert _fields(_target()) == DEFAULTS


def test_control_a_plan_written_without_the_fields_reads_the_defaults():
    """A plan saved before S1, as JSON: the three keys are simply absent, and
    the target must read as it always ran. Written by hand rather than dumped
    from the model, so the model cannot have put the keys in."""
    raw = {"name": "old", "targets": [{
        "name": "M42", "ra_hours": 5.5, "dec_deg": -5.0,
        "steps": [{"filter": "L", "exposure_s": 60, "count": 5}]}]}
    assert not set(DEFAULTS) & set(raw["targets"][0]), "premise: keys absent"
    plan = SequencePlan.model_validate(raw)
    assert _fields(plan.targets[0]) == DEFAULTS


def test_control_a_stored_session_without_the_fields_loads(tmp_path,
                                                          monkeypatch):
    """A session file a pre-S1 build wrote. ``SessionStore.load`` must return
    it with today's behaviour, and ``load_all`` must still list it: that scan
    skips a file that fails validation without a word, so a required field
    here would make a dormant multi-night session vanish on upgrade."""
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path)
    store = SessionStore()
    old = Session(name="old", created_ts=1.0, status="dormant", plan=_plan())
    raw = old.model_dump(mode="json")
    for t in raw["plan"]["targets"]:
        for key in DEFAULTS:
            t.pop(key, None)
    write_json_atomic(store._path(old.id), raw, backup=False)

    loaded = store.load(old.id)
    assert _fields(loaded.plan.targets[0]) == DEFAULTS
    assert old.id in [s.id for s in store.load_all()], \
        "the pre-S1 session vanished from load_all"


def test_control_set_values_survive_a_session_round_trip(tmp_path,
                                                        monkeypatch):
    """A resumed night must centre and focus the way the first night did, so
    the values have to be in the file the session writes, not just in memory.

    RED under mutant "center_tolerance_arcmin exclude=True" (the field kept
    out of ``model_dump``, so the session file never holds it):

        E   AssertionError: assert {'autofocus_s...arcmin': None} ==
            {'autofocus_s..._arcmin': 0.5}
        E     Differing items:
        E     {'center_tolerance_arcmin': None} != {'center_tolerance_arcmin':
              0.5}
    """
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path)
    store = SessionStore()
    set_ = {"center_tolerance_arcmin": 0.5, "center_attempts": 5,
            "autofocus_skip_if_fresh": True}
    s = Session(name="set", created_ts=1.0, status="dormant",
                plan=_plan(**set_))
    store.save(s)
    assert _fields(store.load(s.id).plan.targets[0]) == set_


# ------------------------------------------------------------ the bounds, in the model

@pytest.mark.parametrize("field, value", REFUSED)
def test_the_model_refuses_a_centring_nothing_can_meet(field, value):
    """Each mutant below turns exactly its own parameter red; the others stay
    green.

    RED under mutant "tolerance ge=0" (``gt=0`` weakened to ``ge=0``; a
    0 arcmin tolerance is one no solve residual can meet), on tolerance-0:

        E   Failed: DID NOT RAISE <class
            'pydantic_core._pydantic_core.ValidationError'>

    RED under mutant "tolerance no ceiling" (``le=30`` deleted), on
    tolerance-30.5, with the same line. RED under mutant "attempts ge=0", on
    attempts-0, with the same line. RED under mutant "attempts no ceiling"
    (``le=10`` deleted), on attempts-11, with the same line. RED under mutant
    "center_attempts float" (``int | None`` widened to ``float | None``), on
    attempts-2.5, with the same line.
    """
    with pytest.raises(ValidationError) as caught:
        _target(**{field: value})
    assert [e["loc"] for e in caught.value.errors()] == [(field,)], \
        caught.value.errors()


@pytest.mark.parametrize("field, value", ACCEPTED)
def test_control_the_edges_of_the_bounds_are_accepted(field, value):
    """CONTROL: the refusals above come from the bounds the spec sets and not
    from bounds one step tighter. Within this test each mutant turns exactly
    its own parameter red ("tolerance ge=1" also refuses the 0.5 the two
    round-trip controls set).

    RED under mutant "tolerance lt=30" (``le=30`` tightened), on tolerance-30:

        E   pydantic_core._pydantic_core.ValidationError: 1 validation error
            for Target
        E   center_tolerance_arcmin
        E     Input should be less than 30 [type=less_than, input_value=30,
              input_type=int]

    RED under mutant "tolerance ge=1" (a whole-arcmin floor), on
    tolerance-0.1:

        E     Input should be greater than or equal to 1
              [type=greater_than_equal, input_value=0.1, input_type=float]

    RED under mutant "attempts gt=1", on attempts-1:

        E     Input should be greater than 1 [type=greater_than,
              input_value=1, input_type=int]

    RED under mutant "attempts lt=10", on attempts-10:

        E     Input should be less than 10 [type=less_than, input_value=10,
              input_type=int]
    """
    assert getattr(_target(**{field: value}), field) == value


# ------------------------------------------------------------- the bounds, at the route

@pytest.fixture
def api(tmp_path, monkeypatch):
    """The real app with the camera, the sun check and ``engine.start``
    stubbed (``test_plan_identity``'s isolation). A default site never blocks
    the horizon pre-flight, so the only thing between these requests and
    ``engine.start`` is the body's validation and the guard stack."""
    from astrodeck.config import ConfigStore
    import astrodeck.config as config_mod
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
    app = app_module.create_app()
    with TestClient(app) as c:
        monkeypatch.setattr(app_module.hub, "_check_solar", lambda *a, **kw: None)
        monkeypatch.setattr(app_module.hub, "require", lambda role: object())
        starts: list[SequencePlan] = []
        monkeypatch.setattr(app_module.engine, "start",
                            lambda plan, **kw: starts.append(plan))
        c.starts = starts
        yield c


def _post_start(api, **target_fields):
    """``POST /api/sequence/start`` with a plan whose one target carries
    ``target_fields`` exactly as a client would send them: added to the JSON,
    never through the model, so a value the model refuses still reaches the
    route."""
    body = _plan().model_dump(mode="json")
    body["targets"][0].update(target_fields)
    return api.post("/api/sequence/start", json={**body, "force": False})


@pytest.mark.parametrize("field, value", REFUSED)
def test_the_start_route_refuses_a_centring_nothing_can_meet(api, field,
                                                            value):
    """422 from the BODY'S validation, naming the field, and ``engine.start``
    never reached. The route answers 422 for other reasons too (no frames, a
    repeated id), so the status alone would not say which rule fired.

    RED under mutant "tolerance ge=0", on tolerance-0 only (the run starts):

        E   AssertionError: {"started":true,"frames":2}
        E   assert 200 == 422
        E    +  where 200 = <Response [200 OK]>.status_code

    The three other bound mutants, and "center_attempts float" on
    attempts-2.5, turn their own parameter red here with the same lines.
    Before the fields existed all five started a run, the unknown
    key dropped without a word, which is #170's shape at the plan route.
    """
    r = _post_start(api, **{field: value})
    assert r.status_code == 422, r.text
    locs = [e["loc"] for e in r.json()["detail"]]
    assert locs == [["body", "targets", 0, field]], locs
    assert api.starts == [], "engine.start was reached"


def test_control_the_start_route_carries_set_values_to_the_engine(api):
    """CONTROL: the same request with values inside the bounds starts, and the
    plan ``engine.start`` receives still carries them. The route rebuilds the
    plan from its body (``SequencePlan.model_validate(body.model_dump(...))``),
    so a field that did not survive that rebuild would reach the engine as
    None and centre to 1.2 arcmin whatever the operator set.

    RED under mutant "center_tolerance_arcmin exclude=True", which drops the
    field in that rebuild:

        E   AssertionError: assert {'autofocus_s...arcmin': None} ==
            {'autofocus_s..._arcmin': 0.5}
        E     Differing items:
        E     {'center_tolerance_arcmin': None} != {'center_tolerance_arcmin':
              0.5}
    """
    set_ = {"center_tolerance_arcmin": 0.5, "center_attempts": 5,
            "autofocus_skip_if_fresh": True}
    r = _post_start(api, **set_)
    assert r.status_code == 200, r.text
    assert len(api.starts) == 1
    assert _fields(api.starts[0].targets[0]) == set_
