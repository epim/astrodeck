"""What each licence actually *requires* of us — the one place that judgement lives.

Naming a licence is not compliance. MIT does not ask to be named; it asks for its
copyright line and permission notice to travel with the binary. Apache-2.0 asks
for the licence, any NOTICE file, and a statement of changes. CC BY-SA asks for
attribution, a licence link, an indication of modification, and it infects
derived data. OFL restricts what you may name the font.

So every credit entry carries OBLIGATIONS, not just an SPDX string, and
``tools/gen_credits.py`` refuses to emit an entry whose obligations it cannot
satisfy from real files on disk.

The other half of this module is the FLAG list. A GPL dependency, a
non-commercial data set, or a package with no licence statement at all is not
something a generator gets to resolve — it is an owner decision and possibly a
removal. Those raise ``Flag`` and the build stops rather than quietly shipping.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

#: Obligation codes. The credits screen renders these as "what this requires",
#: and ``test_credits.py`` uses NEEDS_TEXT to decide which entries must carry a
#: reproduced licence body rather than a bare SPDX id.
NOTICE = "notice"          # reproduce copyright + permission notice verbatim
LICENSE_TEXT = "license"   # ship the full licence text
NOTICE_FILE = "notice-file"  # Apache-2.0 §4(d): carry upstream NOTICE content
STATE_CHANGES = "state-changes"  # Apache-2.0 §4(b), CC BY §3(a)(1)(B)
SOURCE_OFFER = "source"    # MPL-2.0 §3.2: source for modified files
ATTRIBUTION = "attribution"  # CC BY family: credit the creator + link licence
SHARE_ALIKE = "share-alike"  # CC BY-SA §3(b): derived data under the same terms
NO_SELL_ALONE = "no-sell-alone"  # OFL: the font may not be sold by itself
RESERVED_NAME = "reserved-name"  # OFL: Reserved Font Name may not be reused

#: Obligations that cannot be discharged by a link. If an entry carries one of
#: these it MUST ship reproduced text, and the generator fails when it cannot
#: find any. This is the check that stops the screen degenerating into a list of
#: licence names, which is the failure mode the whole exercise exists to prevent.
NEEDS_TEXT = frozenset({NOTICE, LICENSE_TEXT})


class Flag(Exception):
    """A licence the generator refuses to decide about on the owner's behalf."""


@dataclass(frozen=True)
class Policy:
    """What one licence family demands."""

    spdx: str
    requires: tuple[str, ...]
    #: Prose shown on the credits screen under the entry, in the second person
    #: plural, describing what WE owe — not what the licence permits.
    summary: str
    #: True when this licence is copyleft in a way that reaches our own source.
    copyleft: bool = False


_PERMISSIVE_NOTICE = (NOTICE,)

_POLICIES: dict[str, Policy] = {}


def _p(spdx: str, requires: tuple[str, ...], summary: str, copyleft: bool = False) -> None:
    _POLICIES[spdx.lower()] = Policy(spdx, requires, summary, copyleft)


# -- permissive: reproduce the notice, and that is the whole of it ------------
for _spdx in (
    "MIT", "MIT-0", "MIT-CMU", "BSD-2-Clause", "BSD-3-Clause", "BSD",
    "ISC", "Zlib", "PSF-2.0", "Python-2.0", "Unicode-3.0", "Unicode-DFS-2016",
    "HPND", "BSD-3-Clause-Clear", "NCSA",
):
    _p(_spdx, _PERMISSIVE_NOTICE,
       "Reproduce the copyright notice and permission notice with the "
       "distribution. No source-code obligation.")

# -- public-domain equivalents: nothing is owed, credit given anyway ----------
for _spdx in ("0BSD", "CC0-1.0", "Unlicense", "WTFPL"):
    _p(_spdx, (),
       "No attribution required. Credited here because we ship it.")

_p("Apache-2.0", (LICENSE_TEXT, NOTICE_FILE, STATE_CHANGES),
   "Ship the full Apache-2.0 text, carry forward any upstream NOTICE file, and "
   "state prominently if we changed the files. No source-code obligation.")

_p("Apache-2.0 WITH LLVM-exception", (LICENSE_TEXT, NOTICE_FILE, STATE_CHANGES),
   "Apache-2.0 with the LLVM exception: ship the full text and any NOTICE, and "
   "state changes. The exception removes the patent/attribution burden on "
   "compiled output.")

