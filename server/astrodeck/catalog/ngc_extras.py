"""OpenNGC columns describe.py wants but ngc.tsv itself doesn't carry --
Hubble type, minor axis, redshift, a filtered NED note -- loaded once from
the sidecar ``build_ngc_extras.py`` precomputed.

Same shape as ``constellations.py``: a dict read from a flat file at import,
nothing parsed or fetched here. See that module and ``build_ngc_extras.py``
for why a sidecar rather than new ngc.tsv columns.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

_DATA_FILE = Path(__file__).with_name("data") / "ngc_extras.tsv"


@dataclass(frozen=True)
class NgcExtra:
    #: OpenNGC's galaxy-subtype code ("Sb", "SBc", ...), or "" if this object
    #: is not a galaxy or OpenNGC published none. Presentation (the code ->
    #: phrase mapping) lives in describe.py, same split as TYPE_PHRASE there.
    hubble: str
    #: Minor axis in arcminutes, or None. Paired with the object's OWN
    #: ``size_arcmin`` (major axis, already on the DSO -- not duplicated
    #: here) by describe.py to decide "edge-on".
    minax: float | None
    #: Redshift z, or None. Sign is carried through UNGUARDED -- 376 of the
    #: source rows are blueshifted, and describe.py is what refuses to turn a
    #: negative or noise-dominated z into a distance.
    redshift: float | None
    #: An NED note, already filtered at build time to the two families worth
    #: a user's time (honest-absence / doubtful-ID, Magellanic Cloud
    #: placement) -- see build_ngc_extras.py's ``worth_surfacing``. "" if
    #: OpenNGC published nothing here or nothing that cleared the filter.
    ned_note: str


def _parse_float(s: str) -> float | None:
    try:
        return float(s) if s else None
    except ValueError:
        return None


def _load() -> dict[str, NgcExtra]:
    """id -> NgcExtra. Never raises: a missing or truncated sidecar degrades
    to an empty map (every lookup returns None, describe() falls back to the
    fields it already had) rather than taking the catalog module down --
    the same defensive shape ``objects.py`` uses for ``ngc.tsv`` itself."""
    out: dict[str, NgcExtra] = {}
    try:
        with open(_DATA_FILE, encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("#") or not line.strip():
                    continue
                parts = line.rstrip("\n").split("\t")
                if len(parts) != 5:
                    continue
                ident, hubble, minax, redshift, note = parts
                out[ident] = NgcExtra(hubble, _parse_float(minax), _parse_float(redshift), note)
    except OSError as e:  # pragma: no cover - defensive
        log.warning("OpenNGC-extras sidecar unavailable (%s); "
                    "extras_for() will return None for everything", e)
    return out


#: id -> NgcExtra, built once at import.
BY_ID: dict[str, NgcExtra] = _load()


def extras_for(obj_id: str) -> NgcExtra | None:
    """The extra OpenNGC fields for this object, or None if it was not in the
    sidecar when build_ngc_extras.py last ran (no OpenNGC row matched, or a
    stale sidecar after a catalog edit)."""
    return BY_ID.get(obj_id)
