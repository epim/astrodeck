"""Flat auto-exposure must work WITHOUT a flat panel, and must re-meter the sky.

FOUND 2026-08-11 while trying to shoot flats on the real rig. ``imaging/flats.py``
has had a correct ADU-target solver since PRO-5, and the engine drove it — but
the whole metering block sat behind ``"covercalibrator" in self.hub.devices``.
This rig has no panel (the operator shoots through a translucent lens cap), so a
step that asked for ``adu_target`` metering silently shot the plan's fixed
``exposure_s`` and said nothing about it.

That gating is backwards. A panel is a CONSTANT source and barely needs metering;
the sky is what a panel-less rig actually shoots against, and it changes by
roughly a factor of two every few minutes at twilight. So the source that needed
metering most was the only one that could not get it.

Two properties here:
  1. metering runs with no panel present;
  2. a set shot against a CHANGING source re-meters as it goes, rather than
     solving once and letting the last frame be saturated or empty.
"""
from __future__ import annotations

import pytest

import astrodeck.hub as hub_module
from astrodeck.hub import Hub
from astrodeck.sequence.engine import FLAT_RESOLVE_EVERY, SequenceEngine
from astrodeck.sequence.models import ExposureStep, SequencePlan, Target


class _Hub:
    """A hub whose sky BRIGHTENS with every capture, so a single solve cannot
    stay correct — which is the whole point of the re-meter."""

    def __init__(self, *, panel: bool = False, adu_per_s: float = 1000.0,
                 drift: float = 1.0):
        self.devices = {"camera": object()}
        if panel:
            self.devices["covercalibrator"] = object()
        self.adu_per_s = adu_per_s
        self.drift = drift              # multiplier applied per capture
        self.captures: list[dict] = []
        self.site = {"latitude": 40.0, "longitude": -105.0, "is_default": False}

    async def capture(self, exposure_s, gain, offset, binning, *,
                      save=True, frame_type="Light", **kw):
        self.captures.append({"exposure_s": exposure_s, "save": save,
                              "frame_type": frame_type})
        median = exposure_s * self.adu_per_s
        self.adu_per_s *= self.drift
        return {"stats": {"median": median}, "id": len(self.captures)}


def _engine(hub) -> SequenceEngine:
    return SequenceEngine(hub)


def _step(**kw):
    base = dict(filter="L", exposure_s=1.0, gain=125, offset=30, binning=1,
                count=1, frame_type="Flat", adu_target=25000)
    base.update(kw)
    return ExposureStep(**base)


