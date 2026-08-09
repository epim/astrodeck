# Third-party licences — what we ship, what each licence asks, where we fall short

Task #194. Compliance audit plus the machinery that keeps it true: the credits
are **generated from the real manifests** (`tools/gen_credits.py`) and
`server/tests/test_credits.py` fails the build when a dependency exists with no
credit entry. The page is what the test protects, not the other way round.

**123 entries** across eleven groups, 100 distinct licence texts, all reproduced
in full and readable offline at **Settings → Credits**.

| Ecosystem | Entries | Source of truth |
|---|---|---|
| Python packages | 35 | `server/pyproject.toml` closed over the installed env |
| Rust crates | 46 | `native/Cargo.lock` |
| JavaScript packages | 9 | `ui/package-lock.json` (non-dev) |
| Fonts | 3 | same lockfile, split out — OFL is not the npm bucket |
| Network services | 8 | registry + a host-grep detector |
| External programs | 5 | registry + a binary-name detector |
| Derived algorithms | 3 | registry (`THIRD-PARTY-NOTICES.md` is the long form) |
| Catalogues and data | 4 | `server/astrodeck/catalog/data/` walk |
| Vendored binaries | 1 | `server/astrodeck/vendor/*` walk |
| AstroDeck's own | 5 | `native/Cargo.toml` workspace members |
| **Needs an owner decision** | **4** | lifted out of the groups above |

No GPL, LGPL, AGPL or SSPL anywhere in the stack. Nothing non-commercial is
bundled — ASTAP's Steinicke and HyperLEDA catalogues carry non-commercial terms
and `scripts/fetch_astap.py` deliberately leaves them behind.

---

## 1. Needs an owner decision — four items

These are not defects to fix; each is a decision about what AstroDeck is willing
to ship. They render first on the credits screen, above everything else, and
`test_copyleft_and_restricted_licences_are_surfaced_not_buried` fails if anything
demotes them.

### 1.1 DSS2 colour imagery — the serious one

**Release builds bundle ~45 MB of DSS2-colour sky tiles and nothing published
authorises that.**

The Digitized Sky Surveys are copyrighted, All Rights Reserved — Anglo-Australian
Observatory Board, Caltech, AURA, UK SERC/PPARC, depending on the plate. STScI's
only permission grant is a *use* grant, scoped to non-profit activity:

> "Scientists and educators conducting research, teaching (including textbooks),
> or other non-profit activities may use data from the copyrighted collections
> freely and without restriction… Commercial, for-profit use of the copyrighted
> collections is prohibited without written permission from the copyright
> holder(s)."

Third-party *redistribution* is not addressed anywhere, and colour DSS carries
its own explicit carve-out: *"For use of color DSS images not covered by above
use policy, contact archive@stsci.edu."*

CDS, who host the tiles, permit mirroring only where "the copyright on the
original data authorises this redistribution" — precisely the condition that
cannot be established. Their HiPS status for this survey is `clonableOnce`, which
a public release tarball cannot honour either (a clone must not itself be
re-cloneable).

Note the distinction that matters: **fetching tiles on demand for your own
viewing sits comfortably inside the use grant.** It is only the bundled offline
pack — `release.yml` → `survey_pack fetch --order 3` → `build_release.py
--survey-pack` — that has no cover.

**Options:** email archive@stsci.edu for written permission (CDS also ask to be
warned in advance of large mirrors, at cds-question@astro.unistra.fr), or swap
the offline pack for a survey with an actual open licence.

### 1.2 Astrospheric

Their Terms of Use: *"Unauthorized use, reproduction, distribution, or
exploitation of Astrospheric's assets, APIs, or content outside of the Service is
strictly prohibited."* Their API documentation scopes the Data API to
*"Astrospheric Professional members for use in personal projects"* and directs
public or commercial projects to contact them.

AstroDeck is a public product shipping an Astrospheric client. The mitigating
facts are real — each user supplies their own key, and their forecast is never
cached beyond display — but they are facts, not a permission.

Also unrecorded until now: Astrospheric's upstream is Environment and Climate
Change Canada, whose licence asks downstream users to carry *"Contains
information licenced under the Data Server End-use Licence of Environment and
Climate Change Canada."* That string is now on the credits page.

**Options:** written permission from Astrospheric, or remove the integration.

### 1.3 Player One Camera SDK — we redistribute six binaries on an inference

