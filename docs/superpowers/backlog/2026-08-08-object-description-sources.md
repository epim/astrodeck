# Where object descriptions should come from

Researched 2026-08-08. Every coverage number below was measured against this
repo's own `server/astrodeck/catalog/data/ngc.tsv` (13,369 rows) plus the one
curated-only row (`Sh2-155`), i.e. the 13,370 objects `objects.CATALOG` holds.
Every licence quote was read from the source, not recalled. Where a source is
silent about redistribution, this document says "silent", because that is the
finding.

The question this answers: *the owner asked whether Wikipedia is really the
best source for a catalogue like this, or whether it should be joined only
where appropriate. It is the second one, and the numbers are lopsided.*

---

## The short answer

**Wikipedia reaches 3,288 of 13,370 objects (24.6%), and for 38% of those its
opening sentence says exactly what `describe.py` already prints.** It is a
link-out, not a pack.

**The best source is the one already vendored.** OpenNGC's own discarded
columns give 12,135 rows (90.8%) a true new clause for 410 KB, under a licence
this repo has already accepted. Nothing else comes close on value per megabyte.

Recommended stack, about 990 KB total:

| Layer | Reaches | Size | Licence | Verdict |
|---|---|---|---|---|
| 0. Composed sentence (shipped) | 13,370 (100%) | 0 | ours | the floor, keep it |
| 1. OpenNGC columns we threw away | 12,135 (90.8%) | 410 KB | CC BY-SA 4.0, already accepted | **do this first** |
| 2. Wikidata discovery provenance | 7,874 (58.9%) | 335 KB | **CC0**, no obligation at all | do this second |
| 3. Dreyer 1888 via Corwin/Erdmann | 7,572 (56.6%) | 200 KB + 4 KB key | CC BY-SA 3.0 over PD text | do this third, NGC only |
| 4. Wikipedia | 3,288 (24.6%) | 40 KB as links | CC BY-SA 4.0 | **link out, do not vendor** |

Layers 1 to 3 together leave **718 rows (5.4%)** with nothing but the composed
sentence. That is the honest fallback and it is discussed in its own section.

Ranked by value per megabyte, with the caveat that the densest is not the most
useful:

1. **Dreyer** is densest at 37,900 rows/MB, and its median payload is `vF, vS`.
2. **OpenNGC columns** at 30,300 rows/MB, and every clause is a real fact.
3. **Wikidata provenance** at 23,500 rows/MB, and it is the only source that
   says who found the thing and when.
4. **Wikipedia vendored** at 2,050 rows/MB. Worst on both axes.

---

## 1. What upstream OpenNGC carries that we discarded

`tools/build_ngc_catalog.py` keeps 8 of upstream's **32 columns**. Upstream is
`NGC.csv` (13,970 rows, 3.7 MB) plus `addendum.csv` (64 rows). Population below
is measured over the **13,369 rows we actually carry**, not over upstream's
full file, because a column that is full for objects we dropped is not a
feature either.

| Column | Populated | % | Bytes if re-imported | Worth it? |
|---|---|---|---|---|
| `Const` | 13,369 | 100.0 | 52 KB | **No.** We already compute it. See §1.1 |
| `Identifiers` | 12,231 | 91.5 | 870 KB | Not description, but see §1.3 |
| `MajAx` (have it) | 12,071 | 90.3 | | already carried as `size_arcmin` |
| `B-Mag` | 11,378 | 85.1 | | already folded into `mag` |
| `MinAx` | 11,092 | 83.0 | 57 KB | **Yes.** Axis ratio gives "edge-on" |
| `PosAng` | 10,775 | 80.6 | 38 KB | Yes, framing already wants it |
| `RadVel` / `Redshift` | 10,639 | 79.6 | 97 KB (z alone) | **Yes.** Distance, see §1.2 |
| `SurfBr` | 10,267 | 76.8 | 63 KB | Yes, a published number beats our derived one |
| `Hubble` | 10,201 | 76.3 | 39 KB | **Yes, the single best row in this table** |
| `J/H/K-Mag` | ~9,700 | 72.5 | 242 KB | No. Infrared, not what an imager plans against |
| `V-Mag` | 4,266 | 31.9 | | already folded into `mag` |
| `NED notes` | 2,287 | 17.1 | 102 KB | **Yes, with a filter.** See §1.4 |
| `Pm-RA` / `Pm-Dec` | 1,069 | 8.0 | | No |
| `Pax` | 736 | 5.5 | | No |
| `IC` cross-ref | 377 | 2.8 | | folded into `alias` already |
| `NGC` cross-ref | 337 | 2.5 | | as above |
| `OpenNGC notes` | 190 | 1.4 | 24 KB | No. Corwin identity disputes, expert-facing |
| `Common names` | 151 | 1.1 | 16 KB | **Yes, all of them.** See §1.5 |
| `M` | 109 | 0.8 | | already the id for those rows |
| `Cstar B/V/U-Mag`, `Cstar Names` | 16 to 111 | 0.1 to 0.8 | | No |
| `Sources` | 13,369 | 100.0 | | provenance codes, useful in a build script only |

