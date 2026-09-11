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

TWO MORE LAYERS, same degrade rule, same floor. Per
docs/superpowers/backlog/2026-08-08-object-description-sources.md:

  * ``ngc_extras.py`` -- OpenNGC columns ngc.tsv itself never carried
    (Hubble galaxy subtype, minor axis for an edge-on flag, redshift for a
    distance, a filtered NED note). Reaches 12,013 of 13,370 objects (89.9%).
  * ``discoverers.py`` -- Wikidata's discoverer + discovery date, CC0.
    Reaches 7,785 objects (58.2%). An ambiguous Wikidata match was already
    dropped at build time (see build_discoverers.py); every row this module
    can return is one the build trusted.

Residual coverage after both -- objects with nothing but the type/
constellation/magnitude floor -- is still real (mostly IC-numbered stars) and
is still the right answer, not a failure: see this module's original
docstring paragraph above, and build_ngc_extras.py / build_discoverers.py for
why each layer stops where it stops rather than guessing further.
"""
from __future__ import annotations

import re

from .constellations import constellation_for
from .discoverers import discoverer_for
from .ngc_extras import NgcExtra, extras_for
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


#: OpenNGC's Hubble galaxy-subtype code -> a lowercase noun phrase, refining
#: TYPE_PHRASE["GX"] ("galaxy") into something an imager actually learns from
#: ("galaxy in Draco" -> "barred spiral galaxy in Draco"). Only consulted for
#: GX-typed objects that have a non-empty ``NgcExtra.hubble``.
#:
#: The 30 codes and their groupings are OpenNGC's, not invented: unbarred
#: spirals (S*) read as "spiral galaxy", barred (SB*) as "barred spiral
#: galaxy", the de Vaucouleurs intermediate class (SAB*) as "intermediate
#: spiral galaxy" -- the exact phrase the survey found real Wikipedia leads
#: already using for this same class ("NGC 4536 is an intermediate spiral
#: galaxy in the constellation Virgo"). Same loud-failure contract as
#: TYPE_PHRASE/_type_phrase: test_catalog_describe.py asserts every Hubble
#: code actually present in ngc_extras.tsv has an entry here.
HUBBLE_PHRASE: dict[str, str] = {
    "E": "elliptical galaxy",
    "E?": "elliptical galaxy",
    "E-S0": "elliptical-to-lenticular galaxy",
    "S0": "lenticular galaxy",
    "S0-a": "lenticular-to-spiral galaxy",
    "Sa": "spiral galaxy",
    "Sab": "spiral galaxy",
    "Sb": "spiral galaxy",
    "Sbc": "spiral galaxy",
    "Sc": "spiral galaxy",
    "Scd": "spiral galaxy",
    "Sd": "spiral galaxy",
    "Sm": "spiral galaxy",
    "S?": "spiral galaxy",
    "SBa": "barred spiral galaxy",
    "SBab": "barred spiral galaxy",
    "SBb": "barred spiral galaxy",
    "SBbc": "barred spiral galaxy",
    "SBc": "barred spiral galaxy",
    "SBcd": "barred spiral galaxy",
    "SBd": "barred spiral galaxy",
    "SBm": "barred spiral galaxy",
    "SABa": "intermediate spiral galaxy",
    "SABb": "intermediate spiral galaxy",
    "SABc": "intermediate spiral galaxy",
    "SABd": "intermediate spiral galaxy",
    "SABm": "intermediate spiral galaxy",
    "I": "irregular galaxy",
    "IB": "barred irregular galaxy",
    "IAB": "intermediate irregular galaxy",
}


def _hubble_phrase(code: str) -> str:
    """Same contract as ``_type_phrase``: a loud failure naming the missing
    code, never a silent fallback that would leak a raw OpenNGC code
    ("Sbc galaxy in Draco") to a user."""
    try:
        return HUBBLE_PHRASE[code]
    except KeyError:
        raise KeyError(
            f"describe(): no phrase mapped for Hubble type code {code!r} "
            f"-- add it to HUBBLE_PHRASE in describe.py before this galaxy "
            f"can describe itself") from None


#: Axis ratio (major/minor) at or above which a galaxy is called "edge-on".
#: 1,267 of the catalog's galaxies clear this per the survey's own count --
#: NGC 891 and NGC 4565 among them, both far above the threshold (~4.5x and
#: ~5.5x respectively on this catalog's own numbers).
_EDGE_ON_RATIO = 3.0


def _is_edge_on(obj: DSO, extra: NgcExtra | None) -> bool:
    if obj.type != "GX" or extra is None or extra.minax is None or extra.minax <= 0:
        return False
    return obj.size_arcmin / extra.minax >= _EDGE_ON_RATIO


# --------------------------------------------------------------- distance
# Non-relativistic Hubble's law (v = cz, d = v/H0) -- the same simplification
# the survey used to characterise this column (median 228 Mly at H0=70). Good
# enough for one clause in one sentence; nobody is planning a session off the
# third decimal place of a redshift-derived distance.
_C_KM_S = 299792.458
_H0_KM_S_MPC = 70.0
_MPC_TO_MLY = 3.2615637858  # 1 pc = 3.2615637858 ly (IAU)

#: THE BLUESHIFT GUARD. 376 of ngc_extras.tsv's redshift rows are negative
#: (Local Group / Virgo infall -- M31 among them), and a naive c*z/H0 prints
#: a negative distance for every one of them. Below this many megaparsecs,
#: peculiar velocity swamps the Hubble flow anyway, so ANY redshift-derived
#: distance under the threshold is unreliable, not just the negative ones --
#: one guard covers both problems instead of a separate `if z < 0` patch.
_MIN_RELIABLE_MPC = 10.0


def _distance_clause(extra: NgcExtra | None) -> str | None:
    if extra is None or extra.redshift is None:
        return None
    d_mpc = _C_KM_S * extra.redshift / _H0_KM_S_MPC
    if d_mpc < _MIN_RELIABLE_MPC:
        return None
    d_mly = d_mpc * _MPC_TO_MLY
    return f"~{d_mly:.0f} Mly away"


# --------------------------------------------------------------- discovery
_MONTH_NAMES = ["", "January", "February", "March", "April", "May", "June",
                "July", "August", "September", "October", "November", "December"]

#: Wikidata's dates are ISO 8601 with a leading sign: "-0129-01-01" for
#: astronomer Hipparchus's ~130 BCE catalogue entries behind NGC 869/884, the
#: Double Cluster. A plain ``str.split("-")`` shreds that leading sign
#: instead of the year (three real rows hit this: M44, NGC 869, NGC 884 --
#: all Wikidata's astronomical-year-numbering, where year 0 = 1 BCE).
_DATE_RE = re.compile(r"^(-?\d+)-(\d{2})-(\d{2})$")


def _format_year(year: int) -> str:
    """"1786" for a CE year; "130 BCE" for a non-positive one. Wikidata uses
    astronomical year numbering (year 0 = 1 BCE, year -1 = 2 BCE, ...), so
    the historical BCE year is ``1 - year``, not ``-year`` -- get this wrong
    by one and Hipparchus's catalogue entry prints the wrong century."""
    return f"{year}" if year > 0 else f"{1 - year} BCE"


