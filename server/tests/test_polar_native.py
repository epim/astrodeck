"""Native three-point polar alignment (Rust TPPA engine), end-to-end vs the sim.

The sim mount can be given a KNOWN polar-axis misalignment (``sim.py``
``SimRig.set_polar_misalignment``): from then on it reports plate-solvable RA/Dec
that trace the small circle a tilted RA axis sweeps, so the native provider's
capture → solve → rotate-in-RA ×3 → ``tppa_from_three`` pipeline recovers the
injected (az, alt) error with no hardware. The whole module is skipped when the
Rust wheel is absent, so the suite stays green without ``maturin develop``.
"""
from __future__ import annotations

import asyncio

import pytest

import astrodeck.hub as hub_module
from astrodeck import providers
from astrodeck.config import ConfigStore, SafetyConfig, Site
from astrodeck.events import bus
from astrodeck.hub import Hub

pytestmark = pytest.mark.skipif(
    not providers.NATIVE_AVAILABLE, reason="astrodeck_native wheel not installed")

# A northern site well away from the default equator so the axis-fit hemisphere
# logic is exercised and the traced circle sits at usable altitudes.
_LAT = 45.0
_LON = -122.0


@pytest.fixture
async def sim_hub(tmp_path, monkeypatch):
    """A connected sim rig with an isolated, northern-site config and solar
    avoidance disabled (so the RA-rotation slews are date-independent in CI)."""
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path)
    store = ConfigStore(path=tmp_path / "astrodeck.json")
    monkeypatch.setattr(hub_module, "config_store", store)
    import astrodeck.config as config_mod
    monkeypatch.setattr(config_mod, "config_store", store)
    # providers.resolve() reads its OWN module-level ``config_store`` for the
    # driver-selection override; without this patch resolution leaks the real
    # dev-box config (and its polar_align selection) into the test.
    monkeypatch.setattr(providers, "config_store", store)
    store.set_site(Site(name="Test", latitude=_LAT, longitude=_LON,
                        is_default=False), expected_version=None)
    store.set_safety(SafetyConfig(solar_avoidance=False))
    h = Hub()
    await h.connect_sim()
    yield h
    try:
        await h.polar.stop()
    except Exception:
        pass
    await h.disconnect_all()


async def _wait(predicate, timeout=45.0):
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.05)
    return False


async def test_native_recovers_injected_error(sim_hub):
    """With a known (az, alt) misalignment injected, the native session routes to
    the Rust engine, walks measuring→adjusting, and the published total error
    matches the injected magnitude to well under half an arcminute."""
    h = sim_hub
    inj_az, inj_alt = 5.0, 6.0  # arcminutes
    h.sim_rig.set_polar_misalignment(inj_az, inj_alt, lat_deg=_LAT, lon_deg=_LON)
    expected = h.sim_rig.polar_misalignment.expected_total_arcmin

    q = bus.subscribe()
    try:
        await h.polar.start()
        assert h.polar.state["source"] == "native"  # resolved synchronously
        # Wait until the measuring fit lands and we are in the adjusting phase.
        assert await _wait(lambda: h.polar.state.get("phase") == "adjusting"), \
            h.polar.state
        # Drain the queue deterministically (all measuring/adjusting events are
        # buffered on it, well under its cap) and reduce to the ordered phase
        # transitions — no concurrent collector to race the cancel.
        seen_phases: list[str] = []
        while not q.empty():
            ev = q.get_nowait()
            if ev.type == "polar":
                ph = ev.data.get("phase")
                if ph and (not seen_phases or seen_phases[-1] != ph):
                    seen_phases.append(ph)
    finally:
        bus.unsubscribe(q)

    st = h.polar.state
    assert st["source"] == "native"
    assert "measuring" in seen_phases and "adjusting" in seen_phases
    assert seen_phases.index("measuring") < seen_phases.index("adjusting")
    # The headline recovery assertion: engine error ≈ injected error.
    assert abs(st["total_error"] - expected) < 0.5, st
    assert abs(st["az_error"] - inj_az) < 0.5, st
    assert abs(st["alt_error"] - inj_alt) < 0.5, st
    # Knob-direction hints are surfaced additively for the native wizard —
    # and they name the OPPOSITE half of the sky from the error, which is the
    # whole content of a direction hint. Accepting any of the four labels (what
    # this asserted until 2026-09-07) cannot fail on a mirror, and a mirror in
    # exactly this field is what the operator reported from the 2026-09-05
    # session. The injected error is +5' azimuth (axis EAST of the pole) and
    # +6' altitude (axis ABOVE it), so the bolts go west and down. See
    # test_the_published_signs_say_where_the_axis_is below for the convention
    # spelled out, and the pointers there for where it is proved exhaustively.
    assert st.get("az_direction") == "left_west", st
    assert st.get("alt_direction") == "down", st

    # Stop lands on a clean terminal idle state.
    await h.polar.stop()
    assert not h.polar.running
    assert h.polar.state["state"] == "idle"


