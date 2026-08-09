"""Who found each object, and when -- loaded once from the sidecar
``build_discoverers.py`` precomputed from Wikidata's P61/P575.

Same shape as ``constellations.py`` and ``ngc_extras.py``: a dict read from a
flat file at import, nothing queried here. See ``build_discoverers.py`` for
the join, the licence (CC0), and how an ambiguous Wikidata match is dropped
rather than guessed -- that filtering already happened by the time this file
reads the sidecar, so every row here is one this repo is confident in.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

_DATA_FILE = Path(__file__).with_name("data") / "discoverers.tsv"


@dataclass(frozen=True)
class Discovery:
    name: str
    #: "YYYY-MM-DD", or "" if Wikidata carried a discoverer but no date.
    date_iso: str
    #: Wikidata's wikibase:timePrecision code: "11" day-exact, "10"
    #: month-exact, "9" year-exact (the coarsest actually seen in this
    #: data), "" if there is no date at all. Presentation (which of those
    #: renders as "on 15 February 1786" vs "in 1714") lives in describe.py.
    precision: str


def _load() -> dict[str, Discovery]:
    """id -> Discovery. Never raises: a missing or truncated sidecar degrades
    to an empty map (every lookup returns None, describe() just drops the
    clause) rather than taking the catalog module down -- the same
    defensive shape ``objects.py`` uses for ``ngc.tsv`` itself."""
    out: dict[str, Discovery] = {}
    try:
        with open(_DATA_FILE, encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("#") or not line.strip():
                    continue
                parts = line.rstrip("\n").split("\t")
                if len(parts) != 4:
                    continue
                ident, name, date_iso, precision = parts
                out[ident] = Discovery(name, date_iso, precision)
    except OSError as e:  # pragma: no cover - defensive
        log.warning("discoverer sidecar unavailable (%s); "
                    "discoverer_for() will return None for everything", e)
    return out


#: id -> Discovery, built once at import.
BY_ID: dict[str, Discovery] = _load()


def discoverer_for(obj_id: str) -> Discovery | None:
    """Who found this object and when, or None -- either Wikidata had
    nothing for it, the match was ambiguous and build_discoverers.py dropped
    it, or (for most Messier ids, and every id build_ngc_extras.py's join
    could not resolve to an NGC/IC code) it was never queryable at all."""
    return BY_ID.get(obj_id)
