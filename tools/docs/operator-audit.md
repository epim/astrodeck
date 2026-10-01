# Operator documentation audit

Reviewed 2026-10-01 against 6e8dad9d66f66b78d5df7cc12872f49e1f626d6b after the privacy rebase.
Baseline references below mean the file at b79c271f51133cf35b5c1f927ffd7817417f99b0, not its rewritten line numbers.

## Scope and evidence

Rewrote nine existing operator pages and added flows-and-mosaics.md, unattended-nights.md and next-ui.md. Every substantive paragraph or numbered procedure has a claim entry in operator-claims.json. The UI-label manifest records literal source locations, with CAPTURE marked as a dynamic prefix whose suffix supplies count, exposure and filter. No bold prose emphasis needs an exception.

All claims remain source-trace. Source inspection is not a completed simulator run, a physical-device test or a release-package verification. Root owns the private simulator exercises and records their executed scope separately. No real rig, relay, credential, configuration or site value was read for this work. No screenshot was added.

## Material stale claims and corrections

| Baseline location | Finding | Resolution and current evidence |
| --- | --- | --- |
| docs/guide/sky-atlas.md:6,142 | Broad offline sky imagery promise blurred schematic, local cache and operator-downloaded survey data. | State offline schematic support, no distributed DSS2 pack, operator tile fetch separate. OFFLINE record in tools/site/claims-ledger.md; source ui/src/next/hubs/sky/SkyHub.tsx routes survey setup. |
| docs/guide/sky-atlas.md:26,30 | Says planet ephemerides do not exist. | Remove that false refusal and the claimed fixed catalogue limitation. Current solar-system and ephemeris implementations exist under server/astrodeck/catalog/solar_system.py and catalog/ephemeris/; ui/src/next/hubs/sky/sheets/targets.tsx routes results by kind. The revised minimum task does not promise every moving target can run as a fixed deep-sky target. |
| docs/guide/plan-and-sequences.md:3,50,241 | Classic Plan labels and navigation presented without interface scope. | Explicit alternative interface entry, flow-library PLAN EDITOR door and tablet/desktop restriction; keep classic entry links in next-ui.md. |
| docs/guide/plan-and-sequences.md:71 | One generic lifecycle reads as unconditional behaviour. | Review target centring/autofocus, provider prerequisites, automation and preflight instead of promising all stages execute on every rig. |
| docs/guide/capture.md:137-144 | Live-view description can imply every frame is saved. | Make SAVE FITS TO LIBRARY explicit; distinguish preview, running stack and saved FITS. |
| docs/guide/capture.md:246-250 | Saved-light WCS description lacks the separate local solver prerequisite. | Add ASTAP plus compatible database requirement and avoid treating a failed solve as measured pointing. |
| docs/guide/focus.md:116-123; docs/guide/guiding.md:3-8 | Native autofocus/guide presented as shipped and ready by default. | State astrodeck_native is missing from published releases, issue #630; source native.py refusal plus release build and prior packaging audit. |
| docs/guide/guiding.md:6 | Blanket NINA/PHD2 parity and no external process claim. | Replace with provider-specific prerequisites and equal NINA, ASIAIR planning, standalone choices. No ASIAIR official API claim. |
| docs/guide/monitor.md:91-100 | Old pause prose describes the engine as running throughout the in-flight exposure, while current UI handles paused state plus remaining shutter as PAUSING. | Explain observable frame-boundary behaviour without asserting that stale internal state. Current RunProgress and RunControls read the in-flight shutter separately. |
| docs/guide/weather.md:138-145; docs/guide/sessions-multi-night.md:126-129 | Sustained cloud forecast described as auto-resume veto. | Positive forecast rain within an hour can veto; cloud forecast is advice. Missing forecast fails open for that veto. Unsafe/stale connected safety readings are separate. |
| docs/guide/safety-and-automation.md:137 | Says no horizon-profile editor. | Link the existing graphical horizon editor and describe its review requirement and photo privacy. |
| docs/guide/sessions-multi-night.md:178-179 | Says deleting a materialized hard link deletes the original capture. | Editing in place affects shared file content; removing only the export link does not remove the original link. Treat materialized exports as read-only. |
| New flows-and-mosaics.md | Existing guide did not explain the current TARGET modal, local draft, grid, pass rotation, counts or re-frame confirmation. | Document the shared current modal and compile review. Generation is not a completed capture run. |
| New unattended-nights.md | Operator needs a coherent preflight, arm, check and cancel procedure. | Link safety/provider checks to gallery auto-resume actions and current-run recovery stop. No hardware safety certification. |
| New next-ui.md | Root and alternative routes were easy to conflate. | Root stays classic, #/next is an alias, hub routes omit the next prefix; explicit classic routes preserve access. |

## Cross-page disagreements resolved

All operator procedures now identify the alternative UI in the opening paragraph. The route matrix is the shared source for direct links. The classic interface remains available and was not removed by this documentation change.