`server/astrodeck/vendor/playerone/LICENSE` is MIT-*shaped*: it ends with MIT's
notice-retention clause and MIT's warranty disclaimer word for word. But its
middle paragraph is Player One's own prose, and **that prose contains no
distribution verb** — not copy, publish, distribute, sublicense or sell. It says
*"You can use our company's products and this SDK to develop any products without
any restrictions"*, preceded by *"This SDK is only used for the secondary
development of our company's cameras or other equipment."*

The repo's own `vendor/playerone/README.md` states the licence "is MIT-style and
permits redistribution". That is an interpretation presented as a quotation, and
it is the kind of claim nobody re-checks. The credits deliberately do **not**
file this under MIT: doing so would be us asserting a permission the licensor
never wrote.

A reasonable reading is that redistribution is intended — a runtime library is
useless unless it ships, and the retained clause presupposes copies will be
distributed. **Options:** written confirmation from
support@player-one-astronomy.com, or stop shipping the binaries and require a
vendor install.

### 1.4 `libasi` — the declared source URL does not exist

`pyproject.toml`'s `asiair` extra installs from
`git+https://github.com/jewzaam/libasi`, and **that repository returns 404**. The
extra cannot be installed, and the comment beside it asserting "libasi is MIT"
cannot be verified against the URL we actually ship. A different repository of
the same name (`epim/libasi`) is MIT-licensed, but it is not the one declared.

**Options:** repoint the URL at a repository that exists and confirm its licence,
or drop the extra.

---

## 2. Gaps we did not close, and one we did

### Closed in this change

**Open-Meteo required a link, and we printed a name.** Their CC BY 4.0 terms are
specific: *"You must include a link next to any location Open-Meteo data are
displayed"*, with example markup. `weatherSourceLabel` named the source
truthfully but the label was plain text. The Sky Conditions chip row now wraps
that label in an anchor to `https://open-meteo.com/`. `weatherSourceLabel` itself
is unchanged, so its existing test still holds.

### Still open — attribution obligations we satisfy on the credits screen only

These are all now discharged **on the credits page**, which is a defensible place
for them. Listed because a reviewer should know they are not discharged anywhere
else, and because some upstreams ask for them "in any publications".

1. **OpenNGC's pass-through acknowledgements.** Its README asks redistributors to
   carry credits for NED, HyperLEDA, SIMBAD, HEASARC and Harold Corwin's NGC/IC
   positions. Currently absent. Not a licence condition (the CC BY-SA obligation
   runs to Mattia Verga), but it is what the upstream asks for.
2. **OpenNGC's snapshot is not pinned.** `server/tools/build_ngc_catalog.py`
   consumes a hand-downloaded `NGC.csv` with no recorded commit or checksum, so
   the exact input behind the shipped `ngc.tsv` cannot be identified from the
   repository. CC BY-SA does not require it; reproducibility does.
3. **IAU-CSN's licence version is inferred.** The catalogue file says "Creative
   Commons Attribution" with no version. CC BY 4.0 comes from the IAU's
   site-wide copyright statement, which is scoped to "images, videos and web
   texts on iau.org" — not explicitly to the data file. The credits say so
   rather than asserting 4.0 flatly.
4. **`EAF_focuser.h` carries no copyright line.** Its coverage is inherited from
   the sibling `LICENSE.txt`, which covers "the Software and associated
   documentation files". Sound, but inherited rather than stated.
5. **pySerial's wheel ships no licence file at all.** The published wheel has no
   `LICENSE` in its metadata; the text on the credits page was taken from the
   project's own `LICENSE.txt` and matches the SPDX header in
   `serial/__init__.py`. Recorded because it is a text we sourced rather than
   found.
6. **`THIRD-PARTY-NOTICES.md` omits everything we actually redistribute as
   binaries or data** — the ZWO SDK, the Player One SDK, OpenNGC and the IAU
   star names. It is thorough about the clean-room ports (PHD2, Max Planck,
   NINA) and about ASTAP/Gaia. The credits screen now covers the omissions; the
   markdown file was left alone on purpose, since another agent holds nearby
   files and the generated page is the durable home.

### Scope decisions, stated rather than hidden

* **Build-time-only tooling is not listed** — vite, tailwindcss, typescript,
  pytest and their 217 transitive dev dependencies. None of them place their own
  licensed code in a shipped artifact. This is a judgement; it is written on the
  credits page itself so a reader can disagree with it.
