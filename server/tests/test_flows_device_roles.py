"""Flows equipment: the DOME CONTROL and FLAT PANEL device contracts.

Backend work-list item 6 of ``design_handoff_astrodeck_flows``. The two ROLES
(``Dome``, ``CoverCalibrator``) already existed with Alpaca + sim backends and
are covered by test_dome_role / test_sim_dome / test_sim_covercalibrator; what is
under test here is the layer those roles never had — the nodes' parameters as
values the server acts on, and the two behaviours the handoff calls
non-negotiable: a shutter that is CONFIRMED open before the run cursor moves on,
and a fail-closed policy no input can argue with.

Driven against the REAL sim devices wherever the sim can express the situation,
and against small in-file fakes only for hardware the sim cannot be (a shutter
that stalls, a driver that lies about slaving) — a fake that reimplements the
thing under test proves nothing about the thing under test.
"""
import time

import pytest

from astrodeck.devices import base
from astrodeck.devices.base import (
    CoverCalibrator,
    CoverState,
    DeviceError,
    Dome,
    DomePolicy,
    DomeShutterState,
    FlatPanel,
    FlatPanelPlacement,
    FlatPanelPolicy,
)
from astrodeck.devices.sim import (
    SimCoverCalibrator,
    SimDome,
    SimRotatingDome,
    build_sim_rig,
)


class _ScriptedDome(Dome):
    """A dome whose shutter reports a scripted sequence of states.

    The last state repeats forever, so a one-element script is a shutter stuck in
    that state — which is the hardware the simulator cannot be (``SimDome``
    always completes its travel) and the hardware that costs a night.
    """

    def __init__(self, states, *, can_bind: bool = False,
                 name: str = "Scripted Dome") -> None:
        super().__init__(name)
        self._states = list(states)
        self.open_commands = 0
        self.bound = False
        self.can_bind = can_bind

    async def connect(self) -> None:
        self.connected = True

    async def disconnect(self) -> None:
        self.connected = False

    async def shutter_state(self) -> DomeShutterState:
        return self._states[0] if len(self._states) == 1 else self._states.pop(0)

    async def open_shutter(self) -> None:
        self.open_commands += 1

    async def close_shutter(self) -> None:
        self._states = [DomeShutterState.CLOSED]

    async def set_bound(self, on: bool) -> None:
        self.bound = bool(on)

    async def get_bound(self) -> bool:
        return self.bound


@pytest.fixture
def fast_poll(monkeypatch):
    """Shorten the shutter re-read interval so a timeout test costs milliseconds.

    ``open_and_confirm`` reads the module global on every wait, so this is a
    runtime seam and not a rewrite of the code under test — the loop, the
    deadline and the raise are all still the shipped ones."""
    monkeypatch.setattr(base, "SHUTTER_POLL_S", 0.005)


@pytest.fixture
async def lit_panel():
    """A connected sim flat panel, lit, cover shut — a rig ready to shoot flats."""
    panel = build_sim_rig()["covercalibrator"]
    await panel.connect()
    await panel.calibrator_on(180)
    return panel


# --------------------------------------------------------------- the FlatPanel
# role reconciliation


def test_flatpanel_names_the_covercalibrator_role_rather_than_a_second_one():
    """One piece of hardware, one role, one hub key.

    If FLAT PANEL were its own class the hub would carry two handles to the same
    panel, and the calibration queue could hold one while the dusk-flats stage
    held the other — two devices' worth of state for one lamp, disagreeing about
    whether it is on."""
    assert FlatPanel is CoverCalibrator
    panel = build_sim_rig()["covercalibrator"]
    assert isinstance(panel, FlatPanel) and isinstance(panel, SimCoverCalibrator)


# ------------------------------------------------------- FLAT PANEL parameters


def test_placement_parses_the_three_strings_the_ui_offers():
    """The node's Position select is a closed list; the server must read all of
    it. A placement that failed to parse would send a dome-mounted panel down the
    dust-cover branch and refuse to ever call it ready."""
    assert FlatPanelPlacement.parse("Dust-cover panel") is FlatPanelPlacement.DUST_COVER
    assert FlatPanelPlacement.parse("Dome-mounted") is FlatPanelPlacement.DOME_MOUNTED
    assert FlatPanelPlacement.parse("Handheld") is FlatPanelPlacement.HANDHELD


def test_an_unrecognised_placement_falls_back_to_the_strictest_reading():
    """A graph from another build must not become permission to shoot the sky.

    DUST_COVER is the only placement that refuses while the cover is open, so it
    is the one an unknown value has to land on: the alternative is a "flat" of
    the open sky that passes every ADU check and then divides real data by it."""
    for junk in ("", None, "Ceiling-mounted", 7):
        assert FlatPanelPlacement.parse(junk) is FlatPanelPlacement.DUST_COVER


