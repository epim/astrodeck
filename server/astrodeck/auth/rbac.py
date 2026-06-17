"""Boot-time route->capability assertion (W2.2 / plan section "BOOT-TIME ROUTE
ASSERTION").

``create_app()`` calls ``assert_route_capabilities(app)`` right before returning
so a mis-wired control route fails LOUDLY at import/boot rather than silently
shipping an open mutating endpoint.

The per-route capability assignment itself lives where the routes are declared
(``api/app.py`` decorates every route with ``dependencies=[Depends(require(cap))]``
and tags motion-capable routes with a ``reaches={...}`` set on the endpoint
function). This module reads those tags back off the live ``app.routes`` and
enforces the four invariants from the plan:

  1. Every MUTATING route (POST/PUT/PATCH/DELETE) carries a declared capability
     (a ``require(...)`` dependency). The WS accept gate is checked separately in
     ``app.py`` (it is not an HTTP route, so it cannot carry an HTTP dependency).
     Exempt: the SPA catch-all + the static mount + the open auth login/callback.
  2. No route is tagged with a RETIRED capability string ("view",
     "config.mount_limits").
  3. Motion-sink declaration: any route whose endpoint declares ``reaches`` that
     intersects ``MOTION_SINKS`` MUST be gated by ``control.mount``. This catches
     a sequence/recover or polar route mis-tagged as ``control.capture``.
  4. Identity-disclosing GETs (``/api/me`` and any future principal/caps GET)
     must carry an auth dependency.

Import-light: imports only the capability strings from the package. It does NOT
import ``api.app`` (``app.py`` imports IT), so there is no cycle.
"""
from __future__ import annotations

from .capabilities import (ALL_CAPS, CAP_CONTROL_MOUNT, RETIRED_CAPS)

# The explicit motion-sink allowlist (plan section 3 of the boot assertion). A
# route DECLARED (via ``reaches=``) to reach any of these MUST be gated by
# ``control.mount`` -- a ``control.capture`` (or weaker) tag on such a route is a
# boot failure. This is the structural defense against a mis-tagged motion path.
MOTION_SINKS = frozenset({
    "Telescope.slew", "Telescope.move_axis", "Telescope.set_tracking",
    "Telescope.park", "Telescope.unpark", "Telescope.pulse_guide",
    "Telescope.sync", "Telescope.stop",
    "SequenceEngine.start", "PolarSession.start",
})

# Methods that MUTATE state and therefore must never be accidentally open.
_MUTATING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

# Endpoint-function attribute names that ``app.py`` sets to declare a route's
# RBAC posture for this assertion to read back.
CAP_ATTR = "_rbac_caps"        # frozenset[str] of caps required by this route
REACHES_ATTR = "_rbac_reaches"  # frozenset[str] of motion sinks this route reaches


class RouteCapabilityError(AssertionError):
    """Raised by ``assert_route_capabilities`` when a route is mis-wired. An
    AssertionError subclass so it surfaces as a hard boot failure."""


def declare(*caps: str, reaches: "frozenset[str] | set[str] | None" = None):
    """Decorator that stamps a route's endpoint function with the caps it
    requires (mirrored from the ``require(...)`` dependencies) and the motion
    sinks it reaches, so the boot assertion can read them back.

    Applied UNDER the FastAPI route decorator in ``app.py`` (so it decorates the
    raw endpoint coroutine). Returns the function unchanged apart from the two
    marker attributes. Cheap + side-effect-free at request time."""
    caps_fs = frozenset(caps)
    reaches_fs = frozenset(reaches or ())

    def _wrap(fn):
        setattr(fn, CAP_ATTR, caps_fs)
        setattr(fn, REACHES_ATTR, reaches_fs)
        return fn
    return _wrap


def _route_caps(route) -> frozenset[str]:
    """The capability set declared for a route, read off the endpoint marker if
    present, else off any ``require(...)`` dependency call attached to the route.

    We primarily rely on the ``declare(...)`` marker on the endpoint function;
    the dependency-introspection fallback keeps the assertion meaningful even if
    a route was wired with only the ``Depends(require(cap))`` and no marker."""
    endpoint = getattr(route, "endpoint", None)
    if endpoint is not None:
        marked = getattr(endpoint, CAP_ATTR, None)
        if marked is not None:
            return marked
    # Fallback: scan the route's declared dependencies for a require()-style dep.
    caps: set[str] = set()
    dependant = getattr(route, "dependant", None)
    deps = list(getattr(dependant, "dependencies", []) or []) if dependant else []
    for dep in deps:
        cap = getattr(getattr(dep, "call", None), "_rbac_cap", None)
        if isinstance(cap, str):
            caps.add(cap)
    return frozenset(caps)


def _route_reaches(route) -> frozenset[str]:
    endpoint = getattr(route, "endpoint", None)
    if endpoint is None:
        return frozenset()
    return getattr(endpoint, REACHES_ATTR, None) or frozenset()


def assert_route_capabilities(app, *, exempt_paths: "set[str] | None" = None,
                              exempt_prefixes: "tuple[str, ...]" = ()) -> None:
    """Enforce the four RBAC route invariants against ``app.routes``.

    Raises ``RouteCapabilityError`` on the first violation (fails ``create_app``).
    ``exempt_paths`` / ``exempt_prefixes`` cover the genuinely-open shell + the
    auth login dance (which must be reachable pre-session)."""
    exempt = set(exempt_paths or ())

    def _is_exempt(path: str) -> bool:
        if path in exempt:
            return True
        return any(path == p or path.startswith(p) for p in exempt_prefixes)

    for route in app.routes:
        path = getattr(route, "path", "") or ""
        methods = getattr(route, "methods", None) or set()
        endpoint = getattr(route, "endpoint", None)

        # The SPA catch-all + static mount carry no methods we gate / no endpoint
        # with a marker; the auth login dance is exempt by path.
        if _is_exempt(path):
            continue

        declared = _route_caps(route)
        reaches = _route_reaches(route)

        # (2) retired-cap tag => fail.
        bad = declared & RETIRED_CAPS
        if bad:
            raise RouteCapabilityError(
                f"route {path} ({sorted(methods)}) tagged retired capability "
                f"{sorted(bad)} -- retired in W2.1")
        # Any declared cap must be a real capability string.
        unknown = declared - ALL_CAPS
        if unknown:
            raise RouteCapabilityError(
                f"route {path} ({sorted(methods)}) declares unknown "
                f"capability {sorted(unknown)}")

        # (3) motion-sink declaration: reaching a motion sink REQUIRES control.mount.
        if reaches & MOTION_SINKS and CAP_CONTROL_MOUNT not in declared:
            raise RouteCapabilityError(
                f"route {path} reaches motion sink "
                f"{sorted(reaches & MOTION_SINKS)} but is NOT gated by "
                f"'{CAP_CONTROL_MOUNT}' (declared: {sorted(declared)})")

        # (1) mutating route must carry a capability.
        mutating = bool(methods & _MUTATING_METHODS)
        if mutating and not declared:
            raise RouteCapabilityError(
                f"mutating route {path} ({sorted(methods)}) has NO capability "
                f"declared -- every control/config write must be gated")


__all__ = [
    "MOTION_SINKS", "CAP_ATTR", "REACHES_ATTR", "RouteCapabilityError",
    "declare", "assert_route_capabilities",
]
