"""POST /api/flows/wizard — the route that was missing under the sheet.

`flows/wizard.py` shipped complete: `generate()`, `generate_record()` and
`flow_name()`, with `tests/test_flows_wizard.py` enumerating every kind against
every subset of the six automation chips and requiring the doctor to return
nothing above `note`. No route reached any of it, so the sheet's GENERATE FLOW
button was an honest-disabled control saying so, and the only way out of the
wizard was START BLANK.

The alternative was re-implementing `genWizard()` in TypeScript, which would
make three transcriptions of one rule set: the prototype, wizard.py, and the
client. wizard.py's own header names that drift as the thing the project keeps
re-finding. So the rules stay on the server and the client asks.

What this file pins is the ROUTE, not the generator:

  * the three answers reach the generator intact, including a comma list
  * the record is PERSISTED, so the sheet can open it immediately
  * it lands in My flows and is editable, never a read-only fixture
  * the generated graph passes the doctor, through the route as well as in
    the unit tests -- an endpoint that returns a warned graph would teach a
    first-time user that the doctor is decoration
  * an unknown kind or chip is refused rather than silently generating the
    default night

SLICE S3 (#189 spec 1.8, #196; task S3-A) adds the mosaic kind's answers
(``rows``, ``cols``, ``overlap_pct``, ``angle_mode``, ``pa_deg``,
``use_measured``), checked at the door against the wizard's own constants,
and two rig facts the route injects and a client never sends: the live
camera field (``hub.effective_optics`` through the route's ``RigFacts``,
which also says whether the profile has a rotator) and the angle the last
solve measured (``hub.last_sky_angle``, for USE MEASURED). With no optics the
wizard answers one target, and the answer's ``notes`` say why.

THE FIXTURE ISOLATES THE CONFIG (S3, #341). It used to patch only the flow
library, so the wizard read the process-wide config store; since the route
injects the optics, a mosaic's answer turned on whatever that store held.
S3 gave it a throwaway store patched into three modules; S4 moved it onto
conftest's ``isolated_config``, which sweeps the store into every module
that holds one and moves the profile library, the capture root, the sky
angle and the hub's camera and rotator with it. A throwaway config has no
optics until a test sets them. Checked under two stand-in "real" configs
(``ASTRODECK_CONFIG_DIR``), one with optics, an active profile with its own
optics and a rotator (A), and one fresh (B): every test here answered the
same under both (S4-TESTHYG).

What #341 feared, measured on the Mosaic and the three other kinds of
``test_every_kind_passes_the_doctor_through_the_route`` and on
``test_a_mosaic_with_no_optics_answers_one_target_and_the_reason``, in a
private copy of ``server/`` (scratchpad s4-testhyg-mut2):

    fixture          session net   under A                  under B
    #341's own       on            5 passed                 5 passed
    #341's own       off           2 failed, 5 errors       5 passed, 5 errors
    isolated_config  off           5 passed                 5 passed
    isolated_config  on            5 passed                 5 passed

So the machine never reached #341's fixture while conftest's session
fixture had the process store on a throwaway file (since 2026-07-29): the
answer turned on a shared store, not on the developer's rig. With that net
removed it does ("2 failed" under A: the Mosaic with no grid was refused,
``assert 422 == 200``, "a mosaic's rows is a whole number from 1 to 10,
not None"; and the no-optics case came back tiled, "no optics: one target,
not a grid"), and conftest's ``_no_test_reads_the_real_config`` errors
every test that read the stand-in (the "errors", through
``ConfigStore._load``, ``ConfigStore.cfg`` and ``ProfileLibrary.get``).
``isolated_config`` answers the same with or without the net.

Each S3 test names its mutant and quotes the failure it produced; mutants
were written over a byte copy of ``api/app.py`` in a private copy of
``server/`` under the session scratchpad (``s3-a-routes-k7m2``).
"""
import pytest
from fastapi.testclient import TestClient

import astrodeck.api.app as app_module
from astrodeck.config import Optics
from astrodeck.flows import wizard
from astrodeck.flows.compile import PASS_PORT, is_multi_panel
from astrodeck.flows.doctor import check as flow_doctor
from astrodeck.flows.models import MY_FLOWS_FOLDER, FlowGraph
from astrodeck.flows.store import flow_store
from astrodeck.flows.to_plan import GRID_MAX, OVERLAP_MAX_PCT
from astrodeck.profiles import Profile, ProfileDevice


@pytest.fixture
def client(isolated_config, tmp_path, monkeypatch):
    monkeypatch.setattr(flow_store, "_dir", tmp_path / "flows", raising=False)
    app = app_module.create_app()
    isolated_config.sweep()
    with TestClient(app) as c:
        c.store = isolated_config.store
        yield c


