# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""An operator may reconnect the rig by activating a saved profile (#759, WP-146).

Owner ruling on #759 (2026-10-07): "This should be permitted for Admin and
operator roles." ``POST /api/profiles/<id>/activate`` is
the documented way to reconnect the whole rig, and it was gated on
``config.backend`` -- the capability that also writes a profile's content, the
driver endpoints and the managed-PHD2 spawn -- so an operator running a night
could not bring a dropped rig back, and a relayed operator could not either.

The shape, pinned here role by role and origin by origin:

* ``control.reconnect`` is a NEW, narrow capability held by admin and operator,
  never by viewer or syncer. It gates an UNFORCED activate of a SAVED profile,
  on the LAN and through the relay (the wave-15 fence row already lets the route
  through; the capability is the only thing that changed).
* ``force`` (abort a running sequence, disarm auto-resume) stays a
  ``config.backend`` decision, so admin only, and stays 403 ``local_only`` over
  the relay for every role, answered before anything else.
* Saving, editing and deleting profiles, ``/apply``, ``/api/connect/*`` and
  ``/api/discover`` keep ``config.backend``: nothing else an operator can reach
  moved.

Named mutants, each run from a byte backup under
``pytest -n 0 tests/test_w17_reconnect_capability.py
tests/test_w15_remote_profile_activate.py tests/test_rbac_core.py
tests/test_rbac_enforcement.py`` and restored byte-identically (sha256 compared)
on 2026-10-07. What each one made the suite print, verbatim (first failing
assertion; the suite is 160 cases):

* RECONNECT_GATE_IS_BACKEND -- ``activate_profile``'s ``@declare`` and its
  ``Depends(require(...))`` both went back to ``CAP_CONFIG_BACKEND``.
  ``test_unforced_activate_by_role_and_origin[operator ...]``:
  ``AssertionError: ('operator', False, '{"detail":"capability not held"}')`` /
  ``assert 403 == 200``; the 409 case reads ``assert 403 == 409`` and the forced
  operator case ``assert None == 'forbidden'``.
* FORCE_NEEDS_ONLY_RECONNECT -- the ``config.backend`` check on ``force`` in
  ``activate_profile`` became ``if False:``. 3 failed, 157 passed, first
  ``test_forced_activate_by_role_and_origin[operator-False-403-forbidden]``:
  ``AssertionError: ('operator', False, '{"started":"profile"}')`` /
  ``assert 200 == 403`` (an operator aborts the run); also
  ``test_an_operators_refused_force_names_the_capability`` and
  ``test_rbac_enforcement.py::test_operator_boundary`` (``assert 404 == 403``).
* ORIGIN_AFTER_CAPABILITY -- ``if force and _scope_is_remote(request):`` became
  ``if force and _scope_is_remote(request) and principal.has(CAP_CONFIG_BACKEND):``
  so the capability sentence is answered before the origin one. 1 failed, 159
  passed: ``test_forced_activate_by_role_and_origin[operator-True-403-local_only]``:
  ``assert 'forbidden' == 'local_only'``.
* VIEWER_HOLDS_RECONNECT -- ``CAP_CONTROL_RECONNECT`` added to
  ``VIEWER_LINK_CAPS``. ``test_unforced_activate_by_role_and_origin[viewer ...]``:
  ``AssertionError: ('viewer', False, '{"started":"profile"}')`` /
  ``assert 200 == 403``; ``test_the_role_table_holds_reconnect_for_admin_and_
  operator_only``: ``assert 'control.reconnect' not in frozenset({'control.
  reconnect', 'view.preview', 'view.status'})``.
* OPERATOR_LACKS_RECONNECT -- ``CAP_CONTROL_RECONNECT`` removed from the operator
  set. Same first failure as RECONNECT_GATE_IS_BACKEND: ``('operator', False,
  '{"detail":"capability not held"}')`` / ``assert 403 == 200``.
* APPLY_RIDES_RECONNECT -- ``/api/profiles/{id}/apply`` took
  ``require(CAP_CONTROL_RECONNECT)`` (and the label) instead of
  ``config.backend``. 3 failed, 157 passed: ``test_an_operator_keeps_every_other_
  profile_and_connect_route_fenced``: ``('post', '/api/profiles/<id>/apply', 200,
  '{"started":"profile"}')`` / ``assert 200 == 403``; ``test_the_routes_floor_is_
  reconnect_and_apply_is_still_backend``: ``assert frozenset({'control.
  reconnect'}) == frozenset({'config.backend'})``; ``test_operator_boundary``:
  ``assert 404 == 403``.
"""
from __future__ import annotations

import time

import pytest

import astrodeck.api.app as app_module
from astrodeck.auth.capabilities import (CAP_CONFIG_BACKEND,
                                         CAP_CONTROL_RECONNECT, ROLES_CAP)
from astrodeck.auth.rbac import _dependency_caps, _marker_caps, iter_app_routes

from test_w15_remote_profile_activate import (  # noqa: F401  (fixtures + helpers)
    _FakeRunningTask, _Spy, _clean_provider, _client, _code, rig)

ROLES_UNDER_TEST = ("admin", "operator", "viewer")


def _user(rig, role):
    return getattr(rig, role)


def _origin(remote: bool) -> str:
    return "relay" if remote else "lan"


# -------------------------------------------------------------- unforced matrix

@pytest.mark.parametrize("remote", [False, True], ids=_origin)
@pytest.mark.parametrize("role,allowed", [("admin", True), ("operator", True),
                                          ("viewer", False)])
def test_unforced_activate_by_role_and_origin(rig, monkeypatch, role, allowed,
                                              remote):
    """The headline: admin and operator reconnect the rig by activating a saved
    profile, on the LAN and through the relay; a viewer is refused 403
    ``capability not held`` in both places and the rig is not touched."""
    spy = _Spy(monkeypatch)
    with _client(rig, _user(rig, role), remote=remote) as c:
        r = c.post(f"/api/profiles/{rig.pid}/activate")
        if allowed:
            assert r.status_code == 200, (role, remote, r.text)
            assert r.json().get("started") == "profile"
            deadline = time.monotonic() + 10
            while "connect" not in spy.calls and time.monotonic() < deadline:
                c.get("/api/status")  # lets the spawned connect run on the loop
                time.sleep(0.02)
        else:
            assert r.status_code == 403, (role, remote, r.text)
            assert r.json()["detail"] == "capability not held"
    assert spy.calls == (["connect"] if allowed else [])


@pytest.mark.parametrize("remote", [False, True], ids=_origin)
def test_an_operators_unforced_activate_still_409s_while_a_run_is_active(
        rig, monkeypatch, remote):
    """The new capability buys a reconnect, not a way to tear the rig down
    under a running sequence: the 409 guard is unchanged."""
    spy = _Spy(monkeypatch)
    monkeypatch.setattr(app_module.engine, "_task", _FakeRunningTask())
    with _client(rig, rig.operator, remote=remote) as c:
        r = c.post(f"/api/profiles/{rig.pid}/activate")
    assert r.status_code == 409, r.text
    assert _code(r) == "running"
    assert spy.calls == []


# --------------------------------------------------------------- forced matrix

@pytest.mark.parametrize("role,remote,status,code", [
    ("admin", False, 200, None),
    ("admin", True, 403, "local_only"),
    # An operator holds control.reconnect but not config.backend: on the LAN the
    # force is refused for want of the capability ...
    ("operator", False, 403, "forbidden"),
    # ... and over the relay for the origin, which outranks the capability
    # sentence (the same order for an admin).
    ("operator", True, 403, "local_only"),
    # A viewer never gets as far as the force: the route floor refuses it.
    ("viewer", False, 403, None),
    ("viewer", True, 403, None),
], ids=lambda v: str(v))
def test_forced_activate_by_role_and_origin(rig, monkeypatch, role, remote,
                                            status, code):
    """``force`` aborts a running sequence and disarms auto-resume. Only an
    admin on the LAN may do it. Every refusal happens BEFORE the run is touched,
    which the spy records: it must be empty."""
    spy = _Spy(monkeypatch)
    monkeypatch.setattr(app_module.engine, "_task", _FakeRunningTask())
    with _client(rig, _user(rig, role), remote=remote) as c:
        r = c.post(f"/api/profiles/{rig.pid}/activate", json={"force": True})
    assert r.status_code == status, (role, remote, r.text)
    if status == 200:
        assert "stop_recovery" in spy.calls and "abort" in spy.calls
        return
    if code is None:
        assert r.json()["detail"] == "capability not held", r.text
    else:
        assert _code(r) == code, r.text
    assert spy.calls == [], "a refused force must not abort the run"


def test_an_operators_refused_force_names_the_capability(rig, monkeypatch):
    """The 403 an operator gets for a force is a sentence the UI can show: it
    names ``config.backend`` and says what the force would have done."""
    _Spy(monkeypatch)
    monkeypatch.setattr(app_module.engine, "_task", _FakeRunningTask())
    with _client(rig, rig.operator, remote=False) as c:
        r = c.post(f"/api/profiles/{rig.pid}/activate", json={"force": True})
    assert r.status_code == 403, r.text
    msg = r.json()["detail"]["detail"]
    assert CAP_CONFIG_BACKEND in msg and "forcing" in msg, msg


# ------------------------------------------------ nothing else an operator can do

def test_an_operator_keeps_every_other_profile_and_connect_route_fenced(
        rig, monkeypatch):
    """The ruling moves ONE route. Everything that writes a profile, applies one
    with its overrides, connects a rig from a caller-chosen spec or scans the
    network is still ``config.backend``, so an operator is refused 403
    ``capability not held`` on the LAN (where no fence would help)."""
    spy = _Spy(monkeypatch)
    pid = rig.pid
    refused = [
        ("post", f"/api/profiles/{pid}/apply", {}),
        ("post", "/api/profiles", {"name": "x"}),
        ("post", "/api/profiles/capture", {}),
        ("patch", f"/api/profiles/{pid}", {"name": "renamed"}),
        ("delete", f"/api/profiles/{pid}", None),
        ("post", f"/api/profiles/{pid}/clear-overrides", {}),
        ("post", f"/api/profiles/{pid}/set-providers", {"providers": {}}),
        ("post", "/api/connect/rig", {"primary": "sim", "roles": {}}),
        ("post", "/api/connect/sim", None),
        ("post", "/api/connect/nina", {}),
        ("get", "/api/discover", None),
        ("get", "/api/discover/alpaca", None),
    ]
    with _client(rig, rig.operator, remote=False) as c:
        for method, path, body in refused:
            kw = {} if body is None else {"json": body}
            r = getattr(c, method)(path, **kw)
            assert r.status_code == 403, (method, path, r.status_code, r.text)
            assert r.json()["detail"] == "capability not held", (method, path)
    assert spy.calls == []
    from astrodeck.profiles import profiles as profile_lib
    assert profile_lib.get(pid).name == "Sim Rig"


# ----------------------------------------------------- the capability tables agree

def test_the_role_table_holds_reconnect_for_admin_and_operator_only():
    assert CAP_CONTROL_RECONNECT == "control.reconnect"
    for role in ("admin", "operator"):
        assert CAP_CONTROL_RECONNECT in ROLES_CAP[role], role
    for role in ("viewer", "syncer"):
        assert CAP_CONTROL_RECONNECT not in ROLES_CAP[role], role


def test_the_routes_floor_is_reconnect_and_apply_is_still_backend(rig):
    """Graded against what each route ENFORCES (its ``require`` dependencies),
    not what its ``@declare`` label says: the activate floor is the narrow
    capability, ``/apply`` -- the route that carries overrides and has no
    saved-id-only argument -- stays ``config.backend``."""
    enforced = {}
    for route in iter_app_routes(rig.app):
        for method in getattr(route, "methods", None) or ():
            enforced[(method, route.path)] = (_dependency_caps(route),
                                              _marker_caps(route))
    act = ("POST", "/api/profiles/{profile_id}/activate")
    apply_ = ("POST", "/api/profiles/{profile_id}/apply")
    assert enforced[act][0] == frozenset({CAP_CONTROL_RECONNECT}), enforced[act]
    assert enforced[act][1] <= enforced[act][0], (
        "the label claims a capability the route does not enforce")
    assert enforced[apply_][0] == frozenset({CAP_CONFIG_BACKEND}), enforced[apply_]
