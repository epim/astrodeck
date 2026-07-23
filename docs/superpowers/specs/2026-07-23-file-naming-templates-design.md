# PRO-11 — Configurable file-naming + folder templates

Combined design spec + TDD implementation plan. One deliverable: a NINA-style
`$$TOKEN$$` template engine that drives both the capture **folder structure** and
**filename**, a persisted **per-target frame counter**, a config field, and a
Settings panel — with the current fixed layout preserved **byte-for-byte** as the
default template.

---

## 1. Design

### 1.1 Goal

Replace the single fixed path builder with a token template so a user can fold
captures by target / night / filter / frame-type and shape the filename, e.g.

```
$$TARGET$$/$$NIGHT$$/$$FRAMETYPE$$/$$FILTER$$_$$DATE$$_$$TIME$$_$$FRAMENR$$
```

The **token-substitution engine** (template + fields → relative path, with
sanitization) is a **pure, pytest-tested core** with no I/O. The stored **default
template reproduces today's layout byte-for-byte** (pinned by a golden test). A
new **persisted per-target counter** replaces today's process-global counter.

### 1.2 Current-state seams (real file:line, all read)

- **`server/astrodeck/hub.py:1680-1688` — `_capture_path` (the sole fixed builder).**
  ```python
  def _capture_path(self, target, frame_type, filter_name=""):
      safe = "".join(c if c.isalnum() or c in "-_ " else "_" for c in target).strip() or "untargeted"
      stamp = time.strftime("%Y-%m-%d_%H%M%S")
      self._frame_counter = getattr(self, "_frame_counter", 0) + 1
      ftok = "".join(c if c.isalnum() or c in "-_" else "_" for c in (filter_name or "")).strip("_")
      parts = [frame_type, safe] + ([ftok] if ftok else []) + [stamp, f"{self._frame_counter:04d}"]
      return CAPTURE_DIR / safe / ("_".join(parts) + ".fits")
  ```
  Layout today: folder `CAPTURE_DIR/<safe_target>/`, file
  `<FrameType>_<safe_target>[_<Filter>]_<YYYY-MM-DD_HHMMSS>_<NNNN>.fits`. Two
  **different** sanitizers: target keeps spaces (`c in "-_ "`, `.strip()`), filter
  strips underscores (`c in "-_"`, `.strip("_")`). The filter token is
  **conditionally** included — that conditional inclusion, not any separator
  collapse, is what avoids a double `_` when there is no filter.
- **`hub.py:1683` — the counter.** `self._frame_counter = getattr(self, "_frame_counter", 0) + 1`
  is **process-global** (shared across every target) and **not persisted** (resets
  on restart). The brief's "persisted per-target counter" is genuinely new.
- **`hub.py:1412` — the sole caller**, inside `capture()`:
  `local_save_path = self._capture_path(target or "untargeted", frame_type, filt)`.
  Only runs on a local (sim/Alpaca) save (`save and frame.rendered_bytes is None`).
  `filt` is resolved just above (`fw.filter_names[await fw.get_position()]`), and
  `target`/`frame_type` arrive as `capture()` args (`hub.py:1381-1383`).
- **`hub.py:94-95` — `CAPTURE_DIR`** (env `ASTRODECK_CAPTURE_DIR`, else in-tree
  `captures/`). Referenced as a module global that every test monkeypatches
  (`monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path)`), so any counter file
  under it auto-isolates in tests.
- **`hub.py:21` / `:14` / `:16`** — `from .config import config_store, ...`, `import time`,
  `from pathlib import Path` are already top-level. `config_store` is monkeypatched
  by tests (`test_hub_capture_precession.py:326`).
- **`hub.py:168-220` — `Hub.__init__`.** State fields live here; the counter is
  **file-backed** (survives restart), so no new `__init__` field is required.
- **`server/astrodeck/config.py:428-455` — `AppConfig`.** Every subsystem appends a
  typed sub-model with a `default_factory` (survey/weather/guide/rotator …). Old
  files without the key deserialize fine (`_load` tolerates missing keys, `:588-591`).
- **`config.py:348-353` — `SurveyConfig`** and **`:829-833` — `set_survey`**: the
  simplest model+setter idiom to mirror. **`config.py:763-781` — `set_providers`**:
  the write-time-validation-raises-`ValueError`(→422) idiom.
- **`config.py:462-487` — filter store** (`load_filter_config`/`save_filter_config`):
  a small side JSON keyed by an id, written via `write_json_atomic`. The counter
  store mirrors this pattern (but lives under `CAPTURE_DIR`, see §1.4).
- **`server/astrodeck/persist.py:142` `read_json_or` / `:90` `write_json_atomic`** —
  the atomic-write + best-effort-`.bak` helpers the counter store reuses.
- **`server/astrodeck/api/app.py:970-977` — `set_survey_config` route** (POST
  `/api/config/survey`, `@declare(CAP_CONFIG_SITE_OPTICS)`, `to_thread(set_...)`,
  `bus.publish("config", config=redacted(cfg))`, returns `_config_payload`). The
  naming route is a carbon copy. Config models imported at **`app.py:53-56`**;
  `CAP_CONFIG_SITE_OPTICS` imported at **`app.py:29`**.
- **`ui/src/components/settings/SettingsView.tsx:161-168`** — the Connect tab's
  left column (`DriversPanel` / `SitePanel` / `WeatherPanel` / `SkyAtlasPanel`);
  `NamingPanel` mounts here.
- **`ui/src/components/settings/SkyAtlasPanel.tsx`** — the panel idiom: `useConfig()`,
  `useCan("config.site_optics")` read-only gating, `useStore.getState().loadConfig()`
  after save, error surfacing, lock-hint footer.
