"""The parts of the stack that no manifest describes.

Everything a package manager knows about is read out of the package manager by
``tools/gen_credits.py``. What is left over is real and still carries
obligations: a DLL committed to the repo, a catalogue extracted from someone
else's data, a weather API whose terms ask for credit, an algorithm written by
reading someone else's source.

A hand-maintained list is exactly the thing this project keeps getting bitten
by — "something asserts a fact, so nobody checks it". So each list here is
paired with a detector in ``server/tests/test_credits.py`` that goes and looks:

* ``VENDORED``  — the generator walks ``server/astrodeck/vendor/*`` and fails on
  any directory shipping binaries that is not keyed here.
* ``DATA``      — the generator walks ``server/astrodeck/catalog/data`` and fails
  on any file not keyed here.
* ``SERVICES``  — the test greps the server source for outbound https hosts and
  fails on any host not claimed by an entry's ``hosts``.
* ``PROGRAMS``  — the test greps for the executable names the server looks for
  and fails on any not claimed by an entry's ``binaries``.
* ``DERIVED``   — no detector is possible (the obligation comes from reading
  source, and nothing on disk records that). Stated plainly rather than
  pretended otherwise; ``THIRD-PARTY-NOTICES.md`` is the long-form record.

``notes`` is prose shown to the user under the entry. Write it for the person
holding the phone, not for a lawyer, but do not soften a gap: if we are relying
on an interpretation rather than an explicit grant, say that.
"""
from __future__ import annotations

from pathlib import Path

import licence_policy as lp

_TEXTS = Path(__file__).resolve().parent / "licence_texts"


def _text(filename: str) -> str:
    return (_TEXTS / filename).read_text(encoding="utf-8").strip()


def _file(title: str, filename: str) -> dict:
    return {"title": title, "body": _text(filename)}


#: Canonical text for licences AstroDeck itself publishes under, so the
#: generator can satisfy their text obligation for our own components too.
_SPDX_TEXT = {"MPL-2.0": "mpl-2.0.txt", "Apache-2.0": "apache-2.0.txt"}


def text_for_spdx(spdx: str | None) -> str:
    name = _SPDX_TEXT.get(spdx or "")
    return _text(name) if name else ""


# ---------------------------------------------------------------------------
# Licence text for packages whose own wheel/package ships none.
# Keyed by lowercase distribution name.
# ---------------------------------------------------------------------------

TEXT_OVERRIDES: dict[str, dict] = {
    "pyserial": {
        # The wheel's METADATA says only "License: BSD", which is vaguer than
        # the truth: serial/__init__.py carries `SPDX-License-Identifier:
        # BSD-3-Clause`. A compliance page should name the licence the project
        # actually chose, not the loosest string in its metadata.
        "spdx": "BSD-3-Clause",
        "title": "LICENSE.txt (from the pySerial source tree)",
        "body": _text("pyserial-bsd-3-clause.txt"),
        "note": (
            "pySerial's published wheel contains no licence file, so this text "
            "was taken from the project's own LICENSE.txt. The SPDX header in "
            "serial/__init__.py agrees: BSD-3-Clause, Chris Liechti."),
    },
}


# ---------------------------------------------------------------------------
# Vendored binaries — keyed by directory under server/astrodeck/vendor/
# ---------------------------------------------------------------------------

