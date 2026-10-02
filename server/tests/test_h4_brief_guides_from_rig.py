# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""The Tonight brief guides the way the rig will (#506).

2026-09-28, 0.3.36 on the rig: both Tonight briefs read that night said the run
"guides with PHD2 (settle below 1.5″, dither every 3 frames)", and the night
log shows the native guider calibrating and guiding. The brief printed the
GUIDE card's ``provider``, ``settle`` and ``dither`` params, the card's
defaults (``nodes.py``: PHD2, 1.5, 3), although the compile's own note for the
stage (``to_plan.NODE_SETTINGS["guide"]``, #239 stage C) says all three are
ignored and "come from Rig > Guider instead". Two surfaces of one flow
contradicted each other.

NOW: ``RigFacts`` carries Rig > Guider's half (``guide_provider``,
``guide_settle``, ``guide_dither_px``, ``guide_dither_every``), read by the
route in ``_rig_facts`` (``_guider_and_settle``), and the brief's guide
clause is built from those alone (``tonight._guide_clause``).

WHAT "THE CONFIG'S SETTLE" IS. The rig has no stored settle: Rig > Guider's
three settle fields shape a DITHER NOW press and are never saved, and the
engine dithers with a distance and no settle, so every dither of a night waits
on the resolved guider's own rule. That rule is what the brief prints: the
native engine's 1.5 px held 10 s (``app.NATIVE_GUIDE_SETTLE``, held below to
the Rust source it copies) and the PHD2 bridge's 1.5 px held 8 s
(``guide.phd2.SETTLE``, imported, not copied). NINA publishes none, and the
brief says it settles by its own rule. The dither distance is the stored one,
``guide.dither_pixels``, and the cadence is the one a flow's plan carries,
which ``to_sequence_plan`` never sets.