def _post(c, **body):
    return c.post("/api/flows/wizard", json=body)


def test_the_route_exists_and_generates(client):
    r = _post(client, kind=wizard.KIND_DEEP_SKY, options=["Guiding"], target="M16")
    assert r.status_code == 200, f"the generator is still unreachable: {r.text[:300]}"
    rec = r.json()
    assert rec.get("id"), "a generated flow with no id cannot be opened"
    assert rec["graph"]["nodes"], "generated an empty canvas"


def test_it_persists_so_the_sheet_can_open_it(client):
    rec = _post(client, kind=wizard.KIND_DEEP_SKY, target="M16").json()
    got = client.get(f"/api/flows/{rec['id']}")
    assert got.status_code == 200, "generated but not saved -- the sheet would 404"
    assert got.json()["graph"]["nodes"], "saved an empty graph"


def test_it_lands_in_my_flows_and_stays_editable(client):
    rec = _post(client, kind=wizard.KIND_POOL,
                target="M16, M17, M8").json()
    assert rec["folder"] == MY_FLOWS_FOLDER
    assert rec["readonly"] is False, (
        "a generated flow is the operator's from the moment it appears; a "
        "read-only one cannot be edited and the wizard would be a dead end")


@pytest.mark.parametrize("kind", wizard.KINDS)
def test_every_kind_passes_the_doctor_through_the_route(client, kind):
    """The stated hard requirement, checked on what the ROUTE returns.

    The unit tests check `generate()`. This checks the graph that actually
    reaches the client after serialisation and persistence, which is the one a
    first-time user sees light up -- or not.
    """
    r = _post(client, kind=kind, options=list(wizard.AUTOMATION_OPTIONS),
              target="M16, M17")
    assert r.status_code == 200, r.text[:300]
    from astrodeck.flows.models import FlowGraph
    graph = FlowGraph.model_validate(r.json()["graph"])
    issues = flow_doctor(graph)
    bad = [i for i in issues if getattr(i, "level", "note") != "note"]
    assert not bad, f"{kind} generated a graph the doctor faults: {bad}"


def test_the_answers_actually_reach_the_generator(client):
    """A route that ignored its body would still pass every test above."""
    lone = _post(client, kind=wizard.KIND_DEEP_SKY, options=[], target="M16").json()
    everything = _post(client, kind=wizard.KIND_DEEP_SKY,
                       options=list(wizard.AUTOMATION_OPTIONS),
                       target="M16").json()
    assert len(everything["graph"]["nodes"]) > len(lone["graph"]["nodes"]), (
        "six automation chips produced no more nodes than none of them -- the "
        "body is being dropped")

    named = _post(client, kind=wizard.KIND_DEEP_SKY, target="NGC 7129").json()
    assert "7129" in named["name"], (
        f"the target never reached flow_name(): {named['name']!r}")


def test_an_unknown_kind_is_refused(client):
    r = _post(client, kind="Planetary lucky imaging", target="Jupiter")
    assert r.status_code == 422, (
        "an unrecognised kind must not quietly generate the default deep-sky "
        f"night; got {r.status_code}")


def test_an_unknown_automation_chip_is_refused(client):
    r = _post(client, kind=wizard.KIND_DEEP_SKY,
              options=["Guiding", "Autoguide the dome cat"], target="M16")
    assert r.status_code == 422, (
        f"an unrecognised chip must be refused, not dropped; got {r.status_code}")


# ====================================================== the mosaic kind (S3)

#: An IMX571 at 1000 mm: a bin-1 field of 1.346 x 0.900 deg.
OPTICS = Optics(focal_length_mm=1000.0, pixel_size_um=3.76,
                sensor_width_px=6248, sensor_height_px=4176)

#: A mosaic answer as the sheet will send it: a 3-column, 2-row grid of M31
#: for a fixed camera at PA 30. The overlap is left to the wizard's default.
MOSAIC = {"kind": wizard.KIND_MOSAIC, "options": ["Guiding"],
          "target": "M31", "rows": 2, "cols": 3,
          "angle_mode": wizard.CAMERA_FIXED_AT_PA, "pa_deg": 30}


def _targets(rec: dict) -> list:
    graph = FlowGraph.model_validate(rec["graph"])
    return [n for n in graph.nodes if n.type == "target"]


def _loop_wires(rec: dict) -> list[dict]:
    return [e for e in rec["graph"]["edges"] if e["fromPort"] == PASS_PORT]


