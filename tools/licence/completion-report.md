# Licence audit completion report

The October audit is complete for the artifact evidence available locally.
This branch corrects notices/credits, preserves current primary-source terms,
and adds checks that expose unresolved distribution findings. It does not
approve a release.

## Scope and corrections

- Built and opened a fresh source/UI tarball using the real release packager.
  Rebuilt its UI with corrected generated credits and retained actual Vite
  chunk inputs. Inventoried every member, all fonts, rasters and embedded data.
- Built and opened a fresh Windows x86_64 PyInstaller executable in a private
  clean environment. Inventoried 887 members and 2,384 PYZ modules without
  running it. Built a separate native ABI3 wheel offline and inspected its six
  files. Retained exact hashes and native linkage evidence.
- Added 16 evidence-backed records for WCSLIB, OpenBLAS/LAPACK/GCC runtime,
  CPython, both bundled OpenSSL versions, setuptools and eight retained
  setuptools vendors. Added 20 full licence/notice files, including ODbL.
- Corrected survey database/image rights, IAU-CSN uncertainty, service terms,
  Player One artifact-specific facts and libasi's current source. Vite runtime
  helpers and Tailwind CSS are now credited despite npm's development flags.
- Regenerated 146 credits and 120 distinct texts. Fixed the stale project
  version and added direct version/input-freshness regressions (closes #639).
- Preserved the original August audit and appended a dated October correction.
  No fetch/acknowledgement behavior, packager, release workflow, Dockerfile,
  device integration or deployed application was changed.

## Validation

The five focused Python test modules pass all 73 tests. A separate reviewer
reran them and independently probed the previously found source-data/text gaps.
All 17 named mutants are killed by their selected assertions; source bytes are
restored in finally blocks and verified by SHA-256 before the restored green
suite. The rogue-binary mutant inserts a DLL before the real packager runs.

Generated credits --check passes. Privacy scans pass with the required external
needles. UTF-8/no-BOM and authorized-path checks pass. Authored files pass the
whitespace check; the unfiltered check reports only trailing whitespace/blank
EOF lines preserved verbatim in the upstream CPython, NumPy and ODbL texts. The UI build records 215 outputs. The live
locked/offline dependency policy gate passes for 40 Cargo and 229 npm entries.
This metadata result does not clear native components hidden inside wheels.

Actual artifact results remain deliberately red:

| Artifact | Result |
| --- | --- |
| Final source/UI tarball | 478 members; IAU-CSN unversioned grant and nebula artwork cause 3 findings |
| Current Windows executable | 887 members; 13 explicit packaging/permission/coverage findings |
| Native ABI3 wheel | Missing complete licence/notice bundle and corresponding-source route, recorded under #630 |
| Other executables, containers and appliance images | Not inspected; no clearance claimed |

[Final gate results](final-gate-results.json) preserve hashes and findings.
[Mutation evidence](mutation-evidence.md) contains the exact failing assertions.
[Service/data review](service-data-audit.md) and
[artifact review](dependency-artifact-audit.md) preserve source links, quotes,
readings and independent-review conclusions.
[Reproduction instructions](README.md) describe the checks and their limits.

## Owner and application work

Claude owns the tracked packaging and application follow-ups:

- #630: missing native engine and native-wheel notice/source packaging.
- #631: obsolete DSS2 workflow fetch.
- #632: Player One redistribution confirmation or exclusion across formats.
- #634: Open-Meteo links beside the newer displays.
- #635: CelesTrak stop-on-error behavior.
- #636: Astrospheric public-client/many-install clarification.
- #637: nebula source/rights evidence or authorized replacement/removal.
- #638: exact WCSLIB source/relinking and GCC runtime exception disposition.

The IAU star-list licence version, MPC bulk redistribution terms, exact
OpenNGC source revision, Microsoft runtime basis, complete third-party native
internals and uninspected platform artifacts remain explicit limitations.
No registry label, notice file or green unit test disposes of them.

All scratch builds, private dependencies and probe outputs were isolated under
the owned worktree's ignored .probe directories. The shared Python/npm
environments were not modified. No real rig, relay or private observing-site
configuration was contacted. No push, PR, merge or deployment was performed.
