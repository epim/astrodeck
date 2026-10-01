"""No route takes a place or a pointing in its query string (#520).

A URL is written down by every hop it crosses: the Fly relay's access log, any
reverse proxy, the browser's history. `GET /api/cloudmap/at?alt=&az=` put the
mount's live pointing into the relay's log several times a minute, and a
pointing at a known time is a function of the site's latitude and longitude -
at park the altitude IS the latitude (#140). `GET /api/site/sky?lat=&lon=` did
the same with the site picker's typed coordinates.

Both moved: the telescope's pointing is read server-side, and a picked point or
a typed place travels in a POST body. This file is what keeps the next route
from putting one back. It walks every served route and fails on any that
DECLARES a query parameter named exactly alt, az, lat, lon or site - on every
method, not only GET, because a POST's URL is logged just the same.

Exact names, deliberately. `alt_step`, `min_alt_deg` and `alt_limit` are real
query parameters here and carry no place; a substring match would flag them and
be switched off within a week.

MUTATIONS RUN, 2026-09-29, each in a private scratch copy of server/
(scratchpad/H4-PRIV-mut in the session scratchpad) from a byte backup restored
with its sha256 checked. Output verbatim.

  M1 "restore get_cloudmap_at(alt, az)" - the handler given back
  `alt: float, az: float` beside `ahead_s`. 1 failed,
  test_no_route_declares_a_site_query_parameter:
      AssertionError: these routes take a place or a pointing in their query
      string, where every log on the way writes it down:
          GET /api/cloudmap/at  ['alt', 'az']

  M2 "restore site_sky(lat, lon)" - the handler given back
  `lat: float | None = None, lon: float | None = None`. 1 failed, the same
  case:
          GET /api/site/sky  ['lat', 'lon']

  M3 (the guard on the guard), `_query_params` stops recursing into the
  route's dependencies. 1 failed,
  test_the_walk_can_see_every_way_a_route_declares_one:
      AssertionError: {'GET /aliased': ['lon'], 'GET /direct': ['alt'],
      'POST /on-a-post': ['site']}
      Right contains 1 more item:
      {'GET /through-a-dependency': ['lat']}
  The real-app case passed under M3, which is why the throwaway app exists:
  nothing in the app declares one through a dependency today, so only a probe
  can show the walk would see it.
"""
from __future__ import annotations

from fastapi import Depends, FastAPI, Query

import astrodeck.api.app as app_module
from astrodeck.auth.rbac import iter_app_routes

#: Restated rather than imported from app.py. A test that read the server's own
#: list would pass the day somebody took a name off it.
FORBIDDEN = frozenset({"alt", "az", "lat", "lon", "site"})


def _query_params(dependant) -> list:
    """Every query parameter a route declares, its dependencies' included.

    Walked by hand rather than through FastAPI's ``get_flat_dependant``, which
    has moved between releases (0.141 no longer exports it). A helper that
    stops importing is a helper that stops grading, and this file is not
    allowed to go quiet that way."""
    out = list(dependant.query_params)
    for sub in dependant.dependencies:
        out += _query_params(sub)
    return out


def _offenders(app) -> dict[str, list[str]]:
    """``{"METHOD path": [names]}`` for every route declaring a forbidden
    query parameter, by its public name (the alias when there is one)."""
    found: dict[str, list[str]] = {}
    for route in iter_app_routes(app):
        dependant = getattr(route, "dependant", None)
        methods = getattr(route, "methods", None)
        if dependant is None or not methods:
            continue                 # the static mount, a websocket route
        names = {getattr(f, "alias", None) or f.name
                 for f in _query_params(dependant)}
        hit = sorted(names & FORBIDDEN)
        if hit:
            label = "/".join(sorted(m for m in methods if m != "HEAD"))
            found[f"{label} {route.path}"] = hit
    return found


def _all_query_names(app) -> set[str]:
    names: set[str] = set()
    for route in iter_app_routes(app):
        dependant = getattr(route, "dependant", None)
        if dependant is not None:
            names |= {getattr(f, "alias", None) or f.name
                      for f in _query_params(dependant)}
    return names


def test_no_route_declares_a_site_query_parameter():
    app = app_module.create_app()
    offenders = _offenders(app)
    assert not offenders, (
        "these routes take a place or a pointing in their query string, where "
        "every log on the way writes it down:\n"
        + "\n".join(f"  {k}  {v}" for k, v in sorted(offenders.items()))
        + "\n\nRead a pointing server-side, or take the value in a POST body "
          "(#520).")


def test_the_walk_reaches_the_real_query_parameters():
    """The control on the real app: the walk must SEE query parameters, or the
    case above passes on an empty list. `alt_step` and `az_step` are the dome's
    grid, real and harmless, and they must be visible and NOT flagged."""
    app = app_module.create_app()
    names = _all_query_names(app)
    assert {"alt_step", "az_step", "ahead_s"} <= names, (
        "the route walk no longer sees query parameters the app certainly "
        f"declares, so it can see nothing: {sorted(names)[:20]}")
    assert "alt_step" not in FORBIDDEN and "az_step" not in FORBIDDEN


def test_the_walk_can_see_every_way_a_route_declares_one():
    """The guard on the guard, on a throwaway app built for it. A parameter
    declared directly, one declared under an alias, and one declared by a
    dependency are all caught; `alt_step` is not."""
    probe = FastAPI()

    def _needs_lat(lat: float = 0.0) -> float:
        return lat

    @probe.get("/direct")
    async def direct(alt: float = 0.0):
        return {}

    @probe.get("/aliased")
    async def aliased(longitude: float = Query(0.0, alias="lon")):
        return {}

    @probe.get("/through-a-dependency")
    async def through(v: float = Depends(_needs_lat)):
        return {}

    @probe.post("/on-a-post")
    async def on_a_post(site: str = ""):
        return {}

    @probe.get("/harmless")
    async def harmless(alt_step: float = 2.0, sitemap: str = ""):
        return {}

    found = _offenders(probe)
    assert found == {
        "GET /direct": ["alt"],
        "GET /aliased": ["lon"],
        "GET /through-a-dependency": ["lat"],
        "POST /on-a-post": ["site"],
    }, found
