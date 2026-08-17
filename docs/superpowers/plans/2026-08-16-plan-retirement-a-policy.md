# Stage A: rig policy moves into Settings — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The twelve rig-policy fields live in config, a plan field of `None`
means "inherit the rig's standard", and a flow-driven night gets the operator's
quality gates instead of the model defaults.

**Architecture:** One new module, `sequence/policy.py`, holding a frozen
`RunPolicy` and a pure `resolve_policy(plan, cfg)`. The engine resolves once at
`start()` and reads `self._policy.<field>` at the twenty call sites that read
`plan.<field>` today. `models.py` stays free of config imports.

**Tech Stack:** Python 3.12 / pydantic v2 / pytest-asyncio; React + TypeScript UI
(no test framework — plain tsx scripts, `npm test`, single file via `npx tsx`).

**Spec:** `docs/superpowers/specs/2026-08-16-plan-retirement-design.md`

## Global Constraints

- No emojis anywhere, including commit messages.
- `git commit -- <paths>`, never bare `git add`.
- Server suite with `-n auto`. UI: `npm test`, never `npx vitest`.
- Config additions must default to TODAY'S plan-model values, so a rig that
  never opens Settings behaves byte-identically.
- Do NOT touch the rig. 0.2.80 is already deployed and will be running an
  unattended NGC 7129 resume tonight.

---

### Task A1: The twelve fields get homes in config

**Files:**
- Modify: `server/astrodeck/config.py`
- Test: `server/tests/test_policy_config.py` (create)

**Interfaces:**
- Produces: `StandardsConfig` + `AppConfig.standards`; new fields on
  `GuideConfig` (`dither_pixels`, `recover_guiding`), `CoolingConfig`
  (`cool_timeout_s`), `EscalationConfig` (`hfr_reject_factor`), `SafetyConfig`
  (`meridian_flip_warn_min`).

- [ ] **Step 1: Write the failing test**

```python
"""Stage A: the rig's standards live in config, and their defaults are the
plan model's defaults so nothing changes for a rig that never opens Settings."""
from astrodeck.config import AppConfig
from astrodeck.sequence.models import SequencePlan


def test_every_moved_field_defaults_to_the_plan_models_value():
    cfg, plan = AppConfig(), SequencePlan()
    pairs = [
        (cfg.guide.dither_pixels, 3.0),
        (cfg.guide.recover_guiding, True),
        (cfg.cooling.cool_timeout_s, 600),
        (cfg.escalation.hfr_reject_factor, 0.0),
        (cfg.safety.meridian_flip_warn_min, 15.0),
        (cfg.standards.apply_filter_offsets, True),
        (cfg.standards.refocus_on_temp_delta_c, 0.0),
        (cfg.standards.min_stars, 0),
        (cfg.standards.max_guide_rms, 0.0),
        (cfg.standards.max_eccentricity, 0.0),
        (cfg.standards.max_consecutive_rejects, 10),
        (cfg.standards.max_consecutive_rejects_night, 20),
    ]
    for got, want in pairs:
        assert got == want


def test_standards_min_stars_is_not_the_wcs_one():
    """WcsStampConfig.min_stars is the floor below which a saved light is not
    WCS-stamped. standards.min_stars is the floor below which a FRAME IS
    REJECTED. Same word, unrelated meanings - do not consolidate them."""
    cfg = AppConfig()
    assert cfg.wcs_stamp.min_stars == 0
    assert cfg.standards.min_stars == 0
    cfg.standards.min_stars = 40
    assert cfg.wcs_stamp.min_stars == 0
```

- [ ] **Step 2: Run it, expect AttributeError on `cfg.standards`**

```
cd server && ./.venv/Scripts/python.exe -m pytest tests/test_policy_config.py -p no:randomly -q
```

- [ ] **Step 3: Add the fields**

