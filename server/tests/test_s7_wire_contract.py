"""The wave-S7 wire contract: every route the UI wave codes against, present
on the real app with the method and the capability the contract names.

WHY THIS FILE EXISTS, and it is not a duplicate of the feature tests. Eleven
features landed as eleven modules, and ``api/app.py`` is the ONE place they are
wired to the outside: a router included after the SPA catch-all answers 404 on
every one of its paths, an import left out of ``create_app`` takes a whole
feature off the wire, and both failures are SILENT - the app boots, the suite is
green, and the only witness is a UI wave coding against routes that are not
there. Each feature test mirrors its own handler and grades its behaviour; this
one grades that the handler is REACHABLE, under the path and cap the contract
promised.

The table below IS the contract handed to wave U7b. A row changed here is a
change to what the UI may call, which is why the caps are written out rather
than read back off the app.
"""
from __future__ import annotations

import pytest

import astrodeck.api.app as app_module
from astrodeck.auth import principal_for_role, reset_active_provider, set_active_provider
from astrodeck.auth.rbac import _dependency_caps, iter_app_routes
from astrodeck.config import ConfigStore
from fastapi.testclient import TestClient


# (method, path, capability) - the capability is what actually GATES the route
# (a real ``Depends(require(cap))``), never the ``@declare`` marker, which is a
# label and is graded separately by the boot assertion.
_WIRE = (
    # -- satellite + comet ephemerides (D-SKY-1) -----------------------------
    ("GET", "/api/ephemeris/status", "view.status"),
    ("POST", "/api/ephemeris/refresh", "config.site_optics"),
    ("GET", "/api/satellites/passes", "view.site_derived"),

    # -- per-channel session stack (D-SES-1) ---------------------------------
    ("GET", "/api/sequence/stack/preview.jpg", "view.preview"),

    # -- promote the last frame (D-SES-4) ------------------------------------
    ("GET", "/api/capture/last", "view.status"),
    ("POST", "/api/capture/last/save", "control.capture"),

    # -- SER video (D-RIG-1) --------------------------------------------------
    ("POST", "/api/capture/video", "control.capture"),
    ("GET", "/api/capture/video", "view.status"),
    ("POST", "/api/capture/video/stop", "control.capture"),
    ("GET", "/api/captures/video", "view.status"),
    ("GET", "/api/captures/video/{rec_id}.ser", "view.media"),
    ("DELETE", "/api/captures/video/{rec_id}", "control.capture"),
    ("POST", "/api/captures/video/{rec_id}/stack", "control.capture"),
    ("GET", "/api/captures/video/{rec_id}/stack.png", "view.preview"),

    # -- mount nudge + the driver ceiling (D-RIG-4) --------------------------
    ("POST", "/api/mount/nudge", "control.mount"),
    ("POST", "/api/mount/move", "control.mount"),

    # -- protected switch ports (D-RIG-5) ------------------------------------
    ("GET", "/api/switch/ports", "view.status"),
    ("POST", "/api/switch/set", "control.power"),
    ("PUT", "/api/switch/ports/{port_id}", "config.safety"),

    # -- rig-level planning prefs (D-FU-1) -----------------------------------
    ("GET", "/api/planning", "view.status"),
    ("PUT", "/api/planning", "control.capture"),
)


@pytest.fixture(scope="module")
def app():
    return app_module.create_app()


def _routes(app) -> dict[tuple[str, str], object]:
    out: dict[tuple[str, str], object] = {}
    for route in iter_app_routes(app):
        path = getattr(route, "path", None)
        if not path:
            continue
        for method in (getattr(route, "methods", None) or ()):
            if method in ("HEAD", "OPTIONS"):
                continue
            out[(method, path)] = route
    return out


@pytest.mark.parametrize("method,path,cap", _WIRE,
                         ids=[f"{m} {p}" for m, p, _ in _WIRE])