* **`references/`** holds ~500 MB of third-party binaries (Toupcam, QHYCCD,
  Altair, SBIG SDKs, MSVC redistributables) from cloned upstream repos. It is
  git-ignored, never packaged, and carries no redistribution exposure — but it is
  worth knowing it is on developer machines, because several of those vendor SDKs
  have restrictive terms.

---

## 3. Vendored-binary verdict

Ten committed binaries, all under `server/astrodeck/vendor/`. No other
third-party binary is tracked anywhere in the repo.

| Vendor | Binaries | Licence | Redistribution permitted? |
|---|---|---|---|
| ZWO (`ASICamera2.dll`, `EAF_focuser.dll`, `CAARotator.dll`, `macos/libASICamera2.dylib`) | 4 | Verbatim MIT, © 2015 ZWO Company | **Yes, explicitly.** The grant enumerates "publish, distribute, sublicense, and/or sell copies". Same basis INDI and NINA ship them on. |
| Player One (`PlayerOneCamera.dll` + 4 Linux `.so` + macOS `.dylib`) | 6 | Vendor licence, MIT-shaped | **Not explicitly — see §1.3.** |

Both vendors' notices travel with the binaries by construction:
`pyproject.toml`'s `[tool.setuptools.package-data]` forces the licence files into
the installed wheel, and `packaging/astrodeck.spec` copies the whole `vendor/`
tree into the PyInstaller binary. `test_every_vendored_binary_ships_its_licence_beside_it`
asserts that the package-data patterns actually match each licence file — a
`pip install` that shipped a DLL without its notice would break MIT's one
condition, silently.

ASTAP is **not** vendored in the repo (git-ignored; a 102 MB Gaia database in git
history is permanent and paid for by every clone). It is fetched at release time
and, as of the current `release.yml`, shipped with an explicit
`--allow-missing astap`.

---

## 4. How this stays true

```
python tools/gen_credits.py           # regenerate ui/src/credits.generated.json
python tools/gen_credits.py --check   # exit 1 if it is out of date
cd server && ./.venv/Scripts/python.exe -m pytest tests/test_credits.py
cd ui && npm test
```

Three modules:

* **`tools/licence_policy.py`** — the only place a judgement about a licence
  lives. Maps an SPDX expression onto the *obligations* it imposes (reproduce
  the notice / ship the text / carry NOTICE / state changes / attribute / share
  alike / do not sell the font alone), and raises on anything an owner must rule
  on. Conservative on purpose: any expression mentioning GPL or a use
  restriction flags, even when an OR offers a permissive branch, because
  distinguishing `MIT OR GPL-2.0` from `MIT AND GPL-2.0` needs a real SPDX
  parser and guessing permissively is the one error that ships a breach.
* **`tools/credits_registry.py`** — the parts with no manifest: vendored
  binaries, shipped data, network services, external programs, derived
  algorithms, and licence text for packages whose own wheel ships none.
* **`tools/gen_credits.py`** — walks the manifests, gathers licence text off
  disk, de-duplicates bodies into a content-addressed pool (~1.4 MB of text
  becomes 551 KB), and emits JSON with no timestamp, so regeneration is
  byte-stable.

Each category gets its **own detector**, because one detector cannot cover them
all:

| Category | What would otherwise rot | Detector |
|---|---|---|
| Python | a new transitive dep | closure over the installed env vs the entries |
| npm / Rust | a lockfile bump | name **and version** equality with the lockfile |
| Vendored binaries | a new vendor directory | walk `vendor/*` for binaries |
| Shipped data | a new `.tsv` | walk `catalog/data/` |
| Services | a new outbound host | grep the product source for hostnames |
| Programs | a program we stopped shipping | grep for the executable names |
| The page | a group silently truncated | mount it and count what rendered |

Derived algorithms are the one category with **no possible detector** — the
obligation comes from having read someone's source, and nothing on disk records
that. Said plainly in the registry rather than pretended otherwise.

---

## 5. Per-entry verdict

Generated from `ui/src/credits.generated.json`. "Satisfied?" means: does the
entry carry what its licence actually asks for, not merely name the licence.

### Needs an owner decision (4)