VENDORED: dict[str, dict] = {
    "zwo": {
        "name": "ZWO camera, focuser and rotator SDKs",
        "version": "ASICamera2 1.41.0.0, EAF 1.8.1, CAA 1.5.6",
        "spdx": "MIT",
        "url": "https://www.zwoastro.com/downloads/developers",
        "notes": (
            "Lets AstroDeck talk to ZWO cameras, the EAF focuser and the CAA "
            "rotator without installing ZWO's own software. ZWO publish these "
            "SDKs under a verbatim MIT licence whose grant explicitly covers "
            "distributing copies, so shipping the compiled libraries is "
            "permitted outright; the notice below travels with them, which is "
            "the only condition MIT attaches. The same libraries are "
            "redistributed the same way by INDI (indi-3rdparty) and by NINA.\n\n"
            "One nit worth recording: EAF_focuser.h carries no copyright line "
            "of its own and inherits its terms from the LICENSE file beside it."),
    },
    "playerone": {
        "name": "Player One Camera SDK",
        "version": "3.10.1 (Windows) / 3.10.0 (Linux, macOS)",
        # Deliberately NOT "MIT". The file reuses MIT's closing paragraphs but
        # replaces MIT's grant, and the words copy / publish / distribute /
        # sublicense / sell appear nowhere in it. Calling it MIT in our own
        # credits would be us asserting a permission the licensor never wrote.
        # An unrecognised id routes this into the "needs an owner decision"
        # group by design — see tools/licence_policy.py.
        "spdx": "LicenseRef-PlayerOne-SDK",
        "url": "https://player-one-astronomy.com/service/software/",
        "flag": (
            "The Player One SDK licence reproduces MIT's notice-retention clause "
            "and warranty disclaimer but replaces MIT's grant with its own "
            "prose, which contains no distribution verb (no copy, publish, "
            "distribute, sublicense or sell) and limits the SDK to \"secondary "
            "development of our company's cameras\". AstroDeck redistributes six "
            "compiled binaries on it. Owner decision: get written confirmation "
            "from support@player-one-astronomy.com, or stop shipping the "
            "binaries and require a vendor install."),
        "requires": (lp.NOTICE,),
        "summaries": [
            "Reproduce the vendor's notice with the distribution. Whether the "
            "notice also grants redistribution is the open question below."],
        "notes": (
            "Lets AstroDeck drive Player One cameras without the vendor's own "
            "software.\n\n"
            "OPEN QUESTION FOR THE OWNER. This SDK's licence file is written in "
            "the shape of MIT — it ends with MIT's notice-retention clause and "
            "MIT's warranty disclaimer word for word — but its middle paragraph "
            "is Player One's own prose, and that prose contains no distribution "
            "verb: not copy, not publish, not distribute, not sublicense, not "
            "sell. What it does say is \"You can use our company's products and "
            "this SDK to develop any products without any restrictions\", "
            "preceded by \"This SDK is only used for the secondary development "
            "of our company's cameras or other equipment.\"\n\n"
            "A reasonable reading is that redistribution is intended: a runtime "
            "library is useless unless it ships, and the retained clause "
            "presupposes that copies will be distributed. But that is an "
            "inference, not a grant, and AstroDeck currently redistributes six "
            "compiled binaries on it. Written confirmation from "
            "support@player-one-astronomy.com would close this; until then it "
            "is listed here rather than quietly filed under MIT."),
    },
}


# ---------------------------------------------------------------------------
# Shipped data — keyed by filename under server/astrodeck/catalog/data/
# ---------------------------------------------------------------------------

