# AstroFlows mosaics: the TARGET block and the panel loop

- Date: 2026-09-23
- Status: design, being built. Revised against the completeness critic (Revision 1), against the owner's rulings of 2026-09-24 (Revision 2), against slices S0 and S1 as built and hardened (Revision 3), against the second hardening round and the orchestrator's rulings for it (Revision 4), against the third hardening round and the orchestrator's rulings for it (Revision 5), and against slice S2 as built and the orchestrator's rulings for it (Revision 6), all at the end. The body carries every revision. S0 (cea8f1f7) and S1 (6c6aae47) are built, and three hardening rounds have since changed part of S1: H1 (952581e0), H2 (fbbcc4cc) and H3 (32107076). S2, the engine group driver, is built on top of them. The sections Revision 3 lists describe the code as it stands after the S1 hardening round (H1), and H2 left what they describe as it was, except in the sections Revision 4 lists too. The sections Revision 4 lists describe the code as it stands after the second hardening round (H2), and H3 left what they describe as it was, except in the sections Revision 5 lists too. The sections Revision 5 lists describe the code as it stands after the third hardening round (H3), and S2 left what they describe as it was, except in the sections Revision 6 lists too. The sections Revision 6 lists describe the code as it stands after S2. Elsewhere, "today" and "a present-day defect" still mean the code as it was when this spec was written, before S0, and section 8 says which slice fixes each: among others, S0 and S1 removed the fabricated `centered: True`, the silent dropped rotator, the dither counter that spanned targets and the uuid4 step ids that 5.6 and 9 still describe as live.
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
- A panel whose guider will not start goes to the back of the rotation and is retried on the next pass, whatever the rig's `guiding_action`, and is set aside after three such passes (Revision 2, ruling 5).
- A pass that takes no exposures ends the rotation.
- While the whole mosaic waits, the scheduler shoots later targets and comes back (the owner's default, a per-flow option). Their visits end when the next panel is due, so they cannot take the rest of the night.

Pressing Run on night two continues the flow's own dormant session, so there is one ledger per flow and `Session.owed()` stays the only definition of finished.

### Decisions

| # | Decision | Why, in one line |
|---|---|---|
| D1 | Extend the TARGET node (type id stays `target`). Do not add a node type. | Saved flows keep loading. One node expands to up to 100 panels, far inside the 400-node cap (`flows/models.py:101-102`). The palette union is unchanged. |
| D2 | The circle is a drawn **structural event wire**, `<last stage>.pass -> target.next`, where the last stage is the tail of the panel lane (1.5). It is never a flow back-edge and never a new port kind. | Flow back-edges are silently dropped today (`compile.py:72-78`). A third port kind breaks the kind-to-kind rule and `flow_order` (`flows/models.py:143`, `compile.py:77-78`). Backward event wires are already legal (`design_handoff_astrodeck_flows/README.md:122`). |
| D3 | The default is **pass-major**: rotate panels every pass, one full filter pass per visit, least complete first. Panel-first is what you get by deleting the wire. Filter-major is rejected. | It extends FILTER CYCLE's own reason for existing ("a night cut short still stacks") from channels to panels. A local, untracked mobile handoff draft describes the same rotation (`ASTRODECK-UPDATE/design_handoff_astrodeck_mobile/README.md:174`); it is not a committed contract, and it numbers panels row-major at 15% overlap, which this spec does not follow. |
| D4 | Panels are derived, never stored. Compile emits one entry per block and `to_plan` expands it. | The graph is the source of truth (`flows/models.py:3-8`). The server is canonical for slew targets (`framing.py:3-8`). |
| D5 | Identity is deterministic (uuid5) and keyed to the block's **anchor**: the geometry at which its counts started. Skipping a panel is not part of the identity. A re-frame that moves every panel corner less than half the overlap width (`REFRAME_CARRY_FRACTION = 0.5`, a first guess) keeps the anchor and carries the counts; a larger one re-anchors and restarts them, and the modal says so before DONE (Revision 2, ruling 3). | Counts stay honest after a real re-frame, a nudge does not throw away a campaign, and continuation works across nights. Measuring against the anchor, never the last save, stops small nudges adding up. This still fixes C's flaw of crediting old frames to moved panels. |
| D6 | One ledger per flow. Run CONTINUES the flow's dormant session through the existing id-safe plan edit plus `engine.start(session=)`. | No second ledger and no session schema change. `owed()` stays single (`session.py:143-152`). |
| D7 | Engine: `SequencePlan.groups`. Rotation is a requeue inside `_run_scheduled`. Visits are bounded by passes in `_run_steps`. The `_run_step` gate stack is untouched. | There is one scheduler to keep correct. Per-panel gating, waits, park-hold, skips and jumps come for free. |
| D8 | Every new TARGET block, single target or mosaic, counts **accepted** subs only (owner ruling, 2026-09-24). The editor no longer offers "every sub taken". A flow saved before this change keeps counting attempts when it loads, shows a one-line notice, and switches to accepted subs when it is next saved (Revision 2, ruling 2). | Rejected frames must not fill a quota. Loading never changes a flow's meaning silently; the save that changes it is announced first. |
| D9 | Reachability is decided in the scheduler's selection, so a blocked panel is "waiting" and the existing do-while wait path runs. A verdict that waiting cannot change (no site, a pier change with flips off) is a refusal, never a wait. The mount is park-held on the idle clock. | This removes the hot loop the safety judge found in A and C, and the tracking-while-idle hole the critic found (5.1). |
| D10 | Meridian: pier hysteresis on the hour angle with the **plan's** flip lead as the margin (never the lead learned by #127), verified by reading the pier side after every hop. A visit ends before its panel's flip point. The flip-owed memory is kept per target. | At most one pier change per group per night, the #136 invariant stays armed, and the pre-flip idle is priced and bounded (5.7). |
| D11 | Angle has three explicit modes: any, rotate to, camera fixed. A mosaic requires an angle. Every hop compares the angle its centring solve measured with the planned angle, whatever the rotator state. | A dropped rotator skips rotation silently today (`hub.py:6455-6456`), and a fixed camera at the wrong angle cannot tile. |
| D12 | Failures set a panel aside for tonight. Deferrals are bounded. The group anti-spin guard counts exposures, not accepted frames. | "Set aside" is not "done", and a clouded pass must not end a mosaic. |
| D13 | One shared modal module for both UIs, opened from the per-type slot. DONE is one atomic write, made after the server has answered with panel centres. | This avoids a fork between the classic and #/next UIs, and prevents a half-applied framing. |
| D14 | Per-panel PA correction for meridian convergence waits for #145. Until then a doctor rule states how much overlap convergence costs. | The sign of the correction cannot be validated on the simulator (`solve/simsolver.py:97`). |
| D15 | "What comes next" is whatever the tail's "all done" output is wired to. By default ("Shoot later targets, then come back"), while every live panel is waiting, the scheduler shoots a later target in visits that end when the next panel is due; once the group is set aside tonight, a later target runs its normal course. The default is the owner's ruling of 2026-09-24 ("No sense in wasting time due to an obstruction"). It is a **per-flow option**, `FlowGraph.settings.whenWaiting`, and "Wait for the mosaic" is its other value (Revision 2, ruling 1). | This honours the owner's "before moving on" without idling a clear sky, and without handing the night away at every routine wait (1.6). |

### Earlier decisions this reverses (named, not silently overturned)

1. `docs/superpowers/specs/2026-07-14-mosaic-apply-steps-design.md:32-37` kept the Atlas geometry-only, with no live group step editor and no server awareness of group steps. A mosaic block that feeds one FILTER CYCLE is exactly a live group step list, and the server now knows about groups.
2. `docs/superpowers/specs/2026-07-13-plan-schedule-design.md:98-106` declined a rows/cols round trip. The block now stores rows/cols and reopens the framing it was made from.
3. The Flows handoff left "Mosaic panels as a node or a Target detail?" open (`design_handoff_astrodeck_flows/AstroDeck Flows.dc.html:565`). This spec rules that a mosaic is a Target detail, and that the loop is a backward event wire the compile consumes as structure. Record the ruling as a README amendment next to `README.md:122` and `:163`, so later agents do not "fix" it back. Mark `MILESTONE2-CONTRACT.md` superseded for the node count (19 there, 21 shipped). Both are drafted as uncommitted edits labelled "Amendment 2026-09-24 (owner approval pending)", and wait for the owner's signature (Revision 2, ruling 6).
4. The Atlas and Sky "Send N panels to Plan" door is retired and replaced by "Send to Flow Wizard" (Revision 2, ruling 4, #196). With it goes S2b, the classic Plan "cycle panels each pass" toggle.

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
- A one-line state chip from the progress route S1 built, `GET /api/flows/{flow_id}/progress` (`CAP_VIEW_STATUS`, S1 item 9). Its answer is `flow_progress`'s in `flows/progress.py`: one entry in `blocks` per TARGET node (one per POOL node, whose members are its panels), each with `banked`, `owed` and `total` and one entry in `panels` per panel, counted from the flow's session as Run reads it (5.9), plus the frames orphaned on steps the flow no longer has. The chip reads `212/315 subs` for a single target (the block's `banked` over its `total`), which is the chip S1 draws, on both canvases (`progressChip`); for a mosaic it will read `4/6 panels done` (the panels whose `owed` is 0), which S3 adds.

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
- The wizard's mosaic kind, including a mosaic sent from the Sky or the Atlas through "Send to Flow Wizard" (slice S6, #196).
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

**What runs next.** Next is whatever the tail's "all done" output is wired to (1.5, item 6). Targets reachable from it are **followers** of the group. While the group has live members, followers sort after every member in `remaining`, whatever their window start. The flow's **`whenWaiting` setting** decides what happens while a mosaic cannot be shot. It is one option per flow, stored in `FlowGraph.settings` (Revision 2, ruling 1), and it applies to every multi-panel block in that flow:

- **"Shoot later targets, then come back"** (the default, by the owner's ruling of 2026-09-24: "No sense in wasting time due to an obstruction"). While every live panel is waiting (below its floor, behind the horizon mask, or held by the meridian rule), the scheduler may select a ready follower. A deferral wait is not filled yet (S2, #304): S2 built it as one `_wait_until` of `DEFER_WAIT_S` inside `_close_group_pass`, so a follower waits it out with the group. This is today's window-sorted skip-ahead (`engine.py:1924-1938`), with one change: such a follower runs with `VisitBound(deadline_ts = group_ready_ts)`.
  - `group_ready_ts` is the earliest real time a live member becomes eligible: its meridian crossing (5.7), the projected time a blocked panel clears the floor and the mask, or the opening its gating waits for (`_group_ready_ts`); the end of a deferral wait (5.1) joins them when a follower can fill one (#304). The last is a forward scan in 60 s steps over the same predicate as `_mount_floor_verdict`, in the shape of `_time_to_gate` (`schedule.py:772`). A member with no clearing time tonight does not bound it. The 60 s recheck is a re-evaluation cadence, never a deadline.
  - The deadline is checked at frame boundaries in `_run_steps`, never inside `_run_step`. For a follower with blocks acquisition that means calling `_run_step(max_frames=1)` in a loop, which is the path cycle acquisition already takes, and checking the deadline between calls.
  - At the deadline the follower's visit ends. It keeps its progress, is requeued behind the group, and selection returns to the group.
  - Once the group is set aside for tonight, or complete, a follower runs its normal course.
  - Without this bound, a follower selected at any all-waiting moment runs to completion and is removed (`engine.py:1964-1965`, `:2010`). The routine pre-flip idle (5.7: 0 to 10.6 min with cut visits, and 2.4 to 37.3 min without them, in the Appendix A.4 cases), a tree, or one 60 s recheck would hand the rest of the night away at every meridian crossing.
- **"Wait for the mosaic"**. Followers get `Target.after_group = <group id>`. Gating reads them as waiting ("after the M31 mosaic") while the group has live members. They are skipped for the night, not marked done, once the group is set aside tonight. They become ready when the group is complete. Tonight shows how many idle hours this choice costs.

Default and why: a multi-night mosaic that holds its followers would idle clear sky for weeks, and a follower that keeps the cursor would starve the mosaic at every meridian crossing. The bounded follower visit gives neither. The owner's "before moving on" is met on any night the mosaic can be shot, because a follower only fills gaps the mosaic cannot use and hands the cursor back when a panel is due again. The owner chose this default on 2026-09-24 and asked for it to be an option; "Wait for the mosaic" stays one tap away.

**Where the option lives.** `FlowGraph` gains `settings: dict` of flat scalars, with missing-key defaults in one table (`FLOW_SETTINGS` in `flows/models.py`, mirrored in `flowsTypes.ts` and covered by the parity test). Its first key is `whenWaiting`, missing-key default "Shoot later targets, then come back". There is no older meaning to preserve, because no graph had a multi-panel block before S3, and the setting does nothing in a graph without one. It is edited in three places, all writing the same key: the flow overview (the inspector with nothing selected), the modal's RUN section (labelled "for every mosaic in this flow"), and the wizard's mosaic path (#196). The compile copies it into each block's `mosaic.when_waiting` (3.2), so the engine sees it per group as before.

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
- Counts: no control. Every new block counts accepted subs only (D8). A block that still counts every sub, because its flow predates the ruling, shows the one-line notice of Revision 2, ruling 2 instead.
- While a mosaic waits (for every mosaic in this flow): Shoot later targets, then come back / Wait for the mosaic. This row edits the flow's `whenWaiting` setting (1.6).
- Readouts, for the shipped default cycle (L R G B at 60 s, Ha OIII SII at 180 s, 45 cycles) on a 3x2:
  - `6 panels x 7 filters x 45 = 1890 subs`
  - `9.75 h per panel, 58.5 h in all`
  - `270 visits at 1 pass per visit`
  - `hop: not measured on this rig yet`, or once it has been measured, `hop 2 m 40 s, measured over 6 hops`. The efficiency figure appears only with a measured hop.
  - `meridian: no idle before the flip` for this 3x2 at Dec 41 (the pre-flip idle of 5.7, computed from the RA span of the panel centres, the plan's flip lead and the hop; none of these is site data). A compact 2x2 of 0.9 x 0.6 deg reads `meridian: up to 10.6 min idle before the flip`.
  - `focus: refocus on temperature` when the focuser temperature delta is armed, or `focus: a sweep only at the first panel; set a temperature delta to refocus as the night cools` when neither it nor `autofocus_every` is set (5.6).
- With `CAP_VIEW_SITE_DERIVED`: "this is a campaign: about 7.8 nights of 7.5 h before hops. The session stays armed and resumes at the next dusk." The old advice "Set DUSK to repeat nightly" is dropped because it changes nothing: `engine.start` arms auto-resume on every run (`engine.py:795`). DUSK's "Single night" is replaced by the option "Automatic resume on subsequent nights until capture quota is fulfilled", default ON (Revision 2, ruling 7, #195). When it is off, this line reads "this is a campaign of about 7.8 nights, and automatic resume is off for this flow: continue it by hand each night".

**CENTRING**

- Tolerance in arcmin (default 1.2) and tries (default 3). These equal what actually runs today: 0.02 deg and 3 (`hub.py:6387-6391`).
- "If a panel will not centre or reach its angle": Auto / Skip it this pass / Shoot anyway. Auto means skip for a mosaic panel and shoot for a single target.

### 2.5 DONE

- DONE stays locked, with the reason "waiting for the server's panel positions", until `POST /api/framing/mosaic` has answered for the current spec.
- Offline, or as a viewer (that route needs `CAP_VIEW_SITE_DERIVED`), DONE unlocks with the chip "panels from the offline mirror; the run computes them on the server". That is true, because `to_plan` calls the same `compute_mosaic`.
- DONE commits **one** new slice action, `flowsApplyFraming(id, patch, loop)`. It writes every changed param and adds or removes the loop wire in a single slice write, with one dirty/compile cycle. `flowsSetParam` writes one key per call today (`flowsSlice.ts:264-279`). Coercion follows each default's type, as `flowsSetParam` does.
- The card turns valid only after the compiler answers.
- **Re-frame carry or restart** (Revision 2, ruling 3). The readout strip shows how far the new layout moves against the block's anchor (3.3), live: `moved 4.2' of the 10.0' this grid allows: counts carry over`. Only when the move reaches the threshold, while the progress route reports banked subs, does DONE ask first: "Re-framing moves the panels 14.8', more than the 10.0' this grid allows, so all 6 panels start from zero: 212 banked subs belong to the old layout and stay on disk." Changing rows or cols always restarts, with the same question. The numbers come from the server (`POST /api/framing/mosaic` with the anchor, 3.3). Offline, the mirror computes them and the chip says "the server decides at save".
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
| `counts` | none (not offered; Revision 2, ruling 2) | `Every sub taken` (kept on load) | `Accepted subs` | sets `plan.count_mode`. The save path rewrites any other value to `Accepted subs` and says so |
| `frameAnchor` | none (server-written) | `""` (the current geometry is the anchor) | written at first save | canonical JSON of the geometry the counts started at; the identity key (3.3, Revision 2, ruling 3) |

`whenWaiting` is not a TARGET param. It is the flow-level setting `FlowGraph.settings.whenWaiting` (1.6, Revision 2, ruling 1). POOL gains the same `counts` treatment as TARGET (created `Accepted subs`, missing-key `Every sub taken`, switched at save), so a new pool-only flow is not the one new flow that counts rejects. DUSK WINDOW's `repeat` is replaced by `autoResume` (Revision 2, ruling 7).

FILTER CYCLE and CAPTURE LOOP gain the `pass` event output. TARGET gains `next`. No `FieldDef` control is added; `FieldDef` gains an optional `help` string for DUSK's info icon (ruling 7). The node-type count stays 21.

### 3.2 Compiled dict (`compile_plan`, still pure: no devices, no config, no clock)

A TARGET node still emits **one** entry, so the PLAN tab shows the compile verbatim and one block reads as one mosaic:

```
{"name", "ra", "dec", "rotation_deg", "node_id",
 "angle": "any" | "rotate" | "fixed",
 "mosaic": null | {"rows", "cols", "overlap", "fov_x", "fov_y", "fov_from",
                   "skip": [[r, c], ...], "order", "passes", "visit_min",
                   "require_centred", "when_waiting"},   # when_waiting copied from graph.settings
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

- `geometry_key` = the first 16 hex characters of sha256 over canonical JSON of `ra_hours` and `dec_deg` (to 1e-6), `rows`, `cols`, `overlap` (to 1e-4), `rotation_deg` (to 1e-3), and `fov_x`, `fov_y` (to 1e-5), taken from the block's **anchor**, not from its current geometry (Revision 2, ruling 3). **`skip` is excluded.** The compile entry carries the anchor as `mosaic.frame_anchor` (or on the entry for a 1x1 block); with no anchor (an unsaved preview) the current geometry is the anchor, so `compile_plan` stays pure.
- **The anchor rule.** `frameAnchor` is written by the server on every save (`_persist_flow`, so POST, PUT, the wizard and the quick flow alike), through one pure function in `catalog/framing.py`, `reframe_carry(anchor, new) -> {carry, max_move_deg, threshold_deg}`:
  - no anchor yet (first save, or a flow saved before S3): the anchor becomes the current geometry.
  - `rows` or `cols` differ from the anchor's, or `rotation` crosses between "any angle" (negative) and a set angle: re-anchor. The counts restart.
  - otherwise lay out both geometries with `compute_mosaic`, place each panel's four corners at its angle, and take `max_move_deg`, the largest angular distance any corner moves. The corners, not the centres, make one number cover a shift, a turn (which moves the corners of a 1x1 panel although its centre stays put) and a change of camera field.
  - `threshold_deg = REFRAME_CARRY_FRACTION x w`, where `w` is the overlap width of the **anchor's** grid: `overlap x fov_x` between columns and `overlap x fov_y` between rows, the smaller of those that exist (for a 1x1 block, `overlap x min(fov_x, fov_y)`). `REFRAME_CARRY_FRACTION = 0.5` is a named constant: the owner's first guess ("less than half the width of the overlap"), to be revisited with S7 data.
  - `max_move_deg < threshold_deg`: keep the anchor, and the counts carry. Otherwise re-anchor, and the counts restart.
  - A block with no camera field (`fov_x = fov_y = 0`) has a threshold of 0, so only an unchanged geometry keeps its anchor. With no field recorded there is no measure of "a little".
  - The anchor is compared with the new geometry, never with the previous save, so moves under the threshold cannot add up across saves.
  - Previews and announcements use the same function. `POST /api/framing/mosaic` takes an optional `anchor` spec and adds `reframe` to its answer, which the modal shows live (2.5). The save's answer lists every block it re-anchored (`reanchored: [{node_id, max_move_deg, threshold_deg}]`), so a raw field edit in the inspector that restarts counts is announced by a toast, and CONTINUE's dropped-steps 409 (5.9) still guards the ledger.
  - The carry has a cost the constant must be judged against. A.2 already budgets half the overlap for convergence and angle error and leaves the other half for pointing error. A carried move at the limit spends that second half, so the full-depth overlap of a carried panel can shrink to nothing at the seam. The stitched union still has no hole. That is why the constant is a first guess, and S7 measures the centring residual it competes with.
- Computed thresholds (from `compute_mosaic`, 25% overlap unless stated; Appendix A.5 has the method): a 3x2 of 2.0 x 1.33 deg panels allows 10.0' (a 3.4 deg turn, or a 6.0% change of field, reaches it); a single row of three such panels allows 15.0'; a 3x3 at 15% overlap allows 6.0' (1.75 deg of turn); a 2x2 of 0.9 x 0.6 deg allows 4.5'.
- **A TARGET with a name and no typed coordinates is keyed on the canonical identity the catalogue resolves its name to** (S1 hardening, #189 A5; H2, #229). `identity.target_key` is the one place this is decided; `to_plan` mints the id through it and the progress route finds the id again through it. Typed coordinates (an RA and a Dec both present, `identity.typed_coordinates`) key on the geometry as above. A name alone is resolved by `tonight.resolve_target`, the one resolver `to_plan` and the progress route both ask, which answers the coordinates, the canonical identity and whether the row moves: the catalogue id for a fixed row (a deep-sky object, a star, a typed position) and the canonical body name for a time-dependent one (a planet, the Moon, a comet, a satellite). The caller passes that identity to `target_key`, so `identity` stays pure, and it keys on `name_key`: `"name:"` followed by the first 16 hex characters of sha256 over `canonical_name`, the canonical JSON of the canonical identity with the angle and the grid spelled as in the geometry key. Such a TARGET's coordinates are the catalogue's answer at the compile's `when`, and for a planet, the Moon or a comet that answer moves by the hour, so keyed on the geometry every compile named new steps and night two banked on nothing night one shot. **A rename keeps the ids when the new name resolves to the same row**: "M 31", "M31", "m31" and "Andromeda" are all `M31`, and "jupiter" is `Jupiter`. A rename to a name that resolves to another row re-keys, and its counts restart, as a move does. Keyed on the name as typed, which A5 first did, a spelling edit restarted a deep-sky campaign. The prefix keeps a name key from ever equalling a geometry key. S1 has no grid on a TARGET, so both keys take the single-target shape; S3 passes the block's grid to the same two keys.
- `group_id = uuid5(NS_FLOWS, f"{flow_id}/{node_id}/{key}").hex`, where `key` is `geometry_key`, or the name key of the bullet above.
- `target_id = uuid5(NS_FLOWS, f"{group_id}/r{row}c{col}").hex`. A 1x1 block uses r0c0, so single targets gain continuity too.
- Pool members: `uuid5(NS_FLOWS, f"{flow_id}/{node_id}/member/{name}").hex`, keyed on the name and never the rank. A second copy of one name in the members box is keyed `{name}#1` (`identity.member_key`), so adding it later re-keys nothing.
- `step_id = uuid5(NS_FLOWS, f"{target_id}/{stage_node_id}/{signature}/{n}").hex`, where `signature` is `identity.step_signature`, `{frame_type}/{filter}/{exposure}/{gain}/{binning}` with no filter spelled empty, and `n` is the occurrence index among identical signatures inside that stage (almost always 0). Each number is spelled to `STEP_PLACES` = 6 decimals with trailing zeros and a bare trailing point trimmed: the exposure is keyed to the microsecond, and gain and binning share the six places, written as integers when integral and to at most 6 decimals otherwise. A 1234.5678 s L sub at gain 100 and bin 1 has the signature `Light/L/1234.5678/100/1`, and gain 100.5 is spelled `100.5`.
  - Why the microsecond (S1 hardening, #189 A6; H2, #189): S1 first spelled the numbers with `{:g}`, which keeps six significant digits, so every exposure of 1000 s or more lost its milliseconds, and 1234.567 s and 1234.568 s shared one count, the #77 fault. The hardening round spelled them to the millisecond, which folded the other end of the range: a planetary sub of 0.5 ms and one of 1 ms were both `0.001`, one count, and 0.1 ms was `0`. Six places keep 0.1 ms, 0.5 ms and 1 ms three recipes. No S1 or H1 id was ever deployed, so neither change needed a migration.
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

**`plan.count_mode = "accepted"`** when any block asks for accepted subs. A disagreement is M7. After ruling 2 a disagreement can only come from a graph written by hand or through the API, because every save rewrites `counts` to accepted subs.

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

class VisitBound:                            # sequence/group_rules.py; engine-internal, never persisted
    passes: int | None                       # rounds per visit (group members)
    min_s: float = 0.0                       # at least this long, checked at round boundaries
    deadline_ts: float | None = None         # end at the first frame boundary that
                                             # cannot fit the next frame: the panel's
                                             # flip point (5.7) or group_ready_ts (1.6)

# Session gains (additive, default empty, SESSION_SCHEMA stays 1):
set_aside: list[dict] = []                   # {target_id, step_id | None, reason, night}
locked_angles: dict[str, dict] = {}          # {target_id: {pa_deg, solved_at, exposed_at, source}}

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
- `Session.set_aside` makes "set aside for tonight" survive a crash: a crash-resume on the same night reads the records whose `night` matches the durable log's night key and does not retry those panels. A later night ignores them. As built (S2, #208): a record is written only through `Session.note_set_aside`, with the `events.night_key()` of the moment, the key the durable log is named by, and saved at once (`_persist_set_aside`); one is made for a panel set aside whole, for a target that sank below its own floor, and for a step its reject guard set aside (`step_id`); and `start` reads tonight's back (`Session.set_aside_on`), so a restart tonight skips them, and skips a target whose every owed step is set aside. `Session` has no `extra="forbid"` (`session.py:70`), so a build that predates the field loads the file and ignores the field. It then retries set-aside panels, which is today's behaviour.
- `Session.locked_angles` holds Revision 2 ruling 9's lock per target id (S2, #189), written only through `Session.lock_angle`: the first lock wins, a later call returns the lock in force, and an angle that is not a finite number is refused, because the API renders JSON without NaN and every route that returns the session would fail. Like `set_aside` it is additive, with `SESSION_SCHEMA` still 1.

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

- v2 to v3 rewrites a target's `rotation == 23.4` to `-1`, the palette default nobody chose. It sets a non-persisted `FlowRecord.migrated` note: "angle 23.4 was the old palette default and commanded a connected rotator to PA 23.4; it now reads 'any angle'. Set it again if you meant it." The note is shown on every read until the operator saves: the editor's, and every run's log. Only `save()` stamps FLOW_SCHEMA, and the bookkeeping writers (`touch_run`) edit the raw file and keep its version, so an unsaved v2 file reads as v2 each time. A save is the one point at which the operator has seen the migrated graph and kept it.
- `_migrate` **refuses** `schema_version > FLOW_SCHEMA`. Today it returns any file at version 2 or above unchanged (`store.py:52-73`), so a future file whose node types are all known would load with this build's meaning. `_on_disk` also silently skips files that fail (`store.py:93-105`). From S0, both kinds of file are listed as visible library rows: "saved by a newer AstroDeck (schema 4); update to open it", or "unreadable: <reason>".
- The writer stamps 3 on every save, as evidence that a 23.4 saved after migration was deliberate.

**FLOW_SCHEMA 4** (slice S3). The v3 to v4 read maps DUSK's `repeat`, whatever its value, to `autoResume = On` with the note of Revision 2, ruling 7, shown like the 23.4 note on every read until the operator saves; otherwise it is a no-op. (If #195 ships before S3, it takes the next schema number itself, with the same rule.) The writer stamps 4 when the file uses any meaning a v3 build would misread:

- a multi-panel block
- `angle = Camera fixed at PA`
- a loop wire
- `counts = Accepted subs`
- `settings.whenWaiting = Wait for the mosaic`
- `autoResume = Off`

Otherwise it stamps 3. Because every save rewrites `counts` to accepted subs (ruling 2), in practice every flow saved on an S3 build stamps 4.

**Downgrade matrix**

| File | Read by | Result |
|---|---|---|
| mosaic flow (v4) | S0-S2 build | refused loudly as future schema |
| rotating mosaic flow (v4, with the loop wire) | a build older than S0 (today's 0.3.32) | `validation_errors` refuses the wire, because the old build has no `pass` output or `next` input ("cycle has no output port 'pass'"), so save and `/run` answer 422. Safe. |
| unlooped mosaic flow (v4, loop wire deleted) | a build older than S0 | loads, `rows`/`cols` ignored, shoots the centre |
| any flow saved on an S3 build (v4, because `counts` was switched at save) | S0-S2 build | refused loudly as future schema, listed as "saved by a newer AstroDeck" |
| any flow saved on an S3 build | a build older than S0 | loads; `counts` is ignored, so it counts every sub again, and `autoResume = Off` is ignored, so it resumes on later nights |

The unlooped-mosaic row and the last row are the residual risks. Mitigation: ship S0 at least one release before S3, so a downgrade from S3 lands on a build that refuses v4.

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
GroupRun: pass_no, visited: set[target_id], exposures_this_pass: int,
          deferred_this_pass: int, failed: dict[target_id, int],
          reject_visits: dict[target_id, int],
          set_aside: dict[target_id, reason], completed: set[target_id],
          flipped: bool, acquired: bool, angle_verified: bool
```

`set_aside` is also written to `Session.set_aside` (3.4), so a crash-resume the same night does not retry those panels. Everything else is recomputed. S2 built it (S2, #189) as `GroupRun` in `sequence/group_rules.py`, pure and engine-free, with one change: a pass counts its exposures by summing each visit's own count (`exposures_this_pass`), the number a snapshot of the ledger would give, without walking it.

**Selection** (`engine.py:1924-1938`). For a member of a group, `gating_status` still decides first: frozen window, altitude start gate, moon, hour angle. A member that gating calls ready must also pass an **eligibility** check:

1. **Reachability.** `_mount_floor_verdict(target, projected=True) -> ReachVerdict | None` is the non-raising twin factored out of `_enforce_mount_floor`, which raises exactly on its answer, so there is one predicate: floor, horizon and obstruction mask, no-go wedges, pier limit, zenith keep-out (S2, #132; `_eligibility_now` asks it at selection). The verdict is tagged (`REACH_TAGS`), because waiting fixes some of these and not others:
   - `wait`: floor, mask and wedge (the `floor` kind: `schedule.effective_floor` folds the three into one number) and the zenith keep-out (the `ceiling` kind). Time changes these. The pier limit is not a wait: the gate's only pier refusal is the flips-off change below, and with flips on it lets a side change through to the meridian rule, which waits for the crossing (5.7). The member is **waiting**, with `wake_ts = now + REACH_RECHECK_S` (60 s, a named constant in `sequence/group_rules.py`) as the re-evaluation cadence. It joins `earliest` like any waiter, so the existing do-while wait path runs (`engine.py:2013-2058`) through `_wait_until` (`engine.py:2133-2177`), with the safety gate and the deadman armed.
   - `refuse`: no saved site while limits are configured (`engine.py:4578-4585`), and a pier-side change with meridian flips off (`engine.py:4531-4538`). Waiting cannot fix either one. No site refuses every panel, so it raises the gate's own `SlewRefused`, a `SafetyAbort`, exactly as the slew gate does. A pier-side change with flips off sets **that panel** aside tonight with the gate's own sentence, while the panels reachable from the current side keep shooting.
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
| `PanelDeferred` (section 5.6), including a guide start that failed (5.6 step 7, Revision 2, ruling 5) | mark it visited, which puts it behind every unvisited member, so it is retried on the next pass. Add 1 to `failed[p]` and `deferred_this_pass`. At `max_failed_visits` (3) consecutive failures: set it aside tonight, with a **warning** alert that names the panel, the reason and the last error ("guiding did not start on 1-2 on 3 consecutive passes: <error>"), then `reporter.mark_skipped`. It is retried the next night. |
| `StopTarget` from a closed window | handled by the existing all-closed path. Every panel shares the frozen window (`engine.py:1897-1903`). |
| `StopTarget` from the altitude floor (`on_floor = advance`) | set that panel aside tonight. It is not done. S2 raises it as `FloorStop`, a `StopTarget` only the scheduler tells apart, and records it in `Session.set_aside` for a single target as well (#208). A guide-start failure on a group member no longer escalates to a per-panel skip; it defers (row above). Only a pass in which every attempted panel failed reaches `guiding_action`, and its skip sets the whole group aside tonight (5.6 step 7). |
| `JumpTarget` | unchanged, via `_apply_jump` (`engine.py:2062`) |
| `SafetyAbort`, `NightQualityStop`, cancellation | propagate unchanged. A mosaic never downgrades a safety abort to a panel skip. |

**Pass boundary.** When no unvisited member is eligible but a visited member is, the pass ends.

1. Exposures this pass = 0 and deferrals = 0. Log "a full pass over N panels took no exposures; setting the mosaic aside for tonight", and set every live member aside. This lifts the anti-spin rule at `engine.py:2818-2824` to group level, but it counts **exposures** (accepted plus rejected), so a clouded pass of rejects does not end a mosaic. The reject guards handle that.
2. Exposures = 0 and deferrals > 0. `await _wait_until(now + DEFER_WAIT_S)` (300 s, a named constant), then start a new pass. The per-panel `max_failed_visits` bounds this at three consecutive all-deferred passes per panel.
3. Otherwise, start pass N+1: clear `visited`, snapshot the counts, and re-sort the group's slice of `remaining`.

S2 built the boundary in `GroupRun.close_pass` (#189), with two outcomes the list leaves implicit: a pass in which every attempted panel failed to start guiding hands the decision to `guiding_action` before any of the three (5.6 step 7), and a boundary with no live member left ends the group at once, rather than waiting `DEFER_WAIT_S` for nothing. A failed guide start is held until the boundary and counted there, because only the whole pass shows whether the guider or the panel is to blame.

If no member is eligible at all, the group is waiting and the existing wait path runs, with the idle-clock park-hold above. Under the default `whenWaiting`, a follower may fill the wait in bounded visits (1.6).

**Why the per-panel reject rule.** With the S0 fix, the per-step guard spans visits, and at one pass per visit it takes `max_consecutive_rejects` (10) visits to trip. A panel that always rejects (a star-poor field under the `min_stars` gate, or trailed frames caught by the eccentricity gate) would burn 10 x 780 s = 2.17 h of shutter per night before every step tripped. The panel rule bounds it at 3 x 780 s = 39 min plus three hops. The opposite failure, unguided frames that are **accepted** (guiding_action defaults to warn, `config.py:281`, and #142 lets those frames through the RMS gate), is not a reject. The owner's guide-start ruling (5.6 step 7, Revision 2, ruling 5) closes it for mosaic panels: a panel whose guider will not start is deferred, never shot unguided.

**Sequential mode** (no loop wire) uses the same machinery without the visit bound: the chosen panel runs to completion. The order policy, set-aside, deferral and meridian rules still apply.

### 5.2 Panel ordering

`sequence/panel_order.py` is a pure, unit-tested function (`order_panels`). The scheduler and ResumeArm (section 5.9) use it (S2, #159); the progress route does not yet. Counts are snapshotted **once per pass**, because `accepted_by_step()` walks every frame (`session.py:110-121`): the scheduler re-sorts the group's slice at the run's start and at each pass boundary (`_resort_group`). `setting_first` reads the scheduler's `_time_to_floor_s`, a 60 s forward scan capped at `SETTING_SCAN_S` (12 h) over the mount's floor, mask and wedges and the panel's own floor; ResumeArm has no such time, so there it orders by fraction banked and then snake index.

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

`_run_steps(ti, target, visit=VisitBound(passes, min_s, deadline_ts))`. Group members and bounded followers (1.6) pass a bound; everyone else gets `None`, as today. S2 built it (S2, #189): with a bound `_run_steps` hands the visit to `_run_visit`, and with `None` it runs exactly as before. A follower's bound has no passes (`passes=None`), so its visit ends only at its deadline or its completion, and a follower with blocks acquisition keeps its block order under the deadline, one frame per `_run_step` call. A sequential member carries a bound only to stop at its flip point (5.7). A visit whose hop outran the hop estimate, so that its deadline came before its first frame, is not a visit: the panel is requeued unvisited and counts toward no pass rule, because a pass whose only visit shot nothing would read as a pass of no exposures and set the mosaic aside (`_visit_panel`).

- **Cycle acquisition.** Rounds proceed as today (`engine.py:2807-2824`). After each round, the visit ends if the panel is complete, or if `rounds >= passes` **and** `elapsed >= min_s`. The clock starts at the visit's first exposure, so the hop does not eat into the visit.
- **Deadline.** Before each `_run_step` call, which is one frame at `per_visit = 1`, the visit ends if `now + exposure + overhead EMA + FLIP_FRAME_MARGIN_S` (30 s, `engine.py:170`) would pass `deadline_ts`. That is the same budget `_maybe_meridian_flip` uses for its frame window (`engine.py:5294-5295`), asked through `VisitBound.next_frame_fits`. A flip-point deadline is taken in clock seconds, not in hours of hour angle, which run 0.27% fast, so a frame the visit lets through never trips the flip gate (#300). A deadline can therefore end a visit mid-round.
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
2. **Stand the guider down first**, bounded, via `_stand_down_guider` (`engine.py:4394`). The native `start_guiding` returns at once while its loop is alive (`guide/native.py:734-740`), so the next target could inherit a loop still chasing the old star and skip the pier-side calibration mirror (`native.py:871`). S1 showed it on the simulator with the real native guider before the fix (`test_guider_stand_down_before_slew.py`, #148, I-02); it is still unverified on the rig. **RULING (S1 hardening, 2026-09-24): the stand-down happens only when `plan.guide` is set.** Then the engine started the guider, and it is the engine's to stop. A guider the operator started under a guiding-off plan is theirs, and a hop does not stop it (`_setup_target`).
3. `hub.goto_and_center(ra, dec, tolerance_deg = tol/60, max_attempts = tries, rotation_deg = <the commanded angle>)`, bounded by `GOTO_TIMEOUT_S` plus 300 s when rotating (`engine.py:185`, `:2355-2362`). The commanded angle is `_commanded_rotation(target)` (S2, #189): the target's planned `rotation_deg`, or else the angle it locked on its first shot (Revision 2, ruling 9), which is commanded only to a connected rotator.
   - The hub already accepts tolerance and attempts (`hub.py:6387-6391`). The engine has never passed them (`engine.py:2357-2359`), so every SLEW + CENTER tolerance anyone typed was ignored. That is a present-day defect and is filed on its own.
   - `None` keeps today's exact call.
   - The tracking-refusal recovery must report what it measured. Today `_setup_target` replaces the recovery's outcome with a fabricated `{"centered": True, "error_arcmin": None}` (`engine.py:2373`), although the recovery ran its own `goto_and_center` and may have logged that it did not converge (`engine.py:6798-6809`). S1 makes `_recover_from_tracking_refusal` return that result, and `_setup_target` uses it, so `centered` is always a measurement.
   - The hub rotates before it centres (`hub.py:6448-6475`). The shortcut is built (S2, U-06, #189): `Hub._rotation_already_set` skips the pre-rotate slew and the rotate loop when the newest sky-angle record says calibrated, the rig's rotator is synced and holds the offset that record wrote, its mechanical angle is within `sky_angle.MOVED_TOL_DEG` of the record's and it is not moving, and it reads the target within `RotatorConfig.tolerance_deg` mod 180. The centring attempts still slew and solve.
4. **[group] Centring and angle checks.** S2 built them (S2, U-04, #189): `_group_hop_checks` for the centring and the rotation flags, then `_group_angle_check` for the angle, which asks `angle_check.angle_verdict` and acts on `group_rules.angle_decision`. Only an `ok` verdict marks the angle verified.
   - `centered: False` under `require_centred` raises `PanelDeferred`.
   - With `rotate` set, `rotation_skipped` raises `PanelDeferred`, and so does the new `rotation_unavailable` flag, which the hub sets when a rotation was asked for and no rotator is connected. Today that case is silent (`hub.py:6455-6456`, I-15).
   - **On every hop, whatever the rotator state**, the engine compares the sky angle this centring solve recorded (`sky_angle.note_solved_rotation`, `sky_angle.py:228`; its `exposed_at` must be at or after the hop start) with `group.pa_deg`, mod 180. Both are CROTA2-convention numbers, so the check does not depend on #145.
   - Beyond `angle_tolerance_deg`:
     - rotate mode: `PanelDeferred`
     - camera-fixed mode: the whole group is set aside at once, because a fixed camera cannot fix itself. The hop raises `GroupSetAside`, which `_visit_panel` catches: every live member is recorded and saved as set aside, with one alert. The warning alert names both numbers: "the camera reads PA 37.2 and this mosaic is laid out at 30.0; turn the camera or re-frame at the measured angle".
   - **No fresh measurement.** The hop may yield no sky angle at or after the hop start. That happens when the centring solve failed and the hub fell back to a raw GoTo (`centered: False`, `error_arcmin: None`), when the solver reported no rotation (`rotation_known` false, which `sky_angle.py:252-253` now drops, #146), or when the hop came through the tracking-refusal recovery without a solve. Then:
     - With `require_centred` (the default), `centered: False` has already raised `PanelDeferred`, so the case never reaches the angle check.
     - Camera-fixed mode, once the angle has been verified on an earlier hop tonight (`_GroupRun.angle_verified`): shoot, and log "angle not re-measured on this hop; the camera is fixed and read 30.4 at 1-2". A fixed camera cannot turn between hops.
     - Camera-fixed mode, never verified tonight: `PanelDeferred("angle not measured")`. Tiles are never laid blind.
     - Rotate mode with a calibrated rotator that reports its target position and no `rotation_skipped`: shoot with a warning. The rotator's own read is the evidence (`_rotator_evidence`: connected, synced, not moving, and reading `pa_deg` within `RotatorConfig.tolerance_deg` mod 180, on a hop that reported neither `rotation_skipped` nor `rotation_unavailable`).
     - Rotate mode otherwise: `PanelDeferred`.
   - "Shoot anyway" turns these into warnings.
   - A 1x1 block with a planned angle only ever warns: a layout of one panel, the skipped panels counted as tiles of it.
5. **[group] Pier-side verification** (section 5.7). If the side read after the goto contradicts the group's side, raise `PanelDeferred("the mount chose the other pier side")`. S2 built it (S2, #136) as `_group_pier_check`: the group's side is the side its first hop read tonight, and after its pier change the side that change read, a measurement and never a convention; a hop that contradicts it defers (kind `pier_side`), and a hop past the meridian that reads the new side is the group's pier change (5.7).
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
7. **Guiding** restarts with the existing bounded start (`engine.py:2450-2500`). The native guider mirrors its calibration on a pier change inside that start.
   - **[group] A failed start defers the panel** (owner ruling 5, 2026-09-24: "try again after the next go around through the other panels. No point in leaving a hole in the mosaic if we don't have to"). When the plan asks for guiding and the start fails or no guider is connected, a group member raises `PanelDeferred("guiding did not start: <error>")`, **whatever `guiding_action` says**. Today's escalations would each leave a hole: warn shoots the panel unguided (and #142 lets those frames through), skip sets it aside at once, abort ends the night over one panel's guide star. Single targets keep today's escalations.
   - The panel moves to the back of the rotation and is retried on the next pass (5.1). In sequential mode it moves behind the unvisited panels and is retried when next selected.
   - **The bound is `max_failed_visits` = 3 consecutive deferred passes**, the counter every `PanelDeferred` shares, reset by a visit that banks an accepted frame. Why three: the retries are a full rotation apart. On a 3x2 at the default cycle one visit is about 16.7 min (780 s of shutter, 70 s of overhead, a 150 s hop; A.3), so the next attempt comes about 83 min later and three attempts span about 2.8 h (a 2x2: 50 min apart, 1.7 h in all). A cloud over the guide star, a star at the edge of the guide camera or a calibration walked after the flip clears inside that span, so two failures are still inside the range of transient faults, the same reasoning as `RESUME_GIVE_UP_AFTER = 3`. Three failures across nearly three hours mean the panel has no usable guide star tonight. Each failed attempt costs a hop plus the bounded start (`GUIDE_START_TIMEOUT_S` 180 s, or `GUIDE_CALIBRATE_TIMEOUT_S` 660 s when a calibration must be walked), 5.5 to 13.5 min and never an exposure, so a starving panel costs at most 16.5 to 40.5 min a night. Four would cost up to 54 min for little more evidence.
   - **A guider that fails on every panel is the rig's fault, not a panel's.** If at least two panels were attempted in a pass and every one of them failed to start guiding, the per-panel counters do not advance (the same rule as "when every member rejects, the sky is to blame", 5.1). The rig's `guiding_action` then decides, exactly as it does for a single target: abort ends the run, skip sets the group aside tonight, and warn continues the panels unguided. This bounds a dead guider (the #133 and #135 shape) at one wasted pass, about 33 min on a 3x2, instead of three passes and about 109 min. It is the one place the rig-wide action still applies to a mosaic, and the owner is asked to confirm it.
   - A guiding loss mid-visit that the #72 recovery bound gives up on ends the visit with the same `PanelDeferred`, because the next hop restarts guiding anyway. This extends the ruling from "fails to start" to "fails to recover", and is marked for the owner to confirm. Not built in S2 (S2, #303): `PanelDeferred` has the kind `guide_lost` and nothing raises it, so such a loss on a member takes the rig's `guiding_action` as it does for a single target: abort ends the run, skip drops the panel for this run only, and warn shoots it unguided.
8. `_arm_meridian_flip(target)` (`engine.py:2510-2561`) re-arms the latch as a backstop.
9. **Dither**: `_frames_since_dither = 0` on every acquisition. A fresh centre is a new pointing, so its first frame spends no dither. Today the counter spans targets (`engine.py:2895-2914`, I-21).
10. Record the hop's wall time as event cost `"hop"` (`_record_event_cost`, `engine.py:956`). Set `_progress_expected = True` and re-anchor the no-progress clock.

Cloud holds and safety pauses return through the current panel's `_setup_target`, as they do for any target today (`engine.py:3783-3789`, `:4109-4112`), with focus freshness applied.

### 5.7 Meridian flip: at most one pier change per group per night

**Keep the flip-owed invariant armed per target.** `_pre_flip_side` becomes a dict keyed by target id (`engine.py:613`, `:827`, `:6415-6436`), bundled with #136 as a comment there rather than a new issue. A panel re-acquired in the flip-lead window keeps its own pre-flip record. That window is where the AM5 stays on the pre-flip side, because it picks its side from the hour angle (`hub.py:6582-6585`). A's reset-on-acquisition would have erased the backstop in exactly that case.

**The record is kept all run** (#237, H3 orchestrator ruling 2), a flip included. `_enforce_flip_owed` writes a target's pre-flip side with `setdefault` only, at its first sighting east of the meridian, and never refreshes it: the flip gate fires at the plan's lead, before the meridian, so on a mount that can flip early a refresh would record the side the mount flipped to, and a target that flipped correctly would be held past the meridian and set aside (#222). Nothing clears the record within the run. The side a target occupies before its flip is a fixed property of its east side, the side a German mount tracks it on there counterweight-down, so the record is as true after the flip as before it, and it is still needed then: a mount that picks its side from the hour angle lands a later goto to the target east of its meridian on that side again, as a mosaic hopping back to a panel it flipped early does, and if that re-acquisition's own flip does not fire, only the record refuses the frame carried past the meridian. The one other writer is the flip gate, `_maybe_meridian_flip`: a flip it measured (both sides readable and different) records the side it left, by the same `setdefault`, which is that target's pre-flip side by definition and matters only when the flip came before any sighting, as it does when a target's first frame falls inside the lead; an unmeasured flip records nothing. The second hardening round cleared the record after a measured flip and marked the target so that nothing wrote it again that run, which let exactly that re-acquisition through; the clear and the mark are gone. The record is cleared at run start and nowhere else.

**Hysteresis.** For each member p, let `h_p = schedule.hours_to_meridian_flip(p.ra, lon)`. It is positive east of the meridian and at or below 0 after the crossing (`schedule.py:517`). Let `hop_h` be the hop EMA (150 s until measured), and `f_h` the first owed frame of that panel: exposure plus per-frame overhead EMA plus `FLIP_FRAME_MARGIN_S` (30 s, `engine.py:170`). Let `lead_h` be the **plan's** flip lead, `plan.meridian_flip_lead_min` (default 10 min, `schedule.py:562`), in hours.

| Group state | Eligible | Waiting (wake time) |
|---|---|---|
| not flipped tonight | pre-flip panels with room for a hop and one frame before their flip point: `h_p - lead_h >= hop_h + f_h`. The visit carries `deadline_ts = now + (h_p - lead_h)`, so it ends at a frame boundary before the flip point (5.3). Post-meridian panels (`h_p <= 0`) only while no pre-flip panel is eligible. | panels with `h_p > 0` and `h_p - lead_h < hop_h + f_h`. Wake at `now + h_p`, the crossing. |
| flipped tonight | post-meridian panels (`h_p <= 0`) | every other panel, until it crosses. Wake at `now + h_p`. |

S2 built the rule (S2, #136) in `_meridian_now`, over `group_rules.meridian_eligibility`, with one band the table leaves out: a panel counts as past the meridian (`h_p <= 0` above) only `MERIDIAN_SIDE_MARGIN_S` (15 s) after its crossing, because a goto at the crossing leaves the side to the mount's own reckoning of the hour angle (the simulator's unsynced goto lands 7 s of RA east); the AM5's own boundary is not measured (S7). `h_p` is the countdown the flip gate reads, and an eligible visit's deadline is its flip point in clock seconds (5.3). With flips off, or no saved site, the rule is off and every reachable member is eligible with no deadline.

**The margin is the plan's lead, never the learned one.** `_flip_lead_s` (`engine.py:5485-5519`) returns 0 in two cases: for a target whose early flip changed nothing (`_flip_no_op`), and for a mount that has been shown this before (#127, `_mount_flips_early() is False`). A zero is right for *when to attempt* a flip, since the AM5 cannot flip before transit. It is wrong as a margin, because the AM5 stops tracking 4.7 to 7.6 min **before** transit (measured on two targets, recorded at `engine.py:5273-5276`). With a zero margin, a pre-flip visit would run into the mount's own limit. The group reads its margin through `_group_flip_margin_s()`, which clamps `plan.meridian_flip_lead_min` the way `_flip_lead_s` does and never consults `_flip_no_op` or the learned trait. Test: with the trait set to "cannot flip early", a panel 8 min before transit is not eligible. Mutant "use `_flip_lead_s`" makes it eligible (`test_group_meridian.py`).

- The group becomes **flipped** when a member is acquired with `h_p <= 0` and the pier-side read after the hop confirms the new side, or when the latch flips a member mid-visit because a prediction was wrong. The hop that confirms it also disarms that panel's flip latch, since the flip it would owe is done; a hop past the meridian whose side cannot be read marks the group flipped, which keeps the one pier change, and leaves the latch armed (`_group_pier_check`).
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
- **A cloud hold watches the mount on its own clock** (#203, #205). The hold keeps tracking on by design, for up to `CLOUD_MAX_HOLD_MIN`, with no frame loop running, and the floor, flip and tracking checks live in the frame loop. So the hold runs them on the hold's own clock, never on its check exposures: the held target's live altitude against the floor and the zenith keep-out, and the plan's flip point, asked of the predicates the idle park-hold uses (`_altitude_limit_verdict`, `_idle_flip_due`); and a tracking read before each check exposure, because a check frame from a mount that has stopped at its limit is a streak, reads as cloud, and holds the run to its bound. The first hardening round (H1) built it (#203, #205): `_hold_for_clear` spends the wait between probes in looks at the mount (`_hold_watch`), and the look before a dark or a probe sees that whole exposure ahead. Before it, the hold ran none of the three. **With flips off, the hold takes no flip-point action** (H2 orchestrator ruling 1): it neither flips nor stops tracking at the flip point, as the frame loop never stops there when flips are off, and a mount that then stops at its own limit is found by the tracking read before the next check, which runs the frame loop's resume-then-recover. Until H2 a flips-off hold stopped tracking at the flip point, and with no flip to spend the latch nothing tracked again, so it judged no sky and ended at its bound however the sky cleared. The idle park-hold's stop runs on a clock of its own as well (6.17): the first attempt and every retry are made on one task (H2, #216), and a stop the mount does not confirm is asked again at most once per `IDLE_STOP_RETRY_S` (60 s), never from the wait loop's tick.
- **A hold points the mount at its own target before it judges the sky** (#203, #224, #225, H2 orchestrator ruling 2). After the zenith keep-out, the hold slews to where the target is now once the slew gate's projection allows it (`_hold_repoint`), because tracking on from where the mount stopped would follow that patch of sky up through the keep-out. A hold opened by a target setup's pre-slew safety gate holds a target B that the mount was never pointed at: the mount is still tracking the last target, stopped where that one was left, or parked. So as it opens, the hold slews to B through the slew gate and the Sun check, unparking a parked mount first, when B is above its floor: the floor and the keep-out the slew gate projects across a slew, and B's own `on_floor` floor (`_hold_repoint_refusal`). Then it watches and probes B. When the gate's projection would refuse, B is below its floor, or the slew fails, the hold stops tracking once (`_hold_park`), says why in the log and in its published detail, and every later look asks again. **A refused re-point keeps holding** (#240, H3 orchestrator ruling 3). The gate's pier guard and the Sun check are not asked first, since only the gate knows the pier side and the Sun; when either refuses the slew itself, it raises `SlewRefused`, the SafetyAbort every limit refusal of the slew gate now raises, and `_hold_repoint` catches it by type: the hold stops tracking once, says why in words and asks again on every later look, as it does for a refusal of the projection, and a refusal never ends the run. A mount call past its bound is a plain SafetyAbort, never a `SlewRefused`, and still ends the run under the engine's dead-link policy (P0-2), as it does from any other slew. The re-point asks the whole slew gate, the monitor's half included. A slew under cloud is allowed: cloud does not make a slew unsafe, and rain and wind are the safety monitor's, whose verdict already gates every slew. The last target's flip latch is disarmed while the mount is elsewhere, and the re-point arms B's own, so a look never flips B on the last target's hour angle. After any successful re-point, after the keep-out or from elsewhere, `_tracked_target` is B and the mount is no longer counted as stopped. There is no centring and no guiding: the hold needs only sky to judge, and whatever acquires B after it centres and guides. **One setup per acquisition** (#241, H3 orchestrator ruling 4). A hold opened by `_setup_target`'s pre-slew gate returns, on release, to the setup it interrupted, which carries on from where it was and makes the acquisition once: its slew, centring, focus sweep and guider start. A re-point the hold made before it is a slew of its own, made only to judge the sky. `_acquisition_behind_gate` names the target whose acquisition waits on a gate, set around the setup's pre-slew gate and around the gate the re-point asks, and a release for that target keeps only the cooler gate and the filter restore. The hold never runs `_setup_target` for it; until H3 it did, and the interrupted setup then acquired the target a second time. A safety pause and a roof reopen that such a gate opens follow the same rule. A hold opened from the frame loop has no setup behind it, and its release acquires the target through `_setup_target`; a safety pause or a roof reopen opened by that hold's own gate returns to the hold, which acquires the target once, when the sky clears (S2, #263): the hold marks its own gate's acquisition too (`_acquisition_behind_gate` around its `_safety_gate(context="frame")`), where the pause used to run a setup under the cloud and the release another. **After any stop of the mount, a hold re-points** (#248, H3 orchestrator ruling 6). A stopped mount's target has moved on at the sidereal rate, so a stop the engine made since the mount was last pointed counts as elsewhere, at the hold's open and when the tracking read finds the mount stopped. `_mount_stopped_since` records the first such stop (the idle park-hold, a safety pause, a roof close, a hold's own stop, the park of the refused-tracking recovery, `_recover_from_tracking_refusal`), and only a fresh pointing clears it (a setup's slew, a re-point, a flip's goto, that recovery's re-slew). `_tracked_target` says which target the mount was last pointed at, and no longer decides "elsewhere" alone. A mount that stopped on its own still takes the frame loop's resume-then-recover in place, and the opening detail comes back only once `_enforce_tracking` has returned, whatever step the hold borrowed its settings from. **Every line and detail is words** (#233, 6.9): the hold, the idle watch and the flip watch name the floor, the keep-out and the flip point, never an altitude, an azimuth or the minutes to a site-derived time. **A scheduler wait opens no target-less hold** (#221): a cloudy verdict with no target is said once per wait spell and published in `sky.hold_deferred` (`_note_hold_deferred`), the wait goes on under the idle park-hold, and the next target's pre-slew gate opens a hold with a target. Until H2 such a hold watched nothing and, on a stopped mount, read every check as cloud until its bound ended the run. **The published detail says what the hold is doing** (#228): it says the sky is being checked only once the mount is on the held target, and after a stop only once tracking is back, by a resume, a re-point or a flip; a stopped mount's detail says "the mount is stopped", why, and "The sky is not judged until it tracks again".

### 5.9 Resume mid-mosaic and across nights

**Within a session** (a crash, `/recover`, or auto-resume):

- Every banked frame is an atomic ledger save (`engine.py:4710-4782`). `done_map` seeds `_done` (`engine.py:806`, `session.py:154-163`).
- The first pass order is recomputed from the ledger, so there is no cursor to lose. A crash mid-visit loses at most the exposure in flight.
- ResumeArm `_recover` re-centres on the panel the shared order function picks, **with its rotation** (S2, #159). `recentre_candidates` walks the plan in the run's own order (`schedule.schedule_order`), leaves out a target that is complete, one set aside tonight and one that waits for a group (`after_group`) that still owes, and puts each group's live panels in `panel_order`'s order; the re-centre commands the planned angle, or else the locked one (`commanded_rotation`, ruling 9). When everything the session still owes is set aside tonight and no calibration is owed, it refuses in words before it touches a device (`nothing_to_shoot_tonight`), and that hold re-alerts every ten minutes (#284). It does not model the rest of the run's gating, a window not yet open or already closed and a moon or hour-angle hold (#283), and it commands a lock with no rotator connected, where the engine checks it instead (#295). Before S2 it took the first non-calibration target even when that target was complete, and passed no rotation (I-13).

**Across nights (CONTINUE).** `run_flow` compiles with `flow_id` and reads the flow's session through `SessionStore.current_for_flow` (S1 hardening, #189 A2): the newest session with `origin == "flow"` and `origin_id == flow_id` by `created_ts`, of any status, and never one older than it. An abandoned newest session means there is none, a dormant one is continued, and a complete one starts fresh:

- **Ordered by `created_ts`, not `updated_ts`.** `created_ts` is written once, by the start that made the session. `engine.start` saves every other armed session it disarms, which moves their `updated_ts`, so ordered by that, CONTINUE would get whichever session last lost the auto-resume singleton.
- **Never past the newest.** Whatever became of the newest session, an older one is a ledger the operator chose to leave. A START OVER leaves the old session dormant and unarmed for good; once the new session is abandoned or complete, "the newest dormant session" would reopen that old ledger unasked.
- **A complete newest session starts fresh**, because CONTINUE carries only a dormant one; reopening it when the flow now owes more is I-30. An abandoned one was closed by the operator, so there is nothing to continue.
- The progress route (1.2) reads the same session through the same method, so the card never counts toward a session Run would not continue.

If a dormant session's plan shares step ids with the new compile:

- `run_flow` applies the id-safe plan **replace**, factored out of `patch_session` (`app.py:5390-5398`) into a function. It is not a merge, and neither is today's code: `patch_session` computes a kept/new/dropped report and then replaces `s.plan` wholesale. The refusal on dropped steps below is **new logic** in CONTINUE. `patch_session` keeps reporting and never refusing.
- It then calls `engine.start(replan_cooling(plan, ...), session=s)`, the same call resume makes (`app.py:5350-5377`).
- **The continued plan keeps the session's `cool_to`** (S1 hardening, #189 A1). A flow has no cooling node, so tonight's compile carries tonight's standing setpoint, and replacing the plan with it would move a session shot at -10 C to tonight's -15 C. Subs at two sensor temperatures cannot share one dark library, which is why `replan_cooling` never re-resolves a plan that has a temperature. When tonight's setpoint differs, the run logs a warning after the start that names both temperatures and says START OVER begins a new session at tonight's. A cleared setpoint names no second temperature and is not warned about. A session with no temperature takes tonight's, as a resume does.
- The response names the session, the night number and the kept/new/dropped counts.

**The critical section.** Today `patch_session` loads (`app.py:5385`), checks for dormant, and saves (`app.py:5433`) in separate `to_thread` calls, outside `SessionStore._write_lock` (`session.py:243`). ResumeArm can start the same session in between (`resume_arm.py:396`), and the save then overwrites the file of a now-active session with stale frames. That is the one-starter race recorded on 2026-09-18, and CONTINUE must not inherit it. CONTINUE therefore:

1. Never saves the patched session itself. `engine.start(session=s)` persists it, as resume does.
2. Holds the store's write lock across the sequence "re-read the session, check `status == "dormant"`, replace the plan, `engine.start`". `engine.start` is synchronous and refuses "already running" (`engine.py:747-895`), so ResumeArm either sees the session active or finds the engine busy.
3. Re-reads the session inside the lock, so frames banked by a ResumeArm run that ended between the route's first read and the lock are kept.

The `patch_session` race itself is a present-day defect and is filed on its own.

**Other cases**

| Case | Behaviour |
|---|---|
| steps that hold frames would be dropped (re-framed, or exposure changed) | 409 unless `accept_dropped`: "212 subs belong to steps this flow no longer has; they stay on disk". A step is **not** counted as dropped when its old target id is in any `skipped_ids` of the new plan's groups: `plan_replace_report` reads them and lists such steps as `skipped`, apart from `dropped`, so CONTINUE does not refuse them (S2 built this server half of S1 item 8; the `skip` param that fills `skipped_ids` lands in S3, and until then no compile skips a panel). Skipping a panel is not a change of identity (2.5), and re-enabling it brings the same step ids back, with its frames, because the ledger counts by step id: `plan_replace_report` reports a step of the new plan that the ledger holds frames on as kept, although the plan the skip was continued with no longer lists it. A skip once continued stays in the refusal's sight: `plan_replace_report` also reads the `skipped_ids` of the session's own plan and counts the steps the session's frames on those panels sit on as its old steps, so re-enabling a panel whose recipe changed while it was skipped, or re-framing its block, still refuses on those frames, and a panel skipped again is exempt through them. A step of such a panel let go of under `accept_dropped` before it was skipped is named again when the panel comes back: asked twice, never lost silently. |
| the dormant session shares **no** step ids with the compile and holds frames, because it was saved before S1 with uuid4 ids (`sequence/models.py:19`, `:80`, `:226`) | 409 `{"adopt": {...}}` rather than a silent fresh start, which would disarm it (`engine.py:795-803`). The UI offers ADOPT or START OVER. ADOPT rewrites the session's step ids to the deterministic ones, under the write lock and after a `.bak` copy. The match is **unique** on (target name, frame type, filter, exposure, gain, binning). Pre-S1 flows carry no mosaics, so this is a one-target-per-node match. Unmatched or ambiguous steps stay as they are and are listed. A unique match is re-keyed only when the old and the new target are within `ADOPT_MAX_SEPARATION_ARCMIN = 10` arcmin of each other on the sky, great circle, inclusive (S1 hardening, #189 A4): a match exactly 10 arcmin apart is re-keyed, and one a hair further is not. A name is a label, not a place, and #190's wizard filed Andromeda's frames as "M16", which still match "M16" by name once the flow is corrected, 103 deg away. A match further apart stays as it is and is listed with its separation. **A moving body matches on its canonical name, and the bound is measured against the body where it was** (H2, #229; H3, #234, H3 orchestrator ruling 7). A body leaves the bound behind in days: sampled every 10 days for two years from September 2026, the shipped ephemeris gives median daily motions of 39 arcmin for Mars, 7.4 for Jupiter and 1.4 for Neptune (about a week to 10 arcmin, longer near a stationary point), and the Moon moves some 13 deg a day. So measured against tonight's position the bound refused a pre-S1 session of one as soon as the body had moved on. `adopt_matches` asks `tonight.resolve_target`, the resolver 3.3 keys a named TARGET through, what each target name is; a name that resolves to a time-dependent row (`tonight.MOVING_KINDS`: a planet, the Moon, a comet or a satellite, H3 orchestrator ruling 9) is keyed on that row's canonical body name, so "jupiter" typed before S1 matches tonight's "Jupiter", and tonight's position is not compared. The name is not trusted either: the match maps only when the OLD target was within the bound of the body, inclusive, where the catalogue places it at each of the step's capture times (`_pointing`, `_capture_times`): the first and the last frame of each night the step was shot on, a frame with no night recorded on its own, or the session's `created_ts` for a step with no frames. A session frame records no solve position, so the old plan's coordinates are the evidence of where the frames were pointed. A frame's time is the evidence of when, except for a session that came through `migrate_legacy_resume`, whose frames carry the migration's time, so its body steps are measured at the wrong instant (#265). Otherwise the step stays as it is and is listed with the reason: off the body, with the worst separation; no capture time; or a body the catalogue could not place. Before H3 a session filed as "Jupiter" at M31's coordinates, #190's fault, was adopted onto Jupiter. A deep-sky or star name keeps the name as typed in its key, and the bound against tonight's target. **The catalogue is asked off the event loop** (#249). A full catalogue search is 10 to 35 ms, some 700 ms for the first body of the process, and ADOPT makes one per name and per body instant; made inside the locked section, that ran on the event loop holding the store's write lock. So `run_flow` builds `adopt_evidence`, every catalogue answer the match needs, with `asyncio.to_thread` from its first read of the session, before the store's write lock and before the recovering check, and only when that read asks the ADOPT question; inside the lock, `adopt_matches(evidence=)` makes no catalogue call. A step whose frames were banked after that lookup is not matched on an answer the evidence lacks: it is listed first, told to press ADOPT again, and the route answers 409 `adopt` and writes nothing, even with `adopt` set. `to_plan`, `progress` and ADOPT resolve a name's identity at one shared instant, `tonight.IDENTITY_WHEN`, and take its coordinates at their own. A session compiled since S1 that shares no step id was re-framed, not saved before S1 (`saved_before_s1` asks its ids, not whether any is shared), so it goes to the dropped-steps row: ADOPT matches by name, and would credit the old field's frames to the moved one. |
| the compile's `count_mode` differs from the session's | 409 unless `accept_recount`: "this session counted every sub taken (412); counting accepted subs makes it 371". The ledger is counted by the frozen plan's `count_mode` (`session.py:130-134`), so changing `counts` mid-campaign recounts every banked frame retroactively. Ruling 2's switch at save is the common way to reach this row: the first CONTINUE after an old flow is saved answers with both totals before anything is recounted. |
| `body.fresh = true` | START OVER: a fresh session. The UI puts it behind a confirm. |
| the newest session is abandoned, or there is none | fresh, as today (`app.py:5198`); an older dormant session is not continued in its place |
| a **complete** session whose flow now owes more (counts raised) | fresh, plus follow-up issue I-30 ("reopen a complete flow session") |

Every existing guard still applies to a continue: `plan_identity_errors`, `quota_unbounded`, the per-panel horizon check and the sun check (`_start_preflight`, 6.3).

Button copy, from the session (slice S5): `CONTINUE M31 MOSAIC (night 3, 412/1890 subs)`, with START OVER behind a confirm.

One starter per session still holds. `engine.start` refuses "already running" to the second of ResumeArm and Run (`engine.py:747-895`). **Starts are refused while auto-resume re-centres** (S1 hardening, #189 A7, #211). ResumeArm's recovery ladder blind-solves and re-centres the mount for minutes before it starts the session, and the engine is idle all that time, so "already running" cannot catch a start made then. `ResumeArm.recovering` is raised for the whole ladder, in the same synchronous stretch as ResumeArm's own `engine.running` check. The four HTTP start paths (`/api/flows/{id}/run`, including CONTINUE, `/api/sessions/{id}/resume`, `/api/sequence/start` and `/api/sequence/recover`) read it immediately before `engine.start`, with no await between, and answer 409 `resume_recovering` while it is raised, because their run would land on a rig the ladder is still moving. Nothing is written, so the session stays dormant and armed, and the ladder's own start follows. The ladder in turn stops before its next move once a run has started, and its own start re-reads the session under the store's write lock instead of starting the copy it read before the ladder. **Abort and a disarm stop the ladder, and the route says it is running** (H2, #220). `POST /api/sequence/abort` stops it before its next step, cancels the step it is awaiting and disarms the session it was recovering, as Abort disarms a running session (6.15), so neither the ladder nor the next tick starts that session. A PATCH that disarms or abandons that session, or arms another in its place, and a DELETE of it, stop the ladder too (`ResumeArm.stop_recovery`); withdrawing any other session leaves it running. `GET /api/sequence/resume-arm` reports `recovering`, and while the ladder runs `recovery` (`{step, session_id, session_name}`, the step a word from `LADDER_STEPS`); both UIs draw them (#246), so the 409's sentence says the Monitor shows the re-centring and the step it is on, and names the disarm that stops it. A goto the ladder has already commanded may still run to its end on the mount: the Telescope contract has no abort for a slew, though some drivers halt on the cancel. Before H2 nothing an operator could press reached the ladder. **The routes that tear the rig down stop it too** (H3, #238): `/api/disconnect`, and a forced profile apply, profile activate or `/api/connect/rig`, stop the ladder and disarm its session, and wait for it to return before they touch the rig; unforced, those three refuse while it runs (6.15). **A recovery solve that finds no light backs off** (#251, H3 orchestrator ruling 8). A covered optic reads "not enough stars" in the same words a cloudy sky does, and the ladder used to retry it every 10 minutes all night, telling nobody. The classifier in `solve/light.py`, which every solve path that exposes its own frame and raises on a failed solve asks (a path that returns its failure instead, the guide-offset measurement, does not yet: #264), judges a failed solve frame by its median against a reference, the dark library's master for its exposure, gain, offset, binning and temperature, or a bias master plus the smallest dark current the doubling law allows: within `K_SIGMA` sigma of it, the verdict is "no light: the optic is capped, covered or obstructed", raised as `NoLightError`; above the most the no-light level could be, the reference's ceiling (the dark master's own level, or the bias plus the largest dark current the law allows, and no ceiling at all with no dark to measure a rate from), the solver's words stand (cloud); with no reference, a frame darker than its reference, or one between a bias reference's floor and its ceiling, there is no verdict. On a no-light verdict ResumeArm holds in words, sends one alert per session per spell (an error line, which a default alert sink delivers), and retries once after `RETRY_INTERVAL_S` (10 min), for the operator who reads the alert and uncaps, then every `NO_LIGHT_RETRY_S` (60 min) while it stays dark; a solve that works, or a failure judged cloud, ends the spell. **With no master at the frame's readout, the check shoots its own reference** (S2, #262, S2 orchestrator ruling 2). The rig's library holds no bias or dark at the solve's readout, so the check had no reference there and the ladder kept its 10 minute retry. So when the library was read and holds neither a dark master for the frame nor a bias master at its readout, `failed_solve_error` takes one frame itself (`_self_reference`): at the camera's shortest exposure, `SELF_REFERENCE_FALLBACK_S` (0.001 s) while no camera reports its minimum, at the frame's gain, offset and binning, shutter closed, under the hub's exposure guard and within `SELF_REFERENCE_TIMEOUT_S` (60 s). It stands in for the bias master: the floor is its level plus the least dark current, the ceiling its level plus the most, and there is no ceiling without a dark to measure a rate from. At the shortest exposure neither the sky nor the dark current puts a measurable ADU into the frame, so what it reads is the pedestal a bias measures; a camera whose shortest-exposure pedestal reads above its long one would raise the floor by the difference, which is not measured. One is kept for the night per camera, gain, offset, binning and `SELF_REFERENCE_BAND_C` (2 C) band of sensor temperature, unless the failed frame reads bright enough that light may have reached it; a self-shot that fails is no reference and is not kept. A hub with no library loaded, or one that could not be read, takes none. An armed auto-resume replays the dormant session's frozen plan, so the editor says so: "the armed session will replay the version from 2026-09-22; press CONTINUE to apply your edits".

### 5.10 Published state and ETA

- `_set_state` (`engine.py:1250`) gains `group = {id, name, mode, pass, panel: "2-3", visit_elapsed_s, panels_done, panels_total, set_aside: [{panel, reason}], meridian_wait}` (S2, #166; `_group_state`). It is present only while the active target belongs to a group, so existing payloads stay byte-identical. The reasons are words only. `meridian_wait` is true while the group waits on the meridian rule, from the wait's start through the hop that ends it until that hop's first exposure.
- The run publishes no time to set and no meridian time: the engine uses them for its order and its rule, and says only words (6.9). A publish while a group waits on the meridian rule names the mosaic, not the panel (`_publish_group_wait`), and carries no countdown.
- For a principal without `CAP_VIEW_SITE_DERIVED`, the `panel` and `pass` fields are withheld while the group waits on the meridian rule, and the hop that ends that wait is not announced by panel label. The timing of that hop is the crossing (6.9). Built (S2, #166): `_withhold_group_timing` takes both out, absent and not null, while `meridian_wait` is true, on `GET /api/sequence/state`, the monitor snapshot and the WS event alike, and every sequence publish of the wait carries `site_derived`, so the WS lanes drop it whole for such a principal. The GET route answers a poll and drops nothing, so a viewer polling it still sees `target` and `detail` change at the crossing (#166).
- `compute_eta` prices the hops still to make (S1, U-07): `_remaining_hops()` times the `hop` event cost, seeded at `HOP_COST_S` = 150 s until a setup has been measured. `hops_costed` is returned by `compute_eta` but not yet rendered: it is false while hops remain and none has been measured, and `eta_confident` is false with it. Saying "hops not yet costed" on screen is the run readouts' job in S5. S2 made the hops still to make the visits still to make (S2, #189): a rotating group's member owes one hop per visit it still owes tonight (`_visits_owed`: its rounds over `visit_passes`), a sequential member one, and a panel or step set aside tonight none; `visit_min_s` is left out, so the count is an upper bound. The per-frame overhead EMA still takes each hop in on the first frame after it, so the clock counts a hop twice (#297).

---

## 6. Safety and failure handling

| # | Hazard or failure | Behaviour |
|---|---|---|
| 6.1 | A hop moves the mount | Only through `_setup_target`, and so through the safety gate, the sun cone (checked again inside `goto_and_center`), `check_slew_limits` and a bounded goto. Nothing new moves the mount on any other path. |
| 6.2 | A panel behind the horizon or obstruction mask, the floor, a wedge or the pier limit | Waiting at selection (5.1). The existing wait path sleeps; there is no busy loop. While nothing is shootable, the mount is park-held on the idle clock, and the tracked target's floor and flip point are checked every tick (5.1). A verdict waiting cannot change is a refusal. If the gate still raises at the slew (a race of seconds), `SafetyAbort` ends the run as today. |
| 6.3 | Start guard | Sun: any panel in the cone refuses the start, always, force or not. Horizon: refuse (unless force) only when **every** panel is blocked now, and list them. Otherwise start, and name the blocked panels in the response and the log. Built (S2, #132): `_start_preflight`, one helper for `/api/sequence/start` and `/api/flows/{id}/run`, in place of the all-or-nothing loop that refused the start for the first panel behind the horizon. A group refuses with 409 `below_horizon`, naming the mosaic and listing every panel (`panels: [{panel, target}]`); a target outside a group refuses alone with the 409 it always had, which `force` still waives without a look; a start that goes ahead answers `below_horizon: [{group, mosaic, target, panel}]` when a panel is down, and `_name_panels_below` logs one line per group, in words. What the run does with a panel that is not up is the selection's (5.1). `/api/sessions/{id}/resume` and `/api/sequence/recover` run neither check (#291). |
| 6.4 | Termination | Six independent bounds: the zero-exposure pass (5.1); `max_failed_visits` per panel with a 300 s `_wait_until` between all-deferred passes; the per-panel reject rule (3 visits that accept nothing while others accept, 5.1); the per-step reject guard, now spanning visits (5.3); the frozen stop boundary; `quota_unbounded` on every start path. A follower never holds the cursor past `group_ready_ts` (1.6). |
| 6.5 | Per-frame gates | No second frame loop. Visits call `_run_step` with its whole gate stack (`engine.py:2870-2954`). |
| 6.6 | Watchdog | `_progress_expected` is False from the start of the hop to the first exposure (`engine.py:2317`, `:2508`). |
| 6.7 | Set aside is not done | Window closed, floor advance, a guider that would not start on three consecutive passes, repeated centring or rotation failure, an angle failure, a panel that rejects alone, a step reject guard, a pier change with flips off: all set aside for tonight. The ledger decides completion. The session stays dormant and armed. Set-aside records are kept in `Session.set_aside` for the night, so a crash-resume does not retry them. The report names every set-aside panel and its reason. |
| 6.8 | Star-poor panel | Deferred for at most `max_failed_visits` consecutive passes, then set aside with a warning alert that names it. It never becomes a silent coverage hole. A panel that starves every night gets a Campaign row naming the starvation (follow-up I-31). |
| 6.9 | Privacy | The setting-first order, the meridian rule and per-panel altitudes use the site inside the engine only. The log ring and the night log speak in panel labels and words: "2-1 first: sets soonest", "1-3 waits for the meridian". Minutes to set, meridian times and altitudes go only to a `CAP_VIEW_SITE_DERIVED` topic (the issue #19 class: a computed value can reveal the site). The progress route carries no site data (`CAP_VIEW_STATUS`). The modal's altitude column, `MosaicNightCard` and Tonight stay gated and hidden for a viewer. **Timing is a channel too, and words alone do not close it.** `/api/logs` is `CAP_VIEW_STATUS` (`app.py:8046`), so a viewer can read it. A line logged when a panel is acquired at its computed meridian crossing timestamps the transit of a known RA. That gives the LST, and the LST gives the longitude, which is why `redact.py:64-88` strips `hours_to_flip`. Rule: a log line or state change whose **time** is set by a site computation (a meridian wait ending, a group flip, a flip at the crossing) carries `site_derived=True`. **The flag is built** (S2, #166). `bus.log(..., site_derived=True)` sets it, and `events.is_site_derived` is the one test every seam asks: `/api/logs` (`_redact_log_rows_for`, before its level and limit), `/api/logs/export` and the night reader (`include_site_derived`) drop a flagged line for a principal without `CAP_VIEW_SITE_DERIVED`, both WS lanes drop any flagged event whole for one, and `state.group` withholds `panel` and `pass` across the wait (5.10). The engine flags, at info level, a meridian wait's start and end, the target line of the hop that ends it, the group's pier change, a visit the flip point ended before its first frame, and a follower's lines. The night log file keeps everything a line says. Not flagged yet: a follower's hop at a wait's start (#302), today's flip lines (#127) and the idle park-hold's flip-point line; and `alerting.py` forwards warning and error lines to the external sinks without asking the flag, harmless while every flagged line is info (#166). Residual: the first frame after a meridian wait still lands at crossing plus hop plus exposure, which is coarse because the hop varies. The privacy issue records that residual, and today's flip lines (#127) leak the same way now; the "holding for the meridian flip point" state detail no longer carries its minutes (H3, #233), but its timing still does. **A hold's lines and the resume-arm route are words** (H3, #233, H3 orchestrator ruling 1). ResumeArm's start-floor and slew-limit refusals, and every hold, idle-watch and flip-watch line and published detail the engine gives, say what happened in words: no altitude, azimuth or site-derived clock time stands beside a target. The numbers behind a resume-arm hold (the target's altitude, its floor and the wait until it rises) ride `hold.site_detail` on `GET /api/sequence/resume-arm`, which `_redact_resume_arm_for` removes, absent, not null, for a principal without `CAP_VIEW_SITE_DERIVED`; an admin and an operator read it, as the owner's role-visibility ruling of 2026-09-22 allows. The slew gate's own numbers ride `SlewRefused.site_detail`, and ResumeArm files them as a slew-limit hold's `hold.site_detail`, so they reach the same holders and no one else; a refusal without them files its words. No log line carries the numbers #233 took out of the lines, the night log file included: a line can be flagged `site_derived` since S2, and none carries them yet; nor does any screen show an operator `hold.site_detail` yet; #258 records both. `test_resume_arm_hold_is_site_free.py` and `test_engine_logs_carry_no_site_numbers.py` hold both paths the way the #19 scanner does: a number a viewer is shown must not move when the site does. |
| 6.10 | Guider churn | Every hop stops guiding and then starts it again. A failed start defers that panel to the next pass whatever `guiding_action` says, bounded at three consecutive passes, and a pass in which every attempted panel failed hands the decision to `guiding_action` (5.6 step 7, Revision 2, ruling 5). Hops multiply guider starts on the #72/#134/#135 path, so S2 includes simulator tests that force a guide-start failure on one panel and on every panel. |
| 6.11 | Meridian | At most one pier change per group per night (5.7). The flip-owed invariant is kept per target. There is never a 180-degree rotator turn. |
| 6.12 | Angle | A mosaic cannot run at "any angle" (M2). The measured angle is checked on every hop, whatever the rotator state (5.6). The rotate loop keeps abandoning a non-improving attempt (`hub.py:6317-6334`). |
| 6.13 | Identity | `plan_identity_errors` on every start path. Duplicate step ids would let one panel's frames count for every panel, and in accepted mode panels 2 to N would read complete at once. |
| 6.14 | Optics drift | The compile never re-tiles. A warning above 2%; a loss (blocks `/run` until accepted) when the live field would leave gaps (M5). |
| 6.15 | Operator STOP | `POST /api/sequence/abort` disarms auto-resume for the session a run was writing (`_finalize_report`), and it stops ResumeArm's recovery ladder as well (H2, #220): before the ladder's next step, cancelling the step it is awaiting, and disarming the session the ladder was recovering, so neither the ladder nor the next tick starts it. A disarm, abandon or delete of that session stops the ladder too, and `GET /api/sequence/resume-arm` reports `recovering` while it runs (5.9). **So do the routes that tear the rig down** (H3, #238). After a restart the ladder solves and re-centres with the engine idle, so `engine.abort` had nothing to stop and these routes pulled the camera and the mount out from under it. `/api/disconnect` stops the ladder before its first await and disarms the session it was recovering; it has no force and no refusal, since a disconnect has always ended whatever it found. Profile apply, profile activate and `/api/connect/rig` refuse unforced with 409 `running` while the ladder runs, as they refuse a live run, with a detail that names the re-centring; forced, they stop it and disarm its session before they go on. Each then waits for the ladder to return (`ResumeArm.wait_stopped`, bounded by `LADDER_STOP_WAIT_S` = 35 s: one Alpaca request running to its 30 s client timeout, plus margin) before it aborts or tears anything down, and answers 409 `running`, touching nothing, when it has not. The disarm is deliberate, and the same as Abort's: tearing the rig down under the ladder is the operator taking the night over, and a session left armed would be started again by the next tick once a rig is connected. Both UIs still draw every `running` 409 with their fixed sentence and not the route's detail, so neither the re-centring nor the disarm that force costs reaches the operator (#256). A forced rig connect used to abort and disarm before it validated its body, so a 422 left the night ended (#257); it now validates the whole body first, ahead of the unforced 409 as well, so a 422 means nothing was touched (S2, #257). The backlog report of an abort followed by an automatic restart 50 s later (`docs/superpowers/backlog/2026-08-22-flow-editor-offers-what-the-engine-refuses.md:74-79`) must be re-checked against the fix for #93 (closed) before anyone claims it. |
| 6.16 | Downgrade | See 3.6. |
| 6.17 | Idle mount | A mount left tracking with no frame loop watching it is park-held on the idle clock (`WAIT_TEARDOWN_S` since the last exposure or hop), or at once if the tracked target reaches its floor or its flip point first (5.1). This closes a present-day hole for eta-0 and constraint waits as well as the mosaic's reach waits. The stop is asked on its own task and its own clock, the first attempt as well as every retry (H2, #216), so the tick that decides the stop only decides it, and a dead mount link cannot stretch the safety gate's cadence (#189 A3); a stop the mount does not confirm is asked again at most once per `IDLE_STOP_RETRY_S` (60 s), never from the wait loop's tick. The run-start cooling wait watches the same way (H2, #202): a target that `start(tracking=)` hands the run, which auto-resume re-centred moments before, is looked at on every cooling probe, so a sensor walking down from ambient after a restart no longer leaves it tracking unwatched. **The two waits that did not watch now do** (H3, #236): the cooler gate's wait when a cloud hold releases (`_cooler_gate(..., watch=True)`), which can hold the held target tracked for up to `cool_timeout_s` with no hold loop and no frame loop looking, and the camera-lane wait at run start (`_await_camera_lane`), up to 150 s with the target a caller handed the run tracked. Both take the same idle look on every poll. The safety pause's cooler gate does not watch: the pause stopped tracking itself, and a look would call the mount still tracking. **A run's end completes a stop the idle watch decided** (H3, #247, H3 orchestrator ruling 5). The end of a run used to cancel the idle-stop task, as a setup does before it turns tracking back on; but nothing turns tracking on after a run, so a first attempt still stopping the guider never reached the mount, and without a park the mount tracked on unwatched. Now `_finish_idle_stop`, from `_run`'s wind-down and from `abort`, lets a first attempt in flight finish for up to `IDLE_STOP_FINISH_S` (180 s), the sum of the bounds it awaits, polling it rather than awaiting it so an Abort's cancel lands on the poll and not on the stop, then ends what is left, asks the mount once more and reads tracking back; a stop still unconfirmed, or a first attempt cut at the bound, is said once, in words. H3 did that whether or not the wind-down parked. **An ending that parks hands the stop to its park** (S2, #270, S2 orchestrator ruling 1). On an unsafe ending that wait put up to `IDLE_STOP_FINISH_S` of a hung guider, and a mount call past it, ahead of the park and the roof close in the rain, with the state still "running". A park stops the mount more surely than the idle stop does, so an ending that parks (`_ending_parks`: every `SafetyAbort`, `SlewRefused` included, and a natural end, a cooling skip or a quality stop whose plan parks when done) cancels the stop's task without awaiting it and moves its fence (`_hand_idle_stop_to_the_park`, `_idle_stop_epoch`), so a guider stop that eats the cancel sends no `set_tracking(False)` once the park has begun. The wind-down then asks the guider to stop on a task of its own, parks and closes the roof at once, and reaps both tasks afterwards, bounded by `GUIDE_OP_TIMEOUT_S`; a park that fails or times out is followed by one bounded stop of tracking, read back and said in words (`_stop_after_a_failed_park`). An operator's Abort and a failure do not park, and complete the stop as above. Two windows remain: an Abort that lands in the park of a natural parking end cancels that park, and with it the stop handed to it, so the mount can be left tracking (#305); and the auto-reopen roof close parks without moving the fence (#306). A cloud hold, which keeps tracking on by design, runs its floor, flip and tracking checks on the hold's own clock (5.8, #203, #205). |
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
2. Guider stand-down before every slew, after the issue's simulator test has demonstrated the defect. By ruling (5.6 step 2), only when `plan.guide` is set: a guider the operator started under a guiding-off plan is theirs.
3. `_pre_flip_side` keyed per target (with #136).
4. Dither counter reset on acquisition.
5. `_hop_focus_is_owed` (5.6 step 6): no age rule for hops; owed only on no good sweep tonight, a failed last sweep, `_refocus_due()`, or the group's first acquisition. `_post_flip_focus_is_owed` is unchanged.
6. Hop event cost and the ETA term.
7. `to_sequence_plan(flow_id=)`: deterministic ids for targets, steps and pool members.
8. `run_flow` CONTINUE, dormant sessions only (5.9): the factored plan replace plus `engine.start(session=)` inside one write-locked section that re-reads the session and never saves it separately; `accept_dropped`, `accept_recount`, `fresh`, and the ADOPT path for pre-S1 sessions. The skipped-panel exemption from `accept_dropped` was not built in S1, which has no groups. S2 built its server half: `TargetGroup.skipped_ids` (3.4), which `plan_replace_report` reads, so a step whose old target id is listed there is reported as skipped and never as dropped (5.9). The `skip` param that fills it (3.1) lands in S3.
9. `GET /api/flows/{id}/progress` (`CAP_VIEW_STATUS`): per block, per panel, per step banked/owed from the flow's session as Run reads it (`current_for_flow`, 5.9), plus orphaned counts. The card chip for single targets.
10. The hub's `rotation_unavailable` flag.
11. An angle-check helper fed by `sky_angle`, not yet called by groups.

Tests:

- Two compiles of one flow give identical ids. An exposure change changes only that step id; a count change changes none. A different `flow_id` gives different ids. Mutant "uuid4" fails the continuity test.
- A second `/run` on the simulator banks on the same ledger entries. Dropping steps that hold frames gets 409 unless accepted. `fresh` starts a new session.
- Skipping a panel with banked frames and pressing CONTINUE does not 409, and re-enabling it restores its counts. Mutant "skipped panels count as dropped" 409s. S1 had no panels to skip; S2 built the case against a compile patched to skip one (S2, #189; `test_continue_skipped_panels.py`), and the end-to-end case with a real `skip` param is S3's, with the param.
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
8. ~~**S2b**: a classic Plan "cycle panels each pass" toggle.~~ Dropped by Revision 2, ruling 4: the Atlas and Sky door to the Plan is retired (#196), so no new Plan mosaics are made, and a `mosaic_group` with no `groups` entry keeps today's panel-first behaviour. Until S6 lands, the Sky copy is corrected to describe panel-first behaviour, as #154 suggests.

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
- Guide-start failures (Revision 2, ruling 5), each under `guiding_action` warn, skip and abort in turn:
  - one panel fails its start: it is deferred to the next pass, the others shoot, it is retried after the others, and it is set aside after 3 consecutive failed passes with the alert naming it. No frame is shot on it unguided. Mutant "honour `guiding_action` per panel" shoots it unguided under warn, sets it aside at once under skip, and ends the run under abort.
  - a panel that fails twice and then starts guiding banks frames and resets its counter. Mutant "count lifetime failures" sets it aside later.
  - every attempted panel fails in one pass (at least two): the counters do not move and `guiding_action` decides (abort ends the run, skip sets the group aside, warn shoots unguided). Mutant "defer even then" spends three passes hopping a dead guider.
- Accepted mode with a rejecting grader terminates.
- A resume from a mid-group `done_map` picks the least complete panel, and ResumeArm re-centres on it with rotation.
- `on_target_complete` fires once per panel, at completion. Mutant "fire every visit" fails.
- `groups = []` is byte-identical against the golden plans. A `mosaic_group` with no group stays panel-first.

**As built** (S2, #189). Items 1 to 7 are built: the pure rules in `sequence/group_rules.py` and `sequence/panel_order.py`, the driver in the engine, and the hub, naming and model changes of item 7. The tests are split by rule rather than kept in one file, every engine case on the clocked simulator through `tests/_group_harness.py`: `test_group_rotation.py` (rotation, passes, order, set-aside, deferrals, the reject rule, guide starts, completion, published state, the ETA), `test_group_reach.py` (the reach verdict), `test_group_meridian.py` (the one pier change, the side check, the margin, the flip-point deadline, the pre-flip idle, a viewer across the wait), `test_group_followers.py` (followers and `after_group`), `test_group_angle_check.py` (the angle check), `test_group_set_aside_persisted.py` (set-aside across a restart) and `test_locked_angle.py` (Revision 2, ruling 9), with the pure rules in `test_group_rules.py` and `test_panel_order.py`. Not built: the guide-lost deferral of 5.6 step 7 (#303) and a follower filling a deferral wait (1.6, #304). The simulator mount reports the far pier side as soon as a tracked target crosses the meridian, with no slew (#298), so the straddle test counts the side each hop lands on, read at the moment it lands.

### S3: Flows vocabulary, compile, doctor, Tonight

1. TARGET params and ports, `create_params`, the `pass` outputs, loop-wire consumption, and the `_trigger_for` pass branch.
2. The compile entry (3.2), the `to_plan` expansion (3.3), the panel lane and `owner_of` (1.5), rig-fact injection, FLOW_SCHEMA 4 stamping.
3. Doctor R4, M1-M15 and L1 in the server. Correct the stale docstring at `doctor.py:13-17`; there is no UI copy. `LEGACY_TYPES` hides SLEW.
4. The wizard's mosaic kind, with a lane without SLEW and the loop wire. It is the generator behind "Send to Flow Wizard" (#196), whose stepped sheet lands in S4 with the modal and whose doors move in S6. The angle comes from the operator or USE MEASURED, never a default; the camera field comes from injected optics, and with none the wizard answers with a single target (1.8). The Examples drop SLEW, and an **eighth Example**, "M31 3x2, rotating", joins the acceptance corpus. It must load, validate and run on the simulator (`examples.py:3-7`).
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
- A v3 flow with no `counts` key compiles to attempts when loaded. Mutant "missing-key default Accepted" fails.
- Saving that flow writes `counts = Accepted subs` on every TARGET and POOL, and the save's answer says so (ruling 2). Mutant "switch on load" changes the compile of a flow nobody saved.
- A palette-created or wizard-created block gets Accepted subs and Any angle. An example keeps the angle it was written with (ruling 9): M31 stays at Rotate to PA 23.4.
- The anchor rule (ruling 3): a move of 9.9' on the 3x2 of 2.0 x 1.33 deg keeps the ids and 10.1' changes them; a 3.3 deg turn keeps them and 3.5 deg changes them; three saves of 6' each in the same direction re-anchor on the second, because each is measured against the anchor. Mutant "compare with the previous save" never re-anchors. Mutant "centres, not corners" keeps the ids of a 1x1 block turned 90 degrees.
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

- The Atlas "Send N panels to Plan" / "Add target to Plan" button (`views/AtlasView.tsx:1569-1570`) and the Sky FRAME's forward action become **SEND TO FLOW WIZARD** (Revision 2, ruling 4, #196). The wizard opens pre-filled with the framing, asks what is missing, and writes a TARGET block with the loop wire. The side channel of Plan targets goes (`next/hubs/sky/sheets/quick.tsx:438-466`), and so does the first-run guide's "press Add target to Plan" (`lib/firstRunWizard.ts:77`).
- Delete the synthetic MOSAIC lane card and the false copy: `FramingCard.tsx:37-43`, `quickCopy.ts:225`, `:237-243`, `flowLane.ts:98-117`.
- One overlap constant.

Tests: the Sky and Atlas doors open the wizard pre-filled and produce exactly one TARGET block and no Plan targets, and a grep test asserts the deleted strings ("panels to Plan", "Add target to Plan") are gone.

### S7: validation before any sky, then one supervised night

1. On the simulator, end to end: a 2x2 rotating, a forced solve failure on one panel, a CONTINUE on a second simulated night, and a meridian straddle.
2. Then one supervised rig night on a bright, low-risk 2x1 or 2x2 near the meridian. Measure the hop cost and set the `passes`/`minVisit` defaults from it.
3. Review the durable night log (`captures/logs/<night>.jsonl`), classifying events by their neighbours.
4. File an issue for every human intervention.

A mosaic has never run on the rig (`docs/reviews/2026-07-30-overnight-systems-test.md:102`).

**The supervised night is approved** (owner ruling 8, 2026-09-24). It will be scheduled once S0 to S6 have landed and item 1 is green on the simulator. The night also measures the two first guesses Revision 2 introduced: the re-frame carry threshold (`REFRAME_CARRY_FRACTION`, against the measured centring residual) and the guide-start retry bound.

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
| I-11 | FILED #157 | `on_target_complete` rules run after every `_run_steps` return, including a target that stopped short (anti-spin, reject guard) (`engine.py:1966-1993`). `target_complete=True` is asserted when it is not true. S2 fixes this for panels; the issue covers single targets. S2 built the single target's half as well (S2, #157): `_schedule_loop` fires a single target's rules only when `_target_complete` says it is complete, as `_visit_panel` does for a panel, so an accepted-mode target whose every frame was rejected no longer announces itself complete (`test_a_single_target_whose_every_frame_is_rejected_is_not_complete`). |
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
| - | EXTENDED #142 | Every panel hop restarts guiding, and a failed start shoots unguided frames that pass the RMS gate. The owner decided the panel policy on 2026-09-24 (ruling 5): defer the panel to the next pass, never shoot it unguided (5.6 step 7). |

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
| U-12 | DROPPED (Revision 2, ruling 4) | A classic Plan "cycle panels each pass" toggle. The Plan door is retired in favour of "Send to Flow Wizard" (#196), so every new mosaic arrives as a TARGET block. |
| U-13 | FILED #196 (S3, S4, S6) | Send to Flow Wizard: one stepped wizard for both UIs, pre-filled from the Atlas or Sky framing, asking filters and counts, exposures, guiding, the stop condition and auto-resume, ending on a review of the server compile with OPEN IN EDITOR and RUN. |

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
| I-45 | FILED #187 | Fixed camera, unknown angle: lay the grid out at the angle the first centring solve measures. It changes the geometry, so it waited on the re-frame identity decision. Ruling 3 decides it: a measured angle within the carry threshold of the laid-out one keeps the counts (a 3.4 deg turn on the 3x2 of 3.3), and a larger one restarts them. |
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
12. **Scope.** S2 is the long pole, and the first visible result (S4) arrives late. The circle can be shown through `/api/sequence/start` before any Flows UI exists. (The classic Plan toggle, S2b, was the other early demonstration; Revision 2 dropped it.)
13. **The idle-clock park-hold changes today's behaviour.** A mount that used to keep tracking through an eta-0 or constraint wait now stops tracking after `WAIT_TEARDOWN_S`. That is the point of the fix, and the next `_setup_target` re-slews and restores tracking as it does after any long wait. It is a named behaviour change with its own issue (#165).
14. **Bounded follower visits cost hops.** A follower that fills a mosaic's wait pays a slew and a centring each time it comes back. The owner chose to fill the gap by default (ruling 1); "Wait for the mosaic" stays one tap away in the flow's settings.
15. **Hop focus has no age rule.** It relies on the frame loop's refocus triggers (`autofocus_every`, the temperature delta). On a rig with neither, a mosaic focuses once per night, which is what one long target does there today. The RUN section says so, so the operator can arm the temperature delta.
16. **The carry threshold is a guess.** `REFRAME_CARRY_FRACTION = 0.5` is the owner's hunch. At the limit a carried move spends the overlap budget A.2 left for pointing error (3.3). S7 measures the centring residual, and the constant is revisited then.
17. **Every save switches the count mode.** Ruling 2's switch makes nearly every flow saved on an S3 build a v4 file, which an S0 to S2 build refuses to open (3.6). That is loud, not silent, but it makes a downgrade after S3 costlier.
18. **The auto-resume tooltip.** The owner's text promises three things the code does not do today (#191, #192, #193). The option ships with an interim text that is true (Revision 2, ruling 7), and a claims-table test keeps the two in step.

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

### A.5 The re-frame carry threshold (Revision 2)

`threshold = REFRAME_CARRY_FRACTION x w`, with `REFRAME_CARRY_FRACTION = 0.5` and `w` the overlap width of the anchor's grid (3.3). The move is the largest angular distance any panel corner travels between the anchor layout and the new one. Each corner is placed at its panel's angle in that panel's own tangent plane with `deproject`, and the panels come from `compute_mosaic`. The last three columns are the smallest pure move of each kind that reaches the threshold, found by bisection. The layouts are at angle 30 deg; none of these numbers is site data.

| Grid (cols x rows) | Panel | Overlap | Dec | w | Threshold | North shift | Turn | Field change |
|---|---|---|---|---|---|---|---|---|
| 3x2 | 2.0 x 1.33 deg | 25% | 41 | 19.95' | 9.98' | 9.98' | 3.43 deg | 6.0% |
| 3x2 | 2.0 x 1.33 deg | 25% | 75 | 19.95' | 9.98' | 9.90' | 3.33 deg | 6.0% |
| 3x1 (one row) | 2.0 x 1.33 deg | 25% | 41 | 30.00' | 15.00' | 15.00' | 5.50 deg | 9.6% |
| 3x3 | 2.0 x 1.33 deg | 15% | 41 | 11.97' | 5.99' | 5.99' | 1.75 deg | 3.1% |
| 2x2 | 0.9 x 0.6 deg | 25% | 41 | 9.00' | 4.50' | 4.50' | 4.53 deg | 7.9% |
| 1x1 | 2.0 x 1.33 deg | 25% | 41 | 19.95' | 9.98' | 9.98' | 7.94 deg | 13.9% |

A turn moves the outer corners of a wide grid most, so the 3x3 at 15% tolerates the least turn. A single panel tolerates the most, because only its own half-diagonal turns.

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

## Revision 2 (owner rulings, 2026-09-24)

2026-09-24. The owner ruled on the eight decisions #189 was waiting on. Ruling 2 was first held for a clarification and then decided the same day. Each ruling is recorded below in the owner's words where they were quoted, with what it changes and where. The body of the spec now carries every ruling, so this section is the record, not a patch list to apply. Applying the rulings produced seven new issues (#190 to #196) and comments on #141, #142, #154, #169, #187 and #189.

| # | Ruling | What changes | Where in the body |
|---|---|---|---|
| 1 | While a mosaic waits, "shoot later targets and come back", and make it an option: "No sense in wasting time due to an obstruction." | D15's default is decided. It is a per-flow option, `FlowGraph.settings.whenWaiting`; "Wait for the mosaic" is the other value. Revision 1's rule stays: a later target's visit ends when a panel is due. | D15, 1.6, 2.4, 3.1, 3.2, 3.6, risk 14 |
| 2 | "Only count accepted frames." | Every new TARGET block, single target or mosaic, counts accepted subs, and the choice is withdrawn. Existing flows keep their stored mode on load, show a one-line notice, and switch when next saved. | D8, 2.4, 3.1, 3.3, 3.6, 5.9, S3 tests, risk 17 |
| 3 | "If the re-frame is less than half the width of the overlap, carry over" the panel counts; otherwise restart them. The number is a hunch. | Identity keys to an anchor geometry. Counts carry while every panel corner moves less than `REFRAME_CARRY_FRACTION` (0.5) of the overlap width: a named constant, derived per grid, to revisit with data. | D5, 2.5, 3.1, 3.3, A.5, S3 tests, I-45, risk 16 |
| 4 | Retire the Atlas/Sky "Send panels to Plan" door and replace it with "Send to Flow Wizard": "Flow wizard should walk you through prompts to set up a flow on your behalf, which the user can then run." | A stepped wizard, pre-filled from the framing (#196). S2b is dropped. S6 moves the doors. | section 0 item 4, 1.4, S2 item 8, S3 item 4, S6, U-12, U-13 |
| 5 | Guiding fails to start on a panel: "try again after the next go around through the other panels. No point in leaving a hole in the mosaic if we don't have to." | The panel defers to the back of the rotation whatever `guiding_action` says, bounded at 3 consecutive passes. A pass in which every attempted panel failed goes to `guiding_action`. | section 0, 5.1, 5.6 step 7, 6.7, 6.10, section 9 (#142), S2 tests |
| 6 | Write the Flows handoff README amendment as a draft now. | Drafted as uncommitted edits labelled "Amendment 2026-09-24 (owner approval pending)". | section 0 item 3; the drafts below |
| 7 | Replace DUSK "Single night" with an explicit option, default ON, with the exact label and hover text. | A new option (#195). Its tooltip was checked against the code: three claims are not true today (#191, #192, #193), so an interim text is proposed. | 2.4, 3.1, 3.6, risk 18 |
| 8 | S7 is approved. | Scheduled once S0 to S6 land. | S7 |

### Ruling 1: while a mosaic waits

The owner chose the default the spec proposed and asked for it to be an option. The option is per flow, not per block, so a flow with two mosaics has one answer to "what do we do while a mosaic waits". It lives in `FlowGraph.settings` (1.6), which is new and additive. `whenWaiting` has no older meaning to preserve, because no graph had a multi-panel block before S3.

Revision 1's follower rule is what makes the default safe, and it is unchanged. A later target picked while every live panel waits runs with `VisitBound(deadline_ts = group_ready_ts)`, so it hands the cursor back at the first frame boundary that cannot fit its next frame before a panel is due (1.6, 5.3). Once the group is set aside tonight, or complete, a later target runs its normal course.

### Ruling 2: accepted frames only

- **New blocks.** Every TARGET block created by the palette, the wizard, the quick flow or an Example counts accepted subs. POOL gets the same treatment, so a new pool-only flow is not the one new flow that counts rejects. The read-only Examples cannot be saved, so their fixtures are converted in S3 and their golden plans are re-pinned deliberately.
- **No choice in the editor.** The "Counts" row goes from the modal's RUN section, and `counts` is not an inspector field.
- **Existing flows.** Loading never changes a flow's meaning: a flow with no `counts` key, or with "Every sub taken", compiles to attempts as before. Both editors, and the phone stage list, show one line while any TARGET or POOL resolves to attempts: "This flow counts every sub taken, rejected ones included. New flows count accepted subs only, and saving this flow switches it." When the flow has a dormant session, the line adds: "Its armed session keeps its count until you CONTINUE."
- **The switch.** It is made by the server in `_persist_flow`, so every writer converges: both UIs, the API, the wizard and the quick flow. The save's answer carries `migrated: ["counts"]`, and the UI says "now counts accepted subs only".
- **Consequences.** The first CONTINUE after the switch meets 5.9's `accept_recount` answer with both totals. `quota_unbounded` now applies to every new flow, and the default reject guards (10 per step, 20 per night) satisfy it; M9 previews it where they are off. The v4 stamp (3.6) means a downgrade after S3 refuses flows saved on S3, loudly. The comment on #141 records that flows now count accepted frames, which narrows #141's gap for flows.

### Ruling 3: re-framing carries counts under half the overlap

The owner's words were "if the re-frame is less than half the width of the overlap, carry over". The spec turns that into one pure function, `framing.reframe_carry(anchor, new)`, and one named constant, `REFRAME_CARRY_FRACTION = 0.5` (3.3).

- **What moves.** A re-frame is measured at the panel corners, as the largest angular distance any corner travels. One number then covers a shift, a turn and a change of camera field. Centres alone would let a 1x1 block turn 90 degrees and keep its counts.
- **The overlap width** is the anchor grid's: `overlap x fov` across the neighbours that exist, the smaller of the two axes. Worked thresholds are in A.5: 10.0' for the 3x2 of 2.0 x 1.33 deg at 25% (a 3.4 deg turn reaches it), 15.0' for a single row of three, 6.0' for a 3x3 at 15%, and 4.5' for a 2x2 of 0.9 x 0.6 deg.
- **The identity key tolerates the move.** `geometry_key` hashes the block's **anchor**, `frameAnchor`. The server writes it at save and replaces it only when a move reaches the threshold, when the grid's rows or cols change, or when the angle switches between "any" and a set value. The anchor is compared with the new geometry, never with the last save, so nudges cannot add up.
- **The constant is a first guess.** At the limit, a carried move spends the half of the overlap that A.2 left for pointing error. S7 measures the centring residual it competes with, and the constant is revisited with that data.
- This decides #187 as well (section 9, I-45), and the comment there says so.

### Ruling 4: Send to Flow Wizard replaces Send panels to Plan

What exists today (HEAD 5c8530c1):

- **The doors.** Classic Atlas "Send N panels to Plan" / "Add target to Plan" (`ui/src/views/AtlasView.tsx:1569-1570`, handler `:830-848`). The #/next Sky quick sheet's Plan side channel (`ui/src/next/hubs/sky/sheets/quick.tsx:438-466`).
- **The guided wizard.** Three answers: kind, automation chips, and a target name (`server/astrodeck/flows/wizard.py:198-393`, `POST /api/flows/wizard` at `app.py:4833-4856`). It has two presentation files, `ui/src/components/flows/FlowWizard.tsx` and `ui/src/next/hubs/session/flows/create/wizard.tsx`. It writes no coordinates, so a typed name lands on M31's (#190).
- **The quick flow.** Four answers, through the same `generate()`: the target with coordinates, subs per filter, the wheel's filters and exposures, and guiding (`wizard.quick`, `wizard.py:574-649`).

The feature (#196):

- **Doors.** Classic Atlas and the #/next Sky FRAME show SEND TO FLOW WIZARD for a mosaic and for a single target. The wizard opens pre-filled with name, coordinates, angle mode and PA, rows, cols, overlap, the camera field and the skipped panels.
- **One stepped sheet, shared by both UIs** (D13's rule). Its steps: target; framing (read-only, with EDIT FRAMING opening the modal of section 2); filters and counts with exposures (the wheel's rows); guiding; the stop condition (DUSK start, stop and minimum altitude, which need #191); auto-resume (#195); and a review of the server's compile with OPEN IN EDITOR and RUN.
- **One generator.** `POST /api/flows/wizard` gains optional fields, and a body with only the three original answers generates exactly today's graph. The doctor bar is unchanged: every generated graph passes at note level or better across the option matrix.
- **What retires.** The Plan side channel, the S2b toggle (U-12) and the "send panels to Plan" strings. The classic Plan page keeps its own target entry for hand-built plans. #154's copy is corrected to panel-first until S6 deletes it, and the comment on #154 points at #196.

### Ruling 5: guiding fails to start on a panel

Specified in 5.6 step 7, with the outcome rows in 5.1 and the tests in S2. In short:

- A group member whose guide start fails, or that has no connected guider when the plan asks for guiding, raises `PanelDeferred`, whatever `guiding_action` says. It moves to the back of the rotation and is retried on the next pass. Single targets keep today's escalations.
- **N = 3 consecutive deferred passes** (`max_failed_visits`, shared by every deferral) sets the panel aside for tonight with a warning alert. Why three: the retries are a full rotation apart, about 83 min on a 3x2 at the default cycle, so three attempts span about 2.8 h of changing sky. Each attempt costs 5.5 to 13.5 min of hop and bounded start, and no exposure.
- **Two points for the owner to confirm.**
  1. A pass in which every attempted panel (at least two) failed to start guiding is treated as the guider's fault. The rig's `guiding_action` then decides. This bounds a dead guider at one wasted pass (about 33 min on a 3x2, against about 109 min without the rule).
  2. A guiding loss mid-visit that the #72 recovery gives up on ends the visit with the same deferral.

The comment on #142 records the ruling.

### Ruling 6: the Flows handoff amendment (draft, not committed)

Two uncommitted edits, each labelled "Amendment 2026-09-24 (owner approval pending)":

- `design_handoff_astrodeck_flows/README.md`, after the loop-back rule (line 122) and after the compile-output paragraph (line 163). They record that a mosaic is a Target detail, not a node, and that the panel loop is a backward event wire (`pass done -> next panel`) which the compile consumes as structure and never emits as an instruction. The first block also records ruling 7's change to the DUSK WINDOW row, because that row names the `repeat` values being retired.
- `design_handoff_astrodeck_flows/MILESTONE2-CONTRACT.md`, a note at its top. It marks the contract superseded for the node count (19 there, 21 shipped in `server/astrodeck/flows/nodes.py`) and the Tonight tab count (3 there, 4 shipped), and points at `nodes.py` as the source of truth.

The parent session commits them only after the owner signs. The comment on #169 points at the drafts.

### Ruling 7: automatic resume on subsequent nights

The owner's label, exactly:

> Automatic resume on subsequent nights until capture quota is fulfilled

The owner's hover text, exactly:

> When true, AstroDeck will attempt to automatically resume the imaging session on subsequent nights until it's fulfilled the number of frames specified in the flow config. It automatically parks at dawn and resumes at sunset. It holds during cloudy weather. Flat panel, dome control, etc all work and respond to the day/night cycle as well as weather events.

**The option** (#195):

- DUSK WINDOW param `autoResume`, a select On / Off, default On. `FieldDef` gains an optional `help` string, drawn as an info icon: hover on a fine pointer, tap on a coarse one (44 px), `aria-describedby` for screen readers. No new control type, so the three-controls pin holds.
- Missing-key default On, which is what every flow does today, because `engine.start` arms auto-resume on every run (`engine.py:795`). A saved "Single night" is not read as Off, which would silently disarm every saved flow. The read maps every `repeat` value to On and shows, on every read until the operator saves: "'Single night' never stopped the next night's automatic resume; this flow now shows that as ON. Turn it off if you meant one night only." (3.6)
- `SequencePlan.resume_across_nights: bool = True`, additive. `to_plan` sets it from `autoResume`, and a flow with no DUSK WINDOW keeps True.
- **Off.** The run still arms at start, so a same-night crash or reboot still resumes. At the stop boundary, `_finalize_report` disarms the session and logs why. ResumeArm refuses and disarms such a session when tonight's observing night differs from the session's first night, which covers a crash before dawn followed by a restart after it. The session stays dormant for CONTINUE by hand.
- The `campaign` compile block (whose `until` was never enforced), the Tonight campaign rows and `doctor._is_campaign` key on the option, and `repeat` retires.
- The wizard's auto-resume step (#196) asks the same question with the same label and icon.

**The tooltip checked against the code** (HEAD 5c8530c1). A tooltip must not promise what the code does not do.

| Claim | True today? | Evidence | Issue |
|---|---|---|---|
| "attempt to automatically resume the imaging session on subsequent nights until it's fulfilled the number of frames specified in the flow config" | Yes | `engine.start` arms `auto_resume` (`engine.py:795`). ResumeArm starts the armed dormant session when its window opens and the sky is dark enough (`resume_arm.py:62-94`). The session is complete only when `owed() == 0` (`engine.py:1759`). | - |
| "It automatically parks at dawn" | Only when the DUSK Stop is Dawn (the default) | Every flow plan carries `park_when_done` and `warm_cooler_when_done` (`to_plan.py:997`), and dawn park is the net under it (`dawn_park.py`). Stop = Clock time or None compiles to no stop (`compile.py:269`), so the run images into daylight and nothing parks it. | #191 |
| "and resumes at sunset" | No, as worded | ResumeArm acts when the flow's window opens, at the rig's `twilight_deg` (default -12) plus the DUSK offset (`schedule.py:384-386`), and never before `dark_enough` (`resume_arm.py:88`). The DUSK Start choice is discarded. Suggested wording: "resumes at dusk". | #191 |
| "It holds during cloudy weather" | Yes on the default configuration; no on one other | The frame-verdict hold runs when no monitor is assigned (`engine.py:3477-3560`, `config.py:167-169`). A cloud-reading monitor pauses. A CLOUD WATCH rule holds (`engine.py:6205-6210`). A monitor that does not read clouds, with no CLOUD WATCH rule, leaves nothing to hold. | #193 |
| "Flat panel, dome control, etc all work and respond to the day/night cycle as well as weather events" | No | Nothing opens the roof or the dust cover for a night; the only calls are the reopen after an unsafe close (opt-in) and a flat step. The roof closes at dawn only with `close_dome_when_done` (default off). The dome is never bound. DUSK FLATS does not run. After a wind-down closes the cover, a resumed night solves through it and holds until dawn (PLAUSIBLE, by reading). | #192, and #194 for the flat step's cover order |

**What ships.** The label is exact. The hover text ships as the owner wrote it only once #191, #192 and #193 have closed. Until then, the option ships with this interim text, for the owner to approve. Each of its sentences is true today:

> When on, AstroDeck resumes this flow automatically on later nights until every frame it asks for is taken. When the flow stops at dawn, it parks the mount and warms the camera, then resumes at dusk when the flow's window opens. During a run it holds for cloud when something reports cloud: the frames themselves when no safety monitor is assigned, a monitor that reads cloud, or a Cloud Watch rule. It does not yet open the dome or the flat panel's cover for the night.

A test holds the shipped tooltip as a table of claims, each mapped to the named test of the behaviour it describes, so a sentence cannot ship without one. The owner's text replaces the interim text clause by clause as the three issues close.

### Ruling 8: S7

Approved. The supervised rig night will be scheduled once S0 to S6 have landed and the simulator run of S7 item 1 is green (S7).

### Ruling 9: the rotator is set explicitly at the start of every run

Owner, 2026-09-24, asked whether the shipped M31 example should keep commanding a connected rotator to PA 23.4: "It's expected that the rotator would need to explicitly set at the start of every run."

- The M31 example (`flows/examples.py:56`) keeps its explicit `rotation: 23.4`. An explicit angle in an example is the intended pattern, not the I-04 defect: I-04 was a palette default that nobody chose. The S3 acceptance line on example blocks is amended to match.
- A run whose target has a set angle commands the rotator to that angle when the target is acquired, every run. It never assumes the rotator is still where an earlier run left it.
- An unframed TARGET locks its angle on the first shot. Asked what a new block with no chosen angle should do, the owner answered: "If the target block has no framing, then whatever the first angle of the first shot is, is locked as the angle. Otherwise any framed shot intrinsically has an angle."
  - A framed TARGET (position, rotation or grid set in the framing modal) carries its angle, and every run commands it.
  - An unframed TARGET (Any angle) takes the position angle measured by its first imaging-camera plate solve in the session, normally the centring solve before its first frame (`sky_angle.note_solved_rotation`, c38156be). That angle is stored on the session as the target's locked angle, with the solve time.
  - From then on, the locked angle behaves exactly like a planned one. Every later acquisition, resume, flip re-centre and night commands the rotator to it, or on a fixed camera checks the measured angle against it (D11, 5.6), so frames from different nights stack.
  - The lock is cleared only by re-framing the block, which re-anchors (ruling 3). The flow editor shows the locked angle and where it came from, so it is visible and not a hidden fact.
  - S2 built it alongside the angle check (S2, U-04, #189). The first fresh solve of an unframed target's acquisition (exposed at or after it began), or failing that the first saved frame's solve, is locked through `Session.lock_angle`, where the first lock wins, keyed by the target's id and saved at once (`_settle_locked_angle`, `_take_pending_lock`). Every later acquisition, restart, flip re-centre and night commands it to a connected rotator (`_commanded_rotation`); with no rotator it is checked instead, and warns beyond `RotatorConfig.tolerance_deg`, never deferring, since a single target leaves no hole beside it. A group member never locks: it has its group's layout angle. The progress route shows the lock as `locked_angle: {pa_deg, source}` (1.2), and no editor draws it yet. ResumeArm's re-centre commands the lock even with no rotator connected (#295). Mutant: "lock re-read on every acquisition" lets the angle drift night to night; mutant "no lock" leaves a resumed night at whatever angle the rotator was left.

### Filed while applying the rulings

| Issue | Kind | What |
|---|---|---|
| #190 | defect, deterministic | The guided wizard writes a typed target name onto M31's coordinates, so NEW FLOW for M16 slews to Andromeda and files the frames as M16. |
| #191 | defect, deterministic | DUSK WINDOW's Start choice never reaches the plan (it always arms at the rig's twilight limit), and a Clock time or None Stop compiles to no stop, so such a run images into daylight and does not park at dawn. |
| #192 | tooltip claim, and a PLAUSIBLE present-day defect | Nothing opens the roof or the dust cover at dusk, the dome is never bound, and dusk flats do not run. A resumed night on a rig with a cover solves through the closed cover and holds until dawn. |
| #193 | tooltip claim | With a monitor that does not read clouds and no CLOUD WATCH rule, nothing holds for cloud. |
| #194 | PLAUSIBLE defect | An automated flat step opens the dust cover after lighting the panel, which on a flip-flat points the panel away from the aperture. |
| #195 | feature | The auto-resume option (ruling 7). |
| #196 | feature | Send to Flow Wizard (ruling 4). |

### Still waiting on the owner

1. Ruling 5: confirm that a pass in which every attempted panel failed to start guiding goes to `guiding_action`, and that a mid-visit guiding loss the #72 recovery gives up on defers the panel too.
2. Ruling 7: approve the interim tooltip text, or hold the option until #191, #192 and #193 close. Approve "resumes at dusk" in place of "resumes at sunset".
3. ~~Ruling 6: sign the README amendment and the MILESTONE2 note.~~ Approved 2026-09-24 and committed (cbdb59a9).
4. #192: the end-of-night roof close default when a dome is connected.
5. **H2 orchestrator ruling 2: a cloud hold watches the mount it actually has** (#221, #224, #225, #228; 5.8). Binding until the owner overturns it. It answers the question this item asked, whether a hold opened by a target setup's pre-slew gate may make that slew itself under cloud: it may. A hold opened for target B while the mount tracks, or is stopped on, the previous target slews to B through the slew gate and the Sun check when B is above its floor, then watches and probes B; when the slew gate would refuse, or B is below its floor, it stops tracking and says so in its published detail. Cloud does not make a slew unsafe: rain and wind belong to the safety monitor, whose verdict already gates every slew. The hold never reads the last target's flip latch for B. A scheduler wait opens no target-less hold: a cloudy verdict there is recorded and published, the wait goes on under the idle park-hold, and the next target's pre-slew gate opens a hold with a target. After any successful re-point, the held target is the tracked one.
6. **H2 orchestrator ruling 1: with flips off, a cloud hold takes no flip-point action** (5.8). Binding until the owner overturns it. The frame loop never stops at the flip point when flips are off, and the hold now does as it does. The tracking read before each check still catches a mount that stops at its own limit, and the resume-then-recover path runs. Before the ruling, a flips-off hold stopped tracking at the flip point, never tracked again, took no check, and ended the night at its 45 minute bound however the sky cleared.
7. **H2 orchestrator ruling 8: a name-only TARGET is keyed on the canonical identity its name resolves to** (#229; 3.3, 5.9). Binding until the owner overturns it. That is the catalogue id for a fixed deep-sky object, so "M 31" and "M31" are one key, and the canonical body name for a moving body (a planet, the Moon, a comet or a satellite), satellites included by H3 orchestrator ruling 9, whose position is the time-dependent answer A5 was about. ADOPT's 10 arcmin bound is not measured between a moving body's old target and tonight's, since the body has moved on; it matches on its canonical name, and H3 orchestrator ruling 7 measures the bound instead between the old target and the body where it was when the frames were taken.
8. **H2 orchestrator ruling 9: step ids spell the exposure to the microsecond** (3.3). Binding until the owner overturns it. Six decimals with trailing zeros trimmed (`STEP_PLACES` = 6), not the millisecond, so sub-millisecond bias and flat exposures stay distinct: 0.0001 s, 0.0005 s and 0.001 s are three keys.
9. **H2 orchestrator ruling 12: a session file with no status key has no status** (#218). Binding until the owner overturns it. It is unreadable, and no reader takes it for a session: `load` reports it as unreadable, with the reason, and the scanning readers skip it, so it is never swept by the boot sweep, never counted as a crash, and never rewritten. Since H3 (#242) `GET /api/sessions` lists it as an unreadable row with its reason, never as a session, and `DELETE /api/sessions/{id}` removes it, with the permission any session delete needs; a file that is not JSON, or fails validation, is listed and deleted the same way.
10. **H3 orchestrator ruling 1: a hold's site-derived numbers reach only a holder of the site-derived view** (#233; 6.9, 5.8). Binding until the owner overturns it. Logs carry a hold's, an idle watch's and a flip watch's facts in words only: no altitude, azimuth or site-derived clock time beside a target. `GET /api/sequence/resume-arm` carries the numbers behind a resume-arm hold in `hold.site_detail`, which it withholds, absent, not null, from a principal without `CAP_VIEW_SITE_DERIVED`; an admin and an operator read it, as the owner's role-visibility ruling of 2026-09-22 allows. A leak test in the style of the #19 scanner fails if either path hands a viewer a number that moves with the site.
11. **H3 orchestrator ruling 2: the pre-flip side is recorded with `setdefault` only, and kept all run** (#237; 5.7). Binding until the owner overturns it. The side a target occupies before its flip is a fixed property of its east side, so the record stays true after a flip, and nothing clears it within the run: H2's clear after a measured flip and its closed-cycle mark are gone. A flip the flip gate measured before any sighting records the side it left, by the same `setdefault`, so the early flip inside the lead that #222 was about still passes.
12. **H3 orchestrator ruling 3: inside a cloud hold, a refused re-point keeps holding** (#240; 5.8). Binding until the owner overturns it. Any slew-gate refusal of a hold's re-point (the altitude floor, the horizon, a wedge, the zenith keep-out, no saved site, the pier guard, or the Sun's cone) raises `SlewRefused`, and the hold stops tracking and keeps holding, as H2 orchestrator ruling 2 says; it never ends the run. A mount call past its bound still ends the run under the engine's dead-link policy (P0-2).
13. **H3 orchestrator ruling 4: one setup per acquisition** (#241; 5.8). Binding until the owner overturns it. A hold opened by `_setup_target`'s pre-slew gate returns, on release, to the setup it interrupted, which carries on from where it was; the hold never runs `_setup_target` for that target itself.
14. **H3 orchestrator ruling 5: a run's end never abandons a stop the idle watch decided** (#247; 6.17). Binding until the owner overturns it. The wind-down completes an in-flight or unconfirmed idle stop, shielded from the cancel and bounded by `IDLE_STOP_FINISH_S` (180 s), and reads tracking back, whether or not it parks. S2 orchestrator ruling 1 (item 19) refines it: an ending that parks hands the stop to its park instead.
15. **H3 orchestrator ruling 6: after any stop of the mount, a hold re-points instead of resuming in place** (#248; 5.8). Binding until the owner overturns it. After the idle park-hold, a safety pause, a roof close or a hold's own stop, a hold points the mount at its target through the slew gate, because a stopped mount's target has drifted at the sidereal rate. `_mount_stopped_since` records the stop explicitly; `_tracked_target` alone does not decide "elsewhere". The #228 claim, that the hold's detail comes back only once tracking is back, holds for a hold that borrowed a dark or a bias step too.
16. **H3 orchestrator ruling 7: ADOPT matches a moving body only where its frames were taken on it** (#234; 5.9). Binding until the owner overturns it. A moving-body step maps only when the old session's recorded pointing for its target agrees with the body's ephemeris at the step's capture times within `ADOPT_MAX_SEPARATION_ARCMIN` (10 arcmin); otherwise it is listed unmatched with the reason. A session frame records no solve position, so the old plan's coordinates are that pointing.
17. **H3 orchestrator ruling 8: a failed solve is judged by its light level** (#251; 5.9). Binding until the owner overturns it. A failed solve frame whose median sits within a few sigma of the level the camera reads with no light on it (the dark library's master for its readout, or a bias master plus the smallest dark current) is "no light: the optic is capped, covered or obstructed"; otherwise the message stays "not enough stars" (cloud), and with no reference there is no verdict. The classifier lives in one place, `solve/light.py`, and every blind-solve caller uses it. On a no-light verdict ResumeArm sends one push alert naming it and backs off: one retry after `RETRY_INTERVAL_S` (10 min), then every `NO_LIGHT_RETRY_S` (60 min).
18. **H3 orchestrator ruling 9: a satellite counts as a moving body** (3.3, 5.9; item 7). Binding until the owner overturns it. `tonight.MOVING_KINDS` holds the solar-system bodies, comets and satellites, and every rule for a moving body applies to all three: the canonical-name key of item 7 and ruling 7's pointing check. Item 7 used to say "a solar-system body", which the code never meant.
19. **S2 orchestrator ruling 1: an ending that parks hands the idle stop to its park** (#270; 6.17). Binding until the owner overturns it. It refines item 14. When the run's end is about to park the mount, `_run` waits for none of `_finish_idle_stop`: `_hand_idle_stop_to_the_park` cancels the stop's task without awaiting it and moves its fence, `_idle_stop_epoch`, so a guider stop that eats the cancel sends no `set_tracking(False)` once the park has begun; the wind-down parks and closes the roof at once and reaps the task afterwards, bounded, and a park that fails or times out is followed by one bounded stop of tracking and a read-back (`_stop_after_a_failed_park`). An ending parks (`_ending_parks`) on every `SafetyAbort`, `SlewRefused` included, and on a natural end, a cooling skip or a quality stop whose plan has `park_when_done`; an operator's Abort and a failure do not, and complete the stop as item 14 says, for up to `IDLE_STOP_FINISH_S` (180 s).
20. **S2 orchestrator ruling 2: with no master at a failed solve's readout, the light check shoots its own reference** (#262; 5.9). Binding until the owner overturns it. When the calibration library was read and holds neither a dark master for the frame nor a bias master at its readout, `failed_solve_error` takes one frame at the camera's shortest exposure, `SELF_REFERENCE_FALLBACK_S` (0.001 s) when the camera reports none, at the frame's gain, offset and binning, shutter closed, under the hub's exposure guard and within `SELF_REFERENCE_TIMEOUT_S` (60 s), and judges the frame against it as against a bias master. It is kept in process memory for the night per camera, gain, offset, binning and `SELF_REFERENCE_BAND_C` (2 C) band of sensor temperature, unless light may have reached it, and one that fails is no reference and is not kept. A hub with no library loaded, or one that could not be read, takes none, and the frame gets no verdict, as before.
21. **S2 orchestrator ruling 3: a saved PPEC model protects itself only while it could still be restored** (#253). Binding until the owner overturns it. #243's rule keeps a profile's saved PPEC window over a stopping session's when the saved one holds more measured points. The saved count now reads 0 for a file the next start could not restore: the engine restores a window only while the time since its stamp is under `GP_RETAIN_MAX_PCT_PERIOD` (40%) of the kernel period, `GP_DEFAULT_KERNEL_PERIOD_S` (200 s), which is 80 s on the default engine (`gp_restore_horizon_s`, `gp_could_restore`), and a stamp after now is refused, as the restore refuses it. A file past that horizon no longer blocks a shorter session's save, so the quick stop and start of every mosaic hop (6.10) keeps the model it had.

Items 5 to 9 are not the owner's rulings. The orchestrator made them so that the second hardening round (H2, #189) could be built, and each is binding until the owner overturns it (Revision 4). Each keeps the number the round gave it, which is not Revision 2's numbering: H2 orchestrator ruling 1 is not Ruling 1 above, and neither are its rulings 2, 8 and 9.

Items 10 to 18 are not the owner's rulings. The orchestrator made them so that the third hardening round (H3, #189) could be built, and each is binding until the owner overturns it (Revision 5). Each keeps the number the round gave it, which is not Revision 2's numbering, nor H2's: H3 orchestrator ruling 1 is neither Ruling 1 above nor H2 orchestrator ruling 1. All nine of its numbers are also Revision 2's, and four of them are also H2 orchestrator rulings 1, 2, 8 and 9, each on another subject.

Items 19 to 21 are not the owner's rulings. The orchestrator made them so that slice S2 (#189) could be built, and each is binding until the owner overturns it (Revision 6). Each keeps the number the slice gave it, which is not Revision 2's numbering, nor H2's, nor H3's: S2 orchestrator ruling 1 is neither Ruling 1 above nor H2 orchestrator ruling 1 nor H3 orchestrator ruling 1. All three of its numbers are also Revision 2's and H3's, and two of them are also H2 orchestrator rulings 1 and 2, each on another subject.

## Revision 3 (S0 and S1 as built, 2026-09-24)

2026-09-24. S0 and S1 are built (cea8f1f7, 6c6aae47), and a hardening round then changed part of what S1 did. This revision brings the sections in the table below in line with that code; the body's other "today" sentences still describe the code before S0 (see the status line). It makes one decision, the stand-down ruling, and records it as a ruling; everything else describes what was built, or names what was not. `server/tests/test_mosaic_spec_claims.py` holds each edited claim to the code it describes, and takes the numbers and spellings from that code, so a change to either side turns it red.

| # | Where | Was | Now | Source |
|---|---|---|---|---|
| 1 | 1.2 | The chip's route was left to a 3.8 that was never written | `GET /api/flows/{flow_id}/progress`, with the shape of `flow_progress` | S1 item 9, `flows/progress.py` |
| 2 | 3.3 | The step id spelled `{exposure_s:g}`; every single TARGET keyed on its geometry | The millisecond spelling of `identity.step_signature`; a TARGET with only a name keyed on its name; a second pool copy keyed `{name}#1` | #189 A5, A6; `flows/identity.py` |
| 3 | 3.6, ruling 7 | Each migration note shown a single time | Shown on every read until the operator saves | `flows/store.py` (`_migrate`, `touch_run`) |
| 4 | 5.6 step 2, S1 item 2 | The guider stands down before every slew | RULING: only when `plan.guide` is set | `_setup_target`, #148 |
| 5 | 5.8, 6.17 | Nothing said about the cloud hold's clock; the unconfirmed idle stop was retried from the wait loop's tick | The hold's floor, flip and tracking checks on its own clock (`_hold_watch`); the stop retry on its own clock | #189 A3, #203, #205 |
| 6 | 5.9 | CONTINUE read the newest dormant session | `current_for_flow`: the newest by `created_ts`, of any status; ADOPT only within 10 arcmin; the session keeps its `cool_to`; starts refused (409 `resume_recovering`) while auto-resume re-centres | #189 A1, A2, A4, A7; #211 |
| 7 | 5.9 table, S1 item 8 and its test | Skipped panels exempt from the dropped-steps refusal in S1 | Debt carried to S2/S3, where `skipped_ids` and `skip` land | S1 has no groups |
| 8 | 5.10 | The hop term an EMA, and "hops not yet costed" said | `hops_costed` returned by `compute_eta`, not yet rendered (S5) | `compute_eta` |
| 9 | 5.8, owner list item 5 | Nothing said about a hold moving the mount | A hold that stopped the mount slews back to the held target before it judges the sky: after the keep-out, and when the mount was stopped on another target; the mount still tracking another target is #224, open, for the owner | `_hold_repoint`, #203, #225 |

## Revision 4 (the second hardening round, H2, 2026-09-24)

2026-09-24. A second hardening round (H2, #189) changed part of what S1 and H1 built, under five rulings the orchestrator made so that the round could be built; the owner list records them as items 5 to 9, each binding until the owner overturns it. This revision brings the sections in the table below in line with that code; the body's other "today" sentences still describe the code before S0 (see the status line). Where it lists a section Revision 3 also lists, its row is the current one: Revision 3's rows 2, 5, 6 and 9 record H1 and are superseded by the rows below for the same sections. `server/tests/test_mosaic_spec_claims.py` holds each edited claim to the code it describes, and checks that this table lists every section that carries an H2 edit and no other.

| # | Where | Was | Now | Source |
|---|---|---|---|---|
| 1 | 3.3 | The step id spelled to the millisecond; a TARGET with only a name keyed on the name as typed | To the microsecond, so 0.1 ms, 0.5 ms and 1 ms are three recipes; a name-only TARGET keyed on the canonical identity `tonight.resolve_target` resolves it to, so a rename to another spelling of the same row keeps the ids | H2 orchestrator rulings 8 and 9, #229; `flows/identity.py`, `flows/tonight.py` |
| 2 | 5.8 | A hold re-pointed the mount after the keep-out and when it found the mount stopped; a mount still tracking the last target was #224, open, for the owner; a flips-off hold stopped at the flip point for good; the idle stop's first attempt ran on the wait loop | H2 orchestrator ruling 2: a hold for a target the mount is not on slews there through the slew gate and the Sun check when the target is above its floor, and otherwise stops tracking and says why, except that a pier-guard or Sun-cone refusal ends the run (#240); a scheduler wait opens no target-less hold; the published detail says what the hold is doing. Ruling 1: with flips off, no flip-point action. The idle stop's first attempt is made on the retry's task | `_hold_for_clear`, `_hold_repoint_refusal`, `_hold_repoint`, `_hold_park`, `_hold_flip_watch`, `_note_hold_deferred`, `_idle_park_hold`; #216, #221, #224, #225, #228, #240 |
| 3 | 5.9 | ADOPT within 10 arcmin for every match; the recovery ladder stopped only for a run that got in | ADOPT's bound inclusive, and a moving body matched on its canonical name with no bound (what that trusts is #234); Abort and a disarm stop the ladder, and `GET /api/sequence/resume-arm` reports it | H2 orchestrator ruling 8, #229, #220; `flows/continuation.py`, `sequence/resume_arm.py`, `api/app.py` |
| 4 | 6.15 | "Unchanged": Abort disarms a running session's auto-resume | Abort also stops the recovery ladder and disarms the session it was recovering; the other callers of `engine.abort` do not yet (#238) | #220; `sequence_abort`, `ResumeArm.stop_recovery` |
| 5 | 6.17 | The unconfirmed stop retried on its own task, and the first attempt made inline on the wait loop | The first attempt on the same task; the run-start cooling wait watches a target auto-resume re-centred, and the two waits that still do not are #236 | #216, #202; `_idle_park_hold`, `_idle_stop_retry`, `_cool_and_wait` |
| 6 | owner list | Item 5 asked whether a hold opened before a slew may make that slew | Item 5 records H2 orchestrator ruling 2 in its place, and items 6 to 9 record rulings 1, 8, 9 and 12, each binding until the owner overturns it | #189 |

## Revision 5 (the third hardening round, H3)

2026-09-25. A third hardening round (H3, #189) changed part of what S1, H1 and H2 built, under nine rulings the orchestrator made so that the round could be built; the owner list records them as items 10 to 18, each binding until the owner overturns it. This revision brings the sections in the table below in line with that code; the body's other "today" sentences still describe the code before S0 (see the status line). Where it lists a section Revision 4 also lists, its row is the current one: Revision 4's rows 2, 3, 4, 5 and 6 record H2 and are superseded by the rows below for the same sections. Revision 2's check of the ruling 7 tooltip is a check of HEAD 5c8530c1 and stays as it was: since #251 its #192 row's resumed night that "holds until dawn" behind a closed cover alerts once and retries hourly instead, where the calibration library holds a reference that places the frame at the camera's no-light level (5.9; the rig holds none yet, #262). `server/tests/test_mosaic_spec_claims.py` holds each edited claim to the code it describes, checks that this table lists every section that carries an H3 edit and no other, and checks that every ruling a server module or test cites by label is the entry this spec records under that label (#239).

| # | Where | Was | Now | Source |
|---|---|---|---|---|
| 1 | 5.7 | The pre-flip record kept per target, with nothing said of when it is written or cleared; H2 cleared it after a measured flip and marked the target so that nothing wrote it again that run | Written with `setdefault` only, at the first sighting east of the meridian or by a flip the flip gate measured before any sighting, and kept all run, a flip included | H3 orchestrator ruling 2, #237, #222; `_enforce_flip_owed`, `_maybe_meridian_flip` |
| 2 | 5.8 | A pier-guard or Sun-cone refusal of a hold's re-point ended the run (#240); a hold opened by a setup's gate ran a setup of its own on release, and the interrupted setup then a second; "elsewhere" was `_tracked_target` alone, so a hold resumed in place on a mount stopped on its own target | A refused re-point keeps holding, and a bound's expiry still ends the run (P0-2); one setup per acquisition; after any stop the hold re-points (`_mount_stopped_since`); every line and detail in words | H3 orchestrator rulings 3, 4, 6 and 1; #240, #241, #248, #233, #263; `SlewRefused`, `_hold_repoint`, `_acquisition_behind_gate`, `_hold_for_clear` |
| 3 | 5.9 | A moving body matched on its name with nothing checked (#234); ADOPT's catalogue calls made in the write lock, on the event loop; the 409 named the route; only Abort and a disarm stopped the ladder; a no-light recovery solve retried every 10 min, telling nobody | A body step measured against the body at its capture times; the catalogue asked off the loop, before the lock; the 409 names the Monitor; the teardown routes stop the ladder too; a no-light verdict alerts once and backs off to hourly | H3 orchestrator rulings 7, 8 and 9; #234, #249, #246, #238, #251, #262; `flows/continuation.py`, `api/app.py`, `solve/light.py`, `sequence/resume_arm.py` |
| 4 | 6.9 | The resume-arm hold's reason and the engine's hold lines carried altitudes, floors and minutes; the night log file kept everything; the flip-point detail counted its minutes | Words at the source; the numbers ride `hold.site_detail`, withheld from a viewer, and `SlewRefused.site_detail`; no log line keeps them yet | H3 orchestrator ruling 1, #233, #258; `api/redact.py`, `sequence/resume_arm.py`, `sequence/engine.py` |
| 5 | 6.15 | Disconnect, profile apply and activate and a forced rig connect neither stopped nor saw the ladder (#238) | Each stops it with its session disarmed, the three that can be forced refuse unforced, and each waits for it, bounded, before it tears the rig down | #238, #256, #257; `api/app.py`, `ResumeArm.wait_stopped` |
| 6 | 6.17 | Two waits did not watch (#236); the run's end cancelled the idle-stop task | Both waits watch; the run's end and an abort complete a decided stop, bounded and shielded, and read tracking back | H3 orchestrator ruling 5, #236, #247; `_cooler_gate`, `_await_camera_lane`, `_finish_idle_stop` |
| 7 | owner list | Items 5 to 9; item 7 said a solar-system body and that the name was trusted (#234); item 9 said every reader reports an unreadable file | Items 10 to 18 record H3 orchestrator rulings 1 to 9, each binding until the owner overturns it; item 7 says a moving body, satellites included, and names ruling 7's check; item 9 says `load` reports the file, the list shows it and DELETE removes it | #189, #242 |

## Revision 6 (slice S2 as built)

2026-09-25. Slice S2 (#189), the engine group driver, is built, under three rulings the orchestrator made so that it could be built; the owner list records them as items 19 to 21, each binding until the owner overturns it, and item 14 says that ruling 1 refines it. This revision brings the sections in the table below in line with that code; the body's other "today" sentences still describe the code before S0 (see the status line). An S2 edit carries S2's mark, "(S2, ...)", "S2 built" or an S2 orchestrator ruling, because the body names S2 as a plan throughout and those plan sentences are not edits. Where it lists a section Revision 5 also lists, its row is the current one: Revision 5's rows 1, 2, 3, 4, 5, 6 and 7 record H3 and are superseded by the rows below for the same sections. Revision 5's note on the ruling 7 tooltip now holds wherever the library can be read at all and the check can shoot: since #262 a library with no master at the solve's readout is given one by the check's own shortest-exposure frame (5.9), and only a self-shot that fails (no connected camera, a busy one, a timeout) leaves the frame with no reference. `server/tests/test_mosaic_spec_claims.py` holds each edited claim to the code it describes, checks that this table lists every section that carries an S2 edit and no other, and checks that every ruling a server module or test cites by label, S2's included, is the entry this spec records under that label (#239).

| # | Where | Was | Now | Source |
|---|---|---|---|---|
| 1 | 1.6 | A follower may fill a deferral wait, whose end is one of `group_ready_ts`'s times | Not yet: the deferral wait blocks inside `_close_group_pass`; `group_ready_ts` is a crossing, a limit's clearing or a gating's opening | #304; `_group_ready_ts`, `_close_group_pass` |
| 2 | 3.4 | `Session` gained `set_aside` only, its records keyed by "the durable log's night key" | `locked_angles` too, written only through `lock_angle`; records written through `note_set_aside` with `events.night_key()`, saved at once and read back by `start` | #208, ruling 9; `sequence/session.py`, `_persist_set_aside` |
| 3 | 5.1 | `_GroupRun` diffing a ledger snapshot; the reach verdict waited on the pier limit and raised `SafetyAbort` for no site | `GroupRun` in `group_rules.py`, summing each visit's exposures; `REACH_TAGS`: the floor and the keep-out wait, no site and a flips-off pier change refuse, no site with the gate's `SlewRefused`; a floor advance raised as `FloorStop` and recorded; the boundary's rig-fault and none-live outcomes | #132, #208; `ReachVerdict`, `_mount_floor_verdict`, `_eligibility_now`, `GroupRun.close_pass` |
| 4 | 5.2 | The scheduler, ResumeArm and the progress route all use the order | The scheduler and ResumeArm do, and the progress route does not yet; ResumeArm's `setting_first` has no time to the floor | #159; `panel_order.py`, `_resort_group`, `_time_to_floor_s` |
| 5 | 5.3 | The bound as designed | `_run_visit` under a bound, `passes=None` for a follower, a flip-point deadline in clock seconds, and a visit its deadline ended before its first frame is not a visit | #189, #300; `_run_visit`, `_visit_panel`, `VisitBound` |
| 6 | 5.6 | The rotate shortcut, the angle check and the pier-side check as designed; the goto passed `target.rotation_deg` | `Hub._rotation_already_set`, `_group_hop_checks`, `_group_angle_check`, `_rotator_evidence`, `GroupSetAside` and `_group_pier_check` built; the goto commands `_commanded_rotation`; the guide-lost deferral not built | U-04, U-06, #136, #303 |
| 7 | 5.7 | The rule as designed, with `h_p <= 0` past the meridian | Built in `_meridian_now`; a panel is past the meridian only `MERIDIAN_SIDE_MARGIN_S` after its crossing; a confirming hop disarms that panel's latch | #136; `_meridian_now`, `meridian_eligibility`, `_group_pier_check` |
| 8 | 5.8 | A safety pause opened by a frame-loop hold's own gate ran a setup of its own, and the release another (#263) | The hold marks its own gate's acquisition, so a pause or a reopen it opens returns to the hold | #263; `_hold_for_clear`, `_acquisition_behind_gate` |
| 9 | 5.9 | ResumeArm re-centred on the first non-calibration target with no rotation; nothing at the rig's readout gave the light check a reference; the skipped-panel exemption was debt | `recentre_candidates` in the run's order, with the commanded angle, refusing when everything owed is set aside tonight; the check's own shortest-exposure frame stands in for a missing bias master; `plan_replace_report` reads `skipped_ids` | S2 orchestrator ruling 2; #159, #283, #284, #295, #262, #282; `sequence/resume_arm.py`, `solve/light.py`, `flows/continuation.py` |
| 10 | 5.10 | Meridian waits on a `CAP_VIEW_SITE_DERIVED` topic; "under S2 the hops become the visits" | `state.group.meridian_wait`, with `panel` and `pass` withheld across the wait on every seam and the wait's publishes flagged, while a viewer's GET still sees `target` change; the ETA prices the visits owed | #166, #297; `_group_state`, `_withhold_group_timing`, `_visits_owed` |
| 11 | 6.3 | The group-aware horizon guard as designed | `_start_preflight` on both start paths: 409 `below_horizon` for a group, `below_horizon` in a started run's answer; resume and recover run neither check | #132, #291; `api/app.py` |
| 12 | 6.9 | The `site_derived` rule as designed | `bus.log(..., site_derived=True)` and `events.is_site_derived`, dropped by the ring, the export, the night reader and both WS lanes; what the engine flags, and what it does not yet | #166, #302; `events.py`, `api/redact.py`, `sequence/engine.py` |
| 13 | 6.15 | A forced rig connect aborted and disarmed before it validated its body (#257) | It validates the whole body first; a 422 touches nothing | #257; `connect_rig` |
| 14 | 6.17 | A run's end completed the idle stop whether or not it parked | An ending that parks hands the stop to its park, fenced; the wind-down stops the guider on a task of its own, parks and closes at once, and stops tracking after a failed park; two windows remain | S2 orchestrator ruling 1; #270, #305, #306; `_ending_parks`, `_hand_idle_stop_to_the_park`, `_wind_down`, `_stop_after_a_failed_park` |
| 15 | S1 | Item 8 and its test carried the skipped-panel exemption as debt to S2/S3 | S2 built the server half; the test runs against a compile patched to skip a panel, and S3 owes the real `skip` | #189; `flows/continuation.py`, `test_continue_skipped_panels.py` |
| 16 | S2 | The plan | What was built, where its tests are, and what was not | #189, #298, #303, #304 |
| 17 | 9 | I-11: S2 fixes `on_target_complete` for panels, and the issue covers single targets | S2 gated a single target's rules on its completion too | #157; `_schedule_loop`, `_visit_panel`, `test_group_rotation.py` |
| 18 | ruling 9 | "Built in S2 alongside the angle check" | What was built, and that no editor draws the lock yet | #189, #295; `Session.lock_angle`, `_settle_locked_angle`, `_commanded_rotation`, `flows/progress.py` |
| 19 | owner list | Items 5 to 18; item 14 said "whether or not it parks" | Items 19 to 21 record S2 orchestrator rulings 1 to 3, each binding until the owner overturns it; item 14 says ruling 1 refines it | #189, #270, #262, #253 |
