"""A RUN WITH NO TEMPERATURE MUST NOT BE A SILENT ONE.

MEASURED ON THE RIG. A flow-driven run put 19 light frames on disk at +23 °C
against a dark library built at -10 °C, and nothing anywhere said a word. The
chain, all five links of it:

  1. the flow vocabulary has no cooling node, so a run's ``cool_to`` can only
     come from ``config.cooling.setpoint_c``;
  2. THE DOCUMENTED WAY TO SET THAT VALUE DID NOT WORK — ``POST /api/config``
     with ``{"cooling": {"setpoint_c": -15}}`` answered 200 and discarded it,
     because ``set_cooling`` carried the stored setpoint over UNCONDITIONALLY
     to stop the settings panel erasing it by omission;
  3. with the field unset, ``to_sequence_plan`` left ``plan.cool_to`` None;
  4. ``engine._run`` skipped the whole cooling block on ``is not None`` and
     logged nothing about it — the last line before the check says the run
     started, and then the night is silent;
  5. ``_enforce_cooling``, the per-frame guard written for exactly this
     failure, is a documented no-op without cooling intent.

Each link alone is survivable. Together they are 80 minutes of unusable data
that only ended because a human read a number off a status screen.

THREE LAYERS, and each one is insufficient on its own. (A) the setpoint has to
be settable, or there is nothing to intend. (B) a run that has no temperature
has to say so, because a setpoint can always be missing again — that is the one
that would have saved the night. (C) the compile has to say so BEFORE the run,
because a warning at 22:00 in a log nobody is reading is worth less than a line
in the editor.

WHAT MUST NOT REGRESS. Setting null must still clear the setpoint (a rig with
no cooler is a real rig), a POST that never mentions cooling must not erase it,
and neither must one that sends the warm-ramp policy block without the
setpoint — that omission-erases-it bug is why the exemption existed, and
reopening it would cost the same nights back.
"""
from __future__ import annotations

import asyncio
import time

import pytest
from fastapi.testclient import TestClient

import astrodeck.api.app as app_module
import astrodeck.config as config_mod
import astrodeck.devices.sim as sim_devices
import astrodeck.hub as hub_module
from astrodeck.config import ConfigStore
from astrodeck.flows.compile import compile_plan
from astrodeck.flows.models import FlowEdge, FlowGraph, FlowNode
from astrodeck.flows.store import FlowStore
from astrodeck.flows.to_plan import blocking_reasons, losses, to_sequence_plan
from astrodeck.hub import Hub
from astrodeck.sequence import ExposureStep, SequenceEngine, SequencePlan, Target
from astrodeck.sequence.models import replan_cooling

#: The two phrases the operator has to see. Asserted rather than the whole
#: sentence so a rewording stays free, and so a line that merely mentions
#: cooling cannot pass for this one.
SAYS_NO_TEMPERATURE = "no target temperature"
SAYS_THE_CONSEQUENCE = "dark library"


# ------------------------------------------------------------------ fixtures

@pytest.fixture
def client(tmp_path, monkeypatch):
    """An app on a throwaway config store and a throwaway flow library."""
    store = ConfigStore(path=tmp_path / "astrodeck.json")
    monkeypatch.setattr(config_mod, "config_store", store)
    monkeypatch.setattr(hub_module, "config_store", store)
    monkeypatch.setattr(app_module, "config_store", store)
    monkeypatch.setattr(config_mod, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(app_module, "flow_store", FlowStore(tmp_path / "flows"))
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path / "captures")
    with TestClient(app_module.create_app()) as c:
        c.config_store = store          # the test's handle on the same store
        yield c


@pytest.fixture
async def sim_hub(tmp_path, monkeypatch):
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path)
    monkeypatch.setattr(hub_module.config_store.cfg().safety, "solar_avoidance", False)
    monkeypatch.setenv("ASTRODECK_SIM_LEGACY_GUIDER", "1")
    h = Hub()
    await h.connect_sim()
    yield h
    await h.disconnect_all()


def _graph() -> FlowGraph:
    return FlowGraph(
        nodes=[FlowNode(id="t", type="target", x=0, y=0,
                        params={"name": "M31", "ra": "00h 42m 44s",
                                "dec": "+41 16 09"}),
               FlowNode(id="c", type="capture", x=100, y=0,
                        params={"exposure": 180, "count": 30, "filter": "Ha"})],
        edges=[FlowEdge(**{"from": "t", "fromPort": "target",
                           "to": "c", "toPort": "run"})])