def test_the_panel_policy_carries_the_nodes_three_parameters():
    """position / ADU target / solve-per-filter are the whole FLAT PANEL
    contract; a parameter dropped here is a setting the operator changes in the
    UI and the rig silently ignores."""
    p = FlatPanelPolicy.from_node_params(
        {"position": "Dome-mounted", "adu": 22000, "solve": "Solve per filter"})
    assert p.placement is FlatPanelPlacement.DOME_MOUNTED
    assert p.adu_target == 22000
    assert p.solve_per_filter is True
    assert FlatPanelPolicy.from_node_params(
        {"position": "Handheld", "adu": 28500, "solve": "Fixed"}
    ).solve_per_filter is False


def test_a_garbled_adu_target_becomes_the_default_and_never_zero():
    """Zero is a target the solver would chase: it drives the panel to black,
    converges, and banks a set of flats that divide real frames by noise."""
    for junk in ({}, {"adu": ""}, {"adu": "bright"}, {"adu": 0}, {"adu": -400}):
        assert FlatPanelPolicy.from_node_params(junk).adu_target == 28500


# ------------------------------------------------- FLAT PANEL "panel ready"


async def test_an_unlit_panel_is_not_ready_and_names_the_state(lit_panel):
    """The queue's flats branch hangs off this. A dark panel that reads ready
    produces a set of "flats" that are darks with the wrong header — and they
    look plausible enough to survive into the library."""
    await lit_panel.calibrator_off()
    r = await FlatPanelPolicy().readiness(lit_panel)
    assert r.ready is False
    assert "off" in r.reason


async def test_a_dust_cover_panel_is_ready_only_while_the_cover_is_shut(lit_panel):
    """A dust-cover panel illuminates the aperture through the shut cover. With
    the cover open the sensor sees the sky past a lamp pointing away from it, and
    the frame is a picture of dusk that the library will file as a flat."""
    policy = FlatPanelPolicy(placement=FlatPanelPlacement.DUST_COVER)
    assert await lit_panel.get_cover_state() is CoverState.CLOSED
    ready = await policy.readiness(lit_panel)
    assert ready.ready is True and ready.unverifiable == ""

    await lit_panel.open_cover()
    opened = await policy.readiness(lit_panel)
    assert opened.ready is False
    assert "cover is open" in opened.reason


async def test_a_flat_cap_with_no_motorised_cover_is_ready_but_says_it_is_blind(
        lit_panel, monkeypatch):
    """A translucent lens cap is the DUSK FLATS node's default method and has no
    motor to report. Refusing it would make the default flats method unrunnable;
    calling it *confirmed* would let an uncapped scope shoot sky flats at ADU
    28500 and nobody would know which nights were which."""
    async def _no_cover():
        return CoverState.NOT_PRESENT
    monkeypatch.setattr(lit_panel, "has_cover", False)
    monkeypatch.setattr(lit_panel, "get_cover_state", _no_cover)

    r = await FlatPanelPolicy(placement=FlatPanelPlacement.DUST_COVER).readiness(
        lit_panel)
    assert r.ready is True
    assert "capped" in r.unverifiable


async def test_a_dome_mounted_panel_needs_the_cover_open_and_admits_what_it_cannot_see(
        lit_panel):
    """The panel is on the dome wall, so a shut cover is a lid between it and the
    sensor — and even open, nothing on this device knows where the mount is
    aimed. A verdict that hid that would let the queue shoot the dome floor."""
    policy = FlatPanelPolicy(placement=FlatPanelPlacement.DOME_MOUNTED)
    shut = await policy.readiness(lit_panel)
    assert shut.ready is False and "cover is shut" in shut.reason

    await lit_panel.open_cover()
    open_ = await policy.readiness(lit_panel)
    assert open_.ready is True
    assert "aimed" in open_.unverifiable


async def test_a_handheld_panel_is_ready_but_flags_the_person_it_cannot_see(
        lit_panel):
    """Nothing reports a human holding a panel. The queue has to be able to tell
    "measured ready" from "ready as far as anyone can tell" so it can announce
    the second instead of quietly starting a 20-frame flat run."""
    await lit_panel.open_cover()
    r = await FlatPanelPolicy(placement=FlatPanelPlacement.HANDHELD).readiness(
        lit_panel)
    assert r.ready is True
    assert "holding" in r.unverifiable