The recommended descriptive subset (`Hubble`, `SurfBr`, `MinAx`, `PosAng`,
`Redshift`, all `Common names`, `NED notes`) costs **410 KB** on top of the
present 575 KB `ngc.tsv`. Re-importing everything costs 3.7 MB.

Upstream declares its licence via REUSE, not a `LICENSE` file: `.reuse/dep5`
says `Files: * / License: CC-BY-SA-4.0`, and `LICENSES/CC-BY-SA-4.0.txt` is the
full 18,375-byte text. GitHub's own licence detector reports `null` for the
repo, which is why it can look unlicensed at a glance. It is not.

### 1.1 Constellations: ours and upstream's agree, and the 7 that do not are not a bug

Upstream has a `Const` column, filled for 100% of our rows. This afternoon's
`build_constellations.py` computed the same thing with astropy. They were
compared row by row:

```
agree     13,362
disagree       7   (0.05%)
```

All seven disagreements sit **within 0.5 arcmin of the IAU boundary**, measured
by walking outward from each position until the constellation flips:

| Object | astropy (ours) | OpenNGC | Distance to the boundary |
|---|---|---|---|
| NGC 1641 | Dorado | Reticulum | under 0.5' |
| NGC 1692 | Lepus | Eridanus | under 0.5' |
| NGC 6079 | Draco | Ursa Minor | under 0.5' |
| NGC 630 | Phoenix | Sculptor | under 0.5' |
| NGC 6832 | Draco | Cygnus | under 0.5' |
| NGC 6946 | Cepheus | Cygnus | under 0.5' |
| NGC 7438 | Lacerta | Cassiopeia | under 0.5' |

NGC 6946 is the one anybody would notice, and it is the one the literature also
refuses to decide: the English Wikipedia infobox reads "Cepheus and Cygnus",
and the article says the galaxy "straddles the boundary between the northern
constellations of Cepheus and Cygnus". A boundary object is a boundary object.

**Do not re-import `Const`.** Ours is computed from the positions we actually
ship, with the B1875 precession the epoch trap in `build_constellations.py`
documents. Importing a second answer would create two sources of truth for
0.05% disagreement and no benefit. What is worth doing is writing these seven
into `tests/test_catalog_constellations.py` as known boundary cases, so a future
OpenNGC pull that moves one of them is visible rather than silent.

### 1.2 Hubble type and redshift are the win

`Hubble` has only **30 distinct non-blank values** across 10,201 rows:

```
E 1553   S0 1073   S0-a 997   Sc 951   Sb 761   Sbc 727   E-S0 630
Sa 478   Sab 462   SBb 323   SABc 281   SBc 274   SBbc 270   SABb 257
SBa 186  SBab 161  SABa 123  Scd 105  I 105   Sd 76   Sm 68   SBm 64
IB 60    SBcd 59   S? 46    SBd 38   E? 28   SABd 18  SABm 16  IAB 11
```

A 30-entry dictionary, the same shape as `describe.TYPE_PHRASE`, turns "galaxy
in Draco" into "barred spiral galaxy in Draco" for 76.3% of the catalogue, for
39 KB. That is the cheapest real upgrade available anywhere in this document.

`MinAx` alongside `MajAx` gives an axis ratio. **1,267 galaxies have a/b >= 3**,
which is the "edge-on" an imager actually cares about (NGC 891, NGC 4565 and
1,265 others we currently describe identically to a face-on blob).

`Redshift` on 10,639 rows converts to a distance. Median 228 Mly at H0 = 70,
p10 41 Mly, p90 502 Mly. **376 rows are negative** (blueshifted, Local Group and
Virgo infall), so a naive z-to-distance would print a negative distance for
M31. The guard is: suppress the clause below roughly 10 Mpc and let the famous
nearby objects carry a hand-written distance instead.

### 1.3 `Identifiers` is a search fix, not a description fix

12,231 rows (91.5%), 870 KB. It is where `PGC 002557`, `UGC 00454` and
`2MASX J00424433+4116074` live. `_ALIASES` currently carries **109 aliases**,
all of them Messier cross-references. A user reading a printed chart that says
`UGC 1831` cannot find NGC 891 today.

It also contains the **Caldwell numbers**: 105 of our rows already carry a
`C nnn` identifier. The Caldwell list can be reconstructed from data already
vendored, without touching Sky Publishing's 1995 article at all.

This is a separate piece of work from descriptions and should not be bundled
with them, but 870 KB for 12,231 rows of extra search keys is a good trade and
it is the same licence.

### 1.4 `NED notes` is 2,287 real English sentences we already have on disk

2,287 rows (17.1%), 1,200 distinct strings, median 35 characters, 102 KB total.
It is uneven, and the uneven part is interesting:

