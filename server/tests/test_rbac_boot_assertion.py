"""W2 RBAC boot route-assertion tests (T-RBAC-5/6).

``assert_route_capabilities(app)`` must FAIL ``create_app()`` if any mutating
route is un-gated, tagged with a retired cap, declares an unknown cap, reaches
a motion sink without ``control.mount``, declares a capability that no
``require()`` dependency actually enforces, or discloses identity from a GET
with no auth dependency. These tests build SYNTHETIC FastAPI apps (no full
create_app) and assert the assertion raises deterministically.
"""
from __future__ import annotations

import pytest
from fastapi import Depends, FastAPI

from astrodeck.auth import (CAP_CONFIG_SAFETY, CAP_CONFIG_SITE_OPTICS,
                            CAP_CONTROL_CAPTURE, CAP_CONTROL_MOUNT,
                            CAP_VIEW_STATUS, require)
from astrodeck.auth.rbac import (IDENTITY_SELF_GATED, RouteCapabilityError,
                                 assert_route_capabilities, declare)


def test_mutating_route_without_cap_fails():
    app = FastAPI()

    @app.post("/api/danger")
    async def danger():
        return {"ok": True}

    with pytest.raises(RouteCapabilityError):
        assert_route_capabilities(app)


def test_mutating_route_with_cap_passes():
    app = FastAPI()

    @app.post("/api/ok", dependencies=[Depends(require(CAP_CONTROL_CAPTURE))])
    @declare(CAP_CONTROL_CAPTURE)
    async def ok():
        return {"ok": True}

    # must NOT raise
    assert_route_capabilities(app)


def test_retired_cap_tag_fails():
    app = FastAPI()

    @app.get("/api/legacy")
    @declare("view")  # retired monolithic capability
    async def legacy():
        return {"ok": True}

    with pytest.raises(RouteCapabilityError):
        assert_route_capabilities(app)


def test_config_mount_limits_retired_fails():
    app = FastAPI()

    @app.post("/api/limits")
    @declare("config.mount_limits")
    async def limits():
        return {"ok": True}

    with pytest.raises(RouteCapabilityError):
        assert_route_capabilities(app)


def test_motion_sink_mistag_fails():
    """A route DECLARED reaching SequenceEngine.start but tagged control.capture
    (NOT control.mount) must fail boot -- the deterministic mis-tag catch."""
    app = FastAPI()

    @app.post("/api/seq", dependencies=[Depends(require(CAP_CONTROL_CAPTURE))])
    @declare(CAP_CONTROL_CAPTURE, reaches={"SequenceEngine.start"})
    async def seq():
        return {"ok": True}

    with pytest.raises(RouteCapabilityError):
        assert_route_capabilities(app)


def test_motion_sink_correct_tag_passes():
    app = FastAPI()

    @app.post("/api/seq", dependencies=[Depends(require(CAP_CONTROL_MOUNT))])
    @declare(CAP_CONTROL_MOUNT, reaches={"SequenceEngine.start"})
    async def seq():
        return {"ok": True}

    assert_route_capabilities(app)


def test_unknown_cap_fails():
    app = FastAPI()

    @app.post("/api/weird")
    @declare("control.teleport")  # not a real capability
    async def weird():
        return {"ok": True}

    with pytest.raises(RouteCapabilityError):
        assert_route_capabilities(app)


# ------------------------------------------------ (b) the marker is not evidence
# A ``@declare(cap)`` is a LABEL the author wrote; only ``Depends(require(cap))``
# is a gate. Grading the label meant a route could advertise ``control.mount``
# while enforcing ``view.status`` and still boot.

def test_marker_cap_without_matching_dependency_fails():
    """@declare says control.mount; the only require() dependency is view.status.
    The label out-runs the enforcement -> boot failure."""
    app = FastAPI()

    @app.post("/api/loud", dependencies=[Depends(require(CAP_VIEW_STATUS))])
    @declare(CAP_CONTROL_MOUNT)
    async def loud():
        return {"ok": True}

    with pytest.raises(RouteCapabilityError, match=r"control\.mount"):
        assert_route_capabilities(app)


def test_marker_cap_backed_by_dependency_passes():
    """The same shape, honestly wired -- must NOT fire (no over-firing)."""
    app = FastAPI()

    @app.post("/api/honest", dependencies=[Depends(require(CAP_CONTROL_MOUNT))])
    @declare(CAP_CONTROL_MOUNT)
    async def honest():
        return {"ok": True}

    assert_route_capabilities(app)


def test_parameter_level_depends_counts_as_enforcement():
    """``principal: Principal = Depends(require(cap))`` in the SIGNATURE is a real
    gate (FastAPI folds it into route.dependant.dependencies), not a missing one."""
    app = FastAPI()

    @app.post("/api/param")
    @declare(CAP_CONTROL_MOUNT)
    async def param(principal=Depends(require(CAP_CONTROL_MOUNT))):
        return {"ok": True}

    assert_route_capabilities(app)


