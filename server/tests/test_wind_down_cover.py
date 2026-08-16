"""A flat panel that fails must not take the dust cover down with it.

`_panel_off_safe` had ONE try around TWO independent duties, and the lamp went
first:

    await calibrator_off()      # raises when the device's link is down
    if has_cover: close_cover()  # never reached

so a covercalibrator that was present but disconnected ended the night with the
telescope open to the sky, and the only trace was one line reading "panel-off
failed" - which sounds like the light might still be on, not like the tube is
uncovered until someone walks outside.

`hub.require` is what raises here: a device present in `hub.devices` but not
connected is exactly the state a dropped USB link leaves behind, and this rig
has form - two drivers have already been caught reporting `connected` from
memory rather than from a measurement.

Closing the cover does not depend on the lamp having answered. Two duties, two
tries.
"""
from __future__ import annotations

import pytest

import astrodeck.hub as hub_module
from astrodeck.hub import Hub
from astrodeck.sequence import SequenceEngine


@pytest.fixture
async def sim_hub(tmp_path, monkeypatch):
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path)
    monkeypatch.setattr(hub_module.config_store.cfg().safety,
                        "solar_avoidance", False)
    h = Hub()
    await h.connect_sim()
    yield h
    await h.disconnect_all()


class TestTheCoverClosesEvenWhenTheLampDoesNot:
    async def test_a_failing_calibrator_off_does_not_skip_the_cover(
            self, sim_hub, monkeypatch):
        """The regression. Before this the cover close sat in the same try as
        the lamp command, so any lamp failure left the tube open."""
        if "covercalibrator" not in sim_hub.devices:
            pytest.skip("the sim rig has no covercalibrator to exercise")
        closed: list[bool] = []

        async def _boom():
            raise RuntimeError("covercalibrator: link is down")

        async def _close():
            closed.append(True)

        monkeypatch.setattr(sim_hub, "calibrator_off", _boom)
        monkeypatch.setattr(sim_hub, "close_cover", _close)
        monkeypatch.setattr(type(sim_hub.calibrator), "has_cover", True,
                            raising=False)

        await SequenceEngine(sim_hub)._panel_off_safe()
        assert closed, (
            "the lamp command failed and took the cover close with it - the "
            "telescope is left open to the sky for the rest of the night")

    async def test_a_failing_cover_close_is_logged_as_a_COVER_problem(
            self, sim_hub, monkeypatch):
        """The message is the whole value of the line. "panel-off failed" tells
        an operator reading a log at breakfast that a lamp misbehaved; it has to
        say the tube is open, because nothing else in the run will close it."""
        if "covercalibrator" not in sim_hub.devices:
            pytest.skip("the sim rig has no covercalibrator to exercise")
        lines: list[tuple[str, str]] = []
        import astrodeck.sequence.engine as engine_mod
        monkeypatch.setattr(engine_mod.bus, "log",
                            lambda lvl, msg, src=None: lines.append((lvl, msg)))

        async def _ok():
            return None

        async def _boom():
            raise RuntimeError("shutter jammed")

        monkeypatch.setattr(sim_hub, "calibrator_off", _ok)
        monkeypatch.setattr(sim_hub, "close_cover", _boom)
        monkeypatch.setattr(type(sim_hub.calibrator), "has_cover", True,
                            raising=False)

        await SequenceEngine(sim_hub)._panel_off_safe()
        hits = [m for lvl, m in lines if "COVER NOT CLOSED" in m]
        assert hits, f"no line names the open cover: {lines}"
        assert any(lvl == "error" for lvl, m in lines if "COVER NOT CLOSED" in m), (
            "an open tube is logged below error level, so it will not reach the "
            "alert sinks that page an operator")

    async def test_the_happy_path_still_does_both(self, sim_hub, monkeypatch):
        if "covercalibrator" not in sim_hub.devices:
            pytest.skip("the sim rig has no covercalibrator to exercise")
        did: list[str] = []

        async def _off():
            did.append("lamp")

        async def _close():
            did.append("cover")

        monkeypatch.setattr(sim_hub, "calibrator_off", _off)
        monkeypatch.setattr(sim_hub, "close_cover", _close)
        monkeypatch.setattr(type(sim_hub.calibrator), "has_cover", True,
                            raising=False)

        await SequenceEngine(sim_hub)._panel_off_safe()
        assert did == ["lamp", "cover"], (
            f"the lamp must go out before the cover shuts over it: {did}")

    async def test_a_rig_with_no_cover_is_untouched(self, sim_hub, monkeypatch):
        """`has_cover` False is a real configuration - a bare flat panel with no
        motorised lid - and calling close_cover on one would be an error."""
        if "covercalibrator" not in sim_hub.devices:
            pytest.skip("the sim rig has no covercalibrator to exercise")
        closed: list[bool] = []

        async def _ok():
            return None

        async def _close():
            closed.append(True)

        monkeypatch.setattr(sim_hub, "calibrator_off", _ok)
        monkeypatch.setattr(sim_hub, "close_cover", _close)
        monkeypatch.setattr(type(sim_hub.calibrator), "has_cover", False,
                            raising=False)

        await SequenceEngine(sim_hub)._panel_off_safe()
        assert not closed, "a panel with no lid was asked to close one"