def _discovery_clause(obj_id: str) -> str | None:
    """"discovered by {name} on {day} {Month} {year}" at day precision,
    "... in {Month} {year}" at month precision, "... in {year}" at year
    precision or coarser, "discovered by {name}" if Wikidata had no date at
    all. See discoverers.py / build_discoverers.py for how this got here and
    why an ambiguous Wikidata match never reaches this function."""
    d = discoverer_for(obj_id)
    if d is None:
        return None
    if not d.date_iso:
        return f"discovered by {d.name}"
    m = _DATE_RE.match(d.date_iso)
    if not m:  # unrecognised shape -- degrade rather than crash on a bad row
        return f"discovered by {d.name}"
    year, month, day = int(m[1]), int(m[2]), int(m[3])
    precision = int(d.precision) if d.precision else 0
    year_s = _format_year(year)
    # A day-or-month for a BCE date is never what the actual source recorded
    # (none of the three real BCE rows are more precise than year), so a
    # future one is rendered as a bare year rather than trusting a precision
    # value this old to mean what it means for a modern discovery.
    if precision >= 11 and year > 0:
        return f"discovered by {d.name} on {day} {_MONTH_NAMES[month]} {year_s}"
    if precision == 10 and year > 0:
        return f"discovered by {d.name} in {_MONTH_NAMES[month]} {year_s}"
    return f"discovered by {d.name} in {year_s}"


def describe(obj: DSO) -> str:
    """One sentence: ``{common name — }{type} in {constellation}{ · mag M.m}
    { · distance}{ · discovery}{ · NED note}``.

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
      * no OpenNGC-extras row, or a galaxy with no Hubble code: the type
        phrase stays the plain TYPE_PHRASE ("galaxy"), not a refined one.
      * no redshift, or one too close to reliable (see ``_MIN_RELIABLE_MPC``,
        which also catches every blueshifted row): no distance clause.
      * no Wikidata discoverer, or the match was ambiguous and
        build_discoverers.py already dropped it: no discovery clause.
      * no NED note, or one that didn't clear build_ngc_extras.py's
        allow-list: no trailing note.

    The type clause alone ("Galaxy") is the floor every object clears, which
    is why this can never return "" or a placeholder.
    """
    extra = extras_for(obj.id)
    has_name = obj.name != obj.id

    if obj.type == "GX" and extra is not None and extra.hubble:
        phrase = _hubble_phrase(extra.hubble)
    else:
        phrase = _type_phrase(obj.type)
    if _is_edge_on(obj, extra):
        phrase = f"edge-on {phrase}"

    body = phrase if has_name else phrase.capitalize()
    con = constellation_for(obj.id)
    if con:
        body = f"{body} in {con}"
    if obj.mag < MAG_UNKNOWN:
        body = f"{body} · mag {obj.mag:.1f}"

    distance = _distance_clause(extra)
    if distance:
        body = f"{body} · {distance}"

    discovery = _discovery_clause(obj.id)
    if discovery:
        body = f"{body} · {discovery}"

    if extra is not None and extra.ned_note:
        body = f"{body} · {extra.ned_note}"

    return f"{obj.name} - {body}" if has_name else body
