# Release gate review, before the required rebase

Date: 2026-10-01. These are local Windows/source results before the required rebase onto e764e172. They are not a release approval or evidence for an unbuilt Linux/macOS executable. Exact artifact hashes and findings counts are in `release-gate-pre-rebase.json`.

## Actual artifacts and corrections

The source tar contains 777 members. All source, UI and asset members are accounted against immutable Git 6e8dad9d, the earlier asset registry and individually read packaging deltas. Its eight findings are the existing nebula/IAU owner questions plus three central pending records. Two physical copies of the UI background produce separate asset findings. No Player One binary or DSS2 tile inclusion was accepted.

The first native wheel (9700cc11) initially passed an insufficient gate. Parent review found its binary source archive incorrectly declared as a PEP 639 License-File. That result is superseded. The builder and gate now require four declared UTF-8, NUL-free text notices and an independently sealed ordinary source archive at `dist-info/source/native-source.tar.gz`. The rebuilt Windows wheel (9b2d8f28) passes: 12 members, zero findings. RECORD, extension/version/platform, exact source closure, tree digest, complete notice texts, declared paths, Cargo graph and SBOM seals are checked. This is build provenance, not a reproducible Rust compiler proof.

The final pre-rebase executable has 3,450 expanded members, including Python modules inside PYZ/base_library.zip. Every member matches a retained TOC input by exact bytes or compiled-source equivalence. Equivalence preserves constants, bytecode, exception tables and code flags; it ignores filename/line bookkeeping and never executes application code. Some OS DLL inputs remain unclassified even though their bytes match an input.

Its entire Windows executable also reconstructs byte for byte from the installed RECORD-verified PyInstaller 6.22.3 bootloader, default console icon, standard manifest and retained PKG, with only the documented timestamp and PE-checksum transforms. The collector never runs the reconstructed executable. Other PyInstaller versions, custom options, ELF and Mach-O transforms fail closed pending specific implementation and review.

The frozen gate now reports 636 findings: 129 previously observed native-payload rights findings, one new psutil native extension, 496 provenance findings, two unreviewed-runtime credit findings, five owner decisions, two artwork findings and one server PEP 610 direct_url.json payload. Categories overlap in the underlying files. Independent review found that the interpreter installation directory did not authenticate its contents. The collector now requires an explicit reviewed per-file runtime source/hash record before assigning CPython verification. No such manifest has been auto-populated; the additional runtime findings are technical release blockers. Exact TOC source matching and authenticated distribution provenance are different checks. Historical bytes are not relabeled as rights approval. The private URL metadata is being excluded through a separately reviewed packaging change. No owner mode resolves unknown binaries, missing notices or platform provenance.

An intermediate collection after the installed native wheel was replaced reported seven old native metadata mismatches. That was a stale artifact/environment pair, not a failure of the subsequent final build: the final collection again has zero unmatched TOC inputs. The prior zero-unmatched collection and the intermediate mismatch must not be presented as the same evidence snapshot.

## Terms applied to actual retained paths

PyInstaller's [versioned COPYING](https://github.com/pyinstaller/pyinstaller/blob/v6.22.3/COPYING.txt) permits combining its bootloader and loader files under its named bootloader exception, while runtime hooks/fake modules use Apache-2.0. Its phrase is "unlimited permission to link or embed compiled bootloader". Our scoped reading is limited to the exact named directories, unchanged compiled-source/input proof and the checked full notice digest. Ordinary PyInstaller build modules do not receive this exception automatically.

