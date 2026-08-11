"""#204 — a reconnect dropped the cooler, lost the target, and said nothing.

MEASURED on the rig 2026-08-09. The 08:08 reconnect (issued to recover the dead
mount link, #207) took the camera from cooler-on / target -10.0 °C / sensor
-10.0 °C to cooler OFF / target 0.0 °C / sensor +26.6 °C by 08:39. Nothing
logged the change and nothing restored it — and because the TARGET itself was
reset, even a later "resume cooling" would have aimed at the wrong number, and
any run started afterwards would have written a stale SET-TEMP into every header
(#153, the same failure one layer down).

The passive warm was ~1.2 °C/min, under the 1.72–5.0 °C/min the warm-ramp work
measured, so that particular event carried no thermal-shock risk. The defect is
the silence and the lost setpoint, not the ramp.

A camera cannot be asked what it was doing before it was reopened, so the
operator's intent has to be written down. These tests pin that it is written,
that it is put back, that it is CLEARED when somebody warms (or the run's
wind-down does), and that a failed restore is loud.
"""
from __future__ import annotations

import pytest

import astrodeck.config as config_mod
import astrodeck.hub as hub_module
from astrodeck.config import ConfigStore, CoolingConfig


@pytest.fixture
def store(tmp_path, monkeypatch):
    s = ConfigStore(path=tmp_path / "astrodeck.json")
    monkeypatch.setattr(config_mod, "config_store", s)
    monkeypatch.setattr(hub_module, "config_store", s)
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path / "captures")
    return s


class _Cam:
    """A camera that remembers what it was last told — and, like the real one,
    comes back from a reconnect knowing nothing."""

    name = "fake"
    connected = True
    can_cool = True

    def __init__(self, *, fail: bool = False):
        self.on = False
        self.target = None
        self.calls: list[tuple] = []
        self._fail = fail

    async def set_cooler(self, on, target_c=None):
        self.calls.append((on, target_c))
        if self._fail:
            raise RuntimeError("USB stall")
        self.on = bool(on)
        if on:
            self.target = target_c


class TestTheSetpointIsRecorded:
    @pytest.mark.asyncio
    async def test_cool_camera_writes_the_standing_request(self, store):
        h = hub_module.Hub()
        cam = _Cam()
        h.devices["camera"] = cam
        await h.cool_camera(-10.0)
        assert cam.on is True and cam.target == -10.0
        assert store.cfg().cooling.setpoint_c == -10.0

    @pytest.mark.asyncio
    async def test_it_survives_a_fresh_config_store(self, store, tmp_path):
        """The point of persisting: a PROCESS restart, not just a reconnect."""
        h = hub_module.Hub()
        h.devices["camera"] = _Cam()
        await h.cool_camera(-15.0)
        reloaded = ConfigStore(path=tmp_path / "astrodeck.json")
        assert reloaded.cfg().cooling.setpoint_c == -15.0

    @pytest.mark.asyncio
    async def test_warming_clears_it(self, store):
        """Otherwise the standing request survives the dawn wind-down and a
        reconnect at 08:00 would helpfully re-cool a camera nobody is using."""
        h = hub_module.Hub()
        h.devices["camera"] = _Cam()
        await h.cool_camera(-10.0)
        assert store.cfg().cooling.setpoint_c == -10.0
        await h.warm_camera(source="test", ramp=False)
        assert store.cfg().cooling.setpoint_c is None

    def test_editing_the_warm_policy_does_not_wipe_it(self, store):
        """The Settings panel POSTs the whole CoolingConfig block, and its
        client type has no setpoint field — so a wholesale replace would erase
        the standing request every time somebody changed the warm rate."""
        store.set_cooling_setpoint(-12.0)
        store.set_cooling(CoolingConfig(warm_ramp=False, warm_rate_c_per_min=3.0))
        assert store.cfg().cooling.setpoint_c == -12.0
        assert store.cfg().cooling.warm_rate_c_per_min == 3.0


class TestRestore:
    @pytest.mark.asyncio
    async def test_a_reconnect_puts_the_setpoint_back(self, store, bus_lines):
        h = hub_module.Hub()
        h.devices["camera"] = _Cam()
        await h.cool_camera(-10.0)

        # THE RECONNECT: a brand-new camera object, cooler off, target unset —
        # exactly what the rig came back with at 08:08.
        fresh = _Cam()
        h.devices["camera"] = fresh
        assert fresh.on is False and fresh.target is None

        assert await h.restore_cooling() is True
        assert fresh.on is True and fresh.target == -10.0
        assert any("cooling restored to -10 °C" in m for _l, m, _s in bus_lines)

    @pytest.mark.asyncio
    async def test_nothing_happens_when_nobody_asked_for_cooling(self, store):
        h = hub_module.Hub()
        cam = _Cam()
        h.devices["camera"] = cam
        assert await h.restore_cooling() is False
        assert cam.calls == [], "it cooled a camera nobody asked to cool"

    @pytest.mark.asyncio
    async def test_a_failed_restore_is_LOUD(self, store, bus_lines):
        """The whole original defect was silence. A restore that does not happen
        must be the noisiest outcome of the three, because the alternative is a
        night of warm frames carrying a stale SET-TEMP."""
        h = hub_module.Hub()
        h.devices["camera"] = _Cam()
        await h.cool_camera(-10.0)
        h.devices["camera"] = _Cam(fail=True)
        assert await h.restore_cooling() is False
        errs = [m for lvl, m, _s in bus_lines if lvl == "error"]
        assert errs, "a failed cooling restore said nothing"
        assert "NOT restored" in errs[0] and "SET-TEMP" in errs[0], errs[0]

    @pytest.mark.asyncio
    async def test_a_rig_with_no_camera_is_not_an_error(self, store, bus_lines):
        """Connecting a rig with no camera at all is normal; it must not warn."""
        h = hub_module.Hub()
        store.set_cooling_setpoint(-10.0)
        assert await h.restore_cooling() is False
        assert not [m for lvl, m, _s in bus_lines if lvl == "error"]

    @pytest.mark.asyncio
    async def test_the_sim_connect_path_restores(self, store, bus_lines):
        """Wiring check: restore_cooling is USELESS if no connect path calls it,
        which is how this class of bug survives a fix."""
        import ast
        import inspect
        # `connect_sim` is a lock wrapper around `_connect_sim_unlocked`, so the
        # names here are the functions that actually finish a connect. Named
        # explicitly rather than walked transitively: if one is renamed, this
        # should fail loudly and make someone re-check the wiring, which is the
        # whole point of the assertion.
        for fn in (hub_module.Hub._connect_sim_unlocked,
                   hub_module.Hub._apply_connect_result):
            src = inspect.getsource(fn)
            tree = ast.parse(src.lstrip())
            called = {n.func.attr for n in ast.walk(tree)
                      if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
            assert "restore_cooling" in called, f"{fn.__name__} does not restore cooling"
