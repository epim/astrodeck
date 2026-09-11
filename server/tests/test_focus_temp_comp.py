"""Focus follows temperature, and the run corrects for it between frames.

WHAT IS PINNED HERE, and why each half needs its own guard.

The arithmetic is nine rules deep and eight of them end in "do nothing". A
feature whose ordinary answer is silence is the house defect class: it can be
disconnected at either end - a coefficient nothing reads, a decision nothing
acts on - and every existing test still passes. So the rule table is graded on
the REASON string as well as the move, because "no move" with no explanation is
indistinguishable from "not wired up".

The engine half is graded on the FAKE FOCUSER'S RECORDED MOVES, not on the
decision object, for the same reason: the whole point of D-RIG-2 is that
something physically moves at a frame boundary.

And the backlash guard is here because it is the single most likely way to get
this feature wrong. A compensating move is tens of steps; the EAF on this rig
has about forty steps of slack. An OUTWARD compensating move issued directly
turns the motor and leaves the tube exactly where it was - and unlike a sweep,
nothing downstream measures it, so the log would say "compensated" every
boundary all night while the focus walked away. `focus.approach` is the one
place that knows to overshoot an outward target and come back down onto it, and
this file pins that compensation goes through it.

SIGN CONVENTION. `steps_per_c` is steps per degree of TEMPERATURE, so the
correction is `ref_pos + steps_per_c * (temp - ref_temp)` - the same form
ASCOM-family software uses. A POSITIVE coefficient therefore moves the drawtube
IN as the night cools and a NEGATIVE one moves it OUT. The two directions are
asserted explicitly below, because a coefficient measured with the sign
backwards does not fail to correct the drift, it DOUBLES it.
"""
from __future__ import annotations

import asyncio
import time

import pytest

import astrodeck.config as config_mod
import astrodeck.hub as hub_module
import astrodeck.sequence.engine as engine_mod
from astrodeck.config import ConfigStore, Site
from astrodeck.focus.tempcomp import (
    TEMP_COMP_PRECEDENCE, TempCompConfig, decide, predict, status_node,
)
from astrodeck.hub import Hub
from astrodeck.sequence import ExposureStep, SequenceEngine, SequencePlan, Target

REF_POS = 10_000
REF_TEMP = 10.0
FOC_MAX = 40_000


def _cfg(**kw) -> TempCompConfig:
    """An ARMED config: enabled, with a coefficient and an anchored reference.
    Every rule that is not about being unarmed starts from here."""
    base = dict(enabled=True, steps_per_c=10.0, reference_temp_c=REF_TEMP,
                reference_position=REF_POS)
    base.update(kw)
    return TempCompConfig(**base)


def _decide(cfg: TempCompConfig, temp, position=REF_POS, focuser_max=FOC_MAX):
    return decide(cfg=cfg, current_temp_c=temp, current_position=position,
                  focuser_max=focuser_max)


# --------------------------------------------------------------- the rule table

