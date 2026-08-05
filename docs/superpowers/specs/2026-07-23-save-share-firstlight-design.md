# NOV-11 — One-tap save/share "first light" image (novice)

Combined design spec + TDD implementation plan. One deliverable: a **"Save first
light"** action in the preview download menu that returns a nicely-stretched,
captioned, phone-sized **JPEG** — target · exposure×count · date — of the frame
the novice is looking at. Server-side composite endpoint
`GET /api/preview/{id}/share.jpg`.

---

## 1. Design

### 1.1 Goal
The payoff of a first night is *showing someone the picture*. Today the only
exports are three raw per-frame files (Stretched PNG / Lossless PNG / FITS) —
each either dim-linear-adjacent, large, or unopenable on a phone, and **none
captioned**. NOV-11 adds one tap that produces a share-ready JPEG: the
already-auto-stretched image the user sees, downscaled to phone width, with a
small caption band ("M42 · 120 s × 30 · Jul 22 2026") baked on. No new stretch,
no new pixels — we composite a caption onto the display image already computed.

### 1.2 Current-state seams (every line read)

**The download menu + honest-disabled idiom** — `ui/src/components/preview/PreviewToolbar.tsx`
- Menu container + the three items: `:159`–`:233`. The **download-anchor pattern**
  to mirror is `:182`–`:190` — `<a role="menuitem" href={u(\`/api/preview/${id}/png\`)} download={\`preview_${id}.png\`} … onClick={() => setDlOpen(false)}>`.
- Honest-disabled §11.8 variants already in-file: disabled "Stretched PNG" span
  `:191`–`:200` and disabled "FITS (on host)" span `:222`–`:230` (dim token +
  lock glyph + `title`, not native `disabled`). The menu button's own
  honest-disabled: `:159`–`:178` (`dlDisabled = id == null || linkDown` at `:99`).
- The menu only renders when `dlOpen && id != null` (`:179`), so any new item can
  assume `id` is defined.

**The stretch + JPEG base** — `server/astrodeck/imaging/processing.py`
- `to_jpeg(data, *, black, mid, white, max_width=1400, quality=85) -> (bytes,w,h)`
  `:142`–`:153` — the auto-stretch → 8-bit 'L' JPEG that already backs the live
  loop. `_encode` `:119`–`:132` (mode 'L', BILINEAR downscale to `max_width`).
  `auto_stretch` `:33`–`:48`. **These are the "nicely stretched" bytes** — the
  share image composites onto them, it does not re-derive a stretch.
- **Text-draw availability**: `from PIL import Image` at `:25`. Verified on this
  box: **Pillow 12.2.0**; `from PIL import ImageDraw, ImageFont` import cleanly and
  `ImageFont.load_default(size)` returns a **scalable FreeType** default font
  (`load_default(size: float|None) -> FreeTypeFont | ImageFont`). `draw.textbbox`
  and `draw.text(..., font=...)` both work. **No external/bundled font file is
  needed** — the caption uses `load_default(px)`. (Confirmed by running the exact
  draw+encode pipeline: title/detail rendered, JPEG re-encoded.)

**The preview export routes** — `server/astrodeck/api/app.py`
- `_PREVIEW_CACHE = {"Cache-Control": "max-age=3600"}` `:2433`.
- Canonical display `/api/preview/{id:int}` `:2435`–`:2447`; `.png` compat
  `:2449`–`:2464`; `/lossless.png` `:2466`–`:2477`; `/thumb.jpg` `:2479`–`:2489`;
  `/fits` (CAP_VIEW_MEDIA) `:2491`–`:2505`; **`/png` download with
  `Content-Disposition: attachment` `:2507`–`:2519`** (the exact header idiom to
  mirror); `/crop` + `/render.png` 501 stubs `:2521`–`:2533`. All read
  `hub.previews.get(preview_id)` and 404 when `None`.
- Route decorator idiom: `@app.get(path, dependencies=[Depends(require(CAP_VIEW_PREVIEW))])`
  then `@declare(CAP_VIEW_PREVIEW)` (`:2507`–`:2508`). `Response` imported at
  `:23`; `asyncio` `:9`, `time` `:14` present. `imaging`/`naming` are **not** yet
  imported into app.py (top-of-file block `:27`–`:71`) — the new route adds
  `from ..imaging import build_caption, compose_share_jpeg, fmt_share_date` and
  `from ..naming import sanitize_component`.