- **`ui/src/components/ui.tsx:18` `Panel`, `:55` `Field`** — layout primitives.
  **`ui/src/components/settings/AlertsPanel.tsx:63-93` — `ActionButton`**: the
  §11.8 honest-disabled idiom (dim + lock glyph + `aria-disabled` + `title`, click
  swallowed — never the native `disabled` attribute).
- **`ui/src/types.ts:518-570` `AppConfig`, `:669-672` `SurveyConfig`** — where the
  mirror types live. **`ui/src/api/backends.ts:393-395` `setSurveyConfig`** — the
  one-line client idiom.
- **`ui/src/lib/__tests__/eta.test.ts`** — the inline-assert `test()/eq()` harness
  run with `npx tsx` (no jsdom, no vitest). The UI pure mirror uses it verbatim.
- **`server/tests/test_filter_names.py:26-31` — `test_capture_path_includes_filter_token`**
  asserts `_Ha_` is in the saved name. It reads the default template through the
  new engine and must keep passing (free regression pin). `save_fits` still gets
  `filter_name=filt` at **`hub.py:1424`** independently of the filename token.

### 1.3 Approach

**Pure engine — `server/astrodeck/naming.py` (stdlib only, no config/hub import).**
The template is a `/`-separated path; the last non-empty segment is the filename.
Within a segment, `_` joins fields. Rendering is **split → substitute → drop-empty
→ rejoin**, which reproduces today's `parts`-list construction exactly (today drops
the empty filter piece and joins the rest with `_`; the engine drops any
empty-valued piece and joins with `_` — same result, no fragile global collapse):

1. `template.split("/")` → folder segments.
2. Each segment `.split("_")` → pieces; substitute `$$TOKEN$$` in each piece;
   drop pieces that render empty; rejoin survivors with `_`.
3. Drop empty segments; append `.fits` to the last; return a **relative** `Path`.

Per-token sanitization keeps byte-for-byte parity with the two legacy sanitizers:
`FILTER` uses **strict** (`alnum`+`-_`, `.strip("_")`); everything else uses
**loose** (`alnum`+`-_ `, `.strip()`). Literal template text is loose-sanitized
too, which neutralizes `..`/`:`/`/` injected as literals (defense in depth on top
of save-time validation). Because a value can never contain `/` (sanitized away)
and never a `..` segment, `CAPTURE_DIR / rel` provably stays under `CAPTURE_DIR`
(the existing `_is_local_save` `is_relative_to` check at `hub.py:1585` is the
backstop).

**Token vocabulary** (values available at the `_capture_path` seam — no new args
threaded through `capture()`):

| Token | Value | Sanitize |
|---|---|---|
| `$$TARGET$$` | sanitized target, `"untargeted"` fallback | loose |
| `$$FRAMETYPE$$` | `Light`/`Dark`/`Flat`/`Bias` | loose |
| `$$FILTER$$` | active filter name, `""` when none | strict |
| `$$DATE$$` | `%Y-%m-%d` (local calendar date) | loose |
| `$$TIME$$` | `%H%M%S` | loose |
| `$$DATETIME$$` | `%Y-%m-%d_%H%M%S` (legacy stamp) | loose |
| `$$NIGHT$$` | observing-night date (`now − 12h`, `%Y-%m-%d`) | loose |
| `$$FRAMENR$$` | per-target counter, `%04d` | loose |

**Default template** (byte-for-byte with today):
```
$$TARGET$$/$$FRAMETYPE$$_$$TARGET$$_$$FILTER$$_$$DATE$$_$$TIME$$_$$FRAMENR$$
```
With filter `Ha` → `M42/Light_M42_Ha_2026-07-23_213045_0001.fits`; without →
`M42/Light_M42_2026-07-23_213045_0001.fits`. Both match the legacy formula (the
`$$DATE$$_$$TIME$$` pair reproduces the `%Y-%m-%d_%H%M%S` stamp with its internal
`_`). `$$NIGHT$$` is offered for stable per-night folders but is **not** in the
default (it would diverge from today across midnight).

**Persisted per-target counter.** A small JSON `{safe_target: last_n}` at
`CAPTURE_DIR/.frame_counters.json`, read-increment-write each saved frame via the
atomic helpers. Lives under `CAPTURE_DIR` (not `AppConfig`) because (a) it changes
every frame — putting it in `AppConfig` would bump `version` and spam the `config`
broadcast / break optimistic concurrency; (b) `CAPTURE_DIR` is the persistent image
library (survives self-update) and every test already monkeypatches it. Counter is
**monotonic per target** (matches "persisted per-target"); the per-night folder,
not a counter reset, separates nights. Captures are serialized by the exposure
guard (`hub.py:1389`), so there's no counter race.

**Config.** `NamingConfig{ template: str = DEFAULT_TEMPLATE }` appended to
`AppConfig`; `ConfigStore.set_naming` validates via `naming.validate_template`
(raises `ValueError`→422) then `bump_and_save`. `redacted()` needs no change (no
secret). The template is non-secret and passes through the config broadcast.

**API.** `POST /api/config/naming` (body `NamingConfig`), `config.site_optics`
(imaging/output concern — same rationale the survey route uses), offload
`set_naming` to a thread, broadcast `redacted(cfg)`, return `_config_payload`.

**UI.** `NamingPanel` (Connect tab): template `<input>` + live rendered **preview**
computed client-side by a pure TS mirror (`ui/src/lib/naming.ts`), a token
reference (chips), Save + Reset-to-default (§11.8 honest-disabled), read-only
under no `config.site_optics`. The TS mirror is pinned by the **same golden
vectors** as the Python engine so the two can't drift silently; the server render
is authoritative for the real path.

### 1.4 Placement