The [community-hooks license](https://github.com/pyinstaller/pyinstaller-hooks-contrib/blob/v2026.8/LICENSE) distinguishes standard hooks from `_pyinstaller_hooks_contrib/rthooks`; only the latter retained runtime paths receive the Apache-2.0 reading. The gate checks the complete current notice digest and embedded full text. It does not rewrite environment metadata, classify the whole distribution as Apache, or treat the executable as containing no GPL-related code.

Current Python notices are collected from installed distributions, including full short selector notices and vendored notices. A Python distribution and a Cargo crate with the same name have separate identities. Packages moved into the generator's flagged group retain their Python/Cargo scope. Absent optional comtypes, h5py and libasi are explicitly recorded without invented installed notices; missing core dependencies remain fatal.

## Policy and trust boundary

`packaging/distribution-policy.json` is the single owner-decision source. Pending preserves the historical per-artifact Player One selection but remains a blocker when retained. Fetch-only cannot clear a still-retained service/artwork/library. An explicit redistribute record resolves only its exact known owner finding and exact known bytes; it does not invent permission, waive source/relinking requirements or approve new data. Each actual new payload needs review.

The source anchor stays 6e8dad9d. `release-reviewed-inputs.json` records only changes read individually, with exact normalized UTF-8 digests and a review explanation. The mandated new base must be reconciled by inspecting its shipped-source diff, not by moving the anchor to the mutable checkout.

Build reports are tied to artifact hashes, collector hashes, installed RECORD checks and retained TOCs. They assume those build inputs and the audited tooling are trusted. They are not signatures against a malicious build host or proof that a compiler generated the intended machine code.

## Validation and remaining work

62 unit tests pass. All 23 named gate mutants fail through their intended assertions; original source bytes are restored in finally blocks and the restored suite passes. `release-gate-mutations.json` records source hashes and targets. The tests cover archive/metadata smuggling, native machine headers and seals, exact root notices and MPL expression, complete source, text licenses, owner modes, data provenance, same-name dependency groups, Python runtime-visible code names and raw PYZ duplicates/gaps/trailing bytes. Actual pre-rebase PYZ namespace and payload ranges pass the stricter reader. They do not establish unrun platform behavior.

After the required rebase: review exact shipped input deltas, refresh the specific review hashes, rebuild native/credits/UI/tar/frozen artifacts, rerun collectors/gates and retain their new evidence. Windows OS/UCRT provenance, native subcomponent rights and other-platform transform reviews remain technical release blockers in addition to the owner's named questions. Root independently owns workflow/upload order, artifact builds and final reporting.
## Rebase reconciliation at 81788167

The final integration checkout is 81788167 atop c58ff3e4. Every changed shipped runtime source between immutable 6e8dad9d and e764e172 was read: ten server files and eighteen UI files. The changes are existing application logic, state handling and UI copy; no new dependency, third-party asset or dataset was introduced in those deltas. Each file's normalized UTF-8 bytes were compared with its immutable e764e172 version before its specific review record was added. The parent separately read and supplied the two Rust source rows, whose immutable hashes and current bytes were checked. The source anchor remains 6e8dad9d.

The final server pyproject was compared semantically with immutable c58ff3e4: only the reviewed package-data table differs. The upstream pytest correction keeps the retention count and removes the failed-only policy. The ledger records its final normalized hash, 2dbbb056878df286aa173d0bd4c6f58cd758174d73c6f811496ea4bdc0ccf121. The final spec and new metadata helper were read individually; only root application/native PEP 610 direct_url.json files are omitted, with nested notices and native source retained. The ledger now contains 39 specific reviewed inputs.

After the rebase, all 23 named mutants again failed through their intended assertions, exact original source bytes were restored and the restored 62-test suite passed. The harness now matches each source's existing line endings so a Windows CRLF checkout does not invalidate its one multiline mutation anchor. This does not change the gate or collector semantics. Gate and provenance source remain frozen. Rebuilt artifacts at the forthcoming integration commit still require fresh collectors and gates; the pre-rebase results above remain explicitly superseded for that build.

## Post-Analysis installer metadata correction

The d836738e executable passed retained-input matching and byte-exact envelope reconstruction, but final member inspection found native dist-info/direct_url.json still present. The application copy was absent. PyInstaller's automatic metadata collection had reintroduced the native file after the spec's explicit metadata expansion. No installer URL body was read or printed. The d836 compact results are preserved separately in release-gate-d836-superseded.json, with the full private reports retained at their recorded location. That executable is superseded, not a final release artifact.

The narrow correction filters the two selected metadata root destinations after Analysis. The gate independently rejects application/native root direct_url.json regardless of a matching RECORD or wheel hash, and native metadata no longer grants an exception for this installer file. Benign INSTALLER and REQUESTED controls remain accepted. All 65 tests pass; all 25 named mutants fail through their intended assertions, exact source bytes are restored and the restored suite passes. Only the two separately read spec/helper hashes were refreshed in the source ledger. A fresh committed build and actual archive absence check remain required before reporting the leak closed in artifact evidence.

## Final actual artifacts at e21ad9cb

The final local artifact batch was built at e21ad9cbb5353186d520e87f00f33354e19f3e51 after the reviewed post-Analysis correction. Exact hashes, byte sizes, finding counts and private evidence digests are in release-gate-rebased.json. The pre-rebase and d836 artifacts are superseded for this batch; d836's retained native installer metadata finding is now closed by actual archive inspection, not merely by source reasoning. None of the three final archives contains direct_url.json. Benign INSTALLER and REQUESTED marker paths remain explicitly listed in the compact evidence, so this is not a claim that all installer metadata is absent.

The source tar has 777 members and all are accounted; its eight findings are the existing owner/artwork/IAU questions. The Windows native wheel has twelve members and passes with zero findings. The Windows executable has 3,448 expanded members. Every payload matches its retained input and the whole executable reconstructs byte for byte using the reviewed Windows envelope method. Its gate still fails with 635 findings: 496 provenance findings (452 unauthenticated interpreter inputs and 44 OS/UCRT inputs), 129 historical native-rights findings, one new psutil native payload, two derived unreviewed-runtime credit findings, five central owner decisions and two artwork findings. These categories overlap in files and remain separate obligations. Byte matching is not authentication or rights clearance.

The required external-needle privacy scan used privacy_scan.load_needles, patterns_for and scan_text entirely in memory on every expanded payload decoded with UTF-8 replacement. It also expanded the native source archive. External needles were present and nonempty; scanned counts were 777 for source, 88 for the native wheel and 3,524 for the executable. All three were clean, with no matching filenames. Reports contain artifact hashes, counts and marker paths only, never forbidden values or payload content. This covers the configured values and scanner rules, not all conceivable personal information. No raw server logs or application state were read.

The gate source remains the reviewed correction with 65 passing tests and 25 named mutants killed, exact byte restoration and a passing restored suite. This final step changed only evidence files. Linux/macOS frozen artifacts remain unbuilt here and have no local clearance. Copyright stamping or any later source edit requires a specific reviewed-hash refresh and rebuilt artifact evidence; this report is bound to the three exact hashes, not a future checkout.

## Delta review to b3ce0f1b (2026-10-20, #1073)

The reviewed base moved from 6e8dad9d (2026-10-01) to b3ce0f1b8f3e2ce68fd7baa740d61c3848165837. That commit carries the owner rulings of 2026-10-20 (#637, #636, #632, and the iau row on #1073), the coverage change that removes astropy.wcs (#638), and the release workflow changes. Between the two bases, about 2,170 commits landed on main.

Method. Every shipped file was compared with its copy at the old base:
- the members of the source tarball built from b3ce0f1b, with LF line endings, through the release packager;
- the 880 recorded UI build inputs.

For each new or changed text file, the added lines were scanned for four things:
- licence or copyright markers other than the project's own header;
- provenance phrases ("adapted/ported/copied from", third-party URLs);
- runs of twelve or more literal data lines, and long opaque tokens;
- non-ASCII characters.

Binary members were listed for review by hand. The scanner and its JSON reports are kept with the session record, not in the repository. Counts:
- Tarball: 578 members. 316 changed, 230 new and 32 unchanged against the old base. ui/dist outputs are "new" by construction, since they are build products reviewed through their recorded inputs.
- UI inputs: 837 changed, 37 new.

Findings, each read by hand:
- **Licence and origin flags.** Every licence and origin flag in the server, native and packaging delta is first-party prose: "derived from the site", "taken from", comments about notices and LGPL, and OpenNGC column notes for data already credited.
- **WMM2025.** `ui/src/next/hubs/sky/sheets/pano/wmm2025.ts` is the one third-party table: the World Magnetic Model WMM2025 coefficients. They are US Government material from NGA/NCEI and in the public domain. The file records the source file, its md5 and the public-domain statement, and a test pins its digest. No credit is required. It is recorded here.
- **noise.ts.** `ui/src/next/hubs/sky/sheets/pano/horizon/noise.ts` is ported from the project's own photosphereStability.ts.
- **Non-ASCII.** Non-ASCII content is typographic: dashes, arrows, degree signs, section signs, box-drawing rules in comments, and Greek letters in formulas.
- **Binary members.** The binary members are the UI's @fontsource woff/woff2 outputs (npm inputs, credited through the UI inventory), the two PWA icons and bg_nebula.png. bg_nebula.png is owner-made and redistributable, per the owner ruling on #637.
- **Data assets.**
  - `ngc_extras.tsv` changed in WP-129 (d39c6361, #181), which added axis-ratio and position-angle columns from the same OpenNGC source (CC-BY-SA-4.0). Its row now names the renamed generated credit.
  - constellations.tsv, discoverers.tsv and ngc.tsv are unchanged. Their old registry hashes were CRLF hashes, and the gate now hashes text payloads after CRLF to LF.
  - The ZWO LICENSE.txt notice is unchanged, and the same line-ending fix applies to it.
- **Player One SDK.** The six libraries are now registered with their exact bytes. Under the #632 ruling they ship in the source tarball too.

Evidence written:
- BASE in audit_release.py.
- reviewed_baseline.
- reviewed_sources: 253 shipped server Python files and pyproject.toml, with CRLF-normalized hashes. Each existing row's component, credit and SPDX are kept; only brightstars.py carries any.
- The ngc_extras.tsv row.

Nothing was regenerated as a build step. A later change to a shipped file is SOURCE_CHANGED or UNACCOUNTED again until it is reviewed.

Still open:
- The wcslib row stays pending until a frozen build is shown free of WCSLIB.
- The frozen executables keep their own provenance and native-rights findings, recorded above. Under the source-first workflow, those binaries are withheld rather than published.

## Delta: OpenNGC snapshot pin (#804)

`ngc.tsv` and `ngc_extras.tsv` are regenerated from one OpenNGC commit, `be3cd6c5498806a2fbb2f7e0452ef802dbb02be0` (master on 2026-10-18), by `server/tools/build_ngc_catalog.py`, which fetches that commit, checks NGC.csv and addendum.csv against recorded sha256 values, and refuses any ref that is not a full commit id. `ngc.tsv` records the commit and both checksums in its header. Same upstream, same licence (CC-BY-SA-4.0), same columns and same filter; no new source and no new field.

Reviewed difference against the previously shipped files (which came from an unrecorded, older download):
- `ngc.tsv`: 13,369 rows before and after, same ids. 141 rows differ: 97 magnitudes and 43 sizes, some rows both (OpenNGC data revisions, for example IC 2105 from 0.65 to 3.0 arcmin), and name or position changes on five rows. Flame Nebula moves from IC 434 to NGC 2024 and Cocoon Galaxy from NGC 4990 to NGC 4490, both upstream corrections; Mel072 has a new position and no magnitude.
- `ngc_extras.tsv`: 12,013 rows before, 12,001 after. 2,436 rows differ, 2,385 of them only in redshift. 20 rows that carried only a redshift upstream has since removed are gone, 8 rows are new, and shape, minor-axis and position-angle cells moved on 51 rows. IC 2105 and IC 3231, which disagreed with `ngc.tsv` about size and so had no shape, now agree and have one.

The registry rows for both files carry the new hashes, computed with `content_sha256` in `audit_artifact.py` (CRLF to LF for text).

## Delta review for #638 (2026-10-23, owner ruling D-F)

- **WCSLIB is not shipped.** `astropy.wcs` left the runtime in #1103, and `packaging/astrodeck.spec` excludes `astropy/wcs/` and `astropy/visualization/wcsaxes/`. The WCSLIB record, its two notice texts and its entries in `THIRD-PARTY-NOTICES.md` and the generated credits are removed. The executables on v0.3.23 through v0.3.28 stay as they are, and the archive baseline still lists `astropy/wcs/_wcs.pyd` because those binaries contain it.
- **The guard stays.** `audit_frozen.py` no longer reports an open disposition for WCSLIB or for the OpenBLAS runtime. A frozen archive member `astropy/wcs/_wcs.*.pyd`, `astropy/wcs/_wcs.*.so` or any `WCSLIB_LICENSE.rst` is a finding: "WCSLIB is not shipped (#638)". `server/tests/test_638_coverage_without_wcslib.py` covers it.
- **The final data TOC.** The spec removed `astropy/wcs/` rows before Analysis, but a PyInstaller hook could add them back. `metadata_payloads.filter_final_datas` now drops installer URLs and WCSLIB data from `a.datas` in one pass (#1073). `packaging/tests/test_release_packaging.py` feeds it hook-added rows.
- **GCC Runtime Library Exception.** The record for the runtime statically linked into NumPy's OpenBLAS carries `bundled-gcc-runtime-exception-3.1.txt`, cut verbatim from the NumPy wheel notice, and the GPL text. The record is marked `reviewed_exception`, so `gen_credits.py` drops the automatic copyleft flag for it and for nothing else; the generic expression stays flagged. `licence_policy.py` states the rule: no GPL-family code without a reviewed exception record.
- **Supersedes** the earlier line "The wcslib row stays pending until a frozen build is shown free of WCSLIB". The `wcslib` row in `packaging/distribution-policy.json` is now `not-shipped` (evidence: owner ruling D-F on PR #1130, 2026-10-23), a mode `packaging/distribution_policy.py` accepts. `audit_release.py` no longer emits `OWNER_PENDING wcslib`, and no longer ties the row to OpenBLAS; it emits `POLICY_EXCLUDED` for wcslib only when `astropy/wcs/_wcs` is present in a frozen artifact.

## Delta review for release 0.3.47 (b3ce0f1b to 31ad609a)

The reviewed base stays b3ce0f1b. The source gate's dry run on 31ad609a named 64 shipped first-party paths that differ from it: 31 SOURCE_CHANGED (3 native Rust files, the notices file, 2 packaging files, 25 UI files) and 33 UNACCOUNTED (32 server Python files and one new one, `server/astrodeck/imaging/frame_class.py`). They are waves 1 and 2 of the open-issue consolidation.

Method. Each path's diff from b3ce0f1b was read in full (ignoring line-ending changes), then the added lines of all 64 were scanned for licence and copyright markers, provenance phrases, third-party URLs, long opaque tokens, runs of literal data lines and non-ASCII characters. The scan found no licence marker, no third-party URL and no data run. The non-ASCII characters are typographic: section signs, middle dots, degree signs, dashes, minus signs, box-drawing rules, and one warning sign inside a comment that moved with its code (it was already in the base). None of the 64 paths is a dependency manifest, lockfile, asset, font, image, binary or catalogue, and between b3ce0f1b and 31ad609a `package-lock.json`, `Cargo.lock`, every `Cargo.toml`, `ui/public` and the vendor tree are unchanged; the only changed data files are the two OpenNGC tables already reviewed under #804.

Evidence written, using the mechanism the gate already reads and nothing new:
- `release-reviewed-inputs.json`: one row per path with the exact normalized sha256 (computed by `release_provenance.sha` over `release_provenance.normalized_source`) and a review sentence. Sixteen paths already had a row from the earlier 6e8dad9d review; those rows now carry the current hash and the delta from b3ce0f1b. The other 48 are new rows.
- `artifact-registry.json`: one `reviewed_sources` row for the new `frame_class.py`, which the gate matches by hash because no earlier rule names it.
- The two version-bearing reviewed hashes were carried by `version_bump_evidence.py 0.3.47`, and `ui/src/credits.generated.json` was regenerated by `tools/gen_credits.py`.

The three licence files were read as licence changes, not as source. `THIRD-PARTY-NOTICES.md`: the WCSLIB table row and the sentence naming WCSLIB are removed and the GCC runtime row links `bundled-gcc-runtime-exception-3.1.txt` and the GPL text (that exception text is a verbatim substring of the NumPy 2.5.3 wheel notice). `packaging/distribution-policy.json`: the wcslib decision becomes `not-shipped`; no other decision changes. `packaging/distribution_policy.py`: `not-shipped` joins `MODES` and two docstring lines describe it. All three are exactly owner ruling D-F (#638) and nothing else. The sentence "no GPL-family code without a reviewed exception record" is in `tools/licence_policy.py`, which is not one of the 64.

Result. A source tarball built from the release commit's tree (build_credits, build_ui_inventory, build_release, as release.yml does) passes `audit_release.py --kind source-tar` with 582 members and 0 blockers.

## Delta review for release 0.3.48 (0.3.47 to daaa3450)

The reviewed base stays b3ce0f1b. A source tarball built from daaa3450 (build_credits, build_ui_inventory, build_release, as release.yml does) named 81 shipped first-party paths that differ from the reviewed state: 52 SOURCE_CHANGED (UI files) and 29 UNACCOUNTED (server Python files). They are waves 3 and 4 of the open-issue consolidation (PRs #1195 to #1235).

Method. For each path the diff from its last reviewed state was read in full, line endings ignored: the 0.3.47 reviewed state (the commit fd22fa93, checked by the ledger row's own hash) for the 23 paths whose ledger row matches it, and b3ce0f1b for the rest. Two paths had a ledger row from the older 6e8dad9d review that matches neither tree (`server/astrodeck/sequence/resume_arm.py`, `ui/src/next/hubs/settings/tuning/files/filesModel.ts`); they were unchanged at 0.3.47 and were accepted there by equality with b3ce0f1b, so their delta is read from b3ce0f1b. Eight of the 81 are new files: `server/astrodeck/imaging/mosaic_preview.py`, `server/astrodeck/sequence/recovery.py`, `server/astrodeck/site_horizon.py`, `ui/src/api/horizonSuggestion.ts`, `ui/src/components/PanelPicker.tsx`, `ui/src/lib/objectShape.ts`, `ui/src/next/hubs/sky/sheets/HorizonSuggestionOffer.tsx` and `ui/src/next/hubs/sky/sheets/horizonSuggestionModel.ts`; each was read in full. The added lines of all 81 were then scanned for licence and copyright markers, provenance phrases, URLs, long opaque tokens, runs of literal data lines and non-ASCII characters. The only copyright and SPDX lines are the project's own header on the new files. The only third-party URL is `forum.telescopius.com` in the `panel_csv.py` docstring: it names the public forum post whose header and rows are the single known sample of Telescopius's CSV, and the file carries only format facts (column names, mark characters, units). The sample rows themselves are not in the shipped file. The non-ASCII characters are typographic: box-drawing rules in a comment and two em dashes in message text that moved with its code and one small triangle in a new warning message.

None of the 81 paths is a dependency manifest, lockfile, asset, font, image, binary or catalogue. Between 0.3.47 and daaa3450 `package-lock.json`, `Cargo.lock`, every `Cargo.toml`, `ui/public`, the vendor tree, the catalogue data, `THIRD-PARTY-NOTICES.md`, `packaging/distribution-policy.json`, `packaging/distribution_policy.py` and every file under `tools/licence` are unchanged apart from this release's own evidence edits.

Evidence written, using the mechanism the gate already reads and nothing new:
- `release-reviewed-inputs.json`: one row per path with the exact normalized sha256 (computed by `release_provenance.sha` over `release_provenance.normalized_source`) and a review sentence. Twenty-five paths already had a row (23 current at 0.3.47, 2 older) and now carry the current hash and the delta; the other 56 are new rows.
- `artifact-registry.json`: three `reviewed_sources` rows for the new server files `mosaic_preview.py`, `recovery.py` and `site_horizon.py`, which the gate matches by hash because no earlier rule names them.
- The two version-bearing reviewed hashes were carried by `version_bump_evidence.py 0.3.48`, and `ui/src/credits.generated.json` was regenerated by `tools/gen_credits.py`.

Result. A source tarball built from this tree passes `audit_release.py --kind source-tar` with 587 members and 0 blockers.