**The preview store + what metadata is retained** — `server/astrodeck/hub.py`
- `PreviewEntry` `:154`–`:165`: `display: bytes`, `mime`, `thumb`, `lossless:
  bytes|None` (latest 1–2 only), `linear: np.ndarray|None`, `meta: dict`.
  `self.previews` `:178`. **`linear` is currently `None`** for every entry
  (`:1562` `linear=None`, comment `:1558`–`:1561`) — so a share image **cannot**
  re-stretch from raw pixels; it must composite onto the encoded
  `display`/`lossless` bytes. `display` is kept for the latest 8 frames
  (`_trim_previews` `:1611`–`:1618`), so any frame the toolbar can show is
  serveable.
- `_publish_preview` `:1458`–`:1575` builds `entry.meta`. Fields present:
  `exposure_s` `:1482`, `gain` `:1483`, `binning` `:1484`, `ts` (server epoch)
  `:1495`, `stats`, `data_width/height`, etc. **Absent: target name and any
  frame/integration count** — the preview event carries no target. NINA path
  uses `frame.rendered_bytes` verbatim as `display` (`:1501`); sim/alpaca path
  encodes via `to_jpeg` (`:1519`) + a `to_png` lossless (`:1521`).

**Where target + count actually live (client-side)** — `ui/src/types.ts`
- `SequenceState.target?: string` `:281`, `.progress?: SequenceProgress` `:284`.
- `SequenceProgress.frames_done: number` `:261`. These are the caption's target +
  count. `LivePreview` can read them from the store and pass them to the toolbar,
  which forwards them to the endpoint as **query params**. (There is no per-frame
  target on `PreviewInfo` `:191`–`:219`.)

**Client wiring + helpers**
- `ui/src/components/preview/LivePreview.tsx` renders `PreviewToolbar` `:102`–`:114`;
  `shown` (the frame on the stage) resolved `:46`; it already uses `useStore`
  (`:54`–`:57`) so it can also read `sequence`. `u()` base-URL helper:
  `ui/src/lib/base.ts:38` (`export const u = (path) => BASE + path`).

**Test idioms**
- tsx inline-assert harness: `ui/src/lib/__tests__/eta.test.ts:21`–`:43`
  (`test`/`eq`/`assert`; run via `npx tsx <file>`).
- pytest: `server/tests/test_imaging.py:1`–`:41` (`synthetic_field(...)` fixture;
  imports from `astrodeck.imaging`). Route-matrix test with a published preview:
  `server/tests/test_monitor_telemetry.py:245`–`:269`.
- Filename sanitizer: `sanitize_component(value, mode="loose")`
  `server/astrodeck/naming.py:31`–`:38`.

### 1.3 Approach

**Endpoint** — `GET /api/preview/{id}/share.jpg` (CAP_VIEW_PREVIEW), placed after
`/png` (`app.py:2519`) and before `/crop` (`:2521`):
- Reads `entry = hub.previews.get(id)`; 404 when `None` (mirrors every sibling).
- Base bytes = `entry.lossless or entry.display` (prefer the lossless PNG when
  held — no double-JPEG; else the display JPEG). Both decode with PIL regardless
  of source (NINA RGB/L JPEG, sim 'L' JPEG, or lossless PNG).
- Caption metadata is **split by where it lives**: the server fills
  `exposure_s`/`gain`/`date` from `entry.meta`; the **client supplies
  `target` + `subs` as query params** (they exist only in `SequenceState`).
  Server sanitizes/caps both for drawing and for the download filename.
- Compositing runs in `asyncio.to_thread` (PIL is blocking), like the sibling
  encode paths (`hub.py:1519`).

**The tested unit is pure and server-side.** Two functions in a new module
`server/astrodeck/imaging/share.py`, exported from `imaging/__init__.py`:
1. `build_caption(...)` — copy/format logic → `(title, detail)`. Exact-string
   pytest asserts.
2. `compose_share_jpeg(...)` — decode → downscale to phone width → draw caption
   band → re-encode JPEG. Structural pytest asserts (decodes as JPEG/RGB, width
   pinned, band added, text drawn on band, deterministic bytes).
