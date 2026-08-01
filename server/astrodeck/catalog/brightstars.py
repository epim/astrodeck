"""Named naked-eye stars (J2000) — the targets you reach for when nothing works.

WHY THIS FILE EXISTS. ``objects.py`` is a deep-sky imaging list: no stars at
all. On the night of 2026-07-31 the rig would not come to focus, and the one
move that unsticks that night — point at a bright named star and look at it —
was the one thing Atlas could not do. The user had to be handed raw RA/Dec for
Caph by hand. The targets that matter most when everything else is broken were
exactly the ones missing.

PROVENANCE. Proper names, Bayer/Flamsteed designations, constellations, V
magnitudes and J2000 positions are derived from the IAU Catalog of Star Names
(IAU WGSN, 2022-04-04 edition; CC BY), cut at V <= 4.00 — roughly the naked-eye
limit from a suburban back garden, so "if you can see it and it has a name, it
is in here". Every row was then cross-checked against an INDEPENDENT source,
the Yale Bright Star Catalogue (V/50, Hoffleit & Warren 1991) by HR number:
240/240 agree, median separation 0.45", worst 10.4" (alpha Centauri, whose
2 arcsec/yr proper motion splits the two catalogs' epochs).

FRAME. Fixed J2000/ICRS, the same convention as objects.py, so the hub's
J2000 -> JNOW precession applies to these rows exactly as to a Messier target.
Proper motion is deliberately not modelled: the largest here is Arcturus at
~2.3"/yr, i.e. ~1 arcmin accumulated since J2000 — an order of magnitude inside
a good mount's pointing error and two inside a finder field.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# objects.py owns the one squashing rule ("M 31" and "m31" are the same query),
# and this module is imported lazily FROM there, so importing it back here at
# module level is not a cycle — objects is fully loaded by then.
from .objects import squash_designation


@dataclass(frozen=True)
class NamedStar:
    """One named star. ``bayer`` is the designation token exactly as the IAU
    writes it — a Greek-letter abbreviation ("bet"), optionally with the
    superscript index that separates alpha-1 from alpha-2 ("alf02"), or a bare
    Flamsteed number ("80"), or "" for a star that has neither."""

    name: str
    bayer: str
    con: str
    mag: float
    ra_hours: float
    dec_deg: float


# Greek letters as (IAU 3-letter abbreviation, English name, the letter itself).
# A user types whichever of the three is in front of them: "bet Cas" off a chart
# label, "beta Cas" out of a book, "β Cas" pasted from a web page. All three are
# the same star and all three must resolve — the abbreviation-only search we
# would get for free finds none of the other two.
GREEK: tuple[tuple[str, str, str], ...] = (
    ("alf", "alpha", "α"), ("bet", "beta", "β"),
    ("gam", "gamma", "γ"), ("del", "delta", "δ"),
    ("eps", "epsilon", "ε"), ("zet", "zeta", "ζ"),
    ("eta", "eta", "η"), ("tet", "theta", "θ"),
    ("iot", "iota", "ι"), ("kap", "kappa", "κ"),
    ("lam", "lambda", "λ"), ("mu", "mu", "μ"),
    ("nu", "nu", "ν"), ("ksi", "xi", "ξ"),
    ("omi", "omicron", "ο"), ("pi", "pi", "π"),
    ("rho", "rho", "ρ"), ("sig", "sigma", "σ"),
    ("tau", "tau", "τ"), ("ups", "upsilon", "υ"),
    ("phi", "phi", "φ"), ("chi", "chi", "χ"),
    ("psi", "psi", "ψ"), ("ome", "omega", "ω"),
)

_BY_ABBREV = {abbrev: (abbrev, english, letter) for abbrev, english, letter in GREEK}

# A Greek letter is not [a-z0-9], so squashing a query would DELETE it and turn
# "β Cas" into a bare "cas" that matches every star in Cassiopeia by accident.
# Spell the letter out before any squashing happens.
_GREEK_CHAR_TO_NAME = {letter: english for _, english, letter in GREEK}
_GREEK_CHAR_TO_NAME["ϑ"] = "theta"     # script theta, as some pages write it
_GREEK_CHAR_TO_NAME["ς"] = "sigma"     # final sigma


def latinise_greek(text: str) -> str:
    """Replace any Greek letter with its English name, padded so it stays a
    separate word ("β Cas" -> "beta  Cas"). Applied to the QUERY only; the data
    below is already ASCII."""
    out = []
    for ch in text:
        name = _GREEK_CHAR_TO_NAME.get(ch)
        out.append(f" {name} " if name else ch)
    return "".join(out)


# Constellation abbreviation -> (nominative, genitive). The nominative is what
# the row SAYS ("Cassiopeia" — a beginner does not read "Cas"); the genitive is
# how the designation is properly written ("beta Cassiopeiae") and is therefore
# something a user may well type. Every abbreviation used below must appear
# here; a test asserts it, so a future magnitude cut cannot silently ship a star
# whose row would render a bare "Cas".
CONSTELLATIONS: dict[str, tuple[str, str]] = {
    "And": ("Andromeda", "Andromedae"),
    "Aql": ("Aquila", "Aquilae"),
    "Aqr": ("Aquarius", "Aquarii"),
    "Ari": ("Aries", "Arietis"),
    "Aur": ("Auriga", "Aurigae"),
    "Boo": ("Bootes", "Bootis"),
    "CMa": ("Canis Major", "Canis Majoris"),
    "CMi": ("Canis Minor", "Canis Minoris"),
    "CVn": ("Canes Venatici", "Canum Venaticorum"),
    "Cap": ("Capricornus", "Capricorni"),
    "Car": ("Carina", "Carinae"),
    "Cas": ("Cassiopeia", "Cassiopeiae"),
    "Cen": ("Centaurus", "Centauri"),
    "Cep": ("Cepheus", "Cephei"),
    "Cet": ("Cetus", "Ceti"),
    "Cnc": ("Cancer", "Cancri"),
    "Col": ("Columba", "Columbae"),
    "CrB": ("Corona Borealis", "Coronae Borealis"),
    "Cru": ("Crux", "Crucis"),
    "Crv": ("Corvus", "Corvi"),
    "Cyg": ("Cygnus", "Cygni"),
    "Del": ("Delphinus", "Delphini"),
    "Dra": ("Draco", "Draconis"),
    "Equ": ("Equuleus", "Equulei"),
    "Eri": ("Eridanus", "Eridani"),
    "For": ("Fornax", "Fornacis"),
    "Gem": ("Gemini", "Geminorum"),
    "Gru": ("Grus", "Gruis"),
    "Her": ("Hercules", "Herculis"),
    "Hya": ("Hydra", "Hydrae"),
    "LMi": ("Leo Minor", "Leonis Minoris"),
    "Leo": ("Leo", "Leonis"),
    "Lep": ("Lepus", "Leporis"),
    "Lib": ("Libra", "Librae"),
    "Lyr": ("Lyra", "Lyrae"),
    "Oph": ("Ophiuchus", "Ophiuchi"),
    "Ori": ("Orion", "Orionis"),
    "Pav": ("Pavo", "Pavonis"),
    "Peg": ("Pegasus", "Pegasi"),
    "Per": ("Perseus", "Persei"),
    "Phe": ("Phoenix", "Phoenicis"),
    "PsA": ("Piscis Austrinus", "Piscis Austrini"),
    "Psc": ("Pisces", "Piscium"),
    "Pup": ("Puppis", "Puppis"),
    "Sco": ("Scorpius", "Scorpii"),
    "Ser": ("Serpens", "Serpentis"),
    "Sgr": ("Sagittarius", "Sagittarii"),
    "Tau": ("Taurus", "Tauri"),
    "TrA": ("Triangulum Australe", "Trianguli Australis"),
    "Tri": ("Triangulum", "Trianguli"),
    "UMa": ("Ursa Major", "Ursae Majoris"),
    "UMi": ("Ursa Minor", "Ursae Minoris"),
    "Vel": ("Vela", "Velorum"),
    "Vir": ("Virgo", "Virginis"),
}

# name, bayer, constellation, V mag, RA hours (J2000), Dec deg (J2000)
_RAW: tuple[tuple[str, str, str, float, float, float], ...] = (
    ("Alpheratz", "alf", "And", 2.07, 0.139794, 29.090431),
    ("Mirach", "bet", "And", 2.07, 1.162201, 35.620557),
    ("Almach", "gam", "And", 2.10, 2.064987, 42.329725),
    ("Nembus", "51", "And", 3.59, 1.633210, 48.628214),
    ("Altair", "alf", "Aql", 0.76, 19.846388, 8.868321),
    ("Tarazed", "gam", "Aql", 2.72, 19.770994, 10.613262),
    ("Okab", "zet", "Aql", 2.99, 19.090169, 13.863477),
    ("Alshain", "bet", "Aql", 3.71, 19.921887, 6.406763),
    ("Sadalsuud", "bet", "Aqr", 2.90, 21.525981, -5.571176),
    ("Sadalmelik", "alf", "Aqr", 2.95, 22.096399, -0.319849),
    ("Skat", "del", "Aqr", 3.27, 22.910837, -15.820827),
    ("Albali", "eps", "Aqr", 3.78, 20.794598, -9.495775),
    ("Sadachbia", "gam", "Aqr", 3.86, 22.360938, -1.387334),
    ("Hamal", "alf", "Ari", 2.01, 2.119557, 23.462418),
    ("Sheratan", "bet", "Ari", 2.70, 1.910670, 20.808031),
    ("Bharani", "41", "Ari", 3.61, 2.833065, 27.260507),
    ("Capella", "alf", "Aur", 0.08, 5.278155, 45.997991),
    ("Menkalinan", "bet", "Aur", 1.90, 5.992145, 44.947433),
    ("Mahasim", "tet", "Aur", 2.65, 5.995353, 37.212585),
    ("Hassaleh", "iot", "Aur", 2.69, 4.949895, 33.166100),
    ("Almaaz", "eps", "Aur", 3.03, 5.032815, 43.823307),
    ("Haedus", "eta", "Aur", 3.18, 5.108581, 41.234476),
    ("Saclateni", "zet", "Aur", 3.69, 5.041302, 41.075839),
    ("Arcturus", "alf", "Boo", -0.05, 14.261020, 19.182409),
    ("Izar", "eps", "Boo", 2.35, 14.749784, 27.074207),
    ("Muphrid", "eta", "Boo", 2.68, 13.911411, 18.397717),
    ("Seginus", "gam", "Boo", 3.04, 14.534631, 38.308251),
    ("Nekkar", "bet", "Boo", 3.49, 15.032434, 40.390567),
    ("Sirius", "alf", "CMa", -1.45, 6.752477, -16.716116),
    ("Adhara", "eps", "CMa", 1.50, 6.977097, -28.972086),
    ("Wezen", "del", "CMa", 1.83, 7.139857, -26.393200),
    ("Mirzam", "bet", "CMa", 1.98, 6.378329, -17.955919),
    ("Aludra", "eta", "CMa", 2.45, 7.401584, -29.303106),
    ("Furud", "zet", "CMa", 3.02, 6.338553, -30.063367),
    ("Nganurganity", "sig", "CMa", 3.49, 7.028652, -27.934830),
    ("Procyon", "alf", "CMi", 0.40, 7.655033, 5.224993),
    ("Gomeisa", "bet", "CMi", 2.89, 7.452512, 8.289316),
    ("Cor Caroli", "alf02", "CVn", 2.89, 12.933796, 38.318376),
    ("Deneb Algedi", "del", "Cap", 2.85, 21.784012, -16.127287),
    ("Dabih", "bet01", "Cap", 3.05, 20.350187, -14.781405),
    ("Algedi", "alf02", "Cap", 3.58, 20.300904, -12.544852),
    ("Nashira", "gam", "Cap", 3.69, 21.668182, -16.662308),
    ("Canopus", "alf", "Car", -0.62, 6.399197, -52.695661),
    ("Miaplacidus", "bet", "Car", 1.67, 9.219994, -69.717208),
    ("Avior", "eps", "Car", 1.86, 8.375232, -59.509484),
    ("Aspidiske", "iot", "Car", 2.21, 9.284835, -59.275232),
    ("Schedar", "alf", "Cas", 2.24, 0.675123, 56.537331),
    ("Caph", "bet", "Cas", 2.28, 0.152968, 59.149781),
    ("Ruchbah", "del", "Cas", 2.66, 1.430264, 60.235284),
    ("Segin", "eps", "Cas", 3.35, 1.906590, 63.670101),
    ("Achird", "eta", "Cas", 3.46, 0.818414, 57.815187),
    ("Fulu", "zet", "Cas", 3.69, 0.616190, 53.896908),
    ("Rigil Kentaurus", "alf", "Cen", -0.01, 14.660138, -60.833975),
    ("Hadar", "bet", "Cen", 0.61, 14.063724, -60.373035),
    ("Toliman", "alf", "Cen", 1.35, 14.659740, -60.837528),
    ("Menkent", "tet", "Cen", 2.06, 14.111374, -36.369958),
    ("Alderamin", "alf", "Cep", 2.45, 21.309659, 62.585574),
    ("Errai", "gam", "Cep", 3.21, 23.655777, 77.632313),
    ("Alfirk", "bet", "Cep", 3.23, 21.477666, 70.560715),
    ("Diphda", "bet", "Cet", 2.04, 0.726492, -17.986606),
    ("Menkar", "alf", "Cet", 2.54, 3.037992, 4.089737),
    ("Kaffaljidhma", "gam", "Cet", 3.56, 2.721678, 3.235816),
    ("Baten Kaitos", "zet", "Cet", 3.74, 1.857676, -10.335044),
    ("Tarf", "bet", "Cnc", 3.53, 8.275256, 9.185544),
    ("Asellus Australis", "del", "Cnc", 3.94, 8.744750, 18.154309),
    ("Phact", "alf", "Col", 2.65, 5.660817, -34.074110),
    ("Wazn", "bet", "Col", 3.12, 5.849331, -35.768310),
    ("Alphecca", "alf", "CrB", 2.22, 15.578130, 26.714693),
    ("Nusakan", "bet", "CrB", 3.90, 15.463814, 29.105699),
    ("Mimosa", "bet", "Cru", 1.25, 12.795351, -59.688764),
    ("Acrux", "alf", "Cru", 1.33, 12.443304, -63.099093),
    ("Gacrux", "gam", "Cru", 1.59, 12.519433, -57.113213),
    ("Imai", "del", "Cru", 2.75, 12.252421, -58.748927),
    ("Ginan", "eps", "Cru", 3.59, 12.356003, -60.401147),
    ("Gienah", "gam", "Crv", 2.58, 12.263436, -17.541929),
    ("Kraz", "bet", "Crv", 2.65, 12.573121, -23.396759),
    ("Algorab", "del", "Crv", 2.94, 12.497738, -16.515431),
    ("Deneb", "alf", "Cyg", 1.25, 20.690532, 45.280339),
    ("Sadr", "gam", "Cyg", 2.23, 20.370473, 40.256679),
    ("Aljanah", "eps", "Cyg", 2.48, 20.770190, 33.970257),
    ("Fawaris", "del", "Cyg", 2.90, 19.749577, 45.130810),
    ("Albireo", "bet", "Cyg", 3.37, 19.512023, 27.959692),
    ("Rotanev", "bet", "Del", 3.64, 20.625816, 14.595115),
    ("Sualocin", "alf", "Del", 3.86, 20.660635, 15.912073),
    ("Eltanin", "gam", "Dra", 2.24, 17.943436, 51.488896),
    ("Athebyne", "eta", "Dra", 2.73, 16.399857, 61.514214),
    ("Rastaban", "bet", "Dra", 2.79, 17.507212, 52.301389),
    ("Altais", "del", "Dra", 3.07, 19.209250, 67.661541),
    ("Aldhibah", "zet", "Dra", 3.17, 17.146443, 65.714684),
    ("Edasich", "iot", "Dra", 3.29, 15.415493, 58.966063),
    ("Thuban", "alf", "Dra", 3.67, 14.073153, 64.375851),
    ("Grumium", "ksi", "Dra", 3.73, 17.892147, 56.872646),
    ("Giausar", "lam", "Dra", 3.82, 11.523395, 69.331075),
    ("Kitalpha", "alf", "Equ", 3.92, 21.263730, 5.247865),
    ("Achernar", "alf", "Eri", 0.45, 1.628568, -57.236753),
    ("Cursa", "bet", "Eri", 2.78, 5.130829, -5.086446),
    ("Acamar", "tet01", "Eri", 2.88, 2.971021, -40.304672),
    ("Zaurak", "gam", "Eri", 2.97, 3.967157, -13.508516),
    ("Rana", "del", "Eri", 3.53, 3.720806, -9.763392),
    ("Ran", "eps", "Eri", 3.73, 3.548846, -9.458259),
    ("Theemin", "ups02", "Eri", 3.81, 4.592511, -30.562341),
    ("Azha", "eta", "Eri", 3.89, 2.940458, -8.898145),
    ("Beemim", "ups03", "Eri", 3.97, 4.400616, -34.016848),
    ("Dalim", "alf", "For", 3.86, 3.201258, -28.987620),
    ("Pollux", "bet", "Gem", 1.16, 7.755264, 28.026199),
    ("Alhena", "gam", "Gem", 1.93, 6.628531, 16.399280),
    ("Castor", "alf", "Gem", 1.98, 7.576629, 31.888276),
    ("Tejat", "mu", "Gem", 2.87, 6.382674, 22.513583),
    ("Mebsuta", "eps", "Gem", 3.06, 6.732202, 25.131127),
    ("Propus", "eta", "Gem", 3.32, 6.247960, 22.506794),
    ("Alzirr", "ksi", "Gem", 3.35, 6.754823, 12.895592),
    ("Wasat", "del", "Gem", 3.52, 7.335383, 21.982316),
    ("Alnair", "alf", "Gru", 1.73, 22.137218, -46.960974),
    ("Tiaki", "bet", "Gru", 2.12, 22.711125, -46.884576),
    ("Aldhanab", "gam", "Gru", 3.00, 21.898813, -37.364855),
    ("Kornephoros", "bet", "Her", 2.78, 16.503667, 21.489611),
    ("Sarin", "del", "Her", 3.12, 17.250531, 24.839204),
    ("Rasalgethi", "alf01", "Her", 3.37, 17.244127, 14.390333),
    ("Alphard", "alf", "Hya", 1.99, 9.459790, -8.658602),
    ("Ashlesha", "eps", "Hya", 3.49, 8.779586, 6.418809),
    ("Ukdah", "iot", "Hya", 3.90, 9.664267, -1.142810),
    ("Praecipua", "46", "LMi", 3.79, 10.888529, 34.214872),
    ("Regulus", "alf", "Leo", 1.36, 10.139531, 11.967209),
    ("Denebola", "bet", "Leo", 2.14, 11.817661, 14.572058),
    ("Zosma", "del", "Leo", 2.56, 11.235139, 20.523718),
    ("Algieba", "gam01", "Leo", 2.61, 10.332876, 19.841489),
    ("Chertan", "tet", "Leo", 3.33, 11.237335, 15.429571),
    ("Adhafera", "zet", "Leo", 3.43, 10.278171, 23.417312),
    ("Subra", "omi", "Leo", 3.52, 9.685843, 9.892308),
    ("Rasalas", "mu", "Leo", 3.88, 9.879394, 26.006953),
    ("Arneb", "alf", "Lep", 2.58, 5.545504, -17.822289),
    ("Nihal", "bet", "Lep", 2.83, 5.470756, -20.759441),
    ("Zubeneschamali", "bet", "Lib", 2.61, 15.283448, -9.382914),
    ("Zubenelgenubi", "alf02", "Lib", 2.75, 14.847976, -16.041777),
    ("Brachium", "sig", "Lib", 3.25, 15.067838, -25.281961),
    ("Zubenelhakrabi", "gam", "Lib", 3.91, 15.592105, -14.789536),
    ("Vega", "alf", "Lyr", 0.03, 18.615649, 38.783689),
    ("Sulafat", "gam", "Lyr", 3.25, 18.982395, 32.689557),
    ("Sheliak", "bet", "Lyr", 3.60, 18.834665, 33.362668),
    ("Rasalhague", "alf", "Oph", 2.08, 17.582242, 12.560035),
    ("Sabik", "eta", "Oph", 2.43, 17.172969, -15.724907),
    ("Yed Prior", "del", "Oph", 2.73, 16.239094, -3.694323),
    ("Cebalrai", "bet", "Oph", 2.76, 17.724542, 4.567300),
    ("Yed Posterior", "eps", "Oph", 3.23, 16.305358, -4.692510),
    ("Marfik", "lam", "Oph", 3.82, 16.515230, 1.983888),
    ("Rigel", "bet", "Ori", 0.18, 5.242298, -8.201638),
    ("Betelgeuse", "alf", "Ori", 0.45, 5.919529, 7.407064),
    ("Bellatrix", "gam", "Ori", 1.64, 5.418851, 6.349703),
    ("Alnilam", "eps", "Ori", 1.69, 5.603559, -1.201919),
    ("Alnitak", "zet", "Ori", 1.74, 5.679313, -1.942574),
    ("Saiph", "kap", "Ori", 2.07, 5.795941, -9.669605),
    ("Mintaka", "del", "Ori", 2.25, 5.533444, -0.299095),
    ("Hatysa", "iot", "Ori", 2.80, 5.590551, -5.909901),
    ("Tabit", "pi03", "Ori", 3.19, 4.830670, 6.961275),
    ("Meissa", "lam", "Ori", 3.39, 5.585632, 9.934156),
    ("Peacock", "alf", "Pav", 1.94, 20.427460, -56.735090),
    ("Enif", "eps", "Peg", 2.38, 21.736432, 9.875009),
    ("Scheat", "bet", "Peg", 2.44, 23.062905, 28.082785),
    ("Markab", "alf", "Peg", 2.49, 23.079348, 15.205267),
    ("Algenib", "gam", "Peg", 2.83, 0.220598, 15.183594),
    ("Matar", "eta", "Peg", 2.93, 22.716705, 30.221244),
    ("Homam", "zet", "Peg", 3.41, 22.691034, 10.831363),
    ("Sadalbari", "mu", "Peg", 3.51, 22.833387, 24.601577),
    ("Biham", "tet", "Peg", 3.52, 22.169996, 6.197863),
    ("Mirfak", "alf", "Per", 1.79, 3.405381, 49.861179),
    ("Algol", "bet", "Per", 2.09, 3.136148, 40.955648),
    ("Miram", "eta", "Per", 3.77, 2.844947, 55.895497),
    ("Misam", "kap", "Per", 3.79, 3.158270, 44.857541),
    ("Atik", "omi", "Per", 3.84, 3.738648, 32.288240),
    ("Menkib", "ksi", "Per", 3.98, 3.982750, 35.791032),
    ("Ankaa", "alf", "Phe", 2.40, 0.438063, -42.306084),
    ("Fomalhaut", "alf", "PsA", 1.17, 22.960846, -29.622237),
    ("Alrescha", "alf", "Psc", 3.82, 2.034118, 2.763735),
    ("Alpherg", "eta", "Psc", 3.83, 1.524725, 15.345823),
    ("Naos", "zet", "Pup", 2.21, 8.059735, -40.003148),
    ("Tureis", "rho", "Pup", 2.83, 8.125736, -24.304324),
    ("Azmidi", "ksi", "Pup", 3.45, 7.821571, -24.859786),
    ("Antares", "alf", "Sco", 1.06, 16.490128, -26.432003),
    ("Sargas", "tet", "Sco", 1.86, 17.621981, -42.997824),
    ("Shaula", "lam", "Sco", 2.08, 17.560144, -37.103824),
    ("Dschubba", "del", "Sco", 2.29, 16.005557, -22.621710),
    ("Larawag", "eps", "Sco", 2.29, 16.836059, -34.293232),
    ("Acrab", "bet", "Sco", 2.56, 16.090620, -19.805453),
    ("Lesath", "ups", "Sco", 2.70, 17.512732, -37.295813),
    ("Paikauhale", "tau", "Sco", 2.82, 16.598042, -28.216017),
    ("Fang", "pi", "Sco", 2.89, 15.980865, -26.114108),
    ("Alniyat", "sig", "Sco", 2.90, 16.353143, -25.592792),
    ("Xamidimura", "mu01", "Sco", 3.00, 16.864509, -38.047380),
    ("Fuyue", "", "Sco", 3.19, 17.830967, -37.043305),
    ("Pipirima", "mu02", "Sco", 3.56, 16.872263, -38.017535),
    ("Iklil", "rho", "Sco", 3.87, 15.948077, -29.214073),
    ("Unukalhai", "alf", "Ser", 2.63, 15.737798, 6.425629),
    ("Kaus Australis", "eps", "Sgr", 1.79, 18.402866, -34.384616),
    ("Nunki", "sig", "Sgr", 2.05, 18.921091, -26.296724),
    ("Ascella", "zet", "Sgr", 2.60, 19.043536, -29.880063),
    ("Kaus Media", "del", "Sgr", 2.72, 18.349901, -29.828104),
    ("Kaus Borealis", "lam", "Sgr", 2.82, 18.466178, -25.421701),
    ("Albaldah", "pi", "Sgr", 2.88, 19.162731, -21.023615),
    ("Alnasl", "gam02", "Sgr", 2.98, 18.096802, -30.424100),
    ("Polis", "mu", "Sgr", 3.84, 18.229391, -21.058832),
    ("Arkab Prior", "bet01", "Sgr", 3.96, 19.377303, -44.458959),
    ("Rukbat", "alf", "Sgr", 3.96, 19.398105, -40.615940),
    ("Aldebaran", "alf", "Tau", 0.87, 4.598678, 16.509302),
    ("Elnath", "bet", "Tau", 1.65, 5.438198, 28.607452),
    ("Alcyone", "eta", "Tau", 2.85, 3.791410, 24.105136),
    ("Tianguan", "zet", "Tau", 2.97, 5.627413, 21.142544),
    ("Ain", "eps", "Tau", 3.53, 4.476944, 19.180435),
    ("Atlas", "27", "Tau", 3.62, 3.819373, 24.053415),
    ("Prima Hyadum", "gam", "Tau", 3.65, 4.329890, 15.627643),
    ("Electra", "17", "Tau", 3.72, 3.747927, 24.113336),
    ("Chamukuy", "tet02", "Tau", 3.73, 4.477706, 15.870882),
    ("Secunda Hyadum", "del01", "Tau", 3.78, 4.382248, 17.542514),
    ("Maia", "20", "Tau", 3.87, 3.763780, 24.367751),
    ("Atria", "alf", "TrA", 1.91, 16.811082, -69.027712),
    ("Mothallah", "alf", "Tri", 3.42, 1.884697, 29.578826),
    ("Alioth", "eps", "UMa", 1.76, 12.900486, 55.959823),
    ("Dubhe", "alf", "UMa", 1.81, 11.062131, 61.751035),
    ("Alkaid", "eta", "UMa", 1.85, 13.792344, 49.313267),
    ("Mizar", "zet", "UMa", 2.23, 13.398762, 54.925362),
    ("Merak", "bet", "UMa", 2.34, 11.030688, 56.382426),
    ("Phecda", "gam", "UMa", 2.41, 11.897179, 53.694758),
    ("Tania Australis", "mu", "UMa", 3.06, 10.372150, 41.499519),
    ("Talitha", "iot", "UMa", 3.12, 8.986793, 48.041826),
    ("Megrez", "del", "UMa", 3.32, 12.257100, 57.032615),
    ("Muscida", "omi", "UMa", 3.35, 8.504409, 60.718170),
    ("Tania Borealis", "lam", "UMa", 3.45, 10.284940, 42.914356),
    ("Alula Borealis", "nu", "UMa", 3.49, 11.307982, 33.094305),
    ("Taiyangshou", "chi", "UMa", 3.69, 11.767504, 47.779406),
    ("Alcor", "80", "UMa", 3.99, 13.420427, 54.987954),
    ("Kochab", "bet", "UMi", 2.07, 14.845090, 74.155504),
    ("Polaris", "alf", "UMi", 2.13, 2.530304, 89.264109),
    ("Pherkad", "gam", "UMi", 3.00, 15.345477, 71.834017),
    ("Alsephina", "del", "Vel", 1.99, 8.745063, -54.708819),
    ("Suhail", "lam", "Vel", 2.23, 9.133266, -43.432589),
    ("Markeb", "kap", "Vel", 2.47, 9.368560, -55.010667),
    ("Spica", "alf", "Vir", 0.98, 13.419883, -11.161319),
    ("Vindemiatrix", "eps", "Vir", 2.85, 13.036277, 10.959149),
    ("Heze", "zet", "Vir", 3.38, 13.578220, -0.595820),
    ("Minelauva", "del", "Vir", 3.39, 12.926725, 3.397470),
    ("Porrima", "gam", "Vir", 3.44, 12.694345, -1.449373),
    ("Zavijava", "bet", "Vir", 3.59, 11.844922, 1.764717),
)

STARS: list[NamedStar] = [NamedStar(*row) for row in _RAW]

_SPLIT_BAYER = re.compile(r"^([a-z]*)(\d*)$")


def _split_bayer(bayer: str) -> tuple[str, int | None]:
    """("alf02") -> ("alf", 2); ("bet") -> ("bet", None); ("80") -> ("80", None)."""
    m = _SPLIT_BAYER.match(bayer)
    if not m:
        return bayer, None
    letters, digits = m[1], m[2]
    if not letters:
        return digits, None            # a bare Flamsteed number
    return letters, int(digits) if digits else None


def designation(star: NamedStar) -> str:
    """The designation as a chart prints it: "beta Cas" -> "β Cas",
    "alf02 Lib" -> "α2 Lib", "80 UMa" -> "80 UMa".

    Empty for a star that has no Bayer or Flamsteed letter at all (G Sco is the
    only one this bright). Returning a bare "Sco" there would print the
    constellation abbreviation in the slot where a designation goes, which
    looks exactly like a designation and is not one."""
    stem, index = _split_bayer(star.bayer)
    if not stem:
        return ""
    letter = _BY_ABBREV[stem][2] if stem in _BY_ABBREV else stem
    suffix = str(index) if index else ""
    return f"{letter}{suffix} {star.con}"


def describe(star: NamedStar) -> str:
    """The one line the Atlas row shows next to the name. Carries the two facts
    the name alone does not: which designation this is (so a chart can be
    matched to it) and WHERE in the sky to look for it, spelled out."""
    nominative = CONSTELLATIONS[star.con][0]
    desig = designation(star)
    return f"{desig} · {nominative}" if desig else nominative


def search_keys(star: NamedStar) -> list[str]:
    """Every spelling of this star that must resolve, squashed to [a-z0-9].

    Built from the cross-product of the letter's three spellings ("bet"/"beta"/
    the letter, already latinised) and the constellation's three ("Cas" /
    "Cassiopeia" / "Cassiopeiae"), with and without the superscript index. That
    is the set a real user actually types; a single canonical form would send
    two thirds of them to "No matches" — which is precisely how this night went
    wrong for Caph.
    """
    keys = [squash_designation(star.name)]
    stem, index = _split_bayer(star.bayer)
    if not stem:
        # No designation to build keys from. Emitting the constellation alone
        # would make "Sco" an EXACT match for this one star and rank it above
        # every other star in Scorpius — the abbreviation still finds them all,
        # as prose, through the description.
        return keys
    stems = {stem}
    if stem in _BY_ABBREV:
        stems.add(_BY_ABBREV[stem][1])           # the English name
    nominative, genitive = CONSTELLATIONS[star.con]
    cons = {star.con, nominative, genitive}
    for s in stems:
        for c in cons:
            keys.append(squash_designation(f"{s} {c}"))
            if index:
                keys.append(squash_designation(f"{s}{index} {c}"))
    return keys


#: (star, squashed search keys, lowercase description) built once at import.
INDEX: list[tuple[NamedStar, list[str], str]] = [
    (s, search_keys(s), describe(s).lower()) for s in STARS
]

TYPE_NAME = "Star"


def row(star: NamedStar) -> dict:
    """The catalog-row shape /api/catalog returns, for one star.

    ``size_arcmin`` is 0: a star IS a point source at any focal length this rig
    will ever have, and 0 is the value ``difficulty.surface_brightness_mag``
    already treats as "no disc". No difficulty tier is attached — that rating is
    about imaging a faint extended object and would be noise on a naked-eye
    star."""
    return {
        "id": star.name,
        "name": describe(star),
        "type": TYPE_NAME,
        "kind": "star",
        "ra_hours": star.ra_hours,
        "dec_deg": star.dec_deg,
        "mag": star.mag,
        "size_arcmin": 0.0,
        "bayer": designation(star),
        "constellation": CONSTELLATIONS[star.con][0],
    }