DATA: dict[str, dict] = {
    "ngc.tsv": {
        "name": "OpenNGC deep-sky catalogue",
        "version": "extract of 13,369 objects",
        "spdx": "CC-BY-SA-4.0",
        "url": "https://github.com/mattiaverga/OpenNGC",
        "texts": [_file("CC BY-SA 4.0 legal code", "cc-by-sa-4.0.txt")],
        "notes": (
            "Every deep-sky object AstroDeck can find by name. By Mattia Verga, "
            "licensed CC BY-SA 4.0 — https://creativecommons.org/licenses/by-sa/4.0/\n\n"
            "WE CHANGED IT, and CC BY-SA requires us to say how. From OpenNGC's "
            "NGC.csv we kept Name, Type, RA, Dec, V-Mag (falling back to B-Mag), "
            "MajAx, Common names and M, and dropped every other column. Right "
            "ascension was converted from sexagesimal to decimal hours and "
            "declination to decimal degrees. Object types were collapsed onto a "
            "shorter code set (the galaxy types G, GPair, GTrpl and GGroup all "
            "become GX; HII, EmN and Neb all become EN, and so on), and rows "
            "typed Dup or NonEx were dropped. Designations were renormalised "
            "from NGC0224 to NGC 224. Where an object has a Messier number that "
            "number became its primary identifier and the NGC/IC designation "
            "became an alias. Only the first of several common names was kept. "
            "Objects with no published magnitude were kept with a sentinel "
            "value rather than discarded.\n\n"
            "SHARE-ALIKE: this extract is itself CC BY-SA 4.0. Anyone "
            "redistributing AstroDeck's ngc.tsv, modified or not, is bound by "
            "the same terms.\n\n"
            "PASS-THROUGH CREDIT that OpenNGC asks redistributors to carry, for "
            "the sources it was itself compiled from:\n"
            "• \"This research has made use of the NASA/IPAC Extragalactic "
            "Database (NED) which is operated by the Jet Propulsion Laboratory, "
            "California Institute of Technology, under contract with the "
            "National Aeronautics and Space Administration.\"\n"
            "• \"We acknowledge the usage of the HyperLeda database "
            "(http://leda.univ-lyon1.fr)\"\n"
            "• \"This research has made use of the SIMBAD database, "
            "operated at CDS, Strasbourg, France\"\n"
            "• HEASARC tables, and Harold Corwin's NGC/IC Positions and "
            "Notes.\n\n"
            "Reproducibility gap, stated rather than hidden: the upstream "
            "snapshot this extract was built from is not pinned to a commit and "
            "no checksum was recorded, so the exact input cannot be identified "
            "from the repository alone."),
    },
    "constellations.tsv": {
        "name": "Constellation assignments (IAU boundaries, via Astropy)",
        "version": "13,370 rows",
        "spdx": "Apache-2.0",
        "url": "https://www.astropy.org/",
        "texts": [_file("Apache License 2.0", "apache-2.0.txt")],
        "notes": (
            "Which constellation each catalogue object falls in. Not third-party "
            "data as such: it is computed here by astropy.coordinates."
            "get_constellation over AstroDeck's own object list, so the rows are "
            "measurements rather than a copied table. The authority underneath "
            "is the IAU constellation boundary system defined by Eugene "
            "Delporte in 1930, which astropy carries; the code that does the "
            "lookup is Astropy, BSD-3-Clause, credited in full under Python "
            "packages. Positions are precessed from J2000 to the B1875 epoch the "
            "boundaries are drawn in — skipping that moves 1,127 of 13,369 "
            "objects into the wrong constellation."),
    },
    "ngc_extras.tsv": {
        "name": "OpenNGC deep-sky catalogue — extra columns (Hubble type, "
                "minor axis, redshift, NED notes)",
        "version": "extract of 12,013 objects",
        "spdx": "CC-BY-SA-4.0",
        "url": "https://github.com/mattiaverga/OpenNGC",
        "texts": [_file("CC BY-SA 4.0 legal code", "cc-by-sa-4.0.txt")],
        "notes": (
            "Four more OpenNGC columns ngc.tsv itself does not carry, used to "
            "richen the sentence describe.py composes per object. By Mattia "
            "Verga, licensed CC BY-SA 4.0 — "
            "https://creativecommons.org/licenses/by-sa/4.0/ — same source, "
            "same licence as ngc.tsv above; this is a second, separately-"
            "built extract from the same upstream, not a different dataset.\n\n"
            "WE CHANGED IT, and CC BY-SA requires us to say how. From "
            "OpenNGC's NGC.csv/addendum.csv we kept four columns beyond what "
            "ngc.tsv already carries: Hubble (galaxy subtype, e.g. \"Sb\"), "
            "MinAx (minor axis in arcminutes, paired with the major axis "
            "ngc.tsv already carries to flag an edge-on galaxy), Redshift "
            "(converted to a distance at H0=70, suppressed below ~10 Mpc so "
            "the 376 blueshifted rows never print a negative distance), and "
            "NED notes — filtered at build time to an ALLOW-LIST of two "
            "families (honest \"nothing here\" / doubtful-identification "
            "admissions, and Magellanic Cloud placements); everything else in "
            "that column, mostly HIPASS/SDSS survey cross-match trivia, was "
            "dropped rather than shipped wholesale. Rows with none of these "
            "four fields populated were not written at all. See "
            "server/astrodeck/catalog/build_ngc_extras.py for the exact "
            "logic and the allow-list regexes.\n\n"
            "SHARE-ALIKE: this extract is itself CC BY-SA 4.0, same as "
            "ngc.tsv. Anyone redistributing it, modified or not, is bound by "
            "the same terms.\n\n"
            "Same reproducibility gap as ngc.tsv: the upstream snapshot is "
            "not pinned to a commit and no checksum was recorded."),
    },
    "discoverers.tsv": {
        "name": "Discoverer and discovery date (Wikidata)",
        "version": "7,785 rows",
        "spdx": "CC0-1.0",
        "url": "https://www.wikidata.org/",
        "notes": (
            "Who found each object and when. CC0 — Wikidata dedicates its "
            "structured data to the public domain, so unlike every other "
            "entry on this page THIS ONE CARRIES NO ATTRIBUTION OBLIGATION "
            "AT ALL; naming Wikidata here is a courtesy, not a requirement. "
            "That licence is exactly why this layer was built against "
            "Wikidata's P61 (discoverer) / P575 (point in time) properties "
            "instead of Wikipedia, which is CC BY-SA and reaches a smaller "
            "share of the catalogue besides.\n\n"
            "THE JOIN. Wikidata models an NGC/IC designation as a P528 "
            "(\"catalog code\") statement qualified by P972 (\"catalog\") "
            "naming the New General Catalogue or the Index Catalogue; the "
            "code string (\"NGC 6543\") matches ngc.tsv's own id format "
            "exactly, confirmed by hand before this was built.\n\n"
            "AMBIGUOUS MATCHES ARE DROPPED, NOT GUESSED — three kinds, all "
            "excluded rather than resolved by a heuristic: a catalog code "
            "that names more than one distinct Wikidata item (100 in the "
            "live data); more than one distinct human discoverer credited on "
            "the same item (Wikidata's own data occasionally credits a "
            "non-human entity via P61, filtered out by requiring "
            "wdt:P31 wd:Q5); and conflicting discovery dates for the same "
            "(code, item) pair. See "
            "server/astrodeck/catalog/build_discoverers.py's module "
            "docstring for real examples of each.\n\n"
            "BE A CONSIDERATE CLIENT. This is fetched at build time only, "
            "paginated (~5,000 rows per request) with a pause between pages "
            "and resumable checkpointing — never queried per-object, and "
            "never queried at read time (the app itself never contacts "
            "Wikidata; see the Wikidata Query Service entry under Network "
            "Services)."),
    },
}

