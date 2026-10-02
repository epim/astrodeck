# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""#657(a): the unsafe dome close must not depend on the flow graph, proven at
FLOW level.

The orchestrator's 2026-10-01 adjudication of #657 found the close already
graph-independent in fact -- it rides one config flag
(``safety.close_dome_on_unsafe``) and never reads the compiled flow's
``automation.dome`` block -- but noted that the only existing proofs
(``test_engine_dome_close.py``) build a bare ``SequencePlan`` by hand. A bare
plan was NEVER GOING TO SEE THE BUG this test guards: it carries no
automation data either way, so it cannot tell "the engine ignores the graph"
apart from "the engine happens to ignore this particular hand-built plan".

This file closes that gap by compiling an actual flow -- TARGET -> CAPTURE,
with NO DOME CONTROL node anywhere in it -- through the real
``compile_plan`` / ``to_sequence_plan`` pipeline the editor and ``/run`` use,
and running the resulting ``SequencePlan`` on the engine exactly as
``test_engine_dome_close.py::test_unsafe_pause_preset_escalates_to_close``
does. Same rig, same safety config, same assertions -- the only thing that
differs is where the plan came from.

NAMED MUTANT (engine.py, ``_wind_down`` call in the ``except SafetyAbort``
handler of the run loop): the unsafe close made conditional on the flow
carrying an ``automation`` block with a ``dome`` key, e.g.

    close_dome=bool(self._cfg and self._cfg.safety.close_dome_on_unsafe
                    and getattr(plan, "automation", {}).get("dome"))

``SequencePlan`` has no ``automation`` field (``to_plan.py``'s docstring:
unknown keys are dropped in silence), so this is always falsy regardless of
what the source flow drew -- which is exactly the shape of the bug #657(a)
asked to be ruled out: a dome absent from the graph still getting rained on,
except inverted (here even a graph that DID carry a DOME CONTROL node would
lose its close, because the engine never carried the data forward to check).
Run under
``pytest server/tests/test_w9_dome_close_flow_independence.py -q`` with the
mutant applied; the test must fail. See
``test_unsafe_close_survives_a_flow_with_no_dome_control_node``'s docstring
for the observed failure once the mutant was actually run.