Plus `fmt_share_date(ts)` / `fmt_exposure(s)` tiny formatters (the endpoint calls
`fmt_share_date`; `build_caption` receives an already-formatted `date_str` so it
stays deterministic and tz-independent under test).

**Data shapes**
```
build_caption(target, exposure_s, sub_count, date_str, gain) -> (title, detail)
  title  = target or "First light"                     (control-stripped, ≤48 chars)
  detail = "120 s × 30 · gain 100 · Jul 22 2026"
             · drop "× N" when sub_count in (None, 0, 1)
             · drop "gain G" when gain is None
compose_share_jpeg(base, title, detail, *, max_width=1080, quality=90,
                   wordmark="AstroDeck") -> (jpeg_bytes, out_w, out_h)
```

**Caption band algorithm** (`compose_share_jpeg`, deterministic):
1. `Image.open(BytesIO(base)).convert("RGB")`.
2. If `w > max_width`: BILINEAR resize to `(max_width, round(h*max_width/w))`. No
   upscale (phone shots of a ≤1400-wide preview only ever shrink).
3. Fonts: `title_px = max(16, round(w/26))`, `detail_px = max(12, round(w/40))`
   via `ImageFont.load_default(px)`. `pad = round(w/45)`, `gap = round(pad*0.4)`.
   `band_h = pad*2 + title_px + gap + detail_px`.
4. New canvas `(w, h + band_h)`, fill `(11,13,17)`. Paste photo at `(0,0)`.
   Draw a 1-px accent line at `y=h` in `(60,80,120)`. Draw `title` at
   `(pad, h+pad)` fill `(238,240,245)`; `detail` at `(pad, h+pad+title_px+gap)`
   fill `(150,162,180)`; right-align `wordmark` in the detail row
   (`x = w - pad - textlength(wordmark)`) fill `(90,100,120)`.
5. `img.save(buf, "JPEG", quality=quality, optimize=False)` → return
   `(buf.getvalue(), img.width, img.height)`.

**Privacy in the caption** — the caption is **target / exposure / count / gain /
date only**. It **never** includes site coordinates or a site label. This is the
strip-entirely posture: the shareable image carries nothing locating the
observer. (No `entry.meta` location field is even read.)

**Client** — `PreviewToolbar` gains an optional prop
`shareMeta?: { target?: string; subs?: number }` and a **first** menuitem
"Save first light" (the headline action, styled `btn-accent` with a `capture`
glyph). Its `href` = `u(\`/api/preview/${id}/share.jpg${shareQuery(...)}\`)`,
`download={\`firstlight_${id}.jpg\`}`. Always enabled when the menu is open
(`id != null`) — the endpoint always composites from `display`, so there is no
404 to gate against (unlike PNG/FITS). `LivePreview` computes `shareMeta` from
`useStore(s => s.sequence)` (`.target`, `.progress?.frames_done`) and passes it.

The query-string assembly (with the "× N only when subs > 1" rule) is the one
piece of client logic worth a test, so it lives in a pure
`ui/src/lib/share.ts#shareQuery` with a tsx test; the menuitem/threading are thin
render verified by `tsc -b`.

### 1.4 Placement summary
| Layer | File | Change |
|---|---|---|
| Server pure | `server/astrodeck/imaging/share.py` (new) | `fmt_exposure`, `fmt_share_date`, `build_caption`, `compose_share_jpeg` |
| Server export | `server/astrodeck/imaging/__init__.py` | re-export the four |
| Server route | `server/astrodeck/api/app.py` | `GET /api/preview/{id}/share.jpg` + two imports |
| Server test | `server/tests/test_share.py` (new) + `test_monitor_telemetry.py` | unit + route-matrix line |
| Client pure | `ui/src/lib/share.ts` (new) + `__tests__/share.test.ts` (new) | `shareQuery` |
| Client render | `ui/src/components/preview/PreviewToolbar.tsx` | prop + menuitem |
| Client render | `ui/src/components/preview/LivePreview.tsx` | compute + pass `shareMeta` |

---

## 2. Global Constraints (verbatim)

- **Privacy.** The real site coordinates **<REDACTED-LAT> / <REDACTED-LON>** and the label
  **"<REDACTED-SITE-LABEL>"** must NEVER appear in code, tests, or docs. The site default is
  **"My Observatory"** / **0.0**. The share caption is strip-entirely: it contains
  target/exposure/count/gain/date ONLY — never coordinates or a site label.
