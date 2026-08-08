"""Constellation lookup for every object in ``objects.CATALOG`` — a dict read
from a flat file at import, nothing computed here.

The values were precomputed by ``build_constellations.py`` (see that file's
docstring for why a sidecar, why precomputed at all, and the epoch trap it
avoids) into ``data/constellations.tsv``. This module's only job is to load
that file once and answer lookups by id — no astropy import, no per-call
precession, safe to call from a hot path (the region query in ``region.py``
calls it once per candidate object on every Atlas pan).
"""
from __future__ import annotations

import logging
from pathlib import Path

log = logging.getLogger(__name__)

_DATA_FILE = Path(__file__).with_name("data") / "constellations.tsv"


def _load() -> dict[str, str]:
    """id -> constellation name. Never raises: a missing or truncated sidecar
    degrades to an empty map (every lookup returns None) rather than taking
    the catalog module down with it — the same defensive shape ``objects.py``
    uses for ``ngc.tsv`` itself."""
    out: dict[str, str] = {}
    try:
        with open(_DATA_FILE, encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("#") or not line.strip():
                    continue
                parts = line.rstrip("\n").split("\t")
                if len(parts) != 2:
                    continue
                ident, name = parts
                out[ident] = name
    except OSError as e:  # pragma: no cover - defensive
        log.warning("constellation sidecar unavailable (%s); "
                    "constellation_for() will return None for everything", e)
    return out


#: id -> constellation name ("Andromeda", not "And"), built once at import.
BY_ID: dict[str, str] = _load()


def constellation_for(obj_id: str) -> str | None:
    """The constellation an object lands in, or None if this id was not in the
    catalog when ``build_constellations.py`` last ran (a stale sidecar after a
    catalog edit — regenerate it rather than treat this as a real gap)."""
    return BY_ID.get(obj_id)
