# Mosaic group: apply one panel's steps to all panels — design

**Date:** 2026-07-14 · **Status:** approved (user picked "Apply-to-all button" + "Plan-panel only" from mockups)

## Problem

A mosaic sends N panels to the Plan, each seeded with the single default step
(`ATLAS_DEFAULT_STEP`, AtlasView.tsx `panelsToTargets`). Setting a real filter
list (e.g. Ha/OIII/SII × 20) means hand-editing every panel identically.

## Design (v1)

Pure client-side convenience — no server or data-model changes. A mosaic group
is already just N `Target`s sharing `mosaic_group`; steps stay per-target.

- In the Plan panel (`ui/src/views/SequenceView.tsx`), each target rendered
  inside a mosaic group block gets an **"apply to all panels"** action in its
  header row, shown only when its group has more than one member.
- Clicking copies **that target's** `steps` to every target with the same
  `mosaic_group` (deep-cloned per member — no shared step references), via the
  existing single-writer `setPlan`. Group totals recompute automatically.
- Overwrite safety follows the delete pattern: no confirm dialog, an **undo
  toast** restores all affected targets' previous steps (mirror
  `deleteWithUndo`, SequenceView.tsx:126-131).
- Panels may diverge again afterwards by normal per-panel editing — the action
  is a one-time copy, not a live link.

The mutation is a pure helper, `applyStepsToGroup(targets, group, sourceSteps)`
in a new `ui/src/lib/planGroups.ts`, with a self-executing tsx test (project
convention; no vitest).

## Explicitly out of scope (YAGNI, user-confirmed)

- Group-level live-linked step editor (linked/unlinked state).
- Choosing the filter list in the Atlas mosaic cluster before Send — the
  Atlas stays geometry-only; steps are edited once in the Plan group.
- Server-side awareness of group steps.
