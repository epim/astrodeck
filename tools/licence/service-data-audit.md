# Service and data licence audit, 2026-10-01

Scope: source baseline `bf3eadddcac9fb74bd802309b57ab6536c15ee36` in `codex/licence-audit`, plus the local tarball identified below. This is an evidence review, not a legal clearance. Licensor quotations are distinguished from the reviewer's reading. Current primary pages were checked on 2026-10-01. No live rig, relay, private configuration or service API key was used.

## Artifact evidence and limits

The inspected artifact is `.probe/licence/tarball/astrodeck-licence-audit.tar.gz`, SHA-256 `28a20a17eff0469bb31019bd019b1fdab14b967455fd6b92e9f71bc4c0cf4275`. Root's durable `tools/licence/tarball-inventory.json` records 478 files. It is a local, non-strict build with a freshly built UI and ASTAP omitted. This review independently opened the tar and checked its relevant member names. It does not certify published platform binaries, a Docker image, an installed Orange Pi environment, or a future native wheel.

Present: all four catalogue TSVs, `catalog/brightstars.py`, the curated targets in `catalog/objects.py`, a generated credits JavaScript chunk, `ui/dist/bg_nebula.png`, and the two PWA icons. Absent: survey packs, NOAA granules, downloaded orbital-element caches, horizon datasets, image test fixtures, and installed Astropy/IERS/sgp4 dependency payloads. The tar carries source and dependencies are installed separately. Do not mistake this absence for proof about a PyInstaller executable.

Baseline packaging risks were reported to root immediately. `packaging/astrodeck.spec:44` copies the entire vendor directory; `:49` also copies any existing `catalog/_bundled_pack`. Those are not the tarball's filtered paths. `scripts/build_release.py:79` excludes Player One libraries, and `:176` rejects survey packs through the licensing policy. Source-level refusals do not prove what the four binary artifacts contain. Packaging changes are outside this worker's scope.

## Findings that need action

| ID | Priority | Finding | Required next step |
|---|---|---|---|
| SD-01 | High, permission unresolved | A public Astrospheric client is still outside the clearly stated personal-project API scope. | Obtain vendor clarification covering public OSS distribution and independent installs; retain the owner-needed label. A user's acknowledgement is not permission from Astrospheric. |
| SD-02 | Medium, service terms | Open-Meteo links exist on Conditions panels but not beside newer wind and forecast-cloud displays. Free endpoint use also has non-commercial and rate conditions. | Route placement fixes to the UI owner. Record service access terms separately from the data's CC BY 4.0 licence. |
| SD-03 | Medium, service operation | CelesTrak errors leave the cache due, causing minute-by-minute retries despite the current stop-on-error policy. | Route a stop/backoff/human-review behavior change to Claude; correct the registry's unconditional cadence assurance. |
| SD-04 | Medium, omitted data licence | Current CDS DSS2 and 2MASS records declare an ODbL-1.0 database layer, which the registry omits. | Add the database terms and notices while retaining separate original-image rights. Do not infer permission to bundle DSS2. |
| SD-05 | High, shipped provenance unresolved | The tarball includes a nebula background with no recoverable source or rights record. | Owner must establish creation/source/licence evidence, or authorize replacement/removal. Appearance is not provenance. |
| SD-06 | Medium, inaccurate current record | NOAA credit notes claim nothing is owed and describe a nonexistent cloud-map screen; the screen now exists and current NOAA terms are more specific. | Update credits and distinguish AstroDeck's derived cloud model from original NOAA data. |
| SD-07 | Low/owner-needed precision | IAU star-name data grant CC Attribution without naming a version; MPC says freely available rather than dedicating data to the public domain. | Preserve the exact scope of these statements. Do not turn either into a stronger grant. |
| SD-08 | Artifact verification dependency | Astropy/IERS and sgp4 package data are absent from the source tarball but relevant to installed and frozen products. | Verify files and notices against each actual dependency wheel/binary artifact. This review does not certify unbuilt products. |

## Network services and runtime data

### SD-01: Astrospheric