async def test_a_cover_mid_travel_is_not_ready_yet(lit_panel, monkeypatch):
    """A frame taken through a half-open cover cannot be un-taken, and the queue
    can simply ask again a second later."""
    async def _moving():
        return CoverState.MOVING
    monkeypatch.setattr(lit_panel, "get_cover_state", _moving)
    r = await FlatPanelPolicy().readiness(lit_panel)
    assert r.ready is False and "settled" in r.reason


async def test_a_missing_or_disconnected_panel_is_never_ready():
    """Doctor rule 7 promises the queue SKIPS flats without a panel. A None or
    dropped handle reading ready would make it try anyway, at whatever brightness
    the last run left behind."""
    absent = await FlatPanelPolicy().readiness(None)
    assert absent.ready is False and "no flat panel" in absent.reason

    panel = build_sim_rig()["covercalibrator"]     # constructed, never connected
    dropped = await FlatPanelPolicy().readiness(panel)
    assert dropped.ready is False


# ------------------------------------------------------ DOME CONTROL parameters


def test_the_dome_policy_has_no_field_that_could_stop_it_closing_on_unsafe():
    """The README calls on-unsafe close non-negotiable. A field would be a door
    for a "don't close" to arrive through — an older client, a hand-edited plan,
    a config merge — and the roof would still be open in the rain because
    something it should never have listened to told it to stay put."""
    import dataclasses

    assert DomePolicy.ON_UNSAFE == "close"
    assert "on_unsafe" not in {f.name for f in dataclasses.fields(DomePolicy)}
    assert DomePolicy().to_plan()["on_unsafe"] == "close"
    # a plan that says otherwise is read and overruled, not honoured
    talked_out_of_it = DomePolicy.from_plan({"bind": True, "on_unsafe": "ignore"})
    assert talked_out_of_it.to_plan()["on_unsafe"] == "close"


def test_the_dome_policy_reads_the_nodes_azimuth_and_timeout_fields():
    """Manual azimuth is a deliberate choice an operator makes when they are
    doing something by hand at the dome; ignoring it moves machinery around
    somebody's cabling."""
    bound = DomePolicy.from_node_params(
        {"bind": "Bind to mount", "onUnsafe": "Close (fail closed)", "timeout": 120})
    assert bound.bind_to_mount is True and bound.shutter_timeout_s == 120.0
    manual = DomePolicy.from_node_params({"bind": "Manual", "timeout": "90"})
    assert manual.bind_to_mount is False and manual.shutter_timeout_s == 90.0

    # BACK-COMPAT: the param was `slave` until 2026-08-14, and every flow
    # saved before then still carries that name on disk. Reading only the new
    # key would silently re-bind a dome the operator had set to Manual.
    old = DomePolicy.from_node_params({"slave": "Manual", "timeout": "90"})
    assert old.bind_to_mount is False, "a graph saved before the rename changed meaning"
    old_plan = DomePolicy.from_plan({"slave": False})
    assert old_plan.bind_to_mount is False, "a plan compiled before the rename changed meaning"


def test_a_zero_or_garbled_shutter_timeout_becomes_the_default_not_no_wait():
    """A zero timeout would make "confirm the roof opened" unsatisfiable on the
    first pass, so every run either fails at the dome or — worse, if anyone ever
    reads zero as "don't bother waiting" — proceeds under a roof nobody
    checked."""
    for junk in ({}, {"timeout": 0}, {"timeout": -30}, {"timeout": ""},
                 {"timeout": "soon"}):
        assert DomePolicy.from_node_params(junk).shutter_timeout_s == 120.0


# ----------------------------------------------------- DOME CONTROL behaviour


async def test_opening_returns_only_once_the_shutter_reports_open(fast_poll):
    """The node's only flow output is "shutter open" and the cursor leaves
    through it into SLEW + CENTER. Returning while the roof is still travelling
    slews the mount under a moving roof."""
    dome = _ScriptedDome([DomeShutterState.CLOSED, DomeShutterState.OPENING,
                          DomeShutterState.OPENING, DomeShutterState.OPEN])
    await DomePolicy(shutter_timeout_s=5).open_and_confirm(dome)
    assert dome.open_commands == 1
    assert await dome.shutter_state() is DomeShutterState.OPEN


async def test_a_shutter_that_never_opens_fails_the_stage(fast_poll):
    """The failure this exists for is silent: a stalled motor, a run that
    proceeds anyway, and 170 black frames whose logs say nothing about the
    roof."""
    stuck = _ScriptedDome([DomeShutterState.OPENING])
    with pytest.raises(DeviceError) as err:
        await DomePolicy(shutter_timeout_s=0.05).open_and_confirm(stuck)
    assert "did not confirm open" in str(err.value)
    assert stuck.open_commands == 1