async def test_the_published_signs_say_where_the_axis_is(sim_hub):
    """THE CONVENTION, on the event the UI reads, in the quadrant nothing else
    on this path covers.

    A NEGATIVE ``az_error`` means the mount's RA axis sits WEST of the celestial
    pole and the azimuth bolt goes EAST; a NEGATIVE ``alt_error`` means the axis
    sits BELOW the pole and the altitude bolt goes UP. Both fields say where the
    axis IS — never which way to move it — and the ``*_direction`` strings carry
    the movement.

    Written down here because on 2026-09-05 the reticle was reported mirrored in
    azimuth with the vertical term fine, and the first thing anyone needs is a
    single worked example of what the published sign is supposed to mean. The
    number itself is proved against forward models that share no code with the
    engine, exhaustively and in both hemispheres, by
    ``test_tppa_engine_geometry.py`` (the three-point fit) and
    ``test_tppa_adjust_and_report.py`` (the live re-scale, including the
    overshoot reversal); what this adds is the assertion at the far end of the
    publish path, on ``PolarAlignSession.state`` as ``/api/polar/state`` and the
    ``polar`` event serve it, with the opposite sign from the test above.
    """
    h = sim_hub
    h.sim_rig.set_polar_misalignment(-5.0, -6.0, lat_deg=_LAT, lon_deg=_LON)
    await h.polar.start()
    assert await _wait(lambda: h.polar.state.get("phase") == "adjusting"), \
        h.polar.state

    st = h.polar.state
    assert st["az_error"] < 0.0 and abs(st["az_error"] + 5.0) < 0.5, st
    assert st["alt_error"] < 0.0 and abs(st["alt_error"] + 6.0) < 0.5, st
    assert st["az_direction"] == "right_east", st
    assert st["alt_direction"] == "up", st
    # The rule, not the two literals: the instruction is always the far side of
    # the pole from the error, on BOTH axes. Stated this way because the defect
    # being guarded against is one axis being mirrored while the other is not.
    assert ("west" in st["az_direction"]) == (st["az_error"] > 0), st
    assert (st["alt_direction"] == "down") == (st["alt_error"] > 0), st


async def test_native_respects_stop_during_measuring(sim_hub):
    """A STOP during the measuring phase cancels the driver and settles idle."""
    h = sim_hub
    h.sim_rig.set_polar_misalignment(4.0, 3.0, lat_deg=_LAT, lon_deg=_LON)
    await h.polar.start()
    assert h.polar.state["source"] == "native"
    # Let it get into the run (first capture/solve) then stop.
    assert await _wait(lambda: h.polar.state.get("phase") == "measuring")
    await h.polar.stop()
    assert not h.polar.running
    assert h.polar.state["state"] == "idle"