#: Product data that does NOT live under catalog/data — it is embedded in
#: Python source, so the directory walk cannot see it. There is no detector for
#: this list; it is the one place in the credits that depends on someone
#: remembering, and it is small on purpose.
DATA_EXTRA: list[dict] = [
    {
        "name": "IAU Catalog of Star Names (IAU-CSN)",
        "version": "2022-04-04 edition, cut at V <= 4.00",
        "spdx": "CC-BY-4.0",
        "url": "https://exopla.net/star-names/modern-iau-star-names/",
        "texts": [_file("CC BY 4.0 legal code", "cc-by-4.0.txt")],
        "notes": (
            "The 241 named naked-eye stars AstroDeck can point at by name "
            "(Vega, Deneb, Albireo...), embedded in "
            "server/astrodeck/catalog/brightstars.py. Compiled by the IAU "
            "Working Group on Star Names (WGSN).\n\n"
            "WE CHANGED IT: the catalogue was cut to stars brighter than "
            "magnitude 4.00 and reduced to the name, position and magnitude "
            "columns. No name, position or magnitude was altered.\n\n"
            "Version note: the catalogue file states \"Creative Commons "
            "Attribution\" without naming a version. CC BY 4.0 is inferred from "
            "the IAU's site-wide copyright statement; the catalogue itself never "
            "says 4.0."),
    },
    {
        "name": "DSS2 colour imagery (bundled offline survey pack)",
        "version": "HEALPix order 3, ~1,020 tiles",
        "spdx": "LicenseRef-DSS-AllRightsReserved",
        "url": "https://archive.stsci.edu/dss/copyright.html",
        "flag": (
            "DSS/DSS2 imagery is copyrighted All Rights Reserved (AAO Board, "
            "Caltech, AURA, UK SERC/PPARC). The only published permission is a "
            "NON-PROFIT research/teaching USE grant; third-party REDISTRIBUTION "
            "is nowhere authorised, and colour DSS is explicitly directed to "
            "archive@stsci.edu. Release builds bundle ~45 MB of these tiles. "
            "Owner decision: obtain written permission from STScI, or swap the "
            "offline pack to an openly-licensed survey."),
        "notes": (
            "The sky imagery behind the Atlas when the rig has no internet. "
            "Release builds embed a low-resolution DSS2-colour tile pack so the "
            "Atlas is not blank offline.\n\n"
            "OPEN QUESTION FOR THE OWNER — the most serious item on this page. "
            "The Digitized Sky Surveys are not public domain and not openly "
            "licensed. STScI's terms read: \"Scientists and educators conducting "
            "research, teaching (including textbooks), or other non-profit "
            "activities may use data from the copyrighted collections freely and "
            "without restriction\" and \"Commercial, for-profit use of the "
            "copyrighted collections is prohibited without written permission\". "
            "Colour DSS has its own carve-out: \"For use of color DSS images not "
            "covered by above use policy, contact archive@stsci.edu.\"\n\n"
            "Neither statement addresses a third party redistributing the "
            "imagery inside a downloadable product, which is what bundling the "
            "pack does. CDS, who host the tiles, permit mirroring only where "
            "\"the copyright on the original data authorises this "
            "redistribution\" — the one condition that cannot be established "
            "here. Their HiPS status for this survey is clonableOnce.\n\n"
            "Fetching tiles on demand for your own viewing is a different act "
            "and sits comfortably inside the use grant. It is the bundled pack "
            "that needs a decision.\n\n"
            "Required acknowledgement, given here: \"The Digitized Sky Surveys "
            "were produced at the Space Telescope Science Institute under U.S. "
            "Government grant NAG W-2166. The images of these surveys are based "
            "on photographic data obtained using the Oschin Schmidt Telescope on "
            "Palomar Mountain and the UK Schmidt Telescope. The plates were "
            "processed into the present compressed digital form with the "
            "permission of these institutions.\" Colourised and HEALPixed by CDS."),
    },
    {
        "name": "2MASS colour imagery",
        "spdx": "LicenseRef-Terms-Of-Service",
        "url": "https://irsa.ipac.caltech.edu/Missions/2mass.html",
        "notes": (
            "An alternative sky survey in the Atlas, fetched on demand and never "
            "bundled. Acknowledgement, as asked for: \"This publication makes "
            "use of data products from the Two Micron All Sky Survey, which is a "
            "joint project of the University of Massachusetts and the Infrared "
            "Processing and Analysis Center/California Institute of Technology, "
            "funded by the National Aeronautics and Space Administration and the "
            "National Science Foundation.\""),
    },
]


