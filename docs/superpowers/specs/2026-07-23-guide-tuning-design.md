# PRO-12 — Editable per-axis guide tuning + Dec-direction + BLC (pro)

Combined design spec + TDD implementation plan. One file.

Feature: expose guide-algorithm knobs that today stop short of the UI — a Dec
guide-direction selector (Auto / North / South / Off), a static backlash-comp
(BLC) seed-pulse field, and editable per-axis algorithm parameters
(aggressiveness / hysteresis / min-move / resist-switch aggression / lowpass
slope) — with the clamps that already exist in the engine.

---

## 0. TL;DR + the load-bearing surprise (read this first)

The brief's premise — "params that already exist in the Rust engine but are
orphaned **above** it" — is only **half** true, and the plan splits on that fault
line:

- **Dec guide-mode and BLC pulse ARE orphaned above the engine.** Both are real
  `EngineConfig` fields, both are parsed by the PyO3 `build_engine_config`, and
  both are already forwarded by `native.py::_build_engine_config`. The engine
  will honor them the instant a value reaches it. The only missing links are the
  four thin layers on top: the pydantic `GuideConfig`, its write-time validator,
  the `guide_algo_config()` passthrough, and the UI controls. **No Rust change.**
  This is **Tier 1** — the shippable core of PRO-12.

- **The per-axis algorithm parameters are orphaned INSIDE the engine.** The
  per-algorithm structs (`Hysteresis`, `ResistSwitch`, `Lowpass`, `Lowpass2`,
  `ZFilter`) each expose the params with clamping constructors — but
  `EngineConfig` has **no** fields to carry them, and `make_algo()` builds every
  algorithm at `::default()`, discarding anything but the kind. Exposing these
  needs an engine change (new `EngineConfig` fields + `make_algo` rewire + PyO3
  parse) **before** any Python/UI plumbing can matter. This is **Tier 2**.

Recommendation: ship Tier 1 first (2 of the 3 brief items, zero Rust risk, ~half
a day), then Tier 2 (the Rust-touching per-axis params). Both are designed below;
`ready: true`. The tiering is called out again in Open Decisions.

---

## 1. Design

### 1.1 Goal

Let a pro user tune the native (Rust-engine) autoguider from the Guide page
without editing config files or rebuilding the wheel:

1. **Dec guide direction** — Auto / North / South / Off (skip Dec guiding, or
   restrict it to one direction to sidestep a sticky Dec axis).
2. **Static BLC pulse (ms)** — a fixed seed pulse added on a Dec direction
   reversal (dossier §10.1; `0` = disabled, the shipped default).
3. **Per-axis algorithm parameters** — the aggressiveness / hysteresis /
   min-move / slope numbers currently shown read-only, made editable, clamped to
   the same bounds the engine enforces.

All three apply on the **next `start_guiding`** (the guider reads config at
construction — same contract the existing algorithm-KIND select already documents
at `GuideView.tsx:429`). No hot-swap of a running guider.

### 1.2 Current-state seams (every claim is a file:line I read)

**UI — what the drawer renders today**

- `ui/src/views/GuideView.tsx:385-499` `GuideSettingsDrawer` — renders only two
  `<select>`s (RA algorithm `:451-460`, Dec algorithm `:462-471`) plus an
  `AlgoParams` read-out.
- `GuideView.tsx:501-510` `AlgoParams` — maps `GUIDE_ALGORITHM_DEFAULTS[kind]` to
  static `<span>`s. **Read-only. No inputs.**
- `GuideView.tsx:396-405` `load()` — GETs `/api/guide/settings`, reads **only**
  `ra_algorithm` / `dec_algorithm`.