- **Never `git add -A`.** Stage only the files this plan names, explicitly.
- **UI gate:** `cd ui && npx tsc -b` must pass.
- **NO jsdom.** Client tests are pure logic via `npx tsx` inline-assert (idiom:
  `ui/src/lib/__tests__/eta.test.ts`). No DOM, no React render in tests.
- **Backend tests:** `server/.venv/Scripts/pytest.exe` run from the **repo root**,
  single-process **`-n0`**.
- **Client toasts** via `useStore.getState().enqueueToast` (not relevant to this
  feature — the share action is a browser download, no toast — noted for
  completeness).
- **Honest-disabled §11.8:** dim token + lock glyph + `aria-disabled` + `title`,
  **never** the native `disabled` attribute. (The share item itself is always
  enabled; this rule governs any state we touch in `PreviewToolbar`.)
- **Do not disrupt astrotown.** No deploy, no touching the running box.

---

## 3. TDD Implementation Plan

Interfaces are exact. Every task: write the test first (red), implement (green),
run the named command, confirm the named output.

### Interfaces

```python
# server/astrodeck/imaging/share.py
from __future__ import annotations
import io, time
import numpy as np
from PIL import Image, ImageDraw, ImageFont

def fmt_exposure(exposure_s: float) -> str: ...
    # 120.0 -> "120 s"; 1.5 -> "1.5 s"; 0.5 -> "0.5 s"; 0/negative -> "— s"

def fmt_share_date(ts: float) -> str: ...
    # server-local date, "%b %d %Y" with the day's leading zero stripped
    # e.g. -> "Jul 22 2026"

def build_caption(target: str | None, exposure_s: float,
                  sub_count: int | None, date_str: str,
                  gain: float | int | None = None) -> tuple[str, str]: ...
    # returns (title, detail); rules in §1.3

def compose_share_jpeg(base: bytes, title: str, detail: str, *,
                       max_width: int = 1080, quality: int = 90,
                       wordmark: str = "AstroDeck") -> tuple[bytes, int, int]: ...
    # returns (jpeg_bytes, out_w, out_h)
```

```ts
// ui/src/lib/share.ts
export function shareQuery(target?: string, subs?: number): string;
// "" when nothing to add; "?target=M42" ; "?target=M%2042&subs=30" (subs only >1)
```

---

### Task T1 — server caption formatters (`share.py` part 1)
**Impl tier: Sonnet** — pure string formatting; no numeric subtlety.
**Files:** `server/astrodeck/imaging/share.py` (new), `server/tests/test_share.py` (new).

**Step 1a — write the failing test.** In `server/tests/test_share.py`:
```python
from astrodeck.imaging import build_caption, fmt_exposure, fmt_share_date

def test_fmt_exposure():
    assert fmt_exposure(120.0) == "120 s"
    assert fmt_exposure(1.5) == "1.5 s"
    assert fmt_exposure(0.5) == "0.5 s"
    assert fmt_exposure(0) == "— s"

def test_fmt_share_date_shape():
    s = fmt_share_date(1_753_000_000.0)   # some 2025 epoch
    import re
    assert re.fullmatch(r"[A-Z][a-z]{2} \d{1,2} \d{4}", s), s
    assert "  " not in s                  # leading day-zero stripped

def test_build_caption_full():
    t, d = build_caption("M42", 120.0, 30, "Jul 22 2026", 100)
    assert t == "M42"
    assert d == "120 s × 30 · gain 100 · Jul 22 2026"

def test_build_caption_single_no_gain():
    t, d = build_caption(None, 5.0, 1, "Jul 22 2026", None)
    assert t == "First light"
    assert d == "5 s · Jul 22 2026"          # no "× 1", no gain

def test_build_caption_strips_and_caps():
    t, _ = build_caption("A" * 80 + "\n\t", 1.0, 0, "x", None)
    assert len(t) <= 48 and "\n" not in t and "\t" not in t
```
Run (must fail on import):
```
server/.venv/Scripts/pytest.exe server/tests/test_share.py -n0 -q
```
Expected: `ImportError` / collection error (module absent).