class TestTheRuleTable:
    """All nine rules, each on the move AND on the sentence it gives back."""

    def test_1_off_says_it_is_off(self):
        d = _decide(_cfg(enabled=False), 5.0)
        assert d.move_to is None and d.delta_steps == 0
        assert d.reason == "temperature compensation is off"

    def test_2_no_coefficient_is_not_the_same_as_off(self):
        """A rig that turned compensation on and never measured its tube must
        not be told compensation is off - it is on and has nothing to apply."""
        d = _decide(_cfg(steps_per_c=0.0), 5.0)
        assert d.move_to is None
        assert d.reason == "no coefficient set"

    def test_3_no_thermometer_never_guesses(self):
        """`Focuser.get_temperature` defaults to None. An assumed ambient would
        move the drawtube on the strength of a number nothing measured."""
        d = _decide(_cfg(), None)
        assert d.move_to is None and d.delta_steps == 0
        assert d.reason == "this focuser has no thermometer"

    def test_4_a_missing_reference_anchors_rather_than_moves(self):
        d = _decide(TempCompConfig(enabled=True, steps_per_c=10.0), 5.0)
        assert d.move_to is None and d.delta_steps == 0
        assert d.rebased is True
        assert d.reason == "first reading - anchored the reference here"

    def test_4_a_reference_temperature_of_zero_is_a_reference(self):
        """`reference_temp_c` is `float | None` and None is NOT 0. A rig
        anchored at freezing must not re-anchor every boundary."""
        cfg = _cfg(reference_temp_c=0.0, reference_position=REF_POS)
        d = _decide(cfg, -3.0)
        assert d.rebased is False
        assert d.move_to == REF_POS - 30

    def test_5_the_arithmetic(self):
        """target = reference_position + steps_per_c * (temp - reference_temp)"""
        d = _decide(_cfg(), REF_TEMP - 4.0)
        assert d.move_to == REF_POS - 40
        assert d.delta_steps == -40

    def test_6_a_drift_under_the_deadband_moves_nothing_and_says_so(self):
        """3 steps against 40 steps of EAF slack turns the motor, not the tube."""
        d = _decide(_cfg(steps_per_c=3.0, deadband_steps=5), REF_TEMP - 1.0)
        assert d.move_to is None and d.delta_steps == 0
        assert d.clamped is False
        assert d.reason == "drift is -3 steps, under the 5-step deadband"

    def test_7_a_wild_drift_is_clamped_to_the_per_move_backstop(self):
        """A 40 C jump is a glitching thermometer or a wrong coefficient, and
        either way the focuser must not be driven 560 steps between frames."""
        d = _decide(_cfg(steps_per_c=14.0, max_step_per_move=200),
                    REF_TEMP + 40.0)
        assert d.clamped is True
        assert d.delta_steps == 200
        assert d.move_to == REF_POS + 200
        assert d.reason == "drift asks for 560 steps; clamped to 200"

    def test_7_the_backstop_is_signed(self):
        d = _decide(_cfg(steps_per_c=14.0, max_step_per_move=200),
                    REF_TEMP - 40.0)
        assert d.delta_steps == -200
        assert d.reason == "drift asks for -560 steps; clamped to -200"

    def test_8_a_target_below_zero_is_clamped_to_zero(self):
        cfg = _cfg(steps_per_c=100.0, reference_position=120,
                   max_step_per_move=5000)
        d = _decide(cfg, REF_TEMP - 3.0, position=120)
        assert d.move_to == 0
        assert d.delta_steps == -120
        assert d.clamped is True
        assert d.reason == "the focuser's travel stops at 0; clamped to 0"

    def test_8_a_target_past_the_top_of_travel_names_the_limit(self):
        cfg = _cfg(steps_per_c=100.0, reference_position=FOC_MAX - 120,
                   max_step_per_move=5000)
        d = _decide(cfg, REF_TEMP + 3.0, position=FOC_MAX - 120)
        assert d.move_to == FOC_MAX
        assert d.delta_steps == 120
        assert d.clamped is True
        assert d.reason == (f"the focuser's travel stops at {FOC_MAX}; "
                            f"clamped to {FOC_MAX}")

    def test_9_the_ordinary_case_moves_and_explains_itself(self):
        d = _decide(_cfg(), REF_TEMP - 2.0, position=REF_POS)
        assert d.move_to == REF_POS - 20
        assert d.delta_steps == -20
        assert d.clamped is False and d.rebased is False
        assert d.reason == ("temperature is -2.0 C from the reference; "
                            "moving -20 steps")


class TestTheSignIsSigned:
    """A coefficient measured backwards does not fail to correct the drift, it
    doubles it. Both directions are asserted rather than inferred."""

    def test_a_positive_coefficient_moves_IN_as_it_cools(self):
        d = _decide(_cfg(steps_per_c=14.0), REF_TEMP - 1.0)
        assert d.delta_steps == -14, "positive coefficient, 1 C fall -> IN"
        assert d.move_to == REF_POS - 14

    def test_a_negative_coefficient_moves_OUT_as_it_cools(self):
        d = _decide(_cfg(steps_per_c=-14.0), REF_TEMP - 1.0)
        assert d.delta_steps == 14, "negative coefficient, 1 C fall -> OUT"
        assert d.move_to == REF_POS + 14

    def test_the_two_signs_are_mirror_images(self):
        warm = _decide(_cfg(steps_per_c=14.0), REF_TEMP + 1.0)
        cool = _decide(_cfg(steps_per_c=14.0), REF_TEMP - 1.0)
        assert warm.delta_steps == -cool.delta_steps == 14


