from .objects import CATALOG, SearchResult, search, search_catalog
from .coords import (altaz, format_dec, format_ra, lst_hours, parse_dec,
                     parse_ra, round_az_deg)
from .constellations import constellation_for
from .describe import describe
from .region import objects_in_region, offered_at_fov, region_rows

__all__ = ["CATALOG", "search", "search_catalog", "SearchResult",
           "altaz", "lst_hours",
           "parse_ra", "parse_dec", "format_ra", "format_dec",
           "constellation_for", "describe", "objects_in_region",
           "offered_at_fov", "region_rows", "order_by_observability"]

def order_by_observability(rows: list[dict]) -> list[dict]:
    """Put the targets that are ABOVE THE HORIZON first, keeping the order the
    search already chose within each group.

    A GOTO list answers "what can I point at tonight", and ordering purely by
    magnitude answered a different question: from 37N the brightest rows are the
    Large Magellanic Cloud at -17 deg and the Carina Nebula at -29, so the most
    prominent entries in the list were the permanently unobservable ones
    (reported 2026-08-19).

    A STABLE PARTITION, not a re-sort. The relevance ordering the search
    computed is still the right ordering among things that are up.

    Rows with no `alt` are left where they are: a caller without
    `view.site_derived` gets no altitude, and inventing an order for them would
    leak the horizon they are not allowed to know.
    """
    up, down, unknown = [], [], []
    for r in rows:
        alt = r.get("alt")
        if alt is None:
            unknown.append(r)
        elif alt > 0:
            up.append(r)
        else:
            down.append(r)
    if not up and not down:
        return list(rows)              # nothing judgeable — leave it alone
    return up + unknown + down