# ---------------------------------------------------------------------------
# Declared optional extras that are not installed in the generating environment.
# They still ship to anyone who installs the extra, so they still get credited.
# Keyed by lowercase distribution name.
# ---------------------------------------------------------------------------

OPTIONAL_UNINSTALLED: dict[str, dict] = {
    "uvloop": {
        "name": "uvloop",
        "version": ">=0.21 (Linux/macOS only)",
        "spdx": "Apache-2.0",
        "url": "https://github.com/MagicStack/uvloop",
        "texts": [_file("LICENSE-APACHE", "apache-2.0.txt")],
        "notes": (
            "A faster asyncio event loop, pulled in transitively by "
            "`uvicorn[standard]` on Linux and macOS. It is NOT a Windows "
            "dependency — uvicorn's marker excludes win32 — which is exactly "
            "why it needs an entry here rather than being discovered by the "
            "generator: a credits file generated on Windows would never see "
            "it, and the Linux bundle that DOES ship it would go uncredited. "
            "uvloop is dual-licensed MIT OR Apache-2.0; the Apache-2.0 text is "
            "reproduced above to satisfy the notice obligation."),
    },
    "comtypes": {
        "name": "comtypes",
        "version": ">=1.4.0 (Windows only)",
        "spdx": "MIT",
        "url": "https://github.com/enthought/comtypes",
        "texts": [_file("LICENSE.txt", "comtypes.txt")],
        "notes": (
            "Lets AstroDeck talk to Windows COM device drivers directly, so an "
            "ASCOM driver works without a separate ASCOM Remote install. Part of "
            "the `comhost` extra, which every Windows bundle installs."),
    },
    "libasi": {
        "name": "libasi",
        "version": "unpinned (git URL)",
        "spdx": "MIT",
        "url": "https://github.com/epim/libasi",
        "texts": [_file("LICENSE", "libasi.txt")],
        "notes": (
            "The transport for talking to a ZWO ASIAIR. Declared by the optional "
            "`asiair` extra, not on PyPI.\n\n"
            "The extra used to install from github.com/jewzaam/libasi, which "
            "returns 404, so it could not be installed by anyone following the "
            "documented path and its MIT claim rested on nothing checkable. "
            "Upstream moved to epim/libasi — identified by content (the only "
            "repository carrying CONFIRMED_FORMATS.md, with an `asiair/` package "
            "exporting ASIAIRClient) rather than by name alone, and its licence "
            "was re-verified live against the repository: public, not a fork, "
            "spdx_id MIT, verbatim MIT text. The URL is fixed everywhere it "
            "appeared, including the hint an operator is shown."),
    },
}


# ---------------------------------------------------------------------------
# Network services. `hosts` is load-bearing: test_credits.py greps the server
# source for outbound hosts and fails on any host no entry claims.
# ---------------------------------------------------------------------------