class TestTheStatusNode:
    """S7c reads a node, not a decision object."""

    def test_it_predicts_without_moving_anything(self):
        node = status_node(_cfg(), temperature_c=REF_TEMP - 2.0,
                           position=REF_POS, focuser_max=FOC_MAX)
        assert node["enabled"] is True
        assert node["steps_per_c"] == 10.0
        assert node["reference_temp_c"] == REF_TEMP
        assert node["reference_position"] == REF_POS
        assert node["predicted_position"] == REF_POS - 20
        assert node["last_move_steps"] is None
        assert "moving -20 steps" in node["last_reason"]

    def test_predict_is_decide_under_a_read_only_name(self):
        a = predict(_cfg(), REF_TEMP - 2.0, REF_POS, FOC_MAX)
        b = _decide(_cfg(), REF_TEMP - 2.0)
        assert a == b


def test_the_precedence_sentence_is_carried_verbatim():
    """The sheet quotes this string; nothing paraphrases it."""
    assert TEMP_COMP_PRECEDENCE == (
        "Temperature compensation moves between frames; the refocus trigger "
        "stops and re-measures. When both are on, compensation runs first and "
        "the trigger still fires.")


# --------------------------------------------------------------- engine level

class _FakeFocuser:
    """A focuser whose thermometer the TEST drives and whose every commanded
    position is written down. The sim focuser returns a constant 4.2 C, so it
    cannot express a night that cools."""
    kind = "focuser"
    name = "Fake Focuser"
    max_position = FOC_MAX
    step_size_um = None
    can_set_position_reference = False
    supports_native_autofocus = False
    filter_offsets: list[int] = []

    def __init__(self, position=REF_POS, temp_c=REF_TEMP):
        self.connected = True
        self.position = int(position)
        self.temp_c = temp_c
        self.moves: list[int] = []

    async def connect(self) -> None:
        self.connected = True

    async def disconnect(self) -> None:
        self.connected = False

    async def get_position(self) -> int:
        return self.position

    async def move_to(self, position: int) -> None:
        self.moves.append(int(position))
        self.position = int(position)

    async def halt(self) -> None:
        pass

    async def is_moving(self) -> bool:
        return False

    async def get_temperature(self):
        return self.temp_c


class _AfResult:
    success = True
    message = ""
    position = REF_POS


@pytest.fixture
def temp_store(tmp_path, monkeypatch):
    """An isolated ConfigStore. Not optional: the engine PERSISTS the
    compensation reference through `set_focus`, so a test that armed the real
    singleton would write the developer's own `astrodeck.json` and leave the
    next test running against this one's coefficient."""
    store = ConfigStore(path=tmp_path / "astrodeck.json")
    store.set_site(Site(name="Test", latitude=40.0, longitude=-74.0,
                        is_default=False))
    monkeypatch.setattr(config_mod, "config_store", store)
    monkeypatch.setattr(hub_module, "config_store", store)
    monkeypatch.setattr(engine_mod, "config_store", store)
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path / "captures")
    return store


@pytest.fixture
async def sim_hub(temp_store, monkeypatch):
    monkeypatch.setattr(Hub, "_check_solar",
                        lambda self, ra, dec, *, force=False: None)
    h = Hub()
    await h.connect_sim()
    yield h
    await h.disconnect_all()


@pytest.fixture
def fake_focuser(sim_hub):
    foc = _FakeFocuser()
    sim_hub.devices["focuser"] = foc
    return foc


def arm(store, **kw):
    """Write a `temp_comp` block through the config's own setter.

    The engine reads it as `getattr(cfg.focus, "temp_comp", None) or
    TempCompConfig()` - the seam that let this land before S7i hung the field
    on `FocusConfig`. S7i has since landed, so this goes through the real
    `set_focus`, which is what the Settings panel will call.
    """
    focus = store.cfg().focus
    store.set_focus(focus.model_copy(update={"temp_comp": TempCompConfig(**kw)}))