`StandardsConfig` is new; the other five go on existing blocks beside the
settings they already relate to (`hfr_reject_factor` next to the
`hfr_reject_action` that acts on it). Every default copied from
`SequencePlan`'s current value. Add `standards: StandardsConfig =
StandardsConfig()` to `AppConfig`.

- [ ] **Step 4: Green, then round-trip**

Confirm an existing config JSON without a `standards` key still loads (the block
is all-defaults and therefore purely additive).

- [ ] **Step 5: Commit**

```bash
git commit -m "feat(config): the rig's imaging standards get a home" -- \
  server/astrodeck/config.py server/tests/test_policy_config.py
```

---

### Task A2: `resolve_policy`, and the plan learns to say "inherit"

**Files:**
- Create: `server/astrodeck/sequence/policy.py`
- Modify: `server/astrodeck/sequence/models.py` (the twelve fields; `quota_unbounded`)
- Modify: `server/astrodeck/sequence/engine.py` (resolve at start; 20 read sites)
- Modify: `server/astrodeck/api/app.py` (4 `quota_unbounded` calls), `server/astrodeck/sequence/resume_arm.py` (1)
- Test: `server/tests/test_policy_resolution.py` (create)

**Interfaces:**
- Consumes: `AppConfig.standards` etc. from A1.
- Produces: `RunPolicy` (frozen dataclass, twelve attributes + `sources: dict[str, str]`
  mapping field name to `"plan"` or `"rig"`), `resolve_policy(plan, cfg) -> RunPolicy`,
  and `quota_unbounded(plan, policy) -> bool`.

- [ ] **Step 1: Write the failing test**

```python
"""The precedence rule, tested directly - one setting across two layers is the
shape that had Polar running simulated for weeks, so the resolver is pure and
the winner is asserted by name."""
from astrodeck.config import AppConfig
from astrodeck.sequence.models import SequencePlan
from astrodeck.sequence.policy import resolve_policy


def test_none_inherits_the_rig_standard():
    cfg = AppConfig()
    cfg.standards.min_stars = 40
    p = resolve_policy(SequencePlan(), cfg)
    assert p.min_stars == 40
    assert p.sources["min_stars"] == "rig"


def test_an_explicit_plan_value_wins():
    cfg = AppConfig()
    cfg.standards.min_stars = 40
    p = resolve_policy(SequencePlan(min_stars=5), cfg)
    assert p.min_stars == 5
    assert p.sources["min_stars"] == "plan"


def test_an_explicit_zero_is_a_choice_not_an_absence():
    """0 means "gate off" for every threshold here. It must beat a configured
    rig standard, or an operator cannot turn a gate off for one night."""
    cfg = AppConfig()
    cfg.standards.min_stars = 40
    p = resolve_policy(SequencePlan(min_stars=0), cfg)
    assert p.min_stars == 0
    assert p.sources["min_stars"] == "plan"


def test_every_moved_field_resolves():
    cfg = AppConfig()
    p = resolve_policy(SequencePlan(), cfg)
    for f in ("dither_pixels", "recover_guiding", "cool_timeout_s",
              "hfr_reject_factor", "meridian_flip_warn_min",
              "apply_filter_offsets", "refocus_on_temp_delta_c", "min_stars",
              "max_guide_rms", "max_eccentricity", "max_consecutive_rejects",
              "max_consecutive_rejects_night"):
        assert hasattr(p, f), f
        assert p.sources[f] == "rig"