_p("MPL-2.0", (LICENSE_TEXT, SOURCE_OFFER),
   "Ship the MPL-2.0 text. File-level copyleft: if we modify a covered FILE we "
   "must publish that file's source under MPL-2.0. Our own separate files are "
   "unaffected.", copyleft=True)

_p("OFL-1.1", (LICENSE_TEXT, NO_SELL_ALONE, RESERVED_NAME),
   "Ship the SIL Open Font License text with the font. The font may be bundled "
   "and sold WITH software but never sold on its own, and any Reserved Font "
   "Name may not be used by a modified version.")

_p("CC-BY-4.0", (ATTRIBUTION, LICENSE_TEXT, STATE_CHANGES),
   "Credit the creator, link the licence, and indicate whether we changed the "
   "data. The licence text travels with the data.")

_p("CC-BY-SA-4.0", (ATTRIBUTION, LICENSE_TEXT, STATE_CHANGES, SHARE_ALIKE),
   "Credit the creator, link the licence, indicate whether we changed the data, "
   "and license any adapted data under CC BY-SA 4.0 as well.", copyleft=True)

#: Not a licence to us at all — a service we call under its terms of use, with
#: no attribution asked for. Listed so the credits are a COMPLETE account of what
#: the app talks to, which is worth more than a list trimmed to the legally
#: mandatory. Using MIT-0 here instead would have been us inventing a grant.
_p("LicenseRef-Terms-Of-Service", (),
   "No attribution required. Listed so this page is a complete account of what "
   "AstroDeck contacts.")

_p("LicenseRef-CDS-Credit", (ATTRIBUTION,),
   "Credit CDS and the originating survey. CDS do not apply one licence across "
   "their HiPS holdings — each dataset carries its own copyright — so the "
   "obligation is the requested credit line, not a licence text.")

#: A US Government work, or material its publisher has placed in the public
#: domain. Nothing is legally owed. Credited anyway, because "we took this from
#: somewhere" is worth saying even when no licence compels it.
_p("LicenseRef-Public-Domain", (),
   "Public domain — no attribution legally required. Credited because the "
   "publisher asks for it and it costs us nothing to say so.")

_p("LicenseRef-ESA-Credit", (ATTRIBUTION,),
   "Credit ESA as the source. ESA publish their public material under CC BY-SA "
   "3.0 IGO terms with credit to ESA.")

_p("Gaia-DPAC", (ATTRIBUTION,),
   "ESA's Gaia data are free to use provided credit is given to 'ESA/Gaia/DPAC'.")

_p("proprietary-redistributable", (NOTICE,),
   "Vendor SDK redistributed under the vendor's own terms; the terms text is "
   "reproduced in full below and permits shipping the compiled library.")

#: Licences that stop the build. Each is an owner decision: adopt the obligation
#: knowingly, or remove the dependency. The generator will not choose.
_FLAGGED: dict[str, str] = {
    "gpl-1.0": "GPL: strong copyleft, would reach AstroDeck's own source.",
    "gpl-2.0": "GPL: strong copyleft, would reach AstroDeck's own source.",
    "gpl-3.0": "GPL: strong copyleft, would reach AstroDeck's own source.",
    "agpl-3.0": "AGPL: copyleft that triggers on network use of the server.",
    "lgpl-2.1": "LGPL: relinking obligation; our PyInstaller single-file binary "
                "statically bundles, which is exactly the case LGPL constrains.",
    "lgpl-3.0": "LGPL: relinking obligation; see LGPL-2.1 note.",
    "sspl-1.0": "SSPL: not an OSI licence; service-source obligation.",
    "bsl-1.1": "Business Source License: use restrictions until the change date.",
    "cc-by-nc-4.0": "Non-commercial: forbids the commercial use AstroDeck may make.",
    "cc-by-nc-sa-4.0": "Non-commercial + share-alike.",
    "proprietary": "No redistribution grant established.",
    "unknown": "No licence statement found at all.",
}

_FLAG_PATTERNS = (
    (re.compile(r"\bA?GPL\b", re.I), "GPL-family licence"),
    (re.compile(r"\bnon[- ]?commercial\b", re.I), "non-commercial restriction"),
    (re.compile(r"research[- ]only|academic[- ]use", re.I), "research-use-only restriction"),
    (re.compile(r"\bSSPL\b", re.I), "SSPL"),
)


