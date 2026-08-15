# Deliverables ledger: #234, #241, #231 + #240

Opened 2026-08-14. One line per deliverable, ticked only when the evidence
exists. A ticked line with no evidence is the defect class this file is here to
avoid, so each one names what proves it.

## Ordering constraint (read before touching a vocabulary)

**Do NOT widen `TriggerKind`/`ActionKind` in `sequence/models.py` before the
engine can execute the new member.** `to_plan.LEGAL_TRIGGERS` reads the enum
directly, so the moment `on_night_end` becomes legal, `to_sequence_plan` stops
reporting it as unmapped. An honest "this rule will not run" becomes silence,
which is worse than the gap. Engine first, enum second, in the same commit.

## A. #234 — engine trigger evaluation — DONE

The per-frame path was already wired. Two things were not.

- [x] A1. Probe over all 7 examples. M16 compiles 6 rules, 3 survive:
      `on_clouds_in -> hold_for_clear`, `on_clouds_in -> notify`,
      `on_unsafe -> abort`. So the cloud-dodge is NOT inert - the title of
      #234 stopped being true when the sky triggers landed. Of the three that
      do not survive, one (`holdresume.resume`) was already classified
      redundant and two were misreported.
- [x] A2. The two `calib` rules were reported at DANGER as "this rule will not
      run". False: `calibration_queue.quota` reaches the plan as
      `cloud_hold_darks` (20 on M16), and the hold shoots those darks itself.
      New `HOLD_HONOURED` in `to_plan.py`, keyed by (trigger, type, port) so
      the campaign's `on_shutdown_complete -> calib` stays an honest loss, and
      guarded on `hold_darks > 0` so a quota of 0 does not claim honour.
- [x] A3. It was a real gap. `target_complete` is True at exactly ONE boundary,
      and that boundary reported the sky as unknown, so a compound rule reading
      "target finished AND sky clear" could never fire on any night. Fixed at
      `engine.py`; the readings now come from the same tri-state sources the
      per-frame path uses.
- [x] A4. Both sabotage-verified. `cloudy=None` at the boundary fails the new
      engine test; emptying `HOLD_HONOURED` fails two to_plan tests.

Two existing tests had to change because they enforced the false sentence:
`test_the_m16_example_holds_for_cloud_and_aborts_on_unsafe` demanded the calib
wires be reported as losses, and `test_nothing_else_blocks_however_bad_it_is`
counted on M16 having more than one danger. Both now assert the accurate thing.

Full suite 5920 passed, 24 skipped.

## B. #241 — engine campaign execution

- [ ] B1. Triggers `on_night_end` / `on_altitude_floor` / `on_shutdown_complete`
      executable in the engine, THEN legal in the enum.
- [ ] B2. Actions `parkclose` / `pool_advance` — same order.
- [ ] B3. Capture cursor persisted across restarts.
- [ ] B4. `to_plan` stops reporting `campaign` as danger only when the engine
      really runs one. Until then the note stays.
- [ ] B5. `parkclose` is gated behind dome integration, itself unmapped. If it
      stays gated, say so out loud rather than shipping a half action.

## C. #231 — filter slot 7 is flagged blackout but never blanked

- [ ] C1. Reproduce: where does a dark/bias select its slot, and what makes
      slot 7 opaque in name only?
- [ ] C2. Fix so a blackout slot is actually dark for darks and bias.
- [ ] C3. Sabotage-verify.

## D. #240 — 18 frames shot through the blackout slot say IMAGETYP=Light

- [ ] D1. Find the 18 frames on the rig, confirm the header.
- [ ] D2. Decide relabel vs quarantine. Never rewrite science headers silently.
- [ ] D3. Stop the next 18: whatever wrote Light for a blackout slot.

## Standing constraints

- Never `git add -A`; commit with explicit paths.
- The real site latitude/longitude and its nickname must never appear in code,
  tests or docs. A pre-commit scan enforces this and it caught THIS file on its
  first commit, because the line naming the rule quoted the values it forbids.
  Invented coordinates for tests: 40.0 / -105.0.
- Subagents never touch the rig. Only the main agent talks to 192.168.216.220.
- No em-dashes in shipped strings.
