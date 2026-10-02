# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""The rig facts the flow routes inject (#189 spec 3.3, 1.8; task S3-A).

``compile_plan``, ``to_sequence_plan`` and the doctor are pure: no devices,
no config, no clock. Four mosaic rules nevertheless turn on something only
the running rig knows, so the ROUTE reads it and hands it in as one
``flows.rig.RigFacts``, the way it hands ``to_plan`` its ``cool_to``:

* the imaging camera's field at bin 1, from ``hub.effective_optics`` (M5:
  the block was framed with another camera; to_plan makes a field smaller
  than one step of the grid a LOSS, which ``/run`` refuses until accepted);
* the measured hop, ``engine.measured_cost("hop")``, never the engine's
  seed (M10, the brief);
* whether the active profile has a rotator (M8, the wizard);
* whether both reject guards are off (M9).

``_compile_payload`` builds ONE value and gives the same object to the plan
and to the doctor, so the PLAN tab's loss and the doctor's warning can never
be computed from two readings of the rig. ``run_flow`` builds it the same
way, and every ``to_sequence_plan`` call in ``api/app.py`` passes it (parsed,
not grepped), so the preview, the progress chip and the run agree.

Controls: a flow with no mosaic compiles and runs exactly as it did, whatever
the rig facts say.

No site data: the field is optics, the hop a duration, the rest booleans.
Every test names its mutant and quotes the failure it produced; each mutant
was written over a byte copy of ``api/app.py`` in a private copy of
``server/`` under the session scratchpad (``s3-a-routes-k7m2``), never in the
shared tree.
"""
from __future__ import annotations

import ast
import pathlib
import re

import pytest
from fastapi.testclient import TestClient

import astrodeck.api.app as app_module
from astrodeck.config import Optics, StandardsConfig
from astrodeck.flows.compile import compile_plan
from astrodeck.flows.models import FlowGraph
from astrodeck.flows.rig import RigFacts
from astrodeck.flows.store import FlowStore
from astrodeck.flows.to_plan import to_sequence_plan
from astrodeck.profiles import Profile, ProfileDevice
from test_flows_continue import _capture, rig  # noqa: F401 (fixture)

#: A camera whose field is SMALLER than one step of the 2.0 x 1.33 deg grid
#: at 25% overlap (1.5 x 0.9975 deg): an IMX571 at 1000 mm, 1.346 x 0.900.
SMALL = Optics(focal_length_mm=1000.0, pixel_size_um=3.76,
               sensor_width_px=6248, sensor_height_px=4176)
#: The same sensor at 500 mm, 2.69 x 1.80 deg: every step is covered.
LARGE = Optics(focal_length_mm=500.0, pixel_size_um=3.76,
               sensor_width_px=6248, sensor_height_px=4176)


@pytest.fixture
def client(isolated_config, tmp_path, monkeypatch):
    """conftest's ``isolated_config`` (#341: the store swept into every
    module, the profile library, the capture root, the sky angle, no camera
    or rotator on the hub), a flow library of the test's own, and a store
    handle to set optics and standards on. It was test_flows_routes' three-
    module patch, which left the profile library on the developer's real
    ``profiles/`` and the hub's device map as the last test left it.

    Checked under two stand-in "real" configs (``ASTRODECK_CONFIG_DIR``),
    one with optics, an active profile with its own optics and a rotator,
    and one fresh: every test here answered the same under both
    (S4-TESTHYG)."""
    monkeypatch.setattr(app_module, "flow_store", FlowStore(tmp_path / "flows"))
    # No hop measured unless a test says so: the module's engine outlives
    # every test in the process.
    monkeypatch.setattr(app_module.engine, "_event_costs", {})
    app = app_module.create_app()
    isolated_config.sweep()
    with TestClient(app) as c:
        c.store = isolated_config.store
        yield c


class _Spy:
    """Wraps a real function and records the keywords of every call."""

    def __init__(self, real):
        self.real = real
        self.calls: list[dict] = []

    def __call__(self, *a, **kw):
        self.calls.append(kw)
        return self.real(*a, **kw)


@pytest.fixture
def spies(monkeypatch):
    plan = _Spy(app_module.to_sequence_plan)
    doctor = _Spy(app_module.flow_doctor)
    monkeypatch.setattr(app_module, "to_sequence_plan", plan)
    monkeypatch.setattr(app_module, "flow_doctor", doctor)
    return plan, doctor


def _target(**over) -> dict:
    """A 3x2 of 2.0 x 1.33 deg panels at 25% overlap, laid out at PA 30 for
    a fixed camera: framed, angled, runnable. M31's catalogue position."""
    return {"id": "t", "type": "target", "x": 0, "y": 0,
            "params": {"name": "M31", "ra": "00h 42m 44s",
                       "dec": "+41 16 09", "rows": 2, "cols": 3,
                       "overlap": 25, "fovX": 2.0, "fovY": 1.33,
                       "angle": "Camera fixed at PA", "rotation": 30,
                       **over}}