def _target(count=2, name="tempcomp"):
    return Target(name=name, ra_hours=5.5, dec_deg=-5.0, center=False,
                  autofocus_first=False,
                  steps=[ExposureStep(filter=None, exposure_s=0.05,
                                      count=count)])


def _plan(count=2, **kw):
    return SequencePlan(name="tc", guide=False, dither_every=0,
                        autofocus_every=0, meridian_flip=False,
                        safety_check=False, targets=[_target(count)], **kw)


def _cooling_engine(hub, foc, per_frame=-1.0):
    """The real engine, with the tube cooling by `per_frame` degrees after every
    captured frame - so the drop lands BETWEEN frames, where compensation runs."""
    eng = SequenceEngine(hub)
    inner = eng._capture

    async def _capture(step, target):
        info = await inner(step, target)
        foc.temp_c = round(foc.temp_c + per_frame, 3)
        return info

    eng._capture = _capture
    return eng


async def _run(eng, plan, timeout=60.0):
    eng.start(plan)
    t0 = time.monotonic()
    while eng.running and time.monotonic() - t0 < timeout:
        await asyncio.sleep(0.02)
    assert not eng.running, eng.state
    return eng


class TestTheEngineMovesBetweenFrames:
    async def test_a_one_degree_drop_moves_the_focuser_by_the_coefficient(
            self, sim_hub, fake_focuser, temp_store, monkeypatch):
        """Two frames, one degree of cooling between them. The focuser has to
        physically move by `steps_per_c` steps at the second boundary - and the
        first boundary must anchor rather than move, because there was no
        reference to drift from."""
        arm(temp_store, enabled=True, steps_per_c=30.0, deadband_steps=5)
        eng = _cooling_engine(sim_hub, fake_focuser)
        await _run(eng, _plan(count=2))
        assert fake_focuser.moves, "no compensating move was ever issued"
        assert fake_focuser.position == REF_POS - 30, fake_focuser.moves
        assert eng._temp_comp_last_move == -30

    async def test_the_move_counts_as_a_frame_event(
            self, sim_hub, fake_focuser, temp_store, monkeypatch):
        """The dither/autofocus precedent: an event between frames is not
        per-frame overhead, and the ETA must not learn it as such."""
        arm(temp_store, enabled=True, steps_per_c=30.0)
        eng = _cooling_engine(sim_hub, fake_focuser)
        seen: list[bool] = []
        inner = eng._capture

        async def _capture(step, target):
            seen.append(bool(eng._frame_had_event))
            return await inner(step, target)

        eng._capture = _capture
        await _run(eng, _plan(count=2))
        assert seen[1] is True, (
            "a compensating move must set _frame_had_event, or its seconds are "
            "learned as per-frame overhead")

    async def test_a_focuser_with_no_thermometer_is_left_alone(
            self, sim_hub, fake_focuser, temp_store, monkeypatch):
        """ANCHORED ON PURPOSE. With no reference the seed-on-first-use rule
        absorbs everything and this test would pass against an engine that
        substituted an assumed ambient for the missing reading - a test that
        cannot fail. Given a reference to drift from, a guessed 0 C would move
        the drawtube hundreds of steps."""
        arm(temp_store, enabled=True, steps_per_c=30.0,
            reference_temp_c=REF_TEMP, reference_position=REF_POS)
        fake_focuser.temp_c = None
        eng = _cooling_engine(sim_hub, fake_focuser, per_frame=0.0)
        await _run(eng, _plan(count=2))
        assert fake_focuser.moves == []

    async def test_off_costs_nothing(self, sim_hub, fake_focuser, temp_store):
        """Anchored for the same reason: an unanchored OFF rig does nothing
        whether the switch is read or not."""
        arm(temp_store, enabled=False, steps_per_c=30.0,
            reference_temp_c=REF_TEMP, reference_position=REF_POS)
        eng = _cooling_engine(sim_hub, fake_focuser)
        await _run(eng, _plan(count=2))
        assert fake_focuser.moves == []