async def test_a_faulted_shutter_fails_immediately_instead_of_burning_the_timeout(
        fast_poll):
    """A dome that has already reported it cannot open will not start working in
    two minutes' time, and two minutes of a clear night spent waiting for it is
    two minutes nobody gets back."""
    faulted = _ScriptedDome([DomeShutterState.CLOSED, DomeShutterState.ERROR])
    started = time.monotonic()
    with pytest.raises(DeviceError) as err:
        await DomePolicy(shutter_timeout_s=30).open_and_confirm(faulted)
    assert "faulted" in str(err.value)
    assert time.monotonic() - started < 1.0


async def test_an_already_open_roof_is_not_sent_through_another_travel_cycle():
    """The stage can be re-entered — a resumed session, a second night on the
    same flow — and some drivers answer a redundant open by re-running the whole
    travel, which on a roll-off roof is a minute of motion for nothing."""
    already = _ScriptedDome([DomeShutterState.OPEN])
    await DomePolicy().open_and_confirm(already)
    assert already.open_commands == 0


async def test_opening_the_real_sim_roof_confirms_it(fast_poll):
    """End to end on the simulator, which is where the whole Flows surface has to
    demo: park, close for the night, then let the flow open the roof at dusk."""
    rig = build_sim_rig()
    dome = rig["dome"]
    await dome.connect()
    rig["_rig"].parked = True
    await dome.close_shutter()
    assert await dome.is_closed() is True

    await DomePolicy(shutter_timeout_s=5).open_and_confirm(dome)
    assert await dome.is_open() is True


# ---------------------------------------------------------------- slave-to-mount


async def test_binding_a_roll_off_roof_says_so_instead_of_failing_the_night():
    """"Slave to mount" is the node's DEFAULT and the default sim dome is a roll-
    off roof with no azimuth. If that combination raised, every example flow with
    a dome in it would be unrunnable on a machine with no hardware — which is the
    one thing the handoff says the surface must not need."""
    rig = build_sim_rig()
    roof = rig["dome"]
    await roof.connect()
    assert isinstance(roof, SimDome) and roof.can_bind is False

    note = await DomePolicy(bind_to_mount=True).apply_binding(roof)
    assert "no effect" in note
    assert await roof.get_bound() is False


async def test_binding_a_rotating_dome_actually_binds_it():
    """The other half: on hardware that HAS an azimuth the setting must take.
    A note-and-carry-on for every dome would make the parameter decorative."""
    dome = SimRotatingDome(build_sim_rig()["_rig"])
    await dome.connect()
    assert dome.can_bind is True
    note = await DomePolicy(bind_to_mount=True).apply_binding(dome)
    assert await dome.get_bound() is True
    assert "bound" in note


async def test_manual_azimuth_never_touches_the_dome(monkeypatch):
    """Manual means a person is driving the dome, possibly standing next to it.
    Slaving anyway moves machinery they did not ask to have move."""
    dome = SimRotatingDome(build_sim_rig()["_rig"])
    await dome.connect()

    async def _refuse(on):
        raise AssertionError("Manual azimuth commanded the dome to slave")
    monkeypatch.setattr(dome, "set_bound", _refuse)

    note = await DomePolicy(bind_to_mount=False).apply_binding(dome)
    assert "manual" in note
    assert await dome.get_bound() is False


async def test_a_dome_that_claims_it_can_bind_and_then_refuses_fails_loudly():
    """Degrading here would leave the OTA photographing the inside of the dome
    wall all night while the status readout said bound - the shape of bug this
    project keeps paying for, where the flag is a memory rather than a
    measurement."""
    class _Liar(_ScriptedDome):
        async def set_bound(self, on: bool) -> None:
            raise DeviceError("dome refused Slaved")

    liar = _Liar([DomeShutterState.OPEN], can_bind=True)
    with pytest.raises(DeviceError):
        await DomePolicy(bind_to_mount=True).apply_binding(liar)


async def test_the_rotating_sim_dome_keeps_the_park_before_close_guard():
    """Adding an azimuth must not cost the collision model: a roof that closes
    over an unparked mount crushes the OTA, and that guard is the reason the sim
    dome exists at all."""
    rig = build_sim_rig()
    dome = SimRotatingDome(rig["_rig"])
    await dome.connect()
    assert isinstance(dome, Dome) and dome.requires_park_before_close is True
    assert rig["_rig"].parked is False
    with pytest.raises(DeviceError):
        await dome.close_shutter()
    assert await dome.shutter_state() is DomeShutterState.ERROR
