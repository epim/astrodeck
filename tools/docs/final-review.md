# Documentation handoff review

Reviewed 2026-10-01 by the parent agent. The scope is 27 user-facing pages, the new tools/docs evidence/checking directory and one new documentation workflow. Application code, the site, main CI and the privacy workflow are unchanged.

## Results

- The final documentation gate passed with required external privacy inputs: 27 pages, 520 claims and 291 UI label records. Claims remain source-traced unless the separate procedure record identifies an executed observation.
- All 17 original guide URLs and their baseline heading anchors remain available. Five new guides cover Flows/mosaics, unattended nights, Windows rigs, Orange Pi appliances and alternative-interface routes.
- Parent rerun: 44 documentation/checker lifecycle tests passed in 0.252 seconds. The 25 named mutants were each killed at the intended assertion, followed by exact-byte restoration and a green suite; see [mutation evidence](mutation-evidence.md).
- 52 existing controlled cloud-hold, dawn, escalation and no-mount-coordinate tests passed with one dependency deprecation warning. The parent reran the static no-mount-coordinate guard after the final tool edits: one test passed in 1.86 seconds.
- [Actual simulator observations](procedure-evidence.md) include first light, one-frame Flow, four-frame mosaic and persisted auto-resume OFF after an ON/OFF cycle. Both owned servers were stopped. Cloud and dawn evidence is controlled test evidence, not a natural overnight run.

The parent reviewed navigation, installation bounds, the new platform/interface pages, Flow/mosaic prerequisites, stale STOP handling, unattended procedures, evidence reconciliation and checker/mutation/workflow code. This is distinct from the writers' [cross-review](setup-review.md) and [author/peer review](operator-review.md). An observed menu closure was reflected in the auto-resume instructions so a reader can find the state after each action.

## Findings handed to Claude

The guide documents native release omission #630, relay Compose build context #645 and the stale shutdown calibration warning #646. The privacy tooling fix #644 is already in the base revision. A separate stale Flow STOP finding was reported: after a completed run the label can remain STOP, while its action can start another run without normal confirmation. The guide says to verify idle state and reload, and not to press the stale control. Claude filed it as [#647](https://github.com/epim/astrodeck/issues/647), which the guide now cites.

No application fix, release certification or hardware safety claim is made by this documentation batch. Installation commands and platform/role descriptions were source-traced; the local procedure used a private editable server, a Vite build with existing dependencies and a separately built native wheel. It does not certify the full clean-install procedure or published packages.

## Outstanding independent acceptance

A fresh-context review could not be spawned because the collaboration tool's total thread limit was reached. Claude agreed to perform that independent adversarial review and literal simulator procedure pass before merge. These local checks and peer reviews do not claim to satisfy it. There was no push, merge, deployment or other GitHub write from this worktree.