class TestBothMechanismsRun:
    async def test_compensation_moves_every_boundary_AND_the_trigger_fires(
            self, sim_hub, fake_focuser, temp_store, monkeypatch):
        """TEMP_COMP_PRECEDENCE, end to end.

        Three frames, one degree of cooling each, a 2 C refocus trigger and a
        10 steps/C coefficient. Compensation must move at the boundaries where
        it has drift to correct, the sweep must still fire when the trigger's
        own threshold is crossed, and afterwards the trigger's baseline and the
        compensation reference must both equal the reading taken at the sweep.
        """
        arm(temp_store, enabled=True, steps_per_c=10.0, deadband_steps=5)
        af: list[dict] = []

        async def _fake_af(cam, foc, **kw):
            af.append(kw)
            return _AfResult()

        monkeypatch.setattr(engine_mod, "run_autofocus", _fake_af)
        eng = _cooling_engine(sim_hub, fake_focuser)
        await _run(eng, _plan(count=3, refocus_on_temp_delta_c=2.0))

        assert len(fake_focuser.moves) >= 2, (
            f"compensation should move at the 2nd and 3rd boundaries: "
            f"{fake_focuser.moves}")
        assert af, "the refocus trigger never fired"
        # The sweep ran at the third boundary, where the focuser had read 8.0 C.
        assert eng._last_focus_temp == pytest.approx(8.0)
        assert eng._temp_comp_ref is not None
        assert eng._temp_comp_ref[0] == pytest.approx(8.0), (
            "the compensation reference and the trigger's baseline must be the "
            "same reading, or they drift apart silently")
        assert eng._temp_comp_ref[1] == fake_focuser.position

    async def test_the_compensating_move_does_not_suppress_the_trigger(
            self, sim_hub, fake_focuser, temp_store, monkeypatch):
        """The trigger is what catches everything compensation cannot. A rig
        that skipped the sweep because it had just compensated would never
        measure its accumulated error against a real star."""
        arm(temp_store, enabled=True, steps_per_c=10.0)
        af: list[dict] = []

        async def _fake_af(cam, foc, **kw):
            af.append(kw)
            return _AfResult()

        monkeypatch.setattr(engine_mod, "run_autofocus", _fake_af)
        eng = _cooling_engine(sim_hub, fake_focuser)
        await _run(eng, _plan(count=3, refocus_on_temp_delta_c=2.0))
        assert fake_focuser.moves and af


class TestTheReferenceIsPersisted:
    """`ConfigStore.set_focus` says in its own docstring that the run
    re-anchors `temp_comp.reference_temp_c` / `reference_position`. A run that
    kept the reference only in memory would make that sentence false, and the
    rig would re-anchor from wherever the drawtube happened to be after every
    restart."""

    async def test_the_first_boundary_writes_the_anchor_to_disk(
            self, sim_hub, fake_focuser, temp_store, tmp_path):
        arm(temp_store, enabled=True, steps_per_c=30.0)
        assert temp_store.cfg().focus.temp_comp.reference_temp_c is None
        eng = _cooling_engine(sim_hub, fake_focuser)
        await _run(eng, _plan(count=2))
        tc = temp_store.cfg().focus.temp_comp
        assert tc.reference_temp_c == pytest.approx(REF_TEMP)
        assert tc.reference_position == REF_POS
        # ...and it is on DISK, not merely in this process's cached config.
        reread = ConfigStore(path=tmp_path / "astrodeck.json")
        assert reread.cfg().focus.temp_comp.reference_position == REF_POS

    async def test_the_anchor_does_not_revert_the_rest_of_the_block(
            self, sim_hub, fake_focuser, temp_store):
        """A wholesale-replace setter written from a stale snapshot would undo
        an operator's mid-run edit to the overshoot."""
        arm(temp_store, enabled=True, steps_per_c=30.0)
        eng = _cooling_engine(sim_hub, fake_focuser)
        await _run(eng, _plan(count=2))
        after = temp_store.cfg().focus
        assert after.approach_overshoot_steps == 200
        assert after.temp_comp.steps_per_c == 30.0
        assert after.temp_comp.enabled is True