| Component | Version | Licence | What it requires | Satisfied? |
|---|---|---|---|---|
| Astrospheric | - | `LicenseRef-Astrospheric-Proprietary` | nothing | **NO — owner decision** |
| DSS2 colour imagery (bundled offline survey pack) | HEALPix order 3, ~1,020 tiles | `LicenseRef-DSS-AllRightsReserved` | nothing | **NO — owner decision** |
| libasi | unpinned (git URL) | `LicenseRef-Unverified-libasi` | nothing | **NO — owner decision** |
| Player One Camera SDK | 3.10.1 (Windows) / 3.10.0 (Linux, macOS) | `LicenseRef-PlayerOne-SDK` | reproduce notice | **NO — owner decision** |

### Vendored binaries (1)

| Component | Version | Licence | What it requires | Satisfied? |
|---|---|---|---|---|
| ZWO camera, focuser and rotator SDKs | ASICamera2 1.41.0.0, EAF 1.8.1, CAA 1.5.6 | `MIT` | reproduce notice | yes — text reproduced |

### Catalogues and data (4)

| Component | Version | Licence | What it requires | Satisfied? |
|---|---|---|---|---|
| 2MASS colour imagery | - | `LicenseRef-Terms-Of-Service` | nothing | n/a — nothing owed |
| Constellation assignments (IAU boundaries, via Astropy) | 13,370 rows | `Apache-2.0` | full text, carry NOTICE, state changes | yes — text reproduced |
| IAU Catalog of Star Names (IAU-CSN) | 2022-04-04 edition, cut at V <= 4.00 | `CC-BY-4.0` | attribution, full text, state changes | yes — text reproduced |
| OpenNGC deep-sky catalogue | extract of 13,369 objects | `CC-BY-SA-4.0` | attribution, full text, state changes, share-alike | yes — text reproduced |

### Network services (8)

| Component | Version | Licence | What it requires | Satisfied? |
|---|---|---|---|---|
| CDS - Centre de Donnees astronomiques de Strasbourg | - | `LicenseRef-CDS-Credit` | attribution | yes — credited on the page |
| ESA / ESAC Science Data Centre | - | `LicenseRef-ESA-Credit` | attribution | yes — credited on the page |
| GitHub | - | `LicenseRef-Terms-Of-Service` | nothing | n/a — nothing owed |
| Google Identity (OpenID Connect) | - | `LicenseRef-Terms-Of-Service` | nothing | n/a — nothing owed |
| Iowa Environmental Mesonet (Iowa State University) | - | `LicenseRef-Public-Domain` | nothing | n/a — nothing owed |
| Open-Meteo | - | `CC-BY-4.0` | attribution, full text, state changes | yes — text reproduced |
| SourceForge | - | `LicenseRef-Terms-Of-Service` | nothing | n/a — nothing owed |
| Telegram Bot API | - | `LicenseRef-Terms-Of-Service` | nothing | n/a — nothing owed |

### External programs (5)

| Component | Version | Licence | What it requires | Satisfied? |
|---|---|---|---|---|
| ASCOM Platform and Alpaca | - | `LicenseRef-Terms-Of-Service` | nothing | n/a — nothing owed |
| ASTAP | command-line solver (astap_cli) | `MPL-2.0` | full text, source for modified files | yes — text reproduced |
| ESA Gaia star databases (via ASTAP) | - | `Gaia-DPAC` | attribution | yes — credited on the page |
| N.I.N.A. (Nighttime Imaging 'N' Astronomy) | - | `MPL-2.0` | full text, source for modified files | yes — text reproduced |
| PHD2 | - | `BSD-3-Clause` | reproduce notice | yes — text reproduced |

### Derived algorithms (3)

| Component | Version | Licence | What it requires | Satisfied? |
|---|---|---|---|---|
| N.I.N.A. and Hocus Focus algorithms (ported) | - | `MPL-2.0` | full text, source for modified files | yes — text reproduced |
| PHD2 guiding algorithms (ported) | - | `BSD-3-Clause` | reproduce notice | yes — text reproduced |
| Predictive PEC / Gaussian process (ported) | - | `BSD-3-Clause` | reproduce notice | yes — text reproduced |

### Python packages (35)