def _mosaic(**over) -> dict:
    """The 3x2 feeding one CAPTURE LOOP with no goal (no other loss)."""
    return {"nodes": [_target(**over), _capture("c1", "L")],
            "edges": [{"from": "t", "fromPort": "target", "to": "c1",
                       "toPort": "run"}]}


#: A flow with no mosaic: one plain TARGET feeding one capture.
PLAIN = {"nodes": [{"id": "t", "type": "target", "x": 0, "y": 0,
                    "params": {"name": "M42", "ra": "05h 35m 17s",
                               "dec": "-05 23 28"}},
                   _capture("c1", "L")],
         "edges": [{"from": "t", "fromPort": "target", "to": "c1",
                    "toPort": "run"}]}


def _compile(client, graph: dict) -> dict:
    r = client.post("/api/flows/compile", json={"graph": graph, "name": "m"})
    assert r.status_code == 200, r.text
    return r.json()


def _live_field() -> tuple[float, float]:
    o = app_module.hub.effective_optics()
    assert o["have_optics"], "premise: the optics resolve to a field"
    return (o["fov_w_deg"], o["fov_h_deg"])


# ================================================================ one value

class TestOneValueForThePlanAndTheDoctor:
    def test_compile_hands_the_same_facts_to_both(self, client, spies,
                                                  monkeypatch):
        """One ``RigFacts`` per compile, the same object in both calls, and
        each field read off the rig: the bin-1 field ``effective_optics``
        computes, the measured hop as ``(mean, samples)``, no rotator known
        (no profile, none connected) and the default guards on.

        RED under mutant "the doctor is not told" (``rig=rig`` removed from
        ``_compile_payload``'s ``flow_doctor`` call), observed:

            AssertionError: the doctor was handed no rig facts
            assert 'rig' in {'mount': None, 'standards':
            StandardsConfig(apply_filter_offsets=True,
            refocus_on_temp_delta_c=0.0, min_stars=0, max_guide_rms=0.0,
            max_eccentricity=0.65, max_consecutive_rejects=10,
            max_consecutive_rejects_night=20)}

        RED under mutant "two readings" (the doctor handed a second
        ``_rig_facts()``), observed:

            AssertionError: one RigFacts, handed to both
            assert RigFacts(fov_deg=(1.346, 0.9), fov_from="the rig's
            optics, matched 2026-09-26", hop_cost_s=120.0, hop_samples=2,
            has_rotator=None, reject_guards_off=False) is RigFacts(fov_deg=
            (1.346, 0.9), fov_from="the rig's optics, matched 2026-09-26",
            hop_cost_s=120.0, hop_samples=2, has_rotator=None, ...

        RED under mutant "the plan is not told" (below) as ``KeyError:
        'rig'``.
        """
        plan, doctor = spies
        client.store.set_optics(SMALL)
        monkeypatch.setattr(app_module.engine, "_event_costs",
                            {"hop": [100.0, 140.0]})
        _compile(client, _mosaic())
        assert "rig" in doctor.calls[-1], "the doctor was handed no rig facts"
        facts = plan.calls[-1]["rig"]
        assert doctor.calls[-1]["rig"] is facts, "one RigFacts, handed to both"
        assert isinstance(facts, RigFacts)
        assert facts.fov_deg == _live_field()
        assert (facts.hop_cost_s, facts.hop_samples) == (120.0, 2)
        assert facts.has_rotator is None
        assert facts.reject_guards_off is False
        assert re.search(r", matched \d{4}-\d{2}-\d{2}$", facts.fov_from), (
            facts.fov_from)

    def test_run_builds_the_facts_the_same_way(self, client, spies):
        """``run_flow`` compiles with the same facts: its plan call carries
        the live field. The run stops at the M5 loss here (no camera is
        needed to see that), which is the next test's subject.

        RED under mutant "the run is not told" (``rig=_rig_facts()`` removed
        from ``run_flow``'s ``to_sequence_plan`` call), observed:

            KeyError: 'rig'
        """
        plan, _doctor = spies
        client.store.set_optics(SMALL)
        fid = client.post("/api/flows", json={"flow": {
            "name": "m", "graph": _mosaic()}}).json()["id"]
        client.post(f"/api/flows/{fid}/run", json={})
        assert plan.calls[-1]["rig"].fov_deg == _live_field()

    def test_every_plan_and_doctor_call_in_the_file_passes_it(self):
        """Every ``to_sequence_plan`` and every ``flow_doctor`` call node in
        ``api/app.py`` passes ``rig``: the preview, the progress chip and
        the run must compile the same night (``test_flows_cooling.py``'s
        rule for ``cool_to``, parsed rather than grepped for its reason).

        RED under mutant "the chip is not told" (``rig=rig`` removed from
        ``_flow_progress_payload``'s call), observed:

            AssertionError: the to_sequence_plan call on line 5950 does not
            pass rig
            assert 'rig' in {'camera_can_cool', 'closes_on_unsafe',
            'cool_to', 'flow_id'}

        and the same way, at their own lines, under "the plan is not told"
        (5642), "the run is not told" (6174) and "the doctor is not told"
        (``the flow_doctor call on line 5669 does not pass rig``,
        ``assert 'rig' in {'mount', 'standards'}``).
        """
        tree = ast.parse(pathlib.Path(app_module.__file__)
                         .read_text(encoding="utf-8"))
        seen = {"to_sequence_plan": 0, "flow_doctor": 0}
        for n in ast.walk(tree):
            if (isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                    and n.func.id in seen):
                seen[n.func.id] += 1
                kw = {k.arg for k in n.keywords if k.arg}
                assert "rig" in kw, (
                    f"the {n.func.id} call on line {n.lineno} does not pass "
                    f"rig")
        assert seen["to_sequence_plan"] >= 3 and seen["flow_doctor"] >= 1, seen