```
254  In the Large Magellanic Cloud.
124  Nothing here; nominal position.
 74  Confused HIPASS source
 55  NGC identification is not certain.
 52  Nothing at this position.
 49  Multiple SDSS entries describe this object.
 46  Extended HIPASS source
 38  Galactic triple star.
```

Two clusters matter to a user and the rest do not:

- **205 rows say nothing is there**, and **160 say the identification is not
  certain**. That is exactly the class of honesty this repo already builds
  ("Pluto is not carried", the unmeasured-magnitude sentinel). An object whose
  catalogue entry is probably wrong should say so on the card before somebody
  spends an hour of clear sky on it.
- **316 rows place the object in a Magellanic Cloud**, which is real character.

The HIPASS/SDSS survey-plumbing notes are noise for this audience and should be
filtered out by an allow-list, not shipped wholesale.

### 1.5 A small existing defect: `.split(",")[0]` picks the first name alphabetically

`build_ngc_catalog.py` takes `(r.get("Common names") or "").split(",")[0]`.
Upstream stores multiple names for **26 objects**, in what looks like
alphabetical order rather than popularity order. Thirteen of the 26 are masked
because a curated row in `objects.py` wins on id collision. The rest ship:

- `NGC 2070` ships **"30 Dor Cluster"**, not "Tarantula Nebula".
- `NGC 6302` ships "Bug Nebula", not "Butterfly Nebula" (defensible either way).
- `NGC 4755` ships "Herschel's Jewel Box", not "kappa Crucis Cluster" (fine).

Also worth knowing even though the curated row hides it: upstream's primary
common name for `IC 434` is **"Flame Nebula"**, which is wrong. The Flame is
NGC 2024. Do not treat upstream common names as authoritative.

Fix: carry all names, show the first, make the rest searchable, and let a
curated override win where we disagree. 16 KB.

---

## 2. Dreyer 1888, the leading candidate, tested rather than confirmed

The belief under test was: *Dreyer is complete, free and historical by
construction.* Two of those three hold. It is not complete, and the complete
transcription is not free.

### 2.1 The complete one is not redistributable

**VizieR VII/118 (NGC 2000.0, Sinnott 1988)**, `ngc2000.dat`, 1,282,941 bytes,
13,226 records, covering NGC 1 to 7840 and IC 1 to 5386 with **zero blank
descriptions**. It is the file everybody uses. Its ReadMe says:

> "This catalog is copyrighted by Sky Publishing Corporation, which has kindly
> deposited the machine version in the data centers for permanent archiving and
> dissemination to astronomers for scientific research purposes only. The data
> should not be used for commercial purposes without the explicit permission of
> Sky Publishing Corporation."

"Scientific research purposes only" is narrower than non-commercial. Shipping
it inside a telescope controller is outside the grant whether or not anyone is
paid. **Do not vendor `ngc2000.dat`.**

There is a tempting argument that Dreyer's 1888 text is public domain and could
be extracted from the copyrighted compilation. That argument may well be right,
and it is not one this project should be the test case for, because a clean
alternative exists for NGC.

### 2.2 The free one is NGC only

**`NGCorig.txt`** from Harold Corwin's site, downloaded and parsed for this
document: **1,199,503 bytes, 7,840 rows, all NGC, zero IC, zero blank
descriptions.** Keyed from the NGC itself by Bob Erdmann between 1991 and 2005,
column-aligned by Corwin in 2017. `ICorig.txt` returns 404; **there is no IC
counterpart.**

Coverage against our catalogue: **7,572 of 13,369 rows (56.6%)**. What it
misses is 5,208 IC rows and 526 NGC ids that are OpenNGC component entries
Dreyer never had a row for (`NGC 1044 NED01`, `NGC 1023A`, and 524 more).

The licence notice on the index page reads:

> "NGC/IC Positions and Notes by Harold G. Corwin, Jr. is licensed under a
> Creative Commons Attribution-ShareAlike 3.0 Unported License. This means that
> you are welcomed to use the Positions and Notes in any way you like (and you
> may correct any mistakes!), but please do acknowledge their origin."

**Two things are not known and should be stated.** The grant names "Positions
and Notes"; `NGCorig.txt` is listed separately as "the original NGC/IC data
keyed in from the NGC itself by Bob Erdmann". Whether the CC notice was meant to
cover it is not stated on the page. The underlying text is unambiguously public
domain by age either way, so the exposure is over the transcription, not the
content. The same page also warns "this file has not yet been thoroughly
proof-read."

### 2.3 The decoding key exists and is tiny

About **90 abbreviations, 3.6 KB of text**. Dreyer published the list himself in
the NGC introduction, which puts the vocabulary in the public domain. A clean
mirror with no copyright claim is at `spider.seds.org/ngc/des.html`. VizieR's
VII/118 ReadMe carries a 96-entry version, but taking the key from the file we
are declining to use would be an odd choice when the PD original is available.

