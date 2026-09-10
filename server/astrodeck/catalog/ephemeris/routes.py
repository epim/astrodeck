"""The three ephemeris routes.

An ``APIRouter`` rather than three more functions in ``api/app.py``, for the
reason the Sky-Atlas lanes are routers: so no two owners edit the same file.
``api/app.py`` includes this WITH the other atlas routers, before ``create_app``
returns -- the trailing SPA catch-all ``GET /{path:path}`` shadows anything
registered after it (measured: 404).

THE GATES, and why each is the one it is:

* ``GET /api/ephemeris/status`` -- ``view.status``. It reports whether elements
  have been downloaded and how old they are. Not a position, not a horizon, not
  a time when something will be overhead: nothing in it is a function of the
  site, so it rides the plain read capability every screen already holds.
* ``POST /api/ephemeris/refresh`` -- ``config.site_optics``. This is the
  capability that makes the server DIAL AN EXTERNAL HOST, which is why it is
  not a view capability, and why ``/api/ephemeris`` also belongs in
  ``_REMOTE_LOCAL_ONLY_MUTATION_PREFIXES``: a tunnelled cookie must not be able
  to make this box fetch from CelesTrak on somebody else's behalf. Exactly the
  argument that put ``/api/survey/pack`` on that list.
* ``GET /api/satellites/passes`` -- ``view.site_derived``, gated WHOLE, like
  ``GET /api/catalog/tonight``. A pass time is the site: "the ISS clears your
  tree line at 21:04 in the north-west" is a solution for latitude and
  longitude, and there is nothing left of the route once that is removed.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, field_validator

from ...auth import CAP_CONFIG_SITE_OPTICS, CAP_VIEW_SITE_DERIVED, CAP_VIEW_STATUS, require
from ...auth.rbac import declare
from . import elements as _elements

router = APIRouter()


class RefreshBody(BaseModel):
    which: str = "all"

    @field_validator("which")
    @classmethod
    def _known(cls, v: str) -> str:
        if v not in ("all", _elements.SATELLITES, _elements.COMETS):
            raise ValueError("which must be satellites, comets or all")
        return v


@router.get("/api/ephemeris/status",
            dependencies=[Depends(require(CAP_VIEW_STATUS))])
@declare(CAP_VIEW_STATUS)
async def ephemeris_status():
    """Cache state only: what has been downloaded, when, and whether it is old
    enough to be worth a warning. No positions -- a status surface that quietly
    computed one would be a site-derived answer behind a status capability."""
    return _elements.ephemeris_store.snapshot()


@router.post("/api/ephemeris/refresh", status_code=202,
             dependencies=[Depends(require(CAP_CONFIG_SITE_OPTICS))])
@declare(CAP_CONFIG_SITE_OPTICS)
async def ephemeris_refresh(body: RefreshBody):
    """Start a fetch. 202 because the answer is "started", not "done": the MPC
    file is 160 kB and CelesTrak is sometimes slow, and a route that blocked on
    it would hold a worker for the whole request timeout.

    409 while one is already running, so a button pressed twice does not become
    two requests to an upstream that asks us to fetch four times a day."""
    try:
        _elements.ephemeris_store.start_refresh(body.which)
    except _elements.AlreadyFetching as e:
        return JSONResponse({"code": "already_fetching", "which": str(e)},
                            status_code=409)
    return {"started": True, "which": body.which}


@router.get("/api/satellites/passes",
            dependencies=[Depends(require(CAP_VIEW_SITE_DERIVED))])
@declare(CAP_VIEW_SITE_DERIVED)
async def satellite_passes(
    hours: float = Query(24.0, gt=0, le=72),
    min_alt_deg: float = Query(0.0, ge=-90, le=90),
    ids: list[int] | None = Query(None),
):
    """Visible passes for the next ``hours``.

    Off the event loop: the search is thousands of SGP4 evaluations and a
    matching number of frame transforms, and the 2-second status poll (plus the
    relay behind it) is sharing this loop."""
    import asyncio

    from .passes import find_passes
    from .satellites import SatellitesUnavailable

    try:
        return await asyncio.to_thread(find_passes, hours, min_alt_deg, ids,
                                       None)
    except SatellitesUnavailable as e:
        # 409, not 500 and not an empty list: the server is fine, the rig is
        # not ready. An empty list here would read as "no passes tonight",
        # which is a different and false statement.
        raise HTTPException(status_code=409, detail=str(e)) from e