class TestMeteringWithoutAPanel:
    """THE GATE, driven through a REAL run.

    The first version of this file called ``_solve_flat_exposure`` directly and
    passed 5/5 against a deliberately re-gated engine — because the gate is in
    ``_run_calibration`` and the solver was never the thing that was broken.
    Same shape as ``test_calibration_filter_path``'s own opening note: reaching
    the function is the entire question, so these run a plan.
    """

    @pytest.mark.asyncio
    async def test_a_flat_step_with_an_adu_target_METERS_on_a_panelless_rig(
            self, tmp_path, monkeypatch):
        import astrodeck.config as configmod
        monkeypatch.setattr(configmod, "FILTER_CONFIG_FILE",
                            tmp_path / "filter_names.json")
        monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path)

        hub = Hub()
        await hub.connect_sim()
        # The SIM rig ships a covercalibrator; the real one here has none (the
        # operator shoots through a translucent lens cap). Drop it, because a
        # panel is exactly what this test must not have — with one present the
        # old gate passes and proves nothing.
        hub.devices.pop("covercalibrator", None)
        try:
            eng = SequenceEngine(hub)
            seen: list[float] = []
            real = eng._solve_flat_exposure

            async def _spy(step, target, start_exposure_s=None):
                out = await real(step, target, start_exposure_s)
                seen.append(out[0])
                return out
            monkeypatch.setattr(eng, "_solve_flat_exposure", _spy)

            plan = SequencePlan(
                name="F", guide=False, dither_every=0, autofocus_every=0,
                meridian_flip=False,
                targets=[Target(name="F", ra_hours=0.0, dec_deg=0.0,
                                calibration=True, autofocus_first=False,
                                center=False,
                                steps=[ExposureStep(filter="L", exposure_s=0.05,
                                                    count=2, frame_type="Flat",
                                                    adu_target=20000)])])
            eng.start(plan)
            await eng._task
            assert seen, (
                "a Flat step with adu_target > 0 ran WITHOUT metering — the "
                "plan asked for an ADU target and silently got the fixed "
                "exposure instead")
        finally:
            await hub.disconnect_all()

    @pytest.mark.asyncio
    async def test_the_solver_itself_still_converges_and_saves_nothing(self):
        hub = _Hub(panel=False, adu_per_s=1000.0)
        eng = _engine(hub)
        target = Target(name="Flats", ra_hours=0.0, dec_deg=89.9, calibration=True,
                        steps=[_step()])
        exp, converged = await eng._solve_flat_exposure(_step(), target)
        assert converged, "the solver did not converge against a steady source"
        assert abs(exp * 1000.0 - 25000) / 25000 < 0.2, \
            f"solved {exp}s -> {exp * 1000:.0f} ADU, wanted ~25000"
        assert hub.captures, "no trial capture was taken"
        assert all(c["save"] is False for c in hub.captures), \
            "a metering trial was written to disk"

    @pytest.mark.asyncio
    async def test_a_reseolve_starts_from_the_last_answer(self):
        """A re-meter mid-set has a very good guess available — the previous
        solution. Starting from the plan's original number instead would re-walk
        the whole ladder every few frames and spend the twilight window
        metering."""
        hub = _Hub(panel=False, adu_per_s=1000.0)
        eng = _engine(hub)
        target = Target(name="Flats", ra_hours=0.0, dec_deg=89.9, calibration=True,
                        steps=[_step()])
        # step.exposure_s is a deliberately terrible 1 s guess; 25 s is right.
        await eng._solve_flat_exposure(_step(exposure_s=1.0), target)
        cold = len(hub.captures)

        hub.captures.clear()
        await eng._solve_flat_exposure(_step(exposure_s=1.0), target,
                                       start_exposure_s=25.0)
        warm = len(hub.captures)
        assert warm <= cold, (
            f"a seeded re-solve took {warm} trials vs {cold} cold — the seed is "
            f"not being used")


class TestTheSkyMoves:
    def test_the_resolve_cadence_is_stated_not_implied(self):
        assert FLAT_RESOLVE_EVERY >= 1
        # A cadence longer than a typical flat set would never fire, which is
        # the bound-that-cannot-fire shape.
        assert FLAT_RESOLVE_EVERY <= 10, \
            "a set of 20 flats must re-meter at least twice"

    @pytest.mark.asyncio
    async def test_a_single_solve_would_drift_out_of_band(self):
        """The measurement that JUSTIFIES the re-meter. If a solve held good
        across a set, re-metering would be waste — so pin that it does not.

        1.35x per frame is roughly twilight: about a factor of two every couple
        of frames at this cadence."""
        hub = _Hub(panel=False, adu_per_s=1000.0, drift=1.35)
        eng = _engine(hub)
        target = Target(name="Flats", ra_hours=0.0, dec_deg=89.9, calibration=True,
                        steps=[_step()])
        exp, _ = await eng._solve_flat_exposure(_step(), target)
        # Five frames later the same exposure lands somewhere else entirely.
        later_adu = exp * hub.adu_per_s
        assert later_adu > 25000 * 1.5, (
            f"the source barely moved ({later_adu:.0f} ADU vs 25000) — this "
            f"fixture no longer models a changing sky, so the re-meter tests "
            f"below prove nothing")


class TestPanelCommandsStayGated:
    def test_the_panel_commands_are_still_behind_a_panel(self):
        """Ungating the METERING must not have ungated the panel COMMANDS —
        calibrator_on against a rig with no calibrator would raise."""
        import ast
        import inspect
        src = inspect.getsource(SequenceEngine._run_calibration)
        tree = ast.parse(src.lstrip())
        # Find the `if` whose body calls calibrator_on; its test must mention
        # covercalibrator.
        guarded = False
        for node in ast.walk(tree):
            if not isinstance(node, ast.If):
                continue
            calls = {n.func.attr for n in ast.walk(node)
                     if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
            if "calibrator_on" in calls:
                if "covercalibrator" in ast.unparse(node.test):
                    guarded = True
        assert guarded, "calibrator_on is no longer gated on a panel existing"