**Step 1b — implement** `fmt_exposure`, `fmt_share_date`, `build_caption` in
`share.py`:
```python
def fmt_exposure(exposure_s: float) -> str:
    e = float(exposure_s or 0)
    if e <= 0:
        return "— s"
    return f"{int(e)} s" if e == int(e) else f"{e:g} s"

def fmt_share_date(ts: float) -> str:
    lt = time.localtime(ts)
    return f"{time.strftime('%b', lt)} {lt.tm_mday} {lt.tm_year}"

def _clean(s: str, cap: int) -> str:
    s = "".join(c for c in (s or "") if c.isprintable()).strip()
    return s[:cap]

def build_caption(target, exposure_s, sub_count, date_str, gain=None):
    title = _clean(target, 48) or "First light"
    exp = fmt_exposure(exposure_s)
    if sub_count and int(sub_count) > 1:
        exp = f"{exp} × {int(sub_count)}"
    parts = [exp]
    if gain is not None:
        parts.append(f"gain {int(gain)}")
    parts.append(date_str)
    return title, " · ".join(parts)
```
Run the same command. Expected: `5 passed`.

---

### Task T2 — server caption compositor (`share.py` part 2)
**Impl tier: Sonnet** — PIL geometry with the exact algorithm above; deterministic.
**Files:** `server/astrodeck/imaging/share.py`, `server/tests/test_share.py`.

**Step 2a — failing test.** Append to `test_share.py`:
```python
import io
import numpy as np
from PIL import Image
from astrodeck.imaging import compose_share_jpeg, to_jpeg

def _base_jpeg(w=1600, h=900):
    grad = np.tile(np.linspace(0, 65535, w, dtype=np.uint16), (h, 1))
    return to_jpeg(grad)[0]                       # (bytes, w, h) -> bytes

def test_compose_shape_and_band():
    base = _base_jpeg(1600, 900)
    out, ow, oh = compose_share_jpeg(base, "M42", "120 s × 30 · Jul 22 2026")
    im = Image.open(io.BytesIO(out))
    assert im.format == "JPEG" and im.mode == "RGB"
    assert ow == 1080                              # downscaled to phone width
    scaled_h = round(900 * 1080 / 1600)            # 608
    assert oh > scaled_h                           # caption band added
    assert (im.width, im.height) == (ow, oh)

def test_compose_no_upscale():
    base = _base_jpeg(320, 240)                    # narrower than max_width
    _, ow, _ = compose_share_jpeg(base, "x", "y")
    assert ow == 320

def test_compose_draws_text_on_black():
    # black photo -> any bright pixels in the band prove the caption was drawn
    black = to_jpeg(np.zeros((200, 600), dtype=np.uint16))[0]
    out, ow, oh = compose_share_jpeg(black, "TITLE", "detail line", max_width=600)
    a = np.asarray(Image.open(io.BytesIO(out)).convert("L"))
    band = a[round(200 * 600 / 600):, :]           # rows below the photo
    assert band.max() > 120                         # text pixels present

def test_compose_deterministic():
    base = _base_jpeg(800, 600)
    assert compose_share_jpeg(base, "M42", "d")[0] == compose_share_jpeg(base, "M42", "d")[0]
```
Run:
```
server/.venv/Scripts/pytest.exe server/tests/test_share.py -n0 -q
```
Expected: the 4 new tests fail (`compose_share_jpeg` absent), T1's 5 still pass.

**Step 2b — implement** `compose_share_jpeg` per the §1.3 algorithm
(`Image.open(...).convert("RGB")`; conditional BILINEAR resize; `load_default(px)`
fonts; new canvas fill `(11,13,17)`; paste; accent line; `draw.text` title/detail;
right-aligned wordmark via `draw.textlength`; `save("JPEG", quality=...)`).
Run the same command. Expected: `9 passed`.

---

### Task T3 — export + route
**Impl tier: Sonnet** — re-export + a route that mirrors `/png` (`app.py:2507`).
**Files:** `server/astrodeck/imaging/__init__.py`, `server/astrodeck/api/app.py`,
`server/tests/test_monitor_telemetry.py`.

**Step 3a — export.** Add `build_caption, compose_share_jpeg, fmt_exposure,
fmt_share_date` to the `from .processing import` sibling import and to `__all__`
in `imaging/__init__.py` (import them `from .share import ...`).

