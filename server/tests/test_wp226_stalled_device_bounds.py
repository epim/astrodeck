# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""Unbounded device awaits, class of #814/#999/#1009: a rotator read that
never returns must not hang a solve (#1038), and a filter wheel move that never
finishes must not hang the autofocus task (#1039).

MUTATION: remove the ``asyncio.wait_for`` around the reads in
``sky_angle.exposure_context`` (test 1) or around ``fw.set_position`` in the
autofocus route's ``_af`` (test 3). Each test then times out on its own guard.
"""
from __future__ import annotations

import asyncio
import time

import pytest
from fastapi.testclient import TestClient

import astrodeck.api.app as app_module
from astrodeck import sky_angle
from astrodeck.config import ConfigStore
from _simhub import sim_hub  # noqa: F401 (fixture import)


async def _never(*a, **k):
    await asyncio.Event().wait()


async def test_a_stalled_rotator_leaves_the_sky_angle_unknown(sim_hub,
                                                              monkeypatch):
    monkeypatch.setattr(sky_angle, "ROTATOR_READ_TIMEOUT_S", 0.05)
    rot = sim_hub.require("rotator")
    monkeypatch.setattr(rot, "get_mechanical_position", _never)
    monkeypatch.setattr(rot, "is_moving", _never)
    t0 = time.monotonic()
    ctx = await asyncio.wait_for(
        sky_angle.exposure_context(sim_hub, None), 5.0)
    assert time.monotonic() - t0 < 2.0
    assert ctx.mech_deg is None and ctx.moving is None
    why = await asyncio.wait_for(
        sky_angle._calibration_refusal(sim_hub, ctx, rot), 5.0)
    assert why and "not read" in why


async def test_a_rotator_that_stalls_after_the_exposure_refuses_in_words(
        sim_hub, monkeypatch):
    monkeypatch.setattr(sky_angle, "ROTATOR_READ_TIMEOUT_S", 0.05)
    rot = sim_hub.require("rotator")
    ctx = await sky_angle.exposure_context(sim_hub, None)
    assert ctx.mech_deg is not None
    monkeypatch.setattr(rot, "is_moving", _never)
    why = await asyncio.wait_for(
        sky_angle._calibration_refusal(sim_hub, ctx, rot), 5.0)
    assert why and "stalled" in why


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv(app_module.NO_AUTOCONNECT_ENV_VAR, "1")
    store = ConfigStore(path=tmp_path / "astrodeck.json")
    import astrodeck.config as config_mod
    import astrodeck.hub as hub_mod
    from astrodeck.profiles import profiles as profile_lib
    monkeypatch.setattr(config_mod, "config_store", store)
    monkeypatch.setattr(hub_mod, "config_store", store)
    monkeypatch.setattr(app_module, "config_store", store)
    monkeypatch.setattr(hub_mod, "CAPTURE_DIR", tmp_path / "captures")
    monkeypatch.setattr(profile_lib, "_dir", tmp_path / "profiles")
    with TestClient(app_module.create_app()) as c:
        try:
            yield c
        finally:
            try:
                c.post("/api/disconnect")
            except Exception:
                pass


def test_autofocus_with_a_stalled_wheel_fails_with_a_plain_message(
        client, monkeypatch):
    assert client.post("/api/connect/sim").status_code == 200
    import astrodeck.hub as hub_mod
    from astrodeck.events import bus
    fw = hub_mod.hub.devices["filterwheel"]
    monkeypatch.setattr(fw, "set_position", _never)
    monkeypatch.setattr(hub_mod, "SOLVE_WHEEL_MOVE_TIMEOUT_S", 0.1)
    ran = []

    async def fake_run_autofocus(cam, foc, **kw):
        ran.append(1)

    monkeypatch.setattr(app_module, "run_autofocus", fake_run_autofocus)
    seen: list = []
    orig = bus.log

    def spy(level, msg, *a, **k):
        seen.append((level, msg))
        return orig(level, msg, *a, **k)

    monkeypatch.setattr(bus, "log", spy)
    r = client.post("/api/focuser/autofocus", json={"filter": 4})
    assert r.status_code == 200, r.text
    deadline = time.monotonic() + 6
    while time.monotonic() < deadline:
        if any(lv == "error" and "autofocus failed" in m for lv, m in seen):
            break
        client.get("/api/status")
        time.sleep(0.02)
    errs = [m for lv, m in seen if lv == "error" and "autofocus failed" in m]
    assert errs, seen
    assert "stalled" in errs[0] and "0.1 s" in errs[0], errs[0]
    assert not ran
    task = hub_mod.hub._busy.get("autofocus")
    assert task is None or task.done()


def test_manual_filter_move_with_a_stalled_wheel_frees_the_lane(
        client, monkeypatch):
    assert client.post("/api/connect/sim").status_code == 200
    import astrodeck.hub as hub_mod
    from astrodeck.events import bus
    fw = hub_mod.hub.devices["filterwheel"]
    monkeypatch.setattr(fw, "set_position", _never)
    monkeypatch.setattr(hub_mod, "SOLVE_WHEEL_MOVE_TIMEOUT_S", 0.1)
    seen: list = []
    orig = bus.log

    def spy(level, msg, *a, **k):
        seen.append((level, msg))
        return orig(level, msg, *a, **k)

    monkeypatch.setattr(bus, "log", spy)
    r = client.post("/api/filterwheel/position", json={"position": 2})
    assert r.status_code == 200, r.text
    deadline = time.monotonic() + 6
    while time.monotonic() < deadline:
        task = hub_mod.hub._busy.get("filterwheel")
        if task is None or task.done():
            break
        client.get("/api/status")
        time.sleep(0.02)
    task = hub_mod.hub._busy.get("filterwheel")
    assert task is None or task.done(), "the filterwheel lane is still held"
    errs = [m for lv, m in seen if lv == "error" and "stalled" in m]
    assert errs and "0.1 s" in errs[0], seen


async def test_a_rotator_whose_busy_read_stalls_leaves_moving_unknown(
        sim_hub, monkeypatch):
    from astrodeck.events import bus
    monkeypatch.setattr(sky_angle, "ROTATOR_READ_TIMEOUT_S", 0.05)
    rot = sim_hub.require("rotator")
    monkeypatch.setattr(rot, "is_moving", _never)
    seen: list = []
    orig = bus.log

    def spy(level, msg, *a, **k):
        seen.append((level, msg))
        return orig(level, msg, *a, **k)

    monkeypatch.setattr(bus, "log", spy)
    t0 = time.monotonic()
    ctx = await asyncio.wait_for(
        sky_angle.exposure_context(sim_hub, None), 5.0)
    assert time.monotonic() - t0 < 2.0
    assert ctx.mech_deg is not None and ctx.moving is None
    assert [m for lv, m in seen if "stalled" in m].__len__() == 1, seen
