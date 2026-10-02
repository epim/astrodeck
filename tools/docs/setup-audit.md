# Setup documentation audit

Baseline: `b79c271f51133cf35b5c1f927ffd7817417f99b0`, 2026-10-01. Scope is the fourteen assigned setup/reference pages and the setup evidence files. Application, site, deployment configuration and internal design documents were read only. No hardware, private observing site, relay deployment, dependency install or executable release was used by this author.

## Findings and changes

| Page | Baseline problem | Disposition |
|---|---|---|
| getting-started | Claimed a source checkout already contained a built UI; classic Simulator label and UI layout were stale; broad simulator promises hid missing native engine. | Explicit UI build, classic/alternative entry distinction, alternative first-light procedure, native/ASTAP caveats. |
| install-binary | Phone URL followed a local-only launch; checksum described as certainty; one-time extraction/later fast starts overpromised; update paths conflated. | Separate local startup and authenticated network deployment, checksum scope, ordinary file replacement, platform and packaging limits. |
| install-docker | Network backend examples implied ASIAIR parity; lengthy migration recipe obscured first-run account bootstrap. | Task sequence retained, authentication and storage explicit, native/USB/rootless limits, migration data-preservation note. |
| equipment-and-profiles | Stale Simulator button and fixed connection claims; task engine versus device driver confused. | Current classic labels and profile lifecycle, exact admin/direct-access boundaries, driver versus recorded-check table. |
| site-and-locations | Only classic described; default-site claims used absolutes; saved preset activation differed across interfaces. | Both interfaces, save versus activate distinction, hemisphere fields, privacy capabilities. |
| remote-access-and-roles | Heading advertised open LAN; only three roles; share links and remote writes overstated. | Loopback bootstrap, four-role table including syncer, trusted-relay boundary and local-only operations. |
| troubleshooting | Claimed no disk log and no filtering; broad restart/recovery suggestions hid run ownership. | Durable night logs and query filters, preserve state, inspect refusal, provider and dawn-display limitations. |
| quickstart | Duplicated stale install/UI/backend walkthrough; source capture location ambiguous. | Stable entry links to current tasks, source capture directory is repository-root `captures`. |
| overview | Architecture essay mixed aspirational parity, installation assumptions and unsupported guarantees. | Concise current boundaries with direct setup links and Apache root/native-component distinction. |
| auth-setup | Open-LAN first run; old password rules and role count; confusing bootstrap/recovery. | CLI account bootstrap, current 12-character/72-byte limits, loopback test caveat, optional Google configuration. |
| relay-deploy | Recipe assumed a clean proxy build context and mixed generic secret mounts with Compose-managed secret behavior. | Current setup sequence, exact origin/port, Compose secret handoff, build-context override and deployment limitations. |
| README | Old version badge, stale Simulator label, universal ASIAIR matrix, whole-night definition of Verified, bundled survey-pack claim, absent native caveat. | Factual bounds and guide links; pitch and section structure preserved. Root Apache-2.0 retained; native terms separately acknowledged. |
| orange-pi-appliance (new) | No user setup page separating portal provisioning from application installation. | Commissioned-appliance use, own-board paths, setup credential/account separation, recorded hardware limits and Wi-Fi recovery. |
| windows-rig (new) | No focused Windows connection/install path. | Standard-user launch, backend ownership, native cameras/AM5 limits, COM-host link, profile reconnect. |

## Cross-site consistency

Setup copy agrees with the published-site source ledger: standalone, NINA and ASIAIR-adjacent planning are equal routes with different capabilities; no tested ASIAIR takeover claim. Native engine omission remains #630. ASTAP is separate. Offline Atlas uses schematic sky and personal downloads; no distributed DSS2 pack or screenshot. Native support is not whole-night validation. Root project licence remains Apache-2.0; the initially suspected Docker licence mismatch was withdrawn after reading LICENSE directly.

## Application/deployment findings sent to parent

- Relay Compose build context mismatch: `deploy/reverse-proxy/docker-compose.relay.yml` resolves context to the repository root, while `relay/Dockerfile` copies `requirements.txt` and `relay/` as if built from `relay`. No root requirements file exists. The parent confirmed and sent this to Claude for issue tracking. Docs give a local context override; no packaging code changed and no Docker build was run.
- Existing #630 native packaging and #640 next-dawn presentation defects remain caveats, not claimed fixes.

## Evidence and cuts

`setup-claims.json` records every drafted prose/list/table/code block and retained README factual paragraph with source lines. `verified_by` remains `source-trace`; supplemental citations connect multi-part claims to code and the prior site/hardware evidence. `setup-ui-labels.json` records exact bold UI text in source. CAPTURE and Connect Rig are explicitly fixed prefixes of dynamic labels. SITES is visible text inside an accessible name that also includes a subtitle/count; automation must match the full name or a prefix.

Long internal route inventories, duplicated architecture descriptions and stale exhaustive matrices were cut from task pages. Existing page paths are unchanged. Original heading anchors are retained as explicit aliases beside the closest current section. Hardware and Docker/Orange Pi procedures remain traced, not newly executed.
