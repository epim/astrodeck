# Setup review

The final setup-only gate passes all fourteen assigned pages with 360 claim records and 86 UI label records. All 84 added baseline heading aliases were checked; no original heading anchor is lost. Private-value scans cover both pages and setup evidence, and all files are UTF-8 without BOM. No source installs, hardware connections or network deployment were performed by this author. Root is independently executing fresh owned simulator procedures and owns the combined gate.

Checked against source: source UI build and configuration/capture paths; loopback/authentication bootstrap; exact classic versus alternative labels; site save/activation; profile startup behavior; native camera/AM5 and provider limits; four roles; Google field names/password limits; relay trust and local-only writes; Orange Pi provisioning boundaries; durable night logs.

Open limits: release availability is not inferred from the workflow matrix; no claim of all-platform or unattended hardware verification. The relay build-context defect is documented and reported. Native release omission #630 and next-dawn presentation #640 remain. Screenshot evidence belongs to root's disposable simulator procedure, not this source review.

All original headings were checked for anchor preservation. README voice and headings remain with factual corrections. Changed prose is UTF-8 without BOM; final privacy and link/style checks are recorded in the handoff. No commit or publication was performed.
## Independent setup feedback resolved

The operator reviewer found five factual qualifications, now applied with matching ledger updates: Node 24 for the source UI build, PHD2 as a guiding provider, manifest/build-script bundle contents, bounded Alpaca and COM capabilities, and EAF inventory evidence distinguished from operation checks. The adjacent EFW claim was narrowed too. README now says an armed session can resume after restart checks instead of implying every session restarts automatically. The relay workaround links issue #645.

## Independent operator accuracy review

Read all twelve operator pages and checked the material behavior against UI handlers and server implementation. This is a source review, not an executed hardware procedure. No application request, capture or equipment command was made during this review.

| Area | Evidence inspected | Result |
| --- | --- | --- |
| Interface routes and plan entry | ui/src/next/router.ts:121; ui/src/next/hubs/session/flows/FlowsScreen.tsx:615 | Classic root, alternative alias and desktop plan entry are distinguished. |
| Capture and save | ui/src/next/hubs/rig/capture/CaptureControls.tsx:94; CaptureScreen.tsx:591 | Capture count, repeated frames, live stack and save outcomes are separate. Root's later save observation is incorporated by the operator author. |
| Focus and guiding | ui/src/next/hubs/rig/sheets/focuser.tsx:1022,1325; guider.tsx:415,722 | Provider prerequisite is explicit. Requested one qualification: backend autofocus can ignore AstroDeck exposure/gain/binning and use its own settings. |
| Plan controls | ui/src/next/hubs/session/plan/PlanPreflight.tsx:208; PlanRunHeader.tsx:243,264 | Double-press start/stop, pending pause and rerun versus recovery distinctions match handlers. |
| Weather and recovery safety | server/astrodeck/weather.py:685; sequence/resume_arm.py:1951 | Rain veto and cloud advice are separate. Requested one qualification in unattended-nights: connected-monitor recovery checks run when safety monitoring is enabled. |
| Sessions and exports | ui/src/next/hubs/session/gallery/cardActions.ts:31,80; server/astrodeck/sequence/bundle.py:797 | Dormant action requirements, ledger deletion versus FITS retention and hardlink consequences match source. |
| Sky framing and offline scope | ui/src/next/hubs/sky/frame/FrameTools.tsx:158; framing and target sheets; tools/site/claims-ledger.md:40 | Camera field and object fit are distinct; schematic offline sky does not promise survey coverage. |
| Flows and unattended limits | server/astrodeck/flows/to_plan.py:407,1197; sequence/group_rules.py:119,1301; sequence/engine.py cloud-hold path | Default target autofocus, ignored node settings, 3/6 held-pass thresholds and the two-pass same-reason path are described without a timing or hardware guarantee. |

Both requested qualifications were sent to the operator author and root. No remaining procedural blocker was found in the reviewed source paths. Independent execution evidence belongs to root's procedure records.

## Checker and workflow verification

The behavioral suite passes 44 tests. All 25 named source mutants fail at their intended assertions; each source backup is restored byte for byte and the suite passes after restoration. See mutation-evidence.md. Lifecycle fixtures use temporary directories and mock sockets, launch/stop calls, ownership and privacy inputs. An initial under-mocked fresh-path fixture reached a loopback bind during development; it was corrected before the recorded successful run. No server was launched by that failed attempt.

The checker improvements reject fenced example anchors, HTML-encoded and URL-encoded private values, and private values in JSON evidence including JSON escapes. Diagnostics use file/category labels rather than rejected document content. This is structural provenance validation: it checks that cited files/lines and UI literals exist, not that every prose claim follows logically from its citation. Manual review remains necessary.

The separate docs workflow uses read-only contents permission, checkout without persisted credentials, stdlib tests and mutants, and the checker. Privacy is required when the external secret is available; otherwise it explicitly reports the skip. No GitHub execution or cross-platform runtime certification is claimed. Local required-privacy gate passed 27 pages, 514 claims and 281 UI-label records after setup corrections; counts may rise with the operator's final review fixes.