class TestTheCompensatingMoveArrivesFromOneSide:
    async def test_an_outward_move_overshoots_and_returns(
            self, sim_hub, fake_focuser, temp_store, monkeypatch):
        """The backlash guard. A compensating move is tens of steps on a
        focuser with about forty steps of slack: issued directly, an OUTWARD
        one turns the motor and leaves the tube where it was, and nothing
        downstream ever measures it. It must go through `focus.approach`, which
        passes an outward target by `focus.approach_overshoot_steps` and comes
        back down onto it."""
        overshoot = temp_store.cfg().focus.approach_overshoot_steps
        assert overshoot > 0, "this rig's overshoot is off; the guard is moot"
        # A NEGATIVE coefficient moves OUT as the tube cools - see the sign
        # convention in the module docstring.
        arm(temp_store, enabled=True, steps_per_c=-30.0, deadband_steps=5)
        eng = _cooling_engine(sim_hub, fake_focuser)
        await _run(eng, _plan(count=2))
        target = REF_POS + 30
        assert fake_focuser.moves == [target + overshoot, target], (
            f"an outward compensating move must overshoot to "
            f"{target + overshoot} and return to {target}; got "
            f"{fake_focuser.moves}")

    async def test_an_inward_move_goes_straight_there(
            self, sim_hub, fake_focuser, temp_store, monkeypatch):
        """There is no slack to take up moving in, so the extra leg would be
        two moves paid for nothing."""
        arm(temp_store, enabled=True, steps_per_c=30.0, deadband_steps=5)
        eng = _cooling_engine(sim_hub, fake_focuser)
        await _run(eng, _plan(count=2))
        assert fake_focuser.moves == [REF_POS - 30], fake_focuser.moves


# ------------------------------------------- compensation vs filter offsets

