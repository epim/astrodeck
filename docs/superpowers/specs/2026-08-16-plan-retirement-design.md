# Retiring the Plan tab (#239)

Design, 2026-08-16. Three staged pieces (A policy, B demotion, C flows
vocabulary), built in that order. Decisions taken by the operator; this records
what is being built and why.

## The question

Flows and Plan are two ways to describe a night. Flows is the newer one, it is
where the campaign work went, and the nav rail is at sixteen entries with
App.tsx carrying two separate comments about the rail height being a real
constraint. So: can Plan go?

## What the gap actually is

`SequenceView` (the Plan tab) edits 21 plan-level fields.
`flows/to_plan.py` sets eight - `name`, `targets`, `instructions`,
`park_when_done`, `warm_cooler_when_done`, `cloud_hold_darks`, `day_darks`, and
`cool_to` when the caller injects the rig's standing setpoint. Four of those
overlap with what Plan exposes. Every other field on a flow-derived plan is the
`SequencePlan` model default, because `plan_extras` is deliberately a short
explicit list rather than a merge.

**On paper that is a 17-field gap. In practice it is about one field.** The last
eight sessions on the rig, read from `captures/sessions/`:

| night | source | settings beyond what a flow would produce |
|---|---|---|
| 08-16 NGC 7129 LRGB+SHO | Plan | `refocus_on_temp_delta_c=2.0`, `cool_to=-10.0` |
| 08-16 NGC 6946 SHO | flow | - |
| 08-12 NGC 6946 SHO | flow | `cool_to=-5.0` |
| 08-12 NGC 6946 SHO | Plan | nothing |
| 08-12 NGC 6946 Ha | Plan | nothing |
| 08-12 NGC 6946 narrowband | flow | - |
| 08-12 Flats capped | Plan | `guide=False`, `meridian_flip=False`, `safety_check=False` |
| 08-12 NGC 6946 Oiii+S | Plan | nothing |

Five of eight are byte-equivalent to what a flow would have compiled. `cool_to`
is already injectable. So the imaging-night gap is `refocus_on_temp_delta_c`,
and the calibration-night gap is the guide/flip/safety triple.

And across every recent night, the entire quality-gate family - `min_stars`,
`max_guide_rms`, `max_eccentricity`, `hfr_reject_factor` - and `count_mode` were
never used at all.

## What measuring it turned up

The gap is not the interesting part. This is: **a flow-driven night runs with
every quality gate off and no temp-drift refocus, and there is nowhere in Flows
to say otherwise.** Last night's run got `refocus_on_temp_delta_c=2.0` only
because it came from Plan; the same target drawn as a flow loses it silently.

That inverts the framing. Most of the seventeen were never per-night intent -
they are rig policy, the same on every night, and they belong somewhere that
applies to every night including the flow-driven ones. Config already houses
exactly this kind of thing: `EscalationConfig` holds `hfr_reject_action` and
`hfr_retake_limit_per_target` while the `hfr_reject_factor` they act on lives on
the plan, which is one setting split across two layers for no reason.

So Plan is not retired by deleting it. It is retired by moving what was always
policy into policy, at which point what is left does not justify a rail slot.

---

## A. Rig policy moves into Settings

### The twelve fields, and where each lands