THE HARNESS for the route cases is ``test_flows_progress_route.py``'s ``api``
(the real app over ASGI, a throwaway config store swept into every module),
with a synthetic site (that file's ``SITE_A``), and the guide resolver's real
inputs set on the app's hub: a guide camera and a mount connected, no guider
wired yet, and the provider pinned in the config, as Rig > Guider pins it.

MUTATIONS. Each named mutant was written over a byte backup in a private copy
of ``server/`` (the session scratchpad's ``H4-ROUTES-A-mut``), only this file
was run there, and the copy was restored and SHA-256 compared after each.
The failures are quoted as observed.
"""
from __future__ import annotations

import math
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

import astrodeck.api.app as app_module
import astrodeck.providers as providers_mod
from astrodeck.config import GuideConfig, ProvidersConfig, Site
from astrodeck.flows.compile import compile_plan
from astrodeck.flows.models import FlowGraph
from astrodeck.flows.rig import RigFacts
from astrodeck.flows.to_plan import to_sequence_plan
from astrodeck.flows.tonight import brief
from astrodeck.guide.phd2 import SETTLE as PHD2_SETTLE
from test_flows_progress_route import (SITE_A, _restore_provider,  # noqa: F401
                                       api)

#: The Rust engine the native settle is copied from.
ENGINE_RS = (Path(__file__).resolve().parents[2] / "native" / "crates"
             / "astro-guide" / "src" / "engine.rs")

#: A GUIDE card that names PHD2 and settle and dither values no rig here uses,
#: so a number the brief prints can be traced to the card or to the rig.
CARD = {"provider": "PHD2", "settle": 2.5, "dither": 7}

#: Rig > Guider's stored dither distance for these tests.
DITHER_PX = 4.5


def _graph(*, guide: bool = True, card: dict = CARD) -> dict:
    """TARGET M31 -> GUIDE (the card) -> CAPTURE L, or the same lane with no
    GUIDE stage."""
    nodes = [{"id": "t", "type": "target", "x": 0, "y": 0,
              "params": {"name": "M31", "ra": "00h 42m 44s",
                         "dec": "+41 16 09"}},
             {"id": "c", "type": "capture", "x": 200, "y": 0,
              "params": {"filter": "L", "exposure": 120, "gain": 100,
                         "bin": "1", "count": 12, "goal": 0}}]
    if not guide:
        return {"nodes": nodes,
                "edges": [{"from": "t", "fromPort": "target", "to": "c",
                           "toPort": "run"}]}
    nodes.insert(1, {"id": "g", "type": "guide", "x": 100, "y": 0,
                     "params": dict(card)})
    return {"nodes": nodes,
            "edges": [{"from": "t", "fromPort": "target", "to": "g",
                       "toPort": "run"},
                      {"from": "g", "fromPort": "guiding", "to": "c",
                       "toPort": "run"}]}


def _plan_dither_every(graph: dict) -> int:
    """The cadence the run of this flow will dither at: the compiled plan's."""
    g = FlowGraph.model_validate(graph)
    plan, _ = to_sequence_plan(compile_plan(g, "g"), g, flow_id="f")
    return plan.dither_every


@pytest.fixture
def native_rig(api, monkeypatch):
    """A rig configured for the native guider: the guide provider pinned to
    AstroDeck's own in the config (Rig > Guider's provider row), a guide
    camera and a mount connected, the native engine installed, no guider
    wired yet, no NINA; and a dither distance of ``DITHER_PX``."""
    api.store.set_providers(ProvidersConfig(guide="astrodeck"))
    api.store.set_guide(GuideConfig(dither_pixels=DITHER_PX))
    hub = app_module.hub
    monkeypatch.setattr(providers_mod, "NATIVE_AVAILABLE", True)
    monkeypatch.setattr(hub, "guider", None)
    monkeypatch.setattr(hub, "nina_client", None)
    for role in ("guide_camera", "telescope"):
        monkeypatch.setitem(hub.devices, role,
                            SimpleNamespace(connected=True, hardware=True))
    return api


async def _brief(api, graph: dict) -> str:
    fid = await api.save_flow(graph)
    api.store.set_site(Site(name="fixture", latitude=SITE_A[0],
                            longitude=SITE_A[1], elevation_m=10.0,
                            is_default=False))
    r = await api.client.get(f"/api/flows/{fid}/tonight")
    assert r.status_code == 200, r.text
    got = r.json()
    assert got["ok"] is True, f"premise: the site is set: {got['reason']}"
    return got["brief"]


# ================================================================ the route

class TestTheBriefNamesTheRigsGuider:
    async def test_a_phd2_card_on_a_native_rig_reads_as_the_native_guider(
            self, native_rig):
        """The card says PHD2, settle 2.5 and dither 7. The brief names the
        native guider, the native engine's settle, the stored dither distance
        and the plan's cadence, and none of the card's three.

        RED under mutant "brief reads the card params" (``_guide_clause``
        given the card's ``provider``, ``settle`` and ``dither`` again, the
        sentence as it was), observed:

            AssertionError: the brief named PHD2: This flow arms M31. For
            each target it guides with PHD2 (settle below 2.5″, dither every
            7 frames). It captures L 120 s × 12 (gain 100, bin 1).
        """
        every = _plan_dither_every(_graph())
        told = await _brief(native_rig, _graph())
        assert "PHD2" not in told, f"the brief named PHD2: {told}"
        assert (f"For each target it guides with AstroDeck native (settle "
                f"below 1.5 px for 10 s, dither {DITHER_PX:g} px every "
                f"{every} frames).") in told, told
        assert "2.5" not in told and "every 7 " not in told, told
        assert every != CARD["dither"], (
            "premise: the card's dither is not the plan's cadence")

    @pytest.mark.parametrize("pin, nina, clause", [
        ("backend", False,
         "guides with PHD2 (settle below 1.5 px for 8 s, dither 4.5 px "
         "every 3 frames)"),
        ("auto", True,
         "guides with NINA (NINA's own settle, dither 4.5 px every 3 "
         "frames)"),
    ], ids=["the PHD2 bridge", "NINA"])
    async def test_each_guider_is_named_with_its_own_settle(
            self, native_rig, monkeypatch, pin, nina, clause):
        """The provider and its settle follow the resolver: pinned to the
        bridge, the PHD2 bridge's own settle (``guide.phd2.SETTLE``, which it
        sends with every dither); on a NINA rig, NINA, which settles by a
        rule it does not publish, and the brief says so rather than print a
        number. The card (PHD2, 2.5, 7) is read by neither.

        RED under mutant "every guider settles as the native engine"
        (``_guider_and_settle`` answering ``NATIVE_GUIDE_SETTLE`` whatever
        the provider), observed for the bridge:

            AssertionError: guides with PHD2 (settle below 1.5 px for 8 s,
            dither 4.5 px every 3 frames) not in: This flow arms M31. For
            each target it guides with PHD2 (settle below 1.5 px for 10 s,
            dither 4.5 px every 3 frames). It captures L 120 s × 12 (gain
            100, bin 1).
        """
        native_rig.store.set_providers(ProvidersConfig(guide=pin))
        if nina:
            monkeypatch.setattr(app_module.hub, "nina_client", object())
        told = await _brief(native_rig, _graph())
        assert clause in told, f"{clause} not in: {told}"
        assert (PHD2_SETTLE["pixels"], PHD2_SETTLE["time"]) == (1.5, 8), (
            "premise: the bridge's own settle")

    async def test_the_cadence_is_the_one_the_run_dithers_at(
            self, native_rig):
        """The brief's "every N frames" is the compiled plan's
        ``dither_every``, whatever the card says: a card of 7 and a card of
        1 read alike, and both as the plan's.

        RED under mutant "the cadence is a guess" (``_rig_facts`` handing
        ``guide_dither_every=5``), observed:

            AssertionError: assert 'every 3 frames' in 'This flow arms M31.
            For each target it guides with AstroDeck native (settle below
            1.5 px for 10 s, dither 4.5 px every 5 frames). It captures L
            120 s × 12 (gain 100, bin 1).'
        """
        for card in (CARD, {**CARD, "dither": 1}):
            every = _plan_dither_every(_graph(card=card))
            told = await _brief(native_rig, _graph(card=card))
            assert f"every {every} frames" in told


@pytest.fixture
def unconnected_rig(api, monkeypatch):
    """``native_rig``'s configuration with the rig not connected, as it is
    while tonight is planned before the rig is switched on: the provider
    pinned to AstroDeck's own, the engine installed, and no guide camera, no
    mount, no guider and no NINA."""
    api.store.set_providers(ProvidersConfig(guide="astrodeck"))
    api.store.set_guide(GuideConfig(dither_pixels=DITHER_PX))
    hub = app_module.hub
    monkeypatch.setattr(providers_mod, "NATIVE_AVAILABLE", True)
    monkeypatch.setattr(hub, "guider", None)
    monkeypatch.setattr(hub, "nina_client", None)
    for role in ("guide_camera", "telescope"):
        monkeypatch.setitem(hub.devices, role, None)
    return api


class TestAGuiderNotYetKnownIsNotNamed:
    """Added by the H4-ROUTES-A verifier. With the rig not connected the
    guide resolver can answer only its last resort, the PHD2 bridge ("no
    guide camera connected — using the PHD2 bridge"), and the brief printed
    exactly #506's sentence on a rig pinned to the native guider, which the
    run guides with once the rig connects (``hub.select_guide_provider``).
    Observed on the tree as first submitted:

        This flow arms M31. For each target it guides with PHD2 (settle
        below 1.5 px for 8 s, dither 4.5 px every 3 frames). It captures L
        120 s × 12 (gain 100, bin 1).

    The mutants below were run by the verifier in a private copy of
    ``server/`` (the session scratchpad's ``H4-ROUTES-A-verify-mut``), each
    written over a byte backup and restored with its SHA-256 compared."""

    async def test_a_rig_pinned_native_but_not_connected_names_no_phd2(
            self, unconnected_rig):
        """Pinned to the native guider, not connected: the brief names no
        guider and no settle, and says where they come from.

        RED under mutant "the fallback is named" (``_guider_and_settle``'s
        not-decided-yet return removed), observed:

            AssertionError: the brief named PHD2 on a rig pinned to the
            native guider: This flow arms M31. For each target it guides
            with PHD2 (settle below 1.5 px for 8 s, dither 4.5 px every 3
            frames). It captures L 120 s × 12 (gain 100, bin 1).
        """
        choice = providers_mod.resolve("guide", app_module.hub)
        assert (choice.kind, choice.label) == ("backend", "PHD2"), (
            f"premise: the resolver's last resort, {choice}")
        told = await _brief(unconnected_rig, _graph())
        assert "PHD2" not in told and "8 s" not in told, (
            f"the brief named PHD2 on a rig pinned to the native guider: "
            f"{told}")
        assert ("For each target it guides (guider, settle and dither from "
                "Rig > Guider).") in told, told

    async def test_control_a_rig_pinned_to_the_bridge_still_names_it(
            self, unconnected_rig):
        """The CONTROL: the same unconnected rig pinned to the bridge. That
        pin is the operator's choice and the bridge is always selectable, so
        the brief names PHD2 and its own settle as before.

        RED under mutant "every unwired bridge is unknown" (the ``pinned !=
        "backend"`` condition dropped), observed here and, the same way, in
        ``test_each_guider_is_named_with_its_own_settle[the PHD2 bridge]``:

            AssertionError: guides with PHD2 (settle below 1.5 px for 8 s,
            dither 4.5 px every 3 frames) not in: This flow arms M31. For
            each target it guides (guider, settle and dither from Rig >
            Guider). It captures L 120 s × 12 (gain 100, bin 1).
        """
        unconnected_rig.store.set_providers(ProvidersConfig(guide="backend"))
        told = await _brief(unconnected_rig, _graph())
        clause = ("guides with PHD2 (settle below 1.5 px for 8 s, dither "
                  "4.5 px every 3 frames)")
        assert clause in told, f"{clause} not in: {told}"

    async def test_a_simulated_rig_settles_as_the_native_engine(
            self, native_rig, monkeypatch):
        """A simulated guide camera and mount: the resolver says "Simulator"
        and the guider is the native engine over the simulated devices, so
        its dithers wait on the native settle.

        RED under mutant "sim guider gets no settle" (``_guider_and_settle``
        giving ``NATIVE_GUIDE_SETTLE`` to ``astrodeck`` alone), observed:

            AssertionError: guides with Simulator (settle below 1.5 px for
            10 s, dither 4.5 px every 3 frames) not in: This flow arms M31.
            For each target it guides with Simulator (Simulator's own
            settle, dither 4.5 px every 3 frames). It captures L 120 s × 12
            (gain 100, bin 1).
        """
        native_rig.store.set_providers(ProvidersConfig(guide="auto"))
        for role in ("guide_camera", "telescope"):
            monkeypatch.setitem(app_module.hub.devices, role,
                                SimpleNamespace(connected=True,
                                                hardware=False))
        told = await _brief(native_rig, _graph())
        clause = ("guides with Simulator (settle below 1.5 px for 10 s, "
                  "dither 4.5 px every 3 frames)")
        assert clause in told, f"{clause} not in: {told}"


# ================================================================ the clause

#: A native rig as the route reads it.
NATIVE = RigFacts(guide_provider="AstroDeck native", guide_settle=(1.5, 10.0),
                  guide_dither_px=4.5, guide_dither_every=3)


class TestTheClause:
    def test_control_a_flow_with_no_guide_has_no_guide_sentence(self):
        """The CONTROL: rig facts that name a guider do not make a flow
        guide. With no GUIDE stage the brief has no guide sentence; with one,
        it has.

        RED under mutant "a guide sentence whenever the rig names a guider"
        (the clause appended when ``guide is not None or rig is not None``),
        observed:

            AssertionError: a flow with no GUIDE reads: This flow arms M31.
            For each target it guides with AstroDeck native (settle below
            1.5 px for 10 s, dither 4.5 px every 3 frames). It captures L
            120 s × 12 (gain 100, bin 1).
        """
        without = brief(FlowGraph.model_validate(_graph(guide=False)),
                        rig=NATIVE)
        assert "guides" not in without, f"a flow with no GUIDE reads: {without}"
        with_guide = brief(FlowGraph.model_validate(_graph()), rig=NATIVE)
        assert "it guides with AstroDeck native" in with_guide, with_guide

    def test_with_no_rig_facts_it_names_where_they_come_from(self):
        """A preview or a test hands no rig facts: the stage still says the
        night guides, names Rig > Guider as the source, and prints none of
        the card's values.

        RED under mutant "brief reads the card params", observed:

            AssertionError: This flow arms M31. For each target it guides
            with PHD2 (settle below 2.5″, dither every 7 frames). It
            captures L 120 s × 12 (gain 100, bin 1).
        """
        told = brief(FlowGraph.model_validate(_graph()))
        assert ("For each target it guides (guider, settle and dither from "
                "Rig > Guider).") in told, told
        for card_value in ("PHD2", "2.5", "7 frames"):
            assert card_value not in told, told

    def test_one_frame_and_never(self):
        """Wording at the edges of the cadence: "every frame", and a rig
        that never dithers says so."""
        g = FlowGraph.model_validate(_graph())
        one = RigFacts(guide_provider="PHD2", guide_settle=(1.5, 8.0),
                       guide_dither_px=3.0, guide_dither_every=1)
        assert "dither 3 px every frame)" in brief(g, rig=one)
        never = RigFacts(guide_provider="PHD2", guide_settle=(1.5, 8.0),
                         guide_dither_px=3.0, guide_dither_every=0)
        assert "(settle below 1.5 px for 8 s, no dither)" in brief(g, rig=never)


# ============================================================ the sources

def _rust_const(name: str) -> float:
    text = ENGINE_RS.read_text(encoding="utf-8")
    m = re.search(rf"const {name}: f64 = ([0-9.]+);", text)
    assert m, f"premise: {name} is in {ENGINE_RS.name}"
    return float(m.group(1))


def test_the_native_settle_is_the_engines():
    """``NATIVE_GUIDE_SETTLE`` is the Rust engine's own settle window, the
    one every native dither waits on, which the wheel does not export.

    RED under mutant "a settle of our own" (``NATIVE_GUIDE_SETTLE = (1.5,
    8.0)``), observed:

        assert (1.5, 8.0) == (1.5, 10.0)
    """
    assert app_module.NATIVE_GUIDE_SETTLE == (
        _rust_const("DEFAULT_SETTLE_TOL_PX"),
        _rust_const("DEFAULT_SETTLE_TIME_S"))


class TestTheFacts:
    def test_unknown_is_none_and_a_pair_is_normalised(self):
        facts = RigFacts(guide_provider="PHD2", guide_settle=[1, 8],
                         guide_dither_px=0, guide_dither_every=0)
        assert facts.guide_settle == (1.0, 8.0)
        assert facts.guide_dither_px == 0.0
        blank = RigFacts()
        assert (blank.guide_provider, blank.guide_settle,
                blank.guide_dither_px, blank.guide_dither_every) == (
            None, None, None, None)

    @pytest.mark.parametrize("field, value", [
        ("guide_provider", ""), ("guide_provider", "  "),
        ("guide_provider", 3),
        ("guide_settle", (0, 10)), ("guide_settle", (1.5, math.nan)),
        ("guide_settle", (1.5,)), ("guide_settle", "1.5 10"),
        ("guide_dither_px", -1), ("guide_dither_px", math.inf),
        ("guide_dither_px", True), ("guide_dither_px", "far"),
        ("guide_dither_every", -1), ("guide_dither_every", True),
        ("guide_dither_every", 2.5),
    ])
    def test_a_reading_that_is_no_reading_is_refused(self, field, value):
        """A fact nobody could have read is refused, as the other fields
        refuse one (the module docstring): "guides with  (...)" from an
        empty name, a settle of 0 px, a negative or infinite dither.

        RED under mutant "no dither check" (``guide_dither_px``'s check
        removed), observed for ``-1``, ``inf``, ``True`` and ``"far"``:

            Failed: DID NOT RAISE <class 'ValueError'>
        """
        with pytest.raises(ValueError):
            RigFacts(**{field: value})