async def test_native_publishes_terminal_on_motion_epoch_bump_mid_measure(sim_hub):
    """A motion-epoch advance mid-measure (mount STOP, park, jog rate-0 STOP,
    deadman halt, or a sequence safety abort all call ``hub.bump_motion_epoch()``)
    must land the polar session on a terminal state, not leave the UI reticle
    stuck on stale ``state:"running"``. Regression for the bug where
    ``_check_alive`` raised ``CancelledError``, which ``run_native`` re-raised
    without publishing anything."""
    h = sim_hub
    h.sim_rig.set_polar_misalignment(4.0, 3.0, lat_deg=_LAT, lon_deg=_LON)
    await h.polar.start()
    assert h.polar.state["source"] == "native"
    assert await _wait(lambda: h.polar.state.get("phase") == "measuring")
    # Simulate an abort path (STOP/park/deadman/safety) WITHOUT going through
    # PolarAlignSession.stop() — only the epoch fence, exactly as those paths do.
    h.bump_motion_epoch()
    assert await _wait(lambda: h.polar.state.get("state") != "running", timeout=10.0), \
        h.polar.state
    assert h.polar.state["state"] in ("error", "idle")
    assert not h.polar.running


async def test_native_reaches_terminal_on_alignment(sim_hub):
    """When the injected error is already below the 'aligned' threshold, the
    adjusting phase converges to a terminal ``done``."""
    h = sim_hub
    # Sub-arcminute total error → the first tppa_update crosses the done gate.
    h.sim_rig.set_polar_misalignment(0.4, 0.3, lat_deg=_LAT, lon_deg=_LON)
    await h.polar.start()
    assert await _wait(lambda: h.polar.state["state"] == "done", timeout=60.0), \
        h.polar.state
    assert h.polar.state["total_error"] < 1.0
    assert not h.polar.running


async def test_native_publishes_the_position_angle_spread(sim_hub):
    """The engine's RAW spread magnitude has to reach the payload under the key
    the PyO3 layer exports, so the UI's "this fit is not trustworthy" caveat can
    name a number. The stubbed engine in test_polar_pause.py cannot catch a
    rename on the Rust side — only a real run can."""
    h = sim_hub
    h.sim_rig.set_polar_misalignment(5.0, 6.0, lat_deg=_LAT, lon_deg=_LON)
    await h.polar.start()
    assert await _wait(lambda: h.polar.state.get("phase") == "adjusting"), \
        h.polar.state
    st = h.polar.state
    assert "position_angle_spread_deg" in st, st
    assert isinstance(st["position_angle_spread_deg"], float), st
    # A clean sim run is a pure RA rotation, so it must NOT raise the caveat.
    assert "position_angle_spread_large" not in st.get("flags", []), st
    await h.polar.stop()


async def test_provider_routes_astrodeck_for_native_rig(sim_hub):
    """A native (sim) rig with camera+mount+trusted solver resolves polar align to
    the AstroDeck native engine."""
    h = sim_hub
    choice = providers.resolve("polar_align", h)
    assert choice.kind == "astrodeck", choice
    assert choice.label == "AstroDeck native", choice


async def test_native_outranks_nina_bridge_in_auto(sim_hub):
    """Driver-selection model: a mere-present NINA bridge does NOT auto-outrank
    the first-party native engine. On a native-capable rig (camera + mount +
    trusted solver), AUTO resolves polar align to AstroDeck native even when a
    NINA bridge is connected — NINA is a transition bridge, used only when
    EXPLICITLY selected (see the next test)."""
    h = sim_hub
    h.nina_client = type("N", (), {"host": "127.0.0.1", "port": 1})()
    choice = providers.resolve("polar_align", h)
    assert choice.kind == "astrodeck", choice
    assert choice.label == "AstroDeck native", choice


async def test_explicit_selection_routes_polar_to_nina(sim_hub):
    """The driver-selection model DOES route polar align to NINA when the user
    explicitly selects the backend provider (config ``providers.polar_align`` =
    the legacy ``"backend"`` NINA-family value), even on a native-capable rig."""
    from astrodeck.config import ProvidersConfig
    h = sim_hub
    h.nina_client = type("N", (), {"host": "127.0.0.1", "port": 1})()
    providers.config_store.set_providers(ProvidersConfig(polar_align="backend"))
    choice = providers.resolve("polar_align", h)
    assert choice.kind == "backend", choice
    assert choice.label == "NINA", choice