| Concern | Location |
|---|---|
| Pure engine + tokens + validation | `server/astrodeck/naming.py` (new) |
| Backend tests | `server/tests/test_naming.py` (new) |
| Config model + setter | `server/astrodeck/config.py` |
| Path builder + counter store | `server/astrodeck/hub.py` (`_capture_path`, `_next_frame_counter`) |
| Counter file | `CAPTURE_DIR/.frame_counters.json` |
| Route | `server/astrodeck/api/app.py` |
| UI types / client / pure mirror / panel | `ui/src/types.ts`, `ui/src/api/backends.ts`, `ui/src/lib/naming.ts`, `ui/src/components/settings/NamingPanel.tsx` |
| UI pure test | `ui/src/lib/__tests__/naming.test.ts` (new) |

---

## 2. Global Constraints (verbatim)

- **Privacy** — real coords `[SITE-LAT]` / `[SITE-LON]` and label **"[SITE-LABEL]"**
  NEVER appear in code/tests/docs. Site default is **"My Observatory"** / `0.0`.
- **Never** `git add -A` — stage explicit paths only.
- **UI gate** — `cd ui && npx tsc -b` must pass.
- **NO jsdom** — pure logic is tested via `npx tsx` inline-assert (idiom:
  `ui/src/lib/__tests__/eta.test.ts`).
- **Backend tests** — `server/.venv/Scripts/pytest.exe` from the repo root,
  `-n0` (single worker).
- **Client toasts** — via `useStore.getState().enqueueToast` (or the `showToast`
  wrapper).
- **Honest-disabled §11.8** — dim + lock glyph + `aria-disabled` + `title`; never
  the native `disabled` attribute on interactive controls (`readOnly` for inputs).
- **Do not disrupt astrotown** (the deployed box).

---

## 3. TDD Plan

Interfaces first, then bite-sized tasks. Every task: write the failing test, then
the code, then run the exact command and confirm the exact output.

### 3.0 Interfaces (exact signatures)

```python
# server/astrodeck/naming.py  (NEW — pure, stdlib only)
from __future__ import annotations
from pathlib import Path
from typing import Mapping

CAPTURE_EXT: str = ".fits"
DEFAULT_TEMPLATE: str = (
    "$$TARGET$$/$$FRAMETYPE$$_$$TARGET$$_$$FILTER$$_$$DATE$$_$$TIME$$_$$FRAMENR$$")
KNOWN_TOKENS: dict[str, str]  # token name -> "loose" | "strict"

def sanitize_component(value: str, mode: str = "loose") -> str: ...
def render_relative_path(template: str, fields: Mapping[str, str]) -> Path: ...
def validate_template(template: str) -> None: ...      # raises ValueError
def token_names() -> tuple[str, ...]: ...              # UI help list
```
```python
# server/astrodeck/config.py  (additions)
from .naming import DEFAULT_TEMPLATE, validate_template

class NamingConfig(BaseModel):
    template: str = DEFAULT_TEMPLATE

# AppConfig:  naming: NamingConfig = Field(default_factory=NamingConfig)

class ConfigStore:
    def set_naming(self, naming: NamingConfig) -> AppConfig: ...
```
```python
# server/astrodeck/hub.py  (additions / rewrite)
def _counter_file(self) -> Path: ...                  # CAPTURE_DIR/.frame_counters.json
def _next_frame_counter(self, key: str) -> int: ...   # persisted increment
def _capture_path(self, target: str, frame_type: str,
                  filter_name: str = "") -> Path: ...  # rewritten; signature unchanged
```
```python
# server/astrodeck/api/app.py  (route)
@app.post("/api/config/naming")
@declare(CAP_CONFIG_SITE_OPTICS)
async def set_naming_config(
        body: NamingConfig,
        principal: Principal = Depends(require(CAP_CONFIG_SITE_OPTICS))): ...
```
```typescript
// ui/src/types.ts
export interface NamingConfig { template: string; }
// AppConfig:  naming?: NamingConfig;

// ui/src/api/backends.ts
export const setNamingConfig = (naming: NamingConfig): Promise<AppConfig> =>
  api.post<AppConfig>("/api/config/naming", naming);

// ui/src/lib/naming.ts  (NEW — pure mirror)
export const DEFAULT_TEMPLATE: string;
export const NAMING_TOKENS: readonly string[];
export function sanitizeComponent(v: string, mode?: "loose" | "strict"): string;
export function renderTemplatePreview(template: string, fields: Record<string, string>): string;

// ui/src/components/settings/NamingPanel.tsx  (NEW)
export default function NamingPanel(): JSX.Element;
```

---

### Task 1 — Pure engine `naming.py` + pytest — **Sonnet**
*(mechanical: exact code + golden vectors below; the byte-for-byte correctness is
the crux but is fully pinned by tests, so no Opus needed.)*

**Files:** `server/astrodeck/naming.py` (new), `server/tests/test_naming.py` (new).