# ========================================================= each fact's source

class TestEachFactIsReadOffTheRig:
    def test_no_optics_is_an_unknown_field_not_a_zero_one(self, client, spies):
        """A fresh config has no pixel size or sensor and no camera is
        connected, so the field is unknown: ``None``, which every rule reads
        as "skip", never ``(0, 0)``, which ``RigFacts`` refuses.

        RED under mutant "a zero field" (``fov = (0.0, 0.0)`` when the optics
        are not known), observed (raised through the test client, as it is
        for every compile under that mutant):

            ValueError: fov_deg x must be a positive finite number, not 0.0;
            an unknown value is None
        """
        plan, _doctor = spies
        _compile(client, _mosaic())
        assert plan.calls[-1]["rig"].fov_deg is None

    def test_an_unmeasured_hop_is_none_never_the_seed(self, client, spies):
        """The engine keeps an assumed 150 s hop for its clocks; a caller that
        reports a cost must not pass that guess off as a measurement (spec
        1.8, M10: "only when a measured cost is injected").

        RED under mutant "the seed" (``_rig_facts`` falls back to
        ``(engine._event_cost("hop", 150.0), 1)`` when nothing is measured),
        observed:

            assert (150.0, 1) == (None, 0)
              At index 0 diff: 150.0 != None
        """
        plan, _doctor = spies
        _compile(client, _mosaic())
        facts = plan.calls[-1]["rig"]
        assert (facts.hop_cost_s, facts.hop_samples) == (None, 0)

    @pytest.mark.parametrize("per_step, per_night, off", [
        (0, 0, True), (0, 20, False), (10, 0, False), (10, 20, False)])
    def test_the_guards_are_off_only_when_both_are(self, client, spies,
                                                   per_step, per_night, off):
        """``quota_unbounded``'s own reading: either guard bounds the loop.

        RED under mutant "either guard off" (``not (a and b)`` for ``not (a
        or b)``), observed for ``(0, 20)`` and ``(10, 0)``:

            AssertionError: assert True is False
             +  where True = RigFacts(fov_deg=None, fov_from='',
             hop_cost_s=None, hop_samples=0, has_rotator=None,
             reject_guards_off=True).reject_guards_off
        """
        plan, _doctor = spies
        client.store.set_standards(StandardsConfig(
            max_consecutive_rejects=per_step,
            max_consecutive_rejects_night=per_night))
        _compile(client, _mosaic())
        assert plan.calls[-1]["rig"].reject_guards_off is off

    def test_the_rotator(self, client, spies, monkeypatch):
        """True on evidence (a connected rotator, or a rotator row in the
        active profile), False only for a profile that lists its devices on
        the native backend with no rotator among them (an unlisted role has
        no address there), and unknown otherwise: no profile, or a primary
        backend that may fill the role itself. An unknown rotator is not a
        "no" (the wizard and M8 both read None as "do not say").

        RED under mutant "no row means no rotator" (the native-primary test
        dropped, so any profile without a rotator row answers False),
        observed at the NINA profile:

            AssertionError: a NINA rig may have a rotator: unknown
            assert False is None
             +  where False = <function TestEachFactIsReadOffTheRig.
             test_the_rotator.<locals>.has at 0x00000285F355AFC0>()

        RED under mutant "rows only" (the connected-device test removed),
        observed at the native profile with a rotator connected:

            AssertionError: a connected rotator is a rotator
            assert False is True

        RED under mutant "no rotator fact" (``has_rotator=None`` always),
        observed:

            AssertionError: a native rig with no rotator row has none
            assert None is False

        The NINA profile answers on its primary, before the native test's
        other two conditions, so both survived every case above; the review
        added the NINA-hosted profile with a device row and the native
        profile with none. Each mutant ran in a private copy of ``server/``
        (scratchpad s3-tcf-review-mut3), restored and SHA-256 compared.
        RED under mutant "the NINA host ignored" (``and not
        profile.nina_host`` dropped from the native test), observed:

            AssertionError: a NINA host may fill the rotator: unknown
            assert False is None

        RED under mutant "an empty list is written down" (``and
        profile.devices`` dropped from the native test), observed:

            AssertionError: a native profile listing nothing: unknown
            assert False is None
        """
        plan, _doctor = spies

        def has() -> bool | None:
            _compile(client, _mosaic())
            return plan.calls[-1]["rig"].has_rotator

        assert has() is None, "no profile and nothing connected: unknown"

        native = Profile(name="Refractor", primary_backend="native", devices=[
            ProfileDevice(role="camera", backend="native")])
        monkeypatch.setattr(app_module.hub, "_active_profile", lambda: native)
        assert has() is False, "a native rig with no rotator row has none"

        with_rot = Profile(name="Refractor", primary_backend="native",
                           devices=[ProfileDevice(role="camera",
                                                  backend="native"),
                                    ProfileDevice(role="rotator",
                                                  backend="native")])
        monkeypatch.setattr(app_module.hub, "_active_profile",
                            lambda: with_rot)
        assert has() is True

        nina = Profile(name="NINA rig", nina_host="10.0.0.9")
        monkeypatch.setattr(app_module.hub, "_active_profile", lambda: nina)
        assert has() is None, "a NINA rig may have a rotator: unknown"

        # THE TWO ARMS THE NINA PROFILE ABOVE NEVER REACHES. Its primary is
        # NINA (no device rows), so it answers before the native test reads
        # its rows or its host. A legacy profile with a device row AND a NINA
        # host derives the NATIVE primary (`Profile._derived_primary`), and
        # NINA may still fill the rotator role there; a native profile that
        # lists no device has written nothing down. Both are unknown.
        hybrid = Profile(name="NINA rig with a camera row",
                         nina_host="10.0.0.9", devices=[
                             ProfileDevice(role="camera", backend="native")])
        assert (hybrid.primary_backend
                or hybrid._derived_primary()) == "native", (
            "premise: the NINA-hosted profile with a row is native-primary")
        monkeypatch.setattr(app_module.hub, "_active_profile", lambda: hybrid)
        assert has() is None, "a NINA host may fill the rotator: unknown"

        bare = Profile(name="Bare", primary_backend="native", devices=[])
        monkeypatch.setattr(app_module.hub, "_active_profile", lambda: bare)
        assert has() is None, "a native profile listing nothing: unknown"

        class _Rotator:
            connected = True

        monkeypatch.setattr(app_module.hub, "_active_profile", lambda: native)
        monkeypatch.setitem(app_module.hub.devices, "rotator", _Rotator())
        assert has() is True, "a connected rotator is a rotator"


