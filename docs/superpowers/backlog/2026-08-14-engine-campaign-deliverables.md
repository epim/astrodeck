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

**The premise was wrong, and finding that out was the work.** A campaign was
reported at DANGER as something "the engine cannot run yet". Three of that
note's four clauses were false and had been since the multi-night session
machinery landed. What a campaign flow actually does today:

| the note said | what runs |
|---|---|
| images ONE night and stops at dawn | ends at its stop boundary, goes dormant |
| the capture cursor is not persisted | `_done` seeds from `Session.done_map()` |
| no target is marked done | finished targets log "already complete - skipping" |
| will not re-arm at the next dusk | `engine.start` arms unconditionally; `ResumeArm.tick` restarts it |

- [x] B3. Cursor persistence: already delivered by `Session`. Proven by
      `test_campaign_across_nights.py` - member A finishes night one, is skipped
      on night two, B gets the night, session goes `complete` and stops arming.
- [x] B4. The note now says what runs and names the one thing that does not:
      `until: nights_30` counts no nights. Bound by two tests.
- [x] B6 (new). The CAMPAIGN tab told operators "Dawn parks + closes; the cooler
      stays cold for day darks" in three places. The dome is not driven and
      `plan_extras` sets `warm_cooler_when_done=True`, so two thirds of that
      sentence was false. Now one `_DAWN` constant, bound to the PLAN by test so
      it cannot go stale.
- [ ] B1. Triggers `on_night_end` / `on_altitude_floor` / `on_shutdown_complete`
      executable in the engine, THEN legal in the enum. NOT DONE, deliberately:
      each still reports "will not run", which is accurate.
- [ ] B2. Actions `parkclose` / `pool_advance` - same order, same reason.
      `pool_advance` may not be needed at all now that the skip-if-complete path
      is the advance; decide before building one.
- [ ] B5. `parkclose` stays gated behind dome integration. The design also wants
      the cooler to stay cold for day darks, which is a change to what the RUN
      does (`plan_extras`), not to what the copy claims. Both open.

## C + D. #231 and #240 are ONE event chain

They were filed as two problems and they are two ends of one night.

**What the rig says.** `filter_names.json` on astrotown: slot 7 named "Dark",
`opaque: true`. The slot is flagged blanked; physically it is an open carrier.

**What the night log says** (2026-08-12.jsonl, the durable record):

```
04:05 cloud hold: building 16 darks at 180s g125 one at a time (4 of 20 banked)
04:05 saved Dark_cloud-hold darks 180s g125_Dark_...0007.fits     <- wheel -> slot 7
04:05 saved Light_NGC 6946_Dark_...0517.fits                      <- the PROBE, still slot 7
04:09 plate solve: filter 'Dark' -> 'L'
04:15 saved Light_NGC 6946_Dark_...0519.fits                      <- and 17 more, all slot 7
```

**CORRECTED TWICE. The original account was right; the first correction was
wrong.** Keeping the whole path, because the mistake is more instructive than
the answer.

*Claim 1 (original):* the 18 subs went through the blackout slot, 54 minutes
lost. *Claim 2 (the "correction"):* no, they hold the real star field - top-N
brightest pixels shared 72.8% with a genuine Ha sub and 4.2% with a dark.
*Claim 3 (measured properly):* claim 1 was right.

**Why claim 2 was wrong.** The darks in that comparison came from five hours
earlier at a COLDER sensor (median 240 against 248). What changed was the
hot-pixel population, not the signal. Against darks from the SAME hour, every
frame on this sensor shares about 72% with every other one:

|           | dark_0405 | dark_0409 | sub_0415 | S_before | Ha_ref |
|-----------|-----------|-----------|----------|----------|--------|
| dark_0405 | 100       | 72.3      | 72.7     | 71.2     | 72.5   |
| sub_0415  | 72.7      | 72.5      | 100      | 71.3     | 72.8   |
| S_before  | 71.2      | 71.2      | 71.3     | 100      | 71.1   |

Dark against dark is 72.3%. The test has NO discriminating power on this
sensor; the 4.2% was a temperature artifact and nothing else.

**What settles it: the median, controlled for temperature.**

    hold darks through slot 7   n=2    median 247   15.8 C
    the eighteen subs           n=18   median 247   15.9 C
    real S, same step, 03:5x    n=2    median 263   16.0 C
    real Ha                     n=6    median 259   17.5 C

