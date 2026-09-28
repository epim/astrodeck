"""probe.py -- Playwright walk of the AstroDeck UI, driven by a JSON route list.

Run with the SYSTEM python (has Playwright + Chromium; the server venv does
NOT -- see server_ctl.py, which owns the server process and has none of this
tool's dependencies).

Guards against the four failure modes recorded in
C:\\Users\\bear\\.claude\\projects\\C--Users-bear-astro\\memory\\astrodeck-ui-probe-traps.md
and the vacuity lesson in verify-on-the-real-thing.md:

  1. Desktop rail vs phone bottom-nav duplicate the SAME labels, only one
     visible at a time. Every text lookup here (clicks AND markers) is
     filtered to VISIBLE matches only (`_visible_matches`) -- a hidden 0x0
     match never counts as "found".
  2. A stale/expired session lands on the sign-in page, which has no
     overflow, no panels, no errors -- a perfect false pass. `_vacuity_guard`
     aborts the route the instant "sign in to control" or "display
     disconnected" appears in the body, or the body is suspiciously short, or
     the header never rendered the ASTRODECK wordmark.
  3. Clicking is not the same as arriving: every route asserts a
     VIEW-SPECIFIC marker after its clicks, chosen (see routes_classic.json's
     _marker_notes) to NOT collide with any nav label -- a marker that is
     ALSO a nav label would false-pass from the nav alone, even stuck on the
     wrong view.
  4. A blank page must ABORT, not pass. A route that fails vacuity still gets
     a screenshot (for evidence) but skips clicks/marker/overflow -- there is
     nothing meaningful to click or measure on a blank shell, and pretending
     otherwise just buries the real failure under noise.
  5. Present is not usable (#189 S4, routes_s4_frame.json). Playwright's
     is_visible() is true for an element clipped to nothing by a scroller and
     for one covered by another control, so a route may also ask that labels
     and controls answer a HIT TEST at their centre (`labels`, `reachable`),
     that a box is big enough to be the thing it claims and, where asked,
     clear of the screen's edges (`boxes`, `min_inset`), that text
     is neither clipped nor wrapped (`text_intact`), and that a control locked
     on a server answer is locked while that answer is held and unlocks once
     it arrives (`gate`). A route may be limited to some widths (`widths`), a
     click step may be `required` (and a step may be a `goto` or a
     `wait_for`), and a route file may `seed` the server before its walk.
     Each is described where it is implemented.

Usage:
    python tools/ui_probe/probe.py --routes tools/ui_probe/routes_classic.json \\
        --widths 390,820,1440 --out .probe/out --port 8801

    # role-gated run (server started with server_ctl.py start --auth):
    python tools/ui_probe/probe.py --routes tools/ui_probe/routes_classic.json \\
        --widths 390,820,1440 --out .probe/out --port 8801 \\
        --auth --role operator --creds .probe/cfg/probe_users.json

Exit code is non-zero if any route failed at any width.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]

# width -> (height, is_mobile/has_touch). Fixed per the spec this harness was
# built against; an unlisted width falls back to a synthesised desktop-shaped
# viewport (see _viewport_for).
WIDTH_PROFILES: dict[int, dict[str, Any]] = {
    390: {"height": 844, "is_mobile": True, "has_touch": True},
    820: {"height": 1180, "is_mobile": False, "has_touch": False},
    1440: {"height": 900, "is_mobile": False, "has_touch": False},
}

FORBIDDEN_SUBSTRINGS = ["sign in to control", "display disconnected"]
MIN_BODY_CHARS = 200
WORDMARK = "ASTRODECK"

# A testid used to count as "visible" the instant Playwright's own
# is_visible() said so -- true for a 0-opacity element AND for one collapsed
# to a couple of pixels by layout (measured 2026-09-10: every Dial on the
# mount sheet at 820px had a 328 x 2 px box, its own children taller than
# that, because `.nx-dial { overflow: hidden }` zeroed a flex item's
# automatic min-height in `.nx-sheet-body`'s flex column -- see
# ui/src/next/next.css and shellCss.test.ts). Playwright's is_visible() does
# not look at size at all, so this shipped on every route and every width
# without failing the probe. MIN_VISIBLE_PX is the floor below which a
# "visible" TESTID element is almost certainly a collapsed control rather
# than a small-but-real one -- 16px is smaller than any real tap target or
# readout in this UI (the smallest deliberate glyph is the 44px touch
# target's own icon), so it flags a genuine collapse without flagging
# legitimate small elements.
#
# This 16x16 rule applies ONLY to `"testid"` checks -- the controls the new
# UI (`next.css`/`ui/src/next/**`) emits. It does NOT apply to text
# `"marker"` checks: a marker matches PROSE (a heading, a nav caption, a
# panel title), and legitimate prose is routinely under 16px tall -- e.g.
# `#/classic`'s "Equipment" marker matches the desktop rail's small nav-icon
# caption, measured 63.8 x 13.5 px, which is real, on-screen, and correctly
# sized text, not a collapsed control (found 2026-09-10 when the 16x16 rule
# was first applied to markers too and false-failed this route). Markers
# instead use MIN_MARKER_HEIGHT_PX: visibility must hold (Playwright's own
# is_visible(), as before) AND the box must clear a much lower height floor,
# just enough to catch a genuinely zero/near-zero-height text node (the same
# defect SHAPE as the dial, applied to text) without flagging real small
# captions. Width is not checked for markers at all -- a narrow but readable
# word is not a defect.
MIN_VISIBLE_PX = 16
MIN_MARKER_HEIGHT_PX = 8


def _box_for(el) -> dict[str, float] | None:
    """The element's bounding box in CSS px, or None if it cannot be measured
    (detached, no layout box -- e.g. `display: none`)."""
    try:
        box = el.bounding_box()
    except Exception:
        return None
    if box is None:
        return None
    return {"width": round(box["width"], 1), "height": round(box["height"], 1)}


def _large_enough(box: dict[str, float] | None, min_px: int = MIN_VISIBLE_PX) -> bool:
    """TESTID rule: a box counts as genuinely visible only if BOTH dimensions
    clear MIN_VISIBLE_PX -- a 328 x 2 px box (the measured dial defect) is
    wide enough to look fine in a width-only check and must fail on height."""
    return box is not None and box["width"] >= min_px and box["height"] >= min_px


def _tall_enough(box: dict[str, float] | None, min_px: int = MIN_MARKER_HEIGHT_PX) -> bool:
    """MARKER rule: a text box only has to clear a low height floor -- wide
    enough to still allow a genuinely small-but-real caption (see
    MIN_MARKER_HEIGHT_PX above) while still failing a zero/near-zero-height
    text node. No width check: a narrow word is not a defect."""
    return box is not None and box["height"] >= min_px


def _viewport_for(width: int) -> dict[str, Any]:
    profile = WIDTH_PROFILES.get(width)
    if profile is None:
        profile = {"height": max(600, round(width * 0.65)),
                   "is_mobile": width < 640, "has_touch": width < 640}
    return profile


def _join_url(base: str, route_url: str) -> str:
    """Join a server base URL with a route's `url` field. Routes carry either
    '/' (classic UI -- no URL routing, always the app root) or a hash route
    like '#/sky' (routes_next.json). Always inserts exactly one '/' between
    base and the route so 'http://host:port' + '#/sky' -> '.../#/sky', never
    a bare 'http://host:port#/sky' (no slash) or '...//#/sky' (doubled)."""
    base = base.rstrip("/")
    if route_url in ("", "/"):
        return base + "/"
    return base + "/" + route_url.lstrip("/")


# ------------------------------------------------------------- text lookups

def _visible_matches(page, text: str, exact: bool = False) -> list:
    """Every VISIBLE element matching `text`. Trap #1: a match that exists in
    the DOM but is hidden (the desktop rail at phone width, the phone bottom
    nav at desktop width) must never count.

    `exact=False` (the default, used for view MARKERS) is Playwright's
    case/whitespace-insensitive SUBSTRING match. `exact=True` (used for NAV
    CLICKS, see _run_clicks) is case-sensitive whole-string match -- measured
    necessary on 2026-09-10: substring "Monitor" also matches the visible
    "Safety monitor" device-role label still mounted underneath the More
    sheet overlay (is_visible() reports true for content covered by an
    overlay -- occlusion is a click-time actionability check, not a
    visibility one), and the click landed on the wrong element. Every nav
    label this harness clicks is a short, exact, known string straight from
    the source (BottomNav.PRIMARY / App.tsx NAV / NavMoreSheet.OVERFLOW_VIEWS),
    so whole-string matching costs nothing and removes the ambiguity class."""
    loc = page.get_by_text(text, exact=exact)
    out = []
    try:
        n = loc.count()
    except Exception:
        return out
    for i in range(n):
        el = loc.nth(i)
        try:
            if el.is_visible():
                out.append(el)
        except Exception:
            continue
    return out


def _wait_for_visible_text(page, text: str, timeout_ms: int = 8000,
                            poll_ms: int = 200) -> tuple[Any | None, dict[str, float] | None]:
    """Polls for a marker that is both VISIBLE and at least
    MIN_MARKER_HEIGHT_PX tall (the MARKER rule -- text, not a control; see
    the comment on MIN_MARKER_HEIGHT_PX). Returns (element, box):
      - (None, None)   -- no visible match ever appeared at all
      - (element, box) -- a visible match appeared; the caller must still
        check `_tall_enough(box)`, because a match that is visible but
        never clears MIN_MARKER_HEIGHT_PX is returned here too (as the last
        visible-but-collapsed match seen), so the caller can report the
        measured box instead of a bare "not found"."""
    deadline = time.monotonic() + timeout_ms / 1000.0
    last_el = None
    last_box = None
    while time.monotonic() < deadline:
        matches = _visible_matches(page, text, exact=False)
        if matches:
            last_el = matches[0]
            last_box = _box_for(last_el)
            if _tall_enough(last_box):
                return last_el, last_box
        page.wait_for_timeout(poll_ms)
    return last_el, last_box


def _visible_css_matches(page, selector: str) -> list:
    """Same shape as `_visible_matches`, for a CSS selector instead of a text
    lookup -- used for `[data-testid=...]` assertions (routes_next.json).
    Visible-only for the same reason as trap #1: the shell renders both a
    desktop rail and a phone bottom nav (and, more relevant here, a sheet's
    underlying hub screen stays mounted while a sheet is open on top of it),
    so a hidden-but-present testid must never count as "found"."""
    loc = page.locator(selector)
    out = []
    try:
        n = loc.count()
    except Exception:
        return out
    for i in range(n):
        el = loc.nth(i)
        try:
            if el.is_visible():
                out.append(el)
        except Exception:
            continue
    return out


def _wait_for_visible_testid(page, testid: str, timeout_ms: int = 8000,
                              poll_ms: int = 200) -> tuple[Any | None, dict[str, float] | None]:
    """Same contract as `_wait_for_visible_text`, for a `[data-testid=...]`
    selector: (None, None) means no visible match ever appeared; otherwise
    the returned box may still be smaller than MIN_VISIBLE_PX and the caller
    must check `_large_enough(box)` itself."""
    selector = f'[data-testid="{testid}"]'
    deadline = time.monotonic() + timeout_ms / 1000.0
    last_el = None
    last_box = None
    while time.monotonic() < deadline:
        matches = _visible_css_matches(page, selector)
        if matches:
            last_el = matches[0]
            last_box = _box_for(last_el)
            if _large_enough(last_box):
                return last_el, last_box
        page.wait_for_timeout(poll_ms)
    return last_el, last_box


# ------------------------------------------------------------------- guards

def _vacuity_guard(page) -> list[str]:
    """Returns a list of failure reasons (empty == the page is genuinely
    live). See module docstring point 2/4."""
    reasons: list[str] = []
    try:
        body_text = page.locator("body").inner_text(timeout=5000)
    except Exception as exc:
        return [f"could not read body text: {exc}"]

    lower = body_text.lower()
    for forbidden in FORBIDDEN_SUBSTRINGS:
        if forbidden in lower:
            reasons.append(f"vacuity: body contains forbidden text {forbidden!r} "
                           f"(false-pass trap: this reads clean otherwise)")

    stripped_len = len(body_text.strip())
    if stripped_len < MIN_BODY_CHARS:
        reasons.append(f"vacuity: body text only {stripped_len} chars "
                       f"(< {MIN_BODY_CHARS}) -- looks blank")

    header = page.locator("header")
    try:
        header_count = header.count()
    except Exception:
        header_count = 0
    if header_count == 0:
        reasons.append("vacuity: no <header> element at all")
    else:
        try:
            header_text = header.first.inner_text(timeout=3000)
        except Exception:
            header_text = ""
        if WORDMARK not in header_text.replace(" ", "").replace("\n", "").upper():
            reasons.append(f"vacuity: header does not contain the {WORDMARK!r} "
                           f"wordmark (header text: {header_text[:120]!r})")
    return reasons


def _measure_overflow(page) -> dict[str, Any]:
    return page.evaluate(
        """() => {
          const doc = document.documentElement;
          const vw = doc.clientWidth;
          const overflow = doc.scrollWidth - vw;
          const offenders = [];
          if (overflow > 2) {
            const all = document.querySelectorAll('body *');
            for (const el of all) {
              const r = el.getBoundingClientRect();
              const over = Math.max(r.right - vw, -r.left);
              if (over > 2) {
                offenders.push({over: Math.round(over),
                                 html: (el.outerHTML || '').slice(0, 200)});
              }
            }
            offenders.sort((a, b) => b.over - a.over);
          }
          return {scrollWidth: doc.scrollWidth, clientWidth: vw,
                   overflow, offenders: offenders.slice(0, 5)};
        }"""
    )


# ---------------------------------------------- usable, not merely present
#
# Module docstring point 5. Every check below exists because a probe that
# stopped at is_visible() passed a page a person could not use:
# probe-visible-is-not-sized (four dials drawn as 2 px lines through a green
# probe), the README's covered-click trap (a label under an open sheet still
# reads visible), and on 2026-09-26 the Target modal's MOVE SKY, which read
# visible at 390 px while SkyCanvas's touch chip lay over it (#189 S4).

# What is at an element's centre, asked of the browser: `inside` is true only
# when the topmost element there is the element itself or one of its
# descendants. A covered control fails (the covering element is named in
# `hit`), and so does one clipped away by a scroller or pushed off the
# viewport (`in_view`), which is the case is_visible() calls visible.
HIT_TEST_JS = """(el) => {
  const r = el.getBoundingClientRect();
  const x = r.left + r.width / 2, y = r.top + r.height / 2;
  const inView = x >= 0 && y >= 0 && x < window.innerWidth && y < window.innerHeight;
  const h = inView ? document.elementFromPoint(x, y) : null;
  return {x: Math.round(x), y: Math.round(y), in_view: inView,
          inside: !!h && (h === el || el.contains(h)),
          hit: h ? (h.outerHTML || h.nodeName).slice(0, 160) : null};
}"""

# Where an element's TEXT is drawn, asked of the browser through a Range (the
# glyph boxes, not the element's box, which for an inline span says nothing
# about where its glyphs land): `lines` counts the distinct line boxes, and
# `clipped` names the first ancestor whose overflow cuts a glyph box, by how
# many px. The clip is the ancestor's PADDING box (client area), which is
# where `overflow` clips. Measured case (2026-09-26): the PANELS bar's
# "0/80" sat in a 16 px `overflow: hidden` bar on the row's 21 px line, and
# its glyphs ran 4 px past the bar's bottom edge.
TEXT_INTACT_JS = """(el) => {
  const range = document.createRange();
  range.selectNodeContents(el);
  const rects = Array.from(range.getClientRects()).filter(r => r.width > 0 && r.height > 0);
  const text = (el.textContent || '').trim().slice(0, 60);
  if (!rects.length) return {text, empty: true, lines: 0, clipped: null};
  const minH = Math.min(...rects.map(r => r.height));
  const tops = [];
  for (const r of rects) {
    if (!tops.some(t => Math.abs(t - r.top) < minH / 2)) tops.push(r.top);
  }
  let clipped = null;
  for (let a = el.parentElement; a && !clipped; a = a.parentElement) {
    const cs = getComputedStyle(a);
    if (cs.overflowX === 'visible' && cs.overflowY === 'visible') continue;
    const b = a.getBoundingClientRect();
    const L = b.left + a.clientLeft, T = b.top + a.clientTop;
    const R = L + a.clientWidth, B = T + a.clientHeight;
    for (const r of rects) {
      const over = Math.max(L - r.left, r.right - R, T - r.top, r.bottom - B);
      if (over > 0.5) {
        clipped = {by: (a.getAttribute('class') || a.nodeName).slice(0, 80),
                   over_px: Math.round(over * 10) / 10};
        break;
      }
    }
  }
  return {text, empty: false, lines: tops.length, clipped};
}"""


# Scroll an element into view the way a PERSON can: only the containers
# whose overflow is `auto` or `scroll`, and the viewport. Playwright's
# scroll_into_view_if_needed (and the DOM's scrollIntoView) also scroll every
# `overflow: hidden` ancestor, which is a scroll container to script though
# no finger can move it - and that UN-CLIPS the very text a clip check is
# looking for. Measured 2026-09-26: the PANELS bar's "0/80" sat 6 px down a
# 16 px `overflow: hidden` bar, cut 4 px at the bottom; scrolled into view
# by Playwright, the bar itself scrolled 4 px and the text measured whole.
BRING_INTO_VIEW_JS = """(el) => {
  for (let a = el.parentElement; a; a = a.parentElement) {
    const cs = getComputedStyle(a);
    const sy = /(auto|scroll)/.test(cs.overflowY), sx = /(auto|scroll)/.test(cs.overflowX);
    if (!sy && !sx) continue;
    const r = el.getBoundingClientRect(), b = a.getBoundingClientRect();
    if (sy && (r.top < b.top || r.bottom > b.bottom)) {
      a.scrollTop += (r.top + r.bottom) / 2 - (b.top + b.bottom) / 2;
    }
    if (sx && (r.left < b.left || r.right > b.right)) {
      a.scrollLeft += (r.left + r.right) / 2 - (b.left + b.right) / 2;
    }
  }
  const r = el.getBoundingClientRect();
  if (r.top < 0 || r.bottom > window.innerHeight || r.left < 0 || r.right > window.innerWidth) {
    window.scrollBy((r.left + r.right) / 2 - window.innerWidth / 2,
                    (r.top + r.bottom) / 2 - window.innerHeight / 2);
  }
}"""


def _hit_test(el) -> dict[str, Any] | None:
    try:
        return el.evaluate(HIT_TEST_JS)
    except Exception:
        return None


def _bring_into_view(page, el) -> None:
    """Scroll `el` into its scroller's view (and the viewport's) before a hit
    test or a clip check: an element legitimately below the fold of a
    scroller is not a defect, one the scroller cannot bring into view is.
    Through BRING_INTO_VIEW_JS, never Playwright's own scroll (see there)."""
    try:
        el.evaluate(BRING_INTO_VIEW_JS)
    except Exception:
        pass
    page.wait_for_timeout(150)


