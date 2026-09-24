# AstroFlows mosaics: the TARGET block and the panel loop

- Date: 2026-09-23
- Status: design, ready to plan, revised once against the completeness critic (Revision 1, at the end). No code has changed.
- Scope: AstroFlows (server `server/astrodeck/flows/`, the sequence engine, both flows UIs), plus the framing and rotation seams a mosaic touches.

## Owner's brief (verbatim)

> the best way to implement mosaics in astroflows. I think this means we need the concept of a target block that replaces the slew and solve. Inside of this target modal we should have a screen that shows us a sky survey and allows us to set position, rotation, mosaic alignment, etc. This should then be able to feed into a filter cycle block. The two should feed in a circle until the designated quantity of subs have been captured for all mosaic panels before moving on to whatever steps are next. Keep an eye out for any missing features that should be implemented via ultracode or when it would be most appropriate to file an issue for those to follow up later. Be bold.

## How this spec was made

Three designs competed: engine-first (A), operator-first (B) and campaign-first (C). Three judges scored them on safety and correctness, operator experience, and implementability. This spec starts from A, which won on two of the three lenses. It adds every item the judges marked "must keep" from B and C, and it fixes every fatal flaw the judges found. Appendix B lists each fix.

Every number here was computed with the repo's own `framing.compute_mosaic` and `framing.project` (`server/astrodeck/catalog/framing.py:100-231`). Appendix A gives the formulas, so each number can be recomputed.

Site privacy: nothing in this document names the site. Per-panel altitude, setting times, hour angles and meridian times are site-derived, and they are treated that way throughout (section 6.9).

---

## 0. The design on one page

A mosaic is **one TARGET block**. The existing TARGET node grows into it. There is no new node type. The block absorbs SLEW + CENTER, whose settings now reach the run. It stores a framing spec as flat params: centre, angle mode and PA, rows x cols, overlap, a snapshot of the camera field, and skipped panels. It also stores a visiting policy.

A modal opens from the block. It shows the sky survey with the grid laid over it. There the operator sets position, angle, grid and overlap, and toggles panels.

The block feeds a FILTER CYCLE. The circle the owner asked for is **drawn** as a dashed amber event wire from the "pass done" output of the **last stage of the panel lane** (usually the FILTER CYCLE) back to TARGET "next panel". The panel lane is the unbroken chain of AUTOFOCUS, GUIDE, CAPTURE LOOP and FILTER CYCLE nodes after the TARGET (section 1.5). The editor adds that wire automatically when a block becomes a mosaic. The wire is the single source of truth for "rotate panels every pass". Without it, each panel runs to completion. The last stage's "all done" fires when every panel has every stage's quota of accepted subs. The cursor then moves on to whatever that output is wired to.

At compile time `to_plan` expands the block into N panel Targets through `compute_mosaic`, the same function the modal previews. Panel ids are deterministic and keyed to the geometry. The plan gains one `TargetGroup`.

The engine rotates between panels by list surgery inside the existing scheduler loop, with no second scheduler. Each visit runs through the unchanged per-frame gate stack. The rules that govern the rotation:

- Blocked panels read "waiting" where gating is decided.
- While nothing can be shot, the mount is park-held on the idle clock (how long nothing has been shootable), never on the length of one wait interval.
- The mount changes pier side at most once per group per night.
- Failures set a panel aside for tonight, never mark it done. A panel that rejects while the others accept is set aside after three such visits.
- A pass that takes no exposures ends the rotation.
- A later target shot while the whole mosaic waits gets visits that end when the next panel is due, so it cannot take the rest of the night.

Pressing Run on night two continues the flow's own dormant session, so there is one ledger per flow and `Session.owed()` stays the only definition of finished.

### Decisions

| # | Decision | Why, in one line |
|---|---|---|
| D1 | Extend the TARGET node (type id stays `target`). Do not add a node type. | Saved flows keep loading. One node expands to up to 100 panels, far inside the 400-node cap (`flows/models.py:101-102`). The palette union is unchanged. |
| D2 | The circle is a drawn **structural event wire**, `<last stage>.pass -> target.next`, where the last stage is the tail of the panel lane (1.5). It is never a flow back-edge and never a new port kind. | Flow back-edges are silently dropped today (`compile.py:72-78`). A third port kind breaks the kind-to-kind rule and `flow_order` (`flows/models.py:143`, `compile.py:77-78`). Backward event wires are already legal (`design_handoff_astrodeck_flows/README.md:122`). |
| D3 | The default is **pass-major**: rotate panels every pass, one full filter pass per visit, least complete first. Panel-first is what you get by deleting the wire. Filter-major is rejected. | It extends FILTER CYCLE's own reason for existing ("a night cut short still stacks") from channels to panels. A local, untracked mobile handoff draft describes the same rotation (`ASTRODECK-UPDATE/design_handoff_astrodeck_mobile/README.md:174`); it is not a committed contract, and it numbers panels row-major at 15% overlap, which this spec does not follow. |
| D4 | Panels are derived, never stored. Compile emits one entry per block and `to_plan` expands it. | The graph is the source of truth (`flows/models.py:3-8`). The server is canonical for slew targets (`framing.py:3-8`). |
| D5 | Identity is deterministic and keyed to the geometry (uuid5). Skipping a panel is not part of the identity. Re-framing restarts the counts, and the modal says so before DONE. | Counts stay honest after a re-frame, and continuation works across nights. This fixes C's flaw of crediting old frames to moved panels. |
| D6 | One ledger per flow. Run CONTINUES the flow's dormant session through the existing id-safe plan edit plus `engine.start(session=)`. | No second ledger and no session schema change. `owed()` stays single (`session.py:143-152`). |
| D7 | Engine: `SequencePlan.groups`. Rotation is a requeue inside `_run_scheduled`. Visits are bounded by passes in `_run_steps`. The `_run_step` gate stack is untouched. | There is one scheduler to keep correct. Per-panel gating, waits, park-hold, skips and jumps come for free. |
| D8 | New blocks count **accepted** subs per panel per filter. Flows saved before this change keep counting attempts, because missing-key defaults reproduce old behaviour. | Rejected frames must not fill a mosaic. Old flows keep their meaning without a data rewrite. |
| D9 | Reachability is decided in the scheduler's selection, so a blocked panel is "waiting" and the existing do-while wait path runs. A verdict that waiting cannot change (no site, a pier change with flips off) is a refusal, never a wait. The mount is park-held on the idle clock. | This removes the hot loop the safety judge found in A and C, and the tracking-while-idle hole the critic found (5.1). |
| D10 | Meridian: pier hysteresis on the hour angle with the **plan's** flip lead as the margin (never the lead learned by #127), verified by reading the pier side after every hop. A visit ends before its panel's flip point. The flip-owed memory is kept per target. | At most one pier change per group per night, the #136 invariant stays armed, and the pre-flip idle is priced and bounded (5.7). |
| D11 | Angle has three explicit modes: any, rotate to, camera fixed. A mosaic requires an angle. Every hop compares the angle its centring solve measured with the planned angle, whatever the rotator state. | A dropped rotator skips rotation silently today (`hub.py:6455-6456`), and a fixed camera at the wrong angle cannot tile. |
| D12 | Failures set a panel aside for tonight. Deferrals are bounded. The group anti-spin guard counts exposures, not accepted frames. | "Set aside" is not "done", and a clouded pass must not end a mosaic. |
| D13 | One shared modal module for both UIs, opened from the per-type slot. DONE is one atomic write, made after the server has answered with panel centres. | This avoids a fork between the classic and #/next UIs, and prevents a half-applied framing. |
| D14 | Per-panel PA correction for meridian convergence waits for #145. Until then a doctor rule states how much overlap convergence costs. | The sign of the correction cannot be validated on the simulator (`solve/simsolver.py:97`). |
| D15 | "What comes next" is whatever the tail's "all done" output is wired to. By default ("Shoot what comes next, then come back"), while every live panel is waiting, the scheduler may shoot a later target in visits that end when the next panel is due; once the group is set aside tonight, a later target runs its normal course. "Wait for the mosaic" is an explicit choice. The default is an owner decision. | This honours the owner's "before moving on" without silently idling a clear sky, and without handing the night away at every routine wait (1.6). |

### Earlier decisions this reverses (named, not silently overturned)

1. `docs/superpowers/specs/2026-07-14-mosaic-apply-steps-design.md:32-37` kept the Atlas geometry-only, with no live group step editor and no server awareness of group steps. A mosaic block that feeds one FILTER CYCLE is exactly a live group step list, and the server now knows about groups.
2. `docs/superpowers/specs/2026-07-13-plan-schedule-design.md:98-106` declined a rows/cols round trip. The block now stores rows/cols and reopens the framing it was made from.
3. The Flows handoff left "Mosaic panels as a node or a Target detail?" open (`design_handoff_astrodeck_flows/AstroDeck Flows.dc.html:565`). This spec rules that a mosaic is a Target detail, and that the loop is a backward event wire the compile consumes as structure. Record the ruling as a README amendment next to `README.md:122` and `:163`, so later agents do not "fix" it back. Mark `MILESTONE2-CONTRACT.md` superseded for the node count (19 there, 21 shipped).

---

## 1. Block model and wiring

### 1.1 The lane

```
DUSK WINDOW --window--> TARGET (M31, 3x2) --each panel--> AUTOFOCUS --> GUIDE --> FILTER CYCLE --all done--> SESSION REPORT
                           ^  next panel (event)                                        |
                           +------------ pass done (event, dashed amber back-arc) -------+
FILTER CYCLE --frame graded (event)--> CONDITION / NOTIFY        (unchanged)
```

- The flow lane stays acyclic. `flow_order` classifies edges by the source port's kind (`compile.py:77-78`), so an event edge never enters Kahn's walk.
- AUTOFOCUS and GUIDE sit inside the drawn circle because their effects are re-established on every panel hop (section 5.6). Their params stay presence-only, as today (`to_plan.py:361-385`).
- Every node type has at most one flow input (checked across all 21 in `flows/nodes.py`), so a lane is a chain and stages follow each other only through a stage's `complete` output. Section 1.5 turns that into the ownership rule.
- "Moving on to whatever steps are next" is the flow wire out of the tail stage's "all done". It fires once, when every non-skipped panel owes nothing (section 1.6).

### 1.2 TARGET block

The type id stays `target`, category SOURCE, label TARGET.

| Port | Kind | Required | Label | Notes |
|---|---|---|---|---|
| `arm` | flow in | yes | arm | unchanged |
| `next` | event in | no (added to `optional_ins`, `nodes.py:84`) | next panel | NEW; only a `pass` wire from the tail of this block's panel lane means anything here |
| `target` | flow out | - | each panel | id unchanged, so every saved wire survives; label changes |

- Params are listed in section 3.1.
- With rows = cols = 1 the block is today's single target, plus centring settings that now reach the engine.
- Card footer `sum()`, sized to the 188 px card:
  - Mosaic rotating: `M31 . 3x2 . PA 30.0 . 25% . rotate`
  - Mosaic without the wire: `M31 . 3x2 . one panel at a time`
  - Single target: `NGC 7331 . any angle`
- A one-line state chip from the progress route (section 3.8): `4/6 panels done`, or `212/315 subs` for a single target.

### 1.3 FILTER CYCLE and CAPTURE LOOP

1. Both gain an event output `pass`, labelled "pass done". It is structural: it means something only when it leaves the tail of a panel lane and enters the owning TARGET's `next`.
   - `_trigger_for` (`compile.py:124-164`) gets an explicit branch for `from_port == "pass"` that returns `"<type>.pass"`. Today capture and cycle return `on_frame_graded` for any port (`compile.py:149-150`).
   - A pass wire to any other destination is therefore an illegal trigger, and `to_plan` reports it as "this rule will not run" at warn level through the existing machinery (`to_plan.py:596-728`).
2. FILTER CYCLE's and CAPTURE LOOP's `complete` output is relabelled "all done". Port labels are per type, so the label is the same on every card. Only the tail's "all done" is "what runs next". A mid-lane stage's `complete` wire only says "the next stage belongs to the same panel", and the wire's label chip reads "then".
3. Under a multi-panel block, every panel owes `cycles x perCycle` subs of every ticked slot. That is the same formula as a single target today (`to_plan.py:515-558`), so each panel is its own quota. One **pass** is one round of `perCycle` subs for every slot still short on that panel, in wheel order.
4. A CAPTURE LOOP inside the circle is treated as a one-slot cycle: acquisition `cycle`, `per_visit` 1, count per panel. Mono operators need no one-slot FILTER CYCLE workaround.
5. Several stages in one panel lane are concatenated in lane order. For example, TARGET -> CYCLE LRGB -> CAPTURE Ha 300 gives every panel all eight steps, the CAPTURE is the tail, and the loop wire leaves the CAPTURE. One pass is one round of `per_visit` frames for every step still short on that panel, in lane order and then wheel order. Any cycle step, or a loop wire, marks every panel `acquisition = "cycle"`.

### 1.4 The circle: the loop wire

**Grammar**

1. `stage.pass -> target.next` is legal when the stage is the **tail** of that TARGET's panel lane (section 1.5) and the block is multi-panel. It compiles to `group.mode = "rotate"`. Without such a wire, a multi-panel block compiles to `mode = "sequential"`, which is panel-first. Deleting the wire changes only the mode, never which stages the panels own.
2. An event input takes many wires (`flows/models.py:149-167`). More than one pass wire into `next` gets a doctor note ("one is enough") and changes nothing.
3. `to_plan` consumes the wire as structure and never emits it as an Instruction. The precedent is the calib wires that are honoured by `plan_extras` (`HONOURED_BY`, `to_plan.py:156-160`).
4. A pass wire into a 1x1 block gets a note: "one panel, nothing to rotate between." A pass wire from a stage in another block's lane gets a warning: "this wire does nothing". A pass wire from a stage of this lane that is not the tail is M12, a danger, because the stages after it would be shot once per panel with nothing to say when.
5. **Flow back-edges become a structural error** (slice S0).
   - `FlowGraph.validation_errors` refuses them with: "this flow loops back on itself at FILTER CYCLE -> TARGET; a flow lane runs once, and the panel loop is the dashed 'pass done' wire".
   - Save refuses with 422, and so does `/run`.
   - Both editors' drop resolvers refuse the wire with the same sentence (`ui/src/components/flows/FlowCanvas.tsx:107-139`, next `canvasModel.ts:155-186`).
   - The false docstring at `compile.py:72-75` ("a flow cycle is not expressible in the editor") is corrected.

**When the wire is added.** It is added automatically only at these moments, and it always leaves the tail of the panel lane (1.5). It is never added as a side effect of connecting something else.

- Modal DONE, when the block becomes multi-panel and owns a stage.
- The wizard's mosaic kind.
- Sky FRAME or Atlas "send to flow" (slice S6).
- The one-tap LOOP PANELS button on the card, in the stage list, and as the RUN section toggle in the modal.

**When it is deleted.** The block runs panel-first. Doctor M3 warns. The card reads "one panel at a time". The stage-list rail disappears.

**How it is drawn** (slice S4).

- `FlowWireLayer` draws any event edge whose destination is `target.next` as a back-arc. The path leaves the source port, drops to `y = max(bottom of the owned body cards) + 28 px`, runs left, and rises into the `next` port.
- The stock S-bezier (`ui/src/components/flows/geometry.ts:199-212`) would sweep back across the body cards. The arc is computed from the card formula (`geometry.ts:65-74`) and is never measured from the DOM.
- It carries a label chip: `every pass: next panel . 6 panels`.
- In night mode it is told apart by the dash pattern and the label, not by hue.
- The phone stage list has no canvas (`ui/src/next/hubs/session/flows/canvas/FlowStagesPhoneSheet.tsx:1-50`). There the loop is a dashed amber rail from the TARGET row to the FILTER CYCLE row, the rows inside it are indented, and the rail is labelled `EVERY PASS: NEXT PANEL`. When the wire is absent, a `LOOP PANELS` button takes its place.

**Why a wire rather than a param.**

- The owner asked to see the circle.
- The phone operator's only view of the graph is wires as rows.
- The grammar already allows backward event wires, with REPORT "done" -> POOL "advance" as precedent (`README.md:122`).

The implementability judge accepted exactly this form. The risk that the wire is lost silently is closed by the S0 `flowsConnect` fix (section 9, item I-06) and by doctor M3.

### 1.5 The panel lane and stage ownership (the scoping rule)

Today every CAPTURE or CYCLE step is appended to every target seen earlier in topological order (`compile.py:216-259`, the two `for t in targets` loops at `:233` and `:258`). That rule is kept byte for byte for any graph with no multi-panel block, so all seven Examples and every saved flow compile unchanged.

When the graph contains any multi-panel TARGET block, **every** capture and cycle stage in that graph is instead scoped by wires, with these definitions:

1. **Lane nodes** are AUTOFOCUS, GUIDE, legacy SLEW, CAPTURE LOOP and FILTER CYCLE. Every other node with a flow port ends a lane: TARGET, POOL, DOME, DUSK FLATS, REPORT, DUSK.
2. **The panel lane** of a TARGET (or POOL) is every lane node reachable from its flow output through lane nodes only. Because every node has at most one flow input, walking back from any node gives exactly one chain.
3. **The owner** of a stage is found by `owner_of(graph, node_id) -> FlowNode | None`, a new pure function in `compile.py`. It walks the single flow parent of the stage, then of that parent, through lane nodes only. It returns the first TARGET or POOL it reaches, and `None` when it reaches any other node type or runs out of parents. A seen-set bounds the walk.
   - This is **not** the doctor's walk. `_flow_upstream_types` (`doctor.py:40-63`) collects the set of every type upstream, which answers "did the cursor pass a guider?" and cannot name a nearest owner. It stays for those presence questions. Compile, to_plan and the doctor rules M3, M4, M12 and M13 call `owner_of`.
4. **The tail** of a multi-panel block's lane is its last stage: the stage whose `complete` output feeds no lane node. A multi-panel lane must be a single chain, with no fan-out from the TARGET or between lane nodes. A branched lane is M12 (danger, not runnable), because "the last stage" would be ambiguous.
5. **The loop wire leaves the tail.** Modal DONE, LOOP PANELS, the wizard and every generator wire `pass` from the tail. A pass wire from any earlier stage of the same lane is M12.
6. **What runs next** is whatever the tail's "all done" output feeds. By construction that is never a stage: a CAPTURE wired straight after the tail is itself a lane node, so it is part of the panel lane and becomes the new tail. The editor moves the loop wire to the new tail when a stage is appended, which is one of the named moments in 1.4.
7. **A stage with no owner** in a graph that has a multi-panel block is M13 (danger, not runnable). For example: TARGET -> CYCLE -> DOME -> CAPTURE. The CAPTURE walks back to DOME, which ends the lane, so the CAPTURE belongs to no target. Under the legacy rule it would have been appended to every panel, which is C's leak. Under this rule it would compile to nothing, and that must be loud. It needs its own TARGET.