All eighteen sit exactly on the dark floor. A real frame of the same target,
through the same step's filter, twenty minutes earlier at the same temperature,
sits 16 ADU above it. **The subs are black, and the FILTER=Dark header was
honest: the wheel really was on slot 7.**

The night log names the interrupted step - "applied filter offset -20 for S" at
03:49, and no filter change after. The run was on S, the hold parked the wheel
on slot 7 at 04:01, and nothing brought it back.

**#231 stays disproven, independently.** The slot BLANKS - which is WHY the
subs are black - and the on-sky sweep confirms it (slot 7 median 248 against L
at 439). The four "not a dark: stars" verdicts were still false positives.

**The lesson, three attempts running:** every wrong answer came from a
statistic computed without controlling the variable that moved. Use the median,
against a control taken minutes apart at the same temperature.

Three fixes, all still right, two for revised reasons:

1. **The hold leaves the wheel parked** (`_restore_beam`). This IS what
   happened. 54 minutes of black frames, and the cloud probe went through the
   same blanked slot, so the hold released on a frame with no sky in it.
2. **`judge_dark`'s star test does not scale** (`source_floor`). Independent of
   all the above and untouched by the correction.
3. **`SnowflakeWheel.get_position` answered from a frozen banner.** LATENT, not
   diagnosed. It was justified by the 18 subs and that evidence is withdrawn.
   The staleness is real and `_apply_filter`'s early return really does turn it
   into a cancelled move, so the guard stays - but it has never been caught
   doing it, and the docstrings now say so.

- [x] C1/D1. Root-caused from the night log, not guessed. 28 frames on disk have
      `FILTER=Dark` with `IMAGETYP` not DARK/BIAS: 20 from the NGC 6946 night
      (18 x 180 s + 2 probes) and 8 test artifacts from 2026-08-02.
- [x] D3. `SequenceEngine._restore_beam`, called unconditionally after every
      `_hold_darks` (so the failure path is covered too) and again after
      `_setup_target` on release. Sabotage-verified: 3 of 5 tests fail without it.
- [x] C2. The inverse detector: `Hub._blackout_light_cards` cards any LIGHT or
      FLAT exposed with a blackout slot in the beam (`BEAMOK=False` + `BEAMWHY`)
      and logs at error. The dark check asked "is this dark actually dark";
      nothing asked "is this light actually going through glass".
- [x] C3. Slot 7 needs NOTHING. It blanks. The task's premise was wrong and the
      dark library from it is sound - what was unsound was the check condemning
      four of its frames.
- [x] D2. NOTHING TO RELABEL. The header is true - the wheel was on slot 7 and
      the frames are black. They are junk with an honest label, not data with a
      false one. What is left is whether to trash them, and whether to un-credit
      the 18 from the step's quota in the session ledger. The ledger credits them to the step's quota
      either way.

## E. Two defects this investigation turned up on its own

- [x] E1. `judge_dark` condemned genuinely black frames. `SOURCE_MIN` is a
      count per megapixel measured on 1024x1024 fixtures and was applied
      unscaled to 26 MP. `source_floor` scales it and returns None - abstain,
      and say so in the operator sentence - past the detector's own cap.
- [x] E2. `SnowflakeWheel.get_position` answered from a banner that never
      expires, so a stopped reader named its last slot forever. Same class as
      #208 and #213. It now refuses on a stale banner and mid-move.
- [ ] E3. NOT FIXED, needs its own task: `detect_stars` returns 19 sources on a
      real 180 s Ha sub of NGC 6946 and 36 on a black frame. It is nearly blind
      at 26 MP, and the cloud detector, the preview star count and the HFR
      readout all sit on it.

## Standing constraints

- Never `git add -A`; commit with explicit paths.
- The real site latitude/longitude and its nickname must never appear in code,
  tests or docs. A pre-commit scan enforces this and it caught THIS file on its
  first commit, because the line naming the rule quoted the values it forbids.
  Invented coordinates for tests: 40.0 / -105.0.
- Subagents never touch the rig. Only the main agent talks to 192.168.216.220.
- No em-dashes in shipped strings.