def _check_labels(page, spec: dict, out_dir: Path, shot_stem: str) -> tuple[list[dict], list[str]]:
    """`labels: {"within": <selector>, "texts": [...], "shot_each": bool}`.

    Each text must match EXACTLY (case-sensitive, whole string) inside the
    first visible `within` element - a label on the screen UNDER a modal has
    the same words (the stage list's RUN button under the Target modal's RUN
    heading) and must not count. Each is scrolled into view, and must then be
    at least MIN_MARKER_HEIGHT_PX tall and answer the hit test: a heading in a
    scroller that has collapsed to nothing reads visible to Playwright and is
    unreachable by a person. `shot_each` saves `<shot>-label-<text>.png`, the
    evidence that each section rendered, not only that it exists."""
    results: list[dict] = []
    reasons: list[str] = []
    within = spec.get("within")
    scope = page
    if within:
        found = _visible_css_matches(page, within)
        if not found:
            return results, [f"labels: container {within!r} is not visible"]
        scope = found[0]
    for text in spec.get("texts", []):
        loc = scope.get_by_text(text, exact=True)
        visible = []
        try:
            for i in range(loc.count()):
                if loc.nth(i).is_visible():
                    visible.append(loc.nth(i))
        except Exception:
            pass
        if not visible:
            results.append({"text": text, "ok": False, "why": "not visible"})
            reasons.append(f"label {text!r} is not visible inside {within!r}")
            continue
        el = visible[0]
        _bring_into_view(page, el)
        box = _box_for(el)
        hit = _hit_test(el)
        ok = _tall_enough(box) and bool(hit and hit.get("inside"))
        results.append({"text": text, "ok": ok, "box": box, "hit": hit})
        if not _tall_enough(box):
            reasons.append(f"label {text!r} is present but collapsed (box {box}, "
                           f"need >= {MIN_MARKER_HEIGHT_PX}px tall)")
        elif not (hit and hit.get("inside")):
            reasons.append(f"label {text!r} is covered or clipped: the point at its "
                           f"centre is not the label ({hit})")
        if spec.get("shot_each"):
            safe = "".join(c if c.isalnum() else "_" for c in text)
            try:
                page.screenshot(path=str(out_dir / f"{shot_stem}-label-{safe}.png"))
            except Exception as exc:
                reasons.append(f"screenshot of label {text!r} failed: {exc}")
    return results, reasons