def _plan(cool_to=None, count=1) -> SequencePlan:
    return SequencePlan(
        name="tonight", guide=False, dither_every=0, autofocus_every=0,
        cool_to=cool_to,
        targets=[Target(name="M42", ra_hours=5.5881, dec_deg=-5.3911,
                        center=False, autofocus_first=False,
                        steps=[ExposureStep(filter="L", exposure_s=0.05,
                                            gain=100, count=count)])])


async def _reached_the_check(bus_lines, timeout=20.0):
    """Block until the engine has logged its run-start line.

    THE PREMISE, NOT A SLEEP. The temperature warning is emitted in the same
    synchronous stretch as ``sequence '...' started``, with no await between
    them, so once the start line is in the list the decision has been made
    either way — an assertion after this point cannot be racing the engine, and
    a test that never sees the start line fails saying so rather than silently
    asserting about a run that never began.
    """
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if any("started:" in m for _l, m, _s in bus_lines):
            return
        await asyncio.sleep(0.02)
    raise AssertionError("the run never logged a start line")


def _temperature_warnings(bus_lines) -> list[tuple[str, str, str]]:
    return [ln for ln in bus_lines if SAYS_NO_TEMPERATURE in ln[1]]


# ---------------------------------------------------------------- (A) settable

class TestTheSetpointIsSettableOverTheApi:
    """``POST /api/config`` answered 200 and threw the value away."""

    def test_the_setpoint_round_trips(self, client):
        r = client.post("/api/config", json={"cooling": {"setpoint_c": -15.0}})
        assert r.status_code == 200, r.text
        assert client.config_store.cfg().cooling.setpoint_c == -15.0, (
            "the route accepted the setpoint and discarded it — a caller cannot "
            "tell 'saved' from 'ignored', which is how a night got shot warm")
        # and it is still there on a fresh READ, not just in the echo
        assert client.get("/api/config").json()["cooling"]["setpoint_c"] == -15.0

    def test_an_explicit_null_clears_it_and_MEANS_no_cooling(self, client):
        """A rig with no cooler is a real rig. Clearing has to stay possible,
        and the cleared value has to reach the plan as 'no intent' rather than
        as some invented default."""
        client.post("/api/config", json={"cooling": {"setpoint_c": -15.0}})
        assert client.config_store.cfg().cooling.setpoint_c == -15.0, (
            "premise: there has to be something to clear, or this test passes "
            "on a store that never accepted the value in the first place")
        r = client.post("/api/config", json={"cooling": {"setpoint_c": None}})
        assert r.status_code == 200, r.text
        assert client.config_store.cfg().cooling.setpoint_c is None
        plan, _ = to_sequence_plan(
            compile_plan(_graph(), "n"),
            cool_to=client.config_store.cfg().cooling.setpoint_c)
        assert plan.cool_to is None, "a cleared setpoint invented a temperature"

    def test_a_post_that_never_mentions_cooling_does_not_erase_it(self, client):
        """The partial merge's whole contract. This is one half of the
        regression the old blanket exemption existed to prevent."""
        client.post("/api/config", json={"cooling": {"setpoint_c": -15.0}})
        r = client.post("/api/config", json={"standards": {"min_stars": 12}})
        assert r.status_code == 200, r.text
        assert client.config_store.cfg().cooling.setpoint_c == -15.0

    def test_the_warm_policy_block_does_not_erase_it_by_omission(self, client):
        """THE OTHER HALF, and the one that actually bit. SafetyLimitsPanel
        POSTs the whole CoolingConfig block; its client type has no setpoint
        field. A wholesale replace would cancel the standing cooling request
        every time somebody nudged the warm rate at 21:00. Absent must still
        mean unchanged — only an explicitly-sent null clears."""
        client.post("/api/config", json={"cooling": {"setpoint_c": -15.0}})
        r = client.post("/api/config", json={"cooling": {"warm_ramp": False,
                                                         "warm_rate_c_per_min": 3.0}})
        assert r.status_code == 200, r.text
        cfg = client.config_store.cfg()
        assert cfg.cooling.setpoint_c == -15.0, (
            "editing the warm-down policy erased the standing cooling request")
        assert cfg.cooling.warm_rate_c_per_min == 3.0 and cfg.cooling.warm_ramp is False

    def test_an_out_of_range_setpoint_is_refused(self, client):
        """-273 is not a colder rig, it is a typo. It must 422 rather than
        persist a number the camera can never reach and then time out on."""
        r = client.post("/api/config", json={"cooling": {"setpoint_c": -273.0}})
        assert r.status_code == 422, r.text
        assert client.config_store.cfg().cooling.setpoint_c is None


