# Operator documentation review

Reviewed 2026-10-01. This worker authored the operator content, so this is an author verification record, not an independent content signoff.

- 12 pages authored: nine existing paths retained and three additions.
- 101 baseline heading anchors preserved by current headings or explicit aliases.
- 156 substantive paragraph/procedure claims mapped to repository source lines.
- 205 page/UI-label entries checked against literal ui/src text.
- Dynamic labels are explicit: CAPTURE carries count/exposure/filter; dormant-flow continuation carries a flow name and progress. A new flow uses plain RUN. CONFIRM RUN is composed by runArm for a new flow, with its literal asserted by the cited UI test and the rendering source recorded separately.
- Local relative file links, claim-text consistency, source bounds, label coverage and UTF8 without BOM checked. Faults: 0.
- No screenshots, site values, credentials, mount park coordinates, app edits, package installs, commits or external writes were made by this worker.

## Verification scope

Every claim retains its source-trace classification. Root owns fresh simulator execution and its [durable procedure record](procedure-evidence.md), which separately identifies executed steps and controlled-test limits. This report does not infer autofocus, guiding, cloud hold, auto-resume or mosaic completion merely from controls and implementation. Published-native packaging limitation #630 and ASTAP prerequisites are explicit.

## Reader pass

Procedures identify the alternative UI first, use numbered actions and reserve bold for UI labels. Long classic-interface/API descriptions were replaced with shorter tasks. The prose pass checked unsupported completion claims, vague parity claims, manual-stop parking, bundle-versus-photo downloads, weather veto scope and changing label suffixes. No em or en dash remains in the authored pages.

## Independent setup documentation review

This worker independently read the setup worker's 14 pages: README.md; docs/overview.md, quickstart.md, auth-setup.md and relay-deploy.md; and guide/getting-started.md, install-binary.md, install-docker.md, windows-rig.md, orange-pi-appliance.md, equipment-and-profiles.md, remote-access-and-roles.md, site-and-locations.md and troubleshooting.md. The review compared procedures and material claims with current source and the prior site/hardware evidence. It did not execute an installation, contact hardware or verify a production relay.

Five findings were corrected by that worker and reread: PHD2 is a guiding alternative rather than an autofocus provider; a stacking bundle contains a manifest/build script rather than the captured photos; protocol support is bounded by offered roles/capabilities and platform prerequisites; EAF evidence is inventory-only rather than full functional verification; and the source instructions use Node 24 rather than an unspecified Node 20 minor. The nearby residual EFW/ASCOM sentence received the same protocol-scope correction and was reread. No peer-review finding remains open.

Source spot checks included auth/capabilities.py role mapping, root docker-compose.yml binding and data ownership, relay/config.py and relay Docker/Compose paths, comhost/__main__.py and pyproject.toml COM prerequisites, solve/astap.py discovery overrides, current UI labels/routes, CI Node version and recorded hardware checks. The relay Compose context workaround is identified as #645. No additional procedural blocker was found in binary, Docker, Orange Pi, account, role, site or relay setup.

The setup worker independently read these 12 operator pages and found two qualifications: unsafe/stale monitor recovery checks require safety monitoring enabled, and backend autofocus can own exposure/gain/binning. Both are incorporated and source-cited. This peer review is distinct from the author's checks above.

## Documentation gate

After the final mosaic and stale-STOP corrections, this worker independently ran `python -X utf8 tools/docs/check_docs.py --require-privacy`. It passed: 27 pages, 520 claims and 291 UI labels. The privacy check was required, not skipped. This verifies the checker-covered document properties; it does not turn source-traced claims into runtime observations.

## Remaining review

Root's independent claims/navigation/gate review and final simulator result reconciliation remain separate. The guide links the procedure evidence and explicitly states that controlled cloud-hold/dawn tests are not a natural simulator cloud hold or real unattended-night proof. The stale STOP warning is present without a guessed issue number; its issue citation can be added after Claude supplies it. Any later source or label change requires rerunning the documentation gate. This worker's files are frozen for final review.