# How far an element's box sits inside the viewport on its left, top and right
# edges, in CSS px. The width is the document's client width, so a classic
# scrollbar is not counted as room. The bottom is left out: a card whose
# sheet is taller than the screen legitimately runs below it.
EDGES_JS = """(el) => {
  const r = el.getBoundingClientRect();
  const w = document.documentElement.clientWidth;
  const q = (v) => Math.round(v * 10) / 10;
  return {left: q(r.left), top: q(r.top), right: q(w - r.right)};
}"""


def _check_boxes(page, specs: list[dict]) -> tuple[list[dict], list[str]]:
    """`boxes: [{"selector", "min_width", "min_height", "min_inset"}]`: the
    first visible match must be at least that big. The floor is the route's,
    derived there from the layout it grades (routes_s4_frame.json computes its
    sky floor from spec 2.2), because what is "non-trivial" depends on what
    the box is: MIN_VISIBLE_PX's 16 x 16 is a floor for a control, and a sky
    16 px tall is a failure.

    `min_inset` also asks that the box sit at least that many px inside the
    viewport on its left, top and right edges (EDGES_JS). A size floor cannot
    see a card drawn against the screen edge: measured 2026-09-28, the #/next
    framing sheet's waiting card sat at x = 0, y = 0 in the phone's sheet
    slot, 390 px wide - full width, visible, its BACK reachable - with its
    dashed border on the top and left edges of the screen, because the sheet
    gave it no frame (FlowFrameSheet.tsx)."""
    results: list[dict] = []
    reasons: list[str] = []
    for spec in specs:
        sel = spec["selector"]
        found = _visible_css_matches(page, sel)
        box = _box_for(found[0]) if found else None
        need_w, need_h = spec.get("min_width", 0), spec.get("min_height", 0)
        ok = box is not None and box["width"] >= need_w and box["height"] >= need_h
        result: dict[str, Any] = {"selector": sel, "ok": ok, "box": box,
                                  "need": {"width": need_w, "height": need_h}}
        inset = spec.get("min_inset")
        inset_ok = True
        edges = None
        if inset is not None and box is not None:
            try:
                edges = found[0].evaluate(EDGES_JS)
            except Exception:
                edges = None
            inset_ok = edges is not None and min(edges.values()) >= inset
            result.update(edges=edges, ok=ok and inset_ok)
            result["need"]["inset"] = inset
        results.append(result)
        if box is None:
            reasons.append(f"box {sel!r} is not visible")
            continue
        if not ok:
            reasons.append(f"box {sel!r} is {box['width']} x {box['height']}px, "
                           f"need >= {need_w} x {need_h}px")
        if not inset_ok:
            reasons.append(f"box {sel!r} sits {edges} px from the viewport's left, "
                           f"top and right edges, need >= {inset}px on each")
    return results, reasons