The notation is compositional, not a fixed vocabulary: `v`, `vv`, `e`, `ee`,
`c`, `p`, `g`, `s`, `l`, `m` stack, so `sbMvSN` is "suddenly brighter to the
middle to a very small nucleus". A longest-match decoder over the 90-token
vocabulary resolves about 81% of comma-blocks cleanly; the remainder is plain
English and numerics (`stellar`, `v diffic`, `mag 14`, `south from zeta Ori`)
that a greedy matcher shreds. A decoder needs a guard that stops tokenising at
`(`, at digits, and at dictionary words. There are 4,754 distinct comma-blocks
in total, so a curated top-500 lookup covers the tail cheaply.

### 2.4 The honest quality assessment

Measured over all 7,840 rows: **mean description 16.9 characters, median 16**.
5,808 distinct strings. The most common are:

```
94  vF, vS          (very faint, very small)
76  eF, vS
65  vF, S, R
55  vF
45  eF
```

**104 of 7,840 (1.3%) carry Dreyer's `!` remarkable marker.** The famous ones
are wonderful:

```
NGC 224   !!!eeB, eL, vmE (Andromeda)
NGC 6543  PN, vB, pS, sbMvSN
NGC 891   ! B, vL, vmE 22d
NGC 4565  B, eL, eE 135d, vsbMN = *10-11
NGC 6960  !! pB, cL, eiF, k Cygni inv
NGC 7000  F, eeL, dif nebulosity
```

and the median one is "very faint, very small", which tells an imager nothing
the magnitude and size columns do not already say. **Dreyer's value here is
historical flavour, not observing guidance.** Ship it as a distinct, labelled
line ("Dreyer, 1888: very faint, very small, round"), not as the sentence that
describes the object. Presented that way even `vF, vS` earns its place, because
the fact being conveyed is *this is what it looked like to the man who wrote the
catalogue*, and a bare `vF, vS` says that honestly.

Verdict on the leading candidate: **correct in spirit, third in priority.** It
is free, it is historical, it is 200 KB, and it reaches 56.6% rather than 100%.

---

## 3. SAC: widely redistributed, not licensed

SAC Deep Sky Database v8.1, ZIP 1,704,874 bytes, `SAC_DeepSky_Ver81_QCQ.TXT`
3,216,674 bytes, **10,342 rows**, 19 columns.

Coverage: 7,840 distinct NGC numbers (98.1% of our NGC ids) but only **471
distinct IC numbers (9.0% of our 5,206)**. Union with our catalogue is about
62%.

The `NGC DESCR` column is **the same Dreyer shorthand**, not modern English:

```
NGC  224  DESCR !!!eeB;eL;vmE          NOTES Local Group;Andromeda Galaxy;nearest spiral
NGC 6543  DESCR vB;pS;sbMvSN           NOTES H IV 37;Cat's Eye Nebula;PK96+29.1
NGC  891  DESCR B;vL;vmE22             NOTES H V 19;NGC 1023 group;Lord Rosse drawing shows dark lane
IC   434  DESCR eF;vvL;vmE;1 deg long incl Zeta Ori
```

Its own `Sacdoc.txt` says so: "Most of these are from the NGC... The
descriptions use the abbreviations from the original NGC and Burnham's."

The licence is the whole story. The only permission statement anywhere in the
archive or on the site is `Sacdoc.txt` line 6:

> "This compilation of data was begun in an effort to provide a comprehensive
> observing list for use at the eyepiece of a modest amateur telescope. This
> data is released for private use of anyone who wishes to use this database."

**"Private use" is not an open licence**, and a vendored pack inside a
redistributed application is not private use. The website carries only
"Copyright © 2026 Saguaro Astronomy Club" and no terms page. Downstream
redistributors do not help: Cartes du Ciel documents the catalogue with no
licence statement, and the KStars credits page does not mention SAC at all. The
only permission evidence found anywhere is a recollection on a Fedora mailing
list that Steve Coe "had no problem with KStars using the SAC data" and that it
was released "with the rules that it is not sold as a separate item". That is
hearsay about a personal permission to one project.

**Verdict: do not vendor.** It is widely redistributed and nobody has objected,
which is not the same thing as a grant. And even with a grant it would deliver
the same shorthand Corwin gives us for free, with 9% IC coverage instead of 0%.

---

## 4. Wikipedia and Wikidata: the number that decides it

### 4.1 Measured coverage

Every one of our 13,369 ids was resolved against the English Wikipedia API,
following redirects, with pages that redirect into a "List of NGC objects"
index classified separately, because a table row is not a lead extract.

| | Our rows | Real article | Redirects into a list | No page |
|---|---|---|---|---|
| NGC ids | 7,993 | **3,003 (37.6%)** | 469 | 4,521 |
| IC ids | 5,206 | **176 (3.4%)** | 28 | 5,002 |
| Messier ids | 109 | **109 (100%)** | 0 | 0 |
| **Total** | 13,369 | **3,288 (24.6%)** | 497 | 9,580 |