**Step 1a — write the engine.**
```python
"""PRO-11 file-naming + folder templates — pure token engine (no I/O).

The persisted per-target counter lives in the Hub (CAPTURE_DIR-backed); this
module is import-light (stdlib only) so config.py can import DEFAULT_TEMPLATE /
validate_template without a cycle."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Mapping

CAPTURE_EXT = ".fits"
DEFAULT_TEMPLATE = (
    "$$TARGET$$/$$FRAMETYPE$$_$$TARGET$$_$$FILTER$$_$$DATE$$_$$TIME$$_$$FRAMENR$$")

# token name -> sanitize mode. "strict" mirrors the legacy filter sanitizer
# (hub.py:1686); "loose" mirrors the legacy target sanitizer (hub.py:1681).
KNOWN_TOKENS: dict[str, str] = {
    "TARGET": "loose", "FRAMETYPE": "loose", "FILTER": "strict",
    "DATE": "loose", "TIME": "loose", "DATETIME": "loose",
    "NIGHT": "loose", "FRAMENR": "loose",
}

_TOKEN_RE = re.compile(r"\$\$([A-Z0-9_]+)\$\$")


def token_names() -> tuple[str, ...]:
    return tuple(KNOWN_TOKENS)


def sanitize_component(value: str, mode: str = "loose") -> str:
    """One path component, sanitized to match the legacy builders byte-for-byte.
    strict: alnum + -_ , strip underscores (legacy filter). loose: alnum + -_ +
    space, strip whitespace (legacy target)."""
    v = value or ""
    if mode == "strict":
        return "".join(c if c.isalnum() or c in "-_" else "_" for c in v).strip("_")
    return "".join(c if c.isalnum() or c in "-_ " else "_" for c in v).strip()


def _sub_piece(piece: str, fields: Mapping[str, str]) -> str:
    """Substitute $$TOKEN$$ in one underscore-delimited piece. Literal runs are
    loose-sanitized (neutralizes `..`/`:` injected as literals); each token value
    is sanitized by its own mode. Unknown tokens render empty."""
    out: list[str] = []
    pos = 0
    for m in _TOKEN_RE.finditer(piece):
        lit = piece[pos:m.start()]
        if lit:
            out.append(sanitize_component(lit, "loose"))
        mode = KNOWN_TOKENS.get(m.group(1))
        if mode is not None:
            out.append(sanitize_component(fields.get(m.group(1), ""), mode))
        pos = m.end()
    tail = piece[pos:]
    if tail:
        out.append(sanitize_component(tail, "loose"))
    return "".join(out)


def render_relative_path(template: str, fields: Mapping[str, str]) -> Path:
    """Render a template to a RELATIVE path (folders via '/', '.fits' appended).
    Split -> substitute -> drop-empty -> rejoin reproduces the legacy parts-list
    (an empty token drops out, so no double '_')."""
    segments: list[str] = []
    for seg in template.split("/"):
        pieces = [_sub_piece(p, fields) for p in seg.split("_")]
        joined = "_".join(p for p in pieces if p != "")
        if joined:
            segments.append(joined)
    if not segments:                      # validation prevents this; ultra-defensive
        segments = ["capture"]
    segments[-1] = segments[-1] + CAPTURE_EXT
    return Path(*segments)


#: sample fields used by validate_template's dry render.
_SAMPLE = {"TARGET": "M42", "FRAMETYPE": "Light", "FILTER": "Ha",
           "DATE": "2026-07-23", "TIME": "213045",
           "DATETIME": "2026-07-23_213045", "NIGHT": "2026-07-23",
           "FRAMENR": "0001"}


def validate_template(template: str) -> None:
    """Raise ValueError (route -> 422) on an unusable template."""
    t = (template or "").strip()
    if not t:
        raise ValueError("naming template must not be empty")
    if "\\" in t or ":" in t:
        raise ValueError("use / for folders; backslash and ':' are not allowed")
    unknown = sorted({m for m in _TOKEN_RE.findall(t) if m not in KNOWN_TOKENS})
    if unknown:
        raise ValueError(
            "unknown token(s): " + ", ".join(f"$${u}$$" for u in unknown) +
            " — valid: " + ", ".join(f"$${k}$$" for k in KNOWN_TOKENS))
    rel = render_relative_path(t, _SAMPLE)
    if rel.is_absolute() or any(part in ("..", ".") for part in rel.parts):
        raise ValueError("template must not contain '..' or absolute segments")
    if not rel.name or rel.name == CAPTURE_EXT:
        raise ValueError("template must render a non-empty filename")
```

**Step 1b — write `server/tests/test_naming.py` (pure part).**
```python
"""PRO-11 file-naming templates — pure engine + per-target counter."""
from __future__ import annotations
import re
import pytest
from astrodeck.naming import (
    DEFAULT_TEMPLATE, render_relative_path, sanitize_component, validate_template)


def test_default_template_byte_for_byte():
    f = dict(TARGET="M42", FRAMETYPE="Light", FILTER="Ha",
             DATE="2026-07-23", TIME="213045", FRAMENR="0001")
    assert render_relative_path(DEFAULT_TEMPLATE, f).as_posix() == \
        "M42/Light_M42_Ha_2026-07-23_213045_0001.fits"
    # no filter -> the empty piece drops, no double underscore (legacy parity)
    assert render_relative_path(DEFAULT_TEMPLATE, dict(f, FILTER="")).as_posix() == \
        "M42/Light_M42_2026-07-23_213045_0001.fits"


def test_sanitizers_match_legacy():
    assert sanitize_component("NGC 7000", "loose") == "NGC 7000"   # target keeps space
    assert sanitize_component("@M42/x", "loose") == "_M42_x"
    assert sanitize_component("L Pro", "strict") == "L_Pro"         # filter -> underscore
    assert sanitize_component("_Ha_", "strict") == "Ha"


def test_folder_tokens_and_empty_folder_drop():
    f = dict(TARGET="M42", NIGHT="2026-07-23", FILTER="", FRAMETYPE="Light",
             DATE="2026-07-23", TIME="213045", FRAMENR="0007")
    tmpl = "$$TARGET$$/$$NIGHT$$/$$FILTER$$/$$FRAMETYPE$$_$$FRAMENR$$"
    # empty $$FILTER$$ folder level drops out entirely
    assert render_relative_path(tmpl, f).as_posix() == \
        "M42/2026-07-23/Light_0007.fits"


def test_no_path_traversal_from_values_or_literals():
    f = dict(TARGET="../../etc", FRAMETYPE="Light", FILTER="", DATE="d",
             TIME="t", FRAMENR="0001")
    rel = render_relative_path("$$TARGET$$/$$FRAMETYPE$$_$$FRAMENR$$", f)
    assert ".." not in rel.parts and not rel.is_absolute()


def test_validate_template():
    validate_template(DEFAULT_TEMPLATE)                 # ok
    for bad in ("", "$$NOPE$$", r"$$TARGET$$\x", "$$TARGET$$/"):
        with pytest.raises(ValueError):
            validate_template(bad)
```

