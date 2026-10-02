# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""WP-22: (a) hub._check_horizon reads cfg.safety.horizon (issue #132; plan
docs/superpowers/plans/2026-09-30-open-issue-backlog.md, WP-22 row). (b) For
WP-21: goto_and_center's single solve_transient key splits into
rotation_solve_transient / centring_solve_transient, with solve_transient kept
as their union for compatibility.

(a) THE GAP. ``hub._check_horizon`` checked only ``alt < 0`` (the true,
geometric horizon) and never read ``cfg.safety.horizon`` (the drawn
obstruction-mask polyline) or ``cfg.safety.nogo_box`` (hard-edged wedges), so
a target sitting above the true horizon but below the configured obstruction
floor read "fine" at every REST caller that routes through it
(``_horizon_block`` / ``_start_preflight`` / the goto and nudge routes) and
the UI's advisory verdict (``_preflight_alt``), and then hit the engine's
mid-run slew gate (``_altitude_limit_verdict``, which already used
``schedule.effective_floor``) on the very first slew. Now ``_check_horizon``
computes the SAME effective floor the engine's ``_mount_floor_verdict``
enforces, so a single-target start can no longer pass this gate only to be
refused mid-run.

A DEC-90 TARGET IS THE FIXTURE. At any latitude L, a target at dec=90 sits at
altitude L for EVERY right ascension and EVERY time of day: the spherical
triangle degenerates (the hour angle term drops out, see
astrodeck.catalog.coords.altaz), so these tests need no wall-clock pinning. A
single-point horizon profile, or an az_min==az_max no-go wedge, applies to
every azimuth (schedule.interp_wrap / schedule.nogo_floor), so the fixture
also does not depend on the azimuth altaz happens to compute at the pole.

(b) THE GAP. A rotate-phase hold and a centring-phase hold are different
failures (a rotator problem vs. a plain re-slew), but both set the same
``solve_transient`` flag, so a caller that wanted to know which phase held a
file could not. ``goto_and_center`` now carries both ``rotation_solve_
transient`` and ``centring_solve_transient``, with the old key kept as their
union for compatibility (``_group_hop_checks`` in engine.py, WP-21's file,
still reads only the old key until WP-21 adds the new one with a fallback).