197 of the 3,288 are reached only through a redirect, meaning the article is
titled by a common name ("Andromeda Galaxy", "Cat's Eye Nebula"). Those are the
famous ones.

### 4.2 What the articles actually say

200 articles were sampled and their lead extracts measured. Median 403
characters, mean 523, p90 1,117. Vendoring all 3,288 leads costs **1.6 MB**;
first sentences only costs 273 KB.

**38% of the first sentences are exactly the sentence we already generate:**

```
NGC 4536 is an intermediate spiral galaxy in the constellation Virgo.
NGC 6881 is a planetary nebula, located in the constellation of Cygnus.
NGC 1386 is a spiral galaxy located in the constellation Eridanus.
NGC  550 is a spiral galaxy in the constellation Cetus.
```

Layer 0 plus the Hubble type from Layer 1 produces those four sentences for
39 KB and no network. This is the direct answer to the owner's question: for
the bulk of the objects Wikipedia covers, Wikipedia is a bot restating the
catalogue we already have.

Where it does add, it adds these, measured over the same sample:

```
mentions a discoverer        84%   <- Layer 2 gives this, CC0, for 58.9% of everything
mentions a distance          68%   <- Layer 1 gives this from redshift, for 79.6%
mentions a nickname          15%   <- genuinely unique
mentions a supernova          6%   <- genuinely unique
mentions a magnitude         12%   <- we already have it
```

Two of the top three are things cheaper layers supply for a much larger share
of the catalogue. What is left that is uniquely Wikipedia's is nicknames,
supernova history and narrative character for the few hundred famous objects,
which is precisely the "join where appropriate" the owner proposed.

### 4.3 Licence and the operational problem

English Wikipedia text is dual-licensed **CC BY-SA 4.0** and the unversioned
GFDL. Attribution requires one of: a hyperlink or URL to the page reused, a
hyperlink to a stable free copy, or a list of all authors. A vendored pack of
lead extracts therefore has to carry a per-object URL and put the whole pack
under BY-SA 4.0.

There is also an operational finding worth recording, because it was learned the
hard way while producing this document. **A single one-off sweep of 13,478
titles got this IP HTTP 429'd by Wikimedia after roughly 25 requests**, across
`en.wikipedia.org/w/api.php`, `www.wikidata.org/w/api.php` and
`api.wikimedia.org` simultaneously. The sweep only completed at 50 titles per
request with a 1.5 second pause between requests, roughly ten minutes for the
catalogue. An application shipped to end users, many of whom sit behind shared
IPs, cannot rely on read-time Wikipedia queries. That is an argument for the
link-out, which costs Wikimedia one page view initiated by the user's own
browser and satisfies the attribution requirement by construction.

### 4.4 Wikidata one-line descriptions: measured, and useless

Wikidata's `description` field is **CC0** with no attribution obligation, which
makes it the most attractive licence in this document. It was measured properly
before being recommended, and it fails on content.

8,642 plain NGC codes carry a Wikidata item; 8,454 have an English description;
those 8,454 use only **722 distinct strings**, and the distribution is:

```
5764  galaxy
 302  star cluster
  97  open cluster
  86  astronomical object
  86  interacting galaxy
  65  globular cluster
  58  spiral galaxy in the constellation Cetus
```

Median length 9 characters. It reaches 7,837 of our rows and says **less** than
`describe()` already prints. **Do not use it.**

### 4.5 Wikidata discovery provenance: measured, and excellent

The same CC0 licence, different property, completely different result.
`P61` (discoverer) and `P575` (time of discovery), pulled from the Wikidata
Query Service and joined to our ids:

```
our rows with a discoverer            7,874  (58.9%)
our rows with discoverer AND a date   7,621  (57.0%)
   of those, NGC-based                7,539
   of those, IC-based                   335
vendored as id/name/date               335 KB
```

Top discoverers: William Herschel 2,493, John Herschel 1,796, Albert Marth 579,
Lewis Swift 508, Édouard Stephan 451, Heinrich d'Arrest 322, Francis
Leavenworth 250, James Dunlop 224.

```
NGC 6543   William Herschel      1786-02-15
NGC  891   William Herschel      1784-10-06
NGC 4565   William Herschel      1785-04-06
IC   434   Williamina Fleming    1888-01-01
NGC 7840   Albert Marth          1864-11-29
```

"Discovered by William Herschel on 6 October 1784" is exactly the character and
history the brief asked for, it is CC0, and it costs 335 KB. Two gaps to state
plainly: **IC provenance is only about 11% populated on Wikidata**, and the
**Messier ids do not join** (M31 returns nothing through the NGC-code route,
because its item is modelled differently), so the 109 most famous objects need
their discovery facts written by hand or joined by QID instead of by code.

---

## 5. SIMBAD, VizieR, NED, HyperLEDA: query-time only, and not even that without care

None of these should become a vendored pack. The reasons differ.

**CDS** (`cds.unistra.fr/legals/`) is disjunctive and unversioned. It never says
"SIMBAD is CC BY 4.0":

