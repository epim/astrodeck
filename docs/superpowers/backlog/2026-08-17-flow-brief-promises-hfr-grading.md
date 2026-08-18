# The flow brief promises HFR grading the run does not do

Found 2026-08-17 while building a flow for NGC 7129.

`/api/flows/{id}/tonight` returns a `brief` — the plain-English account of what
the night will do, and the thing an operator reads before pressing Run. For a
FILTER CYCLE stage it ends:

> ...so every channel grows evenly; **a sub is graded and only counts below
> HFR 3.5″**.

Nothing does that.

* The cycle node's `reject` param (default `3.5`) survives `compile_plan` as
  `steps[].reject_hfr`, and is then **dropped by `to_sequence_plan`** — grep
  `to_plan.py` for "reject" and the only hit is an unrelated docstring. The
  compiled `SequencePlan` step carries `filter, exposure_s, gain, offset,
  binning, count, frame_type, adu_target, panel_brightness, per_visit` and no
  grading field.
* `plan.hfr_reject_factor` comes out `None`, so it resolves to the rig standard
  — and this rig's `standards.max_eccentricity`/`min_stars`/`max_guide_rms` are
  all 0, i.e. **grading is off entirely**.

So the brief names a specific threshold, in arcseconds, for a behaviour that is
switched off. An operator reading it would reasonably believe soft frames are
being discarded and their counts topped up.

**It is also a SILENT loss.** `to_plan.losses()` reports nine other node params
that do not reach the run (safety, slew, autofocus, guide, report, condition,
refocus, abort, instruction text) — the whole point of that list being that a
flow says what it cannot deliver. The cycle node's `reject` is not in it. The
one mechanism designed to catch this misses this one.

## Fix, either direction

1. **Carry it.** `reject_hfr` -> `plan.hfr_reject_factor` (and mind
   `count_mode`: with grading on and `count_mode="accepted"` a rejected frame is
   re-shot, which is the #252 owed-frames path — attempts mode is what this
   rig's plans have used).
2. **Or drop it honestly.** Remove the clause from the brief and add `reject` to
   `losses()` so the flow says so.

(1) is what the node's presence implies. Either way the brief and the run have
to agree, and `losses()` has to know.

## Reproduction

```python
from astrodeck.flows.compile import compile_plan
from astrodeck.flows.to_plan import to_sequence_plan, losses
compiled = compile_plan(graph_with_a_cycle_node)
assert compiled["targets"][0]["steps"][0]["reject_hfr"] == 3.5
plan, unmapped = to_sequence_plan(compiled, graph_with_a_cycle_node)
assert plan.hfr_reject_factor is None            # it did not arrive
assert not any("reject" in l["key"] for l in losses(unmapped))   # and nothing said so
```

---

## FIXED 2026-08-17 (`8fe828e`), in the "say so" direction — plus a follow-up

The clause is gone from both the cycle and capture branches of `tonight.brief`,
and `to_plan.INERT_PARAMS` now reports the drop where drops are reported. Since
`75010fd` that also puts a `!` on the CYCLE node on the canvas.

Not wired up instead, and the units are the reason — `hfr_reject_factor` is a
MULTIPLE of the running median, not an absolute HFR. A test pins that: wire it
and the test fails, telling you to check the units before deleting the loss.

### Still open: the doctor reads `reject: 0` as "reject everything"

Setting the node's `reject` to 0 — the obvious way to say "I do not want
grading" — produces:

> watchdog threshold 3.2″ sits above the grader's reject 0″ — frames get
> rejected before the rule can ever fire

Nothing is rejected: 0 means off everywhere else in this codebase
(`hfr_reject_factor`, `min_stars`, `max_guide_rms`, `max_eccentricity` all
document "0 disables"), and `reject` reaches no engine at all. So the rule
compares two inert numbers and gives advice about a mechanism that does not
run. The effect is that turning the dial off is punished and leaving it at a
value that does nothing is rewarded — which is why the NGC 7129 flow keeps
`reject: 3.5` and an honest loss line rather than `0` and a false warning.

Fix: treat 0 as "no grading" in that doctor rule (skip the comparison), the
same way every other floor in the product treats it.