The arithmetic, since two numbers are in play: **eleven of the seventeen
unreachable fields move here.** The twelfth row below, `cool_timeout_s`, is a
`SequencePlan` field the Plan tab never exposed at all - it is included because
it is policy by the same test as the rest, and leaving it as the one piece of
cooling policy not in `CoolingConfig` would be arbitrary. The six that remain
after this stage are `guide`, `meridian_flip`, `count_mode`, `dither_every`,
`autofocus_every` (C's problem) and `safety_check` (stays on the plan).

Existing homes wherever one exists - a new block for the rest, not a dumping
ground:

| field | home | why it is policy |
|---|---|---|
| `dither_pixels` | `GuideConfig` | a function of guide-scope image scale |
| `recover_guiding` | `GuideConfig` | standing behaviour, not a nightly choice |
| `cool_timeout_s` | `CoolingConfig` | a property of this camera and this ambient |
| `hfr_reject_factor` | `EscalationConfig` | reunites the threshold with the `hfr_reject_action` that acts on it |
| `meridian_flip_warn_min` | `SafetyConfig` | operator lead-time preference |
| `apply_filter_offsets` | `StandardsConfig` (new) | a property of the filter set |
| `refocus_on_temp_delta_c` | `StandardsConfig` | a property of the OTA and focuser |
| `min_stars` | `StandardsConfig` | the operator's standard for a usable frame |
| `max_guide_rms` | `StandardsConfig` | as above |
| `max_eccentricity` | `StandardsConfig` | as above |
| `max_consecutive_rejects` | `StandardsConfig` | guard policy |
| `max_consecutive_rejects_night` | `StandardsConfig` | guard policy |

**NAMING HAZARD:** `WcsStampConfig.min_stars` already exists and is a different
thing entirely - the star floor below which a saved light is not WCS-stamped.
The new one is `standards.min_stars`, the floor below which a frame is rejected.
Two settings, two meanings, one word. Neither should be "consolidated" into the
other, and the Settings copy for each has to say which is which.

### What does NOT move, and why

- **`guide`, `meridian_flip`, `count_mode`, `dither_every`, `autofocus_every`** -
  genuine per-night intent. The unguided Iris night and the dec-blind
  circumpolar flip fix (#248) are both evidence that these change per target.
  They are C's problem.
- **`safety_check`** - stays on the plan. A global "safety off" is a footgun of a
  different order from a global dither distance, and the flats session turning it
  off for one run is exactly the deliberate, scoped act it should stay.
- **`park_when_done` / `warm_cooler_when_done`** - `plan_extras` already forces
  both True for every flow, and the Tonight timeline has always promised it.
  Leave them.

### The inheritance rule

Each of the twelve becomes `X | None = None` on `SequencePlan`, where `None`
means **inherit the rig's standard**. The engine resolves once at run start
against the config snapshot it already takes (`self._cfg`).

Additive, so saved plans with explicit values keep them; and it makes "inherit"
expressible, which is the thing Flows needs - a compiled flow leaves all twelve
`None` and picks up the operator's standards instead of the model defaults.

**Resolution happens in one place**, a `resolve_policy(plan, cfg)` returning a
frozen dataclass the engine reads instead of reading `plan.<field>` at nine
scattered call sites. One function, one test, one thing to sabotage.

**The layering must be visible, because this project has been bitten by exactly
this.** The active profile's providers beat global config while `/api/config`
showed the losing layer, and Polar ran simulated for weeks. So:

- the resolved values, **with the layer each came from**, are recorded in the
  session report at run start - the report answers "what did this night actually
  run with", which today nothing does;
- the Plan editor shows, per field, whether the value is inherited or overridden,
  and what the inherited value is;
- `resolve_policy` is pure and takes both layers explicitly, so a test can assert
  the precedence directly rather than through a running engine.

### Migrating saved plans

Every saved plan today carries concrete values for all twelve, most of them the
model default nobody chose. Left alone they would all read as deliberate
overrides and no Settings change would ever reach them - the migration would do
nothing for the plans that already exist.

So: a one-shot migration nulls any of the twelve whose stored value **equals the
pre-migration model default**, and leaves anything else explicit. Last night's
`refocus_on_temp_delta_c=2.0` stays an override; a `dither_pixels=3.0` nobody
ever touched becomes inherit.

This cannot distinguish "deliberately chose 3.0" from "never touched it". That
is a real loss of information and it is accepted: before this change there was no
way to express the difference, so the information was never captured, and
treating an untouched default as a choice is the reading that makes the feature
useless. The migration logs one line per plan it changes.

---

## B. Plan comes off the rail

- Remove `{ id: "sequence", label: "Plan" }` from `NAV` in `ui/src/App.tsx`. The
  Risk-10 rule there is about not RE-ORDERING a landed order; removing one entry
  shifts nothing else, but the comment block should say that explicitly so the
  next reader does not think the rule was broken.
- The route stays reachable, so bookmarks and deep links keep working.
- Entry points: from Flows ("Edit the compiled plan", beside the Run control),
  and from Settings > Standards ("per-plan overrides live in the plan editor").
- `ui/src/__tests__/nav.test.ts` and the BottomNav / NavMoreSheet references need
  updating; `nav.test.ts` greps the icon table, so the `bridge` placeholder note
  in App.tsx stays relevant.
- The rail drops to fifteen, which is what both padding comments in App.tsx were
  fighting. Whether the padding goes back is a judgement call at the time - do
  not change it blind.

---

## C. What Flows still cannot say

**Scoped deliberately as a decision rule, not a feature list**, because A changes
what is left over and specifying C first would be guessing.

Once A has landed, walk the fields that did NOT move (`guide`, `meridian_flip`,
`count_mode`, `dither_every`, `autofocus_every`, `safety_check`) and ask of each:
**can a flow say this today, and does the graph mean what it draws?**

What is known now:

- `to_plan.py` never sets `guide`. A flow with no GUIDE node still guides; a flow
  with one changes nothing. The doctor is honest about it - `inert_nodes` reports
  "the GUIDE node's settings do not reach the run" - so this is a limitation the
  operator is told about, not a silent lie. C's job is to make the drawn graph
  mean what it says.
- `dither_every` / `autofocus_every` are cadences the CAPTURE LOOP node is the
  obvious carrier for.
- `count_mode` is plan-level and has no node; it may not need one, since nothing
  on this rig has ever used `accepted`.

Anything that survives that walk and has never been used on a real night should
be left unbuilt and recorded as such, rather than ported for symmetry.

---

## Testing

- `resolve_policy` gets a direct table test: plan value wins, `None` inherits,
  and the recorded layer name matches the winner. Sabotage the precedence and it
  must go red.
- One engine-driven test per stage boundary: a flow-compiled plan picks up the
  configured standards (the hole this closes), and a plan with an explicit
  override keeps it.
- The migration gets a test with a plan holding one deliberate override and one
  untouched default, asserting exactly one field survives as explicit.
- B: `nav.test.ts` asserts Plan is absent from the rail AND reachable by route -
  a demotion that accidentally deletes the route is the failure to catch.

## Risks

- **Two layers, one setting** is the pattern that produced the Polar incident.
  Mitigated by a single pure resolver, the source recorded in the report, and the
  editor showing which layer won - but it is the thing to be most careful about.
- **The migration is lossy** in the one way described above.
- **`min_stars` now names two different settings.** Called out here because the
  next person to see them will want to merge them.
- **C may turn out to be empty.** That would be a good outcome, not a failed
  stage.