**Step 3b — route.** In `app.py`, add near the other preview imports:
`from ..imaging import build_caption, compose_share_jpeg, fmt_share_date` and
`from ..naming import sanitize_component`. Then, after the `/png` route
(`:2519`):
```python
@app.get("/api/preview/{preview_id}/share.jpg", dependencies=[Depends(require(CAP_VIEW_PREVIEW))])
@declare(CAP_VIEW_PREVIEW)
async def preview_share(preview_id: int, target: str = "", subs: int = 0):
    """Captioned, phone-sized shareable JPEG of this frame (NOV-11).

    Composites a caption band (target · exposure×count · date) onto the
    already-stretched display bytes. Caption carries NO location — target,
    exposure, count, gain and date only."""
    entry = hub.previews.get(preview_id)
    if entry is None:
        raise HTTPException(404, "preview expired")
    m = entry.meta
    date_str = fmt_share_date(m.get("ts") or time.time())
    title, detail = build_caption(
        target or None, m.get("exposure_s", 0.0),
        subs or None, date_str, m.get("gain"))
    base = entry.lossless or entry.display
    jpeg, _w, _h = await asyncio.to_thread(compose_share_jpeg, base, title, detail)
    safe = sanitize_component(target, "loose") or f"preview_{preview_id}"
    return Response(jpeg, media_type="image/jpeg", headers={
        **_PREVIEW_CACHE,
        "Content-Disposition": f'attachment; filename="firstlight_{safe}.jpg"'})
```

**Step 3c — route-matrix test.** In `test_monitor_telemetry.py`, in the block
that already published a preview (`:257`–`:263`), add:
```python
        rs = client.get(f"/api/preview/{pid}/share.jpg")
        assert rs.status_code == 200
        assert rs.headers["content-type"] == "image/jpeg"
        assert "attachment" in rs.headers.get("content-disposition", "")
        assert client.get(f"/api/preview/{pid}/share.jpg?target=M42&subs=30").status_code == 200
```
Run:
```
server/.venv/Scripts/pytest.exe server/tests/test_share.py server/tests/test_monitor_telemetry.py -n0 -q
```
Expected: all pass (9 unit + the monitor-telemetry suite green).

---

### Task T4 — client `shareQuery` (pure + tsx test)
**Impl tier: Sonnet** — small URL logic; the only genuine rule is "subs only > 1".
**Files:** `ui/src/lib/share.ts` (new), `ui/src/lib/__tests__/share.test.ts` (new).

**Step 4a — failing test** `ui/src/lib/__tests__/share.test.ts` (eta.test.ts harness):
```ts
import { shareQuery } from "../share";
let passed = 0, failed = 0; const failures: string[] = [];
function test(n: string, fn: () => void){try{fn();passed++;}catch(e){failed++;failures.push(`✗ ${n}: ${(e as Error).message}`);}}
function eq<T>(a: T, b: T, m=""){ if(a!==b) throw new Error(`${m} expected ${String(b)}, got ${String(a)}`); }

test("empty when nothing", () => eq(shareQuery(undefined, undefined), ""));
test("target only", () => eq(shareQuery("M42", 1), "?target=M42"));
test("subs only when > 1", () => eq(shareQuery("M42", 30), "?target=M42&subs=30"));
test("drops subs at 1", () => eq(shareQuery("M42", 1), "?target=M42"));
test("url-encodes spaces", () => eq(shareQuery("My Comet", 0), "?target=My+Comet"));

console.log(`${passed} passed, ${failed} failed`);
if (failed) { for (const f of failures) console.error(f); process.exit(1); }
```
Run:
```
cd ui && npx tsx src/lib/__tests__/share.test.ts
```
Expected: failure (module absent).

**Step 4b — implement** `ui/src/lib/share.ts`:
```ts
// Query string for GET /api/preview/{id}/share.jpg. Caption target + sub count
// live only in SequenceState (never on PreviewInfo), so the client forwards them.
export function shareQuery(target?: string, subs?: number): string {
  const p = new URLSearchParams();
  if (target && target.trim()) p.set("target", target.trim());
  if (subs && subs > 1) p.set("subs", String(subs));
  const s = p.toString();
  return s ? `?${s}` : "";
}
```
Run the same command. Expected: `5 passed, 0 failed`. Then `cd ui && npx tsc -b`
(clean).

