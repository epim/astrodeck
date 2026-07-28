"""Curated deep-sky target catalog (J2000). RA in hours, Dec in degrees.

The popular imaging targets: full set of crowd-pleaser Messiers plus the
bright NGC/IC favorites. Not an exhaustive survey catalog — it's the list
you'd actually point a rig at.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .difficulty import difficulty_for


@dataclass(frozen=True)
class DSO:
    id: str
    name: str
    type: str
    ra_hours: float
    dec_deg: float
    mag: float
    size_arcmin: float


_RAW = [
    # id, common name, type, RA h, Dec deg, mag, size'
    ("M1", "Crab Nebula", "SNR", 5.5756, 22.0145, 8.4, 7),
    ("M3", "Globular Cluster M3", "GC", 13.7032, 28.3773, 6.2, 18),
    ("M5", "Globular Cluster M5", "GC", 15.3092, 2.0810, 6.7, 23),
    ("M8", "Lagoon Nebula", "EN", 18.0603, -24.3867, 6.0, 90),
    ("M13", "Hercules Cluster", "GC", 16.6949, 36.4613, 5.8, 20),
    ("M16", "Eagle Nebula", "EN", 18.3133, -13.8067, 6.4, 35),
    ("M17", "Omega Nebula", "EN", 18.3464, -16.1717, 6.0, 46),
    ("M20", "Trifid Nebula", "EN", 18.0397, -23.0300, 6.3, 28),
    ("M27", "Dumbbell Nebula", "PN", 19.9934, 22.7212, 7.4, 8),
    ("M31", "Andromeda Galaxy", "GX", 0.7123, 41.2690, 3.4, 190),
    ("M33", "Triangulum Galaxy", "GX", 1.5640, 30.6602, 5.7, 70),
    ("M42", "Orion Nebula", "EN", 5.5881, -5.3911, 4.0, 85),
    ("M45", "Pleiades", "OC", 3.7833, 24.1167, 1.6, 110),
    ("M51", "Whirlpool Galaxy", "GX", 13.4980, 47.1952, 8.4, 11),
    ("M57", "Ring Nebula", "PN", 18.8931, 33.0292, 8.8, 1.4),
    ("M63", "Sunflower Galaxy", "GX", 13.2637, 42.0294, 8.6, 12.6),
    ("M64", "Black Eye Galaxy", "GX", 12.9455, 21.6828, 8.5, 10),
    ("M65", "Leo Triplet Galaxy", "GX", 11.3155, 13.0923, 9.3, 9.8),
    ("M66", "Leo Triplet Galaxy", "GX", 11.3375, 12.9913, 8.9, 9.1),
    ("M74", "Phantom Galaxy", "GX", 1.6116, 15.7836, 9.4, 10.5),
    ("M76", "Little Dumbbell", "PN", 1.7053, 51.5754, 10.1, 2.7),
    ("M78", "Reflection Nebula M78", "RN", 5.7796, 0.0794, 8.3, 8),
    ("M81", "Bode's Galaxy", "GX", 9.9258, 69.0653, 6.9, 26.9),
    ("M82", "Cigar Galaxy", "GX", 9.9312, 69.6797, 8.4, 11.2),
    ("M83", "Southern Pinwheel", "GX", 13.6169, -29.8657, 7.5, 12.9),
    ("M94", "Croc's Eye Galaxy", "GX", 12.8481, 41.1207, 8.2, 11.2),
    ("M97", "Owl Nebula", "PN", 11.2466, 55.0190, 9.9, 3.4),
    ("M101", "Pinwheel Galaxy", "GX", 14.0535, 54.3488, 7.9, 28.8),
    ("M104", "Sombrero Galaxy", "GX", 12.6665, -11.6231, 8.0, 8.7),
    ("M106", "Galaxy M106", "GX", 12.3160, 47.3037, 8.4, 18.6),
    ("M108", "Surfboard Galaxy", "GX", 11.1919, 55.6741, 10.0, 8.7),
    ("M109", "Galaxy M109", "GX", 11.9600, 53.3745, 9.8, 7.6),
    ("M110", "Galaxy M110", "GX", 0.6728, 41.6853, 8.1, 21.9),
    ("NGC 281", "Pacman Nebula", "EN", 0.8775, 56.6369, 7.4, 35),
    ("NGC 253", "Sculptor Galaxy", "GX", 0.7925, -25.2881, 7.2, 27.5),
    ("NGC 869", "Double Cluster (h)", "OC", 2.3167, 57.1333, 5.3, 30),
    ("NGC 884", "Double Cluster (chi)", "OC", 2.3733, 57.1167, 6.1, 30),
    ("NGC 891", "Silver Sliver Galaxy", "GX", 2.3756, 42.3492, 9.9, 13.5),
    ("NGC 1499", "California Nebula", "EN", 4.0508, 36.6189, 6.0, 145),
    ("NGC 2024", "Flame Nebula", "EN", 5.6964, -1.9100, 7.2, 30),
    ("IC 434", "Horsehead Nebula", "DN", 5.6831, -2.4569, 7.3, 60),
    ("NGC 2237", "Rosette Nebula", "EN", 6.5239, 4.9492, 5.5, 80),
    ("NGC 2264", "Cone Nebula / Xmas Tree", "EN", 6.6864, 9.8950, 3.9, 60),
    ("NGC 3372", "Carina Nebula", "EN", 10.7517, -59.8669, 1.0, 120),
    ("NGC 4565", "Needle Galaxy", "GX", 12.6058, 25.9875, 10.4, 15.8),
    ("NGC 6888", "Crescent Nebula", "EN", 20.2017, 38.3550, 7.4, 20),
    ("NGC 6946", "Fireworks Galaxy", "GX", 20.5808, 60.1539, 9.6, 11.5),
    ("NGC 6960", "Western Veil (Witch's Broom)", "SNR", 20.7600, 30.7081, 7.0, 70),
    ("NGC 6992", "Eastern Veil", "SNR", 20.9389, 31.7236, 7.0, 75),
    ("NGC 7000", "North America Nebula", "EN", 20.9767, 44.5306, 4.0, 120),
    ("NGC 7023", "Iris Nebula", "RN", 21.0258, 68.1736, 6.8, 18),
    ("NGC 7293", "Helix Nebula", "PN", 22.4942, -20.8372, 7.6, 25),
    ("NGC 7331", "Deer Lick Galaxy", "GX", 22.6181, 34.4156, 9.5, 10.5),
    ("NGC 7380", "Wizard Nebula", "EN", 22.7903, 58.1294, 7.2, 25),
    ("NGC 7635", "Bubble Nebula", "EN", 23.3461, 61.2011, 10.0, 15),
    ("IC 1396", "Elephant's Trunk Nebula", "EN", 21.6500, 57.5000, 3.5, 170),
    ("IC 1805", "Heart Nebula", "EN", 2.5550, 61.4500, 6.5, 150),
    ("IC 1848", "Soul Nebula", "EN", 2.8550, 60.4167, 6.5, 140),
    ("IC 5070", "Pelican Nebula", "EN", 20.8458, 44.3736, 8.0, 60),
    ("IC 5146", "Cocoon Nebula", "EN", 21.8919, 47.2683, 7.2, 12),
    ("Sh2-155", "Cave Nebula", "EN", 22.9567, 62.5183, 7.7, 50),
    ("M44", "Beehive Cluster", "OC", 8.6733, 19.9889, 3.1, 95),
    ("M92", "Globular Cluster M92", "GC", 17.2854, 43.1359, 6.3, 14),
    ("M11", "Wild Duck Cluster", "OC", 18.8517, -6.2700, 6.3, 14),
]

_TYPE_NAMES = {
    "EN": "Emission Nebula", "RN": "Reflection Nebula", "PN": "Planetary Nebula",
    "DN": "Dark Nebula", "SNR": "Supernova Remnant", "GX": "Galaxy",
    "OC": "Open Cluster", "GC": "Globular Cluster",
}

CATALOG: list[DSO] = [DSO(*row) for row in _RAW]

_SEPARATORS = re.compile(r"[^a-z0-9]+")


def squash_designation(text: str) -> str:
    """Lowercase `text` and drop every separator, so a catalog designation
    matches however the user chose to write it.

    Designations are conventionally written with a space — "M 31", "NGC 3372",
    "Sh2-155" — which is how a beginner copying a name off a website or out of
    a book will type it, and it is what the Atlas placeholder itself suggests.
    The ids in this file are stored inconsistently ("M31" unspaced, "NGC 3372"
    spaced), so a plain substring test failed one convention or the other in
    BOTH directions: "M 31" and "ngc3372" each found nothing.
    """
    return _SEPARATORS.sub("", text.lower())


# (object, squashed id, lowercase name, lowercase type name), built once.
_INDEX: list[tuple[DSO, str, str, str]] = [
    (o, squash_designation(o.id), o.name.lower(), _TYPE_NAMES[o.type].lower())
    for o in CATALOG
]


def search_catalog(query: str, limit: int = 25) -> list[dict]:
    q = query.strip().lower()
    # The DESIGNATION is matched with separators removed from both sides, so
    # spacing and case are irrelevant. That only ever widens the id match:
    # if the raw query was a substring of the raw id, it is still a substring
    # once the same separators are dropped from both, so "m3" -> M3/M31/M33
    # is untouched. `qs` is empty for a punctuation-only query, which must not
    # match everything, hence the explicit guard.
    #
    # NAMES and TYPE NAMES keep the plain substring test on purpose: those are
    # prose ("Andromeda Galaxy", "Emission Nebula") where a space is a real
    # word boundary, and squashing them would let a query straddle two words.
    qs = squash_designation(q)
    results = []
    for o, sid, name, type_name in _INDEX:
        if not q or (qs and qs in sid) or q in name or q in type_name:
            d = difficulty_for(o.id, o.mag, o.size_arcmin)
            results.append({
                "id": o.id, "name": o.name,
                "type": _TYPE_NAMES[o.type],
                "ra_hours": o.ra_hours, "dec_deg": o.dec_deg,
                "mag": o.mag, "size_arcmin": o.size_arcmin,
                "difficulty": d["tier"],
                "surface_brightness": d["surface_brightness"],
                "difficulty_source": d["source"],
            })
    results.sort(key=lambda r: r["mag"])
    return results[:limit]
