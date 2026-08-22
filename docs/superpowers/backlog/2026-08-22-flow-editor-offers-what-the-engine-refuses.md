# The flow editor offers settings the engine will not run

**Status:** OPEN. Found 2026-08-21 22:00 while building a real observing flow
under time pressure, which is the only reason it was found: every one of these
is invisible until you press Run.

## What happened

Asked for "a Fireworks Galaxy set plus the best-of module", I built a flow with
a `pool` node and a `cycle`, compiled it clean, and pressed Run. It was refused.
Then I stripped things until it started. What follows is what had to go.

## 1. The `pool` node compiles and cannot run

`POST /api/flows/{id}/run` answers 409:

```
parts of this flow do not survive the compile
  targets[*].pool_rank -- 'best of several' is not something the engine can do
  yet: all 5 candidates run in rank order, each skipped if its window is missed,
  rather than one being chosen
```

So the editor offers a **pool** node with a `strategy` of "Best available
(alt x moon)", the compiler turns it into five ranked targets and reports no
warning, and the engine then refuses the whole flow. There is a shipped example
flow named "Best-of-four pool night" and another named "Campaign - best of 4,
month-scale". Neither can run.

Two honest exits, and they are not equal:

- **Implement best-of.** The compiler already produces the ranked list and the
  engine already skips a target whose window is missed. What is missing is
  choosing ONE rather than walking all of them.
- **Or say so in the editor.** If the node cannot do what its own strategy
  dropdown says, the dropdown should not offer it, and the two example flows
  should not ship.

What is not acceptable is the current state, where the only way to discover it
is to lose observing time to a 409 after dark.

## 2. Six node types have settings that reach nothing

To get a plain single-target flow to start I had to empty the params on **six**
nodes. The compiler names each one:

```
nodes.slew        the SLEW node's settings do not reach the run
nodes.autofocus   the AUTOFOCUS node's settings do not reach the run
nodes.guide       the GUIDE node's settings do not reach the run
nodes.report      the REPORT node's settings do not reach the run
nodes.condition   the CONDITION node's settings do not reach the run
nodes.refocus     the REFOCUS node's settings do not reach the run
nodes.cycle.reject   the CYCLE node's HFR reject threshold does not reach the run
instructions[*].message
```

The editor accepts a plate-solve tolerance, a retry count, a solver name, a
V-curve step size, a sample count, a guider provider, a settle time, a dither in
pixels, an HFR reject threshold and a refocus boundary. **None of them reach the
plan.** The run is refused rather than silently ignoring them, which is the
better of the two bad options, but the knobs are still wired to nothing.

Note what this means for the flow I actually ran: it has NO HFR-triggered
refocus, because the condition and refocus nodes had to be deleted outright to
get a green run. The engine refocuses on its own schedule (23 times last night),
so the loss is tolerable, but it is a loss nobody chose.

Relevant history: `4d8aa2b fix(flows): the doctor demanded four wires the engine
does not consult`. That commit fixed the doctor complaining about wires. This is
the same disagreement one layer down - the compiler and the engine disagree
about what a node means - and it was not fixed by that change.

## 3. Aborting a run does not stop auto-resume

`POST /api/sequence/abort` returned `{"aborted": true}` and the sequence stopped.
**Fifty seconds later auto-resume started the previous target again.** I had to
abort a second time and take the slot within the same script to get the new flow
in.

That is defensible behaviour for a crash and wrong for a deliberate abort: a
human pressing Stop and a mount falling over are not the same event, and only
one of them wants the night restarted automatically. Either abort should disarm
the session, or the UI needs a visible "armed for auto-resume" state and a way
to disarm it that is not "abort twice, quickly".

## Why this matters more than it looks

Every one of these costs dark time, and dark time is the scarce resource. Tonight
the sequence was: abort, 409, edit, 409, edit, 409, edit, run - about eight
minutes of clear sky at 66x noise spent discovering that the editor's offers and
the engine's capabilities are different sets.

The compile step already knows all of it. `POST /api/flows/{id}/compile` returns
the same `unmapped` list that the run refuses on. **The editor should show it
while you are editing**, not at the moment you ask for photons.