**Run:**
```
server/.venv/Scripts/pytest.exe server/tests/test_naming.py -n0 -q
```
**Expect:** `5 passed` (counter tests added in Task 3 raise this later).

---

### Task 2 — `NamingConfig` + `set_naming` in config.py — **Sonnet**

**Files:** `server/astrodeck/config.py`, `server/tests/test_naming.py` (append).

**Step 2a — model + AppConfig field.** Near the other sub-models (e.g. after
`SurveyConfig`, `config.py:353`), and import `DEFAULT_TEMPLATE`/`validate_template`
from `.naming` at the top of config.py (naming.py imports nothing from config.py →
no cycle):
```python
from .naming import DEFAULT_TEMPLATE, validate_template  # near the other imports

class NamingConfig(BaseModel):
    """PRO-11: NINA-style $$TOKEN$$ path template for capture folder+filename.
    Default reproduces the legacy fixed layout byte-for-byte."""
    template: str = DEFAULT_TEMPLATE
```
Append to `AppConfig` (after `weather`, `config.py:455`):
```python
    # --- file-naming template (PRO-11; appended — old configs load fine) ---
    naming: NamingConfig = Field(default_factory=NamingConfig)
```

**Step 2b — setter** (mirror `set_survey`/`set_providers`, after `set_survey`,
`config.py:833`):
```python
    def set_naming(self, naming: "NamingConfig") -> AppConfig:
        """Persist the capture-naming template. Write-time validated so an
        unusable template (empty/unknown-token/traversal) is rejected here
        (route maps ValueError -> 422) rather than reaching _capture_path."""
        validate_template(naming.template)
        cfg = self.cfg()
        cfg.naming = naming
        return self.bump_and_save()
```

**Step 2c — tests** (append to `test_naming.py`):
```python
import astrodeck.config as configmod

def test_appconfig_naming_default_and_roundtrip(tmp_path):
    store = configmod.ConfigStore(path=tmp_path / "astrodeck.json")
    assert store.cfg().naming.template == configmod.DEFAULT_TEMPLATE
    store.set_naming(configmod.NamingConfig(template="$$TARGET$$/$$FRAMENR$$"))
    assert store.reload().naming.template == "$$TARGET$$/$$FRAMENR$$"

def test_set_naming_rejects_bad_template(tmp_path):
    store = configmod.ConfigStore(path=tmp_path / "astrodeck.json")
    with pytest.raises(ValueError):
        store.set_naming(configmod.NamingConfig(template="$$NOPE$$"))

def test_old_config_without_naming_key_loads(tmp_path):
    from astrodeck.persist import write_json_atomic
    p = tmp_path / "astrodeck.json"
    write_json_atomic(p, {"version": 1})          # legacy file, no naming key
    assert configmod.ConfigStore(path=p).cfg().naming.template == configmod.DEFAULT_TEMPLATE
```
Add `from astrodeck.naming import DEFAULT_TEMPLATE as _` is unnecessary —
`configmod.DEFAULT_TEMPLATE` is re-exported by the `from .naming import` line.

**Run:** `server/.venv/Scripts/pytest.exe server/tests/test_naming.py -n0 -q`
**Expect:** `8 passed`.

---

### Task 3 — `_capture_path` rewrite + persisted per-target counter — **Sonnet**

**Files:** `server/astrodeck/hub.py`, `server/tests/test_naming.py` (append).

**Step 3a — import the persist helpers** (top of hub.py, near `:21`):
```python
from .persist import read_json_or, write_json_atomic
```

**Step 3b — counter store + rewrite `_capture_path`** (replace `hub.py:1680-1688`):
```python
    def _counter_file(self) -> Path:
        # under CAPTURE_DIR (the persistent image library; auto-isolated by the
        # CAPTURE_DIR monkeypatch every test already applies). Resolved live so
        # the monkeypatch is honored.
        return CAPTURE_DIR / ".frame_counters.json"

    def _next_frame_counter(self, key: str) -> int:
        """Persisted, per-target, monotonic frame number. Captures are serialized
        by the exposure guard, so no lock is needed."""
        data = read_json_or(self._counter_file(), {})
        if not isinstance(data, dict):
            data = {}
        n = int(data.get(key, 0) or 0) + 1
        data[key] = n
        write_json_atomic(self._counter_file(), data)
        return n

    def _capture_path(self, target: str, frame_type: str, filter_name: str = "") -> Path:
        from .naming import render_relative_path, sanitize_component
        # "untargeted" fallback keyed off the SANITIZED target (legacy parity,
        # hub.py old :1681); sanitize is idempotent so the engine re-sanitize is a
        # no-op.
        safe_target = sanitize_component(target, "loose") or "untargeted"
        n = self._next_frame_counter(safe_target)
        t = time.localtime()
        night = time.localtime(time.time() - 12 * 3600)   # noon-rollover night date
        fields = {
            "TARGET": safe_target,
            "FRAMETYPE": frame_type,
            "FILTER": filter_name or "",
            "DATE": time.strftime("%Y-%m-%d", t),
            "TIME": time.strftime("%H%M%S", t),
            "DATETIME": time.strftime("%Y-%m-%d_%H%M%S", t),
            "NIGHT": time.strftime("%Y-%m-%d", night),
            "FRAMENR": f"{n:04d}",
        }
        template = config_store.cfg().naming.template
        return CAPTURE_DIR / render_relative_path(template, fields)
```
The signature is unchanged, so the sole caller (`hub.py:1412`), `save_fits`
(`:1424`), and `test_filter_names.py:26-31` are untouched.

