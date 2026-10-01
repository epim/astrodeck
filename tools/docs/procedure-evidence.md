# Documentation procedure evidence

Executed on 2026-10-01 at source revision 6e8dad9d66f66b78d5df7cc12872f49e1f626d6b. The guide audit began at b79c271f; the four-file privacy fix changed simulator tooling, not the application. [Structured observations](procedure-records.json) retain both fresh runs and the initial connection refusal.

Fresh servers used private configuration and capture directories, separate ports, private Python/browser dependencies, process creation/command checks and listener ancestry checks. Only public synthetic Hanle coordinates were entered. No rig, relay or production configuration was contacted. Browser survey/HiPS and external requests were blocked. No tool read mount altitude/azimuth, a complete device-status payload or private server logs during these post-fix procedures. Both owned servers were stopped on console exit, and auto-resume was OFF before shutdown.

## Executed live procedures

| Procedure | Observed result | Boundary |
| --- | --- | --- |
| First light without the native engine | Connected simulator devices; saved and separately activated the synthetic site; took one unsaved one-second exposure. Preview was 1216 by 912 pixels, with 34 stars and CAPTURED / NOT SAVED. | Manual capture, not autofocus or guiding. |
| Generate a Flow without the native engine | M31 deep-sky Flow graph appeared with guiding and HFR watchdog disabled. | Generated only; not run in this environment. |
| One-frame native-engine Flow | After verified simulator connection, M31 completed DONE, 1/1 frames and one recorded file. Exposure and Count were both 1; guiding and HFR watchdog were off. | Uses a locally built optional native wheel and simulated equipment/solver, not the published release or ASTAP. |
| Four-panel mosaic | Matched camera field, selected a 2 by 2 grid with 25 percent overlap and a requested PA of 0, confirmed the re-frame warning, saved and ran. DONE, 4/4 frames and four files. | Previous one-frame run remained on disk; panel counts restarted. Unguided and short-visit advisories were visible; no start override was requested. |
| Arm and disarm auto-resume | Started a longer Flow, stopped it through the two-step Session Now control while focusing, and obtained a DORMANT session with work remaining. Gallery MORE changed AUTO-RESUME OFF to ON, then ON to OFF. Reload confirmed OFF persisted. | No natural dusk restart or overnight run was tested. The action menu closes after each change and must be reopened to inspect the label. |

The first native-run simulator connection was requested but not verified before navigation. The Flow refused with no camera connected. Repeating the documented connection and waiting for RIG CONNECTED and the simulator camera row resolved it. The record preserves this refusal rather than presenting the first request as a successful connection. No cause or application defect is inferred from that attempt.

After each completed Flow, returning to the canvas could leave a stale STOP control. A full page reload restored RUN. Source review found that pressing the stale STOP can start a new run without the usual confirmation; it was never pressed during these procedures. The finding is tracked as [#647](https://github.com/epim/astrodeck/issues/647), and the guide describes the idle-check/reload workaround. Relevant source: ui/src/components/flows/flowRunControls.tsx:290 and :365, and FlowCanvasToolbar.tsx:141.

## Build and installation limits

A private editable base-server installation and private browser were used on Windows with Python 3.12. The UI was built with Vite using the existing shared dependency tree read-only. This was not a clean npm install, full source-install checklist, release executable test or cross-platform installation test.

The optional native engine was built offline with the locked Cargo graph and installed only into the private environment. Wheel SHA-256: 1c331608e8c1fb1892b18bc9277859e886febc5d6ed5405c9ea4baa1a6a013f9. This separately built wheel does not resolve or validate published native-engine packaging issue #630.

## Controlled engine checks

52 existing tests passed in 26.83 seconds: the static no-mount-coordinate tool guard; test_the_cloud_hold_does_not_recurse.py; test_dawn_park.py; and test_engine_timeouts_escalation.py. These use isolated configuration, simulated or fake devices, injected clocks/verdicts/faults and selected mocked guard seams. They establish behavior under those fixtures, not a natural cloud event or an overnight hardware run.

The missing-monitor sky fallback deliberately refuses simulated sky frames. Cloud hold and its bound are source-traced and exercised by the controlled engine tests; no natural live-simulator cloud event is claimed.

## Review limits

The two documentation writers independently reviewed each other's pages: [setup review](setup-review.md) and [operator review](operator-review.md). The parent agent separately executed these simulator procedures and reconciled their outcomes with the guide.

An additional fresh-context reviewer could not be spawned because the collaboration tool's total thread limit was reached, even after both writers finished. Claude agreed to provide that fresh adversarial review and literal simulator procedure pass before merge. Peer review and the parent's execution do not substitute for that outstanding independent review.