> "Datasets containing only Public Information are distributed under an Open
> Licence or ODbL [...] or CC-BY."
> "The Data Sets must be cited in any work or product that uses them by
> including - if available - the DOI [...] and at least: 'CDS/SIMBAD',
> 'CDS/VizieR', 'CDS/Aladin'."

Note "any work **or product**". The citation duty reaches shipped software.

**VizieR** (`cds.unistra.fr/vizier-org/licences_vizier.html`):

> "The data retrieved with VizieR are free of usage in a scientific context"
> "The commercial usage of the data is subject to rules depending of the origin"
> "Please refer to the ReadMe file associated to the catalogue to verify if a
> copyright exists."

Silent on redistribution. The operative permission is per-catalogue, which is
how VII/118 turns out to be Sky Publishing's.

**SIMBAD rate limits are published and low**: 10 queries per second per IP
triggers a one-minute ban, more than 400 queries per 10 seconds triggers a
one-hour ban, and the FAQ warns that users behind a shared IP get banned by
other people's traffic. Nothing in the CDS documentation addresses scripted
access from an application distributed to end users. It is neither blessed nor
forbidden.

**NED** has no terms-of-use or data-rights page at all. `Documents/Overview`,
`Documents/Guides/Introduction`, `Documents/Guides/BestPractices` and `/node/7`
carry only "Copyright © 2026, California Institute of Technology" and an
acknowledgement sentence. Its sibling service IRSA does publish terms, which
makes the silence conspicuous rather than reassuring.

**HyperLEDA**: "All the data and the software is publicly available in
open-source for non-commercial purposes." No bulk download, SQL web form only,
and no free-text field anywhere in its ~60 columns. Not usable and would not
help if it were.

The required acknowledgement sentences, verbatim, for whichever of these end up
in a credits screen:

- "This research has made use of the SIMBAD database, CDS, Strasbourg
  Astronomical Observatory, France"
- "This research has made use of the VizieR catalogue access tool, CDS,
  Strasbourg Astronomical Observatory, France"
- "This research has made use of the NASA/IPAC Extragalactic Database, which is
  funded by the National Aeronautics and Space Administration and operated by
  the California Institute of Technology."

One thing this document cannot settle: OpenNGC itself was built by merging NED,
SIMBAD, HyperLEDA and HEASARC data and then asserting CC BY-SA 4.0 over the
result. That is one author's unilateral assertion, not a grant from any of those
institutions. No public statement from CDS, NED, HyperLEDA or HEASARC blessing
or objecting was found. What is established is that the practice is widespread
and long unchallenged: OpenNGC is packaged in Fedora and Debian and mirrored by
GAVO. **We are relying on that, and it is worth the owner knowing we are.** A
one-paragraph email to `cds-question@unistra.fr` and to the NED help desk would
convert the biggest open question in this document into a documented answer.

---

## 6. Sources worth knowing about that were not asked for

**Steve Gottlieb's NGC/IC observing notes** (`adventuresindeepspace.com`,
`ngc_notes.txt` 8,647,426 bytes, `ic_notes.txt` 2,074,858 bytes). **7,891 NGC
and 2,519 IC designations** of real modern-English visual description:
"moderately bright, oval 4:3 WNW-ESE, strong sharp concentration with a very
bright core". This is, by a distance, the best content fit for what the brief
describes, and it covers IC where everything else does not. **The page carries
no copyright or permission statement of any kind**, which means all rights
reserved. It is not usable as things stand and it is the single highest-value
email anybody could send about this project.

**Wolfgang Steinicke's Historic NGC/IC** (`Historic_NGCIC.zip`, 1,431,482 bytes,
13,226 entries). Carries discoverer, discovery date, telescope aperture,
Dreyer's description, Herschel class and popular names in one file, for NGC and
IC both. It is the dataset this whole document is trying to reassemble from
parts. Its terms:

> "Important note: Any non-commercial use of my data is free! If a commercial
> use is planned, please contact me! Please inform the author Dr. Wolfgang
> Steinicke (also in case of non-commercial use, e.g. in a free software). In
> any case, a proper acknowledgment of the source, including an answer-back to
> the author is necessary. These data are part of a scientific project and
> subject to copyright!"

Non-commercial plus notify-the-author is incompatible with CC BY-SA and with
every OSI licence, and he names free software explicitly. **Not vendorable as
licensed.** He has granted use to Guide, TheSky and Starry Night, so he is
reachable and has said yes before. Second-highest-value email.

**The NGC/IC Project is gone.** `ngcicproject.org` and
`ngcicproject.observers.org` both fail DNS resolution. Its "Historically
Corrected NGC" survives only in the Wayback Machine and never carried a licence.
Anything that plans to depend on it should not.

**OpenNGC ships nebula outlines**: `outlines/outlines_stellarium.dat`, 754,968
bytes of vector outlines for prominent nebulae, under the same CC BY-SA 4.0 we
already accept. Not a description, but the Atlas could draw the real shape of
the North America Nebula instead of a circle, for 0.75 MB.