MUTATIONS were run from a byte backup of the two edited files inside this
worktree (never the shared tree), restored and sha256-compared after each,
and are recorded verbatim on the work-package return.
"""
from __future__ import annotations

import asyncio
import errno
from pathlib import Path

import pytest

from fastapi.testclient import TestClient

import astrodeck.api.app as app_module
import astrodeck.hub as hub_module
from astrodeck.config import ConfigStore, Site
from astrodeck.devices.base import DeviceError
from astrodeck.hub import Hub

from _simhub import sim_hub  # noqa: F401 (fixture import)

#: A made-up site (never the real rig's -- project rule). At this latitude a
#: dec=90 target sits at exactly this altitude for any RA and any time (see
#: the module docstring).
FIXTURE_LAT, FIXTURE_LON = 20.0, 0.0
#: RA is irrelevant at dec=90 (the pole); altitude == FIXTURE_LAT, always.
POLE_RA, POLE_DEC = 0.0, 90.0
#: The south pole: altitude == -FIXTURE_LAT, always below the true horizon.
SOUTH_POLE_RA, SOUTH_POLE_DEC = 0.0, -90.0


def _hub(tmp_path, monkeypatch) -> tuple[Hub, ConfigStore]:
    """A bare Hub on an isolated ConfigStore at the fixture site (mirrors
    test_sun_guard.py's harness, _make_client/Hub() pattern)."""
    store = ConfigStore(path=tmp_path / "astrodeck.json")
    monkeypatch.setattr(hub_module, "config_store", store)
    store.set_site(Site(name="Fixture", latitude=FIXTURE_LAT,
                        longitude=FIXTURE_LON))
    return Hub(), store


def _set_safety(store: ConfigStore, **fields) -> None:
    store.set_safety(store.cfg().safety.model_copy(update=fields))


# ====================================================== (a) hub._check_horizon

def test_default_config_still_only_blocks_the_true_horizon(tmp_path, monkeypatch):
    """CONTROL: no horizon/nogo/min_alt configured (every rig before this
    WP). The pole target (alt == FIXTURE_LAT, always > 0) is never blocked --
    pins the no-op case so an un-configured rig cannot regress."""
    h, _store = _hub(tmp_path, monkeypatch)
    h._check_horizon(POLE_RA, POLE_DEC)  # no raise


def test_true_horizon_message_is_unchanged(tmp_path, monkeypatch):
    """CONTROL: alt < 0 still raises the exact old sentence. Both
    test_group_start_guard.py and test_resume_recover_preflight.py match it
    with ``str.startswith("target is below the visible horizon")``, so the
    fix must not reword it."""
    h, _store = _hub(tmp_path, monkeypatch)
    with pytest.raises(DeviceError,
                       match=r"^target is below the visible horizon"):
        h._check_horizon(SOUTH_POLE_RA, SOUTH_POLE_DEC)


def test_an_obstruction_mask_blocks_a_target_above_the_true_horizon(
        tmp_path, monkeypatch):
    """THE BUG (#132 a). A single-point horizon profile is a flat floor at
    every azimuth (schedule.interp_wrap): 30 degrees, drawn around a tree
    line. The pole target sits at alt 20 (FIXTURE_LAT) -- above the true
    horizon, below the drawn floor. Before the fix, ``_check_horizon`` never
    read ``cfg.safety.horizon`` at all, so nothing raised here.

    Named mutant "the obstruction check is dropped" (the whole second
    ``if alt < floor`` block removed from ``_check_horizon``): RED, observed
        Failed: DID NOT RAISE <class 'astrodeck.devices.base.DeviceError'>
    """
    h, store = _hub(tmp_path, monkeypatch)
    _set_safety(store, horizon=[(0.0, 30.0)])
    with pytest.raises(DeviceError, match=r"below the obstruction horizon"):
        h._check_horizon(POLE_RA, POLE_DEC)


def test_a_target_above_the_mask_is_not_blocked(tmp_path, monkeypatch):
    """CONTROL for the above: the same pole target (alt 20) with a LOWER
    drawn floor (5 degrees) clears it -- the fix is directional, not "block
    everything once a mask exists"."""
    h, store = _hub(tmp_path, monkeypatch)
    _set_safety(store, horizon=[(0.0, 5.0)])
    h._check_horizon(POLE_RA, POLE_DEC)  # no raise


def test_a_nogo_wedge_blocks_it_too(tmp_path, monkeypatch):
    """The second ingredient of the effective floor: a hard-edged no-go
    wedge (a pier, a wall), not a drawn horizon line. ``az_min == az_max``
    (both 0.0) is the documented full-circle wedge (schedule.nogo_floor), so
    this does not depend on the azimuth altaz computes at the pole.

    Named mutant "nogo_box dropped from the call" (``safety.nogo_box``
    replaced by ``None`` in the ``effective_floor`` call): RED, observed
        Failed: DID NOT RAISE <class 'astrodeck.devices.base.DeviceError'>
    """
    h, store = _hub(tmp_path, monkeypatch)
    _set_safety(store, nogo_box=[{"az_min": 0.0, "az_max": 0.0,
                                  "alt_max": 30.0}])
    with pytest.raises(DeviceError, match=r"below the obstruction horizon"):
        h._check_horizon(POLE_RA, POLE_DEC)


def test_force_waives_the_obstruction_floor_too(tmp_path, monkeypatch):
    """``force`` already waived the true-horizon check; it must waive the
    new obstruction-floor check the same way, not just the old one.

    Named mutant "the obstruction check ignores force" (``and not force``
    dropped from the second ``if``): RED, observed (this case asserts NO
    raise, so the mutant fails it by raising where the test expects
    silence)
        astrodeck.devices.base.DeviceError: target is below the obstruction
        horizon (alt 20°, floor 30° at az 0°)
    """
    h, store = _hub(tmp_path, monkeypatch)
    _set_safety(store, horizon=[(0.0, 30.0)])
    h._check_horizon(POLE_RA, POLE_DEC, force=True)  # no raise


def test_default_site_is_still_inert(tmp_path, monkeypatch):
    """CONTROL: a default (never-configured) site is inert even with a mask
    configured -- an un-set location must not be trusted to refuse a slew
    (the existing rule, unchanged by this fix)."""
    store = ConfigStore(path=tmp_path / "astrodeck.json")
    monkeypatch.setattr(hub_module, "config_store", store)
    assert store.cfg().site.is_default is True
    _set_safety(store, horizon=[(0.0, 89.0)])
    h = Hub()
    h._check_horizon(POLE_RA, POLE_DEC)  # no raise: default site


# ============================================ (a) app.py: _preflight_alt / start

@pytest.fixture
def client(tmp_path, monkeypatch):
    """Isolated app (mirrors test_app_preflight.py's fixture): config +
    plan/profile libraries redirected to tmp, no real rig touched."""
    temp_store = ConfigStore(path=tmp_path / "astrodeck.json")
    import astrodeck.config as config_mod
    monkeypatch.setattr(config_mod, "config_store", temp_store)
    monkeypatch.setattr(hub_module, "config_store", temp_store)
    monkeypatch.setattr(app_module, "config_store", temp_store)

    from astrodeck.plans import PlanLibrary
    from astrodeck.profiles import ProfileLibrary
    monkeypatch.setattr(app_module, "plan_library",
                        PlanLibrary(directory=tmp_path / "plans"))
    monkeypatch.setattr(app_module, "profiles",
                        ProfileLibrary(directory=tmp_path / "profiles"))

    app = app_module.create_app()
    with TestClient(app) as c:
        yield c, temp_store


def test_the_horizon_light_agrees_with_the_obstruction_mask(client):
    """THE UI'S HORIZON LIGHT AGREES WITH IT (#132 a). The GET single-target
    verdict (what the UI's Horizon light reads, ``_preflight_alt``) is "low"
    for the pole target (alt 20) once a 30-degree mask is drawn, and "ok"
    once the mask is lowered to 5 -- the same effective floor
    ``hub._check_horizon`` now blocks on, read through ``config_store``
    rather than a second, independent computation.

    Named mutant "_preflight_alt keeps the flat site floor" (the
    ``effective_floor`` call reverted to ``site.get("horizon_min_deg",
    15.0)``): RED on the first assertion, observed
        AssertionError: assert 'ok' == 'low'
    (alt 20 clears the onboarding default of 15, so the old flat check never
    sees the drawn 30-degree mask at all).
    """
    c, store = client
    store.set_site(Site(name="Fixture", latitude=FIXTURE_LAT,
                        longitude=FIXTURE_LON))
    _set_safety(store, horizon=[(0.0, 30.0)])
    body = c.get("/api/sequence/preflight",
                 params={"ra_hours": POLE_RA, "dec_deg": POLE_DEC}).json()
    assert body["verdict"] == "low", body
    assert body["horizon_min_deg"] == pytest.approx(30.0), body

    _set_safety(store, horizon=[(0.0, 5.0)])
    body = c.get("/api/sequence/preflight",
                 params={"ra_hours": POLE_RA, "dec_deg": POLE_DEC}).json()
    assert body["verdict"] == "ok", body
    assert body["horizon_min_deg"] == pytest.approx(5.0), body


def test_a_single_target_start_is_refused_for_a_masked_target(client, monkeypatch):
    """``_start_preflight`` gives a single (non-mosaic) target the same
    verdict the engine's mid-run floor would (#132 a): the pole target (alt
    20, above the true horizon) behind a 30-degree mask now 409s the start,
    through the same ``_horizon_block`` -> ``hub._check_horizon`` path the
    goto/nudge routes already used. ``force`` still waives it, as it always
    waived the true-horizon check.

    Named mutant "the obstruction check is dropped" (same as hub.py's test
    above, exercised end to end through the start route): RED, observed
        AssertionError: {"started":true,"frames":1}
        assert 200 == 409
    """
    c, store = client
    store.set_site(Site(name="Fixture", latitude=FIXTURE_LAT,
                        longitude=FIXTURE_LON))
    _set_safety(store, horizon=[(0.0, 30.0)])
    monkeypatch.setattr(app_module.hub, "require", lambda role: object())
    started = {"n": 0}
    monkeypatch.setattr(
        app_module.engine, "start",
        lambda plan, **kw: started.__setitem__("n", started["n"] + 1))
    plan = {"name": "masked", "targets": [{
        "name": "Behind the mask", "ra_hours": POLE_RA, "dec_deg": POLE_DEC,
        "steps": [{"exposure_s": 1.0, "count": 1}]}]}

    r = c.post("/api/sequence/start", json=plan)
    assert r.status_code == 409, r.text
    assert r.json()["detail"]["code"] == "below_horizon"
    assert started["n"] == 0

    r = c.post("/api/sequence/start", json={**plan, "force": True})
    assert r.status_code == 200, r.text
    assert started["n"] == 1


# ========================================================== (b) solve_transient

RA, DEC = 5.0, 10.0


# NOT autouse: this file also carries (a)'s plain hub-unit and app-route
# tests, which build their OWN isolated ConfigStore at the same per-test
# ``tmp_path``. An autouse sim_hub would write _simhub.py's _TEST_SITE to
# that shared tmp_path's astrodeck.json before those tests' bodies run their
# own ConfigStore(path=tmp_path/"astrodeck.json") -- which then loads THAT
# site instead of a fresh, unconfigured one. Each (b) test below asks for
# ``sim_hub`` explicitly and calls this itself.
def _on_target(sim_hub) -> None:
    sim_hub.sim_rig.ra_hours = RA
    sim_hub.sim_rig.dec_deg = DEC


@pytest.fixture
def quick_backoff(monkeypatch):
    """A backoff this suite can afford (mirrors test_h4_solve_frame_unique_
    names.py's fixture of the same name): 13 ms instead of the real 0.2 s."""
    monkeypatch.setattr(hub_module, "SOLVE_WRITE_BACKOFF_S", 0.013)


def _sharing_violation(path) -> PermissionError:
    """What Windows raises when another process holds ``path`` (mirrors
    test_h4_solve_frame_unique_names.py's helper of the same name)."""
    e = PermissionError(errno.EACCES, "The process cannot access the file "
                        "because it is being used by another process",
                        str(path))
    e.winerror = 32
    return e


def _failing_writes(monkeypatch, fail):
    """``hub.save_fits`` raising ``fail(path)`` for each path it returns an
    exception for (``None`` writes the file for real)."""
    real = hub_module.save_fits

    def save_fits(frame, path, **kw):
        err = fail(Path(path))
        if err is not None:
            raise err
        return real(frame, path, **kw)

    monkeypatch.setattr(hub_module, "save_fits", save_fits)


async def test_a_rotate_transient_sets_rotation_key_not_centring(
        sim_hub, monkeypatch, quick_backoff):
    """Only the rotate frame is held: the rotate loop exhausts its retries
    and degrades (``rotation_skipped``), the centring solve that follows
    writes cleanly, so this is a ROTATION-phase transient only.

    Named mutant "the split is reverted" (``rotation_solve_transient``
    renamed back to ``solve_transient`` everywhere, as it was before #132 b):
    RED, observed
        AssertionError: assert None is True
         +  where None = <built-in method get of dict object at ...>('rotation_solve_transient')
    """
    _on_target(sim_hub)
    rig = sim_hub.sim_rig
    rig.rotator_pa_offset_deg, rig.rotator_mech_deg = 20.0, 10.0
    _failing_writes(monkeypatch, lambda p: _sharing_violation(p)
                    if p.name.startswith("rotate-") else None)

    result = await sim_hub.goto_and_center(RA, DEC, rotation_deg=45.0)

    assert result["centered"] is True, result
    assert result.get("rotation_skipped") is True, result
    assert result.get("rotation_solve_transient") is True, result
    assert result.get("solve_transient") is True, result  # the union, kept
    assert "centring_solve_transient" not in result, result


async def test_a_centring_transient_sets_centring_key_not_rotation(
        sim_hub, monkeypatch, quick_backoff):
    """The mirror case: every (plain, un-rotated) centring solve held, so
    this is a CENTRING-phase transient only -- no rotation was ever asked
    for, so ``rotation_solve_transient`` must not appear.

    Named mutant "centring_solve_transient key dropped" (the merge at the
    solve-failed return reverted to the old bare
    ``{"solve_transient": True}``): RED, observed
        AssertionError: assert None is True
         +  where None = <built-in method get of dict object at ...>('centring_solve_transient')
    """
    _on_target(sim_hub)
    _failing_writes(monkeypatch, _sharing_violation)

    result = await sim_hub.goto_and_center(RA, DEC)

    assert result["centered"] is False and result["solve_failed"] is True, result
    assert result.get("centring_solve_transient") is True, result
    assert result.get("solve_transient") is True, result  # the union, kept
    assert "rotation_solve_transient" not in result, result


async def test_both_phases_transient_the_old_key_is_their_union(
        sim_hub, monkeypatch, quick_backoff):
    """Both phases held on every try: the rotate loop degrades transiently
    AND the centring solve that follows also exhausts transiently. Both new
    keys are True, and the kept ``solve_transient`` is their union (true
    because EITHER is true, not because the two were merged back into one
    flag) -- the shape the plan's split asks a test to pin directly ("a test
    sets each key by its own failure and checks the union").

    Named mutant "the union drops the rotation half" (the centring-phase
    return's merge changed from ``| _rot_keys | {...}`` to ``| {...}``
    alone, so a prior rotate-phase hold is lost off this return): RED,
    observed
        AssertionError: assert None is True
         +  where None = <built-in method get of dict object at ...>('rotation_solve_transient')
    """
    _on_target(sim_hub)
    rig = sim_hub.sim_rig
    rig.rotator_pa_offset_deg, rig.rotator_mech_deg = 20.0, 10.0
    _failing_writes(monkeypatch, lambda p: _sharing_violation(p)
                    if p.name.startswith(("rotate-", "solve-")) else None)

    result = await sim_hub.goto_and_center(RA, DEC, rotation_deg=45.0)

    assert result["centered"] is False and result["solve_failed"] is True, result
    assert result.get("rotation_skipped") is True, result
    assert result.get("rotation_solve_transient") is True, result
    assert result.get("centring_solve_transient") is True, result
    assert result.get("solve_transient") is True, result
