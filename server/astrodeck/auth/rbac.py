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

**The marker is a label; only the dependency is a gate.** ``@declare(cap)`` is a
sentence the route's author wrote; ``Depends(require(cap))`` is the thing that
runs. Invariants (1), (3) and (4) are therefore graded on ``_dependency_caps``
-- what the route ACTUALLY enforces -- and a ``@declare`` that no matching
dependency backs is itself a boot failure. (Grading the label meant a route
could advertise ``control.mount`` over a slew while enforcing ``view.status``
and boot clean.) The three field-level config routes that legitimately declare
above their route-level floor are named in ``FIELD_LEVEL_CAP_ROUTES``.

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
IDENTITY_ATTR = "_rbac_identity"  # bool: this route discloses the caller identity

# (4) The identity-disclosing GETs we KNOW about, listed by path so invariant (4)
# holds even when nobody remembers to pass ``identity=True`` to ``declare()``.
# Both channels are checked: path membership OR the marker.
IDENTITY_PATHS = frozenset({"/api/me", "/auth/me"})

# The ONLY identity routes allowed to answer without a ``require(...)``
# dependency, each with the reason it is safe. An identity route that is neither
# gated nor named here fails boot -- inheriting a prefix exemption is not a
# reason, it is the absence of one.
IDENTITY_SELF_GATED: "dict[str, str]" = {
    "/auth/me": (
        "auth/routes.py:auth_me resolves the caller itself via "
        "resolve_principal(request, remote=_scope_is_remote(request)) and raises "
        "401 when that is None -- fail-closed, never a default-admin. It cannot "
        "use require(cap): its whole job is to answer 'who am I' for a caller who "
        "may hold no capability at all. Named HERE rather than left to inherit "
        "the /auth prefix exemption, which is what hid it from this invariant."
    ),
}

# (b) Routes that legitimately ``@declare`` MORE than their route-level
# dependency enforces, because the extra caps are enforced per-FIELD inside the
# handler (``_require_config_field_caps`` / ``_require_site_field_caps`` in
# api/app.py) once the body is parsed. The route-level floor is still a real
# ``require(...)`` dependency -- this exemption covers caps ABOVE a floor, never
# the absence of one. Keyed by (METHOD, path) so it cannot generalise sideways.
FIELD_LEVEL_CAP_ROUTES: "dict[tuple[str, str], str]" = {
    ("POST", "/api/config"): "_require_config_field_caps",
    ("POST", "/api/site"): "_require_site_field_caps",
    ("PUT", "/api/site"): "_require_site_field_caps",
}


class RouteCapabilityError(AssertionError):
    """Raised by ``assert_route_capabilities`` when a route is mis-wired. An
    AssertionError subclass so it surfaces as a hard boot failure."""


def declare(*caps: str, reaches: "frozenset[str] | set[str] | None" = None,
            identity: bool = False):
    """Decorator that stamps a route's endpoint function with the caps it
    requires (mirrored from the ``require(...)`` dependencies) and the motion
    sinks it reaches, so the boot assertion can read them back.

    ``identity=True`` marks a route that returns the caller's principal/caps, so
    invariant (4) covers it without its path having to be added to
    ``IDENTITY_PATHS`` first.

    Applied UNDER the FastAPI route decorator in ``app.py`` (so it decorates the
    raw endpoint coroutine). Returns the function unchanged apart from the three
    marker attributes. Cheap + side-effect-free at request time."""
    caps_fs = frozenset(caps)
    reaches_fs = frozenset(reaches or ())
    identity_flag = bool(identity)

    def _wrap(fn):
        setattr(fn, CAP_ATTR, caps_fs)
        setattr(fn, REACHES_ATTR, reaches_fs)
        setattr(fn, IDENTITY_ATTR, identity_flag)
        return fn
    return _wrap


def _marker_caps(route) -> frozenset[str]:
    """What a route's ``@declare(...)`` LABEL claims it requires.

    A statement of intent by the route's author. It is checked FOR TRUTH against
    ``_dependency_caps`` below; it is never accepted as evidence of enforcement."""
    endpoint = getattr(route, "endpoint", None)
    if endpoint is None:
        return frozenset()
    return getattr(endpoint, CAP_ATTR, None) or frozenset()