def _check_reachable(page, selectors: list[str]) -> tuple[list[dict], list[str]]:
    """`reachable: [<selector>, ...]`: every visible match must answer the hit
    test once scrolled into view. A control another control lies over is one
    a finger cannot press, however visible Playwright calls it."""
    results: list[dict] = []
    reasons: list[str] = []
    for sel in selectors:
        found = _visible_css_matches(page, sel)
        if not found:
            results.append({"selector": sel, "ok": False, "why": "not visible"})
            reasons.append(f"control {sel!r} is not visible")
            continue
        for el in found:
            _bring_into_view(page, el)
            hit = _hit_test(el)
            ok = bool(hit and hit.get("inside"))
            results.append({"selector": sel, "ok": ok, "hit": hit})
            if not ok:
                reasons.append(f"control {sel!r} is covered: the point at its centre "
                               f"is not the control ({hit})")
    return results, reasons


def _check_text_intact(page, specs: list[dict]) -> tuple[list[dict], list[str]]:
    """`text_intact: [{"selector", "one_line": bool}]`: in every visible match,
    scrolled into view, no glyph is cut by an ancestor's overflow and, with
    `one_line`, the text sits on one line. A match with no text is skipped
    (an empty cell has nothing to clip). Both are defects is_visible() cannot
    see: the element's own box is fine in each."""
    results: list[dict] = []
    reasons: list[str] = []
    for spec in specs:
        sel = spec["selector"]
        found = _visible_css_matches(page, sel)
        if not found:
            results.append({"selector": sel, "ok": False, "why": "not visible"})
            reasons.append(f"text {sel!r} is not visible")
            continue
        failed: list[str] = []
        measured = 0
        for el in found:
            _bring_into_view(page, el)
            try:
                got = el.evaluate(TEXT_INTACT_JS)
            except Exception as exc:
                got = {"error": str(exc)}
            if got.get("empty"):
                continue
            measured += 1
            ok = (not got.get("error") and got.get("clipped") is None
                  and (not spec.get("one_line") or got.get("lines") == 1))
            results.append({"selector": sel, "ok": ok, **got})
            if got.get("error"):
                failed.append(f"text {sel!r} could not be measured: {got['error']}")
            elif got.get("clipped") is not None:
                failed.append(f"text {got['text']!r} ({sel}) is clipped "
                              f"{got['clipped']['over_px']}px by {got['clipped']['by']!r}")
            elif spec.get("one_line") and got.get("lines") != 1:
                failed.append(f"text {got['text']!r} ({sel}) wraps onto "
                              f"{got['lines']} lines, and must sit on one")
        # One reason per selector, not one per row: six identical lines for a
        # six-row table bury whatever else failed. The results keep them all.
        if failed:
            reasons.append(f"{failed[0]} ({len(failed)} of {measured} with text)")
    return results, reasons


# ------------------------------------------------------------------- gate
#
# `gate: {"url", "body_contains", "control", "request_timeout_ms",
# "unlock_timeout_ms", "shot_locked"}`: a control that must stay locked until
# a server answer arrives (the Target modal's DONE, spec 2.5: "DONE stays
# locked until the answer for the CURRENT spec is in hand").
#
# "It unlocked" alone proves nothing: a DONE that never locked, or one that
# unlocked on the client's own mirror, reads the same by the time a probe
# looks. So the probe HOLDS the answer. Requests matching `url` whose body
# contains `body_contains` are held from the moment the page opens (others
# matching `url` go straight through - the modal's night card asks the same
# route, and DONE does not wait on it). Once the walk has reached the view:
# at least one request must have been held, the control must read locked
# (aria-disabled="true", HonestButton's lock) while it is, the held requests
# are released, the server must answer 2xx, and the control must then unlock.
# The hold is short on purpose (at most `request_timeout_ms` after the
# marker): the UI's own fetch gives up at 15 s (api.ts `timeoutFor`), and a
# client that timed out unlocks DONE on its mirror, which is a different
# claim.