Fixtures mirror ``test_engine_dome_close.py``'s ``temp_store``/``sim_hub``
harness verbatim rather than importing it: WP-82's edit scope is this new
file plus ``devices/base.py`` only, so the small duplication stays local
instead of reaching into a file another work package owns this wave.
"""
from __future__ import annotations

import asyncio

import pytest

import astrodeck.config as config_mod
import astrodeck.hub as hub_module
import astrodeck.sequence.engine as engine_mod
from astrodeck.config import ConfigStore, SafetyConfig, Site
from astrodeck.devices.base import DomeShutterState, SafetyReading
from astrodeck.flows.compile import compile_plan
from astrodeck.flows.models import FlowEdge, FlowGraph, FlowNode
from astrodeck.flows.to_plan import to_sequence_plan
from astrodeck.hub import Hub
from astrodeck.sequence import SequenceEngine


# --------------------------------------------------------------------- fixtures
#
# Same shape as test_engine_dome_close.py's temp_store/sim_hub: an isolated
# ConfigStore swept into every module that holds a module-level reference to
# it, and a fresh Hub on the simulator rig (which auto-connects a sim dome).

@pytest.fixture
def temp_store(tmp_path, monkeypatch):
    store = ConfigStore(path=tmp_path / "astrodeck.json")
    # A made-up site -- never the real rig's (project rule: site coordinates
    # never appear in code, tests or output).
    store.set_site(Site(name="Test", latitude=40.0, longitude=-74.0,
                        is_default=False))
    monkeypatch.setattr(config_mod, "config_store", store)
    monkeypatch.setattr(hub_module, "config_store", store)
    monkeypatch.setattr(engine_mod, "config_store", store)
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path / "captures")
    monkeypatch.setattr(engine_mod, "SAFETY_PAUSE_POLL_S", 0.05)
    monkeypatch.setattr(engine_mod, "SAFETY_SEED_WAIT_S", 0.5)
    return store


@pytest.fixture
async def sim_hub(temp_store, monkeypatch):
    monkeypatch.setattr(Hub, "_check_solar",
                        lambda self, ra, dec, *, force=False: None)
    h = Hub()
    await h.connect_sim()
    yield h
    await h.disconnect_all()


def set_safety(store: ConfigStore, **kw) -> None:
    store.set_safety(SafetyConfig(**kw))


def force_cached_unsafe(hub: Hub, reason: str = "cloud sensor") -> None:
    mon = hub.devices.get("safety")
    if mon is not None:
        mon.force_unsafe(reason)
    hub._safety_reading = SafetyReading(is_safe=False, reason=reason,
                                        source="Sim Safety Monitor")


async def wait_for(predicate, timeout=40.0):
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.05)
    return False


# ------------------------------------------------------------------- the flow

def _n(nid, ntype, x=0.0, y=0.0, **params):
    return FlowNode(id=nid, type=ntype, x=x, y=y, params=params)


def _e(a, ap, b, bp):
    return FlowEdge(**{"from": a, "fromPort": ap, "to": b, "toPort": bp})


def _flow_with_no_dome_control_node() -> FlowGraph:
    """TARGET -> CAPTURE, and nothing else -- no DOME CONTROL node anywhere
    in the graph, which ``compile_plan`` would otherwise surface as
    ``compiled["automation"]["dome"]`` (``compile.py`` line ~1366). Mirrors
    test_flows_night_ends_parked.py's ``_simple_graph`` shape, with a short
    exposure and a single frame so the capture lane reaches its first safety
    check almost immediately instead of waiting out a real sub."""
    return FlowGraph(
        nodes=[_n("t", "target", name="w9 dome test",
                  ra="05h 35m 17s", dec="-05 23 28"),
               _n("c", "capture", x=100, exposure=0.05, count=1,
                  filter="L", gain=100)],
        edges=[_e("t", "target", "c", "run")])


def _compile_to_plan():
    """The flow, compiled through the real editor/``/run`` pipeline, as a
    ``SequencePlan`` the engine can start -- plus the raw ``compiled`` dict,
    so the test can assert on the premise (no ``automation.dome``) straight
    off what the compiler actually produced rather than off the graph it was
    handed.

    Centring and the autofocus-first sweep are turned off on the resulting
    target: both need a plate solver this harness never wires (the point
    under test is the dome close, not the solve), and
    ``test_engine_dome_close.py``'s own bare-plan helper makes the identical
    simplification for the identical reason."""
    graph = _flow_with_no_dome_control_node()
    compiled = compile_plan(graph, "w9 dome independence")
    plan, _unmapped = to_sequence_plan(compiled, graph)
    plan = plan.model_copy(update={
        "targets": [t.model_copy(update={"center": False,
                                         "autofocus_first": False})
                   for t in plan.targets],
    })
    return compiled, plan


# --------------------------------------------------------------------- tests

def test_the_premise_the_flow_compiles_with_no_dome_control_node():
    """THE PREMISE. If this stops being true the rest of the file is proving
    graph independence against a graph that was never missing the node."""
    compiled, plan = _compile_to_plan()
    assert "dome" not in (compiled.get("automation") or {}), (
        "the flow now carries a DOME CONTROL node, so it no longer "
        "disproves the old claim that the engine needs one")
    assert len(plan.targets) == 1 and plan.targets[0].steps, (
        "premise: the compile must actually produce a runnable capture step")


async def test_unsafe_close_survives_a_flow_with_no_dome_control_node(
        sim_hub, temp_store):
    """THE FIX, AT FLOW LEVEL. on_unsafe=pause + close_dome_on_unsafe=True,
    on a plan COMPILED FROM A FLOW WITH NO DOME CONTROL NODE: the run must
    still escalate to the shielded park-and-close teardown exactly as it
    does for a bare SequencePlan in test_engine_dome_close.py -- the close
    rides the config flag alone and has nothing to read off the graph.

    'dome CLOSED' proves the park ran BEFORE the close (SimDome's collision
    model would otherwise have raised on close_shutter before a confirmed
    park).

    Observed failure under the named mutant described in the module
    docstring (closing engine.py's unsafe-teardown ``_wind_down`` call so
    ``close_dome`` also requires ``getattr(plan, "automation", {}).get
    ("dome")``, which a compiled ``SequencePlan`` never carries):

        AssertionError: assert <DomeShutterState.OPEN: 'open'> is <DomeShutterState.CLOSED: 'closed'>
    """
    _compiled, plan = _compile_to_plan()
    dome = sim_hub.devices.get("dome")
    assert dome is not None and dome.connected, "sim rig must connect a dome"
    set_safety(temp_store, enabled=True, on_unsafe="pause",
              unsafe_consecutive=1, resume_safe_consecutive=1,
              max_pause_min=0, min_alt_deg=0.0, close_dome_on_unsafe=True)
    force_cached_unsafe(sim_hub, "cloud sensor")

    engine = SequenceEngine(sim_hub)
    engine.start(plan)
    assert await wait_for(lambda: not engine.running), engine.state
    # It aborted (did NOT sit in 'paused' with the roof open) -- the same
    # escalation a bare plan gets, proving the graph played no part in it.
    assert engine.state.get("state") == "aborted"
    assert engine.state.get("end_reason") == "unsafe"
    assert await sim_hub.require("telescope").is_parked()
    assert await dome.shutter_state() is DomeShutterState.CLOSED