# ======================================================== M5 at the routes

class TestTheFieldAtTheRoutes:
    def test_the_plan_tab_shows_the_loss_and_the_doctor_the_warning(
            self, client):
        """With a camera smaller than one step, the compile answer carries
        to_plan's M5 loss in ``unmapped`` and the doctor's M5 in ``issues``,
        both from the one reading. With no optics neither is said.

        RED under mutant "the plan is not told" (``rig=rig`` removed from
        ``_compile_payload``'s ``to_sequence_plan`` call), observed:

            AssertionError: assert [] == ['targets[M31].mosaic.fov']
              Right contains one more item: 'targets[M31].mosaic.fov'

        RED under mutant "the doctor is not told", observed at the warning:

            AssertionError: [{'level': 'warn', 'text': "\\u25b8 TARGET -
            'arm' input unwired"}, ...]
            assert [] == ['warn']
              Right contains one more item: 'warn'
        """
        quiet = _compile(client, _mosaic())
        assert not [u for u in quiet["unmapped"] if u["key"].endswith(".fov")]
        assert not [i for i in quiet["issues"]
                    if "this camera now images" in i["text"]]

        client.store.set_optics(SMALL)
        out = _compile(client, _mosaic())
        assert [u["key"] for u in out["unmapped"]
                if u["key"].endswith(".fov")] == ["targets[M31].mosaic.fov"]
        warned = [i for i in out["issues"]
                  if "this camera now images 1.35 x 0.90 deg" in i["text"]]
        assert [i["level"] for i in warned] == ["warn"], out["issues"]