def _dependency_caps(route) -> frozenset[str]:
    """What a route ACTUALLY enforces: every ``require(cap)`` dependency wired to
    it. Route-level ``dependencies=[Depends(require(cap))]`` and parameter-level
    ``principal: Principal = Depends(require(cap))`` both land in
    ``route.dependant.dependencies``, so both count.

    This is the enforcement truth the invariants are graded on."""
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


def _route_is_identity(route) -> bool:
    endpoint = getattr(route, "endpoint", None)
    if endpoint is None:
        return False
    return bool(getattr(endpoint, IDENTITY_ATTR, False))


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

        marker = _marker_caps(route)
        enforced = _dependency_caps(route)
        reaches = _route_reaches(route)

        # (4) Identity-disclosing GETs must carry an auth dependency. Checked
        # BEFORE the exemptions: a principal/caps GET that happens to live under
        # an open prefix is the WORST case, not an excused one. /auth/me is the
        # reason -- it self-gates, and inheriting the /auth prefix exemption is
        # what kept it out of this invariant.
        if "GET" in methods and (path in IDENTITY_PATHS or _route_is_identity(route)):
            if not enforced and path not in IDENTITY_SELF_GATED:
                raise RouteCapabilityError(
                    f"identity-disclosing GET {path} carries NO auth dependency "
                    f"-- a principal/caps route must require(...) a capability, "
                    f"or be named in IDENTITY_SELF_GATED with the reason it "
                    f"gates itself")

        # The SPA catch-all + static mount carry no methods we gate / no endpoint
        # with a marker; the auth login dance is exempt by path.
        if _is_exempt(path):
            continue

        # (2) retired-cap tag => fail. Checked on the label AND the dependency:
        # a retired string is wrong wherever it is written.
        written = marker | enforced
        bad = written & RETIRED_CAPS
        if bad:
            raise RouteCapabilityError(
                f"route {path} ({sorted(methods)}) tagged retired capability "
                f"{sorted(bad)} -- retired in W2.1")
        # Any declared cap must be a real capability string.
        unknown = written - ALL_CAPS
        if unknown:
            raise RouteCapabilityError(
                f"route {path} ({sorted(methods)}) declares unknown "
                f"capability {sorted(unknown)}")

        # (b) The label must be backed by a gate. A @declare(cap) with no
        # matching Depends(require(cap)) is a claim nothing keeps -- and it used
        # to SATISFY invariants (1) and (3) all by itself.
        unbacked = marker - enforced
        if unbacked:
            field_level = sorted(m for m in methods
                                 if (m, path) in FIELD_LEVEL_CAP_ROUTES)
            if not field_level:
                raise RouteCapabilityError(
                    f"route {path} ({sorted(methods)}) DECLARES capability "
                    f"{sorted(unbacked)} that no require() dependency enforces "
                    f"(actually enforced: {sorted(enforced)}) -- a @declare "
                    f"marker is a label, not a gate: add "
                    f"Depends(require(...)) or drop the claim")
            if not enforced:
                raise RouteCapabilityError(
                    f"field-level route {path} ({field_level}) declares "
                    f"{sorted(marker)} but enforces NOTHING -- the field-level "
                    f"exemption covers caps ABOVE a real route-level floor, "
                    f"never the absence of one")

        # (3) motion-sink declaration: reaching a motion sink REQUIRES control.mount
        # -- as an ENFORCED dependency, not as a word in the marker.
        if reaches & MOTION_SINKS and CAP_CONTROL_MOUNT not in enforced:
            raise RouteCapabilityError(
                f"route {path} reaches motion sink "
                f"{sorted(reaches & MOTION_SINKS)} but is NOT gated by "
                f"'{CAP_CONTROL_MOUNT}' (enforced: {sorted(enforced)})")

        # (1) mutating route must carry a capability -- again, an enforced one.
        mutating = bool(methods & _MUTATING_METHODS)
        if mutating and not enforced:
            raise RouteCapabilityError(
                f"mutating route {path} ({sorted(methods)}) has NO capability "
                f"enforced -- every control/config write must be gated")


__all__ = [
    "MOTION_SINKS", "CAP_ATTR", "REACHES_ATTR", "IDENTITY_ATTR",
    "IDENTITY_PATHS", "IDENTITY_SELF_GATED", "FIELD_LEVEL_CAP_ROUTES",
    "RouteCapabilityError", "declare", "assert_route_capabilities",
]