SERVICES: list[dict] = [
    {
        "name": "NOAA GOES on AWS",
        "spdx": "LicenseRef-Public-Domain",
        "url": "https://registry.opendata.aws/noaa-goes/",
        "hosts": [
            "noaa-goes18.s3.amazonaws.com",
            "noaa-goes19.s3.amazonaws.com",
            "s3.amazonaws.com",
        ],
        "notes": (
            "GOES-R Advanced Baseline Imager cloud products \u2014 the clear-sky "
            "mask and cloud-top height behind the cloud-occlusion model. Read "
            "anonymously from the public buckets NOAA publish through their Open "
            "Data Dissemination programme; no key, no account, no cost.\n\n"
            "NOAA data is a work of the United States government and is in the "
            "public domain (17 U.S.C. \u00a7 105). The AWS Open Data registry "
            "entry states plainly: \"There are no restrictions on the use of this "
            "data.\" Nothing is legally owed here.\n\n"
            "Credited anyway, because taking a public good silently is a poor "
            "way to treat one. The sky is measured by an instrument somebody "
            "else paid for.\n\n"
            "NOT YET ON SCREEN. Open-Meteo is named in the sky-conditions panel "
            "every time it shows a forecast; there is no cloud-map panel yet, so "
            "this entry is the only place NOAA is currently credited. When that "
            "panel lands it should name NOAA the same way. Written down here "
            "rather than asserted as done, because a credit line that describes "
            "a screen nobody built is the same defect as a docstring describing "
            "a fix nobody called."),
    },
    {
        "name": "Open-Meteo",
        "spdx": "CC-BY-4.0",
        "url": "https://open-meteo.com/",
        "hosts": ["api.open-meteo.com"],
        "texts": [_file("CC BY 4.0 legal code", "cc-by-4.0.txt")],
        "notes": (
            "Cloud cover, temperature, humidity and dewpoint — the forecast "
            "behind the sky-conditions panel and the weather safety gate. "
            "Open-Meteo publish their API data under CC BY 4.0. Weather data "
            "reaches Open-Meteo from national weather services including NOAA, "
            "ECMWF, DWD and Meteo-France; Open-Meteo relicense their own output "
            "as CC BY 4.0 and ask for no pass-through credit, with one caveat "
            "worth knowing: their UK Met Office source is CC BY-SA.\n\n"
            "WHAT THEY ASK FOR, exactly: \"You must include a link next to any "
            "location Open-Meteo data are displayed\", with the example markup "
            "<a href=\"https://open-meteo.com/\">Weather data by "
            "Open-Meteo.com</a>.\n\n"
            "The sky-conditions panel names Open-Meteo on screen every time it "
            "shows a forecast, and does so only when Open-Meteo data is actually "
            "flowing. See the gap note in "
            "docs/superpowers/backlog/2026-08-08-third-party-licences.md about "
            "making that label a link."),
    },
    {
        "name": "Astrospheric",
        "spdx": "LicenseRef-Astrospheric-Proprietary",
        "url": "https://www.astrospheric.com/privacypolicy.html",
        "hosts": ["astrosphericpublicaccess.azurewebsites.net"],
        "flag": (
            "Astrospheric's Terms of Use grant no redistribution right and scope "
            "API access to Professional members' PERSONAL projects; shipping the "
            "integration in a public product appears to need their written "
            "permission. Owner decision: seek permission, or remove."),
        "notes": (
            "Astronomical seeing and transparency forecasts, used only when you "
            "supply your own API key. Astrospheric derive these from Environment "
            "and Climate Change Canada's RDPS/HRDPS models. Credited on screen "
            "beside Open-Meteo whenever Astrospheric samples are actually "
            "flowing — never when they are not.\n\n"
            "OPEN QUESTION FOR THE OWNER. Astrospheric's terms say "
            "\"Unauthorized use, reproduction, distribution, or exploitation of "
            "Astrospheric's assets, APIs, or content outside of the Service is "
            "strictly prohibited\", and their API documentation scopes the Data "
            "API to \"Astrospheric Professional members for use in personal "
            "projects\", directing public or commercial projects to contact "
            "them. AstroDeck is a public product that ships an Astrospheric "
            "client. Each user supplies their own key and their own forecast is "
            "never cached beyond the display, which is the mitigating fact — but "
            "it is not a permission. Written confirmation would close this.\n\n"
            "Pass-through credit their upstream asks for: \"Contains information "
            "licenced under the Data Server End-use Licence of Environment and "
            "Climate Change Canada.\""),
    },
    {
        "name": "Iowa Environmental Mesonet (Iowa State University)",
        "spdx": "LicenseRef-Public-Domain",
        "url": "https://mesonet.agron.iastate.edu/",
        "hosts": ["mesonet.agron.iastate.edu"],
        "notes": (
            "The radar and satellite map tiles. The IEM at Iowa State "
            "University's Department of Agronomy re-serves NOAA/NWS NEXRAD "
            "reflectivity and NOAA GOES-East imagery as map tiles. The "
            "underlying observations are US Government works and carry no "
            "copyright; the IEM ask to be cited for the service that delivers "
            "them, which is what this entry does. Tiles are cached on disk so "
            "the same tile is not requested repeatedly."),
    },
    {
        "name": "CDS - Centre de Donnees astronomiques de Strasbourg",
        "spdx": "LicenseRef-CDS-Credit",
        "url": "https://cds.unistra.fr/",
        "hosts": ["alasky.cds.unistra.fr", "alaskybis.cds.unistra.fr"],
        "notes": (
            "Sky survey imagery behind the Atlas: the hips2fits cutout service "
            "and the HiPS tile mirrors, operated by CDS at the Universite de "
            "Strasbourg / CNRS. AstroDeck acknowledges the use of the Aladin sky "
            "atlas and the HiPS services developed at CDS.\n\n"
            "Online fetching is OFF by default — an observatory usually has no "
            "internet, so the local survey pack is the primary source and CDS is "
            "asked only when you turn it on."),
    },
    {
        "name": "ESA / ESAC Science Data Centre",
        "spdx": "LicenseRef-ESA-Credit",
        "url": "https://www.cosmos.esa.int/",
        "hosts": ["skies.esac.esa.int"],
        "texts": [],
        "notes": (
            "The first-choice mirror for DSS2-colour and 2MASS-colour sky tiles, "
            "operated by the European Space Agency at ESAC, Madrid. ESA's "
            "default terms for its public material are CC BY-SA 3.0 IGO with "
            "credit to ESA."),
    },
    {
        "name": "GitHub",
        "spdx": "LicenseRef-Terms-Of-Service",
        "url": "https://github.com/",
        "hosts": ["api.github.com", "objects.githubusercontent.com"],
        "notes": (
            "Where AstroDeck looks for its own updates. Release metadata only; "
            "no attribution is required for calling the API, and it is listed "
            "here so this page is a complete account of what the app talks to."),
    },
    {
        "name": "Google Identity (OpenID Connect)",
        "spdx": "LicenseRef-Terms-Of-Service",
        "url": "https://developers.google.com/identity/openid-connect/openid-connect",
        "hosts": ["accounts.google.com", "oauth2.googleapis.com",
                  "www.googleapis.com"],
        "notes": (
            "Optional sign-in with a Google account. Contacted only if you "
            "configure Google as an authentication method; the default LAN "
            "posture never calls it. No attribution required — listed for "
            "completeness."),
    },
    {
        "name": "Telegram Bot API",
        "spdx": "LicenseRef-Terms-Of-Service",
        "url": "https://core.telegram.org/bots/api",
        "hosts": ["api.telegram.org"],
        "notes": (
            "One of the alert channels, used only if you configure a Telegram "
            "bot token. No attribution required — listed for completeness."),
    },
    {
        "name": "SourceForge",
        "spdx": "LicenseRef-Terms-Of-Service",
        "url": "https://sourceforge.net/projects/astap-program/files",
        "hosts": ["sourceforge.net"],
        "notes": (
            "Where the release build downloads ASTAP and its star database "
            "from. Build-time only — a running AstroDeck never contacts it."),
    },
    {
        "name": "Wikidata Query Service",
        "spdx": "LicenseRef-Terms-Of-Service",
        "url": "https://query.wikidata.org/",
        "hosts": ["query.wikidata.org", "www.wikidata.org"],
        "notes": (
            "Where server/astrodeck/catalog/build_discoverers.py regenerates "
            "discoverers.tsv from. Build-time only — a running AstroDeck "
            "never contacts Wikidata; the data itself is CC0 and credited "
            "separately under Shipped Data."),
    },
]