class TestRunRefusesTheLossUntilAccepted:
    async def test_a_field_smaller_than_the_step_is_409_until_accepted(
            self, rig):
        """``/run`` refuses a mosaic whose panels would leave gaps on the
        camera now fitted (spec 1.8 M5's loss): 409 ``unmapped`` naming the
        field, nothing started; with ``accept_unmapped`` it starts.

        RED under mutant "the run is not told" (``rig=_rig_facts()`` removed
        from ``run_flow``'s ``to_sequence_plan`` call), observed:

            AssertionError: {"started":true,
            "flow_id":"20da63459a404c2e81bb403c2c952ebd","frames":18,
            "unmapped":[],"session":{"id":"6898deb1457746179d0f0425a096b9b2",
            "night":1,"continued":false,"kept":0,"new":6,"dropped":0}}
            assert 200 == 409
             +  where 200 = <Response [200 OK]>.status_code
        """
        rig.store.set_optics(SMALL)
        fid = await rig.save_flow(_mosaic())
        r = await rig.run(fid)
        assert r.status_code == 409, r.text
        detail = r.json()["detail"]
        assert detail["code"] == "unmapped"
        assert [u["key"] for u in detail["unmapped"]] == [
            "targets[M31].mosaic.fov"]
        assert "leave gaps" in detail["unmapped"][0]["detail"]
        assert rig.starts == [], "a refused run starts nothing"

        r = await rig.run(fid, accept_unmapped=True)
        assert r.status_code == 200, r.text
        assert r.json()["started"] is True
        assert [s.won for s in rig.starts] == [True]

    async def test_control_a_camera_that_covers_the_step_runs(self, rig):
        """A field larger than each step is no loss, so the run starts with
        no acceptance: the refusal above is M5's and nothing else's. Green
        on the code and under every mutant named in this file."""
        rig.store.set_optics(LARGE)
        fid = await rig.save_flow(_mosaic())
        r = await rig.run(fid)
        assert r.status_code == 200, r.text
        assert r.json()["unmapped"] == []


