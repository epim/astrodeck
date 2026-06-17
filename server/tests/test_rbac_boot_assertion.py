"""W2 RBAC boot route-assertion tests (T-RBAC-5/6).

``assert_route_capabilities(app)`` must FAIL ``create_app()`` if any mutating
route is un-gated, tagged with a retired cap, declares an unknown cap, or reaches
a motion sink without ``control.mount``. These tests build SYNTHETIC FastAPI apps
(no full create_app) and assert the assertion raises deterministically.
"""
from __future__ import annotations

import pytest
from fastapi import Depends, FastAPI

from astrodeck.auth import CAP_CONTROL_CAPTURE, CAP_CONTROL_MOUNT, require
from astrodeck.auth.rbac import (RouteCapabilityError, assert_route_capabilities,
                                 declare)


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


def test_live_app_passes_assertion():
    """The REAL create_app() must satisfy its own boot assertion (it is called at
    the end of create_app, so a successful import is itself the proof; this makes
    it explicit)."""
    import astrodeck.api.app as app_module
    app = app_module.create_app()
    # Re-running the assertion with the same exemptions must be a no-op (idempotent).
    assert_route_capabilities(
        app,
        exempt_paths={"/{path:path}", "/api/framing/mosaic",
                      "/api/visibility/order"},
        exempt_prefixes=("/assets", "/auth"))
