"""One true sentence per catalog object, composed only from fields it has.

Model: ``brightstars.describe()`` ("β Cas · Cassiopeia") and
``solar_system.describe()`` (" · "-joined clauses, each one a fact the caller
cannot already see), both in this package. Same rule here: every clause is
something a bare id doesn't tell you -- constellation, a readable type,
brightness when it was ever measured -- joined so the sentence degrades
gracefully instead of lying by omission. NEVER an empty string and NEVER a
placeholder like "Unknown object": an object with neither a common name
(13,197 of the catalog's 13,370 rows) nor a published magnitude (1,823 rows,
``objects.MAG_UNKNOWN``) still gets "Type in Constellation" -- true, and the
one fact the catalog has for every single row (see build_constellations.py:
all 13,370 resolve, so the "in Constellation" clause never has to be dropped
in practice, only defensively coded for).
"""
from __future__ import annotations

from .constellations import constellation_for
from .objects import DSO, MAG_UNKNOWN

#: OpenNGC's short type code -> a lowercase noun phrase a sentence can open
#: with or follow an em dash with. Every code ``objects._TYPE_NAMES`` carries
#: must have an entry here -- test_catalog_describe.py asserts the two key
#: sets match, and a future OpenNGC pull that introduces a code nobody added
#: a phrase for must fail a test, not silently print the raw two-letter code
#: to a user ("PN in Draco").
TYPE_PHRASE: dict[str, str] = {
    "EN": "emission nebula",
    "RN": "reflection nebula",
    "PN": "planetary nebula",
    "DN": "dark nebula",
    "SNR": "supernova remnant",
    "GX": "galaxy",
    "OC": "open cluster",
    "GC": "globular cluster",
    # An NGC/IC-numbered star or asterism -- a real catalogue entry (see
    # objects.py's own note on why these are kept rather than dropped), just
    # not a deep-sky object type in the usual imaging-target sense.
    "STAR": "star",
    "OTHER": "object",
}


def _type_phrase(type_code: str) -> str:
    """The mapped phrase, or a loud failure naming the missing code -- never a
    silent fallback to the raw code, which would read like a typo to a
    beginner ("PN in Draco") rather than the sentence this module exists to
    produce."""
    try:
        return TYPE_PHRASE[type_code]
    except KeyError:
        raise KeyError(
            f"describe(): no phrase mapped for catalog type code {type_code!r} "
            f"-- add it to TYPE_PHRASE in describe.py before this object can "
            f"describe itself") from None


def describe(obj: DSO) -> str:
    """One sentence: ``{common name — }{type} in {constellation}{ · mag M.m}``.

    Each clause is dropped, not blanked, when the fact behind it is missing:

      * no common name: the sentence opens with the type instead of a
        designation a beginner would not recognise ("IC 891" says nothing
        "NGC 891" wouldn't) -- detected as ``obj.name == obj.id``, exactly how
        ``objects._load_bulk`` marks "no common name in OpenNGC" (``common or
        ident``).
      * no constellation on file: the "in X" clause is omitted rather than
        printed with a blank. Should not occur in practice -- every id in
        ``CATALOG`` has a row in the precomputed sidecar -- but a stale
        sidecar after a catalog edit must degrade, not crash a describe call.
      * no published magnitude (``objects.MAG_UNKNOWN`` sentinel): the "mag"
        clause is omitted rather than printing a brightness nobody measured.

    The type clause alone ("Galaxy") is the floor every object clears, which
    is why this can never return "" or a placeholder.
    """
    has_name = obj.name != obj.id
    phrase = _type_phrase(obj.type)
    body = phrase if has_name else phrase.capitalize()
    con = constellation_for(obj.id)
    if con:
        body = f"{body} in {con}"
    if obj.mag < MAG_UNKNOWN:
        body = f"{body} · mag {obj.mag:.1f}"
    return f"{obj.name} — {body}" if has_name else body