# ================================================================= controls

class TestAFlowWithNoMosaicIsUntouched:
    def test_its_compile_answer_does_not_move_with_the_facts(self, client,
                                                             monkeypatch):
        """Every key of a plain flow's compile answer but ``rig``, with every
        fact unknown and then with a field, a measured hop and a connected
        rotator known, is identical: the field, the hop and the rotator are
        read by mosaic rules only. (The guards are the one fact a rule reads for any flow:
        M9 previews the ``quota_unbounded`` refusal the run already makes,
        and it is test_flows_doctor_s3's.)

        Not a mutant's test: it is the control for the mutants above, and it
        was green under each of them but "a zero field", under which every
        compile of every flow fails (``RigFacts`` refuses the zero field with
        the ValueError quoted there), this one included.

        DELIBERATE PIN CHANGE (mosaic S4, #189, re-pinned by the S4
        integration, #406): since S4 the answer carries ``rig``
        (``readouts.rig_readout``), which reports these very facts to the
        Target modal and so moves with them by design. The control now
        compares every OTHER key, and first requires ``rig`` to have moved,
        which is the evidence the facts reached the route at all: without
        it, a harness whose optics or hop never landed would pass the
        equality below vacuously.

        Mutant "a single block reports the hop" (``readouts._block`` starts
        a block's ``hop_s``/``hop_measured`` from the rig, not None), which
        breaks the "a single block reads the same whatever it knows" its
        comment promises, observed in scratchpad/s4-integrate-q7m2:

            E         Differing items:
            E         {'readouts': {'t': {'angle_tolerance_deg': None, 'autofocus_every': 0, 'focus': 'once', 'hop_measured': True, ...}}} != {'readouts': {'t': {'angle_tolerance_deg': None, 'autofocus_every': 0, 'focus': 'once', 'hop_measured': False, ...}}}

        Mutant "rig facts never reach the route" (``_compile_payload``'s
        ``rig = _rig_facts()`` made ``rig = RigFacts()``), under which the
        equality alone would pass, observed in the same copy:

            E       AssertionError: premise: the rig facts reached the compile route: {'fov_deg': None, 'fov_from': '', 'has_rotator': None, 'hop_s': None, 'hop_samples': 0, 'hop_measured': False}
        """
        before = _compile(client, PLAIN)

        class _Rotator:
            connected = True

        client.store.set_optics(SMALL)
        app_module.engine._event_costs["hop"] = [90.0]
        monkeypatch.setitem(app_module.hub.devices, "rotator", _Rotator())
        after = _compile(client, PLAIN)
        assert after["rig"] != before["rig"], (
            f"premise: the rig facts reached the compile route: {after['rig']}")

        def _but_rig(answer: dict) -> dict:
            return {k: v for k, v in answer.items() if k != "rig"}

        assert _but_rig(after) == _but_rig(before)

    async def test_it_runs_the_plan_it_ran_before(self, rig, monkeypatch):
        """The plan ``run_flow`` hands the engine for a plain flow, with a
        field known and a hop measured, equals the plan compiled with no rig
        facts at all (``rig=None``, the pre-S3 call), id for id.
        A control, green under every mutant named in this file."""
        rig.store.set_optics(SMALL)
        monkeypatch.setattr(rig.engine, "_event_costs", {"hop": [90.0]})
        started: list = []
        real = app_module.to_sequence_plan

        def keep(*a, **kw):
            plan, unmapped = real(*a, **kw)
            started.append(plan)
            return plan, unmapped

        monkeypatch.setattr(app_module, "to_sequence_plan", keep)
        fid = await rig.save_flow(PLAIN)
        r = await rig.run(fid)
        assert r.status_code == 200, r.text
        g = FlowGraph.model_validate(app_module.flow_store.get(fid).graph
                                     .model_dump(by_alias=True))
        bare, _ = to_sequence_plan(compile_plan(g, "continue me"), g,
                                   flow_id=fid, rig=None)
        assert started[-1].model_dump() == bare.model_dump()