class TestAFilterOffsetMovesTheReferenceWithIt:
    """The two focus mechanisms that both write an ABSOLUTE position.

    A per-filter offset move (`SequenceEngine._apply_filter`) says "focus for
    this filter is `delta` steps from focus for the last one" and moves the
    drawtube. Compensation then computes `reference_position + steps_per_c *
    (temp - reference_temp)` and moves the drawtube to THAT. With nothing
    joining them, the reference still described the previous filter's focus, so
    the first frame boundary after every filter change read the offset as drift
    and took it straight back out - and both log lines were true on their own,
    which is why it could run all night unnoticed: "applied filter offset +120
    for Ha", then "temperature compensation moved the focuser -120 steps".

    The plans below cool by NOTHING (`per_frame=0.0`), so every move the
    focuser records is a move some mechanism decided to make on a rig whose
    temperature never changed. That is what makes "no move" an assertion.
    """

    @staticmethod
    def _plan(count=1, a="L", b="Ha"):
        """One target, two filters. The sim wheel's offsets are
        [0, 12, 10, 15, 120, 110, 115, 0], so L -> Ha is +120 steps - six times
        the 18-20 steps this rig's broadband offsets are, and well past the
        deadband either way."""
        t = Target(name="offsets", ra_hours=5.5, dec_deg=-5.0, center=False,
                   autofocus_first=False,
                   steps=[ExposureStep(filter=a, exposure_s=0.05, count=count),
                          ExposureStep(filter=b, exposure_s=0.05, count=count)])
        return SequencePlan(name="tc-offsets", guide=False, dither_every=0,
                            autofocus_every=0, meridian_flip=False,
                            safety_check=False, targets=[t])

    @staticmethod
    def _offset(hub, a="L", b="Ha") -> int:
        fw = hub.devices["filterwheel"]
        return (fw.filter_offsets[fw.filter_names.index(b)]
                - fw.filter_offsets[fw.filter_names.index(a)])

    async def test_the_next_boundary_commands_no_move_at_all(
            self, sim_hub, fake_focuser, temp_store):
        """SABOTAGE (run red, restored): delete the
        `_shift_temp_comp_reference` call from `_apply_filter`. The focuser
        then records a third move, straight back to the reference position -
        Ha shot at L's focus."""
        arm(temp_store, enabled=True, steps_per_c=30.0, deadband_steps=5,
            reference_temp_c=REF_TEMP, reference_position=REF_POS)
        sim_hub.sim_rig.filter_slot = 0          # start on L, so only L->Ha moves
        offset = self._offset(sim_hub)
        assert offset == 120, "the sim wheel's offsets moved; retune this test"
        overshoot = temp_store.cfg().focus.approach_overshoot_steps
        eng = _cooling_engine(sim_hub, fake_focuser, per_frame=0.0)
        await _run(eng, self._plan())

        # The OFFSET move, and nothing else. It is outward, so it goes through
        # `focus.approach` (overshoot, then back down onto the target) - that
        # pair is one move, and the list is empty of anything after it.
        assert fake_focuser.moves == [REF_POS + offset + overshoot,
                                      REF_POS + offset], (
            f"the frame boundary after the filter change moved the focuser "
            f"again: {fake_focuser.moves}")
        assert fake_focuser.position == REF_POS + offset

    async def test_the_reference_is_the_one_that_moved(
            self, sim_hub, fake_focuser, temp_store):
        """The fix is a REFERENCE shift, not a suppressed move, so it has to be
        visible in the reference - and at the SAME temperature, because a
        filter change is not a thermometer reading."""
        arm(temp_store, enabled=True, steps_per_c=30.0, deadband_steps=5,
            reference_temp_c=REF_TEMP, reference_position=REF_POS)
        sim_hub.sim_rig.filter_slot = 0
        offset = self._offset(sim_hub)
        eng = _cooling_engine(sim_hub, fake_focuser, per_frame=0.0)
        await _run(eng, self._plan())

        assert eng._temp_comp_ref == (REF_TEMP, REF_POS + offset)
        # ...and on disk, like every other anchor: a restart mid-run must not
        # come back pointing at the previous filter's focus.
        tc = temp_store.cfg().focus.temp_comp
        assert tc.reference_temp_c == pytest.approx(REF_TEMP)
        assert tc.reference_position == REF_POS + offset

    async def test_the_drift_that_is_really_there_is_still_corrected(
            self, sim_hub, fake_focuser, temp_store):
        """The shift must not become a way to swallow real drift. One degree of
        cooling per frame, a filter change in the middle: the offset survives
        AND the temperature is still tracked on top of it."""
        arm(temp_store, enabled=True, steps_per_c=30.0, deadband_steps=5,
            reference_temp_c=REF_TEMP, reference_position=REF_POS)
        sim_hub.sim_rig.filter_slot = 0
        offset = self._offset(sim_hub)
        eng = _cooling_engine(sim_hub, fake_focuser, per_frame=-1.0)
        await _run(eng, self._plan(count=2))

        # Two L frames, the offset, two Ha frames; the tube drops 1 C after
        # every captured frame. Three frames are behind the LAST boundary, so
        # it reads 7.0 C - three degrees under the reference - and asks for
        # 30 * -3 = -90 steps on top of the +120 offset baseline.
        drift = int(round(30.0 * -3.0))
        assert fake_focuser.position == REF_POS + offset + drift, (
            f"final position {fake_focuser.position}, moves "
            f"{fake_focuser.moves}")
        # THE DISCRIMINATING HALF. Both mechanisms are live here, so an
        # assertion on the drift alone would also hold for a run that had
        # thrown the offset away: an unshifted reference lands this same night
        # on REF_POS + drift, 120 steps low and inside Ha's focus tolerance of
        # nothing at all.
        assert fake_focuser.position - drift == REF_POS + offset

    async def test_an_unarmed_rig_pays_nothing_and_writes_nothing(
            self, sim_hub, fake_focuser, temp_store):
        """The shift is guarded by the same two conditions `_apply_temp_comp`
        is, so a rig with compensation off does not gain a config write per
        filter change."""
        arm(temp_store, enabled=False, steps_per_c=30.0,
            reference_temp_c=REF_TEMP, reference_position=REF_POS)
        sim_hub.sim_rig.filter_slot = 0
        offset = self._offset(sim_hub)
        overshoot = temp_store.cfg().focus.approach_overshoot_steps
        eng = _cooling_engine(sim_hub, fake_focuser, per_frame=0.0)
        await _run(eng, self._plan())

        assert fake_focuser.moves == [REF_POS + offset + overshoot,
                                      REF_POS + offset]
        tc = temp_store.cfg().focus.temp_comp
        assert tc.reference_position == REF_POS, "an OFF rig re-anchored"