# ------------------------------------------------------- (B) the run says so

class TestARunWithNoTemperatureSaysSo:
    """The line that would have saved the night."""

    async def test_a_cooling_capable_camera_with_no_setpoint_warns(
            self, sim_hub, bus_lines):
        eng = SequenceEngine(sim_hub)
        eng.start(_plan(cool_to=None))
        await _reached_the_check(bus_lines)
        warnings = _temperature_warnings(bus_lines)
        await eng.abort()
        assert warnings, (
            "the run had no temperature, the camera could have cooled, and the "
            "log said nothing — this is tonight's defect")
        level, message, _src = warnings[0]
        assert level == "warning", (
            f"the one line that would have caught this went out at {level!r}; "
            f"a debug/info line in a 200-entry ring is not a record")
        assert SAYS_THE_CONSEQUENCE in message, (
            f"the warning names no consequence, so a reader cannot tell it "
            f"matters: {message!r}")

    async def test_a_camera_that_cannot_cool_says_NOTHING(
            self, sim_hub, bus_lines, monkeypatch):
        """Not a defect — a fact about the hardware. An uncooled rig that gets
        nagged every single night learns to ignore the log, which costs us the
        test above."""
        monkeypatch.setattr(sim_hub.devices["camera"], "can_cool", False,
                            raising=False)
        eng = SequenceEngine(sim_hub)
        eng.start(_plan(cool_to=None))
        await _reached_the_check(bus_lines)
        warnings = _temperature_warnings(bus_lines)
        await eng.abort()
        assert not warnings, f"nagged a camera with no cooler: {warnings}"

    async def test_a_run_that_HAS_a_temperature_does_not_warn(
            self, sim_hub, bus_lines):
        eng = SequenceEngine(sim_hub)
        eng.start(_plan(cool_to=-10.0))
        await _reached_the_check(bus_lines)
        warnings = _temperature_warnings(bus_lines)
        await eng.abort()
        assert not warnings, f"warned about a run that asked for -10 °C: {warnings}"


# --------------------------------------------------- (C) the compile says so

class TestTheCompileSaysSoFirst:
    """A warning at 22:00 is worth less than a line in the editor at 19:00."""

    def _unmapped(self, **kw) -> list[dict]:
        _plan_, unmapped = to_sequence_plan(compile_plan(_graph(), "n"),
                                            _graph(), **kw)
        return unmapped

    def _entries(self, unmapped) -> list[dict]:
        return [u for u in unmapped if u["key"] == "cooling.setpoint_c"]

    def test_a_coolable_rig_with_no_setpoint_gets_a_note(self):
        entries = self._entries(self._unmapped(cool_to=None,
                                               camera_can_cool=True))
        assert len(entries) == 1, "the compile said nothing about a warm night"
        assert SAYS_THE_CONSEQUENCE in entries[0]["detail"], entries[0]

    def test_it_is_note_level_and_does_not_block_the_run(self):
        """A deliberately uncooled run is legitimate. Reporting this as a LOSS
        would make ``/run`` refuse with 'parts of this flow do not survive the
        compile' — about a flow that never drew a cooling node, because the
        vocabulary has none."""
        unmapped = self._unmapped(cool_to=None, camera_can_cool=True)
        assert self._entries(unmapped)[0]["level"] == "note"
        assert not self._entries(losses(unmapped)), (
            "the cooling note counts as a loss, so the run route now refuses a "
            "flow that draws nothing wrong")
        assert not self._entries(blocking_reasons(unmapped, dome_connected=True)), (
            "the cooling note blocks a run")

    def test_the_note_NAMES_A_CONTROL_THAT_EXISTS(self):
        """It used to say "Set it in Settings", and there is no cooling
        setpoint field anywhere in Settings.

        FlowInspector renders ``u.detail`` verbatim under BEFORE YOU RUN, so
        this string is operator-facing UI copy, not a log line. The twin of
        ``TestTheWarningIsSaidOnceAndInTime::
        test_the_remedy_NAMES_A_CONTROL_THAT_EXISTS`` — asserted in both places
        because the engine and the compile carry SEPARATE copies of the same
        advice, and fixing one and not the other is how they came to disagree
        in the first place.
        """
        detail = self._entries(self._unmapped(cool_to=None,
                                              camera_can_cool=True))[0]["detail"]
        assert "Capture" in detail and "Target" in detail, (
            f"the note does not name the control that exists: {detail!r}")
        assert "Settings" not in detail, (
            f"the note points at a Settings control that does not exist: "
            f"{detail!r}")

    def test_a_rig_that_cannot_cool_gets_no_note(self):
        assert not self._entries(self._unmapped(cool_to=None,
                                                camera_can_cool=False))

    def test_a_setpoint_that_IS_set_gets_no_note(self):
        assert not self._entries(self._unmapped(cool_to=-10.0,
                                                camera_can_cool=True))

    def test_zero_is_a_setpoint_and_not_an_absence(self):
        """0 °C is a real target. A truthiness check here would report a run
        that IS cooled as having no temperature."""
        assert not self._entries(self._unmapped(cool_to=0.0,
                                                camera_can_cool=True))

    def test_EVERY_route_call_site_asks_the_camera(self):
        """A parameter nothing passes is a dead control, and the default is
        False — so a call site that forgets it reports nothing forever, on the
        preview or on the run. Parsed, not grepped: a docstring that mentions
        the call is not a call (the same trap #197 caught)."""
        import ast
        import pathlib
        tree = ast.parse(pathlib.Path(app_module.__file__).read_text(
            encoding="utf-8"))
        calls = [n for n in ast.walk(tree)
                 if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                 and n.func.id == "to_sequence_plan"]
        assert len(calls) >= 2, f"expected preview + run; found {len(calls)}"
        for call in calls:
            kw = {k.arg for k in call.keywords if k.arg}
            assert "camera_can_cool" in kw, (
                f"the to_sequence_plan call on line {call.lineno} never asks "
                f"whether this rig can cool, so it can never report a run with "
                f"no temperature")


