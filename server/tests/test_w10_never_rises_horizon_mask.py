# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""#619 (a), leg 3: the plan-wide 'never rises' pre-flight warning
(``POST /api/sequence/preflight``) must ask ``schedule.effective_floor``,
not a single flat scalar, so a target hidden behind a drawn horizon-mask
obstruction for its WHOLE window is reported as never rising even when its
TRUE (unmasked) peak altitude clears the flat floor.

Before this, the route compared the window's peak altitude against
``max(per-target gate, site.horizon_min_deg, cfg.safety.min_alt_deg)`` --
a flat number that ignores ``cfg.safety.horizon`` and
``cfg.safety.nogo_box`` entirely, which are both azimuth-dependent by
design (``schedule.effective_floor``). A target that only ever occupies
azimuths the obstruction mask raises above its real peak passed this gate
clean -- the never-rises warning never fired, and the operator found out
only once the run tried to slew there.

THE MASK IS A SINGLE-POINT HORIZON PROFILE ON PURPOSE.
``schedule.interp_wrap`` (the function ``effective_floor`` calls) returns
that one point's altitude for EVERY azimuth when the profile has exactly
one point -- so this test needs no azimuth tracking at all: the floor is
simply flat at 50 deg everywhere, and the target's real peak (~40 deg,
self-checked below against the unmodified ``schedule.target_max_altitude``)
sits cleanly under it regardless of which azimuth the peak falls at.

THE FULL-DAY SCAN MAKES THIS TIME-OF-DAY-INDEPENDENT. The target's
``Schedule`` is left at its default (``start_mode="now"``,
``stop_mode="none"``), which resolves to an OPEN window; both the old
scalar scan and the new per-sample one then cover a full sidereal day from
"now" regardless of what real wall-clock instant the test happens to run
at, so the result does not depend on, or need to mock, the real clock.

SITE is a made-up fixture (project site-privacy rule).
"""
from __future__ import annotations

import pytest

import astrodeck.api.app as app_module
from astrodeck.config import ConfigStore, SafetyConfig, Site
from astrodeck.sequence import schedule as schedule_mod

SITE = Site(name="w10-never-rises-fixture", latitude=42.0, longitude=-71.0,
           is_default=False, horizon_min_deg=0.0)

#: dec chosen so the target's TRUE (unmasked) transit peak is ~40 deg at
#: SITE's latitude (peak = 90 - |lat - dec|); self-checked below against
#: the unmodified ``schedule.target_max_altitude`` rather than hand-trusted.
TARGET_DEC = SITE.latitude - 50.0
TARGET_RA = 5.0     # arbitrary -- the single-point mask is azimuth-blind


@pytest.fixture
def client(tmp_path, monkeypatch):
    temp_store = ConfigStore(path=tmp_path / "astrodeck.json")
    import astrodeck.config as config_mod
    import astrodeck.hub as hub_mod
    monkeypatch.setattr(config_mod, "config_store", temp_store)
    monkeypatch.setattr(hub_mod, "config_store", temp_store)
    monkeypatch.setattr(app_module, "config_store", temp_store)

    from astrodeck.plans import PlanLibrary
    from astrodeck.profiles import ProfileLibrary
    monkeypatch.setattr(app_module, "plan_library",
                        PlanLibrary(directory=tmp_path / "plans"))
    monkeypatch.setattr(app_module, "profiles",
                        ProfileLibrary(directory=tmp_path / "profiles"))

    from fastapi.testclient import TestClient
    app = app_module.create_app()
    with TestClient(app) as c:
        yield c, temp_store


def _light_plan(name: str = "masked") -> dict:
    return {
        "name": name,
        "targets": [{
            "name": "behind-the-mask",
            "ra_hours": TARGET_RA, "dec_deg": TARGET_DEC,
            "steps": [{"exposure_s": 1.0, "count": 1}],
        }],
    }


def _never_rises_warnings(body: dict) -> list[dict]:
    return [w for w in body["warnings"] if w["kind"] == "never_rises"]


def test_true_peak_is_the_expected_forty_degrees():
    """Self-check (computed, not guessed): confirms the geometry this whole
    file depends on, against the unmodified ``target_max_altitude``, before
    either real assertion below runs."""
    peak = schedule_mod.target_max_altitude(
        TARGET_RA, TARGET_DEC, SITE.latitude, SITE.longitude, None, None)
    assert 38.0 <= peak <= 42.0, (
        f"self-check failed: expected a ~40 deg true peak, got {peak:.1f}")


def test_a_target_hidden_behind_the_mask_all_window_now_warns(client):
    """The defect itself: peak 40 deg clears the flat floor (0 deg) but
    never clears the 50-deg mask at any azimuth, so the old scalar
    comparison (peak vs. flat floor alone) missed it entirely.

    MUTANT (verified): in ``_never_rises_scan``, ``if alt >= schedule_mod.
    effective_floor(floor_base, horizon, az, nogo_box):`` replaced by
    ``if alt >= floor_base:`` (the horizon/no-go mask dropped from the
    per-sample comparison, leaving only the flat base) turns this red
    with, verbatim:
        AssertionError: expected one never_rises warning, got [] in
        [{'target': '', 'kind': 'no_safety_source', 'blocking': False,
        'message': 'safety is armed but no safety monitor is assigned...
        nothing will watch the weather for this run'}]
        assert 0 == 1
    """
    c, store = client
    store.set_site(SITE, expected_version=None)
    store.set_safety(SafetyConfig(horizon=[(0.0, 50.0)]))

    r = c.post("/api/sequence/preflight", json=_light_plan())
    assert r.status_code == 200, r.text
    body = r.json()
    rises = _never_rises_warnings(body)
    assert len(rises) == 1, (
        f"expected one never_rises warning, got {rises} in {body['warnings']}")
    assert rises[0]["target"] == "behind-the-mask"
    # The message must not claim the target "never rises above 0 deg" --
    # it demonstrably does (peak ~40) -- so it must name the mask instead.
    assert "mask" in rises[0]["message"] or "wedge" in rises[0]["message"], (
        rises[0]["message"])


def test_the_same_target_with_no_mask_configured_does_not_warn(client):
    """Control: identical target and site, but no horizon mask and no
    no-go wedge -- the flat floor is 0, the true peak is ~40, so this must
    NOT warn. Proves the fix did not make the check over-eager."""
    c, store = client
    store.set_site(SITE, expected_version=None)
    store.set_safety(SafetyConfig())   # horizon=None, nogo_box=None, floor 0

    r = c.post("/api/sequence/preflight", json=_light_plan())
    assert r.status_code == 200, r.text
    assert _never_rises_warnings(r.json()) == []


def test_a_target_below_the_flat_floor_still_warns_with_the_old_wording(client):
    """Control: the PRE-EXISTING case (no mask involved at all, the flat
    floor alone already catches it) must keep its exact old message, so
    this refactor does not change behaviour nobody asked to change."""
    c, store = client
    store.set_site(SITE, expected_version=None)
    store.set_safety(SafetyConfig(min_alt_deg=80.0))   # well above the ~40 peak

    r = c.post("/api/sequence/preflight", json=_light_plan())
    assert r.status_code == 200, r.text
    rises = _never_rises_warnings(r.json())
    assert len(rises) == 1, rises
    assert rises[0]["message"] == (
        "behind-the-mask never rises above 80 deg during its window "
        "(peaks at 40 deg)"), rises[0]["message"]
