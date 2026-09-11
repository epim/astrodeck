"""Rig-level planning preferences: the quick-plan defaults and the target pool
(#D-FU-1).

WHY A TYPED ``planning`` BLOCK AND NOT A GENERIC ``GET/PUT /api/prefs/{key}``.

The obvious shape for "the phone wants its localStorage on the rig instead" is a
key/value store: one route, any key, any JSON. It was considered and rejected,
because this server already spent the work to make rig data un-corruptible and a
KV route hands all of it back in a single route.

  * ``ConfigPatchBody`` sets ``extra="forbid"``, so a block nobody modelled is
    rejected at binding rather than silently merged.
  * ``_require_config_field_caps`` fails CLOSED on an unmapped block: a new
    block added without a capability 403s instead of inheriting somebody
    else's.
  * ``SavedLocation`` validates every field it stores, so a coordinate that
    reaches the engine is a coordinate that parsed.

A key/value route reverses all three at once, and the first thing it would need
is a size cap (or a phone fills the rig's disk), a key allowlist (or a client
bug writes ``undefined`` forever) and a shape check (or the sheet that reads it
back crashes on a number that used to be a string). That is a typed model with
extra steps, minus the type. Rig data in this server is typed, versioned,
validated and provenance-tracked, and planning preferences are rig data: they
decide what tonight shoots.

THE KEYS THIS REPLACES (``ui/src/next/hubs/sky/finder/prefs.ts``):

  ``astrodeck-next-sky-quick``   -> ``config.planning.quick``
  ``astrodeck-next-sky-pool``    -> ``config.planning.pool``
  ``astrodeck-next-sky-site``    -> ``AppConfig.active_location_id``
  ``astrodeck-next-optics-aux``  -> the optics fields (S7i, NOT here)

WIRE NOTE, ONE RENAME. The browser key ``ditherN`` is ``dither_n`` on the wire
and on disk, because every other field in this server's config is snake_case and
a single camelCase key in the middle of ``astrodeck.json`` is the kind of thing
that gets "fixed" by the next person and silently resets everybody's dither
interval. The client maps it; nothing else changes.

``active_location_id`` IS NOT IN THIS BLOCK, deliberately, and it is not in
``locations.py`` either -- that module has NO active/selected field at all, by
design (precise coordinates live only in its own file and are served only by the
four /api/locations routes). The id sits on ``AppConfig`` beside
``active_profile_id`` and is written by ``POST /api/locations/{id}/apply``
inside that route's existing single ``set_site_and_safety`` mutation, so the
coordinates and the pointer to where they came from move in one atomic save.
A block a settings panel replaces WHOLESALE cannot hold a pointer the panel has
never heard of: the client would echo the block back without it and erase which
location is applied, which is the ``cooling.setpoint_c`` shape exactly.

RBAC, AND WHY IT IS NOT ``config.*``.

READ at ``view.status``. The pool is a list of catalogue ids and the quick
defaults are exposure times and filter names: no site, no media, no driver
endpoints, nothing that narrows where the rig is. A viewer watching a session
already sees the same target ids go past in the status stream, so gating the
list they came from higher would protect nothing.

WRITE at ``control.capture``, and NOT at any ``config.*`` capability, because
the shipped ``operator`` role holds no ``config.*`` at all (see
``auth/capabilities.py``: operator is view.status/preview/weather/site_derived
plus control.capture/guide/mount). These settings decide what tonight shoots -
how long each sub runs, which filters are in, how often it dithers - which is
precisely the operator's job under the rental model that shaped that role. Put
them behind ``config.safety`` or ``config.site_optics`` and the person running
the rig cannot set up their own night.

RELAY FENCE - A DECISION, NOT AN OVERSIGHT. These routes do NOT join
``_REMOTE_LOCAL_ONLY_MUTATION_PREFIXES`` in ``api/app.py``. That fence exists
for credentials and identity roots, for routes that make the server open a
driver/serial endpoint or an outbound fetch, and for the doors into
``config.site`` (which is why ``/api/locations`` is on it - it writes the site
and the safety horizon through the back). Planning preferences are none of
those: the worst a tunnelled session can do here is pick different filters for a
night it can already start over that same tunnel. The path is ``/api/planning``
and NOT ``/api/config/planning`` precisely so that it cannot inherit the
``/api/config`` prefix fence by accident and become LAN-only without anyone
deciding it should be.

TESTS THAT REPOINT THE STORE must repoint ``astrodeck.planning.config_store``
as well as ``astrodeck.config.config_store`` and ``astrodeck.api.app.config_store``:
this module binds the singleton by name at import, the same way ``api/app.py``
does.
"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, ValidationError

from .auth import CAP_CONTROL_CAPTURE, CAP_VIEW_STATUS, require
from .auth.rbac import declare
from .config import PlanningConfig, QuickDefaults, config_store
from .events import bus

router = APIRouter()


class PlanningPatch(BaseModel):
    """A PARTIAL planning update. Both fields are optional at BOTH levels.

    ``extra="forbid"`` on this model and on ``QuickDefaults`` is what makes an
    unknown key a 422 at binding, before the route body runs, so a typo cannot
    be half-applied: nothing is written at all.

    PRESENCE IS ``model_fields_set``, not value-vs-default. A body that never
    mentions ``pool`` leaves the pool alone; a body that sends ``[]`` empties
    it, and those are different requests. An explicit ``null`` for either block
    is read as ABSENT rather than as "clear the block": neither sub-object has a
    meaningful null state (both have defaults for every field), so the only
    thing a null could mean is "reset to the shipped defaults", which is a
    destructive operation nobody asked for and which an accidental
    ``JSON.stringify(undefined)`` would perform by mistake.
    """
    model_config = ConfigDict(extra="forbid")
    quick: QuickDefaults | None = None
    pool: list[str] | None = None


def merge_planning(stored: PlanningConfig,
                   patch: PlanningPatch) -> PlanningConfig:
    """Apply a partial patch to the stored block. Raises ``ValidationError``.

    NESTED PARTIAL, and that is the whole point of this function. Only the
    fields in ``patch.quick.model_fields_set`` are written, so a sheet that
    sends ``{"quick": {"hours": 3}}`` moves the hours and leaves the learned
    per-filter exposures exactly where they were. A wholesale replace of the
    ``quick`` block looks identical in every round-trip test and erases every
    exposure the rig learned the first time a client with a narrower type
    touches an unrelated toggle - the guide-drawer shape this branch has
    already had to fix once.

    ``pool`` IS replaced whole when present, and that is not an inconsistency:
    a list has no field names to merge by, the order IS the user's shortlist
    order, and the only sane meaning of "here is my pool" is "this is my pool".

    The result is built through ``PlanningConfig(...)`` rather than
    ``model_copy``, because ``model_copy`` skips validation and the trim /
    bound / de-duplicate rule for the pool lives in that model's validator. One
    rule, one place - the same reason ``LocationStore.update`` re-constructs
    ``SavedLocation`` after its ``model_copy``.
    """
    quick = stored.quick
    if patch.quick is not None and "quick" in patch.model_fields_set:
        sent = {name: getattr(patch.quick, name)
                for name in patch.quick.model_fields_set}
        quick = quick.model_copy(update=sent)
    pool = stored.pool
    if patch.pool is not None and "pool" in patch.model_fields_set:
        pool = patch.pool
    return PlanningConfig(quick=quick.model_dump(), pool=list(pool))


def _unprocessable(exc: ValidationError) -> HTTPException:
    """A pydantic failure from the MERGE, in the shape FastAPI's own 422 uses.

    The bounds that reject a 300-entry pool or a 100-character target id are
    model validators on ``PlanningConfig``, so they fire when the merged block
    is constructed rather than when the request body binds. Left to propagate
    that is a 500 with a traceback and no sentence for a body the CALLER got
    wrong, so it is translated here - and translated into the same
    ``{"detail": [{"loc", "msg", "type"}]}`` clients already parse, rather than
    a second bespoke error shape.
    """
    return HTTPException(422, detail=[
        {"loc": ["body", *(str(part) for part in err.get("loc", ()))],
         "msg": err.get("msg", "invalid value"),
         "type": err.get("type", "value_error")}
        for err in exc.errors()])


@router.get("/api/planning",
            dependencies=[Depends(require(CAP_VIEW_STATUS))])
@declare(CAP_VIEW_STATUS)
async def get_planning():
    """The full planning block. See the module docstring for why this is
    ``view.status`` and not a config capability."""
    return config_store.cfg().planning.model_dump()


@router.put("/api/planning",
            dependencies=[Depends(require(CAP_CONTROL_CAPTURE))])
@declare(CAP_CONTROL_CAPTURE)
async def put_planning(body: PlanningPatch):
    """Partial update, then the full block back.

    Returns the WHOLE ``PlanningConfig``, never just the fields that moved: a
    partial write whose answer is also partial gives the client no way to see
    what the merge actually decided, and the first thing a caller does with a
    nested partial is check that the block it did not send is still there.

    ``learned`` IS THE CLIENT'S FLAG and is never set implicitly by this route.
    An implicit "any quick write means something was learned" would make the
    field un-clearable through the only route that writes it - answering 200
    and discarding ``learned: false`` forever, which is the exact shape that
    made ``cooling.setpoint_c`` unwritable and cost 19 warm frames. The sheet
    that learns the defaults sends the flag with them.
    """
    def _apply() -> PlanningConfig:
        # Merge FIRST, inside the same worker call as the write, so a body the
        # validators reject never reaches ``set_planning`` and nothing is
        # persisted. The disk write is why this is on a thread at all.
        merged = merge_planning(config_store.cfg().planning, body)
        return config_store.set_planning(merged).planning

    try:
        planning = await asyncio.to_thread(_apply)
    except ValidationError as e:
        raise _unprocessable(e)
    bus.publish("config", version=config_store.cfg().version)
    return planning.model_dump()