# ------------------------------------------------------------- tonight, whole

class TestTonightEndToEnd:
    """Setpoint unset -> flow compiled -> run started -> the warning is there.

    The three layers were all reachable one at a time before any of them
    worked, which is exactly how the defect survived: each piece looked fine in
    isolation. This walks the path the rig walked.
    """

    def test_the_whole_chain(self, client, bus_lines, monkeypatch):
        # The sun cone is the one guard `force` does not lift, and M42's
        # separation from the Sun is a fact about the DATE. A test whose
        # subject is cooling must not go red in November.
        monkeypatch.setattr(client.config_store.cfg().safety,
                            "solar_avoidance", False)
        assert client.post("/api/connect/sim").status_code == 200
        cam = app_module.hub.devices.get("camera")
        assert cam is not None and cam.can_cool, "premise: this rig can cool"

        # (1) nobody has set a setpoint. That is tonight's starting state.
        assert client.config_store.cfg().cooling.setpoint_c is None

        fid = client.post("/api/flows", json={"flow": {
            "name": "warm night", "folder": "My flows",
            "graph": {"nodes": [
                {"id": "t", "type": "target", "x": 0, "y": 0,
                 "params": {"name": "M42", "ra": "05h 35m 17s",
                            "dec": "-05 23 28"}},
                {"id": "c", "type": "capture", "x": 100, "y": 0,
                 "params": {"filter": "L", "exposure": 0.05, "gain": 100,
                            "count": 2, "goal": 0}}],
                "edges": [{"from": "t", "fromPort": "target",
                           "to": "c", "toPort": "run"}]}}}).json()["id"]

        # (2) the compile says so, before anything moves.
        unmapped = client.post(f"/api/flows/{fid}/compile").json()["unmapped"]
        note = [u for u in unmapped if u["key"] == "cooling.setpoint_c"]
        assert note and note[0]["level"] == "note", unmapped

        # (3) and it does not stop the run — an uncooled night is legitimate.
        r = client.post(f"/api/flows/{fid}/run", json={"force": True})
        assert r.status_code == 200, r.text

        # (4) the log carries the warning the rig never got.
        try:
            end = time.monotonic() + 20.0
            while time.monotonic() < end and not _temperature_warnings(bus_lines):
                time.sleep(0.02)
            warnings = _temperature_warnings(bus_lines)
            assert warnings, (
                "the run started with no temperature on a camera that can "
                "cool, and the night log says nothing — 19 frames at +23 °C is "
                "what that silence costs")
            assert warnings[0][0] == "warning"
        finally:
            client.post("/api/sequence/abort")