| Component | Version | Licence | What it requires | Satisfied? |
|---|---|---|---|---|
| annotated-doc | 0.0.4 | `MIT` | reproduce notice | yes — text reproduced |
| annotated-types | 0.7.0 | `MIT License` | reproduce notice | yes — text reproduced |
| anyio | 4.13.0 | `MIT` | reproduce notice | yes — text reproduced |
| astropy | 7.2.0 | `BSD-3-Clause` | reproduce notice | yes — text reproduced |
| astropy-iers-data | 0.2026.6.8.17.49.5 | `BSD-3-Clause` | reproduce notice | yes — text reproduced |
| astropy_healpix | 2.0.0 | `BSD 3-Clause` | reproduce notice | yes — text reproduced |
| bcrypt | 5.0.0 | `Apache-2.0` | full text, carry NOTICE, state changes | yes — text reproduced |
| certifi | 2026.5.20 | `MPL-2.0` | full text, source for modified files | yes — text reproduced |
| cffi | 2.0.0 | `MIT` | reproduce notice | yes — text reproduced |
| click | 8.4.1 | `BSD-3-Clause` | reproduce notice | yes — text reproduced |
| colorama | 0.4.6 | `BSD License` | reproduce notice | yes — text reproduced |
| comtypes | >=1.4.0 (Windows only) | `MIT` | reproduce notice | yes — text reproduced |
| cryptography | 49.0.0 | `Apache-2.0 OR BSD-3-Clause` | full text, carry NOTICE, state changes, reproduce notice | yes — text reproduced |
| fastapi | 0.136.3 | `MIT` | reproduce notice | yes — text reproduced |
| h11 | 0.16.0 | `MIT` | reproduce notice | yes — text reproduced |
| httpcore | 1.0.9 | `BSD-3-Clause` | reproduce notice | yes — text reproduced |
| httptools | 0.8.0 | `MIT` | reproduce notice | yes — text reproduced |
| httpx | 0.28.1 | `BSD-3-Clause` | reproduce notice | yes — text reproduced |
| idna | 3.18 | `BSD-3-Clause` | reproduce notice | yes — text reproduced |
| numpy | 2.4.6 | `BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0` | reproduce notice | yes — text reproduced |
| packaging | 26.2 | `Apache-2.0 OR BSD-2-Clause` | full text, carry NOTICE, state changes, reproduce notice | yes — text reproduced |
| pillow | 12.2.0 | `MIT-CMU` | reproduce notice | yes — text reproduced |
| pycparser | 3.0 | `BSD-3-Clause` | reproduce notice | yes — text reproduced |
| pydantic | 2.13.4 | `MIT` | reproduce notice | yes — text reproduced |
| pydantic_core | 2.46.4 | `MIT` | reproduce notice | yes — text reproduced |
| pyerfa | 2.0.1.5 | `BSD 3-Clause License` | reproduce notice | yes — text reproduced |
| pyserial | 3.5 | `BSD-3-Clause` | reproduce notice | yes — text reproduced |
| python-dotenv | 1.2.2 | `BSD-3-Clause` | reproduce notice | yes — text reproduced |
| PyYAML | 6.0.3 | `MIT` | reproduce notice | yes — text reproduced |
| starlette | 1.3.0 | `BSD-3-Clause` | reproduce notice | yes — text reproduced |
| typing-inspection | 0.4.2 | `MIT` | reproduce notice | yes — text reproduced |
| typing_extensions | 4.15.0 | `PSF-2.0` | reproduce notice | yes — text reproduced |
| uvicorn | 0.49.0 | `BSD-3-Clause` | reproduce notice | yes — text reproduced |
| watchfiles | 1.2.0 | `MIT` | reproduce notice | yes — text reproduced |
| websockets | 16.0 | `BSD-3-Clause` | reproduce notice | yes — text reproduced |

### JavaScript packages (9)

| Component | Version | Licence | What it requires | Satisfied? |
|---|---|---|---|---|
| @types/prop-types | 15.7.15 | `MIT` | reproduce notice | yes — text reproduced |
| @types/react | 18.3.31 | `MIT` | reproduce notice | yes — text reproduced |
| csstype | 3.2.3 | `MIT` | reproduce notice | yes — text reproduced |
| js-tokens | 4.0.0 | `MIT` | reproduce notice | yes — text reproduced |
| loose-envify | 1.4.0 | `MIT` | reproduce notice | yes — text reproduced |
| react | 18.3.1 | `MIT` | reproduce notice | yes — text reproduced |
| react-dom | 18.3.1 | `MIT` | reproduce notice | yes — text reproduced |
| scheduler | 0.23.2 | `MIT` | reproduce notice | yes — text reproduced |
| zustand | 5.0.14 | `MIT` | reproduce notice | yes — text reproduced |