The site ledger's final DSS2 ruling is retained. No page claims a bundled survey, an offline photographic sky by default, a native engine in published releases, or an official ASIAIR API. Native focus and guide use the same caveat wherever prerequisites matter.

Manual STOP does not park the mount. A configured unsafe teardown, normal completion and a manual stop must not be described as the same shutdown action. Plan, Monitor, Safety and Unattended pages now state this explicitly.

DOWNLOAD BUNDLE gives a manifest and build script, with photos left on the rig. Sessions now distinguishes that from FITS originals downloads. Deleting a session ledger remains separate from deleting saved FITS files.

Forecast clouds do not veto auto-resume. Image-based cloud hold, forecast rain veto and hardware safety are named separately on Weather, Monitor and Unattended pages. No unconditional tracking-during-hold claim remains.

## Deliberate cuts and limits

Removed API route appendices from the operator steps. They were implementation references, not the actions visible in this interface. Removed exact stale classic-layout positions, comparison/parity promises, fixed defaults treated as universal recommendations, timing assurances, unverified all-provider behaviour and repeated long lists of internals. Existing URLs and all baseline heading anchors remain available.

The revised guides cover a minimum accurate operating path. They do not claim exhaustive coverage of video capture, every tuning parameter, all escalation presets or every backend capability. Existing heading aliases for those topics lead to the nearest replacement task, rather than leaving broken external links.

## Application findings

The native release packaging limitation is already #630. Flow-derived targets request autofocus with or without an AUTOFOCUS stage. An early suggestion that deleting/reconnecting that stage might enable a no-native run was corrected before final delivery; the guide instead points to manual capture or an explicitly configured plan. GUIDE presence does control guiding. The route-alias correction and the manual-stop and bundle distinctions were also sent before root's runtime procedures.

One application-copy contradiction was reported to root for Claude and filed as #646: to_plan.py NODE_LOSS for parkclose says no darks are taken after shutdown, but HOLD_HONOURED and plan_extras implement a funded on_shutdown_complete to calib.do day-darks lane. The revised node reference follows the actual implementation and links the issue. No application file was changed.

Root's completed-flow simulator run also reproduced a stale STOP toolbar label. The guide warns the operator to confirm the idle state in Monitor and reload, not press the stale control. This is not only cosmetic: flowRunControls.tsx:290 combines current ownership with a local phase latch for display, while :381-382 chooses the action from current ownership. FlowCanvasToolbar.tsx:139-141 supplies no second-press confirmation for STOP, so the stale label can conceal a start action. The initial phase in flowsSlice.ts:235 is idle. The finding was reported to Claude; no issue number is invented while its citation is pending.

Final auto-resume execution reconciliation: Gallery's MORE menu closes when an action is selected (SessionCard.tsx:118-120). The live arm/disarm procedure reopened it to observe ON, then reopened it to choose ON and verify OFF; the revised unattended steps say so. The optional SESSION > NOW armed-state inspection and STOP AUTO-RESUME recovery control remain source-traced, not exercised by that Gallery procedure. The separate procedure evidence records the actual executed scope.

The full reference covers all 21 declared node types, including legacy SLEW + CENTER and not-yet-executed DUSK FLATS. It describes ignored card settings instead of treating their presence as execution evidence. Unattended guidance now names the 45-minute engine bound, its guarded-operation timing, D-03 pass-based escalation, dawn park/warm and conditional day darks. The current Capture result can save a held unsaved frame; a stale header comment is not used as a source for an unavailable-save claim.

## Current source locations for material corrections

- `ui/src/rootChoice.ts:39`
- `ui/src/next/router.ts:121`
- `ui/src/next/hubs/session/flows/FlowsScreen.tsx:615`
- `server/astrodeck/flows/wizard.py:924`
- `server/astrodeck/flows/wizard.py:932`
- `ui/src/components/flows/flowsSlice.ts:1365`
- `ui/src/components/flows/flowRunControls.tsx:290`
- `ui/src/components/flows/flowRunControls.tsx:382`
- `ui/src/next/hubs/session/flows/canvas/FlowCanvasToolbar.tsx:140`
- `server/astrodeck/focus/native.py:305`
- `ui/src/next/hubs/rig/sheets/guider.tsx:1067`
- `ui/src/next/hubs/session/now/RunControls.tsx:57`
- `ui/src/next/hubs/session/sheets/files.tsx:1341`
- `server/astrodeck/weather.py:685`
- `server/astrodeck/weather.py:749`
- `server/astrodeck/sequence/resume_arm.py:1957`
- `ui/src/next/hubs/sky/sheets/horizon.tsx:440`
- `server/astrodeck/sequence/bundle.py:797`

Claude tracked the completed-flow stale STOP finding as [#647](https://github.com/epim/astrodeck/issues/647). The documented workaround is to verify idle in Monitor and reload, never press the stale control.