# ============================================================================
# ROUND 2. Everything below was found by driving the shipped fix rather than
# reading it. Each layer above turned out to be reachable but not durable, or
# audible but not actionable, or right but unasserted.
# ============================================================================

# --------------------------------------------- (A2) settable AND survivable

class TestTheSetpointSurvives:
    """SETTABLE IS NOT THE SAME AS KEPT.

    Layer (A) made ``POST /api/config`` write the setpoint. It did not make the
    value last: ``Hub._teardown`` ran ``cancel_warm(finalize=True)``, which
    cleared the standing request unconditionally — and every connect path runs
    ``_teardown`` FIRST, as do ``/api/disconnect`` and the lifespan shutdown.
    So the operator could follow the warning's advice at 19:00 and be back in
    the warm-run state before the first frame, with nothing said.

    The knock-on was worse: ``Hub.restore_cooling`` — the whole #153/#204 fix,
    written to put the camera back on the operator's number after a reconnect —
    reads the value ``_teardown`` had just erased, so it returned False every
    time. It had apparently never once fired.
    """

    def test_the_setpoint_REACHES_DISK(self, client):
        """Memory is not persistence, and persistence is the whole point.

        Every other assertion in this file reads the live store or
        GET /api/config, and both answer out of RAM — a ``set_cooling`` that
        stopped calling ``bump_and_save`` passes all of them (measured: the
        full suite stayed green under exactly that mutation). The setpoint is
        persisted on purpose (``CoolingConfig.setpoint_c``): the hub re-applies
        it on the next camera connect, which is what a restart at 03:00 depends
        on.
        """
        client.post("/api/config", json={"cooling": {"setpoint_c": -15.0}})
        assert client.config_store.reload().cooling.setpoint_c == -15.0, (
            "the setpoint never reached disk — it is gone at the next restart")

    def test_it_survives_the_FIRST_connect(self, client):
        """``_teardown`` runs at the top of every connect path, including the
        first one of a process, so this erased a setpoint set while the rig was
        still unplugged — the normal way to set one."""
        client.post("/api/config", json={"cooling": {"setpoint_c": -15.0}})
        assert client.post("/api/connect/sim").status_code == 200
        assert client.get("/api/config").json()["cooling"]["setpoint_c"] == -15.0, (
            "connecting the rig erased the standing cooling request")

    def test_it_survives_a_RECONNECT(self, client):
        client.post("/api/connect/sim")
        client.post("/api/config", json={"cooling": {"setpoint_c": -15.0}})
        assert client.post("/api/connect/sim").status_code == 200
        assert client.config_store.reload().cooling.setpoint_c == -15.0, (
            "reconnecting erased the standing cooling request")

    def test_it_survives_a_DISCONNECT(self, client):
        """Unplugging the rig is not the operator changing their mind about
        what temperature to image at. It used to be read as exactly that."""
        client.post("/api/connect/sim")
        client.post("/api/config", json={"cooling": {"setpoint_c": -15.0}})
        assert client.post("/api/disconnect").status_code == 200
        assert client.config_store.reload().cooling.setpoint_c == -15.0, (
            "disconnecting erased the standing cooling request")

    def test_the_camera_COMES_BACK_cooled(self, client):
        """What ``restore_cooling`` was written for, asserted for the first time.

        Not a duplicate of the three above: they prove the number survives,
        this proves something still ACTS on it. A setpoint that persists into a
        config nobody re-applies is the #153 night over again.
        """
        client.post("/api/connect/sim")
        client.post("/api/config", json={"cooling": {"setpoint_c": -15.0}})
        assert client.post("/api/connect/sim").status_code == 200
        cooler = client.get("/api/status").json()["camera"]["cooler"]
        assert cooler["target_c"] == -15.0 and cooler["on"], (
            f"the camera did not come back on the operator's number: {cooler}")

    def test_a_graceful_shutdown_keeps_it(self, tmp_path, monkeypatch):
        """Driven through the hub's own writer, so it does not depend on the
        route: POST /api/camera/cooler records the setpoint, then the lifespan
        shutdown used to wipe it on the way out. A nightly restart is not a
        decision about tomorrow's dark library."""
        store = ConfigStore(path=tmp_path / "astrodeck.json")
        monkeypatch.setattr(config_mod, "config_store", store)
        monkeypatch.setattr(hub_module, "config_store", store)
        monkeypatch.setattr(app_module, "config_store", store)
        monkeypatch.setattr(config_mod, "CONFIG_DIR", tmp_path)
        monkeypatch.setattr(app_module, "flow_store", FlowStore(tmp_path / "flows"))
        monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path / "captures")
        with TestClient(app_module.create_app()) as c:
            c.post("/api/connect/sim")
            r = c.post("/api/camera/cooler", json={"on": True, "target_c": -10.0})
            assert r.status_code == 200, r.text
            assert store.reload().cooling.setpoint_c == -10.0, "premise"
        # ...the context manager exited: lifespan shutdown -> hub.disconnect_all.
        assert store.reload().cooling.setpoint_c == -10.0, (
            "a graceful shutdown erased the standing cooling request")