class _Gate:
    def __init__(self, page, spec: dict) -> None:
        self.spec = spec
        self.holding = True
        self.held: list = []
        self.answers: list[int] = []
        self._held_requests: list = []
        page.route(spec["url"], self._on_route)
        page.on("response", self._on_response)

    def _gated(self, request) -> bool:
        body = request.post_data or ""
        return self.spec.get("body_contains", "") in body

    def _on_route(self, route) -> None:
        if self.holding and self._gated(route.request):
            self.held.append(route)
            self._held_requests.append(route.request)
            return
        try:
            route.continue_()
        except Exception:
            pass

    def _on_response(self, response) -> None:
        if any(response.request is r for r in self._held_requests):
            self.answers.append(response.status)

    def release(self) -> None:
        self.holding = False
        held, self.held = self.held, []
        for route in held:
            try:
                route.continue_()
            except Exception:
                pass


def _control_locked(page, selector: str) -> tuple[bool | None, str | None]:
    """(locked, reason) for the first visible match of `selector`: locked is
    None when there is no such control."""
    found = _visible_css_matches(page, selector)
    if not found:
        return None, None
    try:
        el = found[0]
        return el.get_attribute("aria-disabled") == "true", el.get_attribute("title")
    except Exception:
        return None, None


def _check_gate(page, gate: _Gate, out_dir: Path, shot_stem: str) -> tuple[dict, list[str]]:
    spec = gate.spec
    control = spec["control"]
    info: dict[str, Any] = {"url": spec["url"], "control": control}
    reasons: list[str] = []
    deadline = time.monotonic() + spec.get("request_timeout_ms", 5000) / 1000.0
    while not gate.held and time.monotonic() < deadline:
        page.wait_for_timeout(100)
    info["held"] = len(gate.held)
    if not gate.held:
        gate.release()
        reasons.append(f"gate: no request to {spec['url']!r} containing "
                       f"{spec.get('body_contains', '')!r} was made, so nothing "
                       f"shows what {control!r} waits on")
        return info, reasons
    locked, why = _control_locked(page, control)
    info["locked_while_held"] = locked
    info["locked_reason"] = why
    if spec.get("shot_locked"):
        try:
            page.screenshot(path=str(out_dir / f"{shot_stem}-locked.png"))
        except Exception as exc:
            reasons.append(f"screenshot while held failed: {exc}")
    t0 = time.monotonic()
    gate.release()
    if locked is None:
        reasons.append(f"gate: control {control!r} is not visible")
        return info, reasons
    if not locked:
        reasons.append(f"gate: control {control!r} was live while the answer it "
                       f"waits on was still held")
    deadline = t0 + spec.get("unlock_timeout_ms", 8000) / 1000.0
    unlocked = False
    while time.monotonic() < deadline:
        now_locked, _ = _control_locked(page, control)
        if gate.answers and now_locked is False:
            unlocked = True
            break
        page.wait_for_timeout(100)
    info["answers"] = list(gate.answers)
    info["unlocked"] = unlocked
    info["unlocked_after_ms"] = round((time.monotonic() - t0) * 1000) if unlocked else None
    if not gate.answers:
        reasons.append(f"gate: the held request to {spec['url']!r} was released "
                       f"and never answered")
    elif not all(200 <= s < 300 for s in gate.answers):
        reasons.append(f"gate: the server answered {gate.answers}, not 2xx")
    elif not unlocked:
        now_locked, now_why = _control_locked(page, control)
        reasons.append(f"gate: control {control!r} stayed locked after the server "
                       f"answered (reason now: {now_why!r})")
    return info, reasons


# --------------------------------------------------------------- clicking

def _step_matches(page, step: dict) -> list:
    """The visible elements a click step names: `selector` (any Playwright
    selector, e.g. `[data-testid="flow-node-frame"]`) or `text` (exact by
    default, see `_visible_matches`)."""
    if step.get("selector"):
        return _visible_css_matches(page, step["selector"])
    text = step.get("text", "")
    exact = step.get("exact", True)
    if step.get("visible", True):
        return _visible_matches(page, text, exact=exact)
    return [page.get_by_text(text, exact=exact).first]


def _run_clicks(page, clicks: list[dict]) -> list[dict]:
    """Best-effort click sequence. A step whose text has NO visible match is
    logged as skipped rather than failing the route outright -- at some
    widths a step is legitimately not applicable (e.g. 'More' only exists on
    the phone bottom nav). The MARKER assertion afterwards is the real gate:
    if a skipped step mattered, the route lands somewhere wrong and the
    marker will not appear.

    A `required` step is the other kind: a WALK through doors (the list, the
    stage list, the stage editor, the modal), where each step's target only
    exists once the last one opened, and arrives when a lazy chunk does. It
    is polled for `wait_ms` (default 8000) rather than looked for once, a
    miss is logged as `missing` and stops the walk (every later step would
    be looked for on the wrong screen), and `_run_route` fails the route on
    it with the step named. Without this a walk whose third door is gone
    reads as "marker not visible", which names the symptom and not the door.
    `position` clicks at an offset inside the element (a canvas card whose
    centre is a port, say).

    Two steps click nothing. `{"goto": "#/..."}` sets the hash, which is what a
    link inside the app does (a same-document navigation: the store, the
    loaded chunks and the open flow all survive it, as they do for a person
    following a link or pressing Back), so a walk can arrive at a view with
    the app in a state a deep link could never give it. `{"wait_for":
    <selector>}` is a required step that only has to become visible: the
    premise of what comes next, stated and checked (e.g. that one flow's
    framing was on screen before a link to another's)."""
    log: list[dict] = []
    for i, step in enumerate(clicks):
        if "goto" in step:
            page.evaluate("(h) => { window.location.hash = h; }", step["goto"])
            page.wait_for_timeout(300)
            log.append({"text": step["goto"], "action": "goto"})
            continue
        if "wait_for" in step:
            step = {**step, "selector": step["wait_for"], "required": True}
        desc = step.get("selector") or step.get("text", "")
        required = bool(step.get("required"))
        matches = _step_matches(page, step)
        if not matches and required:
            deadline = time.monotonic() + step.get("wait_ms", 8000) / 1000.0
            while not matches and time.monotonic() < deadline:
                page.wait_for_timeout(200)
                matches = _step_matches(page, step)
        if not matches:
            if required:
                log.append({"text": desc, "action": "missing", "required": True,
                            "reason": "no visible match within "
                                      f"{step.get('wait_ms', 8000)} ms"})
                log.extend({"text": _step_desc(s), "action": "not-run"}
                           for s in clicks[i + 1:])
                break
            log.append({"text": desc, "action": "skip",
                       "reason": "no visible match at this width"})
            continue
        if "wait_for" in step:
            log.append({"text": desc, "action": "seen", "matched": len(matches)})
            continue
        try:
            kwargs: dict[str, Any] = {"timeout": 5000}
            if step.get("position"):
                kwargs["position"] = step["position"]
            matches[0].click(**kwargs)
            page.wait_for_timeout(300)
            log.append({"text": desc, "action": "click", "matched": len(matches)})
        except Exception as exc:
            log.append({"text": desc, "action": "click-failed", "error": str(exc),
                        "required": required})
            if required:
                log.extend({"text": _step_desc(s), "action": "not-run"}
                           for s in clicks[i + 1:])
                break
    return log


def _step_desc(step: dict) -> str:
    return (step.get("goto") or step.get("wait_for") or step.get("selector")
            or step.get("text", ""))


# ------------------------------------------------------------------- login

class LoginError(RuntimeError):
    pass