Worked cases:

| Graph (flow wires) | Panels own | Tail | Next |
|---|---|---|---|
| TARGET(3x2) -> AF -> GUIDE -> CYCLE -> REPORT | CYCLE | CYCLE | REPORT |
| TARGET(3x2) -> CYCLE -> CAPTURE Ha -> REPORT | CYCLE, CAPTURE | CAPTURE | REPORT |
| TARGET(3x2) -> CYCLE -> TARGET(M33) -> CAPTURE | CYCLE | CYCLE | TARGET M33, which owns the CAPTURE |
| TARGET(3x2) -> CYCLE -> DOME -> CAPTURE | CYCLE | CYCLE | DOME; CAPTURE is M13 |

The switch covers the whole graph, and that closes C's leak. Whenever the two rules would differ for a stage, the compile adds a note that names the stage. The legacy rule is filed as its own defect (I-05).

### 1.6 What "done" means and what runs next

**The group is done** when every non-skipped panel's every step meets its count in the plan's count mode. That is exactly `Session.owed()` over the group's steps equalling 0 (`session.py:143-152`), the single definition of finished. The session is stamped complete only when `owed() == 0` over the whole plan. Otherwise it goes dormant and stays armed (`engine.py:1717-1816`).

**What runs next.** Next is whatever the tail's "all done" output is wired to (1.5, item 6). Targets reachable from it are **followers** of the group. While the group has live members, followers sort after every member in `remaining`, whatever their window start. The block's `whenWaiting` param decides what happens while the mosaic cannot be shot:

- **"Shoot what comes next, then come back"** (default, pending the owner's decision). While every live panel is waiting (below its floor, behind the horizon mask, held by the meridian rule, or in a deferral wait), the scheduler may select a ready follower. This is today's window-sorted skip-ahead (`engine.py:1924-1938`), with one change: such a follower runs with `VisitBound(deadline_ts = group_ready_ts)`.
  - `group_ready_ts` is the earliest real time a live member becomes eligible: its meridian crossing (5.7), the end of a deferral wait (5.1), or the projected time a blocked panel clears the floor and the mask. The last is a forward scan in 60 s steps over the same predicate as `_mount_floor_verdict`, in the shape of `_time_to_gate` (`schedule.py:772`). A member with no clearing time tonight does not bound it. The 60 s recheck is a re-evaluation cadence, never a deadline.
  - The deadline is checked at frame boundaries in `_run_steps`, never inside `_run_step`. For a follower with blocks acquisition that means calling `_run_step(max_frames=1)` in a loop, which is the path cycle acquisition already takes, and checking the deadline between calls.
  - At the deadline the follower's visit ends. It keeps its progress, is requeued behind the group, and selection returns to the group.
  - Once the group is set aside for tonight, or complete, a follower runs its normal course.
  - Without this bound, a follower selected at any all-waiting moment runs to completion and is removed (`engine.py:1964-1965`, `:2010`). The routine pre-flip idle (5.7: 0 to 10.6 min with cut visits, and 2.4 to 37.3 min without them, in the Appendix A.4 cases), a tree, or one 60 s recheck would hand the rest of the night away at every meridian crossing.
- **"Wait for the mosaic"**. Followers get `Target.after_group = <group id>`. Gating reads them as waiting ("after the M31 mosaic") while the group has live members. They are skipped for the night, not marked done, once the group is set aside tonight. They become ready when the group is complete. Tonight shows how many idle hours this choice costs.

Default and why: a multi-night mosaic that holds its followers would idle clear sky for weeks, and a follower that keeps the cursor would starve the mosaic at every meridian crossing. The bounded follower visit gives neither. The owner's "before moving on" is met on any night the mosaic can be shot, because a follower only fills gaps the mosaic cannot use and hands the cursor back when the mosaic can shoot again. Which of the two is the default is an owner decision (Revision 1).

### 1.7 SLEW + CENTER becomes part of TARGET

- `slew` stays in `NODE_DEFS`, so saved graphs load, but it moves to a new `LEGACY_TYPES` set that is hidden from the palette.
- `PALETTE_COVERS_EVERY_NODE_TYPE` (`ui/src/components/flows/palette.ts:144-146`) becomes "covers every non-legacy type". The pins in `nodeDefs.test.ts` and `palette.test.ts:48` change deliberately: still 21 types, 1 of them legacy.
- **No graph rewrite.** Folding SLEW out of stored graphs would have to happen on raw dicts, because `FlowNode._known_type` rejects an unknown type before `with_defaults` runs (`flows/models.py:60-65`). Leaving the node in place avoids that risk entirely.
- A legacy SLEW's `tol`, `retries` and `solver` are **not** carried to TARGET. They never reached the run (`to_plan.py:361-377`). What actually ran was the hub default of 0.02 deg with 3 attempts (`hub.py:6387-6391`). Carrying `tol 0.5` would silently tighten every saved flow's centring.
- Doctor note L1, filled from the node's own values: "this stage is part of the TARGET block now. Its 0.5 arcmin tolerance never reached the run, which centred to 1.2 arcmin. Delete it and set centring on the TARGET."
- Doctor rule 4 (`doctor.py:167-170`) becomes: "CAPTURE with no TARGET upstream - the loop shoots wherever the mount happens to point". It is satisfied by a TARGET, a POOL, or a legacy SLEW upstream along wires.
- The wizard lane (`wizard.py:266-281`), the brief sentence (`tonight.py:664-666`) and the Examples drop SLEW in slice S3. A test pins that their compiled plans are unchanged.

### 1.8 Doctor rules added or changed

The wording follows the house rules: explain why, no em-dashes, never the word "slave" (`doctor.py:3-21`). There is no UI copy of the doctor to update: no rule text exists anywhere in `ui/src`, and the UI renders the issues the server returns. The docstring at `doctor.py:13-17` that says otherwise is stale and is corrected in S3.

| Id | Level | Fires when | Text (abridged) |
|---|---|---|---|
| R4 | warn | capture or cycle has no TARGET, POOL or legacy SLEW upstream along wires (graphs with no multi-panel block; with one, M13 replaces it) | "CAPTURE with no TARGET upstream - the loop shoots wherever the mount happens to point" |
| M1 | danger, plus `GraphNotRunnable` | multi-panel block with fovX or fovY = 0 | "Frame this block: panels are tiled from the camera's field, and this block has not recorded one." |
| M2 | danger, plus `GraphNotRunnable` | multi-panel block with angle "Any angle" | "A mosaic is laid out at one camera angle, and with no angle the panels will not tile. Lock an angle, or use the angle the camera measured." |
| M3 | warn | multi-panel block owns a stage but has no loop wire | "Panels are shot one after another: a night cut short leaves the last panels empty. Wire FILTER CYCLE 'pass done' to TARGET 'next panel' to rotate panels every pass." (names the tail) |
| M4 | note / warn | loop wire on a 1x1 block (note) / from a stage in another block's lane, by `owner_of` (warn) | "one panel, nothing to rotate between" / "this wire does nothing: FILTER CYCLE is not in this TARGET's panel lane" |
| M5 | warn, then loss | live effective optics differ from the snapshot by more than 2% (warn). Live field smaller than the step `fov x (1 - overlap)` on either axis (loss: blocks `/run` until accepted) | "framed for 2.00 x 1.33 deg; this camera now images 1.40 x 0.93 deg, so the panels would leave gaps. Re-frame." |
| M6 | warn | meridian convergence between neighbouring panels uses more than 25% of their overlap (Appendix A) | "at Dec 75 the corner panels turn 6.7 deg against the grid, which uses 39% of the 12' overlap. Widen the overlap or use fewer columns." |
| M7 | warn, plus loss | two blocks disagree on `counts` (count_mode is one plan-wide setting, `sequence/models.py:309`) | "this plan counts accepted subs because TARGET M31 asks for it; TARGET M33's 'every sub taken' cannot be honoured in the same run" |
| M8 | warn / note | "Rotate to PA" with no rotator in the active profile (warn) / "Camera fixed at PA" (note) | "no rotator: turn the camera by hand to PA 30.0 before the first panel. The run measures the angle at every panel and holds the mosaic if it is off by more than 6.0 deg." (the combined budget of Appendix A.2) |
| M9 | warn | counts accepted, no stop boundary, and both reject guards off | previews the `quota_unbounded` refusal (`sequence/models.py:402-435`) before Run |
| M10 | note | a measured hop cost is more than 25% of a visit (only when a measured cost is injected) | "each hop costs 2 m 40 s against a 13 min visit; 2 passes per visit would cut hops by half" |
| M11 | danger | two blocks share a name | "two blocks are both called M31; panel names and rules find targets by name" |
| M12 | danger, plus `GraphNotRunnable` | a multi-panel lane branches, or a pass wire leaves a stage of the lane that is not its tail (1.5) | "the loop wire starts at FILTER CYCLE, but CAPTURE Ha comes after it in the panel lane. Start the loop wire at CAPTURE Ha to shoot it on every panel, or give CAPTURE Ha its own TARGET." |
| M13 | danger, plus `GraphNotRunnable` | in a graph with a multi-panel block, a capture or cycle stage has no owner by `owner_of` (1.5, item 7) | "CAPTURE Ha belongs to no TARGET: DOME ends the M31 panel lane. It would shoot nothing. Give it a TARGET." |
| M14 | note | a REPORT "target done" wire leaves a lane owned by a multi-panel block | "REPORT 'target done' fires once per panel, 6 times for this mosaic (I-35)" |
| M15 | danger | the angle budget is spent: meridian convergence alone uses half the overlap (Appendix A.2, `k <= 0`) | "at Dec {dec} convergence alone uses {c}% of the overlap, which leaves no room for camera angle error. Widen the overlap or use fewer columns." (filled from the A.1 computation; the 1x4 at 10% at Dec 75 uses 38.9% and does not trip it) |
| L1 | note | legacy SLEW present | see 1.7 |

The wizard must still produce output that passes the doctor at note level or better across its option matrix (`wizard.py:9-15`). A wizard mosaic therefore always carries an angle, a camera field and the loop wire, and the wizard must be able to supply the first two honestly:

- **Angle.** The wizard's mosaic kind asks for it, or offers USE MEASURED from `status.sky_angle` (`hub.py:7001`). It never writes a hard-coded default: a default angle nobody chose is exactly the I-04 defect (23.4 commanding a rotator).
- **Camera field.** The wizard route injects the live effective optics (`hub.effective_optics`, `hub.py:1987`) as a rig fact, as `to_plan` does for `cool_to`. With no optics it answers with a single target and says why ("set the camera and focal length in Settings > Optics to plan a mosaic"). It never emits an M1 graph.

---

## 2. The Target modal ("FRAME")

### 2.1 Where it lives and how it opens

- **One shared component**: `ui/src/components/flows/framing/TargetFramingSheet.tsx`, plus a pure model file. The #/next tree re-exports it (the rebuild must not fork presentation files). It is loaded lazily, following the entry-chunk rule D-FU-2.
- **Classic desktop**: a `FRAME ON SKY` row in the per-type slot next to calib's (`ui/src/components/flows/FlowInspector.tsx:146`). No new `FieldDef` control is added, so the "three controls" pin holds (`nodeDefs.test.ts:406-462`). The plain field rows stay for keyboard editing.
- **#/next**: a lazy `flowFrame` sheet registered in `reg.ts`. On phone it opens **in place of** `flowNode` at depth 2, because a third sheet would replace the second (`ui/src/next/router.ts:47`, `:263-272`). It carries `?open=` like every flows sheet (`FlowsCanvasHost.tsx:109-137`). The rebuild's slot sits beside `calibSlot` (`inspector/FlowNodeEditor.tsx:84`).
- **Palette drop** opens the modal at once. This needs `flowsAddNode` to return the new id; today it returns nothing, and adding a stage does not select it (`FlowPalette.tsx:145-149`, `flowsSlice.ts:249-257`).
- **Read-only Examples** open it in view mode and say why.

### 2.2 Layout

**Phone portrait (390 x 844 as the reference)**

1. **Header**, 48 px, sticky: `CANCEL`, `FRAME M31`, `DONE`. DONE is a HonestButton (section 2.4).
2. **Sky**, pinned outside the scroller (the Atlas scroll-trap lesson). Height `min(100vw, 52svh)`, which is 390 px on the reference phone. It shrinks to `40svh` while a text field has focus. The canvas is never hidden behind tabs (framing spec `2026-06-15-ux-sky-atlas-framing-design.md:428`).
3. **Readout strip**, 32 px, in mono: `5.0 x 3.3 deg . 6 panels . 00h42m44s +41 16'`.
4. **Scroller** with 44 px rows and six sections: WHERE, GRID, ANGLE, PANELS, RUN, CENTRING.

**Phone landscape (667-932 px wide)**: sky on the left at 55%, scroller on the right. This is chosen by an aspect or container query, never `lg:`, which strands phone landscape.

**Tablet and desktop**: a new Overlay `full` variant. `Overlay.tsx:80-84` has no full-screen variant today, and sizes still go through `--ov-*` variables. The sky flexes on the left beside a 360 px control column.

### 2.3 The sky and the panel layer

- **One `SkyCanvas`**, extended only through props and never forked (`ui/src/components/atlas/SkyCanvas.tsx:95-153`). It is mounted on a **local** FramingSession seeded from the node's params, following the CompassSurvey precedent (`ui/src/components/sky/ClassicAtlasSky.tsx:130-147`). It never touches the global `store.framing` (`ui/src/store.ts:1283-1318`). That singleton is how one target's mosaic appeared on another target's flow in review #3 (`flowLane.ts:80-86`).
- **Survey and interaction.** Survey tiles come from the existing HiPS engine and the offline pack. The compass, RotateHandle and the live PointingFrame are unchanged. The live footprint is drawn only from telemetry and is never fed the planned target (`atlasFov.ts:8-15`).
- **New `panels` prop, the PanelLayer.** It draws each panel from the **server's** panel coordinates through `skyToView` (`atlasFov.ts:151`) and `fovCornersSky` (`:196`), with HTML labels (`1-1`) and the run-order number.
  - Labels must never come from `FovOverlay`'s screen-space grid, which is a point reflection of the server layout (`FovOverlay.tsx:98-122`), or from `panelRects`, which mirrors columns (`mosaic.ts:228-250`).
  - While the operator drags, the client mirror (`lib/framing.ts:216`) previews the grid. On every settle the modal calls `POST /api/framing/mosaic` (`framing.py:317-347`) and redraws the labels from its answer.
- **Label convention.** 1-based `row-col` in the server convention: row 1 is the north edge and col 1 is the west edge at angle 0. That is the same as the Plan names `<name> r-c`. Server panel (0,0) is the north-west corner (`framing.py:176-231`), so on the north-up, east-left chart `1-1` sits top right.
- **Panel states are told apart by shape, not hue** (night mode):

  | State | Drawing |
  |---|---|
  | pending | thin stroke |
  | skipped | dashed, with an X |
  | shooting (run mode) | thick stroke plus corner ticks: the active-panel style the framing spec reserved, finally fed |
  | done | diagonal hatch |
  | set aside tonight | dotted, with "!" |

- **Gestures.** One-finger drag moves the sky under the pinned grid, which moves the mosaic centre (the existing `panTo`, `SkyCanvas.tsx:457`). A **MOVE GRID / MOVE SKY** toggle switches to dragging the grid over a still sky. That needs a new additive `frameCenter` prop, projected through `skyToView`. Pinch zooms 0.1 to 10 deg. Tapping a panel toggles skip.

### 2.4 Sections

**WHERE**

- CatalogSearch, reused from `QuickFlow.tsx:346-351`. RA and Dec in J2000 sexagesimal, parsed with `raHms`/`decDms` (`QuickFlow.tsx:54-90`).
- FIT OBJECT.
- SUGGEST GRID (ATL-05): `cols = ceil((size - fov*ov) / (fov*(1-ov)))`. It is a hint only until the catalogue carries major axis, minor axis and PA. Today it has `size_arcmin` only (`types.ts:938`).

**GRID**

- COLS and ROWS steppers, 1 to 10. OVERLAP from 0 to 50% in steps of 5, with quick chips at 15, 25 and 35. The default is 25%. It is **one server constant**, exported by `framing.py` and replacing both the 0.25 in `store.ts:1310` and the 0.15 in `next/hubs/sky/frame/mosaic.ts:36`.
- Camera field: `Tiled for 2.00 x 1.33 deg at bin 1 (profile Refractor, matched 2026-09-23)`.
- **MATCH CAMERA** snapshots `effectiveOptics` (`lib/effective.ts`, profile-aware, #129) into `fovX`/`fovY` at bin 1.
- A banner appears when the live optics differ from the snapshot by more than 2%. When the live field would leave gaps, it states M5's sentence.
- The grid controls lock with a reason when optics are unset: "set the camera and focal length in Settings > Optics".

**ANGLE**

Three segments:

- **ANY ANGLE** writes `rotation = -1`. It is locked for grids larger than 1x1, with the reason "a grid is laid out at one camera angle".
- **ROTATE TO** commands the rotator. It is locked with a reason when the active profile has no rotator.
- **CAMERA FIXED AT** never commands the rotator. The run checks the angle instead (section 5.6).

Controls and readouts:

- A continuous degree field with nudges of plus or minus 1 and 15, the rotate handle, and the `[` `]` keys. There is no 15-degree snap. The existing dial shows 0 for other angles (`FramingCard.tsx:124`), which is issue I-24.
- A **USE MEASURED** chip, read from `status.sky_angle` (`hub.py:7001`, which nothing in `ui/src` consumes today): `camera measured 37.2 deg, 14 min ago, by the centring solve, pier west`. One tap lays the grid out at the angle the camera actually sits at. That is the no-rotator workflow, and nobody has to turn anything.
- The convention line: "degrees N through W, clockwise on this chart, as the plate solver reports (CROTA2); the rotator's sense is under test (#145)".
- The tolerance line, computed from the geometry (Appendix A.2): "this layout tolerates a camera error of 6.0 deg (convergence and angle error together use at most half the overlap at a four-panel corner)".

**PANELS**

- One row per panel in run order: order number, `r-c`, an on/off toggle, a progress bar (banked/owed from the progress route), and the set-aside reason when there is one.
- The peak-altitude column and the `MosaicNightCard` (`next/hubs/sky/frame/MosaicNightCard.tsx:49`, props-only) render only with `CAP_VIEW_SITE_DERIVED`. They are **hidden**, never shown empty, for a viewer.
- An ORDER select: Least complete first / Setting first / Grid order.

**RUN** (shown only when the block owns a stage; every number comes from the server compile, never computed in the client)

- A **Rotate panels every pass** toggle. It adds or removes the loop wire, which keeps one source of truth.
- Stay on a panel: `passes` (1 to 20) and "at least N minutes" (0 to 180).
- Counts: Accepted subs / Every sub taken.
- When the mosaic cannot be shot: Shoot what comes next, then come back / Wait for the mosaic.
- Readouts, for the shipped default cycle (L R G B at 60 s, Ha OIII SII at 180 s, 45 cycles) on a 3x2:
  - `6 panels x 7 filters x 45 = 1890 subs`
  - `9.75 h per panel, 58.5 h in all`
  - `270 visits at 1 pass per visit`
  - `hop: not measured on this rig yet`, or once it has been measured, `hop 2 m 40 s, measured over 6 hops`. The efficiency figure appears only with a measured hop.
  - `meridian: no idle before the flip` for this 3x2 at Dec 41 (the pre-flip idle of 5.7, computed from the RA span of the panel centres, the plan's flip lead and the hop; none of these is site data). A compact 2x2 of 0.9 x 0.6 deg reads `meridian: up to 10.6 min idle before the flip`.
  - `focus: refocus on temperature` when the focuser temperature delta is armed, or `focus: a sweep only at the first panel; set a temperature delta to refocus as the night cools` when neither it nor `autofocus_every` is set (5.6).
- With `CAP_VIEW_SITE_DERIVED`: "this is a campaign: about 7.8 nights of 7.5 h before hops. The session stays armed and resumes at the next dusk." The old advice "Set DUSK to repeat nightly" is dropped because it changes nothing: `engine.start` arms auto-resume on every run (`engine.py:795`). What DUSK "Single night" should mean is an owner decision.

**CENTRING**

- Tolerance in arcmin (default 1.2) and tries (default 3). These equal what actually runs today: 0.02 deg and 3 (`hub.py:6387-6391`).
- "If a panel will not centre or reach its angle": Auto / Skip it this pass / Shoot anyway. Auto means skip for a mosaic panel and shoot for a single target.

### 2.5 DONE

- DONE stays locked, with the reason "waiting for the server's panel positions", until `POST /api/framing/mosaic` has answered for the current spec.
- Offline, or as a viewer (that route needs `CAP_VIEW_SITE_DERIVED`), DONE unlocks with the chip "panels from the offline mirror; the run computes them on the server". That is true, because `to_plan` calls the same `compute_mosaic`.
- DONE commits **one** new slice action, `flowsApplyFraming(id, patch, loop)`. It writes every changed param and adds or removes the loop wire in a single slice write, with one dirty/compile cycle. `flowsSetParam` writes one key per call today (`flowsSlice.ts:264-279`). Coercion follows each default's type, as `flowsSetParam` does.
- The card turns valid only after the compiler answers.
- **Re-frame confirmation.** If the geometry key (section 3.3) changes while the progress route reports banked subs, DONE asks first: "Re-framing starts all 6 panels from zero: 212 banked subs belong to the old layout and stay on disk."
- Skip toggles never ask, because skip is not part of the identity. Re-enabling a panel restores its progress. CONTINUE keeps this promise: a skipped panel's banked steps are exempt from the dropped-steps refusal (5.9).

### 2.6 Run mode

While this flow's session is active, the same sheet opens read-only, fed by `state.group` (section 5.10) and the progress route. The current panel gets corner ticks, panels fill as their subs land, and set-aside panels show their reason. What the operator framed is what they watch.

### 2.7 What is stored and what is not

**Stored**: node params only (section 3.1).

**Never stored**:

- Panel centres. `to_plan` derives them on every compile; `mosaic.ts:3-8` calls a client copy "a second truth about where the telescope points".
- Progress, which lives in the ledger.
- Altitudes, which are site-derived and fetched when needed.
- The survey image.

Survey choice and zoom are a per-viewer convenience in `localStorage`, keyed by flow and node id, with every access wrapped in try/catch.

---

## 3. Data model and compile path

### 3.1 TARGET params (flat scalars, as `flowsTypes.ts:24-30` requires)

A NodeDef gains `create_params`. These are overrides applied when a node is **created**: by palette drop, by the wizard, or in an example fixture. Missing-key defaults (`default_params`, merged by `with_defaults`, `flows/models.py:67-88`) reproduce old behaviour. New blocks are created with the new choices written out explicitly. That fixes the "semantics flip needs a migration" class structurally, and a stale browser tab POSTing an old-shaped graph cannot flip it either. `nodeDefs.ts` mirrors `createParams`, and the parity test covers it.

| Key | Control | Missing-key default (old meaning) | Created as | Meaning |
|---|---|---|---|---|
| `name` | text | `""` | `""` | display name and panel-name prefix |
| `ra`, `dec` | text | `""` | `""` | J2000 sexagesimal. Empty means "no usable coordinates" from `to_plan` (`to_plan.py:1062-1073`), so the silent M31 is gone |
| `rotation` | number | `-1` (S0 changes it from 23.4) | `-1` | degrees in the CROTA2 convention. Negative means none; 0 is a real PA (`to_plan.py:1075-1091`) |
| `angle` | select | derived at compile: `rotation < 0` gives Any angle, otherwise Rotate to PA | `Any angle` | Any angle / Rotate to PA / Camera fixed at PA |
| `rows`, `cols` | number | `1` | `1` | 1 to 10 (`MosaicSpecIn`, `framing.py:52-83`) |
| `overlap` | number | `25` | `25` | percent, 0 to 50 |
| `fovX`, `fovY` | number | `0` | `0` | bin-1 degrees snapshotted at framing. 0 means not framed |
| `fovFrom` | text | `""` | `""` | provenance line, for display only |
| `skip` | text | `""` | `""` | for example `3-1, 3-2` |
| `passes` | number | `1` | `1` | full filter passes per visit, 1 to 20 |
| `minVisit` | number | `0` | `0` | minutes, 0 to 180. Re-set in S7 from the measured hop cost |
| `order` | select | `Least complete first` | same | / Setting first / Grid order |
| `centerTol` | number | `1.2` | `1.2` | arcmin. Equals the hub default of 0.02 deg |
| `centerTries` | number | `3` | `3` | 1 to 5 |
| `ifNotCentred` | select | `Auto` | `Auto` | / Skip it this pass / Shoot anyway |
| `counts` | select | `Every sub taken` | `Accepted subs` | sets `plan.count_mode` |
| `whenWaiting` | select | `Shoot what comes next, then come back` | same | / Wait for the mosaic. The default is an owner decision (D15) |

FILTER CYCLE and CAPTURE LOOP gain the `pass` event output. TARGET gains `next`. No `FieldDef` control is added. The node-type count stays 21.

### 3.2 Compiled dict (`compile_plan`, still pure: no devices, no config, no clock)

A TARGET node still emits **one** entry, so the PLAN tab shows the compile verbatim and one block reads as one mosaic:

```
{"name", "ra", "dec", "rotation_deg", "node_id",
 "angle": "any" | "rotate" | "fixed",
 "mosaic": null | {"rows", "cols", "overlap", "fov_x", "fov_y", "fov_from",
                   "skip": [[r, c], ...], "order", "passes", "visit_min",
                   "require_centred", "when_waiting"},
 "loop": bool,                       # a pass wire from an owned stage exists
 "centre": {"tol_arcmin", "attempts"},
 "count_mode": "accepted" | "attempts",
 "steps": [...]}                     # owned stages only (section 1.5)
```

`tonight.py` iterates compiled targets (`tonight.py:393-439`), so this shape gives Tonight one `compute_night` per block instead of per panel, with no extra work.

### 3.3 Expansion, identity and rig facts (`to_plan.to_sequence_plan`)

`to_sequence_plan(compiled, *, flow_id="", rig=...)`. Both `_compile_payload` (`app.py:4757-4812`) and `run_flow` (`app.py:5098`) pass `flow_id`.

**Expansion.** For an entry with `mosaic` set, `to_plan` does the following:

- Parse the coordinates the way it does today.
- Call `framing.compute_mosaic({ra_hours, dec_deg, rows, cols, overlap/100, rotation_deg, fov_x_deg, fov_y_deg})`. That is the one projection; there is no third copy.
- Drop the skipped panels.
- Emit one Target per remaining panel:
  - `name "<name> <row+1>-<col+1>"`, `panel_row`, `panel_col`
  - `mosaic_group = group_id`
  - `rotation_deg`: the PA when `angle == "rotate"`, otherwise `None`
  - `acquisition "cycle"` when any step is a cycle step or `loop` is true
  - `center_tolerance_arcmin`, `center_attempts`
  - `autofocus_skip_if_fresh = True`
  - schedule from the dusk block (`to_plan.py:471-491`)
  - steps expanded per panel through `_cycle_steps` (`to_plan.py:515-558`)
- Emit one `TargetGroup` (section 3.4). Its `skipped_ids` lists the deterministic target ids of the skipped panels, so CONTINUE can tell a skipped panel from a dropped one (5.9).

**Identity.** `NS_FLOWS` is a fixed uuid constant in `to_plan`.

- `geometry_key` = the first 16 hex characters of sha256 over canonical JSON of `ra_hours` and `dec_deg` (to 1e-6), `rows`, `cols`, `overlap` (to 1e-4), `rotation_deg` (to 1e-3), and `fov_x`, `fov_y` (to 1e-5). **`skip` is excluded.**
- `group_id = uuid5(NS_FLOWS, f"{flow_id}/{node_id}/{geometry_key}").hex`
- `target_id = uuid5(NS_FLOWS, f"{group_id}/r{row}c{col}").hex`. A 1x1 block uses r0c0, so single targets gain continuity too.
- Pool members: `uuid5(NS_FLOWS, f"{flow_id}/{node_id}/member/{name}").hex`.
- `step_id = uuid5(NS_FLOWS, f"{target_id}/{stage_node_id}/{frame_type}/{filter}/{exposure_s:g}/{gain}/{binning}/{n}").hex`, where `n` is the occurrence index among identical tuples inside that stage (almost always 0).
  - `count` is not in the id, so raising `cycles` keeps progress.
  - An exposure, gain or binning change starts a new count. That is the #77 rule: two geometries must not share a count.
  - The stage node id keeps identical steps from two stages on one panel apart.
- Without `flow_id` (an unsaved preview, or a graph-less caller), ids fall back to uuid4, as today.
- Because step ids are unique per panel, the ledger's step-id-only counting (`session.py:110-121`) stays correct with **no session schema change** (`SESSION_SCHEMA = 1`, `session.py:26`).

**Rig facts**, injected by the route the way `cool_to` is (`to_plan.py:1034-1051`):

- the live effective optics (`hub.effective_optics`, `hub.py:1987`) for M5
- the measured hop cost for M10 and the brief
- rotator presence for M8

The compile never re-tiles from live optics. The node snapshot is what the engine slews to.

**`plan.count_mode = "accepted"`** when any block asks for accepted subs. A disagreement is M7.

### 3.4 Engine models (`server/astrodeck/sequence/models.py`)

Everything is additive, the defaults reproduce today's behaviour byte for byte, and `ui/src/types.ts` mirrors it all.

```python
class TargetGroup(BaseModel):
    id: str                                  # equals every member's mosaic_group
    name: str = ""
    kind: Literal["mosaic"] = "mosaic"
    mode: Literal["rotate", "sequential"] = "rotate"
    visit_passes: int = Field(1, ge=1, le=20)
    visit_min_s: float = Field(0.0, ge=0, le=10800)
    order: Literal["least_complete", "setting_first", "grid"] = "least_complete"
    require_centred: bool = True
    max_failed_visits: int = Field(3, ge=1, le=20)
    pa_deg: float | None = None              # layout angle, CROTA2 convention (#145)
    rotate: bool = False                     # members carry rotation_deg = pa_deg
    angle_tolerance_deg: float | None = None # None disables the angle check
    skipped_ids: list[str] = []              # target ids of skipped panels (5.9)
    geometry: dict = {}                      # provenance only: rows, cols, overlap, fov, key

class VisitBound:                            # engine-internal, never persisted
    passes: int | None                       # rounds per visit (group members)
    min_s: float = 0.0                       # at least this long, checked at round boundaries
    deadline_ts: float | None = None         # end at the first frame boundary that
                                             # cannot fit the next frame: the panel's
                                             # flip point (5.7) or group_ready_ts (1.6)

# Session gains (additive, default empty, SESSION_SCHEMA stays 1):
set_aside: list[dict] = []                   # {target_id, step_id | None, reason, night}

# Target gains:
center_tolerance_arcmin: float | None = Field(None, gt=0, le=30)
center_attempts: int | None = Field(None, ge=1, le=10)
autofocus_skip_if_fresh: bool = False
panel_row: int | None = None
panel_col: int | None = None
after_group: str | None = None

# SequencePlan gains:
groups: list[TargetGroup] = []
```

- An empty `groups` list gives a byte-identical run.
- A `mosaic_group` with no `groups` entry (today's Plan-UI mosaics) keeps today's panel-first behaviour.
- `angle_tolerance_deg` is computed in `to_plan` from the geometry, after convergence has taken its share (Appendix A.2).
- `Session.set_aside` makes "set aside for tonight" survive a crash: a crash-resume on the same night reads the records whose `night` matches the durable log's night key and does not retry those panels. A later night ignores them. `Session` has no `extra="forbid"` (`session.py:70`), so a build that predates the field loads the file and ignores the field. It then retries set-aside panels, which is today's behaviour.

### 3.5 Identity checks at the start paths (`plan_identity_errors`)

`plan_identity_errors(plan) -> list[str]` is called on **all five** start paths:

- `/api/sequence/start`
- `/api/flows/{id}/run`, including continue
- `/api/sessions/{id}/resume` (`app.py:5350`)
- `/api/sequence/recover`
- ResumeArm

It refuses:

- duplicate target ids
- duplicate step ids
- a group with no members
- a calibration target in a group
- an `after_group` that names no group

It refuses **duplicate target names** only when the plan carries groups or an instruction names targets (`only_target`, `run_target`, `skip_target`). Otherwise it logs a warning. The classic Plan appends duplicate single targets routinely (`store.ts:1323-1335`), and refusing their resume would strand dormant sessions.

It is deliberately **not** a pydantic model validator. `SessionStore.active()` and `load_all()` skip any session that fails validation, without a word (`session.py:212-239`), so a validator would make stored sessions vanish on upgrade. A refused resume keeps the session listed and says why.

### 3.6 Flow schema versions and downgrade

**FLOW_SCHEMA 3** (slice S0; `store.py:28`, `_migrate` at `store.py:52-73`):

- v2 to v3 rewrites a target's `rotation == 23.4` to `-1`, the palette default nobody chose. It sets a non-persisted `FlowRecord.migrated` note that is shown once: "angle 23.4 was the old palette default and commanded a connected rotator to PA 23.4; it now reads 'any angle'. Set it again if you meant it."
- `_migrate` **refuses** `schema_version > FLOW_SCHEMA`. Today it returns any file at version 2 or above unchanged (`store.py:52-73`), so a future file whose node types are all known would load with this build's meaning. `_on_disk` also silently skips files that fail (`store.py:93-105`). From S0, both kinds of file are listed as visible library rows: "saved by a newer AstroDeck (schema 4); update to open it", or "unreadable: <reason>".
- The writer stamps 3 on every save, as evidence that a 23.4 saved after migration was deliberate.

**FLOW_SCHEMA 4** (slice S3) is a stamp-only bump; the v3 to v4 read is a no-op. The writer stamps 4 when the file uses any meaning a v3 build would misread:

- a multi-panel block
- `angle = Camera fixed at PA`
- a loop wire
- `counts = Accepted subs`
- `whenWaiting = Wait for the mosaic`

Otherwise it stamps 3.

**Downgrade matrix**

| File | Read by | Result |
|---|---|---|
| mosaic flow (v4) | S0-S2 build | refused loudly as future schema |
| rotating mosaic flow (v4, with the loop wire) | a build older than S0 (today's 0.3.32) | `validation_errors` refuses the wire, because the old build has no `pass` output or `next` input ("cycle has no output port 'pass'"), so save and `/run` answer 422. Safe. |
| unlooped mosaic flow (v4, loop wire deleted) | a build older than S0 | loads, `rows`/`cols` ignored, shoots the centre |

The last row is the residual risk. Mitigation: ship S0 at least one release before S3.

### 3.7 UI mirrors and test pins

- `nodeDefs.ts`: the new ports, `createParams`, the `LEGACY_TYPES` flag, relabelled ports, and the rotation default -1 (`:199`). `nodeDefs.test.ts` keeps parsing `nodes.py` (`:219-227`). The counts it pins change deliberately.
- There is no UI doctor copy to update (1.8).
- `types.ts` mirrors `TargetGroup` and the new Target fields. It adds `transit_alt_error` to `MosaicPanel` (`types.ts:1965`) and a `status.sky_angle` type.

---

## 4. Ordering: filters across panels versus panel-first

There are three possible orders for a mosaic of P panels and F filters.

| Order | Shape | Kept? |
|---|---|---|
| Panel-first | panel 1 all filters to quota, then panel 2 ... | yes, by deleting the loop wire (`mode = "sequential"`) |
| **Pass-major (default)** | panel 1 one filter pass, panel 2 one filter pass, ..., repeat | **default** |
| Filter-major | L on every panel, then R on every panel ... | rejected |

Why pass-major is the default:

1. **A cut-short night leaves every panel with every channel.** FILTER CYCLE already exists so that channels grow evenly (`nodes.py:165-186`). Pass-major applies the same reasoning across panels. Panel-first leaves the last panels empty. Filter-major leaves missing channels everywhere.
2. **Seams match.** Every panel samples the same transparency, gradient and altitude spread. SGP users have asked for this since 2017 and still cannot have it. Their words: "uncorrectable issues between panels due to the often wildly differing transparency night to night".
3. **The owner asked for it** ("feed in a circle"). A local, untracked mobile handoff draft describes the same rotation (`ASTRODECK-UPDATE/design_handoff_astrodeck_mobile/README.md:174`), but it is not a committed contract.
4. **The cost is hops.** Each visit pays a slew, a centring solve and a guider restart. `passes` and `minVisit` trade hops against evenness. Focus reuse (section 5.6) keeps hops from paying a 7-9 minute autofocus sweep. That holds only with the hop rule of 5.6: under today's 30-minute age rule a rotating mosaic would sweep about every second hop, about 19% of the night. The efficiency of a visit of V minutes and a hop of H minutes is `V / (V + H)`. For a 4-minute LRGB pass with an illustrative H = 2 (not a measurement): 1 pass per visit gives 67%, 2 give 80% and 3 give 86%. The modal shows efficiency only from a measured H.

Filter-major is rejected because it gives up the property FILTER CYCLE exists for, and because per-filter focus offsets make filter changes cheap anyway.

**Within a visit** the order is FILTER CYCLE's own round robin, in wheel order: `per_visit` (= `perCycle`) frames for each slot still short at that panel (`engine.py:2776-2824`). Filters already complete at that panel are skipped.

---

## 5. Execution semantics

### 5.1 Scheduler integration (requeue, passes)

Everything below lives in `_run_scheduled` (`engine.py:1866-2060`). Targets outside a group, and `mosaic_group`s with no `groups` entry, run exactly as today. Per group, the engine keeps a small state object that is never persisted; a resume recomputes it from the ledger:

```
_GroupRun: pass_no, visited: set[target_id], exposures_at_pass_start,
           deferred_this_pass: int, failed: dict[target_id, int],
           reject_visits: dict[target_id, int],
           set_aside: dict[target_id, reason], flipped: bool, acquired: bool,
           angle_verified: bool
```

`set_aside` is also written to `Session.set_aside` (3.4), so a crash-resume the same night does not retry those panels. Everything else is recomputed.

**Selection** (`engine.py:1924-1938`). For a member of a group, `gating_status` still decides first: frozen window, altitude start gate, moon, hour angle. A member that gating calls ready must also pass an **eligibility** check:

1. **Reachability.** `_mount_floor_verdict(target, projected=True) -> Verdict | None` is a new, non-raising twin factored out of `_enforce_mount_floor` (`engine.py:4482`). `_enforce_mount_floor` raises on a non-None verdict, so there is one predicate: floor, horizon and obstruction mask, no-go wedges, pier limit, zenith keep-out. The verdict is tagged, because waiting fixes some of these and not others:
   - `wait`: floor, mask, wedge, zenith keep-out, pier limit. Time changes these. The member is **waiting**, with `wake_ts = now + REACH_RECHECK_S` (60 s, a named constant) as the re-evaluation cadence. It joins `earliest` like any waiter, so the existing do-while wait path runs (`engine.py:2013-2058`) through `_wait_until` (`engine.py:2133-2177`), with the safety gate and the deadman armed.
   - `refuse`: no saved site while limits are configured (`engine.py:4578-4585`), and a pier-side change with meridian flips off (`engine.py:4531-4538`). Waiting cannot fix either one. No site refuses every panel, so it raises `SafetyAbort` exactly as the slew gate does today. A pier-side change with flips off sets **that panel** aside tonight with the gate's own sentence, while the panels reachable from the current side keep shooting.
   - There is **no** return path that says "blocked" to a scheduler that still thinks the panel is ready. This is the hot loop the safety judge found in A and C.
2. **Meridian hysteresis** (section 5.7). An ineligible member is waiting, with a wake time at its meridian crossing.
3. **Pass membership** (rotate mode). A member already visited this pass is not a candidate while any unvisited member is eligible.

**While nothing is shootable, the mount is park-held on the idle clock.** Today park-hold fires only when one computed wait exceeds `WAIT_TEARDOWN_S` (120 s, `engine.py:326`, test at `:2052`). A 60 s reach recheck never qualifies, so while every panel sits behind the mask the mount would keep tracking and guiding the last panel. Nothing would check its meridian or its floor, because those checks run per frame (`_maybe_meridian_flip` from `_run_step`, `engine.py:2886`, `:2937`). `_wait_until` runs the weather gate only, with no target (`engine.py:2163-2166`), and sun_watch stands down while a sequence runs (`sun_watch.py:9-10`). The same hole exists today, with no mosaic, for two kinds of wait:
- An eta-0 wait. A setting target below its start gate whose `_time_to_gate` finds no crossing gets `eta_s = 0` (`schedule.py:762`), so `wait_ts - now` is 0 and the scheduler re-evaluates every 5 s until the window closes, never park-holding.
- A constraint wait. `constraint_gate` nulls `start_ts` (`schedule.py:770-775`), so the waiter takes the 5 s else-branch (`engine.py:2058`), which has no teardown at all.

That is the "safety rides value paths" class. The check hangs off the wait's own interval, when the hazard is a mount tracking with no frame loop watching it. The fix (S0, a present-day issue) drives the teardown from the hazard's clock:

- The engine keeps `_idle_since`, set whenever an exposure or a `_setup_target` completes. The mount has been tracking unwatched since that moment.
- Every `_wait_until` tick with a tracked target (the last acquired one) runs three checks, and park-holds once, latched until the next `_setup_target`:
  1. `now - _idle_since >= WAIT_TEARDOWN_S`.
  2. The tracked target's live altitude (not projected) is below the effective floor, or it sits in a wedge.
  3. The tracked target reaches the plan's flip point (`plan.meridian_flip_lead_min` before transit) before the next tick. `flip_can_be_skipped` exempts it.
- Today's rule, "a planned wait longer than 120 s park-holds at once", stays.
- Test: a clocked-simulator run whose only target sets below its gate with the window open. The mount is park-held within `WAIT_TEARDOWN_S` plus one tick. Mutant: "teardown keyed on `wait_ts - now`" keeps tracking all night. A second case uses a constraint wait, and a third case tracks a target into the floor during a 60 s recheck loop.

The group's members stay contiguous in `remaining`, ordered by the order function (section 5.2).

**After a visit** (replacing the unconditional `remaining.remove(ready)`, `engine.py:2010`):

| Visit outcome | Action |
|---|---|
| panel complete (every step `_step_complete`) | remove it. `on_target_complete` rules run for it. They are now gated on real completion, whereas today they run after every `_run_steps` return (`engine.py:1966-1993`). Log: "panel 1-2 complete: 7 of 7 filters". |
| normal return, still owed, at least one accepted frame | mark it visited and move it behind the group's unvisited members (the requeue). Reset `failed[p]` and `reject_visits[p]`. |
| normal return, exposures taken, **none accepted**, while another live member accepted a frame since this panel's previous visit | the requeue, plus 1 to `reject_visits[p]`. At `max_failed_visits` (3) consecutive such visits: set it aside tonight with a warning alert, "2-3 rejected every frame for 3 visits while the other panels were accepted". When every member rejects, the sky is to blame and the counter does not move: the night guard and the cloud hold own that case. |
| `PanelDeferred` (section 5.6) | mark it visited and add 1 to `failed[p]` and `deferred_this_pass`. At `max_failed_visits` consecutive failures: set it aside tonight, with a **warning** alert that names the panel and the reason, then `reporter.mark_skipped`. |
| `StopTarget` from a closed window | handled by the existing all-closed path. Every panel shares the frozen window (`engine.py:1897-1903`). |
| `StopTarget` from the altitude floor (`on_floor = advance`) or a guiding "skip" escalation | set that panel aside tonight. It is not done. |
| `JumpTarget` | unchanged, via `_apply_jump` (`engine.py:2062`) |
| `SafetyAbort`, `NightQualityStop`, cancellation | propagate unchanged. A mosaic never downgrades a safety abort to a panel skip. |

**Pass boundary.** When no unvisited member is eligible but a visited member is, the pass ends.

1. Exposures this pass = 0 and deferrals = 0. Log "a full pass over N panels took no exposures; setting the mosaic aside for tonight", and set every live member aside. This lifts the anti-spin rule at `engine.py:2818-2824` to group level, but it counts **exposures** (accepted plus rejected), so a clouded pass of rejects does not end a mosaic. The reject guards handle that.
2. Exposures = 0 and deferrals > 0. `await _wait_until(now + DEFER_WAIT_S)` (300 s, a named constant), then start a new pass. The per-panel `max_failed_visits` bounds this at three consecutive all-deferred passes per panel.
3. Otherwise, start pass N+1: clear `visited`, snapshot the counts, and re-sort the group's slice of `remaining`.

If no member is eligible at all, the group is waiting and the existing wait path runs, with the idle-clock park-hold above. Under the default `whenWaiting`, a follower may fill the wait in bounded visits (1.6).

**Why the per-panel reject rule.** With the S0 fix, the per-step guard spans visits, and at one pass per visit it takes `max_consecutive_rejects` (10) visits to trip. A panel that always rejects (a star-poor field under the `min_stars` gate, or trailed frames caught by the eccentricity gate) would burn 10 x 780 s = 2.17 h of shutter per night before every step tripped. The panel rule bounds it at 3 x 780 s = 39 min plus three hops. The opposite failure, unguided frames that are **accepted** (guiding_action defaults to warn, `config.py:281`, and #142 lets those frames through the RMS gate), is not a reject and needs the guide-start policy the owner is asked to decide (Revision 1).

**Sequential mode** (no loop wire) uses the same machinery without the visit bound: the chosen panel runs to completion. The order policy, set-aside, deferral and meridian rules still apply.

### 5.2 Panel ordering

`sequence/panel_order.py` is a pure, unit-tested function. The scheduler, ResumeArm (section 5.9) and the progress route all use it. Counts are snapshotted **once per pass**, because `accepted_by_step()` walks every frame (`session.py:110-121`).

| Policy | Primary key | Tie-breaks |
|---|---|---|
| `least_complete` (default) | fraction banked, ascending | least recently visited (from ledger timestamps; never-visited first), then snake index from `compute_mosaic` |
| `setting_first` | time until the panel reaches its floor or the horizon mask, ascending: the western panels set first | fraction banked, then snake index |
| `grid` | snake order, rotated to start after the last-visited panel | - |

- Every eligible panel gets exactly one visit per pass. A policy only reorders, so no panel can starve.
- C's `catch_up` skipping is **not** adopted. It could skip the only panels that are able to shoot, and the zero-exposure rule would then set the whole mosaic aside.
- Resume needs no cursor. After a restart, a half-visited panel is the least complete one, so it is chosen first.

Worked example, a 3x2 on night one with nothing banked. Everything ties on completion, so snake order decides: `1-1, 1-2, 1-3, 2-3, 2-2, 2-1`. Each visit is one pass of 7 subs (13 min of shutter). The pass then repeats.

### 5.3 Visit bound

`_run_steps(ti, target, visit=VisitBound(passes, min_s, deadline_ts))`. Group members and bounded followers (1.6) pass a bound; everyone else gets `None`, as today.

- **Cycle acquisition.** Rounds proceed as today (`engine.py:2807-2824`). After each round, the visit ends if the panel is complete, or if `rounds >= passes` **and** `elapsed >= min_s`. The clock starts at the visit's first exposure, so the hop does not eat into the visit.
- **Deadline.** Before each `_run_step` call, which is one frame at `per_visit = 1`, the visit ends if `now + exposure + overhead EMA + FLIP_FRAME_MARGIN_S` (30 s, `engine.py:170`) would pass `deadline_ts`. That is the same budget `_maybe_meridian_flip` uses for its frame window (`engine.py:5294-5295`). A deadline can therefore end a visit mid-round.
- Both checks sit at step boundaries in `_run_steps`, **never** inside `_run_step`. An exposure is never cut short.
- The intra-target anti-spin rule stays, and counts exposures.

**The accepted-mode visit bound is broken today, and must be fixed first** (slice S0, issue I-01). `_run_step` claims its visit is "BOUNDED BY ATTEMPTS" (`engine.py:2862-2866`), but the accepted-mode reject branch `continue`s without incrementing `taken_this_visit` (`engine.py:3027-3049`). Only an accepted frame increments it (`:3025`). Two defects mask each other today:

- Because rejects are not counted, a visit keeps shooting a rejecting step until the quota is met or the local `step_rejects` reaches `max_consecutive_rejects`, which defaults to 10 (`config.py:1009`; test at `engine.py:3042-3048`). So the per-step guard **does** trip in cycle mode today, inside one visit. It only returns from that visit, though. The step is not set aside, and the next round re-enters it.
- The present-day harm is therefore that **one rejecting filter holds the whole cycle for up to 10 subs per visit, every round**. On the shipped default cycle, a rejecting 180 s filter turns a 13-minute round into about 40 minutes (780 - 180 + 10 x 180 = 2400 s), and every other channel waits. With the step guard off (0), the same visit runs until the night guard's 20 consecutive rejects (`config.py:1012`), which raises `NightQualityStop` (`engine.py:3038-3041`). One bad filter ends the night even though the others were being accepted.
- "A pass of rejects removes the target for the night" already exists today, whenever every pending step trips its guard. `_frames_done` counts accepted frames only (`engine.py:4733`), so the anti-spin test at `engine.py:2818` sees a pass that took nothing.

Every one of the three designs relied on this bound. The fix has three parts, and it is the only edit to `_run_step`; the gate stack at `engine.py:2870-2954` is not touched:

1. Count a reject toward `taken_this_visit`, which is what the comment already says. This is the fix for the present-day harm.
2. Needed **because of part 1**. Keep the per-step consecutive-reject counter across visits, keyed by `target.id:step.id` and reset on an accepted frame. Today it is a local that restarts at every call (`engine.py:2859`). Once part 1 bounds a visit at `per_visit` attempts (usually 1), a local counter could never reach the threshold, and the guard would stop working in cycle mode. When the carried counter trips, the step is **set aside tonight**: it is excluded from `_run_steps`' pending list, logged, recorded in `Session.set_aside`, and still owed in the ledger. A panel whose remaining steps are all set aside is set aside.
3. Also needed **because of part 1**. The intra-target anti-spin compares an exposure counter, not `_frames_done`. Today a pass must shoot 10 rejects per step before it takes "nothing". After part 1 a single reject per step would do it, and one cloudy pass would remove a single target for the night.

This changes behaviour for existing accepted-mode cycle plans, and each change is an honest fix. A rejected frame no longer holds the filter or ends the night, and the per-step guard keeps working once the visit is bounded.

### 5.4 Quotas per panel per filter

- Each panel's step for a slot has `count = cycles x perCycle` and `per_visit = perCycle` (`to_plan.py:554`).
- With `counts = Accepted subs`, `plan.count_mode = "accepted"`: rejects are recorded in the ledger and never advance the count. `_step_complete` asks `session.accepted(step.id)` (`engine.py:2762-2774`).
- S0 folds `_target_complete` (`engine.py:2179-2182`) onto `_step_complete`, so selection and completion share one definition of done. Today they agree only by construction (#158).
- The step and night reject guards apply. `quota_unbounded` gates every start path. DUSK's "Dawn" stop gives every panel a stop boundary, which satisfies it.

### 5.5 Worked example

A 3x2 mosaic on the shipped default cycle (`nodes.py:178-185`: L R G B at 60 s, Ha OIII SII at 180 s, 45 cycles, `perCycle` 1):

| Quantity | Value |
|---|---|
| one pass | 4 x 60 + 3 x 180 = **780 s** (13 min) |
| per panel | 7 filters x 45 = 315 subs = **9.75 h** |
| whole mosaic | 6 x 315 = **1890 subs**, **58.5 h** of exposure |
| visits at 1 pass per visit | 6 x 45 = **270** |
| nights of 7.5 h | about **7.8**, before hops |

That is a multi-night campaign. The modal, the brief and Tonight say so.

### 5.6 The hop (per-panel setup)

`_setup_target` (`engine.py:2314-2508`) serves every acquisition. The pieces that make hops cheap and safe are general fixes (S1). Only the checks marked [group] are group-specific.

1. `_progress_expected = False`. Then `_safety_gate(context="slew")`, the sun cone, and `check_slew_limits` (`engine.py:4463`), all unchanged. Every hop is a slew through the one motion path.
2. **Stand the guider down first**, bounded, via `_stand_down_guider` (`engine.py:4394`). The native `start_guiding` returns at once while its loop is alive (`guide/native.py:734-740`), so the next target could inherit a loop still chasing the old star and skip the pier-side calibration mirror (`native.py:871`). This is PLAUSIBLE and unverified on the rig. A simulator test must demonstrate it before the fix (I-02).
3. `hub.goto_and_center(ra, dec, tolerance_deg = tol/60, max_attempts = tries, rotation_deg = target.rotation_deg)`, bounded by `GOTO_TIMEOUT_S` plus 300 s when rotating (`engine.py:185`, `:2355-2362`).
   - The hub already accepts tolerance and attempts (`hub.py:6387-6391`). The engine has never passed them (`engine.py:2357-2359`), so every SLEW + CENTER tolerance anyone typed was ignored. That is a present-day defect and is filed on its own.
   - `None` keeps today's exact call.
   - The tracking-refusal recovery must report what it measured. Today `_setup_target` replaces the recovery's outcome with a fabricated `{"centered": True, "error_arcmin": None}` (`engine.py:2373`), although the recovery ran its own `goto_and_center` and may have logged that it did not converge (`engine.py:6798-6809`). S1 makes `_recover_from_tracking_refusal` return that result, and `_setup_target` uses it, so `centered` is always a measurement.
   - The hub rotates before it centres (`hub.py:6448-6475`). A new shortcut (S2) skips the rotate solve when the rotator is calibrated, has not moved, and already reads within tolerance of the target mod 180.
4. **[group] Centring and angle checks.**
   - `centered: False` under `require_centred` raises `PanelDeferred`.
   - With `rotate` set, `rotation_skipped` raises `PanelDeferred`, and so does the new `rotation_unavailable` flag, which the hub sets when a rotation was asked for and no rotator is connected. Today that case is silent (`hub.py:6455-6456`, I-15).
   - **On every hop, whatever the rotator state**, the engine compares the sky angle this centring solve recorded (`sky_angle.note_solved_rotation`, `sky_angle.py:228`; its `exposed_at` must be at or after the hop start) with `group.pa_deg`, mod 180. Both are CROTA2-convention numbers, so the check does not depend on #145.
   - Beyond `angle_tolerance_deg`:
     - rotate mode: `PanelDeferred`
     - camera-fixed mode: the whole group is set aside at once, because a fixed camera cannot fix itself. The warning alert names both numbers: "the camera reads PA 37.2 and this mosaic is laid out at 30.0; turn the camera or re-frame at the measured angle".
   - **No fresh measurement.** The hop may yield no sky angle at or after the hop start. That happens when the centring solve failed and the hub fell back to a raw GoTo (`centered: False`, `error_arcmin: None`), when the solver reported no rotation (`rotation_known` false, which `sky_angle.py:252-253` now drops, #146), or when the hop came through the tracking-refusal recovery without a solve. Then:
     - With `require_centred` (the default), `centered: False` has already raised `PanelDeferred`, so the case never reaches the angle check.
     - Camera-fixed mode, once the angle has been verified on an earlier hop tonight (`_GroupRun.angle_verified`): shoot, and log "angle not re-measured on this hop; the camera is fixed and read 30.4 at 1-2". A fixed camera cannot turn between hops.
     - Camera-fixed mode, never verified tonight: `PanelDeferred("angle not measured")`. Tiles are never laid blind.
     - Rotate mode with a calibrated rotator that reports its target position and no `rotation_skipped`: shoot with a warning. The rotator's own read is the evidence.
     - Rotate mode otherwise: `PanelDeferred`.
   - "Shoot anyway" turns these into warnings.
   - A 1x1 block with a planned angle only ever warns.
5. **[group] Pier-side verification** (section 5.7). If the side read after the goto contradicts the group's side, raise `PanelDeferred("the mount chose the other pier side")`.
6. **Focus**: autofocus runs when `autofocus_first` is set. With `autofocus_skip_if_fresh`, the sweep runs only when `_hop_focus_is_owed()`. The model is `_post_flip_focus_is_owed` (`engine.py:7303-7348`), but its age rule cannot be copied as it stands:
   - `_post_flip_focus_is_owed` sweeps whenever the last sweep is `FRESH_FOCUS_S` (30 min, `engine.py:151`) old, **before** it looks at the temperature (`engine.py:7324-7326`), and `refocus_on_temp_delta_c` defaults to 0, which is off (`config.py:991`). On a rotating mosaic, where one visit plus its hop takes about 16.7 min, that is a 7-9 minute sweep about every second hop: computed, 19% of the night for an 8-minute sweep.
   - A hop does not move the focuser and does not change the tube's temperature. The frame loop already owns drift through `_refocus_due` (`engine.py:6113-6135`: every N frames, or a temperature delta), and that runs at every frame boundary inside a visit. So a hop owes a sweep only when the frame loop would have refocused anyway, or when nothing trustworthy exists:
     - no successful sweep tonight, or the last sweep failed (`_last_focus_at` cleared)
     - `_refocus_due()` is true now (`autofocus_every` reached, or the temperature delta, when armed, exceeded)
     - the group's first acquisition tonight
   - There is no age rule for hops. A rig with neither `autofocus_every` nor a temperature delta armed then focuses once per night on a mosaic. That is exactly what one long single target does on that rig today, and the RUN section says so (`focus: a sweep only at the first panel; set a temperature delta to refocus as the night cools`).
   - `_post_flip_focus_is_owed` itself is unchanged.
   - A skip is logged: "focus reused: swept 14 min ago, 0.3 C since".
   - `_recentre_after_unguided_focus` still runs after any sweep.
   - Temperature and HFR refocus keep firing at frame boundaries.
7. **Guiding** restarts with the existing bounded start and its escalations (`engine.py:2450-2500`). The native guider mirrors its calibration on a pier change inside that start.
8. `_arm_meridian_flip(target)` (`engine.py:2510-2561`) re-arms the latch as a backstop.
9. **Dither**: `_frames_since_dither = 0` on every acquisition. A fresh centre is a new pointing, so its first frame spends no dither. Today the counter spans targets (`engine.py:2895-2914`, I-21).
10. Record the hop's wall time as event cost `"hop"` (`_record_event_cost`, `engine.py:956`). Set `_progress_expected = True` and re-anchor the no-progress clock.

Cloud holds and safety pauses return through the current panel's `_setup_target`, as they do for any target today (`engine.py:3783-3789`, `:4109-4112`), with focus freshness applied.

### 5.7 Meridian flip: at most one pier change per group per night

**Keep the flip-owed invariant armed per target.** `_pre_flip_side` becomes a dict keyed by target id (`engine.py:613`, `:827`, `:6415-6436`), bundled with #136 as a comment there rather than a new issue. A panel re-acquired in the flip-lead window keeps its own pre-flip record. That window is where the AM5 stays on the pre-flip side, because it picks its side from the hour angle (`hub.py:6582-6585`). A's reset-on-acquisition would have erased the backstop in exactly that case.

**Hysteresis.** For each member p, let `h_p = schedule.hours_to_meridian_flip(p.ra, lon)`. It is positive east of the meridian and at or below 0 after the crossing (`schedule.py:517`). Let `hop_h` be the hop EMA (150 s until measured), and `f_h` the first owed frame of that panel: exposure plus per-frame overhead EMA plus `FLIP_FRAME_MARGIN_S` (30 s, `engine.py:170`). Let `lead_h` be the **plan's** flip lead, `plan.meridian_flip_lead_min` (default 10 min, `schedule.py:562`), in hours.

| Group state | Eligible | Waiting (wake time) |
|---|---|---|
| not flipped tonight | pre-flip panels with room for a hop and one frame before their flip point: `h_p - lead_h >= hop_h + f_h`. The visit carries `deadline_ts = now + (h_p - lead_h)`, so it ends at a frame boundary before the flip point (5.3). Post-meridian panels (`h_p <= 0`) only while no pre-flip panel is eligible. | panels with `h_p > 0` and `h_p - lead_h < hop_h + f_h`. Wake at `now + h_p`, the crossing. |
| flipped tonight | post-meridian panels (`h_p <= 0`) | every other panel, until it crosses. Wake at `now + h_p`. |

**The margin is the plan's lead, never the learned one.** `_flip_lead_s` (`engine.py:5485-5519`) returns 0 in two cases: for a target whose early flip changed nothing (`_flip_no_op`), and for a mount that has been shown this before (#127, `_mount_flips_early() is False`). A zero is right for *when to attempt* a flip, since the AM5 cannot flip before transit. It is wrong as a margin, because the AM5 stops tracking 4.7 to 7.6 min **before** transit (measured on two targets, recorded at `engine.py:5273-5276`). With a zero margin, a pre-flip visit would run into the mount's own limit. The group reads its margin through `_group_flip_margin_s()`, which clamps `plan.meridian_flip_lead_min` the way `_flip_lead_s` does and never consults `_flip_no_op` or the learned trait. Test: with the trait set to "cannot flip early", a panel 8 min before transit is not eligible. Mutant "use `_flip_lead_s`" makes it eligible.

- The group becomes **flipped** when a member is acquired with `h_p <= 0` and the pier-side read after the hop confirms the new side, or when the latch flips a member mid-visit because a prediction was wrong.
- The latch stays armed per panel as the backstop.
- The rule is disabled for a panel when `flip_can_be_skipped(dec, lat, side)` (`schedule.py:856`) says the mount can track through, and for the whole plan when `plan.meridian_flip` is off.
- The side change always happens through the hop's goto. It is **verified by measurement** (step 5 of 5.6), never assumed, which is what the hub itself does after a flip (`hub.py:6577-6609`).
- After a flip the rotator is never turned 180 degrees: a centred rectangle turned 180 degrees has the same footprint (`rotation.py:26-30`).
- **Cost 1, the pre-flip idle.** Nothing in the group is eligible from the moment the last pre-flip panel runs out of room until the first panel crosses. That lasts `max(0, lead_h + hop_h + f_h - span_h)`, where `span_h` is the RA span of the live panel centres in sidereal time. If a visit had to fit whole (no deadline), it would last `max(0, visit_h + lead_h - span_h)`. Computed in Appendix A.4: for a 3x2 of 2.0 x 1.33 deg at Dec 41 it is 0 min with the deadline, against 10.7 min (1 pass per visit) and 24.9 min (2 passes) without it. For a compact 2x2 of 0.9 x 0.6 deg it is 10.6 min, against 23.1 and 37.3 min. The idle is shown in the modal (2.4 RUN). It is park-held on the idle clock (5.1), and under the default `whenWaiting` a follower may fill it in a bounded visit (1.6).
- **Cost 2, unevenness after the flip.** Once the group has flipped, only crossed panels are eligible, so the first panels to cross are visited again while the rest cross one by one. That window is the RA span of the panel centres: computed, 16.1 min for a 3x3 of 2.0 x 1.33 deg panels at 25% overlap at Dec 41, 24.7 min at Dec 60 and 49.4 min at Dec 75. Least-complete ordering evens it out over later passes.

### 5.8 Stop windows and holds

- Every panel's `[start, stop]` window is frozen at run start (`engine.py:1897-1903`). Panels share the flow's dusk/dawn block, so dawn ends them all at the same frame boundary, and `max_run_min` acts as a group budget measured from run start (`schedule.py:417`).
- A rising panel is simply "not ready" until it clears `min_altitude_deg`.
- A setting panel with `on_floor = advance` is set aside tonight.
- Holds are not pauses (`sequence/models.py:133-152`). The dawn stop, the safety gate and the deadman stay armed through hops, waits and deferrals.

### 5.9 Resume mid-mosaic and across nights

**Within a session** (a crash, `/recover`, or auto-resume):

- Every banked frame is an atomic ledger save (`engine.py:4710-4782`). `done_map` seeds `_done` (`engine.py:806`, `session.py:154-163`).
- The first pass order is recomputed from the ledger, so there is no cursor to lose. A crash mid-visit loses at most the exposure in flight.
- ResumeArm `_recover` re-centres on the panel the shared order function picks, **with its rotation**. Today it takes the first non-calibration target even when that target is complete, and passes no rotation (`resume_arm.py:602`, `:640-650`, I-13).

**Across nights (CONTINUE).** `run_flow` compiles with `flow_id` and looks for the newest session with `origin == "flow"`, `origin_id == flow_id` and `status == "dormant"`. If that session's plan shares step ids with the new compile:

- `run_flow` applies the id-safe plan **replace**, factored out of `patch_session` (`app.py:5390-5398`) into a function. It is not a merge, and neither is today's code: `patch_session` computes a kept/new/dropped report and then replaces `s.plan` wholesale. The refusal on dropped steps below is **new logic** in CONTINUE. `patch_session` keeps reporting and never refusing.
- It then calls `engine.start(replan_cooling(plan, ...), session=s)`, the same call resume makes (`app.py:5350-5377`).
- The response names the session, the night number and the kept/new/dropped counts.

**The critical section.** Today `patch_session` loads (`app.py:5385`), checks for dormant, and saves (`app.py:5433`) in separate `to_thread` calls, outside `SessionStore._write_lock` (`session.py:243`). ResumeArm can start the same session in between (`resume_arm.py:396`), and the save then overwrites the file of a now-active session with stale frames. That is the one-starter race recorded on 2026-09-18, and CONTINUE must not inherit it. CONTINUE therefore:

1. Never saves the patched session itself. `engine.start(session=s)` persists it, as resume does.
2. Holds the store's write lock across the sequence "re-read the session, check `status == "dormant"`, replace the plan, `engine.start`". `engine.start` is synchronous and refuses "already running" (`engine.py:747-895`), so ResumeArm either sees the session active or finds the engine busy.
3. Re-reads the session inside the lock, so frames banked by a ResumeArm run that ended between the route's first read and the lock are kept.

The `patch_session` race itself is a present-day defect and is filed on its own.

**Other cases**

| Case | Behaviour |
|---|---|
| steps that hold frames would be dropped (re-framed, or exposure changed) | 409 unless `accept_dropped`: "212 subs belong to steps this flow no longer has; they stay on disk". A step is **not** counted as dropped when its old target id is in the new group's `skipped_ids`. Skipping a panel is not a change of identity (2.5), and re-enabling it brings the same step ids back, with its frames, because the ledger counts by step id. |
| the dormant session shares **no** step ids with the compile and holds frames, because it was saved before S1 with uuid4 ids (`sequence/models.py:19`, `:80`, `:226`) | 409 `{"adopt": {...}}` rather than a silent fresh start, which would disarm it (`engine.py:795-803`). The UI offers ADOPT or START OVER. ADOPT rewrites the session's step ids to the deterministic ones, under the write lock and after a `.bak` copy. The match is **unique** on (target name, frame type, filter, exposure, gain, binning). Pre-S1 flows carry no mosaics, so this is a one-target-per-node match. Unmatched or ambiguous steps stay as they are and are listed. |
| the compile's `count_mode` differs from the session's | 409 unless `accept_recount`: "this session counted every sub taken (412); counting accepted subs makes it 371". The ledger is counted by the frozen plan's `count_mode` (`session.py:130-134`), so changing `counts` mid-campaign recounts every banked frame retroactively. |
| `body.fresh = true` | START OVER: a fresh session. The UI puts it behind a confirm. |
| no dormant session | fresh, as today (`app.py:5198`) |
| a **complete** session whose flow now owes more (counts raised) | fresh, plus follow-up issue I-30 ("reopen a complete flow session") |

Every existing guard still applies to a continue: `plan_identity_errors`, `quota_unbounded`, the per-panel horizon check and the sun check.

Button copy, from the session (slice S5): `CONTINUE M31 MOSAIC (night 3, 412/1890 subs)`, with START OVER behind a confirm.

One starter per session still holds. `engine.start` refuses "already running" to the second of ResumeArm and Run (`engine.py:747-895`). An armed auto-resume replays the dormant session's frozen plan, so the editor says so: "the armed session will replay the version from 2026-09-22; press CONTINUE to apply your edits".

### 5.10 Published state and ETA

- `_set_state` (`engine.py:1250`) gains `group = {id, name, mode, pass, panel: "2-3", visit_elapsed_s, panels_done, panels_total, set_aside: [{panel, reason}]}`. It is present only while the active target belongs to a group, so existing payloads stay byte-identical. The reasons are words only.
- Times to set and meridian waits go only to a `CAP_VIEW_SITE_DERIVED`-gated topic.
- For a principal without `CAP_VIEW_SITE_DERIVED`, the `panel` and `pass` fields are withheld while the group waits on the meridian rule, and the hop that ends that wait is not announced by panel label. The timing of that hop is the crossing (6.9).
- `compute_eta` (`engine.py:988-1038`) adds `remaining_visits x hop EMA`, and says "hops not yet costed" when no sample exists.

---

## 6. Safety and failure handling

| # | Hazard or failure | Behaviour |
|---|---|---|
| 6.1 | A hop moves the mount | Only through `_setup_target`, and so through the safety gate, the sun cone (checked again inside `goto_and_center`), `check_slew_limits` and a bounded goto. Nothing new moves the mount on any other path. |
| 6.2 | A panel behind the horizon or obstruction mask, the floor, a wedge or the pier limit | Waiting at selection (5.1). The existing wait path sleeps; there is no busy loop. While nothing is shootable, the mount is park-held on the idle clock, and the tracked target's floor and flip point are checked every tick (5.1). A verdict waiting cannot change is a refusal. If the gate still raises at the slew (a race of seconds), `SafetyAbort` ends the run as today. |
| 6.3 | Start guard | Sun: any panel in the cone refuses the start, always, force or not (`app.py:3409-3422`). Horizon: refuse (unless force) only when **every** panel is blocked now, and list them. Otherwise start, and name the blocked panels in the response and the log. This replaces the all-or-nothing loop at `app.py:5175-5189`. |
| 6.4 | Termination | Six independent bounds: the zero-exposure pass (5.1); `max_failed_visits` per panel with a 300 s `_wait_until` between all-deferred passes; the per-panel reject rule (3 visits that accept nothing while others accept, 5.1); the per-step reject guard, now spanning visits (5.3); the frozen stop boundary; `quota_unbounded` on every start path. A follower never holds the cursor past `group_ready_ts` (1.6). |
| 6.5 | Per-frame gates | No second frame loop. Visits call `_run_step` with its whole gate stack (`engine.py:2870-2954`). |
| 6.6 | Watchdog | `_progress_expected` is False from the start of the hop to the first exposure (`engine.py:2317`, `:2508`). |
| 6.7 | Set aside is not done | Window closed, floor advance, guiding skip, repeated centring or rotation failure, an angle failure, a panel that rejects alone, a step reject guard, a pier change with flips off: all set aside for tonight. The ledger decides completion. The session stays dormant and armed. Set-aside records are kept in `Session.set_aside` for the night, so a crash-resume does not retry them. The report names every set-aside panel and its reason. |
| 6.8 | Star-poor panel | Deferred for at most `max_failed_visits` consecutive passes, then set aside with a warning alert that names it. It never becomes a silent coverage hole. A panel that starves every night gets a Campaign row naming the starvation (follow-up I-31). |
| 6.9 | Privacy | The setting-first order, the meridian rule and per-panel altitudes use the site inside the engine only. The log ring and the night log speak in panel labels and words: "2-1 first: sets soonest", "1-3 waits for the meridian". Minutes to set, meridian times and altitudes go only to a `CAP_VIEW_SITE_DERIVED` topic (the issue #19 class: a computed value can reveal the site). The progress route carries no site data (`CAP_VIEW_STATUS`). The modal's altitude column, `MosaicNightCard` and Tonight stay gated and hidden for a viewer. **Timing is a channel too, and words alone do not close it.** `/api/logs` is `CAP_VIEW_STATUS` (`app.py:8046`), so a viewer can read it. A line logged when a panel is acquired at its computed meridian crossing timestamps the transit of a known RA. That gives the LST, and the LST gives the longitude, which is why `redact.py:64-88` strips `hours_to_flip`. Rule: a log line or state change whose **time** is set by a site computation (a meridian wait ending, a group flip, a flip at the crossing) carries `site_derived=True`. `/api/logs` and the log topic drop it for a principal without `CAP_VIEW_SITE_DERIVED`, and `state.group` withholds `panel` and `pass` across it (5.10). The night log file keeps everything. Residual: the first frame after a meridian wait still lands at crossing plus hop plus exposure, which is coarse because the hop varies. The privacy issue records that residual, and today's flip lines (#127) and the "holding for the meridian flip point (N min)" state detail (`engine.py:5546-5547`) leak the same way now. |
| 6.10 | Guider churn | Every hop stops guiding and then starts it again. The bounded start and its warn/skip/abort escalations apply per panel: skip sets that panel aside, abort ends the run. Hops multiply guider starts on the #72/#134/#135 path, so S2 includes a simulator test that forces a guide-start failure mid-mosaic. |
| 6.11 | Meridian | At most one pier change per group per night (5.7). The flip-owed invariant is kept per target. There is never a 180-degree rotator turn. |
| 6.12 | Angle | A mosaic cannot run at "any angle" (M2). The measured angle is checked on every hop, whatever the rotator state (5.6). The rotate loop keeps abandoning a non-improving attempt (`hub.py:6317-6334`). |
| 6.13 | Identity | `plan_identity_errors` on every start path. Duplicate step ids would let one panel's frames count for every panel, and in accepted mode panels 2 to N would read complete at once. |
| 6.14 | Optics drift | The compile never re-tiles. A warning above 2%; a loss (blocks `/run` until accepted) when the live field would leave gaps (M5). |
| 6.15 | Operator STOP | Unchanged: `POST /api/sequence/abort` disarms auto-resume (`engine.py:1789`). The backlog report of an abort followed by an automatic restart 50 s later (`docs/superpowers/backlog/2026-08-22-flow-editor-offers-what-the-engine-refuses.md:74-79`) must be re-checked against the fix for #93 (closed) before anyone claims it. |
| 6.16 | Downgrade | See 3.6. |
| 6.17 | Idle mount | A mount left tracking with no frame loop watching it is park-held on the idle clock (`WAIT_TEARDOWN_S` since the last exposure or hop), or at once if the tracked target reaches its floor or its flip point first (5.1). This closes a present-day hole for eta-0 and constraint waits as well as the mosaic's reach waits. |
| 6.18 | Follower | A later target shot while the mosaic waits never keeps the cursor past `group_ready_ts` (1.6). |

---

## 7. Reuse (with paths)

**Engine** (`server/astrodeck/sequence/engine.py`)

- `_run_scheduled` (1866-2060): frozen windows (1897), selection (1924), StopTarget and JumpTarget handling (1994-2010), `_apply_jump` (2062), `_pending_skips` (1911-1919), the wait path (2013-2058).
- `_run_steps` (2776-2824) and its anti-spin rule (2818-2824), gaining `VisitBound`.
- `_run_step` (2826-3054) as the only frame loop.
- `_step_complete` (2762-2774); `_target_complete` (2179-2182), folded onto `_step_complete`.
- `_setup_target` (2314-2508); `_arm_meridian_flip` (2510-2561); `_enforce_flip_owed` (6354-6436).
- `_stand_down_guider` (4394); `_park_hold` (4437); `check_slew_limits` (4463) and `_enforce_mount_floor` (4482), the source of the new non-raising verdict.
- `_post_flip_focus_is_owed` (7303-7348) and `FRESH_FOCUS_S` (151), generalised into `_focus_is_owed`.
- `_record_event_cost` (956); `compute_eta` (988); `_wait_until` (2133); `start(session=)` (747-895); `_set_state` (1250).

**Session and models**

- `sequence/session.py:110-163`: `accepted_by_step`, `recorded_by_step`, `owed`, `done_map`.
- `sequence/models.py`: Target (78-105; `mosaic_group` 103 is reused as the group key), `count_mode` (309), `quota_unbounded` (402-435).
- `sequence/schedule.py`: `gating_status` (702-778), `resolve_window` (417), `schedule_order` (794-806), `hours_to_meridian_flip` (517), `flip_can_be_skipped` (856).
- `sequence/resume_arm.py:599-662`.

**Hub, rotation, sky angle**

- `hub.py`: `goto_and_center` (6387-6391, already takes `tolerance_deg` and `max_attempts`), the rotate branch (6448-6475), `rotate_to_pa` (6166) and its abandon rule (6317-6334), `meridian_flip` (6577-6609), `effective_optics` (1987), `status.sky_angle` (7001).
- `sky_angle.py:228`; `rotation.py:26-30`; `guide/native.py:734-740`, `:871`.

**Framing**

- `catalog/framing.py`: `MosaicSpecIn` (52-83), `project`/`deproject` (100-174), `compute_mosaic` (176-231), `_stamp_transit_alt` (248), `POST /api/framing/mosaic` (317-347).
- Golden vectors: `server/tests/test_catalog_frame_id.py`, `ui/src/lib/__tests__/framing.test.ts`.
- Client mirror: `ui/src/lib/framing.ts:91` (`fovFromOptics`), `:216` (`mosaicGrid`).
- `catalog/visibility.py:760-789` keeps working, because `mosaic_group` is kept.

**Flows**

- `flows/compile.py`: pool expansion (188-215) as the one-node-to-many template, `_trigger_for` (124-164), `flow_order` (59-101).
- `flows/to_plan.py`: `_cycle_steps` (515-558), the rotation sentinel (1075-1091), `HONOURED_BY` (156-160), rig-fact injection (1034-1051), `blocking_reasons`/losses (1184-1232).
- `flows/doctor.py:40-63` (the wire walk for ownership).
- `flows/models.py:67-88` (`with_defaults`), `flows/store.py:52-105`.
- `flows/tonight.py:835-928` (the Campaign row pattern).
- `api/app.py`: `_compile_payload` (4757-4812), `run_flow` (5098-5198), resume (5350-5377), id-safe merge (5382-5398), `_horizon_block` and `_solar_block` (3386-3422).

**UI**

- `components/atlas/SkyCanvas.tsx`, `RotateHandle.tsx`, `PointingFrame.tsx`, `TileEngine.tsx`.
- `lib/atlasFov.ts:151`, `:196`; `lib/effective.ts`.
- `next/hubs/sky/frame/MosaicNightCard.tsx`, `components/atlas/mosaicNightSummary.ts`, `next/hubs/sky/frame/mosaic.ts:70` (`fetchPanels`, server then mirror), `next/hubs/sky/frame/FrameTools.tsx`.
- `components/flows/QuickFlow.tsx:54-90`, `:346-351`.
- `components/flows/FlowInspector.tsx:146` and `next/.../inspector/FlowNodeEditor.tsx:84` (the per-type slot).
- `components/flows/TonightCampaign.tsx:502-578` (per-panel rows).
- `components/flows/FlowCyclePlan.tsx` and `cyclePlanRows.ts` (rows fed by the rig).
- `components/Overlay.tsx`; `components/sky/ClassicAtlasSky.tsx:130-147` (the local-state precedent).

---

## 8. Build plan

Every slice ships on its own, keeps every existing plan and flow byte-identical except where a fix is named, and names the mutant each new test kills. A test that still passes with its branch deleted is not a test. Subagents write code and never commit; the operator commits with explicit pathspecs. The issues in section 9 were filed on 2026-09-23 (#147 to #188, plus comments on #132, #136, #141 and #142), and the build is tracked in #189. Each slice closes the issues it names.

### S0: defects the mosaic stands on (no mosaic yet; ship at least one release before S3)

1. `flows/store.py`: FLOW_SCHEMA 3, the 23.4 migration with its note, future-schema refusal, visible rows for unreadable files, and the writer stamping 3. Palette default rotation -1 (`nodes.py:117`, `nodeDefs.ts:199`). The golden `server/tests/fixtures/flow_plan_golden/ngc7331_quick.json:71` is updated deliberately (23.4 becomes null).
2. `FlowGraph.validation_errors` refuses flow back-edges, with the same refusal in both drop resolvers. Correct the `compile.py:72-75` docstring.
3. `flowsConnect` replaces the existing wire only on **flow** inputs (`flowsSlice.ts:298-307`).
4. `plan_identity_errors` on all five start paths.
5. Fold `_target_complete` onto `_step_complete`.
6. The accepted-mode visit fix, in three parts (5.3).
7. The idle-clock park-hold in `_run_scheduled` and `_wait_until` (5.1): `_idle_since`, the three per-tick checks against the tracked target, and the latch. It also covers today's eta-0 and constraint waits.
8. `_setup_target` takes the tracking-refusal recovery's own centring result instead of the fabricated `centered: True` (`engine.py:2373`, 5.6 step 3).

Tests:

- A v2 file with 23.4 migrates, with the note; with 30 it does not. Mutant "migration keyed on `!= 23.4`" fails.
- A v9 file is listed as unreadable and is never loaded. Mutant "drop the refusal" loads it.
- A drawn loop is refused at save, at `/run`, and by both UIs' drop tests. Mutant "remove the check" compiles a zero-frame plan that the doctor calls clean.
- In a copy of the campaign example, both wires into `calib.do` survive a new wire, while a flow input is still replaced. Mutant "filter on any input" fails.
- Duplicate step ids get 422 on `/api/sequence/start` and `/run`, 409 on resume, and a refusal from recover and ResumeArm. Duplicate names are allowed with a warning when there are no groups and no named instructions. A stored session with duplicate names still appears in `load_all`. Mutant "model validator" makes it vanish.
- Accepted mode with a regraded frame: `_target_complete` and `_step_complete` agree.
- Accepted-mode cycle with injected rejects:
  - one always-rejecting filter among accepting ones: a visit ends after `per_visit` attempts, and the other filters' frames interleave at one reject per round. Today it is 10 rejects per round. Mutant "no increment in the quota branch" fails.
  - the same plan with `max_consecutive_rejects = 0`: no `NightQualityStop` while the other filters accept. Mutant "no increment" raises it after 20.
  - consecutive rejects span visits, and the step is set aside after `max_consecutive_rejects` of them. Mutant "local counter" never trips.
  - a pass of all rejects does not end the cycle; mutant "compare `_frames_done`" fails
- Idle-clock park-hold on the clocked simulator:
  - a setting target below its gate with the window open gets `set_tracking(False)` within `WAIT_TEARDOWN_S` plus one tick. Mutant "teardown keyed on `wait_ts - now`" keeps tracking.
  - a constraint wait park-holds the same way.
  - a tracked target that reaches its floor during a 60 s recheck loop is park-held at that tick.
  - the latch issues `set_tracking(False)` once per idle spell, not every 5 s.
- The tracking-refusal recovery that fails to converge leaves `result["centered"]` False. Mutant "fabricated True" fails.

### S1: identity, centring, hop hygiene, progress (useful to single targets on its own)

1. The new Target fields. `_setup_target` passes tolerance and attempts only when they are set (the call is otherwise identical).
2. Guider stand-down before every slew, after the issue's simulator test has demonstrated the defect.
3. `_pre_flip_side` keyed per target (with #136).
4. Dither counter reset on acquisition.
5. `_hop_focus_is_owed` (5.6 step 6): no age rule for hops; owed only on no good sweep tonight, a failed last sweep, `_refocus_due()`, or the group's first acquisition. `_post_flip_focus_is_owed` is unchanged.
6. Hop event cost and the ETA term.
7. `to_sequence_plan(flow_id=)`: deterministic ids for targets, steps and pool members.
8. `run_flow` CONTINUE, dormant sessions only (5.9): the factored plan replace plus `engine.start(session=)` inside one write-locked section that re-reads the session and never saves it separately; `accept_dropped` (skipped panels exempt), `accept_recount`, `fresh`, and the ADOPT path for pre-S1 sessions.
9. `GET /api/flows/{id}/progress` (`CAP_VIEW_STATUS`): per block, per panel, per step banked/owed from the newest session of that flow, plus orphaned counts. The card chip for single targets.
10. The hub's `rotation_unavailable` flag.
11. An angle-check helper fed by `sky_angle`, not yet called by groups.

Tests:

- Two compiles of one flow give identical ids. An exposure change changes only that step id; a count change changes none. A different `flow_id` gives different ids. Mutant "uuid4" fails the continuity test.
- A second `/run` on the simulator banks on the same ledger entries. Dropping steps that hold frames gets 409 unless accepted. `fresh` starts a new session.
- Skipping a panel with banked frames and pressing CONTINUE does not 409, and re-enabling it restores its counts. Mutant "skipped panels count as dropped" 409s.
- A pre-S1 dormant session (uuid4 ids, frames banked) gets the 409 adopt answer, never a silent fresh start. ADOPT maps unique matches and lists the rest, and the `.bak` exists. Mutant "fresh on no shared ids" disarms it.
- A `counts` change against a session gets 409 with both totals unless `accept_recount`.
- CONTINUE and a ResumeArm start fired together (a barrier in the test, not a sleep) leave one run and a session file whose frames include every frame the winner banked. Mutant "save before start, outside the lock" loses frames.
- `goto_and_center` receives `0.5/60` when the target says 0.5 arcmin, and exactly today's arguments when unset.
- The guider stop is observed before the slew.
- Focus at a hop, on the clocked simulator: skipped 35 min after a good sweep with the temperature delta armed and unmoved; run after a temperature move; run on the group's first acquisition; run after a failed sweep. Mutant "30-minute age rule" sweeps at the 35-minute hop. Mutant "always sweep" fails.
- Re-acquiring target A in the lead window after target B keeps A's record. Mutant "engine-wide slot" fails.
- The first frame after an acquisition spends no dither.
- A viewer can read the progress route, and it carries no site-derived field.

### S2: the engine group driver (the core ultracode task; demonstrable through `/api/sequence/start` with no UI)

1. `TargetGroup` and `SequencePlan.groups`. Membership, eligibility, requeue and pass bookkeeping in `_run_scheduled`. `VisitBound` in `_run_steps`.
2. `sequence/panel_order.py`.
3. The non-raising reachability verdict at selection, tagged `wait` or `refuse`. Meridian hysteresis on `_group_flip_margin_s()` with post-hop side verification, and the visit deadline at the flip point.
4. `PanelDeferred`; set-aside semantics, persisted in `Session.set_aside`; the group anti-spin rule; the per-panel reject rule; the deferral wait; the angle check with its no-measurement cases; deferral on `rotation_skipped` and `rotation_unavailable`.
5. `on_target_complete` gated on panel completion. `after_group` gating. Bounded follower visits (`group_ready_ts`, 1.6).
6. Published group state. ResumeArm using the order function and passing rotation.
7. The hub rotate shortcut. FITS `MOSAIC` and `PANEL` keywords and a `$$PANEL$$` naming token (`naming.py:18-29`). The `types.ts` mirror.
8. **S2b**: a classic Plan "cycle panels each pass" toggle on a `mosaic_group` header emits `groups`, so the Sky copy's promise becomes true on the Plan path first. That copy is corrected to name the Plan, not the flow (I-08).

Tests (`server/tests/test_group_rotation.py`: the real `_run_scheduled` against the simulator hub on a clocked fake time, never sleeps, and doubles never read live device state):

- 2x2, L and R, count 3, `per_visit` 1. The visit order is exactly `1-1(L,R) 1-2 2-2 2-1`, repeated 3 times, in snake order. Every panel completes and `owed() == 0` completes the session. Mutant "remove the requeue" fails.
- `passes = 2` halves the hops. `visit_min_s` extends a visit at round boundaries.
- Seeded uneven progress: least complete goes first, and ties fall to never-visited, then snake.
- The floor with `on_floor = advance` on one panel sets only that panel aside.
- A forced centring failure on panel 3: deferred for 3 passes, then set aside with a warning alert naming it. Mutant "no failure counter" fails.
- A fixed-camera angle error beyond tolerance sets the group aside after the first measurement, and the alert carries both numbers.
- An all-deferred pass waits through `_wait_until`, with the safety gate called.
- A zero-exposure pass ends the group. Mutant "skip the group anti-spin" hits a wall-clock bound.
- A horizon-blocked panel: the number of selection iterations per simulated minute stays under a small bound. Mutant "return waiting after selection" (A's original) blows the bound.
- A meridian straddle on the clocked simulator gives exactly one pier change. A post-hop side mismatch defers. Mutant "no hysteresis" gives more than one change. This proves something only if the simulator mount picks its pier side from the hour angle as the AM5 does (`hub.py:6582-6585`); a test asserts that first.
- With the learned "cannot flip early" trait set, a panel 8 min before transit is not eligible. Mutant "use `_flip_lead_s`" makes it eligible.
- A visit whose next frame would cross its panel's flip point ends at the frame boundary before it. The measured pre-flip idle on the clocked simulator matches the A.4 formula within one frame.
- A follower selected during a meridian wait ends its visit at `group_ready_ts` and keeps its progress. The group then resumes. Mutant "follower runs to completion" never returns to the group.
- A panel that rejects every frame while the others accept is set aside after 3 visits. When every panel rejects, no panel is set aside by this rule. Mutant "count all zero-accept visits" sets the whole group aside in a cloud spell.
- A pier change with flips off sets that panel aside and the others keep shooting. No site with limits configured raises `SafetyAbort`. Mutant "every verdict waits" waits all night.
- A crash-resume the same night does not retry a set-aside panel, and the next night does.
- The no-measurement cases of 5.6 step 4 each have a case: fixed and verified shoots, fixed and never verified defers, rotate with a calibrated rotator warns.
- A guide-start failure forced mid-mosaic: skip sets that panel aside; abort ends the run.
- Accepted mode with a rejecting grader terminates.
- A resume from a mid-group `done_map` picks the least complete panel, and ResumeArm re-centres on it with rotation.
- `on_target_complete` fires once per panel, at completion. Mutant "fire every visit" fails.
- `groups = []` is byte-identical against the golden plans. A `mosaic_group` with no group stays panel-first.

### S3: Flows vocabulary, compile, doctor, Tonight

1. TARGET params and ports, `create_params`, the `pass` outputs, loop-wire consumption, and the `_trigger_for` pass branch.
2. The compile entry (3.2), the `to_plan` expansion (3.3), the panel lane and `owner_of` (1.5), rig-fact injection, FLOW_SCHEMA 4 stamping.
3. Doctor R4, M1-M15 and L1 in the server. Correct the stale docstring at `doctor.py:13-17`; there is no UI copy. `LEGACY_TYPES` hides SLEW.
4. The wizard's mosaic kind, with a lane without SLEW and the loop wire. The angle comes from the operator or USE MEASURED, never a default; the camera field comes from injected optics, and with none the wizard answers with a single target (1.8). The Examples drop SLEW, and an **eighth Example**, "M31 3x2, rotating", joins the acceptance corpus. It must load, validate and run on the simulator (`examples.py:3-7`).
5. The Tonight mosaic branch:
   - one `compute_night` per block
   - per-panel peak altitude through `_stamp_transit_alt` (gated)
   - `_budget` counting every panel, plus hops once measured (`tonight.py:510-555` dedupes them today)
   - a brief sentence
   - Campaign rows per panel from the progress route, with no pool required (`tonight.py:868-871` requires one today)
   - a TIMELINE band drawn from the worst and best panel (the `mosaicNightSummary` reduction)

Tests:

- A golden 2x2 compile and expansion.
- A two-lane flow with a mosaic scopes by wire. With no mosaic, all seven Examples compile byte-identically.
- The four worked cases of 1.5 compile as the table says. `owner_of` is a chain walk: a TARGET -> CYCLE -> DOME -> CAPTURE graph gives M13. Mutant "owner is any upstream TARGET" (the doctor's set walk) hands the CAPTURE to the mosaic.
- A pass wire from a mid-lane stage is M12. Appending a CAPTURE after the tail moves the loop wire to it.
- The wizard with no optics answers with a single target and a reason. It never writes a default angle; a grep test asserts no numeric angle literal in the mosaic kind.
- A v3 flow with no `counts` key compiles to attempts. Mutant "missing-key default Accepted" fails.
- A palette-created block gets Accepted subs and Any angle.
- A mosaic flow stamps 4, and a single-target flow stamps 3.
- M1-M15 each have a positive and a negative case. M6 trips on a 1x4 at 10% overlap at Dec 75 (38.9%) and not on a 3x3 at 25% at Dec 41 (3.1%).
- The loop wire emits no Instruction. A pass wire anywhere else says "will not run".
- The wizard option matrix passes the doctor.
- `nodeDefs` parity.
- Tonight calls `compute_night` once per block (spied). The budget equals panels times the per-panel figure. A viewer gets no altitudes.

### S4: the Target modal and canvas (one shared module, both UIs)

1. `TargetFramingSheet` with every section in 2.4. The local FramingSession.
2. SkyCanvas `panels` and `frameCenter` props; the PanelLayer from server coordinates.
3. `flowsApplyFraming`. `flowsAddNode` returns the id, and dropping a block auto-opens the modal.
4. The Overlay `full` variant; the phone pinned-canvas layout and landscape.
5. The `flowFrame` sheet replacing `flowNode`.
6. The loop back-arc and label chip, the stage-list rail and LOOP PANELS, the card footer and chip.

Tests (tsx scripts exporting `{passed, failed, total}`, with jsdom globals installed before imports):

- Labels sit at the server's coordinates. Mutant "FovOverlay index layout" fails.
- DONE stays locked until the server answers; the offline chip path works.
- One `flowsApplyFraming` per DONE, which means one compile.
- The re-frame confirmation appears only when the progress route reports banked subs, and a skip never asks.
- Any angle is locked on a grid, and Rotate to is locked without a rotator, each with its reason.
- A viewer never sees the altitude column or the night card.
- The back-arc stays below the body cards (a formula test).
- The phone stage list shows the rail, or LOOP PANELS when the wire is absent.
- Opening block A's modal never changes `store.framing`. Mutant "global session" fails.
- Then a probe on the real page with a phone profile (`tools/ui_probe`, visible=true, asserting a view marker), per verify-on-the-real-thing.

### S5: run mode, CONTINUE copy, readouts

The modal's run mode fed by `state.group`. The Run button reads from the session (`CONTINUE ... (night n, done/total)`). The phone monitor's readouts, which nothing writes today (`FlowStagesPhoneSheet.tsx` header), are fed from sequence state for mosaics. Tests use recorded state fixtures, and the button copy test compares against the progress route.

### S6: converge the doors

- Sky FRAME quick and Atlas "send to flow" write a TARGET block with the loop wire. The side channel of Plan targets goes (`next/hubs/sky/sheets/quick.tsx:438-466`).
- Delete the synthetic MOSAIC lane card and the false copy: `FramingCard.tsx:37-43`, `quickCopy.ts:225`, `:237-243`, `flowLane.ts:98-117`.
- One overlap constant.

Tests: the Sky quick path produces exactly one TARGET block and no Plan targets, and a grep test asserts the deleted strings are gone.

### S7: validation before any sky, then one supervised night

1. On the simulator, end to end: a 2x2 rotating, a forced solve failure on one panel, a CONTINUE on a second simulated night, and a meridian straddle.
2. Then one supervised rig night on a bright, low-risk 2x1 or 2x2 near the meridian. Measure the hop cost and set the `passes`/`minVisit` defaults from it.
3. Review the durable night log (`captures/logs/<night>.jsonl`), classifying events by their neighbours.
4. File an issue for every human intervention.

A mosaic has never run on the rig (`docs/reviews/2026-07-30-overnight-systems-test.md:102`).

### S8: gated on #145

- The per-panel convergence correction, `panel PA = block PA + convergence at the panel centre`, in both projections with new golden vectors. The sign comes from the rig measurement.
- Flats keyed by rotator mechanical angle.

---

## 9. Missing features

Tags:

- **ULTRACODE**: build inside the named slice. Tracked together in #189, never filed one by one.
- **FILED #N**: a defect or follow-up found during this design, filed on 2026-09-23 after checking `gh issue list` and scanning each body against the needles file.
- **EXTENDED #N**: a comment with the new evidence on an existing issue rather than a duplicate.

No earlier issue mentioned mosaics. The ones checked were #132, #136, #141, #142 and #145, which are open, and #93, which is closed. The build plan and its order live in the tracking issue, #189.

**Defects (present day unless marked latent)**

| Id | Issue | Gap |
|---|---|---|
| I-01 | FILED #147 (blocks S2) | In accepted mode a visit is not bounded by attempts. The reject branch never increments `taken_this_visit` (`engine.py:3027-3049`, against the comment at `:2862-2866`), so one rejecting filter holds the cycle for up to `max_consecutive_rejects` (10) subs per round, every round. With the step guard off, 20 rejects end the night (`NightQualityStop`). The local counter (`engine.py:2859`) and the `_frames_done` anti-spin (`:2818`, `:4733`) only work because of this defect, so the fix has three parts (5.3). Deterministic. Class: a claim nothing keeps. |
| I-02 | FILED #148 (PLAUSIBLE, unverified on the rig) | The guider is not stood down before an inter-target slew (`engine.py:2314-2509`). Native `start_guiding` is a no-op while its loop lives (`guide/native.py:738-740`), so the pier-side calibration mirror (`native.py:871`) is skipped. Prove it on the simulator first. |
| I-03 | FILED #149 | A drawn flow loop is silently deleted. `flow_order` drops the looped nodes (`compile.py:72-78`), and its docstring says a loop cannot be drawn. `validation_errors` has no cycle check (`flows/models.py:110-171`). The drop resolvers allow the wire (`FlowCanvas.tsx:107-139`). The phone layout strands the nodes (`autoLayout.ts:293-298`). The result is a zero-frame plan with a clean doctor. |
| I-04 | FILED #150 | The TARGET palette default rotation 23.4 commands a connected rotator on every palette, wizard and quick flow (`nodes.py:117`, `nodeDefs.ts:199`; `wizard.py:283-293` overwrites only name and coordinates). The golden fixture pins it (`ngc7331_quick.json:71`). Same class as the rotation-0 migration. |
| I-05 | FILED #151 | Steps are scoped by canvas order, not by wires (`compile.py:233`, `:258`). Which steps a target gets depends on where its card sits, and a target that sorts after every capture is slewed, centred and focused while shooting nothing, silently. |
| I-06 | FILED #152 (both UIs) | `flowsConnect` deletes the existing wire on **event** inputs too (`flowsSlice.ts:298-307`), although the server allows event fan-in (`flows/models.py:149-167`) and the campaign example depends on it (`examples.py:200-204`). |
| I-07 | FILED #153 | The flow store silently skips unreadable, unknown-type and future-schema files (`store.py:93-105`). `_migrate` passes any file at version 2 or above through unchanged (`store.py:67-68`), so a future file with only known node types would run with this build's meaning. |
| I-08 | FILED #154 (a claim nothing keeps) | The Sky copy promises that panels cycle every pass (`FramingCard.tsx:37-42`, `quickCopy.ts:237-243`, `flowLane.ts:115`, `next/lib/fov.ts:7`, `:66-69`). The panels go to the classic Plan (`quick.tsx:454-459`), the flow shoots only the centre, and the engine works panel-first (`engine.py:1964-1965`, `:2010`). |
| I-09 | FILED #155 (two definitions of done) | The POOL `quota` is compiled (`compile.py:199`) and read by nothing (`to_plan.py:186-193`), with no unmapped note. The engine finishes at `cycles x perCycle` (`to_plan.py:554`), while the Campaign tab judges by the pool quota by name (`tonight.py:873-908`). |
| I-10 | FILED #156 (latent) | No server check that target ids, step ids or names are unique (`sequence/models.py:272-374`). The ledger counts by step id alone (`session.py:110-121`), and jumps match the first name (`engine.py:2093`). |
| I-11 | FILED #157 | `on_target_complete` rules run after every `_run_steps` return, including a target that stopped short (anti-spin, reject guard) (`engine.py:1966-1993`). `target_complete=True` is asserted when it is not true. S2 fixes this for panels; the issue covers single targets. |
| I-12 | FILED #158 (latent) | `_target_complete` sums `_done` (`engine.py:2179-2182`), while `_step_complete` asks the ledger in accepted mode (`:2762-2774`). They agree today only because a regrade is refused during a run (`app.py:5449-5452`) and `_done` is re-seeded at start. Any divergence, or one step over its count masking another under it, makes the scheduler skip a target the session still owes. |
| I-13 | FILED #159 | ResumeArm re-centres on the first non-calibration target even when that target is complete, without its rotation, and refuses the whole resume when that one target is low (`resume_arm.py:602`, `:631`, `:647`, `:652`). |
| I-14, I-15 | FILED #160 | Rotation is ignored on the non-centred slew path (`engine.py:2384-2427`), and `goto_and_center` skips rotation with no flag when the rotator is absent or dropped (`hub.py:6455`). The caller cannot tell a rotated panel from an unrotated one. The flip re-centre without rotation (`hub.py:6609`) is harmless by mod-180 equality. |
| I-16 | FILED #161 (doc defect) | The framing spec's meridian warning (`2026-06-15-ux-sky-atlas-framing-design.md:365`) and a comment at `AtlasView.tsx:980-982` say post-flip panels need re-rotating. A centred panel turned 180 degrees has the same footprint (`rotation.py:26-30`). |
| I-17 | EXTENDED #141 | Flows never set `count_mode`, so "until N subs" counts rejected frames (`sequence/models.py:309`; golden `:7`). Closed for new blocks by D8. |
| I-18 | FILED #162 | The run-phase latch: `flows.run.phase` is set to running (`flowsSlice.ts:402`). Only NowEmpty's own RUN press resets it (`NowEmpty.tsx:394-397`), so the classic header reads STOP after one run, and pressing it aborts (`flowRunControls.tsx:100`). |
| I-39 | FILED #165 (safety rides value paths) | The scheduler never park-holds on eta-0 waits (`schedule.py:762`) or constraint waits (`schedule.py:766-775`, `engine.py:2058`). Park-hold keys on one wait interval exceeding 120 s (`engine.py:2052`), so an idle mount keeps tracking and guiding with no floor or meridian check (5.1). |
| I-40 | FILED #166 (privacy) | Viewer-readable logs (`app.py:8046`) timestamp flips taken at the crossing, which yields the longitude. The sequence state's "holding for the meridian flip point (N min)" detail (`engine.py:5546-5547`) and waiting `schedule` block (`:2031`) are not in the redaction tables (`redact.py:130-131`). |
| I-41 | FILED #167 (intermittent) | `patch_session` loads, checks and saves outside the store lock (`app.py:5385`, `:5433`), so it can race ResumeArm starting the same session. CONTINUE must not inherit it (5.9). |
| I-42 | FILED #168 | Tiling uses configured optics, never the solver's measured scale, and the two UIs disagree about the reducer (`ui/src/lib/framing.ts:91-101` against `next/lib/fov.ts:35-39`; `config.py:99-111`). |
| I-43 | FILED #169 (doc defect) | Stale docs: a UI doctor copy that does not exist (`doctor.py:13-17`), an "ungated" framing route that is gated (`01-screen-ia-map.md:152` against `framing.py:317-319`), and a 19-node contract. |
| I-47 | FILED #170 | The engine never passes `tolerance_deg` or `max_attempts` to `goto_and_center` (`engine.py:2357-2359`), so no flow or plan can set its centring. `to_plan` discloses that SLEW's fields are ignored (`to_plan.py:361-376`), but the card still offers them. |
| I-48 | FILED #171 (latent) | After a tracking-refusal recovery, `_setup_target` fabricates `centered: True` (`engine.py:2373`) and discards the recovery's own centring result (`engine.py:6798-6809`). |

**Existing issues extended**

| Id | Issue | Gap |
|---|---|---|
| I-19 | EXTENDED #136 | `_pre_flip_side` is engine-wide (`engine.py:613`, `:827`, `:6419`, `:6485`). Key it per target (5.7). |
| I-20 | EXTENDED #132 | The slew gate SafetyAborts the whole run for a target behind the mask, and the start refuses on the first blocked target. The tagged verdict at selection (5.1) is the per-panel half, and the start guard should list blocked panels (6.3). |
| - | EXTENDED #142 | Every panel hop restarts guiding, and a failed start shoots unguided frames that pass the RMS gate. The owner decides the panel policy. |

**Small follow-ups**

| Id | Issue | Gap |
|---|---|---|
| I-21 | FILED #163 (minor) | `_frames_since_dither` is not reset on a fresh centre (`engine.py:2895-2914`). Fixed in S1. |
| I-22 | FILED #164 | `max_run_min` is not a per-target budget: for a "now" start its clock starts at the run start (`schedule.py:380-381`, `:438-441`). |
| I-23 | FILED #172 | The session stack reseeds on every target change (`imaging/sessionstack.py:512-515`), so a rotating mosaic restarts the Monitor picture every visit. Needs per-panel stacks, then an assembled mosaic preview. |
| I-24 | FILED #173 (small bug) | The FramingCard dial selects 0 when the angle is not a multiple of 15 (`FramingCard.tsx:124`; continuous degrees come from `SkyCanvas.tsx:580-584`). |
| I-25 | FILED #174 (small) | `MosaicPanel` lacks `transit_alt_error` (`types.ts:1965-1972`; the fix is documented at `mosaicNightSummary.ts:25-41`). `status.sky_angle` has no UI type. |

**Ultracode builds** (all in #189)

| Id | Tag | Gap |
|---|---|---|
| U-01 | ULTRACODE S2 | The engine group driver: `TargetGroup`, requeue in `_run_scheduled`, `VisitBound` with a deadline, `panel_order.py`, tagged eligibility, set-aside semantics persisted for the night, the per-panel reject rule, meridian hysteresis on the plan's lead, bounded follower visits. Nothing visits A, then B, then A today (`engine.py:1964-1965`, `:2010`). |
| U-02 | ULTRACODE S1 | Deterministic, geometry-keyed identity, and Run CONTINUE on the flow's dormant session, write-locked, with skipped panels exempt, ADOPT and `accept_recount`. Today every compile mints uuid4 and every flow run is a fresh session that disarms the last one (`app.py:5198`, `engine.py:795-803`). |
| U-03 | ULTRACODE S1 | The SLEW settings finally reach the run through TARGET (#170). The hub already takes them (`hub.py:6387-6391`); `_setup_target` never passes them (`engine.py:2357-2359`). |
| U-04 | ULTRACODE S1/S2 | The fixed-camera and dropped-rotator angle check, fed by `sky_angle` (`sky_angle.py:228`), with the combined budget of Appendix A.2 and the no-measurement cases of 5.6, plus the modal's USE MEASURED chip (`hub.py:7001`). |
| U-05 | ULTRACODE S1 | Focus at a hop: owed only on no good sweep tonight, a failed sweep, `_refocus_due()` or the group's first acquisition; no 30-minute age rule (5.6). Today every flow target sweeps at every setup (`engine.py:2429-2440`), and a sweep takes 7-9 minutes on this rig. |
| U-06 | ULTRACODE S2 | The hub rotate shortcut: skip the rotate solve when the rotator is calibrated, unmoved and within tolerance (`hub.py:6166-6334`). Validate on the rig. |
| U-07 | ULTRACODE S2/S5 | Run readouts for flows: `state.group`, the hop cost in the ETA (`engine.py:956`, `:988`), the pre-flip idle. No `flow.node` or `flow.log` topic exists (`FlowPhoneMonitor.tsx:6-14`). |
| U-08 | ULTRACODE S2 | Per-panel provenance: FITS `MOSAIC`/`PANEL` and `$$PANEL$$` (`naming.py:18-30`). Stitching tools must be able to group panels and never co-add them. |
| U-09 | ULTRACODE S3 | The TARGET block vocabulary, the loop-wire grammar, the panel lane and `owner_of`, `create_params`, doctor M1-M15, the wizard mosaic kind with a real angle and injected optics, the eighth Example. |
| U-10 | ULTRACODE S3 | The Tonight mosaic branch: one `compute_night` per block (`tonight.py:393-439`), a budget that counts panels (`:510-555`), Campaign rows without a pool (`:868-871`), the brief sentence. |
| U-11 | ULTRACODE S4 | UI seams: a local FramingSession (`store.ts:1283` is a singleton), `flowsApplyFraming` (`flowsSlice.ts:264-279` writes one key), `flowsAddNode` returning the id, the SkyCanvas `panels` and `frameCenter` props, the Overlay `full` variant, the loop back-arc, the stage-list rail. |
| U-12 | ULTRACODE S2b | A classic Plan "cycle panels each pass" toggle on an existing `mosaic_group`, so every door to a mosaic shares one engine behaviour. |

**Later follow-ups**

| Id | Issue | Gap |
|---|---|---|
| I-26 | FILED #175 (blocked on #145) | Per-panel angle correction for meridian convergence. `compute_mosaic` stamps one angle on every panel (`framing.py:212`). Computed cost in Appendix A.1. It matters only at high Dec or low overlap, so S3 ships M6 and M15 and S8 ships the correction. |
| I-27 | FILED #176 | Flats keyed by rotator mechanical angle once panel angles differ (NINA Target Scheduler keys flats by filter, gain, offset, binning, readout mode, rotation and ROI). |
| I-28 | FILED #177 | Coverage verification: check each saved light's WCS stamp (`hub.py:3392`) against its panel footprint and draw a coverage map. That is the honest way to call a panel covered. |
| I-29 | FILED #178 | Telescopius-shape panel CSV import and export (Pane, RA, DEC, Position Angle (East), width, height, Overlap, Row, Column), with the angle convention stated explicitly. |
| I-30 | FILED #179 | Reopen a **complete** flow session when the edited flow owes more. Resume and the id-safe edit are dormant-only (`app.py:5356`, `:5391`). With #141, a session can be stamped complete on rejected frames. |
| I-31 | FILED #180 | Starving panels: a panel with no guide star (this guide scope reaches only bright fields) or no solvable field is set aside every night and never completes. Needs a per-block policy (allow unguided; blind pointing after N failed solves) and a Campaign row that names the starvation. |
| I-32 | FILED #181 | Object shape data (major axis, minor axis, PA from OpenNGC) for FIT OBJECT, SUGGEST GRID, and aligning the grid to the object. Only `size_arcmin` exists (`types.ts:938`; circles at `SkyCanvas.tsx:1010-1011`). |
| I-33 | FILED #182 | `SkyCanvas` `ZOOM_MAX` of 10 deg (`SkyCanvas.tsx:55`) cannot show a large mosaic whole. The bundled order-3 pack draws a 0.5 deg panel about 35 px across. The modal must say so rather than crop silently. |
| I-34 | FILED #183 (payoff) | Make POOL a group on the same engine primitive (`kind "set"`, best-scoring member per visit). That unblocks the Best-of-four and Campaign examples, which answer 409 today (`to_plan.py:1118-1126`). |
| I-35 | FILED #184 | An optional `on_mosaic_complete` trigger, added only in the commit where the engine raises it (engine first, enum second: `sequence/models.py:123-127`; `to_plan` reads `TriggerKind` directly). Until then M14 notes the per-panel firing. |
| I-36 | FILED #185 | AUTOFOCUS node `when` policy inside the circle (stale / every panel / first panel), making the node's params live for groups, and running a due sweep on the richest panel. |
| I-44 | FILED #186 | Per-panel exposure overrides inside one mosaic (a bright core panel, a faint edge panel). |
| I-45 | FILED #187 | Fixed camera, unknown angle: lay the grid out at the angle the first centring solve measures. It changes the geometry key, so it waits on the owner's re-frame identity decision. |
| I-46 | FILED #188 | Gallery and Session Review have no mosaic grouping (`report.py:169`, `bundle.py:443` group by target name). |
| I-37 | VERIFY, do not file blind | The backlog says an abort was followed by an automatic restart 50 s later (`2026-08-22-flow-editor-offers-what-the-engine-refuses.md:74-79`). Re-check it against the fix for #93 before filing. |
| I-38 | VALIDATION GAP | No mosaic has run on the rig. The simulator solver encodes the handedness assumption (`solve/simsolver.py:97`), so no simulator test can validate #145 or the sign of the per-panel PA. Whether the simulator mount picks its pier side by hour angle, as the AM5 does, must be asserted before the S2 meridian test proves anything. |

---

## 10. Risks

1. **The scheduler is the safety surface.** Mitigations: visits run through the unchanged `_run_step`; there are six independent termination bounds (6.4); an idle mount is park-held on the idle clock (5.1); every S2 branch has a named mutant; the tests use a clocked simulator, never sleeps. Windows' 15.6 ms timer resolution makes sub-resolution margins a coin toss.
2. **The hop cost is unmeasured.** The defaults (`passes 1`, `minVisit 0`) are the owner's literal circle, not a tuned number. S7 measures the cost and re-sets them. Until then every surface says "hop not measured".
3. **The meridian rule is unvalidated on the AM5.** It relies on the goto choosing the new side once the hour angle is past the meridian, and it verifies this by reading the side. Flips have cost 32 minutes on this rig and triggered #136. A clocked simulator straddle comes first; S7 is the first real test.
4. **Guider churn.** Every hop restarts the guider, on the path of #72, #134 and #135. Starts are bounded with escalations, and there is a forced-failure simulator test.
5. **Continuity can surprise.** An operator who expects Run to start over will be surprised once. The button says CONTINUE with the night and the counts, and START OVER is one confirm away.
6. **Semantics flips.** Four changes each have their own evidence: wire-scoped compile (only in graphs that contain a mosaic), the rotation default (FLOW_SCHEMA 3), accepted counting (`create_params`, so old flows are untouched), and the accepted-mode visit fix (a named behaviour change with an issue).
7. **#145.** Rotator-commanded panels inherit whatever #145 finds. The angle check compares like with like, so it does not depend on #145.
8. **Ledger cost.** `accepted_by_step` walks every frame, and the session file is rewritten on every frame. Counts are snapshotted once per pass. The cost at thousands of frames per project is unmeasured; measure it in S7.
9. **Two UIs.** The modal, the back-arc and the rail must live in shared modules. The pins in `nodeDefs.test` fail loudly by design.
10. **Downgrade** to a build older than S0 would shoot a mosaic's centre. Ship S0 a release early.
11. **Privacy.** Scheduling reasons are computed from the site. The rule is words in logs, minutes only behind the gate. Scan every published artifact against the needles file.
12. **Scope.** S2 is the long pole, and the first visible result (S4) arrives late. The circle can be shown through `/api/sequence/start` and the classic Plan toggle (S2b) before any Flows UI exists.
13. **The idle-clock park-hold changes today's behaviour.** A mount that used to keep tracking through an eta-0 or constraint wait now stops tracking after `WAIT_TEARDOWN_S`. That is the point of the fix, and the next `_setup_target` re-slews and restores tracking as it does after any long wait. It is a named behaviour change with its own issue (#165).
14. **Bounded follower visits cost hops.** A follower that fills a mosaic's wait pays a slew and a centring each time it comes back. Whether the default is to fill the gap at all is the owner's D15 decision; "Wait for the mosaic" stays one tap away.
15. **Hop focus has no age rule.** It relies on the frame loop's refocus triggers (`autofocus_every`, the temperature delta). On a rig with neither, a mosaic focuses once per night, which is what one long target does there today. The RUN section says so, so the operator can arm the temperature delta.

---

## Appendix A: computed numbers

All values below come from the repo's `compute_mosaic` and `project` (`framing.py:100-231`).

### A.1 Meridian convergence (3x3 grid of 2.0 x 1.33 deg panels, 25% overlap, angle 0)

Local north at each panel centre was measured against the grid's up axis by projecting a point 1e-4 deg north of the panel centre. The share of overlap used at the far corner is `(f_y / 2) * sin(delta) / (overlap * f_x)`, where delta is the worst relative rotation between neighbours.

| Dec | Worst panel | Worst between neighbours | Horizontal overlap used | RA span of panel centres |
|---|---|---|---|---|
| 20 | 0.55 deg | 0.55 deg | 1.3% | 12.8 min |
| 41 | 1.32 deg | 1.32 deg | 3.1% | 16.1 min |
| 60 | 2.68 deg | 2.68 deg | 6.2% | 24.7 min |
| 75 | 5.97 deg | 5.97 deg | 13.8% | 49.4 min |

- A 1 row x 4 col grid of 2.0 x 1.33 deg panels at 10% overlap uses 9.1% of the overlap at Dec 41 and **38.9%** at Dec 75. That trips M6.
- A 2x2 at Dec 20 has a worst panel of 0.27 deg.
- A 2x3 of 0.9 x 0.6 deg at Dec 41 has panel centres spanning 7.2 min of RA.
- The full footprint of 3 columns of 2.0 deg at 25% is 5.0 deg wide: `width x sec(Dec)` = 26.5 min of RA at Dec 41 and 40.0 min at Dec 60. Flip timing follows the panel centres (the table above), not the footprint.

### A.2 Tolerance on the angle of a fixed camera

Rotating every panel by theta about its own centre, on a grid laid out at the planned angle, shifts each neighbour perpendicular to the line between them by `step x sin(theta)`, where `step = f x (1 - overlap)` along that line. The corner overlap shrinks by that shift. So the angle that uses a fraction k of the overlap is:

`sin(theta_max) = k * min( overlap*f_y / (f_x*(1-overlap)), overlap*f_x / (f_y*(1-overlap)) )`

With k = 0.5 (half the overlap):

| Panel | Overlap | Tolerance |
|---|---|---|
| 2.0 x 1.33 deg | 25% | 6.36 deg |
| 2.0 x 1.33 deg | 15% | 3.36 deg |
| 2.0 x 1.33 deg | 10% | 2.12 deg |
| 0.9 x 0.6 deg | 25% | 6.38 deg |

For a single row or column no hole can open between panels, so the same formula is conservative there. The modal and alerts say so.

**Combined with convergence (Revision 1).** Meridian convergence (A.1) and camera angle error both eat the same corner overlap, so they are budgeted together: `k = max(0, 0.5 - c)`, where `c` is the convergence share of the overlap from A.1. Convergence and angle error together then never use more than half the overlap, and the other half is left for pointing error. `angle_tolerance_deg` is computed this way. `k = 0` is M15. Computed for 2.0 x 1.33 deg panels:

| Grid | Overlap | Dec | c | Tolerance (k = 0.5) | Tolerance (k = 0.5 - c) |
|---|---|---|---|---|---|
| 3x3 | 25% | 20 | 1.3% | 6.36 deg | 6.20 deg |
| 3x3 | 25% | 41 | 3.1% | 6.36 deg | 5.97 deg |
| 3x3 | 25% | 60 | 6.2% | 6.36 deg | 5.57 deg |
| 3x3 | 25% | 75 | 13.8% | 6.36 deg | 4.60 deg |
| 1x4 | 10% | 41 | 9.1% | 2.12 deg | 1.73 deg |
| 1x4 | 10% | 75 | 38.9% | 2.12 deg | 0.47 deg |

The 1x4 case at Dec 75 spends 38.9% plus 50% of its overlap under the old rule. Under the combined rule it keeps 0.47 deg of angle tolerance, which a fixed camera will rarely meet, and M6 has already warned.

### A.3 Budgets

- Default cycle pass: 4 x 60 + 3 x 180 = 780 s. 45 cycles make 9.75 h per panel. Six panels make 58.5 h and 1890 subs, about 7.8 nights of 7.5 h, and 270 visits at one pass per visit.
- LRGB at 60 s x 45 on a 3x2: 1080 subs, 18.0 h.
- Visit efficiency `4K / (4K + H)` for a 4-minute pass, K passes per visit and an illustrative H = 2 min: K=1 gives 67%, K=2 gives 80%, K=3 gives 86%.
- A panel that always rejects: 10 visits x 780 s = 2.17 h of shutter per night through the per-step guard alone, against 3 x 780 s = 39 min under the per-panel reject rule (5.1).
- Hop focus under the 30-minute age rule: one visit plus its hop takes about 16.7 min (780 s of shutter, 7 x 10 s of assumed per-frame overhead, a 150 s hop), so an 8-minute sweep would fall due every second hop, 19.4% of the night. The hop rule of 5.6 removes it.

### A.4 The pre-flip idle (Revision 1)

Assumptions, stated because they are not measurements: 10 s of per-frame overhead, a 150 s hop, the default 10-minute plan lead, the shipped default cycle (780 s per pass), and a shortest owed frame of 60 s plus overhead plus the 30 s flip margin. The RA span of the panel centres comes from `compute_mosaic`, in sidereal time.

- With cut visits (5.7): `idle = max(0, lead + hop + f - span)`.
- If a visit had to fit whole: `idle = max(0, visit + lead - span)`, with `visit = passes x (780 s + 7 x 10 s) + hop`.

| Grid (cols x rows) | Panel | Dec | Span | Idle, cut visits | Idle, whole visit, 1 pass | Idle, whole visit, 2 passes |
|---|---|---|---|---|---|---|
| 3x2 | 2.0 x 1.33 deg | 20 | 12.8 min | 1.4 min | 13.9 min | 28.1 min |
| 3x2 | 2.0 x 1.33 deg | 41 | 16.0 min | 0 | 10.7 min | 24.9 min |
| 3x2 | 2.0 x 1.33 deg | 60 | 24.3 min | 0 | 2.4 min | 16.6 min |
| 2x2 | 0.9 x 0.6 deg | 41 | 3.6 min | 10.6 min | 23.1 min | 37.3 min |

The idle scales with the hop and the lead, not with the site, so the modal can show it to every role. When it happens is site-derived, and stays behind the gate.

## Appendix B: judges' findings and where each is handled

| Finding | Where fixed |
|---|---|
| A: a blocked panel returns "waiting" to a scheduler that still sees it ready (asyncio busy loop) | 5.1 item 1: the verdict is evaluated in selection; a blocked panel joins `earliest` and the existing wait path runs. S2 test counts iterations. |
| A: resetting `_pre_flip_side` on acquisition disables the flip-owed backstop | 5.7: keyed per target (from C), with #136 |
| A: "exactly one pier change" not enforced | 5.7: hysteresis on the hour angle plus post-hop side verification (from B and C) |
| A: `rotation_skipped` with a connected rotator not deferred | 5.6 step 4, plus the new `rotation_unavailable` flag and the angle check on every hop |
| A and B: duplicate-name refusal strands classic sessions | 3.5: names refused only with groups or named instructions; a start-path check, never a validator |
| B: requeue without StopTarget rules | 5.1 outcome table: floor and guiding skip set the panel aside |
| B: fixed-camera error only warns; default "shoot" leaves holes | 5.6 step 4: defer or set aside; `ifNotCentred` Auto skips for mosaics |
| B: step ids collide without an occurrence index | 3.3: stage node id plus occurrence index |
| B: SLEW fold placed where `_known_type` rejects it first | 1.7: no fold; SLEW stays as a hidden legacy type |
| B: refuse the whole start if any panel is blocked | 6.3: refuse only when every panel is blocked |
| B: meridian span copy ignored sec(Dec) | Appendix A.1: 26.5 min at Dec 41 for a 5.0 deg footprint |
| C: ids exclude geometry | 3.3: `geometry_key` in the group id |
| C: `Session.carried` second ledger | D6: continue one session; no carry |
| C: `catch_up` can starve and set a mosaic aside | 5.2: not adopted |
| C: third port kind | D2: a structural event wire |
| C: legacy scoping leaks a post-mosaic CAPTURE into every panel | 1.5: wire scoping for the whole graph once any mosaic exists |
| All: accepted-mode visit not bounded by attempts | 5.3 and I-01, fixed in S0 |
| Operator: the loop invisible on phone | 1.4: stage-list rail and LOOP PANELS |
| Operator: the circle should be the default | 1.4: the wire is added automatically on DONE and by every generator |
| Operator: Run continued silently | 5.9: CONTINUE button copy with night and counts, START OVER behind a confirm |
| Implementability: a parallel `_run_group` mini-scheduler | D7 and 5.1: requeue inside `_run_scheduled` |
| Implementability: a visit deadline inside `_run_step` | 5.3: round boundaries in `_run_steps` |
| Implementability: a new FieldDef control between the server and UI slices | 2.1: the per-type slot |

## Revision 1 (critic)

2026-09-23. The completeness critic re-ran the Appendix A numbers (all reproduced) and found the errors below. Each was re-checked against the code before it was fixed here. Every defect is now filed: #147 to #188, with comments on #132, #136, #141 and #142. The build is tracked in #189.

| # | Critic finding | Verified at | Fix in this spec |
|---|---|---|---|
| 1 | I-01 was half wrong about today: rejects do not increment `taken_this_visit`, so the per-step guard does trip, inside one visit | `engine.py:3027-3049`, `:3025`, `:2859`; `config.py:1009`, `:1012` | 5.3 now states the present-day harm: one rejecting filter holds the cycle for up to 10 subs per round, and with the step guard off 20 rejects end the night. Parts 2 and 3 of the fix are named as consequences of part 1. S0 tests changed. #147. |
| 2 | Circle and ownership contradicted each other; "nearest TARGET upstream" still let a CAPTURE after "all done" belong to the mosaic; the doctor walk returns a set of types | all 21 node types have at most one flow input (`flows/nodes.py`); `doctor.py:40-63` | 1.5 rewritten around the panel lane: `owner_of` is a chain walk through lane nodes, the tail is the last stage, the loop wire leaves the tail, and "what runs next" is whatever the tail's "all done" feeds, which is never a stage. New rules M12 (loop not at the tail, branched lane) and M13 (unowned stage). 1.3, 1.4 and D2 aligned. S3 tests. |
| 3 | Blocked-panel waits never park-hold (60 s recheck < 120 s teardown); the same hole exists today for eta-0 and constraint waits | `engine.py:326`, `:2052`, `:2058`, `:2163-2166`; `schedule.py:762`, `:766-775`; `sun_watch.py:9-10` | 5.1: park-hold on the idle clock, with the tracked target's floor and flip point checked every wait tick, latched. S0 item 7 with tests. 6.2, 6.17. Filed as a present-day defect, #165. |
| 4 | The meridian rule used "the plan's lead", exposed to the learned zero of #127; the pre-flip idle was not priced | `engine.py:5485-5519`, `:5273-5276` | 5.7: the margin is `plan.meridian_flip_lead_min` via `_group_flip_margin_s()`, never `_flip_lead_s()`. Visits carry a deadline at the flip point. The idle is priced (A.4: 0 to 10.6 min with cut visits, against 2.4 to 37.3 min without them) and shown in RUN. D10. |
| 5 | D15 let a follower take the rest of the night at any all-waiting moment | `engine.py:1964-1965`, `:2010` | 1.6: a follower picked while the group waits runs in visits that end at `group_ready_ts`, checked at frame boundaries in `_run_steps`, and is requeued. D15, 6.18, S2 tests. The default remains an owner decision. |
| 6 | Focus reuse was mispriced: the 30-minute age rule would sweep about every second hop | `engine.py:7324-7326`, `:151`; `config.py:991` | 5.6 step 6: `_hop_focus_is_owed` has no age rule. A hop owes a sweep only on no good sweep tonight, a failed sweep, `_refocus_due()`, or the group's first acquisition. Computed cost of the old rule: 19.4% (A.3). |
| 7 | A panel that always rejects burns about 2 h per night | 10 x 780 s | 5.1: the per-panel reject rule sets a panel aside after 3 visits that accept nothing while other panels accept, which bounds it at 39 min. A sky-wide reject spell does not count. |
| 8a | `patch_session` is replace-and-report, not a merge | `app.py:5390-5398` | 5.9: CONTINUE uses the factored replace; the dropped-steps refusal is named as new logic. |
| 8b | A skip toggle on a panel with banked frames would 409 | expansion drops skipped panels | `TargetGroup.skipped_ids`; CONTINUE exempts them (2.5, 3.3, 3.4, 5.9). |
| 8c | Pre-S1 dormant sessions have uuid4 ids and would be disarmed by a silent fresh start | `sequence/models.py:19`, `:80`, `:226`; `engine.py:795-803` | 5.9: 409 with ADOPT (unique-match id rewrite under the lock, `.bak` first) or START OVER. |
| 8d | Load, patch, save and start can race ResumeArm | `app.py:5385`, `:5433`; `session.py:243` | 5.9: one write-locked section that re-reads, replaces and starts, and never saves before `engine.start`. The present-day race is #167. |
| 8e | Changing `counts` mid-campaign recounts the ledger | `session.py:130-134` | 5.9: 409 with both totals unless `accept_recount`. |
| 9 | Privacy: log timestamps of computed meridian events reveal the longitude | `app.py:8046`; `redact.py:64-88` | 6.9 and 5.10: a `site_derived` flag on such lines and state changes, dropped for non-holders. Verification also found the "holding for the meridian flip point (N min)" state detail (`engine.py:5546-5547`) and the waiting `schedule` block (`:2031`) outside the redaction tables (`redact.py:130-131`). #166. |
| 10 | The angle check had undefined cases, and the convergence and angle budgets were never summed | `engine.py:2373`; `sky_angle.py:252-253` | 5.6 step 4: explicit no-measurement cases. The fabricated `centered: True` becomes a measurement (S0 item 8, #171). A.2: combined budget `k = 0.5 - c`, with the new rule M15. |
| 11 | The wizard mosaic kind must pick an angle and needs injected optics | I-04 class | 1.8: the angle comes from the operator or USE MEASURED, never a default; optics are injected, and with none the wizard answers with a single target. S3 item 4 and tests. |
| 12 | Stale claims: a UI doctor copy, the downgrade row, the untracked mobile handoff, the IA map line | `ui/src` has no rule text; `ASTRODECK-UPDATE/` is untracked | The UI copy work is removed (1.8, 3.7, S3). 3.6 splits the pre-S0 downgrade row: a looped mosaic answers 422, and only an unlooped one shoots the centre. D3 and section 4 mark the handoff as an untracked draft. The IA map line is in #169. |
| 13 | "Set DUSK to repeat nightly" changes nothing | `engine.py:795` | Removed from 2.4. "Single night" becomes an owner decision. |
| 14 | Reachability must separate permanent refusals from waits | `engine.py:4531-4538`, `:4578-4585` | 5.1: the verdict is tagged `wait` or `refuse`. No site raises `SafetyAbort`; a pier change with flips off sets that panel aside. |
| 15 | REPORT "done" fires once per panel | `compile.py:159` | Doctor note M14; follow-up #184. |
| 16 | Set-aside state is not persisted, so a crash retries set-aside panels the same night | engine state only | `Session.set_aside`, an additive field (SESSION_SCHEMA stays 1), read by a same-night resume (3.4, 5.1, 6.7). |
| - | Not verified by the critic: whether the simulator mount picks its pier side by hour angle | `hub.py:6582-6585` for the AM5 | An S2 test asserts it before the meridian straddle test counts (8, I-38). |

Two corrections came from verification rather than from the critic. I-12 is latent, not live: a regrade is refused during a run (`app.py:5449-5452`), and it is filed as such (#158). I-18's latch has one reset, NowEmpty's own RUN press (`NowEmpty.tsx:394-397`), and the classic header still reads the stale value (#162).