def test_the_route_is_on_the_wire_with_the_capability_the_contract_names(
        app, method, path, cap):
    """SABOTAGE (run red, restored): commenting out any one ``include_router``
    in ``create_app``. Every path that router carries fails here BY NAME - which
    is the failure mode this file exists for, because the app still boots, the
    feature's own tests still pass against their mirrored handlers, and the only
    other symptom is a 404 in a browser nobody has opened yet."""
    routes = _routes(app)
    assert (method, path) in routes, (
        f"{method} {path} is not on the app. Either the route was never "
        f"registered, or its router was included AFTER the SPA catch-all "
        f"GET /{{path:path}}, which shadows everything behind it (measured: "
        f"404). Registered paths starting with the same prefix: "
        f"{sorted(p for m, p in routes if p.startswith(path.split('{')[0]))}")
    enforced = _dependency_caps(routes[(method, path)])
    assert enforced == frozenset({cap}), (
        f"{method} {path} is ENFORCED on {sorted(enforced)}, and the wire "
        f"contract says {cap!r}. The marker (@declare) is not evidence: only a "
        f"Depends(require(...)) refuses anybody.")


def test_the_two_fence_prefixes_landed_and_the_switch_itself_did_not(app):
    """The relay fence, as wave S7 leaves it.

    ``/api/ephemeris`` because a refresh makes THIS BOX dial out, which is the
    SSRF shape the list exists for. ``/api/switch/ports`` because the PUT
    decides what the ENGINE refuses during a run - protection policy, the same
    argument that put ``/api/locations`` there.

    THE THIRD ASSERTION IS THE ONE THAT MATTERS. ``startswith`` means a prefix
    of ``/api/switch`` would read as the same intent and would silently kill
    remote power control - operating a power box from the sofa is the product,
    and nothing else in the suite would notice it stopping."""
    prefixes = tuple(app_module._REMOTE_LOCAL_ONLY_MUTATION_PREFIXES)
    assert "/api/ephemeris" in prefixes
    assert "/api/switch/ports" in prefixes
    assert any("/api/switch/ports/1".startswith(p) for p in prefixes)
    assert not any("/api/switch/set".startswith(p) for p in prefixes), (
        "fencing /api/switch/set would make a power box unusable over the relay")


def test_the_planning_route_is_deliberately_not_fenced(app):
    """A decision, recorded as an assertion (ruling 12).

    ``PUT /api/planning`` writes exposure times and catalogue ids and nothing
    else - no host, no path, no credential, no outbound fetch. Deciding what
    tonight shoots is exactly what a remote operator is doing, and it lives at
    ``/api/planning`` rather than under ``/api/config`` on purpose."""
    for prefix in app_module._REMOTE_LOCAL_ONLY_MUTATION_PREFIXES:
        assert not "/api/planning".startswith(prefix), (
            f"/api/planning is fenced by {prefix!r}; the ruling is that it is "
            f"not relay-fenced")
    assert "/api/planning" not in app_module._REMOTE_LOCAL_ONLY_EXACT


def test_capture_and_mount_are_not_fenced_either(app):
    """The other two non-decisions from the same pass.

    Taking and keeping frames is the science; pointing the telescope remotely
    is the whole point of a relay. Fencing either would mean the answer to
    "keep that one" or "a bit further east" is yes at the scope and no from the
    sofa."""
    for path in ("/api/capture", "/api/capture/last/save", "/api/capture/video",
                 "/api/mount/goto", "/api/mount/nudge", "/api/mount/move"):
        for prefix in app_module._REMOTE_LOCAL_ONLY_MUTATION_PREFIXES:
            assert not path.startswith(prefix), f"{path} is fenced by {prefix!r}"


# ------------------------------------------------------------- the refusals
# One end-to-end pass per new route, for the roles the matrix says are refused.
# The parametrised matrix in test_rbac_enforcement.py grades every role on every
# row; this is the same assertion made against the SAME app object the contract
# above was read off, so a route cannot pass the presence check here and be
# gated by something else in practice.

_REFUSED = (
    ("POST", "/api/capture/last/save", {}, ("viewer", "syncer")),
    ("POST", "/api/mount/nudge", {"axis": "ra", "arcmin": 5.0},
     ("viewer", "syncer")),
    ("PUT", "/api/switch/ports/1", {"follow_dew": True},
     ("viewer", "syncer", "operator")),
)