```

`test_an_explicit_zero_is_a_choice_not_an_absence` is the one that matters: the
whole scheme rests on `None` being the only "unset", so a stored `0` has to win.

- [ ] **Step 2: Run, expect ModuleNotFoundError**

- [ ] **Step 3: Write `policy.py`**

Frozen dataclass, one `_pick(plan_value, rig_value, name)` helper that records
the source, no config import in `models.py`.

- [ ] **Step 4: Make the twelve optional**

In `models.py`, each becomes `X | None = None` with a one-line comment saying
`None` = inherit. `quota_unbounded` takes the resolved policy for its two guard
reads and its signature becomes `quota_unbounded(plan, policy)`.

- [ ] **Step 5: Resolve at start, and convert the call sites**

`engine.py:472`, immediately after `self._cfg = config_store.cfg()`:
`self._policy = resolve_policy(plan, self._cfg)`. Then convert, in this order
(line numbers pre-edit): 959 `cool_timeout_s`; 2050 and 4345 `dither_pixels`;
2130-2135 the two reject guards; 4009 `apply_filter_offsets`; 4194
`recover_guiding`; 4246 and 4260 `refocus_on_temp_delta_c`; 4410
`hfr_reject_factor`; 4422-4427 `min_stars`; 4429-4436 `max_guide_rms`;
4438-4443 `max_eccentricity`; 902 `meridian_flip_warn_min` (drop the `getattr`
default — the resolver always answers).

`plan.guide` at 4194 stays a plan read; only `recover_guiding` moves.

- [ ] **Step 6: Update the five `quota_unbounded` callers**

`api/app.py:3805, 3986, 5450, 5727` and `resume_arm.py:241`. Each already has
config in reach.

- [ ] **Step 7: Full server suite**

```
cd server && ./.venv/Scripts/python.exe -m pytest -n auto -q
```

Expect failures ONLY where a test constructs a plan and asserts a concrete
default; each is a real decision about whether the test meant "the plan says" or
"the rig says". Read each before changing it.

- [ ] **Step 8: Sabotage**

Invert `_pick` so the rig always wins, and confirm
`test_an_explicit_plan_value_wins` and `test_an_explicit_zero_is_a_choice`
BOTH go red. Restore.

- [ ] **Step 9: Commit**

---

### Task A3: Existing plans stop pretending they chose the defaults

**Files:**
- Modify: `server/astrodeck/plans.py`
- Test: `server/tests/test_policy_migration.py` (create)

- [ ] **Step 1: Write the failing test**

One stored plan with `refocus_on_temp_delta_c=2.0` (deliberate) and
`dither_pixels=3.0` (the untouched default). After migration exactly one field
is still explicit, and a log line names the plan.

- [ ] **Step 2-4: Implement, green, commit**

A one-shot pass at load: for each of the twelve, if the stored value equals the
pre-migration model default, set it to `None`. Log one line per plan changed,
naming the fields nulled. Idempotent — a second run finds nothing.

The lossy case is intended and recorded in the spec: a deliberate `3.0` is
indistinguishable from an untouched `3.0`, because until now there was no way to
express the difference.

---

### Task A4: The report says what the night actually ran with

**Files:**
- Modify: `server/astrodeck/sequence/report.py`, `server/astrodeck/sequence/engine.py`
- Test: `server/tests/test_report_records_policy.py` (create)

- [ ] **Step 1: Write the failing test**

A run started with a configured rig standard and one plan override finishes with
a report carrying the twelve resolved values AND the source of each.

- [ ] **Step 2-4: Implement, green, commit**

This is the visibility half of the layering mitigation. Without it the report
can no longer answer "what gates were on that night", because the answer is no
longer in the plan alone.

---

### Task A5: Settings gets a Standards panel, and the plan editor says "inherited"

**Files:**
- Create: `ui/src/components/settings/StandardsPanel.tsx`
- Modify: `ui/src/components/settings/SettingsView.tsx`, `EscalationPanel.tsx`,
  `SafetyLimitsPanel.tsx`, `ui/src/types.ts`, `ui/src/views/SequenceView.tsx`
- Test: `ui/src/components/settings/__tests__/standardsPanel.test.ts` (create)

- [ ] **Step 1: Write the failing test** — the panel's field list matches the
  server's `StandardsConfig` (the same anti-drift shape as `nodeDefs.test.ts`,
  which is the test that caught the flows drift: parse the Python, compare).

- [ ] **Step 2-5: Implement, green, tsc, commit**

In `SequenceView`, each of the twelve renders its inherited value when `null`
with an explicit "inherited from Settings" affordance and a way to override, so
the losing layer is never the only one on screen.

---

### Task A6: Prove it end to end

- [ ] A flow-compiled plan picks up configured standards — the hole this stage
  exists to close. Drive `to_sequence_plan` then `resolve_policy` and assert the
  gates are the operator's, not the model's.
- [ ] Full server suite `-n auto`, full `npm test`, `npx tsc --noEmit`, `npx vite build`.