# ---------------------------------------------------------------------------
# External programs. `binaries` is load-bearing the same way `hosts` is.
# ---------------------------------------------------------------------------

PROGRAMS: list[dict] = [
    {
        "name": "ASTAP",
        "version": "command-line solver (astap_cli)",
        "spdx": "MPL-2.0",
        "url": "https://www.hnsky.org/astap.htm",
        "binaries": ["astap", "astap_cli", "astap.exe", "astap_cli.exe"],
        "texts": [_file("Mozilla Public License 2.0", "mpl-2.0.txt")],
        "notes": (
            "The plate solver: it looks at a frame and works out exactly where "
            "the telescope is pointing. By Han Kleijn; source at "
            "https://github.com/han-k59/astap\n\n"
            "AstroDeck runs ASTAP as a separate process over its command line "
            "and does not link against it, so MPL-2.0's file-level copyleft "
            "covers ASTAP's own source and reaches nothing of AstroDeck's. When "
            "a release bundles the solver binary, this licence text ships beside "
            "it.\n\n"
            "Deliberately not bundled: ASTAP's installer also carries deep-sky "
            "and variable-star catalogues from Wolfgang Steinicke and HyperLEDA, "
            "both of which permit non-commercial use only. AstroDeck's fetch "
            "script takes the solver and the star database and leaves those "
            "behind, so no non-commercial restriction is inherited."),
    },
    {
        "name": "ESA Gaia star databases (via ASTAP)",
        "spdx": "Gaia-DPAC",
        "url": "https://www.cosmos.esa.int/web/gaia/credits",
        "binaries": [],
        "notes": (
            "The star database ASTAP matches frames against is derived from the "
            "European Space Agency's Gaia mission. Gaia data are free to use "
            "provided credit is given to ESA/Gaia/DPAC, and that credit is given "
            "here.\n\n"
            "\"This work has made use of data from the European Space Agency "
            "(ESA) mission Gaia (https://www.cosmos.esa.int/gaia), processed by "
            "the Gaia Data Processing and Analysis Consortium (DPAC, "
            "https://www.cosmos.esa.int/web/gaia/dpac/consortium).\"\n\n"
            "Applies to every database AstroDeck may ship or you may install: "
            "W08, D05, G05, D20, D50, D80."),
    },
    {
        "name": "PHD2",
        "spdx": "BSD-3-Clause",
        "url": "https://openphdguiding.org/",
        "binaries": ["phd2", "phd2.exe", "PHD2"],
        "texts": [_file("PHD2 licence", "phd2-bsd-3-clause.txt")],
        "notes": (
            "The guiding program many astrophotographers already run. AstroDeck "
            "can use it instead of its own guider, over PHD2's JSON event "
            "socket. AstroDeck never launches PHD2 — it looks at the standard "
            "install paths only so it can tell \"not installed\" apart from "
            "\"installed but not running\", which is the difference between two "
            "very different pieces of advice."),
    },
    {
        "name": "N.I.N.A. (Nighttime Imaging 'N' Astronomy)",
        "spdx": "MPL-2.0",
        "url": "https://nighttime-imaging.eu/",
        "binaries": [],
        "texts": [_file("Mozilla Public License 2.0", "mpl-2.0.txt")],
        "notes": (
            "AstroDeck can drive a rig through a running NINA instance over its "
            "HTTP and WebSocket API, for people who want to keep the setup they "
            "already have. Nothing of NINA is bundled or linked; it is a network "
            "protocol between two separate programs."),
    },
    {
        "name": "ASCOM Platform and Alpaca",
        "spdx": "LicenseRef-Terms-Of-Service",
        "url": "https://ascom-standards.org/",
        "binaries": [],
        "notes": (
            "The standard Windows device interface and its network form, Alpaca. "
            "AstroDeck speaks Alpaca's HTTP protocol and discovers devices over "
            "UDP; the ASCOM Platform itself is a separate installation by the "
            "ASCOM Initiative and is neither bundled nor linked."),
    },
]