- `GuideView.tsx:413-435` `save()` — builds+validates a full `GuideSettings`
  (`:418-424`) but PUTs **only** `{ ra_algorithm, dec_algorithm }` (`:425-428`);
  `blcPulseMs` is hardcoded `0` at `:424` with a comment ("keeps its disabled
  default until a settings editor surfaces it").

**UI lib — the client shape + clamps (already richer than the UI uses)**

- `ui/src/lib/guideSettings.ts:98-108` `GuideSettings` — already carries
  `blcPulseMs`. **No `decGuideMode` field.**
- `guideSettings.ts:113-125` `defaultGuideSettings()` — seeds `blcPulseMs: 0`.
- `guideSettings.ts:129-145` clamps: `MAX_AGGRESSION 2.0`, `MAX_HYSTERESIS 0.99`,
  `MAX_BLC_PULSE_MS 10000`, `clampBlcPulse()` (non-negative integer ms).
- `guideSettings.ts:147-177` `validateGuideSettings()` — clamps aggression,
  hysteresis, every param `>= 0`, and BLC. **Already correct; just under-used.**
- Existing tests: `ui/src/lib/__tests__/guideSettings.test.ts` (12 tests incl.
  BLC clamp `:56-72`) — extend, don't replace.

**API endpoint**

- `server/astrodeck/api/app.py:3000-3006` `GET /api/guide/settings` — returns
  `config_store.cfg().guide.model_dump()`. Grows automatically as `GuideConfig`
  grows.
- `app.py:3008-3021` `PUT /api/guide/settings` — body is `GuideConfig`, validated
  via `config_store.set_guide` (ValueError→422 `:3018-3019`), publishes the
  `config` bus event `:3020`. Grows automatically as `GuideConfig` grows.

**Server config**

- `server/astrodeck/config.py:316-320` `RA_GUIDE_ALGORITHMS` /
  `DEC_GUIDE_ALGORITHMS` vocabularies.
- `config.py:323-325` `GuideConfig` — **only** `ra_algorithm` + `dec_algorithm`.
  **No `dec_guide_mode`, no `blc_pulse_ms`, no per-axis params.**
- `config.py:775-790` `set_guide()` — validates the two algorithm kinds only.

**Server guide wiring**

- `server/astrodeck/guide/native.py:112-123` `guide_algo_config()` — returns
  **only** `{"ra_algorithm", "dec_algorithm"}`. This is the single chokepoint
  that feeds persisted settings into the guider.
- `native.py:626-661` `_build_engine_config()` — line `637` already forwards
  `dec_guide_mode` (`cfg.get("dec_guide_mode", "auto")`); line `645` already
  lists `blc_pulse_ms` in the passthrough allowlist. **Per-axis params are NOT in
  the allowlist and are NOT read.**
- `native_backend.py:157-161` and `sim_backend.py:127-133` — both spread
  `**guide_algo_config()` into the `NativeGuider` config dict. Whatever
  `guide_algo_config()` returns reaches `_build_engine_config`.

**Rust engine (the Tier-2 fault line)**

- `native/crates/astrodeck-native/src/lib.rs:988-1043` `build_engine_config` —
  parses `dec_guide_mode` (`:1038-1040`, via `parse_dec_mode`) and `blc_pulse_ms`
  (`:1013`). **Does NOT parse any per-axis algorithm param.**
- `lib.rs:875-885` `parse_dec_mode` — accepts `auto` / `off` / `north` / `south`.
- `native/crates/astro-guide/src/engine.rs:125-156` `EngineConfig` — fields:
  `cal`, `find`, `max_ra_duration_ms`, `max_dec_duration_ms`, `dec_guide_mode`,
  `ra_algorithm`, `dec_algorithm`, `blc_pulse_ms`, `max_stars`. **No per-axis
  algorithm-param fields.**
- `engine.rs:369-384` `make_algo(kind, is_ra)` — `Box::new(Hysteresis::default())`
  etc. **Every algorithm is constructed at its default; no param passthrough.**
- The clamps the brief refers to DO exist, in each algorithm's `new()`:
  `algorithms/hysteresis.rs:57-73` (min_move `<0`→default, hysteresis clamp
  `0..=0.99`, aggression `0..=2.0`→default), `resist_switch.rs:63-77`
  (`aggression 0..=1`), `lowpass.rs:164-178` (Lowpass), `lowpass.rs:273+`
  (Lowpass2), `zfilter.rs:392-418` (ZFilter). Tier 2 reuses these constructors so
  the clamps are honored for free.
- `calibration.rs:130-140` `DecMode` = `Off | Auto | North | South`.

### 1.3 Concrete approach

#### Tier 1 — Dec direction + BLC (no Rust)

**Data shapes.**

`GuideConfig` (config.py) gains two fields with the exact engine defaults:

```python
dec_guide_mode: str = "auto"     # DecMode default (engine.rs:168)
blc_pulse_ms: int = 0            # EngineConfig.blc_pulse_ms default (engine.rs:171)
```

New vocabulary constant beside the algorithm lists:

```python
DEC_GUIDE_MODES: tuple[str, ...] = ("auto", "north", "south", "off")
```

`set_guide()` validates `dec_guide_mode in DEC_GUIDE_MODES` (ValueError→422, same
shape as the algorithm checks) and clamps `blc_pulse_ms` to `[0, 10000]` integer
ms (mirrors the UI's `clampBlcPulse`, so a value that skips the UI still can't
reach the engine out of range).

`guide_algo_config()` (native.py) returns the two new keys too:

```python
return {"ra_algorithm": g.ra_algorithm, "dec_algorithm": g.dec_algorithm,
        "dec_guide_mode": g.dec_guide_mode, "blc_pulse_ms": g.blc_pulse_ms}
```

That is the **entire** server-to-engine wiring — `_build_engine_config` already
forwards both keys (native.py:637, :645). No `_build_engine_config` edit.

**Client shape.** `guideSettings.ts` gains a `decGuideMode` field + a
vocabulary + a validator that snaps an unknown value back to `"auto"`:

```ts
export type DecGuideMode = "auto" | "north" | "south" | "off";
export const DEC_GUIDE_MODES: readonly { value: DecGuideMode; label: string }[] = [
  { value: "auto",  label: "Auto (both directions)" },
  { value: "north", label: "North only" },
  { value: "south", label: "South only" },
  { value: "off",   label: "Off (no Dec guiding)" },
];
```

`GuideSettings.decGuideMode` added; `defaultGuideSettings()` seeds `"auto"`;
`validateGuideSettings()` coerces an invalid mode to `"auto"`.

**Copy table (UI).**

| Control        | Label                    | Options / placeholder                          |
|----------------|--------------------------|------------------------------------------------|
| Dec direction  | `Dec guide direction`    | Auto (both directions) / North only / South only / Off (no Dec guiding) |
| BLC pulse      | `Dec backlash pulse (ms)`| placeholder `0` — "0 disables backlash comp"   |

**Behavior.** `load()` reads the two new fields off the GET payload (guarded —
absent ⇒ keep the default, older payloads load fine); `save()` PUTs them
alongside the algorithm kinds after `validateGuideSettings()` clamps.

#### Tier 2 — editable per-axis params (Rust + full stack)

**Rust.** Add an `AxisAlgoParams` carrier to `EngineConfig`:

```rust
#[derive(Debug, Clone, Copy, PartialEq, Default)]
pub struct AxisAlgoParams {
    pub min_move: Option<f64>,
    pub aggression: Option<f64>,     // hysteresis + resist_switch
    pub hysteresis: Option<f64>,     // hysteresis
    pub slope_weight: Option<f64>,   // lowpass
    pub aggressiveness: Option<f64>, // lowpass2
    pub exp_factor: Option<f64>,     // z_filter
}
```

`EngineConfig` gains `ra_params: AxisAlgoParams`, `dec_params: AxisAlgoParams`
(both `Default` = all-`None`, so an old caller is byte-for-byte unchanged).
`make_algo(kind, is_ra, p: &AxisAlgoParams)` reads only the fields the chosen
algorithm consumes, falling back to the algorithm's own default constant when
`None`, and constructs via the existing **clamping** `new()` (so no new clamp
logic — the engine's contract is preserved):

```rust
AlgoKind::Hysteresis => Box::new(Hysteresis::new(
    p.hysteresis.unwrap_or(DEFAULT_HYSTERESIS),
    p.aggression.unwrap_or(DEFAULT_AGGRESSION),
    p.min_move.unwrap_or(DEFAULT_MIN_MOVE))),
```

`build_engine_config` (lib.rs) parses `ra_params`/`dec_params` sub-dicts.

**Python.** `GuideConfig` gains nested `ra_params` / `dec_params` (a small
`GuideAxisParams` BaseModel, all `Optional[float]`); `set_guide` clamps them to
the same bounds; `guide_algo_config()` forwards them; `_build_engine_config`
allowlist adds `ra_params` / `dec_params`.

**UI.** `AlgoParams` becomes an editable input grid (reuse the honest-disabled
idiom for the read-only viewer), feeding `save()`'s already-built
`validateGuideSettings()` params (the client clamps already exist).

### 1.4 Placement

All UI lives inside the **existing** `GuideSettingsDrawer` (`GuideView.tsx:385`),
under the Dec-algorithm select — no new panel. Rename the panel title from
"Guide Algorithm" to **"Guide Tuning"** (`:438`) once the drawer holds more than
the kind. The drawer stays lazy-loaded on first Edit (`:407-411`), keeping the
Guide page's first paint cheap. NOV-7 also edits this file; the implementer
sequences PRO-12 **after** NOV-7 and re-reads the current drawer before editing.

---

## 2. Global Constraints (verbatim, binding)

- **Privacy.** The real coordinates `<REDACTED-LAT>` / `<REDACTED-LON>` and the label
  "<REDACTED-SITE-LABEL>" must NEVER appear in code, tests, or docs. Site default is
  "My Observatory" / `0.0`. (This feature touches no coordinates; do not
  introduce any.)
- **Never `git add -A`.** Stage named paths only.
- **UI typecheck gate:** `cd ui && npx tsc -b`.
- **NO jsdom.** Pure logic is tested via `npx tsx` inline-assert, following the
  idiom in `ui/src/lib/__tests__/eta.test.ts` /
  `ui/src/components/__tests__/healthStrip.test.ts`. Thin render is verified by
  the typechecker only.
- **Backend tests:** `server/.venv/Scripts/pytest.exe` run from the repo root,
  `-n0` (single worker).
- **Rust:** `native/` has its own cargo tests — run `cargo test` **in the crate**
  (`native/crates/astro-guide` and/or `native/crates/astrodeck-native`).
- **Client toasts:** `useStore.getState().enqueueToast` (this view already
  threads a `showToast`/`onToast` prop — reuse it).
- **Honest-disabled idiom (§11.8):** dim token + lock glyph + `aria-disabled` +
  `title`; NEVER the native `disabled` attribute for the honest-disabled read-only
  state. (Note: the existing drawer uses native `disabled` on its selects at
  `:453`/`:464`; keep parity with the surrounding code for new controls unless the
  reviewer asks to migrate — do not silently regress the pattern either way.)
- **Do not disrupt astrotown.**

---

## 3. TDD Plan

Tasks are ordered so each ships green independently. Tier 1 (T1–T4) is the
recommended first PR; Tier 2 (T5–T7) follows.

### TIER 1 — Dec direction + BLC (no Rust)

---

#### T1 — `GuideConfig`: add `dec_guide_mode` + `blc_pulse_ms` + validation

**Impl tier: Sonnet** (mechanical pydantic + a vocabulary check mirroring the two
existing algorithm checks).

**Files**
- Modify: `server/astrodeck/config.py` (add `DEC_GUIDE_MODES`, two `GuideConfig`
  fields, extend `set_guide`).
- Test: `server/tests/test_native_guide_surface.py` (extend — it already exercises
  the guide config surface).

**Interfaces**
```python
DEC_GUIDE_MODES: tuple[str, ...] = ("auto", "north", "south", "off")

class GuideConfig(BaseModel):
    ra_algorithm: str = "hysteresis"
    dec_algorithm: str = "resist_switch"
    dec_guide_mode: str = "auto"           # DecMode default (engine.rs:168)
    blc_pulse_ms: int = 0                  # EngineConfig default (engine.rs:171)

# in ConfigStore.set_guide, after the two algorithm checks:
#   if guide.dec_guide_mode not in DEC_GUIDE_MODES: raise ValueError(...)
#   guide.blc_pulse_ms = max(0, min(10000, int(guide.blc_pulse_ms)))
```

**Steps**
1. Add `DEC_GUIDE_MODES` beside `DEC_GUIDE_ALGORITHMS` (config.py:320).
2. Add the two fields to `GuideConfig` (config.py:323-325) with the exact engine
   defaults and a cite comment.
3. In `set_guide` (config.py:775-790) add the `dec_guide_mode` membership check
   (ValueError with the valid list, same shape as the algorithm checks) and clamp
   `blc_pulse_ms` in place before `cfg.guide = guide`.
4. Add a test asserting: default `GuideConfig().dec_guide_mode == "auto"` and
   `.blc_pulse_ms == 0`; `set_guide` rejects `dec_guide_mode="sideways"`
   (ValueError); `set_guide` clamps `blc_pulse_ms=99999` → `10000` and
   `blc_pulse_ms=-5` → `0`; a valid `"north"` / `250` round-trips through
   `cfg().guide.model_dump()`.

**Commands / expected**
```
server/.venv/Scripts/pytest.exe server/tests/test_native_guide_surface.py -n0 -q
# expected: all pass (existing + the ~4 new assertions)
```

---

#### T2 — `guide_algo_config()`: forward the two new keys

**Impl tier: Sonnet** (a two-key dict extension at the single chokepoint).

**Files**
- Modify: `server/astrodeck/guide/native.py` (`guide_algo_config`, :112-123).
- Test: `server/tests/test_native_guide_surface.py` (extend).

**Interfaces**
```python
def guide_algo_config() -> dict:
    ...
    g = config_store.cfg().guide
    return {"ra_algorithm": g.ra_algorithm, "dec_algorithm": g.dec_algorithm,
            "dec_guide_mode": g.dec_guide_mode, "blc_pulse_ms": g.blc_pulse_ms}
```

**Steps**
1. Extend the returned dict with `dec_guide_mode` + `blc_pulse_ms`. Keep the
   defensive `except → {}` intact (an old config without the block still yields
   `{}` → `_build_engine_config` defaults).
2. Test that after `set_guide(GuideConfig(dec_guide_mode="off", blc_pulse_ms=300))`,
   `guide_algo_config()` returns those values; and that with a torn-down config
   store it returns `{}` (defensive path).
3. **Wiring sanity (no new test needed — reasoned):** `_build_engine_config`
   (native.py:637, :645) already reads both keys, so `guide_algo_config()`'s
   output now reaches `EngineConfig`. Optionally extend an existing
   `_build_engine_config` unit test to assert `dec_guide_mode`/`blc_pulse_ms`
   appear in the built dict when present in `cfg`.

**Commands / expected**
```
server/.venv/Scripts/pytest.exe server/tests/test_native_guide_surface.py -n0 -q
# expected: all pass
```

---

#### T3 — `guideSettings.ts`: add `decGuideMode` + vocabulary + validation

**Impl tier: Sonnet** (pure lib; the BLC clamp already exists).

**Files**
- Modify: `ui/src/lib/guideSettings.ts`.
- Test: `ui/src/lib/__tests__/guideSettings.test.ts` (extend the existing suite).

**Interfaces**
```ts
export type DecGuideMode = "auto" | "north" | "south" | "off";
export interface DecGuideModeOption { value: DecGuideMode; label: string; }
export const DEC_GUIDE_MODES: readonly DecGuideModeOption[] = [
  { value: "auto",  label: "Auto (both directions)" },
  { value: "north", label: "North only" },
  { value: "south", label: "South only" },
  { value: "off",   label: "Off (no Dec guiding)" },
];
export function isValidDecGuideMode(m: string): m is DecGuideMode { ... }

export interface GuideSettings {
  ra: AxisGuideSettings;
  dec: AxisGuideSettings;
  decGuideMode: DecGuideMode;   // NEW
  blcPulseMs: number;
}
// defaultGuideSettings(): decGuideMode: "auto"
// validateGuideSettings(): decGuideMode = isValidDecGuideMode(s.decGuideMode) ? s.decGuideMode : "auto"
```

**Steps**
1. Add `DecGuideMode` type + `DEC_GUIDE_MODES` + `isValidDecGuideMode`.
2. Add `decGuideMode` to `GuideSettings`; seed `"auto"` in `defaultGuideSettings`;
   coerce-to-`"auto"` in `validateGuideSettings`.
3. Extend `guideSettings.test.ts`: `defaultGuideSettings().decGuideMode === "auto"`;
   `validateGuideSettings` coerces `"bogus"` → `"auto"` and preserves `"south"`.

**Commands / expected**
```
cd ui && npx tsx src/lib/__tests__/guideSettings.test.ts
# expected: "guideSettings.test.ts: <n> passed, 0 failed"  (n = 12 existing + ~2 new)
cd ui && npx tsc -b
# expected: clean (no errors)
```

---

#### T4 — `GuideSettingsDrawer`: render Dec-direction select + BLC field, wire load/save

**Impl tier: Sonnet** (thin render + two extra fields on the existing
load/save; verified by the typechecker, no jsdom).

**Files**
- Modify: `ui/src/views/GuideView.tsx` (`GuideSettingsDrawer` `:385-499`).

**Interfaces (component-local state + payloads)**
```ts
// new local state
const [decMode, setDecMode] = useState<DecGuideMode>(defaultGuideSettings().decGuideMode);
const [blcMs, setBlcMs] = useState<string>("0");   // text input, parsed on save

// load() GET payload widened:
const s = await api.get<{
  ra_algorithm: string; dec_algorithm: string;
  dec_guide_mode?: string; blc_pulse_ms?: number;
}>("/api/guide/settings");
if (isValidDecGuideMode(s.dec_guide_mode ?? "")) setDecMode(s.dec_guide_mode as DecGuideMode);
if (typeof s.blc_pulse_ms === "number") setBlcMs(String(s.blc_pulse_ms));

// save() builds the full settings, then PUTs the widened body:
const v = validateGuideSettings({
  ra:  { algorithm: ra,  params: { ...GUIDE_ALGORITHM_DEFAULTS[ra] } },
  dec: { algorithm: dec, params: { ...GUIDE_ALGORITHM_DEFAULTS[dec] } },
  decGuideMode: decMode,
  blcPulseMs: Number(blcMs),   // NaN/blank → clampBlcPulse floors to 0
});
await api.put("/api/guide/settings", {
  ra_algorithm: v.ra.algorithm,
  dec_algorithm: v.dec.algorithm,
  dec_guide_mode: v.decGuideMode,
  blc_pulse_ms: v.blcPulseMs,
});
```

**Steps**
1. Import `DEC_GUIDE_MODES`, `isValidDecGuideMode`, `type DecGuideMode` from
   `../lib/guideSettings`.
2. Add the two `useState`s. Extend `load()` to read the two new (optional) fields.
3. Under the Dec-algorithm `<label>` (`:471`), add a Dec-direction `<select>`
   mapping `DEC_GUIDE_MODES`, and a `Dec backlash pulse (ms)` numeric input
   (`inputMode="numeric"`, `placeholder="0"`). Both gated by `!canGuide || busy`
   exactly like the selects at `:453`/`:464`.
4. Replace the hardcoded `blcPulseMs: 0` (`:424`) and add `decGuideMode` to the
   `validateGuideSettings` call; extend the PUT body with the two keys.
5. Update the footnote (`:491-494`) to note params are still the default set
   (until Tier 2) but Dec direction + BLC now persist and apply on next start.
6. Retitle the panel "Guide Tuning" (`:438`).

**Commands / expected**
```
cd ui && npx tsc -b
# expected: clean. (Render correctness is the typechecker's job — no jsdom.)
```

Manual smoke (optional, not a gate): open Guide → Edit, pick "North only", set
BLC 250, Save → toast "saved — applies on next start"; reopen → values persist.

---

### TIER 2 — editable per-axis params (Rust + full stack)

---

#### T5 — Rust: carry per-axis params through `EngineConfig` → `make_algo`, parse in PyO3

**Impl tier: Opus.** Justification: this is the load-bearing numeric/correctness
change — it alters how the engine *constructs its control algorithms*. Getting
the `Option`-fallback-to-default-constant and the clamp-reuse right (and NOT
regressing the "every algorithm constructs at its dossier §15 default when
unspecified" invariant, on which dozens of existing golden tests depend) is
genuinely subtle. A mechanical pass risks silently shifting the default guide
behavior.

**Files**
- Modify: `native/crates/astro-guide/src/engine.rs` (`AxisAlgoParams` struct,
  two `EngineConfig` fields, `make_algo` signature + bodies + its call sites).
- Modify: `native/crates/astrodeck-native/src/lib.rs` (`build_engine_config`:
  parse `ra_params`/`dec_params` sub-dicts).
- Test: `native/crates/astro-guide/src/engine.rs` `#[cfg(test)]` (new unit tests)
  and/or a new `native/crates/astro-guide/tests/axis_params.rs`.
- Test: `native/crates/astrodeck-native` — extend an existing PyO3 config test if
  present, else assert via the Python surface in T6.

**Interfaces**
```rust
#[derive(Debug, Clone, Copy, PartialEq, Default)]
pub struct AxisAlgoParams {
    pub min_move: Option<f64>,
    pub aggression: Option<f64>,
    pub hysteresis: Option<f64>,
    pub slope_weight: Option<f64>,
    pub aggressiveness: Option<f64>,
    pub exp_factor: Option<f64>,
}
// EngineConfig gains:  pub ra_params: AxisAlgoParams,  pub dec_params: AxisAlgoParams,
//   (Default = AxisAlgoParams::default() = all None)
fn make_algo(kind: AlgoKind, is_ra: bool, p: &AxisAlgoParams) -> Box<dyn GuideAlgorithm>;
```

**Steps**
1. Add `AxisAlgoParams` (derive `Default`), add the two fields to `EngineConfig`
   and its `Default` impl (both `AxisAlgoParams::default()`).
2. Change `make_algo` to take `&AxisAlgoParams`; for each arm, `unwrap_or` the
   algorithm's existing default constant and call the clamping `new()`
   (Hysteresis/ResistSwitch/Lowpass/Lowpass2/ZFilter). PPEC ignores params
   (unchanged — GP has its own `GpParams`). Update both call sites (RA passes
   `&c.ra_params`, Dec `&c.dec_params`).
3. In `build_engine_config` (lib.rs), after the algorithm parse, read optional
   `ra_params` / `dec_params` `PyDict`s into `AxisAlgoParams` (each key optional
   `f64`).
4. Tests: (a) all-`None` params ⇒ each algorithm byte-identical to `::default()`
   (pin the no-regression invariant); (b) a set `aggression`/`hysteresis`/etc.
   reaches the constructed algorithm; (c) an out-of-range value is clamped by the
   reused `new()` (e.g. hysteresis `1.5` → `0.99`, aggression `5.0` → default).

**Commands / expected**
```
cd native/crates/astro-guide && cargo test
# expected: ok. all existing golden tests still pass (invariant (a)) + new tests
cd native/crates/astrodeck-native && cargo test
# expected: ok
```

> Deploy note: Tier 2 requires rebuilding + reinstalling the `astrodeck_native`
> wheel on any target box before the Python/UI layers below have any effect. Do
> NOT disrupt astrotown — build/test locally; the box upgrade is a separate,
> deliberate step.

---

#### T6 — Python: nested per-axis params in `GuideConfig`, clamp, forward

**Impl tier: Sonnet** (pydantic nesting + dict passthrough mirroring T1/T2).

**Files**
- Modify: `server/astrodeck/config.py` (`GuideAxisParams` model, two `GuideConfig`
  fields, clamp in `set_guide`).
- Modify: `server/astrodeck/guide/native.py` (`guide_algo_config` forwards
  `ra_params`/`dec_params`; `_build_engine_config` allowlist adds the two keys).
- Test: `server/tests/test_native_guide_surface.py` (extend).

**Interfaces**
```python
class GuideAxisParams(BaseModel):
    min_move: float | None = None
    aggression: float | None = None
    hysteresis: float | None = None
    slope_weight: float | None = None
    aggressiveness: float | None = None
    exp_factor: float | None = None

class GuideConfig(BaseModel):
    ...
    ra_params: GuideAxisParams = GuideAxisParams()
    dec_params: GuideAxisParams = GuideAxisParams()
```

**Steps**
1. Add `GuideAxisParams`; add `ra_params`/`dec_params` to `GuideConfig`.
2. In `set_guide`, clamp each present param to the engine bounds
   (aggression `[0,2]`, hysteresis `[0,0.99]`, others `>= 0`) — defense in depth;
   the Rust `new()` clamps again.
3. `guide_algo_config()` adds `ra_params`/`dec_params` (as
   `g.ra_params.model_dump(exclude_none=True)` so absent params stay engine
   defaults). `_build_engine_config` allowlist (native.py:643-646) gains
   `"ra_params"`, `"dec_params"`.
4. Tests: round-trip a `hysteresis=0.3` RA param; assert `set_guide` clamps
   `aggression=9` → `2`; assert `guide_algo_config()` emits the sub-dicts.

**Commands / expected**
```
server/.venv/Scripts/pytest.exe server/tests/test_native_guide_surface.py -n0 -q
# expected: all pass
```

---

#### T7 — UI: make `AlgoParams` editable, wire into save

**Impl tier: Sonnet** (thin render; client clamps already exist in `validateGuideSettings`).

**Files**
- Modify: `ui/src/views/GuideView.tsx` (`AlgoParams` `:501-510` → editable;
  `save()` sends per-axis params).
- Test: `ui/src/lib/__tests__/guideSettings.test.ts` already covers the clamp math
  (no new lib logic) — no new test file; typecheck is the render gate.

**Interfaces**
```ts
// AlgoParams becomes controlled:
function AlgoParams({ kind, params, onChange, disabled }: {
  kind: GuideAlgorithmKind;
  params: GuideAlgorithmParamDefaults;
  onChange: (next: GuideAlgorithmParamDefaults) => void;
  disabled: boolean;
}) { /* number inputs per Object.entries(params); onChange merges the edited key */ }

// save() sends the edited params (already clamped by validateGuideSettings):
await api.put("/api/guide/settings", {
  ra_algorithm: v.ra.algorithm,
  dec_algorithm: v.dec.algorithm,
  ra_params:  toSnake(v.ra.params),   // {minMove→min_move, slopeWeight→slope_weight, ...}
  dec_params: toSnake(v.dec.params),
  dec_guide_mode: v.decGuideMode,
  blc_pulse_ms: v.blcPulseMs,
});
```

**Steps**
1. Lift RA/Dec params into drawer state (seed from `GUIDE_ALGORITHM_DEFAULTS[kind]`
   on kind change). Render each as a numeric input via editable `AlgoParams`.
2. Add a `toSnake` mapping helper (camelCase client keys → engine snake_case) —
   pure, add a tiny tsx test for it in `guideSettings.test.ts`.
3. Wire the edited params through the existing `validateGuideSettings()` call in
   `save()` (clamps for free) and into the PUT body.
4. For the read-only viewer, apply the honest-disabled idiom (§11.8) on the inputs.

**Commands / expected**
```
cd ui && npx tsx src/lib/__tests__/guideSettings.test.ts   # toSnake + existing, 0 failed
cd ui && npx tsc -b                                        # clean
```

---

## 4. Open decisions

1. **Ship Tier 1 alone first, or bundle all three?**
   **Rec:** Ship Tier 1 (T1–T4) as PRO-12 v1 — it delivers Dec-direction + BLC
   with zero Rust risk and no wheel rebuild, satisfying 2 of the 3 brief items.
   Land Tier 2 (T5–T7) as PRO-12 v2 behind the wheel rebuild. Rationale: the
   brief's "orphaned above the engine" premise only holds for Tier 1; Tier 2 is a
   genuine engine change with a deploy dependency.

2. **`EngineConfig` param carrier shape: `Option<f64>` struct vs flat fields.**
   **Rec:** `AxisAlgoParams { min_move: Option<f64>, ... }` with `Default` =
   all-`None`. `None` ⇒ the algorithm's own default constant, which preserves the
   "constructs at dossier §15 default" invariant that existing golden tests pin.
   Flat non-optional fields would force a default value into `EngineConfig` and
   risk shifting behavior for every current caller.

3. **Where do the per-axis clamps live — Rust, Python, or client?**
   **Rec:** All three, defense in depth, but the **Rust `new()` constructors are
   authoritative** (they already clamp). Python `set_guide` and client
   `validateGuideSettings` clamp too so a bad value is rejected/snapped before it
   ever reaches the wheel, but neither invents new bounds — both mirror the engine
   constants (aggression 2.0, hysteresis 0.99).

4. **BLC clamp ceiling (10000 ms).** The client already uses `MAX_BLC_PULSE_MS =
   10000` and the engine "raises its Dec ceiling to admit the seed." **Rec:** keep
   10000 as the shared cap; encode it once server-side in `set_guide` to match the
   client, rather than deriving a new number.

5. **Honest-disabled vs native `disabled` for the new controls.** The existing
   drawer selects use native `disabled` (`:453`/`:464`). **Rec:** match the
   surrounding code for the new Tier-1 selects/inputs to avoid a mixed pattern in
   one panel; if the reviewer wants §11.8 honest-disabled, migrate the whole
   drawer in a follow-up rather than half of it now.

6. **NOV-7 sequencing.** Both features edit `GuideView.tsx`. **Rec:** implementer
   lands NOV-7 first, then re-reads the current `GuideSettingsDrawer` before
   applying T4/T7 (line numbers here are against the pre-NOV-7 file).
