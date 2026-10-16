# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""#1007: a NaN altitude floor or no-go wedge refuses nothing, so every door
into them shares one rule and the floor never reads a NaN as "no obstruction".

``SafetyConfig.min_alt_deg`` was a bare ``float`` and ``nogo_box`` a free
``list[dict]``. A config.json holding a NaN in either loaded; the same NaN
validated through ``POST /api/config``; and the floor then compared
``alt < nan``, which is False at every altitude. ``schedule.nogo_floor`` skipped
a wedge whose ``alt_max`` was NaN (``max(floor, nan)`` keeps its first argument)
and ``effective_floor`` returned the NaN itself. Both gates (``hub._check_horizon``
and the engine's ``_altitude_limit_verdict``) therefore let the target in: a
false OPEN on the guard that stops the mount driving into its own pier. Same
class as #982, which closed it for the drawn horizon.

Every case is graded on BEHAVIOUR: what the model accepts, what the route
answers and leaves stored, what a real config.json loads as and says, what the
floor is, and whether the real gates refuse a target. Bad values are written to
a real config.json as the bare ``NaN`` literal a hand edit or an old build
leaves, never built in memory. Numbers that appear in no rule text (987.25,
731.5) mark a stored value, so a line that echoes one is caught.

Repo convention (mirrors tests/test_982_stored_horizon_repaired_on_load.py):
in-process fakes via monkeypatch + TestClient, NO unittest.mock, an isolated
ConfigStore and LocationStore per test.
"""
from __future__ import annotations

import json
import math
import time

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

import astrodeck.api.app as app_module
from astrodeck.api.app import ConfigPatchBody
from astrodeck.auth import (principal_for_role, reset_active_provider,
                            set_active_provider)
from astrodeck.config import (CONFIG_SCHEMA, AppConfig, ConfigStore,
                              SafetyConfig, nogo_wedge_problem)
from astrodeck.devices.base import DeviceError
from astrodeck.locations import LocationStore
from astrodeck.sequence import ExposureStep, SequenceEngine, SequencePlan, Target
from astrodeck.sequence.schedule import (HORIZON_BLOCKED_DEG, effective_floor,
                                         nogo_floor)

from test_idle_park_hold import (  # noqa: F401 (fixtures and harness)
    LAT, LON, _ra_at, sim_hub, temp_store)

NAN = float("nan")
INF = float("inf")
#: A stored number no rule text contains, so an echo of it is caught.
LEAK = 987.25
LEAK_AZ = 731.5
_LEAK_TEXT = ("987", "731")

_GOOD_WEDGE = {"az_min": 170.0, "az_max": 190.0, "alt_max": 35.0}
_AZS = [float(a) for a in range(0, 360, 5)]


# --------------------------------------------------------------------- harness

def _write(path, *, min_alt=20.0, nogo=None, **extra):
    """A config.json whose ``safety`` holds exactly ``min_alt`` and ``nogo``,
    stamped current so the load owes no migration and therefore writes
    nothing. ``json.dumps`` writes a NaN as the bare ``NaN`` literal."""
    safety = {"min_alt_deg": min_alt}
    if nogo is not _OMIT:
        safety["nogo_box"] = nogo
    body = {"schema_version": CONFIG_SCHEMA, "version": 7, "safety": safety,
            **extra}
    path.write_text(json.dumps(body), encoding="utf-8")


#: "Leave the key out of the file" (None is a value the file can hold).
_OMIT = object()


def _wedges(wedges):
    return None if wedges is None else [dict(w) for w in wedges]


def _limit_warnings(bus_lines, word):
    return [m for (lvl, m, src) in bus_lines
            if src == "config" and lvl == "warning" and word in m]


class _FakeProvider:
    name = "fake"

    def __init__(self, principal):
        self._principal = principal

    async def resolve(self, request):
        return self._principal


@pytest.fixture(autouse=True)
def _reset_provider_after():
    yield
    reset_active_provider()


def _app_on(store, tmp_path, monkeypatch):
    """The real app over an isolated ConfigStore ``store`` and an isolated
    LocationStore, with an admin principal."""
    import astrodeck.config as config_mod
    import astrodeck.hub as hub_mod
    import astrodeck.locations as loc_mod
    monkeypatch.setattr(config_mod, "config_store", store)
    monkeypatch.setattr(hub_mod, "config_store", store)
    monkeypatch.setattr(app_module, "config_store", store)
    monkeypatch.setattr(hub_mod, "CAPTURE_DIR", tmp_path / "captures")
    monkeypatch.delenv(app_module.AUTH_ENV_VAR, raising=False)
    monkeypatch.setattr(app_module, "configure_provider_from_auth",
                        lambda _auth: app_module.get_active_provider())
    locs = LocationStore(path=tmp_path / "locations.json")
    monkeypatch.setattr(loc_mod, "location_store", locs)
    monkeypatch.setattr(app_module, "location_store", locs)
    reset_active_provider()
    set_active_provider(_FakeProvider(principal_for_role("admin")))
    return app_module.create_app()


def _post(client, safety_json: str):
    """POST ``{"safety": <safety_json>}`` as raw text, so a NaN reaches the
    server as the bare literal a hand-built client sends."""
    return client.post("/api/config", content='{"safety": ' + safety_json + "}",
                       headers={"Content-Type": "application/json"})


# ============================================ the model: every door shares it

@pytest.mark.parametrize("bad", [
    pytest.param(NAN, id="nan"),
    pytest.param(INF, id="infinity"),
    pytest.param(-INF, id="negative-infinity"),
    pytest.param(-0.5, id="below-zero"),
    pytest.param(90.5, id="above-the-zenith"),
    pytest.param(LEAK, id="far-above-the-zenith"),
])
def test_safety_config_refuses_a_floor_that_is_not_finite_and_0_to_90(bad):
    with pytest.raises(ValidationError) as err:
        SafetyConfig(min_alt_deg=bad)
    assert "finite number from 0 to 90" in str(err.value)


@pytest.mark.parametrize("good", [0.0, 10, 45.5, 90.0])
def test_safety_config_keeps_a_floor_inside_the_rule_exactly(good):
    assert SafetyConfig(min_alt_deg=good).min_alt_deg == good


def test_the_issues_own_body_no_longer_validates():
    """The probe from the issue: the config patch body took a NaN floor."""
    with pytest.raises(ValidationError):
        ConfigPatchBody.model_validate_json(
            '{"safety": {"min_alt_deg": NaN}}')


# =========================================== the wedge rule, said once

@pytest.mark.parametrize("wedge, rule", [
    pytest.param({"az_min": 100, "az_max": 120, "alt_max": NAN},
                 "must be finite", id="nan-altitude"),
    pytest.param({"az_min": NAN, "az_max": 120, "alt_max": 30},
                 "must be finite", id="nan-azimuth-from"),
    pytest.param({"az_min": 100, "az_max": NAN, "alt_max": 30},
                 "must be finite", id="nan-azimuth-to"),
    pytest.param({"az_min": 100, "az_max": 120, "alt_max": INF},
                 "must be finite", id="infinite-altitude"),
    pytest.param({"az_min": -INF, "az_max": 120, "alt_max": 30},
                 "must be finite", id="negative-infinite-azimuth"),
    pytest.param({"az_min": 100, "az_max": 120, "alt_max": LEAK},
                 "altitude must be 0..90", id="altitude-above-the-zenith"),
    pytest.param({"az_min": 100, "az_max": 120, "alt_max": -1.0},
                 "altitude must be 0..90", id="altitude-below-zero"),
    pytest.param({"az_min": LEAK_AZ, "az_max": 120, "alt_max": 30},
                 "azimuth must be 0..360", id="azimuth-out-of-range"),
    pytest.param({"az_min": 100, "az_max": -1.0, "alt_max": 30},
                 "azimuth must be 0..360", id="azimuth-minus-1"),
    pytest.param({"az_min": 100, "az_max": 120}, "az_min, az_max and alt_max",
                 id="no-altitude"),
    pytest.param({}, "az_min, az_max and alt_max", id="empty"),
    pytest.param(None, "az_min, az_max and alt_max", id="null"),
    pytest.param("wedge", "az_min, az_max and alt_max", id="text"),
    pytest.param([100, 120, 30], "az_min, az_max and alt_max", id="a-list"),
    pytest.param({"az_min": "north", "az_max": 120, "alt_max": 30},
                 "must be numbers", id="text-value"),
    pytest.param({"az_min": 100, "az_max": 120, "alt_max": None},
                 "must be numbers", id="null-value"),
    pytest.param({"az_min": 100, "az_max": 120, "alt_max": [30]},
                 "must be numbers", id="list-value"),
    pytest.param({"az_min": 100, "az_max": 120, "alt_max": 10 ** 400},
                 "must be numbers", id="integer-too-large-for-a-float"),
])
def test_a_wedge_the_floor_cannot_read_is_named_without_its_value(wedge, rule):
    problem = nogo_wedge_problem(wedge)
    assert problem is not None and rule in problem, problem
    for needle in _LEAK_TEXT:
        assert needle not in problem, "the rule echoed a value"


@pytest.mark.parametrize("wedge", [
    pytest.param({"az_min": 170, "az_max": 190, "alt_max": 35}, id="ints"),
    pytest.param({"az_min": 350.0, "az_max": 10.0, "alt_max": 25.0},
                 id="wraps-north"),
    pytest.param({"az_min": 0, "az_max": 360, "alt_max": 90}, id="the-limits"),
    pytest.param({"az_min": 90, "az_max": 90, "alt_max": 0}, id="degenerate"),
    pytest.param({"az_min": "170", "az_max": "190", "alt_max": "35"},
                 id="numeric-text"),
])
def test_a_wedge_the_settings_panel_can_draw_passes_the_rule(wedge):
    assert nogo_wedge_problem(wedge) is None


# ============================================ POST /api/config (the API door)

_BAD_POST_FLOORS = [
    pytest.param("NaN", id="nan"),
    pytest.param("Infinity", id="infinity"),
    pytest.param("-Infinity", id="negative-infinity"),
    pytest.param("-1", id="below-zero"),
    pytest.param("91", id="above-the-zenith"),
]


@pytest.mark.parametrize("floor", _BAD_POST_FLOORS)
def test_post_config_refuses_a_floor_that_is_not_finite_and_0_to_90(
        tmp_path, monkeypatch, floor):
    """The route answers 422 (not 500: the refusal itself holds the NaN it is
    refusing, and must still encode) and the stored floor is untouched."""
    path = tmp_path / "astrodeck.json"
    _write(path, min_alt=20.0)
    store = ConfigStore(path=path)
    app = _app_on(store, tmp_path, monkeypatch)
    before = store.cfg().version

    with TestClient(app, raise_server_exceptions=False) as c:
        r = _post(c, '{"min_alt_deg": ' + floor + "}")

    assert r.status_code == 422, r.text[:300]
    assert store.cfg().safety.min_alt_deg == 20.0
    assert store.cfg().version == before, "a refused body wrote something"


@pytest.mark.parametrize("wedge, rule", [
    pytest.param('{"az_min": 100, "az_max": 120, "alt_max": NaN}',
                 "must be finite", id="nan-altitude"),
    pytest.param('{"az_min": NaN, "az_max": 120, "alt_max": 30}',
                 "must be finite", id="nan-azimuth"),
    pytest.param('{"az_min": 100, "az_max": 120, "alt_max": Infinity}',
                 "must be finite", id="infinite-altitude"),
    pytest.param('{"az_min": 100, "az_max": 120, "alt_max": 987.25}',
                 "altitude must be 0..90", id="altitude-out-of-range"),
    pytest.param('{"az_min": 731.5, "az_max": 120, "alt_max": 30}',
                 "azimuth must be 0..360", id="azimuth-out-of-range"),
    pytest.param('{"az_min": 100, "az_max": 120}',
                 "az_min, az_max and alt_max", id="missing-altitude"),
    pytest.param('{"az_min": "north", "az_max": 120, "alt_max": 30}',
                 "must be numbers", id="text-value"),
])
def test_post_config_refuses_a_wedge_the_floor_cannot_read(
        tmp_path, monkeypatch, wedge, rule):
    """Each bad wedge sits SECOND in a list beside a good one: the refusal
    names its place (2), and nothing in the body is stored."""
    path = tmp_path / "astrodeck.json"
    _write(path, min_alt=20.0, nogo=[_GOOD_WEDGE])
    store = ConfigStore(path=path)
    app = _app_on(store, tmp_path, monkeypatch)
    before = store.cfg().version

    with TestClient(app, raise_server_exceptions=False) as c:
        r = _post(c, '{"min_alt_deg": 25, "nogo_box": ['
                  + json.dumps(_GOOD_WEDGE) + ", " + wedge + "]}")

    assert r.status_code == 422, r.text[:300]
    detail = r.json()["detail"]
    assert detail["code"] == "invalid_nogo_box"
    assert "no-go wedge 2 not saved" in detail["detail"]
    assert rule in detail["detail"], detail
    for needle in _LEAK_TEXT:
        assert needle not in detail["detail"], "the refusal echoed a value"
    assert store.cfg().version == before, "a refused body wrote something"
    assert store.cfg().safety.min_alt_deg == 20.0, "the good block half-landed"
    assert _wedges(store.cfg().safety.nogo_box) == [_GOOD_WEDGE]


def test_post_config_stores_good_wedges_and_a_good_floor_as_sent(
        tmp_path, monkeypatch):
    """The rule only refuses. A body inside it saves exactly as it did."""
    path = tmp_path / "astrodeck.json"
    _write(path)
    store = ConfigStore(path=path)
    app = _app_on(store, tmp_path, monkeypatch)
    sent = [{"az_min": 350, "az_max": 10, "alt_max": 25},
            {"az_min": 170.5, "az_max": 190.5, "alt_max": 35.5}]

    with TestClient(app, raise_server_exceptions=False) as c:
        r = _post(c, '{"min_alt_deg": 12.5, "nogo_box": ' + json.dumps(sent) + "}")

    assert r.status_code == 200, r.text[:300]
    assert store.cfg().safety.min_alt_deg == 12.5
    assert _wedges(store.cfg().safety.nogo_box) == sent


@pytest.mark.parametrize("nogo", ["null", "[]"], ids=["null", "empty-list"])
def test_post_config_can_still_clear_the_wedges(tmp_path, monkeypatch, nogo):
    path = tmp_path / "astrodeck.json"
    _write(path, nogo=[_GOOD_WEDGE])
    store = ConfigStore(path=path)
    app = _app_on(store, tmp_path, monkeypatch)

    with TestClient(app, raise_server_exceptions=False) as c:
        r = _post(c, '{"min_alt_deg": 20, "nogo_box": ' + nogo + "}")

    assert r.status_code == 200, r.text[:300]
    assert not store.cfg().safety.nogo_box


# ====================================== the config file (the third door)
#
# The load never lowers a limit the floor was reading. A stored number outside
# the rule is clamped to its nearest end, because that is what the gates read
# before the rule existed (the classic Settings panel can type -10 or 95 into a
# wedge, and POST /api/config took it). Only what cannot be read at all is
# removed: NaN, text, null, a missing key, an infinite azimuth.

@pytest.mark.parametrize("stored", [
    pytest.param(NAN, id="nan"),
    pytest.param("abc", id="text"),
    pytest.param(None, id="null"),
    pytest.param([20], id="a-list"),
])
def test_an_unreadable_stored_floor_loads_as_the_default_and_says_so(
        tmp_path, bus_lines, stored):
    """The store loads (refusing would stop the server), the floor is the
    default, and ONE warning names the rule and no value."""
    path = tmp_path / "astrodeck.json"
    _write(path, min_alt=stored)

    cfg = ConfigStore(path=path).cfg()

    assert cfg.safety.min_alt_deg == 0.0
    said = _limit_warnings(bus_lines, "min_alt_deg")
    assert len(said) == 1, said
    assert "not a number" in said[0] and "reset to the default" in said[0]
    for needle in _LEAK_TEXT:
        assert needle not in said[0], "the warning echoed a stored value"


@pytest.mark.parametrize("stored, clamped", [
    pytest.param(INF, 90.0, id="infinity-blocked-so-it-stays-the-zenith"),
    pytest.param(-INF, 0.0, id="negative-infinity-did-nothing-so-it-is-zero"),
    pytest.param(-3.0, 0.0, id="below-zero"),
    pytest.param(95, 90.0, id="above-the-zenith"),
    pytest.param(LEAK, 90.0, id="far-above-the-zenith"),
])
def test_a_stored_floor_outside_0_to_90_is_clamped_not_reset_to_no_floor(
        tmp_path, bus_lines, stored, clamped):
    """A floor of 95 blocked everything below the zenith before the rule. Reset
    to the default it would block nothing, which is a guard switched off by an
    upgrade; clamped it blocks as it did."""
    path = tmp_path / "astrodeck.json"
    _write(path, min_alt=stored)

    cfg = ConfigStore(path=path).cfg()

    assert cfg.safety.min_alt_deg == clamped
    said = _limit_warnings(bus_lines, "min_alt_deg")
    assert len(said) == 1, said
    assert "clamped" in said[0] and "reset" not in said[0], said
    for needle in _LEAK_TEXT:
        assert needle not in said[0], "the warning echoed a stored value"


_RULE_FINITE = "values must be finite"
_RULE_AZ = "azimuth must be 0..360"
_RULE_ALT = "altitude must be 0..90"


@pytest.mark.parametrize("stored, kept, rule, count", [
    pytest.param([{"az_min": 100, "az_max": 120, "alt_max": NAN}, _GOOD_WEDGE],
                 [_GOOD_WEDGE], _RULE_FINITE, 1, id="nan-altitude"),
    pytest.param([{"az_min": NAN, "az_max": 120, "alt_max": 30}, _GOOD_WEDGE],
                 [_GOOD_WEDGE], _RULE_FINITE, 1, id="nan-azimuth"),
    pytest.param([{"az_min": 100, "az_max": INF, "alt_max": 30}, _GOOD_WEDGE],
                 [_GOOD_WEDGE], _RULE_FINITE, 1, id="infinite-azimuth"),
    pytest.param([{"az_min": 100, "az_max": 120}, None, "x", _GOOD_WEDGE],
                 [_GOOD_WEDGE], "az_min, az_max and alt_max", 3,
                 id="not-a-wedge"),
    pytest.param([{"az_min": "north", "az_max": 120, "alt_max": 30}, _GOOD_WEDGE],
                 [_GOOD_WEDGE], "must be numbers", 1, id="text-value"),
    pytest.param([{"az_min": 100, "az_max": 120, "alt_max": NAN}], None,
                 _RULE_FINITE, 1, id="nothing-usable-remains"),
    pytest.param("oops", None, "must be a list", 1, id="not-a-list-text"),
    pytest.param({"az_min": 100}, None, "must be a list", 1,
                 id="not-a-list-object"),
])
def test_an_unreadable_stored_wedge_is_dropped_the_rest_kept_and_it_says_so(
        tmp_path, bus_lines, stored, kept, rule, count):
    path = tmp_path / "astrodeck.json"
    _write(path, nogo=stored)

    cfg = ConfigStore(path=path).cfg()

    assert _wedges(cfg.safety.nogo_box) == kept
    assert cfg.safety.min_alt_deg == 20.0, "the repair touched the floor"
    said = _limit_warnings(bus_lines, "nogo_box")
    assert len(said) == 1, said
    assert "repaired on load" in said[0]
    assert "dropped as unreadable" in said[0] and "brought into range" not in said[0]
    assert rule in said[0], said
    assert f"(x{count})" in said[0], said
    for needle in _LEAK_TEXT:
        assert needle not in said[0], "the warning echoed a stored value"
    # What it kept is a wedge the API door would take.
    for wedge in cfg.safety.nogo_box or []:
        assert nogo_wedge_problem(wedge) is None


@pytest.mark.parametrize("stored, expect, rule, count", [
    pytest.param([{"az_min": -10, "az_max": 10, "alt_max": 30}],
                 [{"az_min": 350.0, "az_max": 10.0, "alt_max": 30.0}],
                 _RULE_AZ, 1, id="negative-azimuth-wraps-to-north"),
    pytest.param([{"az_min": 340, "az_max": 400, "alt_max": 25}],
                 [{"az_min": 340.0, "az_max": 40.0, "alt_max": 25.0}],
                 _RULE_AZ, 1, id="azimuth-past-360-wraps"),
    pytest.param([{"az_min": LEAK_AZ, "az_max": 120, "alt_max": 30}, _GOOD_WEDGE],
                 [{"az_min": 11.5, "az_max": 120.0, "alt_max": 30.0}, _GOOD_WEDGE],
                 _RULE_AZ, 1, id="far-azimuth-wraps-beside-a-good-one"),
    pytest.param([{"az_min": 100, "az_max": 120, "alt_max": 95}],
                 [{"az_min": 100.0, "az_max": 120.0, "alt_max": 90.0}],
                 _RULE_ALT, 1, id="above-the-zenith-stays-a-block"),
    pytest.param([{"az_min": 1, "az_max": 2, "alt_max": LEAK},
                  {"az_min": 3, "az_max": 4, "alt_max": LEAK}, _GOOD_WEDGE],
                 [{"az_min": 1.0, "az_max": 2.0, "alt_max": 90.0},
                  {"az_min": 3.0, "az_max": 4.0, "alt_max": 90.0}, _GOOD_WEDGE],
                 _RULE_ALT, 2, id="altitude-out-of-range-twice"),
    pytest.param([{"az_min": 100, "az_max": 120, "alt_max": INF}],
                 [{"az_min": 100.0, "az_max": 120.0, "alt_max": 90.0}],
                 _RULE_ALT, 1, id="infinite-altitude-blocked-so-it-stays-a-block"),
    pytest.param([{"az_min": 100, "az_max": 120, "alt_max": -INF}],
                 [{"az_min": 100.0, "az_max": 120.0, "alt_max": 0.0}],
                 _RULE_ALT, 1, id="negative-infinite-altitude-did-nothing"),
    pytest.param([{"az_min": 100, "az_max": 120, "alt_max": -4.0}],
                 [{"az_min": 100.0, "az_max": 120.0, "alt_max": 0.0}],
                 _RULE_ALT, 1, id="below-zero-altitude"),
])
def test_a_stored_wedge_the_floor_can_read_is_brought_into_range_and_kept(
        tmp_path, bus_lines, stored, expect, rule, count):
    """Reviewer round 1, blocking: dropping a finite wedge outside 0..360 /
    0..90 switched off a pier guard the floor read correctly. Wrapped and
    clamped, it is kept."""
    path = tmp_path / "astrodeck.json"
    _write(path, nogo=stored)

    cfg = ConfigStore(path=path).cfg()

    assert _wedges(cfg.safety.nogo_box) == expect
    assert cfg.safety.min_alt_deg == 20.0, "the repair touched the floor"
    said = _limit_warnings(bus_lines, "nogo_box")
    assert len(said) == 1, said
    assert "repaired on load" in said[0]
    assert "brought into range and kept" in said[0] and "dropped" not in said[0]
    assert rule in said[0], said
    assert f"(x{count})" in said[0], said
    for needle in _LEAK_TEXT:
        assert needle not in said[0], "the warning echoed a stored value"
    for wedge in cfg.safety.nogo_box:
        assert nogo_wedge_problem(wedge) is None, "kept a wedge the API door refuses"


def test_one_stored_list_with_wedges_dropped_and_wedges_clamped_says_both_once(
        tmp_path, bus_lines):
    path = tmp_path / "astrodeck.json"
    _write(path, nogo=[{"az_min": 100, "az_max": 120, "alt_max": NAN},
                       {"az_min": -10, "az_max": 10, "alt_max": 30}, _GOOD_WEDGE])

    cfg = ConfigStore(path=path).cfg()

    assert _wedges(cfg.safety.nogo_box) == [
        {"az_min": 350.0, "az_max": 10.0, "alt_max": 30.0}, _GOOD_WEDGE]
    said = _limit_warnings(bus_lines, "nogo_box")
    assert len(said) == 1, said
    assert "dropped as unreadable" in said[0] and _RULE_FINITE in said[0]
    assert "brought into range and kept" in said[0] and _RULE_AZ in said[0]
    assert "2 usable wedge(s) kept" in said[0]


@pytest.mark.parametrize("wedge", [
    pytest.param({"az_min": -10, "az_max": 10, "alt_max": 30},
                 id="negative-azimuth"),
    pytest.param({"az_min": 340, "az_max": 400, "alt_max": 25},
                 id="azimuth-past-360"),
    pytest.param({"az_min": 100, "az_max": 120, "alt_max": 95},
                 id="altitude-above-the-zenith"),
    pytest.param({"az_min": 100, "az_max": 120, "alt_max": INF},
                 id="infinite-altitude"),
    pytest.param({"az_min": -400, "az_max": 500, "alt_max": 95},
                 id="both-out-of-range"),
])
def test_a_stored_out_of_range_wedge_gates_after_the_load_as_the_floor_read_it_before(
        tmp_path, wedge):
    """The reviewer's probe. The raw wedge is what the floor read on the file
    as written (a pier guard at the wrapped azimuth, a block when ``alt_max`` is
    over the zenith); the loaded wedge must give the same floor at every
    azimuth, seen from 0.5 degrees apart so both edges and the north seam are
    crossed. Dropped, ``nogo_floor`` read 0 here and the guard was off."""
    path = tmp_path / "astrodeck.json"
    _write(path, min_alt=20.0, nogo=[wedge])

    loaded = ConfigStore(path=path).cfg().safety

    seen = []
    for half in range(0, 720):
        az = half / 2.0
        was = min(90.0, effective_floor(20.0, None, az, [wedge]))
        now = effective_floor(loaded.min_alt_deg, loaded.horizon, az,
                              loaded.nogo_box)
        assert now == was, (az, was, now)
        seen.append(now)
    assert max(seen) > 20.0, "the wedge never raised the floor: nothing was proved"


def test_the_settings_panel_can_save_back_a_wedge_that_was_brought_into_range(
        tmp_path, monkeypatch):
    """What the panel shows is what the load kept, and the API door must take
    it back: a load that kept -10 as -10 would have the panel's own save
    refused with a 422 over a value it did not type."""
    path = tmp_path / "astrodeck.json"
    _write(path, min_alt=20.0,
           nogo=[{"az_min": -10, "az_max": 10, "alt_max": 95}])
    store = ConfigStore(path=path)
    app = _app_on(store, tmp_path, monkeypatch)

    with TestClient(app, raise_server_exceptions=False) as c:
        shown = c.get("/api/config").json()["safety"]
        r = c.post("/api/config", json={"safety": shown})

    assert shown["nogo_box"] == [{"az_min": 350.0, "az_max": 10.0, "alt_max": 90.0}]
    assert r.status_code == 200, r.text[:300]
    assert _wedges(store.cfg().safety.nogo_box) == shown["nogo_box"]


def test_a_nan_in_both_stored_limits_repairs_both_with_one_warning_each(
        tmp_path, bus_lines):
    path = tmp_path / "astrodeck.json"
    _write(path, min_alt=NAN,
           nogo=[{"az_min": 100, "az_max": 120, "alt_max": NAN}, _GOOD_WEDGE])

    cfg = ConfigStore(path=path).cfg()

    assert cfg.safety.min_alt_deg == 0.0
    assert _wedges(cfg.safety.nogo_box) == [_GOOD_WEDGE]
    assert len(_limit_warnings(bus_lines, "min_alt_deg")) == 1
    assert len(_limit_warnings(bus_lines, "nogo_box")) == 1


@pytest.mark.parametrize("floor, nogo", [
    pytest.param(0.0, None, id="defaults"),
    pytest.param(0, [], id="the-explicit-clear"),
    pytest.param(90.0, [{"az_min": 350, "az_max": 10, "alt_max": 25}],
                 id="the-zenith-and-a-wedge-through-north"),
    pytest.param(12.5, [_GOOD_WEDGE, {"az_min": 0, "az_max": 360, "alt_max": 90}],
                 id="the-limits"),
])
def test_limits_that_pass_the_rules_load_exactly_as_stored_and_silently(
        tmp_path, bus_lines, floor, nogo):
    path = tmp_path / "astrodeck.json"
    _write(path, min_alt=floor, nogo=nogo)

    cfg = ConfigStore(path=path).cfg()

    assert cfg.safety.min_alt_deg == floor
    assert _wedges(cfg.safety.nogo_box) == nogo
    assert _limit_warnings(bus_lines, "min_alt_deg") == []
    assert _limit_warnings(bus_lines, "nogo_box") == []


def test_a_config_with_no_safety_block_loads_silently(tmp_path, bus_lines):
    path = tmp_path / "astrodeck.json"
    path.write_text(json.dumps({"schema_version": CONFIG_SCHEMA, "version": 1}),
                    encoding="utf-8")
    cfg = ConfigStore(path=path).cfg()
    assert cfg.safety.min_alt_deg == 0.0 and cfg.safety.nogo_box is None
    assert _limit_warnings(bus_lines, "min_alt_deg") == []
    assert _limit_warnings(bus_lines, "nogo_box") == []


def test_the_config_route_answers_200_over_a_nan_floor_and_wedge(
        tmp_path, monkeypatch):
    """The symptom #982 met for the horizon: a NaN made the redacted payload
    un-encodable and ``GET /api/config`` answered 500, so the settings UI that
    could fix it never loaded."""
    path = tmp_path / "astrodeck.json"
    _write(path, min_alt=NAN,
           nogo=[{"az_min": 100, "az_max": 120, "alt_max": NAN}, _GOOD_WEDGE])
    app = _app_on(ConfigStore(path=path), tmp_path, monkeypatch)

    with TestClient(app, raise_server_exceptions=False) as c:
        r = c.get("/api/config")

    assert r.status_code == 200, r.text[:200]
    safety = r.json()["safety"]
    assert safety["min_alt_deg"] == 0.0
    assert safety["nogo_box"] == [_GOOD_WEDGE]


def test_the_load_does_not_rewrite_the_file_the_next_save_does(tmp_path):
    """The bad file is the evidence, so the load leaves it; the first settings
    save writes the repaired limits, and nothing in them is non-finite."""
    path = tmp_path / "astrodeck.json"
    _write(path, min_alt=NAN,
           nogo=[{"az_min": 100, "az_max": 120, "alt_max": NAN}, _GOOD_WEDGE])
    before = path.read_bytes()

    store = ConfigStore(path=path)
    store.cfg()
    assert path.read_bytes() == before, "the load rewrote the config file"

    store.bump_and_save()
    on_disk = json.loads(path.read_text(encoding="utf-8"))
    assert on_disk["safety"]["min_alt_deg"] == 0.0
    assert on_disk["safety"]["nogo_box"] == [_GOOD_WEDGE]
    json.dumps(on_disk, allow_nan=False)


@pytest.mark.parametrize("primary", ["{ not json", None],
                         ids=["corrupt-primary", "missing-primary"])
def test_a_backup_holding_a_nan_floor_and_wedge_is_repaired_before_restore(
        tmp_path, bus_lines, primary):
    """The other load path: a primary that is corrupt or gone is rebuilt from
    ``astrodeck.json.bak`` and the rebuilt primary is what the next boot reads.
    A repair that ran only on the primary would write the NaN straight back."""
    path = tmp_path / "astrodeck.json"
    bak = tmp_path / "astrodeck.json.bak"
    _write(bak, min_alt=NAN,
           nogo=[{"az_min": 100, "az_max": 120, "alt_max": NAN}, _GOOD_WEDGE])
    if primary is not None:
        path.write_text(primary, encoding="utf-8")

    cfg = ConfigStore(path=path).cfg()

    assert cfg.version == 7, "the backup was the source"
    assert cfg.safety.min_alt_deg == 0.0
    assert _wedges(cfg.safety.nogo_box) == [_GOOD_WEDGE]
    on_disk = json.loads(path.read_text(encoding="utf-8"))
    assert on_disk["safety"]["min_alt_deg"] == 0.0, "the primary was re-persisted bad"
    assert on_disk["safety"]["nogo_box"] == [_GOOD_WEDGE]
    assert len(_limit_warnings(bus_lines, "min_alt_deg")) == 1
    assert len(_limit_warnings(bus_lines, "nogo_box")) == 1


def test_a_repaired_wedge_keeps_gating_and_no_azimuth_reads_below_the_floor(
        tmp_path):
    """The loaded limits fed to the floor, end to end, for the issue's own
    probe: after the load the good wedge still raises the floor where it
    stands, the NaN wedge is gone, and no azimuth reads below the minimum."""
    path = tmp_path / "astrodeck.json"
    _write(path, min_alt=20.0,
           nogo=[{"az_min": 100, "az_max": 120, "alt_max": NAN}, _GOOD_WEDGE])

    safety = ConfigStore(path=path).cfg().safety

    assert effective_floor(safety.min_alt_deg, safety.horizon, 180.0,
                           safety.nogo_box) == 35.0
    for az in _AZS:
        floor = effective_floor(safety.min_alt_deg, safety.horizon, az,
                                safety.nogo_box)
        assert math.isfinite(floor) and floor >= safety.min_alt_deg, az


# ======================================= the floor itself, NaN or not

@pytest.mark.parametrize("bad", [NAN, INF, -INF], ids=["nan", "inf", "-inf"])
@pytest.mark.parametrize("horizon, nogo", [
    pytest.param(None, None, id="no-other-term"),
    pytest.param([(0.0, 10.0), (180.0, 30.0)], None, id="with-a-horizon"),
    pytest.param(None, [_GOOD_WEDGE], id="with-a-wedge"),
])
def test_a_base_floor_that_cannot_be_read_blocks_the_sky_not_opens_it(
        bad, horizon, nogo):
    """The issue's probe was ``effective_floor(nan, None, 110.0, None)``, which
    returned ``nan``: every comparison ``alt < nan`` is False. The unreadable
    floor is the zenith at every azimuth, whatever else is configured."""
    for az in (0.0, 45.0, 110.0, 180.0, 275.0, 359.9):
        assert effective_floor(bad, horizon, az, nogo) == HORIZON_BLOCKED_DEG, az


@pytest.mark.parametrize("bad", [NAN, INF, -INF], ids=["nan", "inf", "-inf"])
def test_a_wedge_altitude_that_cannot_be_read_blocks_inside_the_wedge(bad):
    """The wedge is NOT skipped: inside its azimuth range the floor is the
    zenith. Outside it the wedge still has nothing to say, so one unreadable
    wedge does not close the whole sky. The issue's probe was
    ``effective_floor(20.0, None, 110.0, [wedge])``, which returned 20.0."""
    wedge = {"az_min": 100.0, "az_max": 120.0, "alt_max": bad}
    assert nogo_floor([wedge], 110.0) == HORIZON_BLOCKED_DEG
    assert nogo_floor([wedge], 100.0) == HORIZON_BLOCKED_DEG     # near edge
    assert nogo_floor([wedge], 120.0) == HORIZON_BLOCKED_DEG     # far edge
    assert nogo_floor([wedge], 99.0) == 0.0
    assert nogo_floor([wedge], 121.0) == 0.0
    assert effective_floor(20.0, None, 110.0, [wedge]) == HORIZON_BLOCKED_DEG
    assert effective_floor(20.0, None, 200.0, [wedge]) == 20.0


def test_an_unreadable_wedge_altitude_wraps_north_like_any_other_wedge():
    wedge = {"az_min": 350.0, "az_max": 10.0, "alt_max": NAN}
    assert nogo_floor([wedge], 0.0) == HORIZON_BLOCKED_DEG
    assert nogo_floor([wedge], 355.0) == HORIZON_BLOCKED_DEG
    assert nogo_floor([wedge], 180.0) == 0.0


@pytest.mark.parametrize("az_min, az_max", [
    pytest.param(NAN, 120.0, id="nan-from"),
    pytest.param(100.0, NAN, id="nan-to"),
    pytest.param(NAN, NAN, id="nan-both"),
    pytest.param(INF, 120.0, id="infinite-from"),
    pytest.param(100.0, -INF, id="negative-infinite-to"),
])
def test_a_wedge_azimuth_that_cannot_be_read_covers_the_whole_circle(
        az_min, az_max):
    """A wedge that cannot say where it stands is not dropped: it is read the
    way a degenerate wedge (az_min == az_max) already is, as everywhere, at its
    own altitude (or the zenith when that cannot be read either)."""
    here = [{"az_min": az_min, "az_max": az_max, "alt_max": 30.0}]
    both = [{"az_min": az_min, "az_max": az_max, "alt_max": NAN}]
    for az in (0.0, 110.0, 250.0):
        assert nogo_floor(here, az) == 30.0, az
        assert nogo_floor(both, az) == HORIZON_BLOCKED_DEG, az


def test_a_finite_wedge_that_was_malformed_is_still_ignored_not_fatal():
    """The gate runs mid-night: a wedge with a missing key or a word for a
    number is still skipped rather than raising. Only a NUMBER that cannot be
    read blocks."""
    box = [{"az_min": 0, "az_max": 90}, {"az_min": "north", "az_max": 90,
                                          "alt_max": 10}, None,
           {"az_min": 100, "az_max": 110, "alt_max": 22}]
    assert nogo_floor(box, 105.0) == 22.0
    assert nogo_floor(box, 5.0) == 0.0


@pytest.mark.parametrize("seed_az", range(0, 360, 45))
def test_an_unreadable_value_never_lowers_a_floor_at_any_azimuth(seed_az):
    """The property the issue is about. Take readable limits, add ONE
    unreadable thing (a NaN wedge altitude, a NaN wedge azimuth, an infinite
    one, or a NaN base floor), and the floor at EVERY azimuth is at or above
    what the readable limits gave alone. A NaN may raise a floor; it may never
    lower one, which is what ``max()`` keeping its first argument did."""
    horizon = [(0.0, 8.0), (seed_az + 1.0, 25.0), (300.0, 12.0)]
    good = [_GOOD_WEDGE, {"az_min": 350.0, "az_max": 10.0, "alt_max": 25.0}]
    unreadable = [
        {"az_min": 100.0, "az_max": 120.0, "alt_max": NAN},
        {"az_min": 100.0, "az_max": 120.0, "alt_max": INF},
        {"az_min": NAN, "az_max": 120.0, "alt_max": 5.0},
        {"az_min": 100.0, "az_max": INF, "alt_max": 5.0},
    ]
    for bad_wedge in unreadable:
        for az in _AZS:
            base = effective_floor(20.0, horizon, az, good)
            with_bad = effective_floor(20.0, horizon, az, good + [bad_wedge])
            assert with_bad >= base, (bad_wedge, az)
            assert math.isfinite(with_bad)
    for az in _AZS:
        assert effective_floor(NAN, horizon, az, good) >= \
            effective_floor(20.0, horizon, az, good)


def test_readable_limits_give_the_answer_they_always_did():
    """The control: nothing here moved a floor that was already readable."""
    horizon = [(0.0, 5.0), (180.0, 5.0)]
    box = [{"az_min": 170, "az_max": 190, "alt_max": 35}]
    assert effective_floor(10.0, horizon, 180.0, box) == 35.0
    assert effective_floor(10.0, horizon, 90.0, box) == 10.0
    assert effective_floor(0.0, None, 90.0, None) == 0.0
    assert effective_floor(90.0, None, 90.0, None) == 90.0
    assert nogo_floor([{"az_min": 90, "az_max": 90, "alt_max": 15}], 270.0) == 15.0


# ============================ the real gates, with an unreadable floor beyond them

def _high_target(now: float) -> tuple[float, float]:
    """RA/Dec of a target high in the sky at the fixture site, now (about 70
    degrees up): far above any floor an ordinary rig sets, and below the zenith
    so a floor of 90 refuses it."""
    return _ra_at(1.0, now), LAT + 15.0


async def test_the_hub_slew_gate_refuses_when_the_floor_cannot_be_read(
        sim_hub, temp_store):
    """``hub._check_horizon`` (the gate GOTO and sequence start run): a NaN
    floor that got past the doors by direct assignment used to refuse nothing,
    because ``alt < nan`` is False. It reads as the zenith now."""
    ra, dec = _high_target(time.time())
    temp_store.cfg().safety.min_alt_deg = 20.0
    sim_hub._check_horizon(ra, dec)             # premise: a readable 20 passes

    temp_store.cfg().safety.min_alt_deg = NAN
    with pytest.raises(DeviceError, match="below the obstruction horizon"):
        sim_hub._check_horizon(ra, dec)


async def test_the_hub_slew_gate_refuses_inside_a_wedge_whose_altitude_is_nan(
        sim_hub, temp_store):
    """The wedge half of the issue: a pier guard whose ``alt_max`` is NaN was
    skipped, so a target behind the pier was let through."""
    from astrodeck.catalog import altaz
    ra, dec = _high_target(time.time())
    _alt, az = altaz(ra, dec, LAT, LON)
    safety = temp_store.cfg().safety
    safety.min_alt_deg = 20.0
    safety.nogo_box = [{"az_min": (az - 10.0) % 360.0,
                        "az_max": (az + 10.0) % 360.0, "alt_max": NAN}]

    with pytest.raises(DeviceError, match="below the obstruction horizon"):
        sim_hub._check_horizon(ra, dec)

    safety.nogo_box = [{"az_min": (az + 90.0) % 360.0,
                        "az_max": (az + 110.0) % 360.0, "alt_max": NAN}]
    sim_hub._check_horizon(ra, dec)             # elsewhere in the sky: passes


@pytest.mark.parametrize("bad", [NAN, INF, -INF], ids=["nan", "inf", "-inf"])
async def test_the_engine_gate_refuses_when_the_floor_cannot_be_read(
        sim_hub, temp_store, bad):
    """The engine's own gate (``_altitude_limit_verdict``) decides whether a
    floor is configured from ``min_alt_deg > 0`` BEFORE it asks
    ``effective_floor``. A NaN is truthy and not greater than 0, so a rig with
    no horizon and no wedge returned 'nothing to enforce' without the floor
    being asked at all. So is a negative infinity; only +inf got through to the
    floor by being greater than 0, and the three are graded alike so a gate that
    tests for one of them (``isnan``) is caught."""
    e = SequenceEngine(sim_hub)
    e.plan = SequencePlan(name="gate", guide=False, meridian_flip=False,
                          safety_check=False, targets=[])
    e._cfg = AppConfig(safety=SafetyConfig(enabled=False))
    ra, dec = _high_target(time.time())
    target = Target(name="Alpha", ra_hours=ra, dec_deg=dec, center=False,
                    steps=[ExposureStep(filter="L", exposure_s=30.0, gain=100,
                                        count=1)])
    assert e._altitude_limit_verdict(target, projected=False, cfg=e._cfg) is None, (
        "premise: with no floor configured the target is let in")

    e._cfg.safety.min_alt_deg = bad             # past the doors, by assignment
    verdict = e._altitude_limit_verdict(target, projected=False, cfg=e._cfg)

    assert verdict is not None and verdict.kind == "floor"