# ---------------------------------------------------------------------------
# Code written by reading someone else's source.
# ---------------------------------------------------------------------------

DERIVED: list[dict] = [
    {
        "name": "PHD2 guiding algorithms (ported)",
        "spdx": "BSD-3-Clause",
        "url": "https://github.com/OpenPHDGuiding/phd2",
        "texts": [_file("PHD2 licence", "phd2-bsd-3-clause.txt")],
        "notes": (
            "AstroDeck's native guider is a Rust reimplementation of PHD2's "
            "guiding stack — hysteresis, resist-switch, the Z filter, multi-star "
            "centroiding, calibration — written from a source-mapped algorithm "
            "dossier taken from PHD2 at commit 4a13cf245d7e485e79533697f87b0"
            "32b304df952. No PHD2 line was copied, but the logic derives from "
            "PHD2's, so PHD2's notice travels with it."),
    },
    {
        "name": "Predictive PEC / Gaussian process (ported)",
        "spdx": "BSD-3-Clause",
        "url": "https://github.com/OpenPHDGuiding/phd2/tree/master/contributions/MPI_IS_gaussian_process",
        "texts": [_file("MPI-IS Gaussian process licence",
                        "mpi-is-gaussian-process-bsd-3-clause.txt")],
        "notes": (
            "The guider's predictive periodic-error correction is ported from "
            "PHD2's MPI_IS_gaussian_process contribution by the Max Planck "
            "Institute for Intelligent Systems, Tuebingen.\n\n"
            "Algorithm reference: Edgar D. Klenske, Melanie N. Zeilinger, "
            "Bernhard Schoelkopf, Philipp Hennig, \"Gaussian Process Based "
            "Predictive Control for Periodic Error Correction\", IEEE "
            "Transactions on Control Systems Technology, vol. 24, no. 1, "
            "pp. 110-121, 2016."),
    },
    {
        "name": "N.I.N.A. and Hocus Focus algorithms (ported)",
        "spdx": "MPL-2.0",
        "url": "https://nighttime-imaging.eu/",
        "texts": [_file("Mozilla Public License 2.0", "mpl-2.0.txt")],
        "notes": (
            "Autofocus curve fitting and star detection were reimplemented from "
            "audited dossiers of NINA and the Hocus Focus plugin by George "
            "Hilios (https://github.com/ghilios/hocus-focus). No code was "
            "copied; both upstreams are MPL-2.0 and the resulting crates are "
            "MPL-2.0 too. "
            "Hocus Focus remains the template our autofocus follows, and its "
            "published design decisions continue to shape ours: the hyperbolic "
            "V-curve, excluding starless positions at the extremes of a wide "
            "sweep from the fit rather than letting them poison it (and saying "
            "so when it happens), statistical outlier rejection within a "
            "frame's star population, and its finding that loose detection "
            "settings admit noise and donut fragments that skew autofocus on "
            "wide or defocused sweeps - which is the same failure we measured "
            "on narrowband on 2026-08-17. Those were read from its public "
            "documentation and release notes, not from its source."),
    },
]