def _login(page, base: str, role: str, creds: dict) -> None:
    if role not in creds:
        raise LoginError(f"no credentials for role {role!r} in creds file "
                         f"(have: {list(creds)})")
    username = creds[role]["username"]
    password = creds[role]["password"]

    try:
        page.goto(base, wait_until="networkidle", timeout=20000)
    except Exception:
        page.goto(base, wait_until="load", timeout=20000)
    page.wait_for_timeout(500)

    user_field = page.get_by_label("Username")
    try:
        user_field.wait_for(state="visible", timeout=8000)
    except Exception:
        raise LoginError(
            "expected a login form (server started with --auth) but no "
            "'Username' field ever appeared -- either auth is not actually "
            "enabled on this server, or the app failed to render at all")

    user_field.fill(username)
    page.get_by_label("Password").fill(password)
    page.get_by_role("button", name="Sign in", exact=True).click()

    # "Landed" used to mean "the text 'Devices' became visible" -- a CLASSIC-UI
    # marker (the Equipment view's inner panel title) that never appears after
    # a routes_next.json login, because the default post-login screen there is
    # the Sky hub, not Equipment. Waiting on a classic-only string here failed
    # every `--auth` run against the new UI, not because login was broken, but
    # because this helper was checking for the wrong app. The UI-agnostic
    # signal both roots share is the login FORM itself going away (the
    # Username field detaching/hiding once `Root()` swaps in the authenticated
    # app shell) -- true whether that shell is classic `App` or `NextApp`.
    deadline = time.monotonic() + 10.0
    form_closed = False
    while time.monotonic() < deadline:
        try:
            if not user_field.is_visible():
                form_closed = True
                break
        except Exception:
            form_closed = True  # detached from the DOM entirely also counts
            break
        page.wait_for_timeout(200)

    if not form_closed:
        # Surface whatever error text the form is showing, if any.
        err_hint = ""
        try:
            err_hint = page.locator("text=/Invalid username or password|Sign-in failed/i") \
                .first.inner_text(timeout=1000)
        except Exception:
            pass
        raise LoginError(
            f"login as {username!r} (role={role}) did not reach the app "
            f"shell within 10s{f' -- form said: {err_hint!r}' if err_hint else ''}")

    # The form closing proves the credentials were accepted; it does not prove
    # something real rendered behind it (a blank/broken landing would also
    # make the form disappear). Reuse the same vacuity guard every route
    # already passes through, rather than inventing a second, weaker check.
    page.wait_for_timeout(300)
    vacuity_reasons = _vacuity_guard(page)
    if vacuity_reasons:
        raise LoginError(
            f"login as {username!r} (role={role}) closed the sign-in form but "
            f"landed on what looks like a blank/broken shell: {vacuity_reasons}")


# ------------------------------------------------------------------ routes

def _run_isolated_route(context, base: str, route: dict, out_dir: Path,
                        width: int) -> dict:
    """One page per route; cookies/storage stay in the authenticated context.

    Hash navigation keeps the old document's requests and console callbacks
    alive. A fresh page retains startup errors for this route without charging
    it for the previous route's delayed failures.
    """
    page = context.new_page()
    try:
        return _run_route(page, base, route, out_dir, width)
    finally:
        page.close()


def _run_route(page, base: str, route: dict, out_dir: Path, width: int) -> dict:
    name = route.get("name") or route.get("shot") or route["url"]
    shot_name = route.get("shot", name)
    target = _join_url(base, route.get("url", "/"))

    console_errors: list[str] = []
    failed_requests: list[dict] = []

    def on_console(msg):
        if msg.type == "error":
            console_errors.append(msg.text)

    def on_pageerror(exc):
        console_errors.append(f"pageerror: {exc}")

    def on_response(resp):
        try:
            status = resp.status
        except Exception:
            return
        if status >= 400:
            failed_requests.append({"status": status, "url": resp.url})

    page.on("console", on_console)
    page.on("pageerror", on_pageerror)
    page.on("response", on_response)

    # Installed BEFORE the page opens: the request it holds may leave the
    # moment the view mounts, long before the walk is done.
    gate = _Gate(page, route["gate"]) if route.get("gate") else None
    width_dir = out_dir / str(width)
    width_dir.mkdir(parents=True, exist_ok=True)

    t0 = time.monotonic()
    reasons: list[str] = []
    click_log: list[dict] = []
    overflow_info: dict[str, Any] = {}
    marker_ok = False
    testid_box: dict[str, float] | None = None
    marker_box: dict[str, float] | None = None
    usable: dict[str, Any] = {}

    try:
        page.goto(target, wait_until="networkidle", timeout=20000)
    except Exception:
        try:
            page.goto(target, wait_until="load", timeout=20000)
            page.wait_for_timeout(1000)
        except Exception as exc:
            reasons.append(f"navigation failed: {exc}")

    page.wait_for_timeout(500)

    vacuity_reasons = _vacuity_guard(page)
    reasons.extend(vacuity_reasons)

    expected_errors = route.get("expected_errors", [])

    testid_ok = None

    if not vacuity_reasons:
        click_log = _run_clicks(page, route.get("click", []))
        for step in click_log:
            if step.get("required") and step["action"] in ("missing", "click-failed"):
                reasons.append(f"required step {step['text']!r} failed: "
                               f"{step.get('reason') or step.get('error')}")
        page.wait_for_timeout(400)

        # A route asserts a data-testid (routes_next.json's primary check, see
        # probe.py's extension for the new UI), a text marker (routes_classic.json's
        # original check), or both -- whichever the route list supplies. At least
        # one is required: a route with neither has nothing gating a false pass.
        testid = route.get("testid")
        marker = route.get("marker")

        if testid:
            found_testid, testid_box = _wait_for_visible_testid(page, testid, timeout_ms=8000)
            if found_testid is None:
                testid_ok = False
                reasons.append(f"testid {testid!r} ([data-testid={testid!r}]) not "
                               f"visible after clicks (click log: {click_log})")
            elif not _large_enough(testid_box):
                # Present, and Playwright's own is_visible() agrees -- but too
                # small to be a real control. This is the mount-dial defect
                # class: `.nx-dial { overflow: hidden }` zeroed a flex item's
                # automatic min-height, so the element measured 328 x 2 px
                # while its own children measured taller than that.
                testid_ok = False
                h = testid_box["height"] if testid_box else "?"
                w = testid_box["width"] if testid_box else "?"
                reasons.append(
                    f"testid {testid!r} ([data-testid={testid!r}]) is {h}px tall "
                    f"- present but collapsed (box {w} x {h}px, need >= "
                    f"{MIN_VISIBLE_PX} x {MIN_VISIBLE_PX}px; click log: {click_log})")
            else:
                testid_ok = True

        if marker:
            found, marker_box = _wait_for_visible_text(page, marker, timeout_ms=8000)
            if found is None:
                marker_ok = False
                reasons.append(f"marker {marker!r} not visible after clicks "
                               f"(click log: {click_log})")
            elif not _tall_enough(marker_box):
                # MARKER rule (not the testid 16x16 rule): a marker matches
                # PROSE, and legitimate prose is routinely under 16px tall
                # (e.g. a nav-icon caption), so a marker only has to clear a
                # low height floor -- just enough to catch a genuinely
                # zero/near-zero-height text node, same defect SHAPE as the
                # dial, without flagging real small captions.
                marker_ok = False
                h = marker_box["height"] if marker_box else "?"
                w = marker_box["width"] if marker_box else "?"
                reasons.append(
                    f"marker {marker!r} is {h}px tall - present but collapsed "
                    f"(box {w} x {h}px, need >= {MIN_MARKER_HEIGHT_PX}px tall; "
                    f"click log: {click_log})")
            else:
                marker_ok = True
        else:
            marker_ok = True  # no text marker required for this route

        if not testid and not marker:
            marker_ok = False
            reasons.append("route defines neither 'testid' nor 'marker' -- "
                           "nothing to assert, so it cannot pass")

        # Module docstring point 5, only once the view is known to be the
        # right one: measured on the wrong screen, every check below would add
        # a failure that is only the missing marker again, and bury it.
        if testid_ok is not False and marker_ok:
            usable = _run_usable_checks(page, route, gate, width_dir, shot_name, reasons)
        elif gate is not None:
            gate.release()

        overflow_info = _measure_overflow(page)
        if overflow_info.get("overflow", 0) > 2:
            offenders = overflow_info.get("offenders", [])
            reasons.append(
                f"horizontal overflow {overflow_info['overflow']}px "
                f"(scrollWidth={overflow_info['scrollWidth']} "
                f"clientWidth={overflow_info['clientWidth']}); offenders: "
                + "; ".join(o["html"] for o in offenders[:3]))

    unexpected_console = [e for e in console_errors
                          if not any(x in e for x in expected_errors)]
    unexpected_failed = [r for r in failed_requests
                         if not any(x in r["url"] for x in expected_errors)]
    if unexpected_console:
        reasons.append(f"{len(unexpected_console)} console error(s): "
                       + "; ".join(unexpected_console[:3]))
    if unexpected_failed:
        reasons.append(f"{len(unexpected_failed)} failed request(s) (>=400): "
                       + "; ".join(f"{r['status']} {r['url']}"
                                   for r in unexpected_failed[:3]))

    if gate is not None:
        gate.release()  # never leave a request hanging behind a failed walk
    shot_path = width_dir / f"{shot_name}.png"
    try:
        page.screenshot(path=str(shot_path))
    except Exception as exc:
        reasons.append(f"screenshot failed: {exc}")

    page.remove_listener("console", on_console)
    page.remove_listener("pageerror", on_pageerror)
    page.remove_listener("response", on_response)

    passed = len(reasons) == 0
    return {
        "width": width, "route": name, "url": target, "marker": route.get("marker"),
        "marker_ok": marker_ok, "marker_box": marker_box,
        "testid": route.get("testid"), "testid_ok": testid_ok, "testid_box": testid_box,
        "passed": passed, "reasons": reasons,
        "click_log": click_log, "overflow": overflow_info, **usable,
        "console_errors": console_errors, "failed_requests": failed_requests,
        "screenshot": str(shot_path), "duration_s": round(time.monotonic() - t0, 2),
    }


