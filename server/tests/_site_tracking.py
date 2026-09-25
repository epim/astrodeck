"""The two-synthetic-site comparison: a number counts only if it moves when
only the site moves.

WHY TWO SITES, and why a number has to TRACK to count. The #19 scanner
(``test_no_route_leaks_the_site_coordinates.py``) found that a scan for "does
this body contain 41.23" matches almost any body with enough floats in it, so a
hit there counts only when it is present under site A and absent under site B.
That rule was written for the coordinates themselves. The site also reaches a
reader re-encoded, as an altitude, an azimuth, a floor verdict's numbers or an
ETA (#140, #233), and none of those is a rendering of either coordinate, so
searching for the coordinates cannot find them. What they share is the property
the rule already uses: they move when the site moves and nothing else does.

So this module compares every NUMERIC TOKEN in what a reader was shown under
site A with every numeric token shown under site B, with the clock, the plan
and the session held the same. A token whose count differs between the two
moved with the site. A session id, a timestamp from the injected clock, a
frame count or a target's catalogue number does not move, and is not a hit.

THE CALLER'S HALF OF THE BARGAIN: hold everything else still. Anything that
moves for another reason (a wall-clock timestamp, a freshly minted uuid whose
hex happens to hold a digit run, a random seed) reads as tracking the site and
must be held fixed or left out of the texts compared. The tokenizer skips
digits glued to letters, so a uuid's hex does not normally yield a token, but
the comparison cannot tell a site from any other moving input: the caller has
to make the site the only thing that moves.

Shared by #233's leak test (``test_resume_arm_hold_is_site_free.py``) and
reused by T11 of the same slice; one rule for both, so neither can be
strengthened or weakened without the other.
"""
from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable

# The #19 scanner's two synthetic sites, and chosen for the same reasons:
# both far from 0 and from each other, neither a round number, and no rounding
# of one is a rounding of the other. They are synthetic; no real place is
# meant.
SITE_A = (41.2345678, -73.9876543)
SITE_B = (12.3456789, -45.6789012)

#: A number standing on its own: optionally signed, optionally decimal, and
#: not glued to a letter, a digit or a dot on either side, so "M42", the hex
#: of a uuid and the "3" of "v1.2.3" are not tokens while "42", "-5.4" and
#: the "30" of "30 deg" are.
_NUMBER = re.compile(r"(?<![\w.])-?\d+(?:\.\d+)?(?![\w])")


def site(pair: tuple[float, float]) -> dict:
    """A hub-style site dict for one of the synthetic sites: configured
    (``is_default`` False), so nothing reads it as "no site set"."""
    lat, lon = pair
    return {"name": "synthetic", "latitude": lat, "longitude": lon,
            "elevation_m": 10.0, "is_default": False, "horizon_min_deg": 0.0}


def numeric_tokens(text: str) -> Counter:
    """Every standalone number in ``text``, counted."""
    return Counter(_NUMBER.findall(text))


def tracking_tokens(under_a: Iterable[str],
                    under_b: Iterable[str]) -> dict[str, list[str]]:
    """The numeric tokens whose count differs between what a reader was
    shown under site A and under site B: ``{"a": [...], "b": [...]}``, each
    list the tokens seen more often under that site, and both empty when no
    number tracks the site.

    Pure, so the rule itself can be graded on fabricated texts. BOTH
    subtractions matter: a token only in A is a number the site produced
    there, and a token only in B is the same number under the other site. A
    token present under both, as often, did not move, whatever it is."""
    a = numeric_tokens("\n".join(under_a))
    b = numeric_tokens("\n".join(under_b))
    return {"a": sorted((a - b).elements()), "b": sorted((b - a).elements())}


def any_tracking(found: dict[str, list[str]]) -> bool:
    """True when ``tracking_tokens`` found a number that moved."""
    return bool(found["a"] or found["b"])