def test_a_mosaic_with_no_optics_answers_one_target_and_the_reason(client):
    """Spec 1.8: with no camera field the wizard never emits a grid it cannot
    tile (M1). It answers one target, no panel loop, and says why, in the
    answer's ``notes`` and on the card's tagline; the saved flow is the same
    single target.

    RED under mutant "the notes are dropped" (the wizard route answers
    ``_persist_flow``'s dict without ``notes``), observed:

        KeyError: 'notes'

    (The tagline says it too, which is why the answer's notes need their own
    test: the card line survives that mutant.)
    """
    r = _post(client, **MOSAIC)
    assert r.status_code == 200, r.text
    rec = r.json()
    (target,) = _targets(rec)
    assert not is_multi_panel(target), "no optics: one target, not a grid"
    assert _loop_wires(rec) == []
    assert wizard.NO_OPTICS_REASON in rec["notes"]
    assert wizard.NO_OPTICS_REASON in rec["tagline"]
    (stored,) = _targets(client.get(f"/api/flows/{rec['id']}").json())
    assert not is_multi_panel(stored)


def test_a_mosaic_is_tiled_from_the_live_field(client):
    """With optics set, the grid is laid out from the field the route read
    off the rig (bin 1, ``hub.effective_optics``), at the operator's PA, and
    the panel loop is wired from the tail of the lane. No fallback note.

    RED under mutant "the wizard is not told" (the route passes
    ``rig=None``), observed:

        AssertionError: the grid is tiled from the rig's field
        assert False
         +  where False = is_multi_panel(FlowNode(id='n2', type='target',
         x=258.0, y=60.0, params={'name': 'M31', 'ra': '00h 42m 44.3s', ...

    Red too under "the notes are dropped" (``KeyError: 'notes'``).
    """
    client.store.set_optics(OPTICS)
    live = app_module.hub.effective_optics()
    r = _post(client, **MOSAIC)
    assert r.status_code == 200, r.text
    rec = r.json()
    (target,) = _targets(rec)
    assert is_multi_panel(target), "the grid is tiled from the rig's field"
    p = target.params
    assert (p["rows"], p["cols"]) == (2, 3)
    assert (p["fovX"], p["fovY"]) == (live["fov_w_deg"], live["fov_h_deg"])
    assert (p["angle"], p["rotation"]) == (wizard.CAMERA_FIXED_AT_PA, 30.0)
    assert [e["to"] for e in _loop_wires(rec)] == [target.id]
    assert wizard.NO_OPTICS_REASON not in rec["notes"]


def test_use_measured_takes_the_angle_the_last_solve_measured(client,
                                                              monkeypatch):
    """USE MEASURED reads ``status.sky_angle``'s PA (``hub.last_sky_angle``),
    which the route injects; with no solve recorded it is a refusal, never a
    guessed angle (spec 1.8, the I-04 defect).

    RED under mutant "the measured angle is not injected" (the route passes
    ``measured_pa_deg=None``), observed:

        AssertionError: {"detail":{"detail":"the camera has no measured angle
        yet: no centring solve has recorded one. Type the PA",
        "code":"invalid_wizard_answer"}}
        assert 422 == 200
         +  where 422 = <Response [422 Unprocessable Entity]>.status_code

    Red too under "the wizard is not told" (no optics, so one target comes
    back 200 where the first call must refuse) and "the generator's refusal
    is not mapped" (the refusal escapes as the ValueError).
    """
    client.store.set_optics(OPTICS)
    answer = {**MOSAIC, "use_measured": True}
    del answer["pa_deg"]
    r = _post(client, **answer)
    assert r.status_code == 422, r.text
    assert "no measured angle" in r.json()["detail"]["detail"]

    monkeypatch.setattr(app_module.hub, "last_sky_angle",
                        {"pa_deg": 123.4, "source": "centring"}, raising=False)
    r = _post(client, **answer)
    assert r.status_code == 200, r.text
    (target,) = _targets(r.json())
    assert target.params["rotation"] == 123.4


@pytest.mark.parametrize("bad", [
    {"angle_mode": "Any angle"}, {"rows": GRID_MAX + 1}, {"cols": 0},
    {"rows": 2.5}, {"overlap_pct": OVERLAP_MAX_PCT + 1},
    {"overlap_pct": -1}, {"pa_deg": float("nan")}, {"rows": True},
    {"pa_deg": True}, {"overlap_pct": True}, {"pa_deg": "30"},
    {"overlap_pct": "30"}],
    ids=["any-angle", "rows-over", "cols-zero", "rows-half",
         "overlap-over", "overlap-negative", "pa-nan", "rows-bool",
         "pa-bool", "overlap-bool", "pa-text", "overlap-text"])