def _run_usable_checks(page, route: dict, gate: "_Gate | None", width_dir: Path,
                       shot_name: str, reasons: list[str]) -> dict[str, Any]:
    """Module docstring point 5, in the order the Target modal needs: the gate
    first, because its hold must stay under the UI's own 15 s fetch timeout
    (see the gate section), then the checks that look at the view as it
    stands, then the labels, whose scrolling moves it."""
    out: dict[str, Any] = {}
    if gate is not None:
        out["gate"], why = _check_gate(page, gate, width_dir, shot_name)
        reasons.extend(why)
    if route.get("boxes"):
        out["boxes"], why = _check_boxes(page, route["boxes"])
        reasons.extend(why)
    if route.get("reachable"):
        out["reachable"], why = _check_reachable(page, route["reachable"])
        reasons.extend(why)
    if route.get("text_intact"):
        out["text_intact"], why = _check_text_intact(page, route["text_intact"])
        reasons.extend(why)
    if route.get("labels"):
        out["labels"], why = _check_labels(page, route["labels"], width_dir, shot_name)
        reasons.extend(why)
    return out


# --------------------------------------------------------------------- main

def _load_routes(path: Path, include_pending: bool) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    routes = data["routes"]
    if not include_pending:
        routes = [r for r in routes if not r.get("_pending")]
    return routes


def _load_seed(path: Path) -> list[dict]:
    """The route file's `seed` ops, or [] (every route file before S4)."""
    return json.loads(path.read_text(encoding="utf-8")).get("seed", [])


def _routes_for_width(routes: list[dict], width: int) -> list[dict]:
    """The routes that run at `width`. A route with `widths` runs only at
    those: a walk through the phone's stage list and one through the classic
    desktop editor are different doors to one view, and a hash that is the
    flow LIST on a phone is the CANVAS at 820 (routes_next.json's
    session-flows-canvas had to stay `_pending` for want of this). A route
    without `widths` runs at every width, as every route did before."""
    return [r for r in routes if not r.get("widths") or width in r["widths"]]


# ------------------------------------------------------------------- seed

class SeedError(RuntimeError):
    pass


def _seed(request, base: str, ops: list[dict]) -> list[dict]:
    """Seed the server before a walk, through the browser context's own
    request client, so an `--auth` run seeds as the signed-in role and a
    refused seed says which role was refused.

    One op so far, `copy_flow: {"from", "id", "name", "folder", "set_params",
    "expect_node"}`: read a flow (a shipped Example), save a copy under a new
    id through `POST /api/flows`, and read the copy back. The copy exists
    because an Example is read-only (the store refuses an Example's id with
    403, and the Target modal opens an Example in view mode with no DONE),
    and a walk that means to grade DONE needs a flow a person could edit.
    `set_params` - `{node_id: {param: value}}` - changes the copy's node
    params before it is saved (a second copy whose TARGET is another object,
    so two flows share a node id and differ in what it frames).
    `expect_node` - `{"id", "type", "min_panels", "params"}` - is checked on
    the copy AS STORED: were the Example ever to stop being a mosaic, the walk
    would grade a one-panel sheet and still pass, with nothing saying the
    fixture had changed under it; and a walk that tells two flows apart by
    their TARGET's name passes vacuously if the names never differed."""
    base = base.rstrip("/")
    log: list[dict] = []
    for op in ops:
        spec = op.get("copy_flow")
        if spec is None:
            raise SeedError(f"unknown seed op {sorted(op)!r}")
        src = request.get(f"{base}/api/flows/{spec['from']}")
        if not src.ok:
            raise SeedError(f"GET /api/flows/{spec['from']} -> {src.status}")
        record = src.json()
        record.update(id=spec["id"], name=spec["name"],
                      folder=spec.get("folder", "My flows"), readonly=False)
        for node_id, params in spec.get("set_params", {}).items():
            node = next((n for n in record.get("graph", {}).get("nodes", [])
                         if n.get("id") == node_id), None)
            if node is None:
                raise SeedError(f"{spec['from']!r} has no node {node_id!r} to set params on")
            node.setdefault("params", {}).update(params)
        saved = request.post(f"{base}/api/flows", data={"flow": record})
        if not saved.ok:
            raise SeedError(f"POST /api/flows (copy of {spec['from']!r} as "
                            f"{spec['id']!r}) -> {saved.status}: {saved.text()[:300]}")
        back = request.get(f"{base}/api/flows/{spec['id']}")
        if not back.ok:
            raise SeedError(f"the copy {spec['id']!r} does not read back: "
                            f"GET -> {back.status}")
        stored = back.json()
        want = spec.get("expect_node")
        if want:
            node = next((n for n in stored.get("graph", {}).get("nodes", [])
                         if n.get("id") == want["id"]), None)
            if node is None or node.get("type") != want.get("type", node.get("type")):
                raise SeedError(f"the copy {spec['id']!r} has no {want.get('type')} "
                                f"node {want['id']!r} (got {node!r:.200})")
            params = node.get("params") or {}
            try:
                panels = int(params.get("rows", 1)) * int(params.get("cols", 1))
            except (TypeError, ValueError):
                panels = 0
            if panels < want.get("min_panels", 1):
                raise SeedError(f"the copy {spec['id']!r}'s node {want['id']!r} is "
                                f"{panels} panel(s), and the walk grades a mosaic "
                                f"(need >= {want['min_panels']})")
            for key, value in want.get("params", {}).items():
                if params.get(key) != value:
                    raise SeedError(f"the copy {spec['id']!r}'s node {want['id']!r} "
                                    f"stored {key}={params.get(key)!r}, not {value!r}")
        log.append({"copy_flow": spec["id"], "from": spec["from"],
                    "readonly": stored.get("readonly")})
    return log


