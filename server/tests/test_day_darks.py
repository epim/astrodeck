"""Darks after the night, in the one window where they mean anything.

The campaign's SHUTDOWN COMPLETE -> CALIBRATION QUEUE wire was reported as "this
rule will not run", and that was true: nothing in the engine is evaluated after
a shutdown - `_run_instructions` has three call sites and all of them are inside
`_run_scheduled`, which returns before the wind-down.

But the obstacle was never the missing enum member. It was PHYSICAL. A dark is
indexed by exposure, gain, offset, binning AND TEMPERATURE, and the wind-down
parks, closes, then starts a background warm ramp. Darks taken after that ramp
begins are indexed to a temperature the library will never be asked for again -
cover for nothing.

So this is a PHASE, not a rule: it sits between the park and the warm, which is
the only moment the mount is stowed, the cover is shut and the sensor is still
at setpoint. Once the phase exists the wire is honoured, and the rule is
redundant rather than dead - the same reclassification `pool.advance` got.

BOUNDED THREE WAYS, because it runs unattended with nobody awake to stop it: the
queue's quota, what the library is actually short of at each recipe, and the
safety gate between frames.

AND OFF ON EVERY ABORT PATH. `_wind_down` also runs on a rain trip, a safety
abort, a cooling skip and a give-up-after-repeated-crashes. Spending an hour on
calibration in the middle of any of those would be absurd - the rig is being put
away because something is wrong.
"""
from __future__ import annotations

import pytest

import astrodeck.hub as hub_module
from astrodeck.hub import Hub
from astrodeck.sequence import SequenceEngine, SequencePlan
from astrodeck.sequence.models import ExposureStep, Target


@pytest.fixture
async def sim_hub(tmp_path, monkeypatch):
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path)
    monkeypatch.setattr(hub_module.config_store.cfg().safety,
                        "solar_avoidance", False)
    h = Hub()
    await h.connect_sim()
    yield h
    await h.disconnect_all()


def _light(exp=1.0, gain=100, offset=10, binning=1, filt="L") -> ExposureStep:
    return ExposureStep(filter=filt, exposure_s=exp, gain=gain, offset=offset,
                        binning=binning, count=2)


def _plan(targets, day_darks=0) -> SequencePlan:
    return SequencePlan(targets=targets, day_darks=day_darks)


def _engine(hub, plan) -> SequenceEngine:
    e = SequenceEngine(hub)
    e.plan = plan
    return e


class TestTheRecipesMatchTheLights:
    def test_one_recipe_per_distinct_setting(self, sim_hub):
        t = Target(name="M31", ra_hours=0.7, dec_deg=41.0, center=False,
                   autofocus_first=False,
                   steps=[_light(exp=180, filt="Ha"),
                          _light(exp=180, filt="Oiii"),   # same settings, other filter
                          _light(exp=30, filt="L")])
        got = _engine(sim_hub, _plan([t]))._day_dark_recipes()
        assert sorted(r.exposure_s for r in got) == [30.0, 180.0], (
            "a dark is indexed by exposure/gain/offset/binning and NOT by "
            "filter - two narrowband steps at the same settings need one dark "
            "recipe, not two")

    def test_calibration_targets_are_not_covered_by_darks(self, sim_hub):
        cal = Target(name="darks", ra_hours=0.0, dec_deg=0.0, calibration=True,
                     center=False, autofocus_first=False,
                     steps=[ExposureStep(exposure_s=300, gain=100, count=5,
                                         frame_type="Dark")])
        assert _engine(sim_hub, _plan([cal]))._day_dark_recipes() == []

    def test_non_light_steps_are_skipped(self, sim_hub):
        t = Target(name="M31", ra_hours=0.7, dec_deg=41.0, center=False,
                   autofocus_first=False,
                   steps=[_light(exp=60),
                          ExposureStep(exposure_s=0.001, gain=100, count=3,
                                       frame_type="Bias")])
        got = _engine(sim_hub, _plan([t]))._day_dark_recipes()
        assert [r.exposure_s for r in got] == [60.0]


class TestItIsBounded:
    async def test_a_zero_quota_shoots_nothing(self, sim_hub, monkeypatch):
        shot: list[int] = []

        async def _cal(ti, target):
            shot.append(1)

        t = Target(name="M31", ra_hours=0.7, dec_deg=41.0, center=False,
                   autofocus_first=False, steps=[_light()])
        e = _engine(sim_hub, _plan([t], day_darks=0))
        monkeypatch.setattr(e, "_run_calibration", _cal)
        await e._day_darks()
        assert not shot, (
            "a flow that did not ask for day darks had its camera held cold "
            "and its disk filled anyway")

    async def test_the_quota_caps_the_total_across_recipes(self, sim_hub,
                                                           monkeypatch):
        shot: list[float] = []

        async def _cal(ti, target):
            shot.append(target.steps[0].exposure_s)

        t = Target(name="M31", ra_hours=0.7, dec_deg=41.0, center=False,
                   autofocus_first=False,
                   steps=[_light(exp=10), _light(exp=20), _light(exp=30)])
        e = _engine(sim_hub, _plan([t], day_darks=4))
        monkeypatch.setattr(e, "_run_calibration", _cal)
        # library empty: every recipe wants its full share
        monkeypatch.setattr(e, "_hold_darks_shortfall",
                            lambda step, q: (q, 0))
        await e._day_darks()
        assert len(shot) == 4, (
            f"the quota is a TOTAL, not a per-recipe allowance: {shot}")

    async def test_a_full_library_shoots_nothing(self, sim_hub, monkeypatch):
        """The queue's if-stale half. Re-taking darks the rig already owns
        spends a morning and fills a disk for no gain."""
        shot: list[int] = []

        async def _cal(ti, target):
            shot.append(1)

        t = Target(name="M31", ra_hours=0.7, dec_deg=41.0, center=False,
                   autofocus_first=False, steps=[_light()])
        e = _engine(sim_hub, _plan([t], day_darks=20))
        monkeypatch.setattr(e, "_run_calibration", _cal)
        monkeypatch.setattr(e, "_hold_darks_shortfall",
                            lambda step, q: (0, 40))
        await e._day_darks()
        assert not shot

    async def test_a_capture_failure_stops_the_lane_not_the_wind_down(
            self, sim_hub, monkeypatch):
        """These frames are a bonus taken after the night's real work is safely
        in the ledger. A calibration hiccup must never stop the warm ramp, which
        is what actually protects the sensor."""
        async def _boom(ti, target):
            raise RuntimeError("camera fell over")

        t = Target(name="M31", ra_hours=0.7, dec_deg=41.0, center=False,
                   autofocus_first=False, steps=[_light()])
        e = _engine(sim_hub, _plan([t], day_darks=5))
        monkeypatch.setattr(e, "_run_calibration", _boom)
        monkeypatch.setattr(e, "_hold_darks_shortfall", lambda step, q: (q, 0))
        await e._day_darks()          # must not raise