# ------------------------------------- (B2) required cooling is ENFORCEABLE

class TestRequireCoolingCoversANoSetpointRun:
    """The operator's strongest possible statement, previously inert.

    ``escalation.require_cooling`` with ``cooling_action="abort"`` means "this
    rig must be cooled; tear the night down if it is not". The entire
    escalation block lived inside ``if plan.cool_to is not None``, so a run with
    NO setpoint — the exact failure this file is about — sailed straight past
    it, shot the full set of warm lights and reported success.

    A warning is the right level ONLY when cooling is optional.
    """

    async def _run(self, hub, monkeypatch, action, require=True,
                   cool_to=None, count=2):
        """Run to a terminal state and return a SNAPSHOT of it.

        Snapshotted before the cleanup abort, because ``eng.abort()`` on an
        already-finished run rewrites ``state`` to "aborted" — an assertion
        made after it would read the teardown, not the night. monkeypatch, not
        plain assignment: ``config_store.cfg()`` is one shared object for the
        whole session, so an escalation policy set here would otherwise follow
        every later test in this worker into unrelated files.
        """
        cfg = config_mod.config_store.cfg()
        monkeypatch.setattr(cfg.escalation, "require_cooling", require)
        monkeypatch.setattr(cfg.escalation, "cooling_action", action)
        eng = SequenceEngine(hub)
        eng.start(_plan(cool_to=cool_to, count=count))
        end = time.monotonic() + 40.0
        while time.monotonic() < end and eng.state.get("state") not in (
                "complete", "error", "aborted"):
            await asyncio.sleep(0.05)
        st = dict(eng.state)
        if st.get("state") not in ("complete", "error", "aborted"):
            await eng.abort()
        return st

    @pytest.mark.parametrize("action", ["abort", "skip"])
    async def test_it_refuses_to_shoot(self, sim_hub, monkeypatch, action):
        st = await self._run(sim_hub, monkeypatch, action)
        assert (st.get("progress") or {}).get("frames_done", 0) == 0, (
            f"require_cooling + {action} still put warm frames on disk: {st}")

    async def test_abort_is_an_ABORT(self, sim_hub, monkeypatch):
        """Not merely "no frames" — the two actions mean different things and a
        test that only counted frames would let them collapse into one."""
        st = await self._run(sim_hub, monkeypatch, "abort")
        assert st.get("state") == "aborted" and st.get("end_reason") == "unsafe", st

    async def test_skip_ends_the_run_as_a_cooling_skip(self, sim_hub,
                                                       monkeypatch):
        st = await self._run(sim_hub, monkeypatch, "skip")
        assert st.get("state") == "complete", st
        assert st.get("end_reason") == "cooling_skip", (
            f"a skipped night must be distinguishable from a finished one: {st}")

    async def test_warn_still_shoots(self, sim_hub, monkeypatch):
        """The historical fail-open, pinned. "Required" plus "warn" is a real
        combination and it means "tell me, then carry on"."""
        st = await self._run(sim_hub, monkeypatch, "warn")
        assert st.get("state") == "complete"
        assert (st.get("progress") or {}).get("frames_done", 0) == 2, st

    async def test_the_DEFAULT_rig_still_shoots(self, sim_hub, monkeypatch):
        """require_cooling is False out of the box. An uncooled night is
        legitimate and must stay legitimate — the warning is the whole of the
        response unless the operator asked for more."""
        st = await self._run(sim_hub, monkeypatch, "warn", require=False)
        assert st.get("state") == "complete"
        assert (st.get("progress") or {}).get("frames_done", 0) == 2, st

    async def test_a_camera_that_cannot_cool_is_not_blocked(self, sim_hub,
                                                            monkeypatch):
        """require_cooling cannot sensibly demand a TEC that is not there, and
        bricking an uncooled rig on a setting it can never satisfy would be a
        worse bug than the one being fixed."""
        monkeypatch.setattr(sim_hub.devices["camera"], "can_cool", False,
                            raising=False)
        st = await self._run(sim_hub, monkeypatch, "abort")
        assert st.get("state") == "complete"
        assert (st.get("progress") or {}).get("frames_done", 0) == 2, st


