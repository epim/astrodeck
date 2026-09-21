"""One question, asked one way: is there a real observing site? (issue #24)

The site defaults to latitude 0, longitude 0 with ``is_default`` True. Every
astronomical computation downstream - sun altitude, dark windows, hour angle,
pier side, meridian flips, sun avoidance - then answers confidently for the
Gulf of Guinea, and each answer looks plausible. A preflight built on it put
NGC 7331 four hours west when it was east and rising.

The audit on #24 found 36 consumers reading the site's coordinates without
asking whether there is one, against 23 that ask. What it also found is WHY:
there was no single predicate. Five call sites had each written their own
check, returning five different shapes, and the dict-vs-pydantic accessor was
copy-pasted at four of them. That is the ground a thirty-seventh grows in.

So: one accessor, one predicate, one place to read the coordinates. The five
existing checks keep their own return shapes - a caller that answers
``(dusk, dawn) | None`` cannot become one that raises - but they all ask the
question through :func:`site_is_set` now, so re-deciding what "no site" means
is one edit rather than five.

THE ONE DELIBERATE EXCEPTION is ``schedule.dark_enough``, which FAILS OPEN: it
returns True at a default site, because refusing to call it dark would stop a
rig that has not been configured yet from ever taking a frame, and that gate's
job is to stop DAYLIGHT imaging rather than to enforce configuration. It says
so at its own definition. It is the only one, and it should stay the only one.
"""
from __future__ import annotations

from typing import Any

__all__ = ["site_get", "site_is_set", "site_lat_lon"]


def site_get(site: "dict | Any"):
    """An accessor that reads either shape the site arrives in.

    ``hub.site`` is a plain dict and ``cfg.site`` is a pydantic ``Site``. Both
    callers are real and neither should have to convert, so the shim lives
    here instead of being written out a fifth time.
    """
    if isinstance(site, dict):
        return site.get
    return lambda k, d=None: getattr(site, k, d)


def site_is_set(site: "dict | Any") -> bool:
    """Has the operator actually saved an observing location?

    ``False`` for the 0,0 default and for anything unreadable. A missing
    ``is_default`` key reads as CONFIGURED, which matches every hand-built site
    dict in the tree: they are written precisely because they mean a real one.
    """
    if site is None:
        return False
    return not site_get(site)("is_default", False)


def site_lat_lon(site: "dict | Any") -> "tuple[float, float] | None":
    """``(latitude, longitude)`` for a configured site, or ``None``.

    The None covers both "no site saved" and "the numbers are unreadable",
    because a caller can do nothing different about the two and every one of
    them was already collapsing them.
    """
    if not site_is_set(site):
        return None
    get = site_get(site)
    # NOT `float(get("latitude", 0.0) or 0.0)`, which is what all four call
    # sites wrote. That idiom turns a null latitude into 0.0 - the equator - so
    # a half-saved site with `is_default` already cleared and no coordinates yet
    # reads as a real location in the Gulf of Guinea, which is the exact failure
    # this predicate exists to prevent, arriving by another door.
    #
    # Without the `or`, `float(None)` raises TypeError and the handler below
    # answers None, so absent and unparseable take one path. An explicit
    # `if lat is None` in front of it was written first and deleted: removing it
    # changed no test, which is this project's own definition of a guard nobody
    # is holding.
    #
    # Zero itself is a legal latitude and still comes through.
    try:
        return float(get("latitude")), float(get("longitude"))
    except (TypeError, ValueError):
        return None
