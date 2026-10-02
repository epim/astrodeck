# Release engineering handoff

JOB4 implements matching native-wheel builds and stateless packaged capability probes (#630), removes DSS2 fetching from release builds (#631), repairs and checks relay build contexts (#645), and repairs owned binary smoke lifecycle checks (#654). The local native wheel passes its artifact gate. The source and frozen gates remain red for the findings below, so publication is blocked. The workflow uploads accepted payloads only after their gates and waits for source plus all four binary targets before publishing.

Artifact source commit: `e21ad9cbb5353186d520e87f00f33354e19f3e51`, atop frozen integration base `c58ff3e4`. The immutable review anchor remains `6e8dad9d66f66b78d5df7cc12872f49e1f626d6b`. An evidence-only commit records these results separately; rebuilding later requires the new checkout's source revision and fresh evidence.

## Actual final artifacts

| Artifact | Expanded members | Gate | Findings | SHA-256 |
| --- | ---: | --- | ---: | --- |
| source | 777 | FAIL | 8 | `a81996596e3a1b5660305f7316550a6440d4e6ff8f1ad2accf187015fd68c087` |
| native | 12 | PASS | 0 | `594782a346999938efd846a1c5c833d6fd9d865c3deac56869044dc8154d3ca4` |
| frozen | 3448 | FAIL | 635 | `18558b736c160ab8d6bda8c5421c9b590d8be39ea9dc2d280023ba0f06547464` |

The source's 777 members are accounted against the immutable anchor, 39 individually reviewed input rows, current generated UI and the asset registry. Its eight findings concern artwork/IAU and pending owner records. The native wheel contains 12 members, four UTF-8/NUL-free license/notice texts, an ordinary native source archive, Cargo/SBOM data and source/extension seals. Its source revision matches the build commit. The binary has zero retained-input mismatches and its entire Windows envelope reconstructs byte for byte from verified PyInstaller 6.22.3 inputs and the retained package.

The frozen gate has 635 findings: 496 provenance findings (452 runtime and 44 OS/UCRT), 129 historical native rights findings, one new psutil extension, two derived runtime-credit findings, five owner decisions and two artwork findings. Categories overlap in the underlying files. Directory placement and previously observed bytes do not authenticate runtime provenance or grant platform/subcomponent redistribution rights. Explicit reviewed runtime source/hash records and component rights review remain technical release blockers alongside owner decisions.

## Privacy and actual behavior

No `direct_url.json` member ships in any final source/native/frozen artifact, including inspected nested ZIP and native-source archive contents. Private PEP 610 installer URL metadata is absent. No URL body was printed. The frozen executable retains benign `INSTALLER` and empty `REQUESTED` markers; their exact paths are recorded in the structured privacy results. This statement does not claim those benign markers are absent.

The external needles file was required and present. Scanning used the repository scanner's escaping, case and numeric-truncation rules in memory over every expanded member: 777 source files, 88 native files and 3,524 frozen files, totaling 4,389 scans. The native and frozen totals each include 76 nested native source members. All three scans are clean; no matched content or needle values were emitted. This proves the configured privacy checks, with their documented scope.

All 244 installed server Python files match this committed source exactly. The packaged extension imports and executes the real Rust detector on a blank 32 by 32 uint16 frame, with zero stars and the matching source seal. The disposable server uses fresh private config/captures and an ephemeral loopback port; process creation, executable, arguments, ancestry and listener ownership are checked before requests. `/healthz` reports 0.3.39, `/` serves the SPA and `/api/backends` has ten registrations. Owned cleanup completed. No full status, mount altitude/azimuth or raw application log was read.

## Validation and review

279 focused tests pass: 74 packaging, 65 artifact gates, eight workflow, 14 relay and 118 existing regressions. All 91 named mutants fail through their intended assertions: 48 packaging, 25 gates, eight workflow and ten relay. Every mutation restores exact original bytes in finally, checks restored hashes and reruns its restored suite. Actionlint 1.7.11, whitespace/BOM checks, all 39 reviewed input hashes, required source privacy and staged privacy pass. The checks use private build caches and a prepared test interpreter; the shared node_modules junction remains read-only.

Independent reviews reused the existing task context. They closed two relay matcher false accepts and three provenance false clears: broad runtime-directory trust, omitted runtime-visible Python code names and collapsed duplicate PYZ entries. Their reports are `packaging/root-release-review.md` and `tools/licence/release-provenance-review.md`. These reviews were not fresh-context reviews.

Final artifact inspection also caught an invalid binary PEP 639 License-File, a relative font-input inventory classification, stale same-version server wheel installation and automatic metadata hooks reintroducing private URL metadata after explicit selection. Text-only license declarations, exact wheel reinstall without dependency changes, explicit input classification and a narrow post-Analysis metadata filter correct those failures. The gate independently rejects the private metadata even when RECORD and wheel hashes match.

## Preserved evidence and integration limits

`release-gate-pre-rebase.json` records the corrected pre-rebase tar/wheel/executable hashes (`42a2bc63`, `9b2d8f28`, `3c7792ff`). Their actual bytes remain under the private `evidence/pre-rebase` directory. The invalid initial wheel's apparent PASS is superseded by the corrected PEP 639 gate. The d836 artifact bytes and bounded inputs are preserved in `evidence/pre-analysis-filter`, with its compact results in `release-gate-d836-superseded.json`. That d836 executable still had the native private metadata member; it must not be treated as final. `release-gate-rebased.json` and this report describe only the final e21ad artifacts.

The source tar carries native source/build material but the self-updater does not install the native wheel. OCI native packaging/inventory remains #655; the existing image job is downstream of accepted publication. ASTAP and its database are external. DSS2 tiles are excluded. The four-platform workflow is implemented, while these local builds prove only source and Windows x86_64. Linux/macOS envelope reconstruction fails closed until specifically implemented and reviewed. No hosted matrix, Docker runtime build, real hardware, elevated-account lifecycle, deployment or release was run.

Owner decisions stay centralized in `packaging/distribution-policy.json`: Player One (#632), artwork (#637), WCSLIB/LGPL (#638), Astrospheric (#636) and the IAU grant remain pending. Player One preserves the prior artifact-specific selection. An authorized owner change requires the package-data-only sync/check described in `packaging/RELEASE.md`. No owner mode waives unknown payloads, missing notices or native obligations.

The scoped exceptions are the repinned existing binary smoke test, the three fixture-copy lines in the existing licence-artifact test, and one server-pyproject fingerprint in tracked credits. No application/Rust runtime behavior, root Dockerfile, rig deployment or production config was changed. No push, merge, tag, dispatch or publication was performed.

Claude owns integration and copyright stamping after handoff. Each resulting source/header delta requires individual verification and a specific reviewed hash refresh, fresh credits and rebuilt artifact evidence. Do not ignore headers broadly, approve current bytes automatically or reset the immutable anchor. JOB5 waits for Claude's post-JOB4/wave6 feat SHA; JOB6's browser simulator spike follows JOB5.