def test_the_answers_are_checked_at_the_door(client, bad):
    """Checked against the wizard's constants (``MOSAIC_ANGLES``,
    ``GRID_MIN``, ``to_plan.GRID_MAX``, ``OVERLAP_MIN_PCT``,
    ``OVERLAP_MAX_PCT``) whether or not the rig has optics: with none the
    generator never reads the grid, so without the door a malformed answer
    would come back 200 as a single target.

    RED under mutant "no door" (each of ``FlowWizardBody``'s four mosaic
    validators made to return its value unchecked), observed for every case
    but ``rows-half`` (``rows-over`` shown; the others read the same):

        AssertionError: {"id":"e3f354fc56cd4fc39f6960cf464a1f7f",
        "name":"M31","folder":"My flows","tagline":"Generated by the wizard
        - mosaic, planned as one target: set the camera and focal length in
        Settings > Optics to plan a mosaic", ...
        assert 200 == 422
         +  where 200 = <Response [200 OK]>.status_code

    (The tagline's em dash is written here as a hyphen, in this quote and
    the next file-local one, to keep the source ASCII.)

    ``rows-half`` survives "no door" because the field's ``int`` type
    refuses 2.5 as well; it is red under mutant "no door, float rows" (the
    side validator unchecked and ``rows`` typed ``float``), with the same
    lines, as are ``rows-bool``, ``rows-over`` and ``cols-zero``.

    ``pa-bool``, ``overlap-bool`` and ``pa-text`` were added by the S3-A
    verifier: pydantic's float coercion took ``true`` as 1.0 and "30" as
    30.0 before the door looked. Red on the code as it was left. S4
    (#344, item 20) confirmed them under the two door mutants, each
    ``mode="before"`` switched to after (the validator's body untouched),
    in a private copy of ``server/`` (scratchpad s4-testhyg-mut2), and
    added ``overlap-text``, the overlap's spelling of ``pa-text``, which
    no case held. Observed verbatim (ids elided, the em dash written as a
    hyphen, as above):

    mutant "the PA door coerces" (``@field_validator("pa_deg")``):

        E       AssertionError: {"id":"...","name":"M31","folder":"My
        flows","tagline":"Generated by the wizard - mosaic, planned as one
        target: set the camera and focal length in Settings > Optics to
        plan a mosaic","graph":{"nodes":[{"id":"n1","type":"dusk", ...
        E       assert 200 == 422
        E        +  where 200 = <Response [200 OK]>.status_code
        FAILED tests/test_flows_wizard_route.py::
        test_the_answers_are_checked_at_the_door[pa-bool]
        FAILED tests/test_flows_wizard_route.py::
        test_the_answers_are_checked_at_the_door[pa-text]
        FAILED tests/test_flows_wizard_route.py::test_a_bool_is_not_an_angle
        3 failed, 28 passed, 1 warning in 24.57s

    mutant "the overlap door coerces" (``@field_validator("overlap_pct")``),
    the same lines at:

        FAILED tests/test_flows_wizard_route.py::
        test_the_answers_are_checked_at_the_door[overlap-bool]
        FAILED tests/test_flows_wizard_route.py::
        test_the_answers_are_checked_at_the_door[overlap-text]
        FAILED tests/test_flows_wizard_route.py::test_a_bool_is_not_an_angle
        3 failed, 28 passed, 1 warning in 19.17s

    Each mutant turns only its own two cases red, so the other eight
    (``any-angle`` to ``rows-bool``) stay green under both: each is
    refused by a bound or a type the switch does not move.
    """
    body = {**MOSAIC, **bad}
    if body.get("pa_deg") != body.get("pa_deg"):
        # A NaN is not JSON; send the literal a sloppy client would.
        r = client.post("/api/flows/wizard", content=(
            '{"kind": "Mosaic", "target": "M31", "rows": 2, "cols": 3, '
            '"angle_mode": "Camera fixed at PA", "pa_deg": NaN}'),
            headers={"content-type": "application/json"})
    else:
        r = _post(client, **body)
    assert r.status_code == 422, r.text