class _FixedPrincipal:
    name = "fake"

    def __init__(self, principal):
        self._principal = principal

    async def resolve(self, request):
        return self._principal


@pytest.fixture
def client(tmp_path, monkeypatch):
    import astrodeck.config as config_mod
    import astrodeck.hub as hub_mod

    store = ConfigStore(path=tmp_path / "astrodeck.json")
    monkeypatch.setattr(config_mod, "config_store", store)
    monkeypatch.setattr(hub_mod, "config_store", store)
    monkeypatch.setattr(app_module, "config_store", store)
    monkeypatch.setattr(hub_mod, "CAPTURE_DIR", tmp_path / "captures")
    monkeypatch.setattr(app_module, "configure_provider_from_auth",
                        lambda _auth: app_module.get_active_provider())
    reset_active_provider()
    built = app_module.create_app()
    with TestClient(built) as c:
        yield c
    reset_active_provider()


@pytest.mark.parametrize("method,path,body,roles", _REFUSED,
                         ids=[f"{m} {p}" for m, p, _b, _r in _REFUSED])
def test_the_new_write_routes_refuse_the_roles_the_matrix_names(
        client, method, path, body, roles):
    for role in roles:
        set_active_provider(_FixedPrincipal(principal_for_role(role)))
        r = client.request(method, path, json=body)
        assert r.status_code == 403, (
            f"{method} {path} answered {r.status_code} for a {role}: "
            f"{r.text[:200]}")


# ------------------------------------------------- the config blocks (item 6)
# ``focus`` and ``dew`` reach the store through POST /api/config, gated on
# config.safety; ``planning`` deliberately does NOT, because it has its own
# route under a capability the shipped operator actually holds. Nothing else in
# the suite grades the spine's half of that: the feature tasks own the models
# and the setters, and this is the wiring between them.

def test_the_focus_and_dew_blocks_reach_the_store_and_ride_config_safety(
        client, tmp_path, monkeypatch):
    # The ISOLATED store the client fixture installed, not the module default.
    store = app_module.config_store

    # The operator holds no config.* at all, which is the point: temperature
    # compensation with the sign backwards does not fail to correct the focus
    # drift, it doubles it, and a dew policy decides how much power sits on the
    # glass all night.
    set_active_provider(_FixedPrincipal(principal_for_role("operator")))
    assert client.post("/api/config", json={
        "focus": {"approach_overshoot_steps": 150}}).status_code == 403
    assert client.post("/api/config", json={"dew": {"enabled": True}}
                       ).status_code == 403

    set_active_provider(_FixedPrincipal(principal_for_role("admin")))
    r = client.post("/api/config", json={
        "focus": {"approach_overshoot_steps": 150,
                  "temp_comp": {"enabled": True, "steps_per_c": 12.5}}})
    assert r.status_code == 200, r.text
    assert store.cfg().focus.approach_overshoot_steps == 150
    assert store.cfg().focus.temp_comp.enabled is True
    assert store.cfg().focus.temp_comp.steps_per_c == pytest.approx(12.5)

    assert client.post("/api/config", json={"dew": {"enabled": True}}
                       ).status_code == 200
    assert store.cfg().dew.enabled is True
    # ...and it is READ back, so what the rig is doing is visible rather than
    # only writable.
    echoed = client.get("/api/config").json()
    assert echoed["focus"]["temp_comp"]["steps_per_c"] == pytest.approx(12.5)
    assert echoed["dew"]["enabled"] is True


def test_planning_is_not_a_config_block(client):
    """It is readable on /api/config (it is part of AppConfig) and NOT writable
    there. ``extra="forbid"`` means the whole body is rejected rather than the
    key silently dropped - a 200 that merged nothing is the shape that cost 19
    warm frames through ``cooling.setpoint_c``."""
    set_active_provider(_FixedPrincipal(principal_for_role("admin")))
    r = client.post("/api/config", json={"planning": {"pool": ["M31"]}})
    assert r.status_code == 422, r.text
    assert "planning" in r.text
    assert "planning" in client.get("/api/config").json(), (
        "the block is viewer-readable on /api/config by design; only the WRITE "
        "lives at PUT /api/planning")