def _resolve_routes_path(raw: str) -> Path:
    p = Path(raw)
    if p.is_file():
        return p
    alt = Path(__file__).resolve().parent / raw
    if alt.is_file():
        return alt
    raise FileNotFoundError(f"routes file not found: {raw!r} "
                            f"(tried {p} and {alt})")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="probe")
    ap.add_argument("--base", default=None,
                    help="server base URL; default http://127.0.0.1:<port>")
    ap.add_argument("--port", type=int, default=8801)
    ap.add_argument("--routes", default="routes_classic.json")
    ap.add_argument("--widths", default="390,820,1440",
                    help="comma-separated CSS pixel widths")
    ap.add_argument("--out", default=str(REPO_ROOT / ".probe" / "out"))
    ap.add_argument("--role", choices=["viewer", "operator", "admin"],
                    default="admin")
    ap.add_argument("--auth", action="store_true",
                    help="log in via the local form before walking routes")
    ap.add_argument("--creds", default=str(REPO_ROOT / ".probe" / "cfg"
                                          / "probe_users.json"),
                    help="path to the probe_users.json written by "
                        "server_ctl.py start --auth")
    ap.add_argument("--include-pending", action="store_true",
                    help="also attempt routes flagged _pending (routes_next.json)")
    ap.add_argument("--only", default=None,
                    help="comma-separated route 'name' values -- run only this "
                        "subset (e.g. an --auth --role viewer smoke pass over a "
                        "handful of routes rather than the whole file). Unknown "
                        "names are silently ignored, matching nothing rather "
                        "than erroring, since the caller may pass names that "
                        "only exist in some route files.")
    ap.add_argument("--headed", action="store_true")
    args = ap.parse_args(argv)

    base = args.base or f"http://127.0.0.1:{args.port}"
    routes_path = _resolve_routes_path(args.routes)
    routes = _load_routes(routes_path, args.include_pending)
    seed_ops = _load_seed(routes_path)
    if args.only:
        only_names = {n.strip() for n in args.only.split(",") if n.strip()}
        routes = [r for r in routes if r.get("name") in only_names]
    widths = [int(w.strip()) for w in args.widths.split(",") if w.strip()]
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    creds = None
    if args.auth:
        creds_path = Path(args.creds)
        if not creds_path.is_file():
            print(f"ERROR: --auth given but no creds file at {creds_path} "
                 f"(start the server with server_ctl.py start --auth first)",
                 file=sys.stderr)
            return 2
        creds = json.loads(creds_path.read_text(encoding="utf-8"))

    from playwright.sync_api import sync_playwright

    all_results: list[dict] = []
    login_failures: list[str] = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not args.headed)
        try:
            for width in widths:
                profile = _viewport_for(width)
                context = browser.new_context(
                    viewport={"width": width, "height": profile["height"]},
                    is_mobile=profile["is_mobile"], has_touch=profile["has_touch"],
                )
                page = context.new_page()
                if args.auth:
                    try:
                        _login(page, base, args.role, creds)
                    except LoginError as exc:
                        msg = f"width={width}: LOGIN FAILED: {exc}"
                        print(msg, file=sys.stderr)
                        login_failures.append(msg)
                        for route in _routes_for_width(routes, width):
                            all_results.append({
                                "width": width, "route": route.get("name"),
                                "url": route.get("url"), "passed": False,
                                "reasons": [f"login failed: {exc}"],
                            })
                        context.close()
                        continue

                page.close()
                width_routes = _routes_for_width(routes, width)
                if seed_ops and width_routes:
                    # Every width seeds afresh: the copy is an upsert, so a
                    # walk at 1440 grades the flow as seeded, never as the
                    # walk at 390 left it.
                    try:
                        seeded = _seed(context.request, base, seed_ops)
                        print(f"[{width}px] seeded: {seeded}")
                    except SeedError as exc:
                        msg = f"width={width}: SEED FAILED: {exc}"
                        print(msg, file=sys.stderr)
                        for route in width_routes:
                            all_results.append({
                                "width": width, "route": route.get("name"),
                                "url": route.get("url"), "passed": False,
                                "reasons": [f"seed failed: {exc}"],
                            })
                        context.close()
                        continue
                for route in width_routes:
                    result = _run_isolated_route(context, base, route, out_dir, width)
                    all_results.append(result)
                    status = "PASS" if result["passed"] else "FAIL"
                    print(f"[{width}px] {status:4s} {result['route']:16s} "
                         f"{result['duration_s']:5.1f}s  {result['screenshot']}")
                    if not result["passed"]:
                        for r in result["reasons"]:
                            print(f"           - {r}")

                context.close()
        finally:
            browser.close()

    report_path = out_dir / "report.jsonl"
    with open(report_path, "w", encoding="utf-8") as f:
        for r in all_results:
            f.write(json.dumps(r) + "\n")

    _print_table(all_results, widths)
    print(f"\nreport: {report_path}")

    any_failed = any(not r["passed"] for r in all_results)
    return 1 if any_failed else 0


def _print_table(results: list[dict], widths: list[int]) -> None:
    print("\n" + "=" * 72)
    print("PROBE RESULTS")
    print("=" * 72)
    by_width: dict[int, list[dict]] = {w: [] for w in widths}
    for r in results:
        by_width.setdefault(r["width"], []).append(r)
    total = len(results)
    failed = sum(1 for r in results if not r["passed"])
    for width in widths:
        rows = by_width.get(width, [])
        print(f"\n-- {width}px --")
        for r in rows:
            status = "PASS" if r["passed"] else "FAIL"
            print(f"  {status:4s}  {r['route']}")
    print(f"\n{total - failed}/{total} passed"
         f"{f'  ({failed} FAILED)' if failed else ''}")
    print("=" * 72)


if __name__ == "__main__":
    raise SystemExit(main())