**Step 3c — tests** (append to `test_naming.py`):
```python
import astrodeck.hub as hub_module
from astrodeck.hub import Hub

def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path)
    store = configmod.ConfigStore(path=tmp_path / "astrodeck.json")
    monkeypatch.setattr(hub_module, "config_store", store)
    monkeypatch.setattr(configmod, "config_store", store)
    return store

def test_hub_default_layout_byte_for_byte(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    p = Hub()._capture_path("M42", "Light", "Ha")
    assert p.parent == tmp_path / "M42"
    assert re.fullmatch(r"Light_M42_Ha_\d{4}-\d{2}-\d{2}_\d{6}_0001\.fits", p.name)

def test_counter_is_per_target_and_persists(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    h = Hub()
    assert h._capture_path("M42", "Light", "").name.endswith("_0001.fits")
    assert h._capture_path("M42", "Light", "").name.endswith("_0002.fits")
    assert h._capture_path("M31", "Light", "").name.endswith("_0001.fits")  # per-target
    # persists across a fresh Hub sharing CAPTURE_DIR
    assert Hub()._capture_path("M42", "Light", "").name.endswith("_0003.fits")

def test_custom_template_folders_by_night_and_filter(tmp_path, monkeypatch):
    store = _isolate(tmp_path, monkeypatch)
    store.set_naming(configmod.NamingConfig(
        template="$$TARGET$$/$$NIGHT$$/$$FILTER$$/$$FRAMETYPE$$_$$FRAMENR$$"))
    p = Hub()._capture_path("M42", "Light", "Ha")
    assert p.relative_to(tmp_path).parts[:3] == ("M42",) + tuple(p.parent.parts[-2:])
    assert p.parent.name == "Ha" and p.name == "Light_0001.fits"
```

**Run:** `server/.venv/Scripts/pytest.exe server/tests/test_naming.py server/tests/test_filter_names.py -n0 -q`
**Expect:** `11 passed` in test_naming + `test_filter_names` still green (the filter
token regression).

---

### Task 4 — `POST /api/config/naming` route — **Sonnet**

**Files:** `server/astrodeck/api/app.py`, `server/tests/test_naming.py` (append).

**Step 4a — import** — add `NamingConfig` to the `from ..config import (...)` block
(`app.py:53-56`).

**Step 4b — route** — mirror `set_survey_config` (paste after it, `app.py:977`):
```python
    # ---------------------------------------------------- naming template (PRO-11)
    # Capture folder+filename token template. config.site_optics (imaging/output
    # concern, same rationale as the survey route). Write-time validated in
    # set_naming (ValueError -> 422); redacted union broadcast so every open
    # client's Naming panel updates.
    @app.post("/api/config/naming")
    @declare(CAP_CONFIG_SITE_OPTICS)
    async def set_naming_config(
            body: NamingConfig,
            principal: Principal = Depends(require(CAP_CONFIG_SITE_OPTICS))):
        try:
            cfg = await asyncio.to_thread(config_store.set_naming, body)
        except ValueError as e:
            raise HTTPException(422, str(e))
        bus.publish("config", config=redacted(cfg))
        return _config_payload(principal)
```

**Step 4c — test** (append; mirror an existing app route test's client fixture —
`test_automation_api.py` / `test_sessions_api.py` set `CAPTURE_DIR` + a temp config
store and build a `TestClient`):
```python
def test_naming_route_saves_and_422s(tmp_path, monkeypatch):
    # build the app test client the same way test_automation_api.py does, then:
    #   r = client.post("/api/config/naming", json={"template": "$$TARGET$$/$$FRAMENR$$"})
    #   assert r.status_code == 200
    #   assert config_store.cfg().naming.template == "$$TARGET$$/$$FRAMENR$$"
    #   r = client.post("/api/config/naming", json={"template": "$$NOPE$$"})
    #   assert r.status_code == 422
    ...
```
*(Use the existing client fixture from a sibling app-route test; keep the assertions
above.)*

**Run:** `server/.venv/Scripts/pytest.exe server/tests/test_naming.py -n0 -q`
**Expect:** all green (12 passed).

---

### Task 5 — UI pure mirror `lib/naming.ts` + tsx test — **Sonnet**

**Files:** `ui/src/lib/naming.ts` (new), `ui/src/lib/__tests__/naming.test.ts` (new).