class TestOnlyANormalNightTakesThem:
    async def test_the_wind_down_defaults_to_off(self, sim_hub, monkeypatch):
        """Every existing caller passes three arguments, so the default is what
        the abort, unsafe, quality-stop, cooling-skip and give-up-and-stow paths
        all get. It has to be off."""
        ran: list[int] = []

        async def _dd():
            ran.append(1)

        t = Target(name="M31", ra_hours=0.7, dec_deg=41.0, center=False,
                   autofocus_first=False, steps=[_light()])
        e = _engine(sim_hub, _plan([t], day_darks=20))
        monkeypatch.setattr(e, "_day_darks", _dd)
        await e._wind_down(park=False, warm=False)
        assert not ran, (
            "a wind-down that did not ask for day darks took them - which "
            "means a rain trip now spends an hour on calibration")

    async def test_it_runs_when_asked(self, sim_hub, monkeypatch):
        ran: list[int] = []

        async def _dd():
            ran.append(1)

        t = Target(name="M31", ra_hours=0.7, dec_deg=41.0, center=False,
                   autofocus_first=False, steps=[_light()])
        e = _engine(sim_hub, _plan([t], day_darks=20))
        monkeypatch.setattr(e, "_day_darks", _dd)
        await e._wind_down(park=False, warm=False, day_darks=True)
        assert ran

    async def test_the_darks_come_BEFORE_the_warm(self, sim_hub, monkeypatch):
        """The whole reason this is a phase and not a rule. `warm_camera` starts
        a background ramp and returns, so a dark taken after it is indexed to a
        temperature that will never be asked for again."""
        order: list[str] = []

        async def _dd():
            order.append("darks")

        async def _warm(source=""):
            order.append("warm")
            return {"active": False, "note": "test"}

        t = Target(name="M31", ra_hours=0.7, dec_deg=41.0, center=False,
                   autofocus_first=False, steps=[_light()])
        e = _engine(sim_hub, _plan([t], day_darks=20))
        monkeypatch.setattr(e, "_day_darks", _dd)
        monkeypatch.setattr(sim_hub, "warm_camera", _warm, raising=False)
        await e._wind_down(park=False, warm=True, day_darks=True)
        assert order[:1] == ["darks"], (
            f"the warm ramp started before the darks were taken: {order}")


class TestTheCampaignWireIsNowHonoured:
    def _campaign(self):
        from astrodeck.flows import examples as ex
        from astrodeck.flows.compile import compile_plan
        from astrodeck.flows.to_plan import to_sequence_plan
        rec = [f for f in ex.examples() if f.id == "example-campaign"][0]
        return to_sequence_plan(compile_plan(rec.graph, rec.name), rec.graph)

    def test_the_quota_reaches_the_plan(self):
        plan, _ = self._campaign()
        assert plan.day_darks > 0, (
            "the calibration queue's quota does not reach the day-darks lane, "
            "so the phase runs with nothing to spend")

    def test_the_wire_is_a_note_not_a_loss(self):
        _plan_, un = self._campaign()
        rows = [u for u in un if "on_shutdown_complete" in u["key"]]
        assert rows, "the wire vanished from the report entirely"
        assert rows[0]["level"] == "note", (
            f"still reported as a loss: {rows[0]['detail']}")
        assert "will not run" not in rows[0]["detail"]

    def test_a_queue_NOT_wired_to_shutdown_gets_no_day_darks(self):
        """The lane is keyed on the operator's own wire. A calibration queue fed
        only from a CLOUD WATCH is asking for hold darks, and holding a camera
        cold for an extra hour on a night nobody asked for it is a real cost."""
        from astrodeck.flows.to_plan import plan_extras
        compiled = {"automation": {"calibration_queue": {"quota": 20}},
                    "instructions": [{"when": "on_clouds_in",
                                      "action": "calib", "to_port": "do"}]}
        extras = plan_extras(compiled)
        assert extras.get("cloud_hold_darks") == 20
        assert not extras.get("day_darks"), (
            "a queue wired only to the cloud watch got a day-darks lane it "
            "never asked for")