**Source and shipped form.** `server/astrodeck/weather.py:44` declares the v1 endpoint. `:362` sends the user's key; `:50` uses a six-hour nominal interval. `:454` maintains an in-memory cache and `:604` checks the restricted-asset acknowledgement before fetching. The tarball ships this client code, not a shared key or forecast database. `server/astrodeck/licensing.py:152` and `tools/credits_registry.py:505` already surface the permission question.

**Licensor words.** The [v1 API documentation](https://www.astrospheric.com/DynamicContent/api_info) describes availability to “Astrospheric Professional members for use in personal projects.” It separately directs public or commercial projects to contact the vendor. The [current v2 API documentation](https://www.astrospheric.com/DynamicContent/api_info_v2?src=helppage) also retains personal-project scope and offers contact for broader uses. The [service terms](https://www.astrospheric.com/privacypolicy) prohibit unauthorized use and redistribution outside the service and license a subscription to one individual.

**Reading.** Per-user keys, limited requests, and local display reduce operational exposure. They do not establish that shipping and maintaining a public, many-install integration has the vendor's approval. Conversely, the docs do not establish that distributing AstroDeck's independently written client source is itself an infringement. This remains an owner permission question, not a categorical finding of illegality. A checkbox cannot resolve it.

**Obligations and gap.** Keep the vendor credited when its samples are shown and preserve the upstream ECCC notice. The [ECCC end-use licence](https://eccc-msc.github.io/open-data/licence/readme_en/) permits reuse with source acknowledgement and, where possible, a licence link. The existing fallback acknowledgement in `credits_registry.py:530` matches the published fallback; adding a direct current terms link would improve traceability. ECCC permission does not replace Astrospheric's service terms. No authorized public-product agreement was found in the repo.

### SD-02: Open-Meteo

**Source and form.** `weather.py:43` uses the free `api.open-meteo.com` endpoint. `:49` specifies a 15-minute cadence, approximately 96 successful normal calls per installation per day. `:156` allows one retry per attempt. Forecasts are in memory, not bundled data. The CC BY material reaches users through derived charts, current wind readouts, and cloud bands.

**Licensor words.** The [data licence page](https://open-meteo.com/en/licence) says, “You must include a link next to any location Open-Meteo data are displayed.” It identifies API data as CC BY 4.0, requiring credit, a licence link and indication of changes. The [service terms](https://open-meteo.com/en/terms) restrict the free API to non-commercial use and list limits of 600 calls/minute, 5,000/hour, 10,000/day and a monthly plan limit. Commercial use requires a paid plan.

**Reading and observed compliance.** Public OSS client distribution is not itself prohibited in the checked terms. Each deployment's actual use and aggregate traffic still matter; no many-install or commercial exception can be inferred from CC BY. AstroDeck does not ship Open-Meteo server code. Its separate AGPL server-code licence is not evidence of AGPL code in this client.

The required nearby link is implemented in `ui/src/components/weather/SkyConditionsPanel.tsx:356` and `ui/src/next/hubs/weather/conditions/ConditionsScreen.tsx:261`. It is missing from other displays: `SkyHub.tsx:657` feeds current wind into `cards/DomeCard.tsx:146`; `weather/dome/DomeScreen.tsx:180` and `:336` render the same wind; `weather/dome/domeOverlay.tsx:297` displays its label. `sky/sheets/quick.tsx:331` derives forecast cloud bands used at `:631`. Those files contain no Open-Meteo link. A Settings credit does not satisfy the provider's explicit placement request.

**Fix.** Add nearby source links to these surfaces, describe derived values in credits, and document the free-service scope. No behavior or UI was changed by this audit.

### IEM radar and satellite map tiles

**Source and form.** The application proxies IEM map tiles and keeps an operator-local cache. `ui/src/next/hubs/weather/radar/RadarScreen.tsx:18` deliberately retains the reused map's IEM attribution. `credits_registry.py:581` identifies Iowa State's service. No IEM tile is in the inspected tarball.

**Licensor words and reading.** The [IEM disclaimer](https://mesonet.agron.iastate.edu/disclaimer.php) says materials “are in the public domain and may be used freely by anyone for any lawful purpose.” Attribution is appreciated. This is a more direct basis than merely inferring public domain from NOAA ancestry. Record IEM's statement and keep the credit. No redistribution blocker was identified for the checked service/data scope.

### SD-06: NOAA GOES and AstroDeck's cloud probability

**Source and form.** `server/astrodeck/cloudmap/source.py:87` fetches ABI-L2-ACMC and ABI-L2-ACHAC; `:105` constructs NOAA GOES bucket URLs and `:412` identifies the runtime cache. `granule.py:389` applies file metadata; `occlusion.py:553` samples mask probability while using AstroDeck's own geometry, height and motion processing. These results are derived app outputs, not a bundled weather dataset. No granules occur in the inspected tar.

**Licensor words.** The NOAA-managed [AWS data registry](https://registry.opendata.aws/noaa-goes/) states, “NOAA data disseminated through NODD are open to the public and can be used as desired.” It requests attribution for unaltered data, prohibits implying NOAA endorsement or affiliation, and prohibits presenting modified data as original, unaltered NOAA data.

**Gap and fix.** `credits_registry.py:465` and `:472` contain stale assertions about unrestricted wording and no cloud-map panel. Keep NOAA attribution, name the upstream ABI products, state that cloud probability/geometry displays are processed by AstroDeck, and avoid implying NOAA validates AstroDeck's decisions. This does not require relicensing AstroDeck code as NOAA data. No separately downloaded proprietary model weights were found in the cloudmap implementation or tarball.

### SD-03: CelesTrak GP elements

**Source and form.** `catalog/ephemeris/elements.py:70` fetches the visual GP group as JSON and `:71` fetches selected catalogue IDs. `:75` sets a 12-hour refresh; `:97` sends the AstroDeck user agent. Orbital elements are cached on the operator's disk. The tar contains implementation and identifiers, not an active orbital-element cache. `satellites.py:24` separately references the sgp4 verification corpus in the dependency.

**Licensor words.** The current [usage policy](https://celestrak.org/usage-policy.php) says machine clients should “immediately stop querying” on a non-200 response and report it for human investigation. It currently describes GP updates at two-hour intervals. [GP format documentation](https://celestrak.org/NORAD/documentation/gp-data-formats.php) supports JSON and warns about six-digit catalogue IDs.

**Gap.** The normal refresh is conservative, but not the failure path. `elements.py:392` bases due status on the last successful cache time. `:599` polls due sources, and `:679` records failure without moving that time or stopping future calls. Therefore failed or empty caches remain due every 60 seconds. `:509` and `:527` also allow individual fetches after group failure. Manual refresh is only protected against concurrency at `:612`, not repeated completed requests. The registry's claim that a 12-hour interval is always honored is incorrect.

**Reading and fix.** Service conditions apply even when underlying observations originate with government sources. Avoid an unqualified claim that a public-domain designation settles all access/use rights. Route stop-on-error and bounded manual refresh to the app owner; disclose the current gap. This audit did not call the live element APIs.

### MPC comet orbital elements

**Source/form.** `elements.py:72` declares CometEls.txt; `:77` uses a nominal weekly cadence; `:545` parses the response. There is no comet cache in the tarball.

**Licensor words.** The current [MPC FAQ](https://docs.minorplanetcenter.net/mpc-ops-docs/faqs/) says, “Data from the MPC's database is made freely available to the public.” It names its funders and separately cautions against excessive requests.

**Reading/gap.** This supports the intended runtime access. It is not an explicit CC0 grant or public-domain dedication. `credits_registry.py:563` overstates the evidence by labeling it public domain. Keep MPC/funder acknowledgement and the no-bundled-cache distinction; get clearer terms before proposing bulk redistributed data. The nominal cadence does not describe failure/manual-refresh behavior, as above. The legacy MPC pages attempted during this review returned access errors; the current primary FAQ was accessible.


## Survey and catalogue data

### SD-04: CDS HiPS, DSS2 and 2MASS are layered rights

**Source and actual artifact.** `catalog/survey_pack.py:49` registers DSS2 color, DSS2 red and 2MASS color. `:53` prefers the ESA mirror for color DSS2; `:68` does so for 2MASS. `catalog/tiles.py:99` serves/caches tiles and `catalog/survey.py:48` uses the CDS cutout service. The tarball has no survey tiles or bundled pack. The existing runtime seed refusal is at `survey_pack.py:188`; the tar build rejects staging at `scripts/build_release.py:176`. The stale release-workflow fetch is still at `.github/workflows/release.yml:83`, with `--survey-pack` at `:107`; Claude has filed #631. Do not represent that workflow text as evidence that the final tar actually includes tiles.

**Licensor evidence.** The current primary [DSS2 color record](https://alasky.cds.unistra.fr/MocServer/query?ID=CDS/P/DSS2/color&fmt=html&get=record) and [2MASS color record](https://alasky.cds.unistra.fr/MocServer/query?ID=CDS/P/2MASS/color&fmt=html&get=record) both state `hips_license = ODbL-1.0`, `hips_copyright = CNRS/Unistra`, and `hips_status = public master clonableOnce`. These are rights in the CDS HiPS database, not a blanket grant over each original image. Both records identify the original observation sources separately. The DSS record links STScI's copyright page and identifies the ESA copy as an unclonable mirror.

The [ODbL legal code](https://opendatacommons.org/licenses/odbl/1-0/) expressly says it “does not cover the copyright over the Contents independent of this Database.” My reading: public database distribution requires licence/URI and preserved notices; public use of a produced work requires a database/licence notice; public derivative databases can trigger share-alike and machine-readable access duties. Private internal use has different treatment. No requirement to license the entire AstroDeck application under ODbL was identified. Exact applicability to an operator's cache, a published cutout, or a shared instance depends on the use. Keep these distinctions instead of declaring all fetching clear.

For the original DSS images, [STScI's current data-use policy](https://archive.stsci.edu/publishing/data-use) says, “Commercial, for-profit use of the copyrighted collections is prohibited without written permission from the copyright holder(s).” It permits specified nonprofit uses and identifies DSS as copyrighted. The [DSS copyright page](https://archive.stsci.edu/dss/copyright.html) identifies different plate rights holders. The [CDS HiPS manual](https://aladin.cds.unistra.fr/hips/HipsgenManual.pdf) explains the one-generation cloning status. These sources do not establish unrestricted public release redistribution. ODbL on the database does not cure this original-image question.

For 2MASS, the [mission acknowledgement page](https://irsa.ipac.caltech.edu/data/2MASS/docs/releases/allsky/doc/sec1_8b.html) asks, “Please include the following standard acknowledgment in any published material that makes use of 2MASS data products.” The existing registry contains that acknowledgement. The current CDS record adds the database layer missing from the old “nothing owed” verdict.

**Gap and remediation.** Revise the DSS entry's obsolete “bundled offline survey pack” description. Add CDS ODbL notices and terms; keep DSS original-image permission unresolved for redistribution. Replace the registry's claim that ESA's general CC BY-SA terms govern these mirrored third-party surveys. A mirror host does not replace the survey's own notices. Preserve the original DSS/2MASS acknowledgements and make applicable credit available with public outputs. Do not enable bundling from this report. The general CDS website's terms pages were blocked/inaccessible in this review; the survey-specific primary records and manual were accessible and are the evidence relied on.

### OpenNGC extracts

**Source and artifact.** `server/astrodeck/catalog/data/ngc.tsv:1` and `ngc_extras.tsv:1` declare OpenNGC and CC-BY-SA-4.0. Both files are in the tar. `server/tools/build_ngc_catalog.py:8` and `catalog/build_ngc_extras.py:51` identify the input and transformation. `credits_registry.py:174` and `:234` state author, changes, share-alike, licence link and upstream credits.

**Licensor words.** The upstream [OpenNGC README](https://raw.githubusercontent.com/mattiaverga/OpenNGC/master/README.md) says “OpenNGC is released under CC-BY-SA-4.0 license.” It also supplies pass-through acknowledgements for the constituent sources.

**Reading/compliance.** Redistribution of these extracts is supported by that grant subject to attribution, licence/notice retention, modification identification and share-alike for adapted data. The generated credits carry the full legal text and the descriptions of changed columns/normalization. Do not label the extracts as Apache just because the application is Apache. The unchanged gap from August is reproducibility: no exact original input commit/checksum is recorded. That does not itself prove a licence violation. A plain data notice beside standalone TSVs would make the obligations easier to retain outside the UI; the actual tar currently conveys the full explanation in its compiled credits chunk.

### Wikidata discoverers

**Source and artifact.** `catalog/data/discoverers.tsv:1` and `catalog/build_discoverers.py:264` identify Wikidata structured properties. The file is in the tar. The build script's query endpoint is at `:92`; the product reads the resulting data locally.

**Licensor words.** [Wikidata's licensing policy](https://www.wikidata.org/wiki/Wikidata:Licensing) says structured data “is released into the public domain under Creative Commons Zero.” Its non-structured text uses different terms.

**Reading/compliance.** The P61/P575/P528 extraction fits the structured-data scope. CC0 permits this use without a mandatory attribution condition; the included Wikidata credit is useful provenance. Do not extend the statement to copied Wikipedia prose or arbitrary Wikidata site pages. No such prose was found in this extraction.

### SD-07: named stars and constellation output

**IAU named stars.** `catalog/brightstars.py:10` identifies the 2022-04-04 IAU-CSN source and the magnitude-limited 241-row extract. It is shipped as Python source, so a detector that scans only non-code data misses it. The [WGSN secretary's source file](https://www.pas.rochester.edu/~emamajek/WGSN/IAU-CSN.txt) identifies the same edition and states that IAU products are “released under Creative Commons Attribution.” It names no version. `credits_registry.py:325` correctly discloses that the 4.0 version is an inference, but the machine-readable `CC-BY-4.0` label remains more definite than the source. The IAU copyright URL attempted during this audit returned 404. Preserve the source attribution, link, date and changes; seek version clarification or use an explicitly uncertain local licence identifier rather than claiming a verified 4.0 grant.

**Constellations.** `catalog/build_constellations.py:65` uses Astropy's `get_constellation` over the app catalogue; `data/constellations.tsv:1` identifies generated output, which is included in the tar. This is an object-to-constellation assignment list, not a copied polygon dataset. The actual boundary table used by Astropy identifies [Roman et al. 1987](https://raw.githubusercontent.com/astropy/astropy/main/astropy/coordinates/data/constellation_data_roman87.dat); “Delporte 1930” alone omits this implementation input. Astropy's [package licence](https://raw.githubusercontent.com/astropy/astropy/main/LICENSE.rst) allows source/binary redistribution with notice and disclaimer retention. Record Astropy/Roman as the calculation's provenance. The registry's Apache label describes AstroDeck's generated output policy, not an Apache grant by the IAU. If a future artifact includes the boundary table, inventory it through the actual Astropy distribution, including its notice.

**Curated targets.** `catalog/objects.py:45` contains the hand-curated core, and `:128` distinguishes it from OpenNGC. It ships as source. No external copied table or precise upstream snapshot for those hand-entered facts is recorded. Treat this as first-party curation according to the repository record, not a proven independent astronomical data source. Maintain the distinction from the CC BY-SA extracts.

### SD-08: IERS, Astropy and sgp4 data inside dependencies

The source tar includes `server/pyproject.toml:15`'s Astropy dependency but not the installed distribution. Orange Pi's constraints pin Astropy/IERS at `packaging/appliance/constraints-linux-aarch64-cp313.txt:38`. `packaging/astrodeck.spec:29` explicitly collects Astropy data, so frozen-product evidence is required.

The [astropy-iers-data licence](https://raw.githubusercontent.com/astropy/astropy-iers-data/main/LICENSE.rst) grants source/binary redistribution subject to BSD notice/disclaimer and non-endorsement conditions; its [README](https://raw.githubusercontent.com/astropy/astropy-iers-data/main/README.rst) identifies the BSD-3-Clause package. Its [file/source declarations](https://raw.githubusercontent.com/astropy/astropy-iers-data/main/astropy_iers_data/__init__.py) identify finals2000A, EOP C04 and leap-second data from IERS/USNO/Observatoire de Paris/IANA. These are concrete dataset dependencies, not “no external data.” Direct IERS policy pages attempted here were inaccessible. The package grant is evidence for that maintained distribution, not an independently established licence for every possible IERS download. Verify its actual wheel files and notices in each installed/frozen artifact and do not claim all upstream data were cleared solely from the package's SPDX label.

Historical Windows evidence supplied by the artifact reviewer records IERS data in the September 8 local executable, SHA-256 `c09b534670a207736b44b32c0aa9a6a32be7913a684dd0c7b38a5bf6b6a13382`; its source revision is unverified. The current local Windows probe, SHA-256 `f68acf1fbcce3f3787d1c18b9fc3a127dfcde74d5391863482d7091228f47525`, contains `eopc04.1962-now` (5,171,100 bytes), `finals2000A.all` (3,767,520 bytes), `Leap_Second.dat` and three readme files. Neither is a certification of a published release. See `tools/licence/dependency-historical-windows-archive.json` and `dependency-current-windows-archive.json`.

The artifact reviewer decoded the current executable's frozen UI credits as data and found the complete 1,490-character astropy-iers-data BSD notice, matching the installed package after whitespace normalization. The displayed credit version is older than the installed package and needs correction, but the absence of a standalone LICENSE in the CArchive is not a notice failure: BSD permits notice reproduction in documentation or other distribution materials. See `tools/licence/dependency-frozen-ui-notice-proof.json`. This closes the package-notice question for that current local probe, not the separate upstream-policy provenance limit above.

The [python-sgp4 licence](https://raw.githubusercontent.com/brandon-rhodes/python-sgp4/master/LICENSE) is MIT. Retain its notice where the package is distributed. Its verification TLE corpus is acknowledged in `catalog/ephemeris/satellites.py:24` and in the credits note. That corpus is separate from runtime CelesTrak data. The registry simultaneously says sgp4 includes a verification corpus and “ships no data files at all”; correct the latter. No corpus was in the source tar, but installed and frozen copies need their own inventory.

### Horizons and sample frames

No third-party terrain/horizon dataset was found in the tar or runtime source. `server/astrodeck/locations.py:122` stores user-provided horizon settings; the editor operates on these values. Synthetic truth fixtures under `tools/photosphere_sim/fixtures/` are development assets and absent from the tar.

`server/tests/fixtures/ngc604_20260906/README.md:1` identifies repository-owned observing-frame crops; those test images are absent from the tar and must not be characterized as shipped data. This review did not read their FITS headers or private observing details. Simulated camera output is generated by app code in `devices/sim.py:619` and `:628`; it does not copy a bundled external sky image. No new external sample-frame licence requirement was established by the inspected artifact.

## UI raster provenance

### SD-05: nebula background

`ui/public/bg_nebula.png` is 945,918 bytes and is copied to `ui/dist/bg_nebula.png` in the tar. SHA-256: `c44f5e8e23760a61f1685e2597124e875f3637966384c9318196606985c73b5b`. Signature inspection and Pillow report JPEG, 1024 by 1024, despite the .png suffix. Metadata contains only JFIF/density fields, with no author, source or copyright. The image was visually inspected, without attempting to infer legal origin from its appearance.

The asset first appears in commits `f1b65161` and `81a583e7`, whose messages describe a nebula theme. Neither the commit record nor repository text searches supplies a source URL, generation record, purchase receipt or permission. The public repository licence alone does not prove the uploader held rights to a preexisting image. No claim that this is AI-generated, NASA-owned, DSS imagery or third-party copyrighted material is established. This is a high-priority evidence gap in an actually distributed asset.

Root has asked Claude for owner provenance. Until that evidence is available, this audit cannot approve unrestricted redistribution. Appropriate resolutions are a recorded owner creation/licence statement with the asset hash, a verifiable external grant and required credit, or separately authorized replacement/removal. No asset was modified.

### PWA icons

`ui/public/icon-192.png` and `icon-512.png` ship in the tar and are referenced by `ui/public/manifest.json:13`. Their addition is recorded in `ef85350b`, the shell/PWA commit. Both were visually inspected: a simple cyan target mark on a dark background. No external source or generator was found in the searched repository paths.

The repo history supports treating these as project UI assets, with a low-priority provenance-record improvement. It does not prove independent authorship. Record the creator/generator or owner confirmation alongside their hashes if the artifact registry represents them as first-party. Their simple appearance alone is not a legal exemption or a third-party licence grant.

## October recheck of the August audit

| August statement | October evidence and disposition |
|---|---|
| Releases bundle DSS2 and require an owner decision. | Obsolete for the inspected tar: pack staging/seed are refused. Permission concern remains for redistribution, stale workflow #631 remains, and PyInstaller input inclusion is a separate risk. Current CDS metadata adds ODbL database terms. |
| Astrospheric personal-project scope needs owner action. | Still unresolved. Current v1 and v2 primary docs retain the narrow scope. Per-user keys and acknowledgement do not settle public-client permission. |
| Player One is redistributed under a licence lacking an explicit distribution verb. | The tar excludes its libraries but retains LICENSE/README. Baseline PyInstaller whole-vendor copying is a distinct exposure for the artifact reviewer. This worker did not re-clear the SDK grant. |
| Declared libasi URL returns 404. | Source no longer declares that URL: `server/pyproject.toml` and `credits_registry.py:424` point to epim/libasi. The old claim must not be repeated as current. Exact optional dependency revision/licence is assigned to the dependency reviewer. |
| Open-Meteo placement gap was closed. | Closed on the two Conditions components; newer wind/cloud-band displays need nearby links. Service access restrictions were not adequately separated from CC BY data rights. |
| 2MASS has nothing owed. | Too broad: mission acknowledgement is requested and the current CDS database declares ODbL. No tiles were bundled in the inspected tar. |
| IAU-CSN 4.0 is an inference. | Confirmed. The actual edition's source header gives CC Attribution without a version. The old IAU copyright URL is unavailable. |
| Constellations are wholly app-generated. | Assignment rows are app-generated; the boundary implementation comes through Astropy's Roman et al. table and should be named. |
| All data accounted for by catalogue-directory scan. | Insufficient alone: brightstars.py, curated source tables, dependency IERS/TLE corpora and UI raster assets require explicit artifact inventory. |
| No restricted data is bundled. | Cannot be restated globally while the shipped nebula asset lacks provenance and other artifact families have not been opened. |

## Handoff and review status

Serious packaging paths and the nebula provenance gap were sent to root as soon as found. Open-Meteo placement, CelesTrak failure behavior, ODbL metadata, NOAA wording, IAU version and MPC grant precision were also sent for action. This worker authored this report and `tools/licence/registry-proposals.json`; no shared registry or application code was changed. Root owns changes to registries, notices, generated credits and gates; application/packaging behavior changes require the separate Claude-owned work described in the brief.

No claim of complete legal clearance, no-GPL status for an uninspected artifact, or tested compliance of a future native wheel is made. The data/service review can be used to correct the dated master audit, but unresolved owner evidence and artifact limitations must travel with it.

## Independent artifact-gate review, initial pass

The service/data reviewer independently read `tools/licence/audit_artifact.py`, `artifact-registry.json` and `build_ui_inventory.mjs`, and ran mutations in memory against the inspected tarball. No archive, registry, fixture or tool source was changed.

Two gaps were sent to root for correction:

1. An unregistered source file containing an ASCII table at `server/astrodeck/catalog/unaccounted_survey.py` adds no finding. All non-vendor Python files are currently treated as first-party source. Source-embedded `brightstars.py` and `objects.py` need explicit provenance records, and claims of complete artifact coverage need a reviewed source manifest or an explicit manual-review boundary. A UTF-8/NUL check only catches some binary disguises; it does not account for embedded data.
2. Removing all licence texts from the generated OpenNGC credit adds no finding. Registered assets currently check credit-name presence but do not apply the required-text check used for npm components. Apply required-text, flag and SPDX consistency checks to the credits of registered data/font assets too, including references into the generated licence-text pool.

The unchanged local baseline produced four expected findings: missing Vite and Tailwind compiler/runtime credits, and two findings for the unresolved nebula asset. These expected failures did not mask the mutation comparison: neither mutation added any finding. This is an initial review of tools still being edited, not a final gate signoff. Root owns corrections and regression tests.

## Optional libasi source recheck, 2026-10-01

`server/pyproject.toml:66` declares the optional `asiair` extra as `libasi @ git+https://github.com/epim/libasi`, with no revision pin. The current public upstream is [epim/libasi](https://github.com/epim/libasi), branch `main`, observed revision `878baea5ec22fa4392fdc3645f05785644bb2a53`. GitHub reports it as public and not a fork. The source declares package `libasi` version `0.1.0`, exports `ASIAIRClient` from `asiair`, includes `CONFIRMED_FORMATS.md`, and declares no project dependencies. These content checks match the optional backend import and cited protocol reference; no installation or device call was made.

The [licence at that exact revision](https://raw.githubusercontent.com/epim/libasi/878baea5ec22fa4392fdc3645f05785644bb2a53/LICENSE) is MIT, copyright 2026 libasi contributors. Its SHA-256 is `97ce16543c6e505a48a2a334648fb9cef487f9998eaa4ef9dbdedc4376268579`; the complete existing `tools/licence_texts/libasi.txt` matches after whitespace normalization. The MIT grant supports redistribution of that source subject to preserving its copyright and permission notice. This closes the old missing-source/404 evidence gap for the current declared URL. The old dead URL is not the active dependency.

The dependency remains unpinned, so a future optional install is not fixed to this inspected revision. Neither the source tarball nor the base-dependency Windows probe establishes inclusion of an installed libasi copy; an artifact built with the optional extra needs its own revision and payload record. No claim about rights in ZWO firmware, protocol documentation from another owner, or permission from a device vendor is inferred from libasi's MIT file.

## Bundled-component handoff and final independent review

`tools/licence/bundled-components.json` contains 16 registry-style records tied to the current local Windows artifact: WCSLIB, OpenBLAS, LAPACK, GCC runtime, CPython, both observed OpenSSL versions, setuptools and eight retained setuptools vendors. Twenty `tools/licence_texts/bundled-*` files preserve complete package/publisher texts. Text references have SHA-256 hashes. WCSLIB uses `LGPL-3.0-or-later`; the GCC runtime uses `GPL-3.0-or-later WITH GCC-exception-3.1`. Both retain owner flags for #638. This inventory does not establish a GPL/LGPL-free distribution.

The ODbL 1.0 legal text comes from the publisher's own website repository at pinned revision `c9902b1df7595e1d765da24c320ac84b8ad2f3dd`, path `content/licenses/odbl/odbl-10.txt`. Its retained normalized UTF-8 SHA-256 is `607680718977f6f6c9607972afd98f208573f19251315ed1362a8589b51beaf5`. The public website download returned 403; the publisher repository supplied the full text. The database grant remains separate from DSS/2MASS image rights.

After root integrated the inventory and corrected the gate, this reviewer independently reran the five focused test modules: **73 passed**. The command used the existing audit Python environment with pytest, `-n 0 -q -p no:cacheprovider`; no dependencies were installed. The earlier ASCII-table and missing-notice gaps are closed. Separate in-memory probes rejected an unregistered ASCII data module, missing OpenNGC text, a corrupt notice-pool reference, five prohibited or owner-needed licence expressions, and an unknown frozen member plus changed executable hash. No shared source, fixture or archive was mutated by these probes.

The final read review covered `audit_artifact.py`, `audit_frozen.py`, `audit_dependencies.py`, the UI input recorder, source/data registry, policy, generator and freshness tests. Source hashes bind reviewed Python files, including embedded star data. Asset credits must match reviewed SPDX and carry valid notice references. The UI recorder now recognizes only the observed CommonJS helper identifier; unknown virtual helpers fail instead of inheriting Vite's licence. Generated input fingerprints cover registry/policy/generator/texts/manifests, and the tests reject stale generated data. Survey records use the remote-data tier, preserve ODbL text and retain separate original-image uncertainty. Restricted-asset prose no longer treats a local fetch or operator acknowledgement as blanket permission.

The reports inspected after correction remain bounded and conservative:

- The 478-file local tarball reports three findings: IAU star-name licence-version uncertainty and the nebula asset's unknown licence/owner decision.
- The 887-member local Windows executable reports 13 findings, including the six Player One libraries, nebula provenance, WCSLIB/GCC dispositions, stale baseline credits, absent native engine, and incomplete Microsoft/native coverage. Its gate does not permit a cleared result merely because known bytes match.
- The dependency report covers 269 resolved Cargo/npm entries and passes SPDX policy. This reviewer confirmed its recorded lockfile fingerprints match current files. That report explicitly excludes binary, notice and service clearance.

No further gate or registry defect was found within this reviewed scope. The open rights, packaging and service findings remain release blockers or owner work as described in the reports. Root's 16 named mutation kills were reported by root; this worker did not claim an independent rerun of that mutation harness. The independent evidence here is the 73-test rerun, the separate in-memory probes and the code/report review.