### Fonts (3)

| Component | Version | Licence | What it requires | Satisfied? |
|---|---|---|---|---|
| @fontsource/chakra-petch | 5.2.7 | `OFL-1.1` | full text, not sold alone, reserved font name | yes — text reproduced |
| @fontsource/ibm-plex-mono | 5.2.7 | `OFL-1.1` | full text, not sold alone, reserved font name | yes — text reproduced |
| @fontsource/ibm-plex-sans | 5.2.8 | `OFL-1.1` | full text, not sold alone, reserved font name | yes — text reproduced |

### Rust crates (46)

| Component | Version | Licence | What it requires | Satisfied? |
|---|---|---|---|---|
| approx | 0.5.1 | `Apache-2.0` | full text, carry NOTICE, state changes | yes — text reproduced |
| autocfg | 1.5.1 | `Apache-2.0 OR MIT` | full text, carry NOTICE, state changes, reproduce notice | yes — text reproduced |
| bytemuck | 1.25.1 | `Zlib OR Apache-2.0 OR MIT` | reproduce notice, full text, carry NOTICE, state changes | yes — text reproduced |
| cfg-if | 1.0.4 | `MIT OR Apache-2.0` | reproduce notice, full text, carry NOTICE, state changes | yes — text reproduced |
| crossbeam-deque | 0.8.6 | `MIT OR Apache-2.0` | reproduce notice, full text, carry NOTICE, state changes | yes — text reproduced |
| crossbeam-epoch | 0.9.18 | `MIT OR Apache-2.0` | reproduce notice, full text, carry NOTICE, state changes | yes — text reproduced |
| crossbeam-utils | 0.8.21 | `MIT OR Apache-2.0` | reproduce notice, full text, carry NOTICE, state changes | yes — text reproduced |
| either | 1.16.0 | `MIT OR Apache-2.0` | reproduce notice, full text, carry NOTICE, state changes | yes — text reproduced |
| heck | 0.5.0 | `MIT OR Apache-2.0` | reproduce notice, full text, carry NOTICE, state changes | yes — text reproduced |
| indoc | 2.0.7 | `MIT OR Apache-2.0` | reproduce notice, full text, carry NOTICE, state changes | yes — text reproduced |
| libc | 0.2.186 | `MIT OR Apache-2.0` | reproduce notice, full text, carry NOTICE, state changes | yes — text reproduced |
| matrixmultiply | 0.3.10 | `MIT/Apache-2.0` | reproduce notice, full text, carry NOTICE, state changes | yes — text reproduced |
| memoffset | 0.9.1 | `MIT` | reproduce notice | yes — text reproduced |
| nalgebra | 0.33.3 | `Apache-2.0` | full text, carry NOTICE, state changes | yes — text reproduced |
| nalgebra-macros | 0.2.2 | `Apache-2.0` | full text, carry NOTICE, state changes | yes — text reproduced |
| ndarray | 0.16.1 | `MIT OR Apache-2.0` | reproduce notice, full text, carry NOTICE, state changes | yes — text reproduced |
| num-bigint | 0.4.8 | `MIT OR Apache-2.0` | reproduce notice, full text, carry NOTICE, state changes | yes — text reproduced |
| num-complex | 0.4.6 | `MIT OR Apache-2.0` | reproduce notice, full text, carry NOTICE, state changes | yes — text reproduced |
| num-integer | 0.1.46 | `MIT OR Apache-2.0` | reproduce notice, full text, carry NOTICE, state changes | yes — text reproduced |
| num-rational | 0.4.2 | `MIT OR Apache-2.0` | reproduce notice, full text, carry NOTICE, state changes | yes — text reproduced |
| num-traits | 0.2.19 | `MIT OR Apache-2.0` | reproduce notice, full text, carry NOTICE, state changes | yes — text reproduced |
| numpy | 0.22.1 | `BSD-2-Clause` | reproduce notice | yes — text reproduced |
| once_cell | 1.21.4 | `MIT OR Apache-2.0` | reproduce notice, full text, carry NOTICE, state changes | yes — text reproduced |
| paste | 1.0.15 | `MIT OR Apache-2.0` | reproduce notice, full text, carry NOTICE, state changes | yes — text reproduced |
| portable-atomic | 1.13.1 | `Apache-2.0 OR MIT` | full text, carry NOTICE, state changes, reproduce notice | yes — text reproduced |
| portable-atomic-util | 0.2.7 | `Apache-2.0 OR MIT` | full text, carry NOTICE, state changes, reproduce notice | yes — text reproduced |
| proc-macro2 | 1.0.106 | `MIT OR Apache-2.0` | reproduce notice, full text, carry NOTICE, state changes | yes — text reproduced |
| pyo3 | 0.22.6 | `MIT OR Apache-2.0` | reproduce notice, full text, carry NOTICE, state changes | yes — text reproduced |
| pyo3-build-config | 0.22.6 | `MIT OR Apache-2.0` | reproduce notice, full text, carry NOTICE, state changes | yes — text reproduced |
| pyo3-ffi | 0.22.6 | `MIT OR Apache-2.0` | reproduce notice, full text, carry NOTICE, state changes | yes — text reproduced |
| pyo3-macros | 0.22.6 | `MIT OR Apache-2.0` | reproduce notice, full text, carry NOTICE, state changes | yes — text reproduced |
| pyo3-macros-backend | 0.22.6 | `MIT OR Apache-2.0` | reproduce notice, full text, carry NOTICE, state changes | yes — text reproduced |
| quote | 1.0.46 | `MIT OR Apache-2.0` | reproduce notice, full text, carry NOTICE, state changes | yes — text reproduced |
| rawpointer | 0.2.1 | `MIT/Apache-2.0` | reproduce notice, full text, carry NOTICE, state changes | yes — text reproduced |
| rayon | 1.12.0 | `MIT OR Apache-2.0` | reproduce notice, full text, carry NOTICE, state changes | yes — text reproduced |
| rayon-core | 1.13.0 | `MIT OR Apache-2.0` | reproduce notice, full text, carry NOTICE, state changes | yes — text reproduced |
| rustc-hash | 1.1.0 | `Apache-2.0/MIT` | full text, carry NOTICE, state changes, reproduce notice | yes — text reproduced |
| rustversion | 1.0.22 | `MIT OR Apache-2.0` | reproduce notice, full text, carry NOTICE, state changes | yes — text reproduced |
| safe_arch | 0.7.4 | `Zlib OR Apache-2.0 OR MIT` | reproduce notice, full text, carry NOTICE, state changes | yes — text reproduced |
| simba | 0.9.1 | `Apache-2.0` | full text, carry NOTICE, state changes | yes — text reproduced |
| syn | 2.0.118 | `MIT OR Apache-2.0` | reproduce notice, full text, carry NOTICE, state changes | yes — text reproduced |
| target-lexicon | 0.12.16 | `Apache-2.0 WITH LLVM-exception` | full text, carry NOTICE, state changes | yes — text reproduced |
| typenum | 1.20.1 | `MIT OR Apache-2.0` | reproduce notice, full text, carry NOTICE, state changes | yes — text reproduced |
| unicode-ident | 1.0.24 | `(MIT OR Apache-2.0) AND Unicode-3.0` | reproduce notice, full text, carry NOTICE, state changes | yes — text reproduced |
| unindent | 0.2.4 | `MIT OR Apache-2.0` | reproduce notice, full text, carry NOTICE, state changes | yes — text reproduced |
| wide | 0.7.33 | `Zlib OR Apache-2.0 OR MIT` | reproduce notice, full text, carry NOTICE, state changes | yes — text reproduced |

### AstroDeck's own components (5)

| Component | Version | Licence | What it requires | Satisfied? |
|---|---|---|---|---|
| astro-focus | 0.1.0 | `MPL-2.0` | full text, source for modified files | yes — text reproduced |
| astro-guide | 0.1.0 | `Apache-2.0` | full text, carry NOTICE, state changes | yes — text reproduced |
| astro-star | 0.1.0 | `MPL-2.0` | full text, source for modified files | yes — text reproduced |
| astro-tppa | 0.1.0 | `MPL-2.0` | full text, source for modified files | yes — text reproduced |
| astrodeck-native | 0.1.0 | `MPL-2.0` | full text, source for modified files | yes — text reproduced |