---

### Task T5 — toolbar menuitem + LivePreview threading (thin render)
**Impl tier: Sonnet** — additive prop + one `<a>` + one store read; typecheck-verified.
**Files:** `ui/src/components/preview/PreviewToolbar.tsx`,
`ui/src/components/preview/LivePreview.tsx`.

**Step 5a — PreviewToolbar.** Add `shareMeta?: { target?: string; subs?: number }`
to the props type (after `linkDown` `:74`) and destructure it (`:62`). Import
`shareQuery` (`import { shareQuery } from "../../lib/share";`). Inside the open
menu (`{dlOpen && id != null && (` `:179`), as the **first** child of the
`role="menu"` div (before the Stretched-PNG block `:181`):
```tsx
<a
  role="menuitem"
  href={u(`/api/preview/${id}/share.jpg${shareQuery(shareMeta?.target, shareMeta?.subs)}`)}
  download={`firstlight_${id}.jpg`}
  className="btn btn-accent !justify-start !px-2 !py-1.5 text-[11px] inline-flex items-center gap-1"
  onClick={() => setDlOpen(false)}
>
  <Icon name="capture" size={11} /> Save first light
</a>
```
(Always enabled — the endpoint composites from `display`, which every in-ring
frame has, so there is no 404 to honest-disable against.)

**Step 5b — LivePreview.** Add `const sequence = useStore((s) => s.sequence);`
beside the other `useStore` reads (`:54`–`:57`) and pass to the toolbar
(`:102`–`:114`):
```tsx
shareMeta={{ target: sequence.target, subs: sequence.progress?.frames_done }}
```

Run:
```
cd ui && npx tsc -b
```
Expected: clean (no output / success). This typecheck is the render verification
for T5 (no jsdom).

---

### Final verification (all)
```
server/.venv/Scripts/pytest.exe server/tests/test_share.py server/tests/test_monitor_telemetry.py -n0 -q
cd ui && npx tsx src/lib/__tests__/share.test.ts && npx tsc -b
```
Expected: pytest green; `5 passed, 0 failed`; clean `tsc -b`. Stage only the seven
named files (never `git add -A`).

---

## 4. Open decisions (with recommendations)

1. **New module `imaging/share.py` vs adding to `processing.py`.**
   *Recommendation: new module.* processing.py is the stretch/encode SSOT;
   caption compositing is a distinct concern and matches the existing
   `clouds.py`/`stars.py`/`fitsio.py` modularity. Re-exported through
   `imaging/__init__.py` so callers see one namespace.

2. **Base image: `lossless` (when held) vs always `display`.**
   *Recommendation: `entry.lossless or entry.display`.* The lossless PNG (latest
   1–2 frames) avoids a JPEG→JPEG recompress and is the frame the novice most
   likely just shot; older frames fall back to `display` cleanly. Zero extra
   memory (both already retained).

3. **Count semantics — `frames_done` (plan-wide) vs per-target subs.**
   *Recommendation: pass `progress.frames_done`.* For a first-night single-target
   run it equals the target's subs; it is honest ("30 subs so far") and needs no
   new server plumbing. When no sequence is running (`sequence.target` undefined),
   `subs` is 0 → caption omits "× N" and titles "First light". Revisit if
   multi-target first-nights become common (would need a per-target counter in
   the event).

4. **Phone width 1080 / quality 90.**
   *Recommendation: keep 1080×q90 as endpoint defaults.* 1080 is the common phone
   share width and never upscales our ≤1400 preview; q90 keeps the caption crisp
   at a small file. Exposed as function kwargs so a future "size" query param is
   trivial, but not wired now (YAGNI).

5. **A toast on save?**
   *Recommendation: no toast.* It is a native browser download via `<a download>`
   (same idiom as the existing three exports, which fire no toast). Adding one
   would diverge from the established menu behavior for no user benefit.

6. **Font: `load_default(px)` vs vendoring a TTF.**
   *Recommendation: `load_default(px)`.* Verified scalable on Pillow 12.2.0 (the
   deployed stack), needs no asset, and renders a clean caption. If a future
   Pillow downgrade loses scalable `load_default`, vendoring a single DejaVu TTF
   is the fallback — but do not add the asset speculatively.
