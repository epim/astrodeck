# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
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
    "sgp4": {
        # The wheel's metadata carries `License-Expression: MIT` and the wheel
        # DOES ship its own LICENSE file, so the generator finds the text
        # without this entry. The entry exists for the other two fields: the
        # SPDX id, pinned so a future metadata change cannot quietly widen it,
        # and the note, which is the only place the reader is told what this
        # package is doing in an astrophotography controller.
        "spdx": "MIT",
        "title": "LICENSE (from the python-sgp4 wheel)",
        "body": _text("sgp4-mit.txt"),
        "note": (
            "Turns a satellite's two-line element set into a position. By "
            "Brandon Rhodes, and it is the reference implementation of AIAA "
            "2006-6753 rather than a reading of it — the C++ that paper ships, "
            "ported, with the paper's own verification corpus (SGP4-VER.TLE "
            "and tcppver.out) inside the package. AstroDeck's test suite "
            "propagates that corpus on every run.\n\n"
            "Chosen over Skyfield, which is by the same author and equally "
            "correct on satellites, because Skyfield's planetary half wants a "
            "downloaded JPL kernel and this app has to work at a dark site "
            "with no network. The package also carries its verification corpus; it does not need a downloaded planetary kernel."),
    },
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


# These build dependencies place their own licensed runtime/helper or CSS bytes
# in the actual SPA. The October artifact audit observed their output.
NPM_RUNTIME_CONTRIBUTORS = {
    "vite": "Runtime module-preload, preload and bundled CommonJS helpers. Vite's LICENSE.md also carries the bundled plugin notices.",
    "tailwindcss": "The shipped CSS includes Tailwind's preflight rules; build-time classification does not erase their MIT notice.",
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
            "development of our company's cameras\". The inspected Windows executable and examined published "
            "archives contain six compiled libraries; the source tarball excludes them. Owner decision: get written confirmation "
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
            "inference, not a grant. The fresh Windows executable and examined "
            "published archives contain all six SDK libraries, while the inspected "
            "source tarball excludes them. Written confirmation from "
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
            "Delporte in 1930, implemented by Astropy using the Roman et al. 1987 boundary table; the code that does the "
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
        "spdx": 'LicenseRef-IAU-CSN-CC-Attribution-Unversioned',
        "url": 'https://www.pas.rochester.edu/~emamajek/WGSN/IAU-CSN.txt',
        "texts": [],
        "notes": (
            "IAU Working Group on Star Names catalogue, 2022-04-04 edition. AstroDeck embeds 241 rows in brightstars.py, retaining names, positions and magnitudes for stars with V <= 4.00. The cut and reduced columns are AstroDeck's changes; the retained values are unaltered.\n\nThe catalogue header names Creative Commons Attribution but no version. The previous CC BY 4.0 label depended on a separate IAU website inference whose source could not be reverified. This entry preserves that uncertainty rather than treating the inference as a specific grant."),
        'flag': 'The 2022-04-04 source header grants Creative Commons Attribution without specifying a version. A 4.0 grant has not been independently established; preserve attribution and obtain a version clarification.',
        'requires': ['attribution', 'state-changes'],
    },
    {
        "name": 'DSS2 imagery (optional remote survey)',
        "version": 'CDS/P/DSS2/color',
        "spdx": 'ODbL-1.0 AND LicenseRef-DSS-AllRightsReserved',
        "texts": [_file("Open Database License 1.0", "bundled-odbl-1.0.txt")],
        "url": 'https://archive.stsci.edu/dss/copyright.html',
        "flag": (
            "Original-image redistribution permission remains unresolved. CDS's ODbL grant covers its HiPS database and does not replace the DSS image copyright. Do not enable release bundling without owner clearance."),
        "notes": (
            'Optional Atlas imagery fetched to the operator\'s cache. The inspected local source tarball contains no survey pack; source staging and seeding reject bundled DSS2 color. The frozen build specification has a separate whole-directory inclusion path and must be checked on its own. Offline Atlas uses the schematic sky without downloaded tiles.\n\nThe current CDS color record declares ODbL-1.0 for the HiPS database, with CNRS/Unistra copyright and a one-generation cloning status. Original DSS image rights remain separate. STScI permits specified nonprofit uses and requires written permission for commercial uses outside that grant. Fetching locally does not settle every deployment or public-output use.\n\nAcknowledgement: "The Digitized Sky Surveys were produced at the Space Telescope Science Institute under U.S. Government grant NAG W-2166. The images of these surveys are based on photographic data obtained using the Oschin Schmidt Telescope on Palomar Mountain and the UK Schmidt Telescope. The plates were processed into the present compressed digital form with the permission of these institutions." Colourised and HEALPixed by CDS. When publicly using the database or a produced work, retain the applicable ODbL database attribution and licence reference; adapted databases may require share-alike and access to the database or alterations. These conditions do not license AstroDeck application code under ODbL.'),
        'group': 'remote-data',
        'requires': [lp.LICENSE_TEXT, lp.ATTRIBUTION, lp.SHARE_ALIKE],
        'tier': 'remote-data',
    },
    {
        "name": '2MASS colour imagery (optional remote survey)',
        "spdx": 'ODbL-1.0 AND LicenseRef-2MASS-Acknowledgement',
        "texts": [_file("Open Database License 1.0", "bundled-odbl-1.0.txt")],
        "url": 'https://alasky.cds.unistra.fr/MocServer/query?ID=CDS/P/2MASS/color&fmt=html&get=record',
        "notes": (
            'Optional 2MASS Atlas imagery fetched on demand to the operator\'s cache. No 2MASS tiles occur in the inspected local source tarball. The current CDS HiPS record declares ODbL-1.0 for its database, with CNRS/Unistra copyright. That database licence is separate from the original 2MASS data products.\n\nAcknowledgement: "This publication makes use of data products from the Two Micron All Sky Survey, which is a joint project of the University of Massachusetts and the Infrared Processing and Analysis Center/California Institute of Technology, funded by the National Aeronautics and Space Administration and the National Science Foundation." Public use of the database or a produced work needs the applicable CDS database attribution and ODbL licence reference. Adapted databases can carry share-alike and database-access obligations; this is not a licence on AstroDeck application code.'),
        'group': 'remote-data',
        'requires': [lp.LICENSE_TEXT, lp.ATTRIBUTION, lp.SHARE_ALIKE],
        'tier': 'remote-data',
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
        "spdx": 'LicenseRef-NOAA-NODD-Terms',
        "url": 'https://registry.opendata.aws/noaa-goes/',
        "hosts": [
            "noaa-goes18.s3.amazonaws.com",
            "noaa-goes19.s3.amazonaws.com",
            "s3.amazonaws.com",
        ],
        "notes": (
            "NOAA GOES-R ABI clear-sky-mask and cloud-top-height products feed AstroDeck's cloud model. Granules are downloaded from public NOAA buckets and cached on the operator's machine; none occur in the inspected local source tarball.\n\nNOAA permits use of the NODD data and requests source attribution for unaltered data. Its terms prohibit implying NOAA endorsement or affiliation and presenting modified data as original NOAA data. AstroDeck's cloud probability, height projection and motion displays are processed outputs of AstroDeck, not NOAA forecasts or NOAA-validated safety decisions."),
        'requires': ['attribution', 'state-changes'],
    },
    {
        "name": "Open-Meteo",
        "spdx": 'CC-BY-4.0',
        "url": 'https://open-meteo.com/en/licence',
        "hosts": ["api.open-meteo.com"],
        "texts": [_file("CC BY 4.0 legal code", "cc-by-4.0.txt")],
        "notes": (
            "Open-Meteo forecasts supply weather conditions, wind readouts and derived forecast-cloud bands. The data are CC BY 4.0: credit Open-Meteo, link the licence and identify changes. Open-Meteo additionally requires a source link beside every location displaying its data. The Conditions views have these links; newer wind and cloud-band displays still need them.\n\nThe free API endpoint is limited to non-commercial use and published request limits. Commercial use requires the appropriate paid service. These service conditions are separate from the data licence. Public OSS client distribution does not establish an exception for every deployment or the aggregate traffic from many installs. The normal refresh is 15 minutes with a bounded retry; this is not a guarantee about shared-IP traffic.\n\nDerived cloud bands and app summaries are AstroDeck processing of the provider's forecast. AstroDeck does not redistribute the separately licensed Open-Meteo server implementation."),
        'requires': ['attribution', 'license', 'state-changes'],
    },
    {
        "name": "Astrospheric",
        "spdx": 'LicenseRef-Astrospheric-Proprietary',
        "url": 'https://www.astrospheric.com/privacypolicy',
        "hosts": ["astrosphericpublicaccess.azurewebsites.net"],
        "flag": (
            "Public OSS integration and many independent installs remain an owner permission question under the API's personal-project scope. Per-user keys and an instance acknowledgement do not establish vendor permission. Obtain written clarification covering this use."),
        "notes": (
            'Optional seeing and transparency forecasts use the operator\'s own Astrospheric key and an in-memory forecast cache. Current v1 and v2 API documentation describe access for Professional members\' personal projects; broader uses are directed to the vendor.\n\nOur reading: the public client and its independent deployments need clarification from Astrospheric. The checked terms do not by themselves prove that publishing independently written client source is infringement, nor do a user\'s key and acknowledgement prove this public integration is authorized. The existing acknowledgement records the operator\'s assertion only.\n\nUpstream acknowledgement: "Contains information licenced under the Data Server End-use Licence of Environment and Climate Change Canada." Licence: https://eccc-msc.github.io/open-data/licence/readme_en/. ECCC\'s underlying data permission does not replace Astrospheric\'s service terms.'),
        'requires': ['attribution'],
    },
    {
        "name": "CelesTrak",
        # Service access conditions are separate from the origin of the data.
        "spdx": 'LicenseRef-CelesTrak-Service-Terms',
        "url": 'https://celestrak.org/usage-policy.php',
        "hosts": ["celestrak.org"],
        "notes": (
            'CelesTrak supplies the visual GP group and selected catalogue IDs as JSON. AstroDeck keeps an operator-local cache; no active orbital-element cache occurs in the inspected local source tarball. The normal scheduled refresh is 12 hours and requests identify the AstroDeck client.\n\nCurrent CelesTrak policy says to stop automated queries after a non-200 response and report the problem for human investigation. It now describes GP updates every two hours. The nominal AstroDeck interval is conservative, but the failure and manual-refresh paths do not prove this policy is honored. Service conditions are distinct from the origins of the underlying observations; a public-domain label does not dispose of those conditions.'),
        'flag': "Current error paths can keep retrying a due cache every minute and continue individual queries after group failure. This conflicts with CelesTrak's stop-on-error instructions and needs a separate behavior fix.",
        'requires': [],
    },
    {
        "name": "IAU Minor Planet Center",
        "spdx": 'LicenseRef-MPC-Data-Terms',
        "url": 'https://docs.minorplanetcenter.net/mpc-ops-docs/faqs/',
        "hosts": ["minorplanetcenter.net", "www.minorplanetcenter.net"],
        "notes": (
            "The Minor Planet Center supplies CometEls.txt orbital elements. AstroDeck caches these on the operator's machine. No comet-element cache occurs in the inspected local source tarball. The normal refresh interval is one week; failure and manual-refresh paths can make additional requests.\n\nMPC says its database is freely available to the public and asks clients to avoid excessive requests. This is evidence for the intended runtime access, not an explicit public-domain dedication. Acknowledge the MPC, its cited funders and the observers whose measurements support the computed orbits."),
        'flag': 'The checked MPC FAQ says data are freely available, but does not establish CC0 or a public-domain dedication. Confirm terms before proposing bulk redistribution.',
        'requires': ['attribution'],
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
        "spdx": 'LicenseRef-CDS-Survey-Terms',
        "url": "https://cds.unistra.fr/",
        "hosts": ["alasky.cds.unistra.fr", "alaskybis.cds.unistra.fr"],
        "notes": (
            'CDS operates the HiPS tiles and hips2fits cutout services used for optional Atlas imagery. AstroDeck acknowledges the Aladin sky atlas and HiPS services developed at CDS, Universite de Strasbourg/CNRS.\n\nSurvey-specific records govern the database and original-image rights. The checked DSS2 color and 2MASS color records declare ODbL-1.0 for their HiPS databases; original-image terms remain separate. Preserve CDS and originating-survey credits and applicable database notices with public use. No survey pack occurs in the inspected local source tarball. Offline Atlas can draw a schematic sky.'),
        'requires': ['attribution'],
    },
    {
        "name": "ESA / ESAC Science Data Centre",
        "spdx": 'LicenseRef-ESA-Survey-Mirror',
        "url": "https://www.cosmos.esa.int/",
        "hosts": ["skies.esac.esa.int"],
        "texts": [],
        "notes": (
            "ESA/ESAC supplies the first configured mirror for DSS2 color and 2MASS color tiles. Credit ESA as the mirror operator and retain CDS and originating-survey acknowledgements.\n\nThese are third-party survey holdings. ESA's general licence for its own public material is not evidence that it relicenses DSS or 2MASS. Use the survey-specific notices and database terms; the checked CDS records describe the ESA copies as unclonable mirrors."),
        'requires': ['attribution'],
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