**Step 5a — the mirror** (same split→substitute→drop→rejoin; the loose/strict
sanitizers mirror the Python):
```typescript
// PRO-11 client mirror of server/astrodeck/naming.py. Advisory PREVIEW only —
// the server render is authoritative for the real path. Kept in lock-step with
// the Python engine by the shared golden vectors in naming.test.ts.
export const CAPTURE_EXT = ".fits";
export const DEFAULT_TEMPLATE =
  "$$TARGET$$/$$FRAMETYPE$$_$$TARGET$$_$$FILTER$$_$$DATE$$_$$TIME$$_$$FRAMENR$$";
export const NAMING_TOKENS = [
  "TARGET", "FRAMETYPE", "FILTER", "DATE", "TIME", "DATETIME", "NIGHT", "FRAMENR",
] as const;
const MODE: Record<string, "loose" | "strict"> = {
  TARGET: "loose", FRAMETYPE: "loose", FILTER: "strict", DATE: "loose",
  TIME: "loose", DATETIME: "loose", NIGHT: "loose", FRAMENR: "loose",
};
const TOKEN_RE = /\$\$([A-Z0-9_]+)\$\$/g;

export function sanitizeComponent(v: string, mode: "loose" | "strict" = "loose"): string {
  const ok = (c: string) =>
    /[A-Za-z0-9]/.test(c) || (mode === "strict" ? c === "-" || c === "_"
                                                 : c === "-" || c === "_" || c === " ");
  const mapped = [...(v ?? "")].map((c) => (ok(c) ? c : "_")).join("");
  return mode === "strict" ? mapped.replace(/^_+|_+$/g, "") : mapped.trim();
}

function subPiece(piece: string, fields: Record<string, string>): string {
  return piece.replace(TOKEN_RE, (_m, name: string) =>
    name in MODE ? sanitizeComponent(fields[name] ?? "", MODE[name]) : "")
    // loose-sanitize literal remainder is folded in above for tokens; literals
    // (non-token text) are sanitized here:
    .split("$$").join("");  // no stray token markers survive
}

export function renderTemplatePreview(template: string, fields: Record<string, string>): string {
  const segs: string[] = [];
  for (const seg of template.split("/")) {
    const joined = seg.split("_").map((p) => subPiece(p, fields)).filter((p) => p !== "").join("_");
    if (joined) segs.push(joined);
  }
  if (segs.length === 0) segs.push("capture");
  segs[segs.length - 1] += CAPTURE_EXT;
  return segs.join("/");
}
```
*(Note for the implementer: to match the Python literal-sanitization exactly, run
`sanitizeComponent(lit,"loose")` on the non-token text spans as well; the simplest
faithful port walks the piece with `TOKEN_RE.exec` like `_sub_piece`. The golden
vectors below are the contract — make them pass.)*

**Step 5b — test** (`naming.test.ts`, eta.test.ts harness):
```typescript
import { DEFAULT_TEMPLATE, renderTemplatePreview, sanitizeComponent } from "../naming";
// ... paste the passed/failed/test/eq harness from eta.test.ts ...

const F = { TARGET: "M42", FRAMETYPE: "Light", FILTER: "Ha", DATE: "2026-07-23",
            TIME: "213045", DATETIME: "2026-07-23_213045", NIGHT: "2026-07-23",
            FRAMENR: "0001" };

test("default template byte-for-byte (matches Python golden)", () => {
  eq(renderTemplatePreview(DEFAULT_TEMPLATE, F),
     "M42/Light_M42_Ha_2026-07-23_213045_0001.fits");
});
test("empty filter drops the piece — no double underscore", () => {
  eq(renderTemplatePreview(DEFAULT_TEMPLATE, { ...F, FILTER: "" }),
     "M42/Light_M42_2026-07-23_213045_0001.fits");
});
test("night+filter foldering; empty filter folder drops", () => {
  eq(renderTemplatePreview("$$TARGET$$/$$NIGHT$$/$$FILTER$$/$$FRAMETYPE$$_$$FRAMENR$$",
     { ...F, FILTER: "" }), "M42/2026-07-23/Light_0001.fits");
});
test("sanitizers mirror server", () => {
  eq(sanitizeComponent("NGC 7000", "loose"), "NGC 7000");
  eq(sanitizeComponent("L Pro", "strict"), "L_Pro");
});
// ... console.log report + export result, as in eta.test.ts ...
```

**Run:**
```
cd ui && npx tsx src/lib/__tests__/naming.test.ts
```
**Expect:** `naming.test: N/N passed`, no failures.

---

### Task 6 — `NamingPanel` + types + client + SettingsView wiring — **Sonnet**

**Files:** `ui/src/types.ts`, `ui/src/api/backends.ts`,
`ui/src/components/settings/NamingPanel.tsx` (new),
`ui/src/components/settings/SettingsView.tsx`.

**Step 6a — types** (`types.ts`): add `export interface NamingConfig { template: string; }`
near `SurveyConfig` (`:669`), and `naming?: NamingConfig;` to `AppConfig` (after
`survey?`, `:569`).

**Step 6b — client** (`api/backends.ts`, next to `setSurveyConfig` `:393`):
```typescript
/** POST /api/config/naming → config payload. config.site_optics. */
export const setNamingConfig = (naming: NamingConfig): Promise<AppConfig> =>
  api.post<AppConfig>("/api/config/naming", naming);
```
(Import `NamingConfig` in the types import at the top of backends.ts.)