@dataclass
class Resolution:
    """The generator's verdict for one entry's licence string."""

    policies: list[Policy] = field(default_factory=list)
    #: Set when the licence needs an owner decision instead of a credit entry.
    flag: str | None = None

    @property
    def requires(self) -> tuple[str, ...]:
        seen: list[str] = []
        for pol in self.policies:
            for req in pol.requires:
                if req not in seen:
                    seen.append(req)
        return tuple(seen)

    @property
    def needs_text(self) -> bool:
        return bool(NEEDS_TEXT & set(self.requires))

    @property
    def summaries(self) -> list[str]:
        # De-duplicated: `Zlib OR Apache-2.0 OR MIT` resolves to three distinct
        # policies, two of which say exactly the same thing about what we owe.
        # Printing that sentence twice tells the reader nothing and gives the
        # renderer two children with the same key.
        seen: list[str] = []
        for pol in self.policies:
            if pol.summary not in seen:
                seen.append(pol.summary)
        return seen


def _split_expression(expr: str) -> list[str]:
    """Break an SPDX expression into the individual licence ids it names.

    ``OR`` means upstream lets us pick, but we do not get to *silently* pick:
    we credit every branch, because a reader auditing us should see the same
    choice we had. ``AND`` obviously requires all of them. Splitting on both
    and de-duplicating gets that with no special-casing.
    """
    expr = expr.replace("(", " ").replace(")", " ")
    # Note what is NOT split on: `WITH`. `Apache-2.0 WITH LLVM-exception` is
    # one licence id, not two, and splitting only on the boolean operators
    # leaves it intact without needing any escaping.
    parts = re.split(r"\s+(?:AND|OR)\s+|/", expr, flags=re.I)
    out: list[str] = []
    for part in parts:
        part = " ".join(part.split())
        if part and part not in out:
            out.append(part)
    return out


#: Free-text licence fields that are not SPDX but are unambiguous in practice.
_ALIASES = {
    "bsd 3-clause": "BSD-3-Clause",
    "bsd 3-clause license": "BSD-3-Clause",
    "bsd-3clause": "BSD-3-Clause",
    "bsd license": "BSD",
    "bsd": "BSD",
    "mit license": "MIT",
    "apache 2.0": "Apache-2.0",
    "apache software license": "Apache-2.0",
    "the unlicense (unlicense)": "Unlicense",
    "mpl 2.0": "MPL-2.0",
    "mozilla public license 2.0 (mpl 2.0)": "MPL-2.0",
    "python software foundation license": "PSF-2.0",
    "psf": "PSF-2.0",
    "sil open font license 1.1": "OFL-1.1",
    "ofl": "OFL-1.1",
    "mit-cmu": "MIT-CMU",
    "historical permission notice and disclaimer": "HPND",
}


def resolve(expression: str | None) -> Resolution:
    """Map a licence expression onto the obligations it imposes on us.

    Raises ``Flag`` for anything an owner must rule on: copyleft that reaches
    our source, a use restriction, or no statement at all.
    """
    raw = (expression or "").strip()
    if not raw:
        raise Flag("no licence statement found — cannot ship without one")

    # Deliberately conservative: a licence expression that mentions GPL or a use
    # restriction ANYWHERE gets flagged, even when it offers a permissive
    # alternative via OR. Distinguishing `MIT OR GPL-2.0` (fine, take MIT) from
    # `MIT AND GPL-2.0` (not fine) needs a real SPDX expression parser, and
    # guessing wrong in the permissive direction is the one error that ships a
    # breach. Over-flagging costs an owner one glance; under-flagging costs a
    # licence violation. Nothing in the current stack trips this.
    for pattern, why in _FLAG_PATTERNS:
        if pattern.search(raw):
            raise Flag(f"{why}: {raw!r}")

    res = Resolution()
    unknown: list[str] = []
    for part in _split_expression(raw):
        key = part.lower()
        key = _ALIASES.get(key, part).lower()
        if key in _FLAGGED:
            raise Flag(f"{_FLAGGED[key]} ({part!r})")
        pol = _POLICIES.get(key)
        if pol is None:
            unknown.append(part)
            continue
        if pol not in res.policies:
            res.policies.append(pol)

    if unknown and not res.policies:
        raise Flag(
            f"unrecognised licence {raw!r} — add it to tools/licence_policy.py "
            "once an owner has read the actual terms")
    if unknown:
        # Some branches understood, some not. Do not paper over the remainder;
        # a partially-understood expression is exactly where an obligation goes
        # missing.
        raise Flag(f"licence expression {raw!r} has unhandled branch(es): {unknown}")
    return res