# ----------------------------------------------- (B3) the warning's SHAPE

class TestTheWarningIsSaidOnceAndInTime:
    """Level was asserted twice. Count and position were asserted nowhere.

    A mutation that emitted the warning per-frame AS WELL AS once at start ran
    the whole suite green. On a 170-frame night that is 170 copies of one amber
    sentence into a ``deque(maxlen=200)`` — the repeated line evicts the night
    it was supposed to annotate, and an operator who is nagged stops reading.
    """

    async def test_it_is_said_ONCE_not_once_a_frame(self, sim_hub, bus_lines):
        eng = SequenceEngine(sim_hub)
        eng.start(_plan(cool_to=None, count=3))
        end = time.monotonic() + 40.0
        while time.monotonic() < end and eng.state.get("state") not in (
                "complete", "error", "aborted"):
            await asyncio.sleep(0.05)
        await eng.abort()
        said = _temperature_warnings(bus_lines)
        assert len(said) == 1, f"the warning was logged {len(said)} times"

    async def test_it_is_said_BEFORE_the_first_frame(self, sim_hub, bus_lines):
        """A warning after the first exposure has already lost the frame it
        exists to save; after the last one it is a post-mortem. Asserted as an
        INDEX against the run-start line, because "it shows up eventually" —
        what the end-to-end test polls for — passes at frame 19."""
        eng = SequenceEngine(sim_hub)
        eng.start(_plan(cool_to=None, count=2))
        await _reached_the_check(bus_lines)
        seen = list(bus_lines)
        await eng.abort()
        start = next(i for i, ln in enumerate(seen) if "started:" in ln[1])
        warn = next((i for i, ln in enumerate(seen)
                     if SAYS_NO_TEMPERATURE in ln[1]), None)
        assert warn is not None and warn == start + 1, (
            f"the warning is not the line after the run-start line "
            f"(start={start}, warning={warn})")

    async def test_the_remedy_NAMES_A_CONTROL_THAT_EXISTS(self, sim_hub,
                                                          bus_lines):
        """It used to say "Settings", and there is no cooling setpoint field
        anywhere in Settings — the one instruction that made the line
        actionable sent the operator to a dead end. The control that does exist
        is Target °C on the Capture tab.

        Asserted rather than left to review because the remedy is the only part
        of the sentence the operator can act on, and a stale pointer is worse
        than none: it costs them the search before they give up.
        """
        eng = SequenceEngine(sim_hub)
        eng.start(_plan(cool_to=None))
        await _reached_the_check(bus_lines)
        said = _temperature_warnings(bus_lines)
        await eng.abort()
        assert said, "premise: the warning fired"
        msg = said[0][1]
        assert "Capture" in msg and "Target" in msg, (
            f"the remedy does not name the control that exists: {msg!r}")
        assert "Settings" not in msg, (
            f"the remedy points at a Settings control that does not exist: "
            f"{msg!r}")


# ------------------------------ (C2) the advisory when nothing is plugged in