**Step 6c — `NamingPanel.tsx`** (SkyAtlasPanel gating idiom + AlertsPanel §11.8
honest-disabled buttons):
```tsx
// NamingPanel.tsx — Settings → capture file-naming template (PRO-11).
// Live client preview (advisory; server render is authoritative). config.site_optics.
import { useEffect, useState, type JSX } from "react";
import { setNamingConfig } from "../../api/backends";
import { ApiError } from "../../api";
import { useConfig, useStore } from "../../store";
import { useCan } from "../../lib/caps";
import { Panel, Field } from "../ui";
import { Icon } from "../icons";
import {
  DEFAULT_TEMPLATE, NAMING_TOKENS, renderTemplatePreview,
} from "../../lib/naming";

const SAMPLE = { TARGET: "M42", FRAMETYPE: "Light", FILTER: "Ha", DATE: "2026-07-23",
  TIME: "213045", DATETIME: "2026-07-23_213045", NIGHT: "2026-07-23", FRAMENR: "0001" };

export default function NamingPanel(): JSX.Element {
  const config = useConfig();
  const canEdit = useCan("config.site_optics");
  const stored = config?.naming?.template ?? DEFAULT_TEMPLATE;
  const [draft, setDraft] = useState(stored);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => { setDraft(stored); }, [stored]);

  const dirty = draft !== stored;
  const save = async () => {
    if (busy || !canEdit || !dirty) return;
    setBusy(true); setErr(null);
    try {
      await setNamingConfig({ template: draft });
      await useStore.getState().loadConfig();
      useStore.getState().showToast("success", "capture naming saved");
    } catch (e) {
      setErr(e instanceof ApiError
        ? (e.status === 403 ? "config.site_optics required"
           : e.status === 422 ? e.message : e.message)
        : "Could not save.");
    } finally { setBusy(false); }
  };
  const reset = () => { if (canEdit) setDraft(DEFAULT_TEMPLATE); };

  const saveInert = busy || !canEdit || !dirty;
  const resetInert = busy || !canEdit || draft === DEFAULT_TEMPLATE;
  return (
    <Panel title="File Naming">
      <div className="flex flex-col gap-3">
        <Field label="Template">
          <input className="field mono" value={draft} readOnly={!canEdit}
                 onChange={(e) => setDraft(e.target.value)} aria-label="File-naming template"
                 spellCheck={false} />
        </Field>
        <div className="text-[12px] text-dim">Preview</div>
        <div className="mono text-[12px] text-ink break-all">
          captures/{renderTemplatePreview(draft || DEFAULT_TEMPLATE, SAMPLE)}
        </div>
        <div className="flex flex-wrap gap-1.5" role="group" aria-label="Tokens">
          {NAMING_TOKENS.map((t) => (
            <button key={t} type="button"
              aria-disabled={!canEdit || undefined}
              title={!canEdit ? "config.site_optics required" : `insert $$${t}$$`}
              onClick={!canEdit ? undefined : () => setDraft((d) => `${d}$$${t}$$`)}
              className={`text-[11px] px-2 py-1 border uppercase tracking-wide
                ${!canEdit ? "!text-dim cursor-not-allowed" : "bg-raise border-line2 text-dim"}`}>
              {`$$${t}$$`}
            </button>
          ))}
        </div>
        <div className="flex gap-2 flex-wrap">
          <button type="button" aria-disabled={saveInert || undefined}
            title={!canEdit ? "config.site_optics required" : undefined}
            onClick={saveInert ? undefined : () => void save()}
            className={`btn btn-touch inline-flex items-center gap-1
              ${saveInert ? "!text-dim cursor-not-allowed" : "btn-accent"}`}>
            {!canEdit && <Icon name="lock" size={11} />} Save
          </button>
          <button type="button" aria-disabled={resetInert || undefined}
            onClick={resetInert ? undefined : reset}
            className={`btn btn-touch ${resetInert ? "!text-dim cursor-not-allowed" : ""}`}>
            Reset to default
          </button>
        </div>
        {err && <p className="text-[12px] text-warn">{err}</p>}
        {!canEdit && (
          <p className="text-[11px] text-dim inline-flex items-center gap-1.5">
            <Icon name="lock" size={11} /> Read-only — changing naming needs config.site_optics access.
          </p>
        )}
        <p className="text-[11px] text-dim">
          Folders via <span className="mono">/</span>; tokens like{" "}
          <span className="mono">$$TARGET$$</span>. The default reproduces the classic layout.
        </p>
      </div>
    </Panel>
  );
}
```
*(Verify `useStore.getState().showToast` exists — `store.ts:611`. If the codebase
prefers `enqueueToast`, use `useStore.getState().enqueueToast({level:"success",
title:"capture naming saved"})`.)*

**Step 6d — wire into SettingsView** (`SettingsView.tsx`): `import NamingPanel from
"./NamingPanel";` and add `<NamingPanel />` in the Connect-tab left column after
`<SkyAtlasPanel />` (`:167`).

**Run (the UI gate):**
```
cd ui && npx tsc -b
```
**Expect:** clean exit (no TS errors). Optionally re-run the Task 5 tsx test.

---

## 4. Open decisions

| # | Question | Recommendation |
|---|---|---|
| 1 | **Counter reset semantics** — monotonic-per-target-forever vs per-night reset? | **Monotonic per target** (matches "persisted per-target counter"; per-night folders separate nights; avoids intra-target filename collisions). Add a per-night reset later only if users ask. |
| 2 | **Capability** — reuse `config.site_optics` or mint a dedicated `config.naming`? | **Reuse `config.site_optics`** — the survey route already reuses it for the same "imaging/output config" reason; no role-table churn. |
| 3 | **Unknown tokens** — reject at save or silently drop? | **Reject at save** (`validate_template`) for typo protection; the engine still drops unknowns at render as defense in depth (a hand-edited config never crashes). |
| 4 | **Extra tokens** (`$$GAIN$$`/`$$EXPOSURE$$`/`$$BINNING$$`) — include now? | **Defer.** They aren't available at the `_capture_path` seam without threading new args through `capture()`; keep the signature stable and ship the target/date/filter/frame set. Add later behind the same engine. |
| 5 | **Should the default fold by night** (`$$NIGHT$$`) out of the box? | **No** — the default must be byte-for-byte with today (`$$DATE$$`, calendar-local). Ship `$$NIGHT$$` as a token + offer preset templates in the panel so users opt in. |
| 6 | **Counter write on the event loop** — inline JSON write per saved frame. | **Keep inline** (~100 B, frames are seconds apart; the FITS write is already offloaded right after). Offload only if profiling shows a stall. |
| 7 | **TS/Python engine drift** — client preview mirrors the server. | **Accept, pin with shared golden vectors** (Task 1 ⇄ Task 5). Server render stays authoritative for the real path; the preview is advisory. Revisit a server `POST /api/naming/preview` endpoint only if drift bites. |