def test_motion_sink_graded_on_dependency_not_marker():
    """A route CLAIMING control.mount over a motion sink while enforcing only
    control.capture must not boot. Grading the marker passed this: the declared
    set contained control.mount, so invariant (3) was satisfied by the label."""
    app = FastAPI()

    @app.post("/api/slewish", dependencies=[Depends(require(CAP_CONTROL_CAPTURE))])
    @declare(CAP_CONTROL_MOUNT, reaches={"Telescope.slew"})
    async def slewish():
        return {"ok": True}

    with pytest.raises(RouteCapabilityError, match=r"control\.mount"):
        assert_route_capabilities(app)


def test_field_level_route_may_declare_above_its_dependency_floor():
    """POST /api/config, POST /api/site, PUT /api/site legitimately declare the
    field-level caps they enforce INSIDE the handler (_require_config_field_caps)
    over a view.status route-level floor. Named exemption, keyed by method+path."""
    app = FastAPI()

    @app.post("/api/config", dependencies=[Depends(require(CAP_VIEW_STATUS))])
    @declare(CAP_VIEW_STATUS, CAP_CONFIG_SITE_OPTICS, CAP_CONFIG_SAFETY)
    async def post_config():
        return {"ok": True}

    assert_route_capabilities(app)


def test_field_level_exemption_does_not_cover_another_path():
    """The exemption is per (method, path) -- it does not generalise to a route
    that merely declares the same caps."""
    app = FastAPI()

    @app.post("/api/config/other", dependencies=[Depends(require(CAP_VIEW_STATUS))])
    @declare(CAP_VIEW_STATUS, CAP_CONFIG_SITE_OPTICS, CAP_CONFIG_SAFETY)
    async def other():
        return {"ok": True}

    with pytest.raises(RouteCapabilityError, match=r"config\.site_optics"):
        assert_route_capabilities(app)


def test_field_level_exemption_still_requires_a_real_floor():
    """The exemption covers caps ABOVE a route-level floor, never the ABSENCE of
    one -- an un-gated POST /api/config is still a boot failure."""
    app = FastAPI()

    @app.post("/api/config")
    @declare(CAP_VIEW_STATUS, CAP_CONFIG_SITE_OPTICS)
    async def post_config():
        return {"ok": True}

    with pytest.raises(RouteCapabilityError, match="(?i)floor|no capability"):
        assert_route_capabilities(app)


# ------------------------------------- (4) identity-disclosing GETs need a gate

def test_identity_get_without_auth_dependency_fails():
    """/api/me discloses {role, email, caps}. A GET of it with no require()
    dependency tells an unauthenticated caller who the rig thinks they are."""
    app = FastAPI()

    @app.get("/api/me")
    async def me():
        return {"role": "admin"}

    with pytest.raises(RouteCapabilityError, match="(?i)identity"):
        assert_route_capabilities(app)


def test_identity_get_with_auth_dependency_passes():
    app = FastAPI()

    @app.get("/api/me", dependencies=[Depends(require(CAP_VIEW_STATUS))])
    @declare(CAP_VIEW_STATUS, identity=True)
    async def me():
        return {"role": "admin"}

    assert_route_capabilities(app)


def test_identity_marker_covers_a_path_not_on_the_known_list():
    """A NEW principal/caps GET is covered by declare(identity=True) without the
    path having to be added to IDENTITY_PATHS first."""
    app = FastAPI()

    @app.get("/api/session/whoami")
    @declare(identity=True)
    async def whoami():
        return {"role": "admin"}

    with pytest.raises(RouteCapabilityError, match="(?i)identity"):
        assert_route_capabilities(app)


def test_identity_check_is_not_waived_by_a_prefix_exemption():
    """Invariant (4) runs BEFORE exempt_paths/exempt_prefixes. Silently inheriting
    the /auth prefix exemption is exactly what hid /auth/me for a year."""
    app = FastAPI()

    @app.get("/auth/session-info")
    @declare(identity=True)
    async def session_info():
        return {"role": "admin"}

    with pytest.raises(RouteCapabilityError, match="(?i)identity"):
        assert_route_capabilities(app, exempt_prefixes=("/auth",))


def test_auth_me_is_on_the_named_self_gating_allowlist():
    """/auth/me self-gates (resolve_principal + 401) instead of require(), so it
    passes -- but only because it is NAMED, with its reason, on the allowlist."""
    app = FastAPI()

    @app.get("/auth/me")
    async def auth_me():
        return {"role": "admin"}

    assert_route_capabilities(app, exempt_prefixes=("/auth",))
    assert "/auth/me" in IDENTITY_SELF_GATED
    assert len(IDENTITY_SELF_GATED["/auth/me"]) > 40  # a reason, not a shrug


def test_live_app_passes_assertion():
    """The REAL create_app() must satisfy its own boot assertion (it is called at
    the end of create_app, so a successful import is itself the proof; this makes
    it explicit)."""
    import astrodeck.api.app as app_module
    app = app_module.create_app()
    # Re-running the assertion with the SAME exemptions app.py passes must be a
    # no-op (idempotent). Kept in lockstep with app.py's call site on purpose: a
    # test that exempts more than the app does grades a weaker app than we ship.
    assert_route_capabilities(
        app,
        exempt_paths={"/{path:path}"},
        exempt_prefixes=("/assets", "/auth"))
    # The two identity GETs are actually present -- an assertion that passes
    # because the routes vanished would be worthless.
    paths = {getattr(r, "path", "") for r in app.routes}
    assert {"/api/me", "/auth/me"} <= paths