**Corwin's `NGCorig.txt` also carries provenance columns** we would otherwise
buy from Wikidata: William Herschel class and number on 2,479 rows (31.6%),
John Herschel number on 3,935 (50.2%), General Catalogue number on 6,083
(77.6%), and an "other observers" column on 3,808 (48.6%) with values like
`d'A` (536), `Ld R` (239), `Sw VI` (123), `Bigourdan` (93). It needs its own
abbreviation key and is less complete than Wikidata's P61, so it is a fallback
for the IC-shaped hole, not a primary.

**Messier's own 1781 descriptions** are public domain in the French original
(*Connoissance des Temps*; Messier died 1817), and scans are on archive.org. The
widely-copied English at `messier.seds.org/xtra/history/m-cat81.html` (60,826
bytes) is Frommert and Kronberg's modern translation carrying fresh copyright,
and the site states no terms. 110 objects is small, but they are the 110
everybody points at. Vendor the French and translate it, or write our own.

**Stars.** `brightstars.py` already derives from the IAU Catalog of Star Names
(CC BY), which is the right choice and should stay. Two additions are possible:
IAU-CSN itself (`IAU-CSN.txt`, 71,800 bytes, 452 names, CC BY) carries no
etymology, and the WGSN file says only that it "is working to add brief
summaries of etymological information to future editions", so **there is no
structured star-name-etymology dataset in existence**. HYG v4.4
(`codeberg.org/astronexus/hyg`, 32.4 MB CSV, 13.0 MB gzipped, 119,614 rows,
**CC BY-SA 4.0**, the same licence we already carry) would give spectral type
and distance for all 241 named stars, which turns "β Cas · Cassiopeia" into
"β Cas · Cassiopeia · F2 subgiant, 55 light years". Vendoring the whole 32 MB
for 241 rows is absurd; extracting those 241 rows is about 15 KB.

**Solar system.** Nothing needs to change. `solar_system.describe()` already
does the right thing: every clause is computed for tonight and is something the
user cannot see (phase, apparent size, whether the mount will refuse the slew).
A vendored paragraph about Jupiter would be worse than what is there, because
it would be the same paragraph every night. NASA prose is public domain via
17 U.S.C. §105, not via NASA's media policy page, which enumerates images,
audio and video and never mentions text. The NSSDC planetary fact sheets are
currently 307-redirecting to a maintenance page. If Moon feature names are ever
wanted, the USGS/IAU Gazetteer carries a prose "Origin" field per feature and is
US public domain, but it has no bulk export and the Moon alone would be about
9,000 page fetches.

---

## 7. Attribution: what has to appear, and where

This repo is **Apache-2.0**. Today its only attribution for vendored data is a
docstring in `objects.py`, a docstring in `brightstars.py` and a `#` comment on
line 1 of `ngc.tsv`. **There is no user-facing credits surface anywhere in
`ui/src`.** For CC BY-SA data that is thin: the licence requires attribution
"in any reasonable manner", and a comment inside a file the user never opens is
not obviously that.

The pattern to copy already exists: `weatherSourceLabel` in
`ui/src/lib/weather.ts` names Astrospheric **only when astrospheric samples are
actually in the payload**, so the label never claims a source that is not
flowing. Descriptions should work the same way. A card that shows a Dreyer line
names Dreyer; a card showing only the composed sentence names nothing extra.

Per layer, concretely:

| Layer | Licence | Where the notice goes | What it must say |
|---|---|---|---|
| 1. OpenNGC columns | CC BY-SA 4.0 | data file header, plus one line in a Credits screen | "Deep-sky data from OpenNGC by Mattia Verga, CC BY-SA 4.0", linked, plus a copy of `LICENSES/CC-BY-SA-4.0.txt` in the repo |
| 2. Wikidata provenance | **CC0** | nothing required | courtesy credit only. This is the only layer with no obligation |
| 3. Dreyer via Corwin | CC BY-SA 3.0 over PD text | data file header, Credits screen, and on the card next to the line | "Dreyer, *New General Catalogue*, 1888; transcribed by R. E. Erdmann Jr., column-aligned by H. G. Corwin Jr., CC BY-SA 3.0" |
| 4. Wikipedia | CC BY-SA 4.0 | the hyperlink itself | a link to the specific article satisfies the licence. This is the main reason to link rather than vendor |
| stars (existing) | CC BY | Credits screen | "Star names from the IAU Catalog of Star Names (IAU WGSN)" |
| HYG, if taken | CC BY-SA 4.0 | data file header + Credits | "HYG database, astronexus, CC BY-SA 4.0" |

### Share-alike, plainly

CC BY-SA's copyleft attaches to the **data and to adaptations of it**, not to
unrelated source code that happens to sit in the same repository. Concretely:

- `ngc.tsv` is already CC BY-SA 4.0 and the Apache-2.0 code around it is
  unaffected. That is a mere aggregation and it is standard practice.
- A description pack **derived** from CC BY-SA sources is adapted material and
  must itself be CC BY-SA 4.0. Keep it as its own file with its own header.
- The **sentence the API returns** for an object, if it is composed from CC
  BY-SA inputs, is adapted material. In practice this means the API and UI must
  attribute, which they should anyway, and it means the strings cannot be
  relicensed as Apache-2.0. It does **not** mean the code becomes CC BY-SA.
- Corwin is CC BY-SA **3.0**. BY-SA 3.0 permits distributing adaptations under
  "a later version of the same License", so a mixed pack can be labelled CC
  BY-SA 4.0. Say which 3.0 material is inside it.

**Nothing in the recommended stack forces a licence change on this repo**, and
Layer 2 (CC0) is free of obligations entirely. Everything that *would* have
forced something has been excluded for that reason: SAC ("private use"),
Steinicke (non-commercial plus notify), HyperLEDA (non-commercial), NGC 2000.0
("research purposes only"), Gottlieb (no grant). If AstroDeck ever ships a paid
edition, none of the recommended layers becomes a problem; every excluded one
would have.

---

## 8. The honest fallback

After Layers 1, 2 and 3, **718 rows (5.4%) still have nothing but the composed
sentence.** By type:

```
372  * (an NGC/IC-numbered single star)
122  ** (double star)
 80  G (galaxy)
 66  GPair
 24  Neb
 34  everything else
```

630 of those 718 also have no published magnitude. Only 16 of them have a
Wikipedia article. They are overwhelmingly IC numbers pointing at stars.

What such an object gets today, from `describe.py`, is:

```
Star in Andromeda
```

and that is the right answer, for the reason `describe.py`'s own docstring
already gives: every clause is a fact the bare id does not carry, and the
sentence drops clauses rather than blanking them. "Star in Andromeda" is true,
it is the two facts we have, and a beginner reading it learns what IC 1 is and
where to look. A placeholder would be worse in three separate ways: "Unknown
object" is false (we know the type), "No description available" is about our
data rather than about the sky, and an empty string forces the browser to invent
copy, which is exactly the failure mode `objects.py` documents at length for
"Planets aren't supported yet".

The one improvement worth making for this population is not more text, it is
the NED note where one exists: **205 rows across the whole catalogue carry
NED's "Nothing here; nominal position" or "Nothing at this position", and 160
carry "NGC identification is not certain."** Surfacing those turns a shrug into
a warning, and it costs nothing because the sentences are already on disk.

---

## 9. What to do, in order

1. **Re-import 410 KB of OpenNGC columns** into `ngc.tsv` and teach
   `describe.py` to use them: Hubble type as a phrase, axis ratio for edge-on,
   redshift as a distance with the blueshift guard, the full common-name list,
   and a filtered `NED notes`. Biggest gain, no new licence, one build script.
2. **Add the 7 boundary constellations to the constellation test** as known
   disagreements with upstream, so a future pull that moves one is visible.
3. **Vendor Wikidata P61 and P575** as a 335 KB CC0 sidecar. Fill the Messier
   gap by QID rather than by catalogue code.
4. **Vendor Dreyer for NGC** from `NGCorig.txt` as a 200 KB sidecar plus the
   4 KB abbreviation key, shown as a separate labelled line, never as the
   object's description.
5. **Link out to Wikipedia** for the 3,288 rows that have an article. A flag
   plus a title is about 40 KB. Do not vendor extracts.
6. **Build a Credits screen** and put every notice from §7 in it. This is
   overdue independently of any of the above.
7. **Send two emails**: Steve Gottlieb, for the only corpus that actually
   matches the brief and covers IC; Wolfgang Steinicke, for the only file that
   has everything in one place. Both have said yes to other projects.
8. **Optionally, ask CDS and NED** whether the OpenNGC-style derivation we are
   relying on is acceptable to them. We are currently relying on silence.

## What is not known

- Whether Corwin's CC BY-SA 3.0 notice was intended to cover `NGCorig.txt`,
  which the page attributes to Bob Erdmann's keying rather than to Corwin's own
  Positions and Notes. The underlying Dreyer text is PD regardless.
- Whether CDS or NED consider OpenNGC's relicensing of their merged data
  acceptable. No statement either way was found.
- Whether VizieR VII/1B (Revised NGC, Warren/ADC 1982) is redistributable. Its
  ReadMe carries no copyright statement at all, and CDS's per-catalogue model
  means silence is not a grant. It is NGC-only and truncated at 40 characters,
  so it is not worth resolving.
- The exact IC coverage of Wikidata's P61 beyond the 335 rows that join to our
  catalogue. The gap is real; its cause was not investigated.
- Whether Wikimedia's rate limiting would be materially different for an
  application sending a proper contact User-Agent at low volume. The 429s
  observed here came from a 13,478-title sweep, which is not the shape of
  normal use.