class TestTheAdvisorySurvivesADisconnectedRig:
    """"A line on the canvas at 19:00" was the advisory's whole justification,
    and at 19:00 the rig is not connected.

    ``_camera_can_cool`` read ``hub.devices["camera"].can_cool`` live, and
    ``_teardown`` clears ``hub.devices``, so the note appeared only if you
    happened to compile while plugged in. Measured before the fix: silent
    disconnected, present after POST /api/connect/sim, silent again after
    POST /api/disconnect.
    """

    def _note(self, client) -> list[dict]:
        r = client.post("/api/flows/compile",
                        json={"graph": _graph().model_dump(by_alias=True),
                              "name": "probe"})
        assert r.status_code == 200, r.text
        return [u for u in r.json()["unmapped"]
                if u.get("key") == "cooling.setpoint_c"]

    def test_a_rig_that_never_had_a_camera_stays_silent(self, client):
        """The silence that must be preserved. False also means "no camera has
        ever connected here", so an uncooled rig is not nagged on every edit."""
        assert not self._note(client)

    def test_the_note_survives_unplugging(self, client):
        assert client.post("/api/connect/sim").status_code == 200
        assert self._note(client), "premise: connected + coolable + no setpoint"
        assert client.post("/api/disconnect").status_code == 200
        assert self._note(client), (
            "the note vanished when the rig unplugged — which is the state it "
            "is in at the hour the advisory exists for")

    def test_the_capability_is_REMEMBERED_on_disk(self, client):
        """Across a restart, not merely across a disconnect: the editor is
        routinely opened on a box that has just booted."""
        client.post("/api/connect/sim")
        client.post("/api/disconnect")
        assert client.config_store.reload().camera_can_cool_seen is True

    def test_swapping_to_an_uncooled_camera_corrects_the_record(
            self, client, monkeypatch):
        """LAST-SEEN, not sticky-true. A record that could only ever go True
        would nag forever after one connect of a cooled camera.

        The swap is done by wrapping ``SimCamera.__init__``, not by setting
        ``can_cool`` on the live instance or on the class: every connect builds
        a FRESH camera object, so the instance is the thing about to be thrown
        away, and ``__init__`` assigns ``self.can_cool = True`` over any class
        attribute. Both of those made an earlier version of this test pass
        while proving nothing.
        """
        client.post("/api/connect/sim")
        assert client.config_store.cfg().camera_can_cool_seen is True

        original_init = sim_devices.SimCamera.__init__

        def _camera_with_no_tec(self, *a, **kw):
            original_init(self, *a, **kw)
            self.can_cool = False

        monkeypatch.setattr(sim_devices.SimCamera, "__init__",
                            _camera_with_no_tec)
        client.post("/api/connect/sim")
        assert app_module.hub.devices["camera"].can_cool is False, (
            "premise: the reconnected camera reports no cooler")
        client.post("/api/disconnect")
        assert client.config_store.cfg().camera_can_cool_seen is False
        assert not self._note(client), "nagged a rig whose camera has no cooler"


# ------------------------------------- (D) the advice works on night two

class TestAResumedSessionCanBeGivenATemperature:
    """The warning fires on all five engine entries. On the three resume
    entries its REMEDY was inert: a dormant session replays the plan it stored,
    and ``cool_to`` is only ever produced by ``to_sequence_plan`` at first
    compile. Setting the setpoint the next morning changed nothing, so a
    multi-night session warned every night with no reachable way to act on it
    short of abandoning the session.
    """

    def test_a_stored_plan_with_no_temperature_picks_up_the_setpoint(self):
        assert replan_cooling(_plan(cool_to=None), -12.5).cool_to == -12.5

    def test_a_stored_plan_that_HAS_one_is_left_alone(self):
        """The narrow rule that keeps this safe. Re-resolving unconditionally
        would let night two run at -15 °C when nights one and three ran at
        -10 °C, and a session whose subs span two sensor temperatures cannot be
        calibrated against one dark library — a subtler bug than the one being
        fixed."""
        assert replan_cooling(_plan(cool_to=-10.0), -15.0).cool_to == -10.0

    def test_no_setpoint_leaves_the_plan_untouched(self):
        assert replan_cooling(_plan(cool_to=None), None).cool_to is None

    def test_zero_is_a_setpoint_here_too(self):
        """``is not None``, not truthiness — 0 °C is a real target."""
        assert replan_cooling(_plan(cool_to=None), 0.0).cool_to == 0.0

    def test_EVERY_resume_entry_re_resolves(self):
        """An AST check, for the same reason
        ``test_EVERY_route_call_site_asks_the_camera`` is one: the three
        entries into a dormant session must not disagree about its
        temperature, and a fourth added later must not quietly skip this.
        """
        import ast
        import pathlib
        bad = []
        for rel in ("astrodeck/api/app.py", "astrodeck/sequence/resume_arm.py"):
            path = pathlib.Path(__file__).resolve().parents[1] / rel
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not (isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Attribute)
                        and node.func.attr == "start"):
                    continue
                # only the resume shape: start(<plan>, session=<dormant session>)
                if not any(k.arg == "session" for k in node.keywords):
                    continue
                first = node.args[0] if node.args else None
                if not (isinstance(first, ast.Call)
                        and getattr(first.func, "id", None) == "replan_cooling"):
                    bad.append(f"{rel}:{node.lineno}")
        assert not bad, (
            f"a resume entry starts a stored plan without re-resolving the "
            f"standing setpoint: {bad}")