def test_a_bool_is_not_an_angle(client):
    """With optics set, so the generator lays the grid out: ``pa_deg:
    true`` is refused at the door, never laid out at PA 1.0. The generator
    refuses a bool itself (``_mosaic_angle``), but pydantic's float
    coercion used to turn it into 1.0 before the generator saw it. The
    same for ``overlap_pct: true``, which became a 1% overlap.

    Found by the S3-A verifier (#344). RED on the code as the
    implementer left it. Confirmed in S4 (item 20) under both door
    mutants, ``mode="before"`` switched to after with the validator's
    body untouched, in a private copy of ``server/`` (scratchpad
    s4-testhyg-mut2), observed verbatim (the id elided, the em dash
    written as a hyphen):

        E           AssertionError: {"id":"...","name":"M31","folder":"My
        flows","tagline":"Generated by the wizard - mosaic","graph":{
        "nodes":[{"id":"n1","type":"dusk", ...
        E           assert 200 == 422
        E            +  where 200 = <Response [200 OK]>.status_code

    Under "the PA door coerces" the 200's TARGET was a 3x2 at
    ``"rotation":1.0``, a PA nobody typed; under "the overlap door
    coerces" a 3x2 at PA 30 with ``"overlap":1``, a 1% overlap nobody
    typed (``"overlap":"0.0100"`` in its frame anchor). A control: the
    number 1 the bool was read as is laid out at PA 1.0, so the refusal is
    of the spelling, not the angle.
    """
    client.store.set_optics(OPTICS)
    for bad in ({"pa_deg": True}, {"overlap_pct": True}):
        r = _post(client, **{**MOSAIC, **bad})
        assert r.status_code == 422, r.text
    r = _post(client, **{**MOSAIC, "pa_deg": 1})
    assert r.status_code == 200, r.text
    (target,) = _targets(r.json())
    assert target.params["rotation"] == 1.0


def test_a_grid_with_another_kind_is_refused(client):
    """The generator's own refusal, answered 422 by the route: a 3x2 sent
    with "Deep-sky target" would otherwise come back as one panel looking
    exactly like the mosaic asked for.

    RED under mutant "the generator's refusal is not mapped" (the route's
    ``except ValueError`` removed), observed:

        ValueError: cols, rows belong to the 'Mosaic' kind; this is
        'Deep-sky target'
    """
    r = _post(client, kind=wizard.KIND_DEEP_SKY, target="M31", rows=2, cols=3)
    assert r.status_code == 422, r.text
    assert "belong to the 'Mosaic' kind" in r.json()["detail"]["detail"]


def test_rotate_to_pa_on_a_rig_with_no_rotator_is_refused(client,
                                                          monkeypatch):
    """The route's rotator fact reaches the generator: a native profile with
    no rotator row cannot turn the camera, so "Rotate to PA" is refused. An
    unknown rotator (no profile) is not a "no", and the same answer is
    generated.

    RED under mutant "no rotator fact" (the route's ``RigFacts`` built with
    ``has_rotator=None`` always), observed:

        AssertionError: {"id":"e263228f50164c3fa3252a10a8bbe0f3",
        "name":"M31","folder":"My flows","tagline":"Generated by the wizard
        - mosaic", ...
        assert 200 == 422
         +  where 200 = <Response [200 OK]>.status_code

    Red too under "the wizard is not told" and "the generator's refusal is
    not mapped" (``ValueError: the active profile has no rotator, so
    nothing can turn the camera to a PA: ...``).
    """
    client.store.set_optics(OPTICS)
    answer = {**MOSAIC, "angle_mode": wizard.ROTATE_TO_PA}
    assert _post(client, **answer).status_code == 200, "unknown is not a no"
    native = Profile(name="Refractor", primary_backend="native", devices=[
        ProfileDevice(role="camera", backend="native")])
    monkeypatch.setattr(app_module.hub, "_active_profile", lambda: native)
    r = _post(client, **answer)
    assert r.status_code == 422, r.text
    assert "no rotator" in r.json()["detail"]["detail"]


@pytest.mark.parametrize("kind, target", [
    (wizard.KIND_DEEP_SKY, "M16"), (wizard.KIND_POOL, "M16, M17"),
    (wizard.KIND_EAA, "M16")])
def test_control_the_other_kinds_generate_what_they_did(client, kind,
                                                        target):
    """The three original kinds, with the rig's optics known, generate
    exactly the graph ``generate_record`` makes from the same three answers
    with no rig facts at all, less the anchor the save writes on each TARGET
    (ruling 3; server-owned, test_flows_reframe_end_to_end.py). A control:
    green on the code and under every mutant named in this file."""
    client.store.set_optics(OPTICS)
    options = list(wizard.AUTOMATION_OPTIONS)
    rec = _post(client, kind=kind, options=options, target=target).json()
    want = wizard.generate_record(kind, options, target).graph
    got = FlowGraph.model_validate(rec["graph"])
    # The generator creates the key blank; the save fills it in.
    for n in (*got.nodes, *want.nodes):
        n.params.pop("frameAnchor", None)
    assert got.model_dump(by_alias=True) == want.model_dump(by_alias=True)
