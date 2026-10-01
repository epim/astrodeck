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
  6. What the page says must be what the rig says (#189 S5 and S6,
     routes_s5_s6.json). A readout, a RUN button's copy or a review that
     reads plausibly is not evidence it reads TRUE: "M31 2-1, pass 3" is as
     well formed when the engine is on 1-2. So a route may compare the page
     against the server's own answer (`api_text`, `run_copy`), say what a
     control's text must be (`text_expect`) and how many of a thing there are
     (`count`), forbid a request the walk must not cause (`forbid_requests`),
     and grade what the walk saved (`new_flow`) and left in the browser
     (`no_plan_targets`). Every button in a view can be held to the hit test
     and a height floor at once (`reachable_all`), a box to filling its row
     (`fills`) and a scroller to showing its content (`unsquashed`), text
     that shares a line to lining up (`aligned`), and text is also graded
     against its OWN box (an ellipsis is a cut). A walk may type (`fill`),
     wait on the rig (`wait_api`), run checks mid-walk (`check`) and take a
     screenshot mid-walk (`shot`). A seed can save a
     flow drawn in the route file (`save_flow`) and refuse to walk on a
     server that already holds a session for it (`fresh`), and a seed op may
     be limited to some widths.
  7. Readouts equal to the routes, read at one moment (#189 S7 item 1,
     routes_s7.json). `readouts` reads the page's readouts and the two routes
     they are drawn from, GET /api/sequence/state and GET /api/flows/{id}/
     progress, inside ONE page evaluation: the routes before the DOM and
     again after it, so the two reads bracket what the page showed. They must
     agree with each other (a run that moved between them is read again) and
     the DOM must EQUAL them. A phone walk is a touch walk: `touch` holds that
     the page reports a touch screen, and on such a route every click step is
     a tap. A walk can follow the page (`wait_change`), remember a value the
     rig reported and later wait for it to change (`remember_api`, `wait_api`
     `differs_from`). Seeds can save a site through the API (`set_site`, and
     `daylight_site` for one where the sun is up now), draw a TARGET where the
     meridian will be (`save_flow` `meridian`), run a flow and abort it
     (`run_flow`) and move its session onto an earlier night (`seed_session`,
     through the server's own venv). A seed op may be limited to some routes.
     Nothing here ever runs against the rig's port (RIG_PORT).
  8. Where a box sits, in the browser a person has (mosaic slice H4: #535,
     #495, #440, #492). A desktop walk's Chromium draws its scrollbars,
     where Playwright's default hid them and so measured every scrolling
     desktop column one bar too wide (`_Browsers`, HIDE_SCROLLBARS); a phone
     walk keeps the default, since a phone's bar takes no width. `scrollbar`
     holds on the real page that a scrolling column's bar is there. Every
     earlier check grades an element alone, so a canvas pushed under the
     sky's clip passed: `contained` holds each element whole inside the box
     that clips it, and `apart` holds a canvas clear of the lines drawn under
     it, each in a state the route names (`when`: the survey degraded, a
     catalogue verdict showing). Each is described where it is implemented.

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
import math
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit

REPO_ROOT = Path(__file__).resolve().parents[2]

# width -> (height, is_mobile/has_touch). Fixed per the spec this harness was
# built against; an unlisted width falls back to a synthesised desktop-shaped
# viewport (see _viewport_for).
WIDTH_PROFILES: dict[int, dict[str, Any]] = {
    390: {"height": 844, "is_mobile": True, "has_touch": True},
    820: {"height": 1180, "is_mobile": False, "has_touch": False},
    1440: {"height": 900, "is_mobile": False, "has_touch": False},
}

#: The port the rig's own AstroDeck server listens on (CLAUDE.md, "The rig").
#: The probe never walks a server there (#189 S7): the walks press RUN, seed
#: flows and sessions, and save sites, and the rig's server is not a probe
#: server however it is reached. server_ctl.py refuses to start one there too.
RIG_PORT = 8800


def _refuse_rig_port(base: str) -> str | None:
    """Why the probe may not walk `base`, or None. A base with no port is
    port 80 or 443, never the rig's."""
    try:
        port = urlsplit(base).port
    except ValueError as exc:
        return f"--base {base!r} is not a URL the probe can walk ({exc})"
    if port == RIG_PORT:
        return (f"{base} is on port {RIG_PORT}, the rig's server port: the probe "
                f"walks a private server only (server_ctl.py start --port <any "
                f"other>)")
    return None


def _new_context(browser, width: int):
    """A browser context for `width`, from WIDTH_PROFILES: a phone width is
    a touch screen (has_touch, is_mobile), because a phone walk is taps
    (#189 S7: "390 x 844 with touch"). One constructor for main and for the
    probe's own tests, so a profile that lost its touch is a profile the
    tests see. The browser comes from `_Browsers.for_width(width)`, which
    decides whether its scrollbars are drawn."""
    profile = _viewport_for(width)
    return browser.new_context(
        viewport={"width": width, "height": profile["height"]},
        is_mobile=profile["is_mobile"], has_touch=profile["has_touch"])


# SCROLLBARS ARE PART OF THE LAYOUT BEING GRADED (#535). Playwright passes
# Chromium `--hide-scrollbars` on every launch unless told not to, so on the
# probe's pages a column that scrolls kept its whole width, and every check of
# whether something fits a desktop column was made against a box about 10 px
# wider than a Windows desktop browser gives it (index.css sets
# `scrollbar-width: thin` on every element, a 10 px bar). #469's "While a
# mosaic waits" select passed the probe whole at 11px in the classic
# inspector's 284 px column and was cut on the operator's screen: measured on
# the real page on 2026-09-29, that column (`[data-flows-inspector]`) scrolls
# 1928 px of content in 766 and is 273 px inside with the bar drawn, 283 with
# it hidden, and the select in it 243 px wide against 253. So a desktop walk
# launches with that default left out and sees the bar a desktop browser
# draws. A phone walk keeps the default: a phone's scrollbar overlays the
# content and takes no width, which is what the hidden bar measures too.
HIDE_SCROLLBARS = "--hide-scrollbars"


def _launch_args(width: int) -> dict[str, Any]:
    """The `chromium.launch()` keywords a walk at `width` needs: a desktop
    width draws its scrollbars (`ignore_default_args`), a phone width
    (WIDTH_PROFILES' `is_mobile`) keeps Playwright's default. See
    HIDE_SCROLLBARS."""
    if _viewport_for(width)["is_mobile"]:
        return {}
    return {"ignore_default_args": [HIDE_SCROLLBARS]}


class _Browsers:
    """One Chromium per kind of screen, each launched the first time a walk
    at such a width asks for it, with `_launch_args` (a launch flag is the
    browser's, not a context's, so a phone walk and a desktop walk cannot
    share one). `main` and every one of the probe's own tests take their
    browsers from here, so a test grades the page as the probe's walks see
    it, scrollbars included; test_probe_isolation.py holds that nothing
    else launches one. `launch` is passed to every launch (a channel, say)."""

    def __init__(self, playwright, headless: bool = True, **launch: Any) -> None:
        self._pw = playwright
        self._headless = headless
        self._launch = launch
        self._by_args: dict[tuple, Any] = {}

    def for_width(self, width: int):
        args = _launch_args(width)
        key = tuple(sorted((k, tuple(v)) for k, v in args.items()))
        if key not in self._by_args:
            self._by_args[key] = self._pw.chromium.launch(
                headless=self._headless, **self._launch, **args)
        return self._by_args[key]

    def context(self, width: int):
        """`_new_context(width)` on the browser for `width`."""
        return _new_context(self.for_width(width), width)

    def close(self) -> None:
        browsers, self._by_args = list(self._by_args.values()), {}
        for browser in browsers:
            try:
                browser.close()
            except Exception:
                pass


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
# `clipped` names the first box whose overflow cuts a glyph box, by how
# many px. The clip is the box's PADDING box (client area), which is
# where `overflow` clips. Measured case (2026-09-26): the PANELS bar's
# "0/80" sat in a 16 px `overflow: hidden` bar on the row's 21 px line, and
# its glyphs ran 4 px past the bar's bottom edge.
#
# The walk starts at the element's OWN box, not its parent (#189 S5). An
# element that clips its own text - `overflow: hidden` with an ellipsis, as a
# flow's name on the RUN button's CONTINUE copy is drawn - hides the tail of
# it as surely as an ancestor does, and its glyph boxes still run past its
# edge (the layout is the whole text; only the paint is cut), so the same
# test sees it. The first version began at the parent: on the wizard's title,
# cut by its own ellipsis at 390 px (measured 2026-09-28, the unfixed build),
# it reported only the 0.7 px of that hidden layout that ran past the body,
# "clipped 0.7px by 'BODY'", which names neither the cut nor its cause; from
# the element's own box it reads "clipped 9.5px by 'swz-title'". A fixture
# of a span whose ellipsis stays inside the page reads whole to the old walk
# (test_probe_s5_s6.OwnBoxClipTest).
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
  for (let a = el; a && !clipped; a = a.parentElement) {
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

# Whether a scroller shows all its content, or else stands at its own
# max-height (the one reason a box may hide content it holds: it was told to
# scroll past that height). A scroller a flex column has shrunk below both
# shows neither: measured 2026-09-28, the phone stage sheet's LOG, a 170 px
# max-height scroller holding 32 px of lines, was 17 px tall, one line cut in
# half, and each line still measured whole once scrolled to.
UNSQUASHED_JS = """(el) => {
  const q = (v) => Math.round(v * 10) / 10;
  const cs = getComputedStyle(el);
  const max = parseFloat(cs.maxHeight);
  const h = el.getBoundingClientRect().height;
  const ok = el.scrollHeight <= el.clientHeight + 1 || (isFinite(max) && h >= max - 1);
  return {ok, client: q(el.clientHeight), content: q(el.scrollHeight),
          max: isFinite(max) ? q(max) : null};
}"""

# The element's width against the CONTENT width of the ancestor `sel` names
# (its client width less its horizontal padding), both in CSS px; `content`
# is null when no such ancestor exists.
FILLS_JS = """(el, sel) => {
  const q = (v) => Math.round(v * 10) / 10;
  const a = el.parentElement ? el.parentElement.closest(sel) : null;
  const width = q(el.getBoundingClientRect().width);
  if (!a) return {width, content: null, container: null};
  const cs = getComputedStyle(a);
  const content = a.clientWidth - parseFloat(cs.paddingLeft) - parseFloat(cs.paddingRight);
  return {width, content: q(content), container: (a.getAttribute('class') || a.nodeName).slice(0, 60)};
}"""


def _check_boxes(page, specs: list[dict]) -> tuple[list[dict], list[str]]:
    """`boxes: [{"selector", "min_width", "min_height", "min_inset", "fills",
    "unsquashed"}]`: the first visible match must be at least that big
    (`unsquashed` is UNSQUASHED_JS, on a scroller). The floor is the route's,
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
    gave it no frame (FlowFrameSheet.tsx).

    `fills` names an ANCESTOR (a selector `closest` finds) whose content box
    the element must fill across, within a pixel: a `full` button is as wide
    as its row. A width floor sees only the gross case. Measured 2026-09-28,
    the phone stage sheet's footer column shrank to its content (a flex item
    of a flex ROW with no `flex`), so RUN was 92 px wide, and with CONTINUE's
    longer copy 349 px of the 358 the footer holds: over any floor that leaves
    room for a few pixels of layout change, and still not full."""
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
        if spec.get("fills") and box is not None:
            try:
                room = found[0].evaluate(FILLS_JS, spec["fills"])
            except Exception as exc:
                room = {"error": str(exc)}
            fills_ok = (isinstance(room, dict) and room.get("content") is not None
                        and room["width"] >= room["content"] - 1)
            result.update(fills=room, ok=result["ok"] and fills_ok)
            if not fills_ok:
                reasons.append(f"box {sel!r} is {room.get('width') if isinstance(room, dict) else '?'}"
                               f"px wide and does not fill {spec['fills']!r} "
                               f"({room})")
        if spec.get("unsquashed") and box is not None:
            try:
                depth = found[0].evaluate(UNSQUASHED_JS)
            except Exception as exc:
                depth = {"error": str(exc)}
            deep_ok = isinstance(depth, dict) and bool(depth.get("ok"))
            result.update(depth=depth, ok=result["ok"] and deep_ok)
            if not deep_ok:
                reasons.append(f"box {sel!r} is squashed: it shows {depth.get('client') if isinstance(depth, dict) else '?'}"
                               f"px of {depth.get('content') if isinstance(depth, dict) else '?'}px of "
                               f"content and is under its own max-height ({depth})")
        inset = spec.get("min_inset")
        inset_ok = True
        edges = None
        if inset is not None and box is not None:
            try:
                edges = found[0].evaluate(EDGES_JS)
            except Exception:
                edges = None
            inset_ok = edges is not None and min(edges.values()) >= inset
            result.update(edges=edges, ok=result["ok"] and inset_ok)
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


# ------------------------------------------------- where a box sits (H4)
#
# Module docstring point 8. Every check above grades an element ALONE: it is
# visible, big enough, whole, unclipped, answering at its centre. None of them
# asks where it sits relative to the box that clips it, and so a canvas
# pushed 18 px under the Target modal's sky, its MOVE SKY toggle 10 px under
# with it, and a status line painted over the canvas's bottom edge passed
# both frame walks (#495, measured on the real page with the "square fills
# the box" build of #440's fix: the sky 48 to 438, the canvas 30 to 420,
# MOVE SKY's top at 38, the N at 34, the degraded line at 410 over the
# canvas). MOVE SKY's centre still answered the hit test, the line's text was
# whole, and every size floor held.

# The element's box against the PADDING box of its nearest ancestor that
# `within` names (where that ancestor's `overflow` clips), in CSS px: `over`
# is how far the element runs past each edge (negative inside).
CONTAINED_JS = """(el, within) => {
  const q = (v) => Math.round(v * 10) / 10;
  const r = el.getBoundingClientRect();
  const box = {left: q(r.left), top: q(r.top), right: q(r.right), bottom: q(r.bottom)};
  const a = el.parentElement ? el.parentElement.closest(within) : null;
  if (!a) return {box, within: null, over: null};
  const b = a.getBoundingClientRect();
  const L = b.left + a.clientLeft, T = b.top + a.clientTop;
  const R = L + a.clientWidth, B = T + a.clientHeight;
  return {box, within: {left: q(L), top: q(T), right: q(R), bottom: q(B)},
          over: {left: q(L - r.left), top: q(T - r.top), right: q(r.right - R),
                 bottom: q(r.bottom - B)}};
}"""

# Boxes, for `apart`: the element's border box in CSS px.
BOX_JS = """(el) => { const r = el.getBoundingClientRect(); const q = (v) => Math.round(v * 10) / 10;
  return {left: q(r.left), top: q(r.top), right: q(r.right), bottom: q(r.bottom),
          text: (el.textContent || '').replace(/\\s+/g, ' ').trim().slice(0, 50)}; }"""

# A scroller's own word for whether it scrolls and how much of its width its
# scrollbar takes: its border box less its client width and its borders. It
# scrolls when its content is taller than it AND its overflow lets a person
# scroll it (`auto` or `scroll`): an `overflow: hidden` box with more content
# than room draws no bar, and is a clip, not a column that scrolls.
SCROLLBAR_JS = """(el) => {
  const cs = getComputedStyle(el);
  const bl = parseFloat(cs.borderLeftWidth) || 0, br = parseFloat(cs.borderRightWidth) || 0;
  const scrolls = /(auto|scroll)/.test(cs.overflowY) && el.scrollHeight > el.clientHeight + 1;
  return {scrolls, content: el.scrollHeight,
          client: el.clientHeight, width: el.offsetWidth, inner: el.clientWidth,
          bar: Math.round((el.offsetWidth - el.clientWidth - bl - br) * 10) / 10,
          overflow_y: cs.overflowY, scrollbar_width: cs.scrollbarWidth};
}"""

#: How far a box may run past an edge, or into another box, before it counts:
#: the half pixel `text_intact` allows a glyph, for sub-pixel layout.
LAYOUT_TOLERANCE_PX = 0.5


def _await_premise(page, when: str | None, timeout_ms: int) -> bool:
    """Wait up to `timeout_ms` for `when` to be visible; True at once when
    there is no premise. A layout check grades a STATE (the survey degraded,
    a verdict under the canvas), and a check made before the state arrives
    grades the layout it exists to catch the absence of: the canvas fits the
    sky exactly until a line is drawn under it."""
    if not when:
        return True
    deadline = time.monotonic() + timeout_ms / 1000.0
    while True:
        if _visible_css_matches(page, when):
            return True
        if time.monotonic() >= deadline:
            return False
        page.wait_for_timeout(200)


def _contained_attempt(page, spec: dict) -> tuple[bool, list[dict], list[str]]:
    within = spec["within"]
    rows: list[dict] = []
    problems: list[str] = []
    for sel in spec.get("selectors", []):
        found = _visible_css_matches(page, sel)
        if not found:
            rows.append({"selector": sel, "ok": False, "why": "not visible"})
            problems.append(f"contained: {sel!r} is not visible, so nothing says it sits "
                            f"inside {within!r}")
            continue
        for i, el in enumerate(found):
            try:
                got = el.evaluate(CONTAINED_JS, within)
            except Exception as exc:
                got = {"error": str(exc)}
            name = sel if len(found) == 1 else f"{sel} #{i + 1}"
            over = got.get("over")
            if got.get("error") or over is None:
                rows.append({"selector": name, "ok": False, **got})
                problems.append(f"contained: {name!r} has no ancestor {within!r}"
                                if not got.get("error") else
                                f"contained: {name!r} could not be measured: {got['error']}")
                continue
            worst = max(over.values())
            ok = worst <= LAYOUT_TOLERANCE_PX
            rows.append({"selector": name, "ok": ok, **got})
            if not ok:
                edges = ", ".join(f"{e} {v}px" for e, v in over.items() if v > LAYOUT_TOLERANCE_PX)
                problems.append(f"contained: {name!r} runs past {within!r} ({edges}): its box "
                                f"{got['box']}, the container's {got['within']}")
    if not spec.get("selectors"):
        problems.append(f"contained: no selectors, so nothing is held inside {within!r}")
    return not problems, rows, problems


def _check_contained(page, specs: list[dict]) -> tuple[list[dict], list[str]]:
    """`contained: [{"within", "selectors", "when", "timeout_ms", "settle_ms"}]`:
    every visible match of each selector lies whole, within
    LAYOUT_TOLERANCE_PX, inside the padding box of its nearest ancestor that
    `within` (plain CSS, for `closest`) names: the box that clips it. Each
    selector must match at least one visible element, so a control that went
    missing is a failure and never a pass on nothing to measure.

    `when` is the state graded (`_await_premise`): the check waits up to
    `timeout_ms` (default 15000) for it, and its absence is the failure,
    named. Then the layout is read until it holds or `settle_ms` (default
    1500) runs out, and the last reading is reported: a line that has just
    appeared may take a frame to lay out, and an overflow that lasts is not
    a frame late."""
    results: list[dict] = []
    reasons: list[str] = []
    for spec in specs:
        within = spec["within"]
        when = spec.get("when")
        if not _await_premise(page, when, spec.get("timeout_ms", 15000)):
            results.append({"within": within, "ok": False, "when": when, "premise": False})
            reasons.append(f"contained: the state it grades never arrived: {when!r} was not "
                           f"visible within {spec.get('timeout_ms', 15000)} ms")
            continue
        def attempt(spec=spec):
            held, rows, problems = _contained_attempt(page, spec)
            return held, (rows, problems)

        ok, (rows, problems) = _poll(page, spec.get("settle_ms", 1500), attempt)
        results.append({"within": within, "when": when, "ok": ok, "rows": rows})
        reasons.extend(problems)
    return results, reasons


def _overlap(a: dict, b: dict) -> tuple[float, float]:
    """How far two boxes overlap across and down, in CSS px (<= 0: apart)."""
    return (round(min(a["right"], b["right"]) - max(a["left"], b["left"]), 1),
            round(min(a["bottom"], b["bottom"]) - max(a["top"], b["top"]), 1))


def _apart_attempt(page, spec: dict) -> tuple[bool, dict]:
    a_sel, b_sel = spec["a"], spec["b"]
    info: dict[str, Any] = {"a": a_sel, "b": b_sel, "box": None, "others": [], "why": None}
    a_found = _visible_css_matches(page, a_sel)
    if not a_found:
        info["why"] = f"apart: {a_sel!r} is not visible"
        return False, info
    try:
        a_box = a_found[0].evaluate(BOX_JS)
    except Exception as exc:
        info["why"] = f"apart: {a_sel!r} could not be measured ({exc})"
        return False, info
    info["box"] = a_box
    need = spec.get("min_b", 1)
    b_found = _visible_css_matches(page, b_sel)
    if len(b_found) < need:
        info["why"] = (f"apart: {len(b_found)} visible {b_sel!r}, need >= {need}: "
                       f"nothing to hold apart from {a_sel!r}")
        return False, info
    problems: list[str] = []
    for el in b_found:
        try:
            box = el.evaluate(BOX_JS)
        except Exception as exc:
            problems.append(f"apart: a {b_sel!r} could not be measured ({exc})")
            continue
        across, down = _overlap(a_box, box)
        hit = across > LAYOUT_TOLERANCE_PX and down > LAYOUT_TOLERANCE_PX
        info["others"].append({**box, "overlap": [across, down], "ok": not hit})
        if hit:
            # The boxes without their text: the line's words are quoted
            # first, and the canvas's text is every label drawn on it.
            edges = lambda b: {k: v for k, v in b.items() if k != "text"}  # noqa: E731
            problems.append(f"apart: {box['text']!r} ({b_sel}) lies over {a_sel!r} by "
                            f"{down}px down and {across}px across: its box {edges(box)}, "
                            f"the other's {edges(a_box)}")
    info["why"] = problems[0] if problems else None
    info["problems"] = problems
    return not problems, info


def _check_apart(page, specs: list[dict]) -> tuple[list[dict], list[str]]:
    """`apart: [{"a", "b", "min_b", "when", "timeout_ms", "settle_ms"}]`: the
    first visible `a` (the canvas) and every visible `b` (the lines drawn
    under it) do not overlap by more than LAYOUT_TOLERANCE_PX both ways. At
    least `min_b` (default 1) `b` must be visible, waited for with the
    premise `when` for up to `timeout_ms`: a check of "no line lies over the
    canvas" passes on a canvas with no lines. `contained` cannot see this: a
    line inside the clip, over the square's bottom 18 px, is inside the box.
    Read as `contained` is (`settle_ms`)."""
    results: list[dict] = []
    reasons: list[str] = []
    for spec in specs:
        when = spec.get("when")
        timeout = spec.get("timeout_ms", 15000)
        need = spec.get("min_b", 1)
        premise = _await_premise(page, when, timeout)
        if premise:
            # The lines are part of the state: wait for as many as the walk
            # expects before reading how they sit.
            deadline = time.monotonic() + timeout / 1000.0
            while (len(_visible_css_matches(page, spec["b"])) < need
                   and time.monotonic() < deadline):
                page.wait_for_timeout(200)
        if not premise:
            results.append({"a": spec["a"], "b": spec["b"], "ok": False, "premise": False})
            reasons.append(f"apart: the state it grades never arrived: {when!r} was not "
                           f"visible within {timeout} ms")
            continue
        ok, info = _poll(page, spec.get("settle_ms", 1500), lambda spec=spec: _apart_attempt(page, spec))
        results.append({"ok": ok, **info})
        if not ok:
            reasons.extend(info.get("problems") or [info["why"]])
    return results, reasons


def _check_scrollbar(page, specs: list[dict]) -> tuple[list[dict], list[str]]:
    """`scrollbar: [{"selector", "min_px", "timeout_ms"}]`: the first visible
    match scrolls, and its scrollbar takes at least `min_px` of its width
    (SCROLLBAR_JS), read until it does or `timeout_ms` (default 5000) runs
    out, since a column's content may arrive after it does. This is the
    harness grading itself on the real page (#535): a desktop walk in a
    browser that hid its scrollbars would measure every fit in this column
    one bar too generous, and pass the layout #469 cut, so a walk that
    measures in a scrolling column says first that the bar is there. A
    column that does not scroll shows nothing either way, and fails: the
    walk must name a column that scrolls in the state it reaches."""
    results: list[dict] = []
    reasons: list[str] = []
    for spec in specs:
        sel = spec["selector"]
        need = spec.get("min_px", 8)

        def attempt(sel=sel, need=need):
            found = _visible_css_matches(page, sel)
            if not found:
                return False, None
            try:
                got = found[0].evaluate(SCROLLBAR_JS)
            except Exception as exc:
                return False, {"error": str(exc)}
            return bool(got.get("scrolls")) and got.get("bar", 0) >= need, got

        ok, got = _poll(page, spec.get("timeout_ms", 5000), attempt)
        results.append({"selector": sel, "ok": ok, "need": need, "measured": got})
        if ok:
            continue
        if got is None:
            reasons.append(f"scrollbar: {sel!r} is not visible")
        elif got.get("error"):
            reasons.append(f"scrollbar: {sel!r} could not be measured: {got['error']}")
        elif not got.get("scrolls"):
            reasons.append(f"scrollbar: {sel!r} does not scroll ({got['content']}px of content "
                           f"in {got['client']}px, overflow-y {got['overflow_y']}), so it shows "
                           f"nothing of the scrollbar")
        else:
            reasons.append(f"scrollbar: {sel!r} scrolls ({got['content']}px of content in "
                           f"{got['client']}px) and its scrollbar takes {got['bar']}px of its "
                           f"{got['width']}px, need >= {need}px: this browser hides its "
                           f"scrollbars, so every fit measured in this column is a bar too "
                           f"generous (#535)")
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


# ------------------------------------------------ the page against the rig
#
# Module docstring point 6. Each check below exists because a page can read
# well and read false (#189 S5, S6): a STAGE readout names a panel in the
# right shape whether or not it is the engine's, a CONTINUE button prints
# numbers whether or not they are the ledger's, and a wizard review that
# lists one TARGET says nothing of what the walk saved or what it left in the
# browser. So they ask the server, through the browser context's own request
# client (an --auth run reads as the signed-in role, as `_seed` does), and
# compare. A value the server did not send never passes: a template with a
# hole the answer leaves null fails, rather than matching the text "None".

def _origin(page) -> str:
    """scheme://host:port of the page, which is the server the walk is on."""
    u = urlsplit(page.url)
    return f"{u.scheme}://{u.netloc}"


def _api_json(page, path: str) -> tuple[int, Any]:
    """GET `path` on the page's own server: (status, parsed JSON or None).
    Status 0 means the request itself failed (the error is in the body)."""
    try:
        resp = page.context.request.get(_origin(page) + path)
    except Exception as exc:
        return 0, {"error": str(exc)}
    try:
        return resp.status, resp.json()
    except Exception:
        return resp.status, None


def _field(obj: Any, dotted: str) -> Any:
    """`obj["a"]["b"]` for "a.b" (a list index is a number), or None."""
    cur = obj
    for part in dotted.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        elif isinstance(cur, list) and part.isdigit() and int(part) < len(cur):
            cur = cur[int(part)]
        else:
            return None
    return cur


_HOLE = re.compile(r"\{([A-Za-z0-9_.]+)(?:\|([a-z]+))?\}")


def _localdate(value: Any) -> str | None:
    """A unix time as the local YYYY-MM-DD, or None for anything that is not a
    finite number. The browser the probe drives runs in this machine's zone,
    so the page's "local date" and this one are the same day (#189 S7: the
    replay notice names the frozen version's date in the viewer's zone)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        return time.strftime("%Y-%m-%d", time.localtime(float(value)))
    except (OverflowError, OSError, ValueError):
        return None


#: Filters a hole may name after a bar, `{session.plan_saved_ts|localdate}`.
#: One, and only what a template needs: a second language in the route file
#: is a second place for a claim to be wrong.
_FILTERS = {"localdate": _localdate}


def _render(template: str, obj: Any) -> tuple[str | None, list[str]]:
    """`template` with each `{dotted.field}` filled from `obj`. (None, holes)
    when any hole is absent, null or empty: `"{group.panel}: shooting now"`
    must not become "None: shooting now" and then match nothing, or worse,
    match a page that prints None. A hole may pass its value through a filter
    (`_FILTERS`); an unknown filter, or a value the filter cannot read, is a
    hole too."""
    missing: list[str] = []

    def fill(m: re.Match) -> str:
        value = _field(obj, m.group(1))
        if m.group(2) is not None:
            fn = _FILTERS.get(m.group(2))
            value = fn(value) if fn is not None and value is not None else None
        if value is None or value == "":
            missing.append(m.group(1) + (f"|{m.group(2)}" if m.group(2) else ""))
            return ""
        return str(value)

    out = _HOLE.sub(fill, template)
    return (None if missing else out), missing


def _text_of(el) -> str:
    """An element's text as the DOM holds it, whitespace collapsed: the
    textContent, not innerText, because innerText applies `text-transform`
    (the classic buttons upper-case "night" on screen, and the copy is the
    words) and breaks lines between flex items."""
    try:
        raw = el.evaluate("(e) => e.textContent || ''")
    except Exception:
        return ""
    return " ".join(raw.split())


def _holds_text(got: str, want: str) -> bool:
    """`want` is in `got`, and a number at either end of it is the whole of a
    number there. As bare text "0 / 80" is inside "FRAMES10 / 80", and "M31
    1-1 \u00b7 pass 1" inside "... pass 12": a count or a pass that only ends like
    the engine's would pass as the engine's. Only digits are fenced, never
    letters, because the text is textContent and runs words together: a
    readout's label into its value ("FRAMES0 / 80" on the real page,
    2026-09-28) and a panel row's words into the next row's number ("1-1:
    shooting now21-2ON..."). Found by the S56-PROBE verifier."""
    if not want:
        return False
    head = r"(?<!\d)" if want[0].isdigit() else ""
    tail = r"(?!\d)" if want[-1].isdigit() else ""
    return re.search(head + re.escape(want) + tail, got) is not None


def _poll(page, timeout_ms: int, attempt) -> Any:
    """Call `attempt()` until it answers (True, info) or the time is up, and
    return its last info. The page updates on its own clock (a WS publish, a
    progress refetch), so a check reads more than once before it fails."""
    deadline = time.monotonic() + timeout_ms / 1000.0
    while True:
        ok, info = attempt()
        if ok or time.monotonic() >= deadline:
            return ok, info
        page.wait_for_timeout(200)


def _check_text_expect(page, specs: list[dict]) -> tuple[list[dict], list[str]]:
    """`text_expect: [{"selector", <one test>, "timeout_ms"}]`, the test one of:
    `equals`, `contains` or `matches` (a regex, searched) on the first visible
    match's text; or `none_contain` on EVERY visible match, which with
    `min_count` (default 1) must also find that many, so a check of "no stage
    reads IDLE" cannot pass on a page with no stages. Text is `_text_of`."""
    results: list[dict] = []
    reasons: list[str] = []
    for spec in specs:
        sel = spec["selector"]

        def attempt(spec=spec, sel=sel):
            texts = [_text_of(el) for el in _visible_css_matches(page, sel)]
            if "none_contain" in spec:
                bad = [t for t in texts if spec["none_contain"] in t]
                need = spec.get("min_count", 1)
                if len(texts) < need:
                    return False, (texts, f"text {sel!r}: {len(texts)} visible, need >= {need}")
                if bad:
                    return False, (texts, f"text {sel!r}: {len(bad)} of {len(texts)} contain "
                                          f"{spec['none_contain']!r}, e.g. {bad[0]!r}")
                return True, (texts, None)
            if not texts:
                return False, (texts, f"text {sel!r} is not visible")
            got = texts[0]
            if "equals" in spec and got != spec["equals"]:
                return False, (texts, f"text {sel!r} reads {got!r}, not {spec['equals']!r}")
            if "contains" in spec and spec["contains"] not in got:
                return False, (texts, f"text {sel!r} reads {got!r}, which does not "
                                      f"contain {spec['contains']!r}")
            if "matches" in spec and not re.search(spec["matches"], got):
                return False, (texts, f"text {sel!r} reads {got!r}, which does not "
                                      f"match {spec['matches']!r}")
            return True, (texts, None)

        ok, (texts, why) = _poll(page, spec.get("timeout_ms", 3000), attempt)
        results.append({"selector": sel, "ok": ok, "texts": texts[:8]})
        if not ok:
            reasons.append(why)
    return results, reasons


def _check_api_text(page, specs: list[dict]) -> tuple[list[dict], list[str]]:
    """`api_text: [{"selector", "get", "template", "timeout_ms"}]`: the first
    visible match's text contains `template` filled from the server's answer
    to GET `get` (`_holds_text`: a number at either end is held whole), both
    read afresh on every poll (the engine moves on between two reads, so they
    must agree at one moment, not each be right once)."""
    results: list[dict] = []
    reasons: list[str] = []
    for spec in specs:
        sel = spec["selector"]

        def attempt(spec=spec, sel=sel):
            status, body = _api_json(page, spec["get"])
            want, missing = _render(spec["template"], body)
            found = _visible_css_matches(page, sel)
            got = _text_of(found[0]) if found else None
            info = {"status": status, "want": want, "got": got, "missing": missing}
            return (status == 200 and want is not None and got is not None
                    and _holds_text(got, want)), info

        ok, info = _poll(page, spec.get("timeout_ms", 6000), attempt)
        results.append({"selector": sel, "ok": ok, **info})
        if ok:
            continue
        if info["status"] != 200:
            reasons.append(f"api_text: GET {spec['get']} -> {info['status']}")
        elif info["missing"]:
            reasons.append(f"api_text: GET {spec['get']} answered no "
                           f"{', '.join(info['missing'])}, so {spec['template']!r} "
                           f"has nothing to say")
        elif info["got"] is None:
            reasons.append(f"api_text: text {sel!r} is not visible")
        else:
            reasons.append(f"api_text: text {sel!r} reads {info['got']!r}, and the "
                           f"server says {info['want']!r}")
    return results, reasons


def _run_copy_want(page, flow: str) -> tuple[dict | None, str | None]:
    """What RUN must say over this flow's session, from the server alone:
    `CONTINUE <NAME> (night <n>, <banked>/<total> subs)`, where the night is
    the route's `continue_night`, the night a CONTINUE pressed now would
    start, and the counts are the blocks' `banked` and `total` summed
    (runCopy.ts `runCopy`, spec 5.9), and the name is the stored flow's in
    capitals. A route that names no night gets `(<banked>/<total> subs)`
    and no night, as the button prints it: never a night worked out here.

    Until H4 this was the session's `nights` plus one, the rule the button
    had then, and the probe went on grading it after the button changed
    (#511, H4-ROUTES-B; #569): over a first run aborted the same evening the
    page reads night 1, as the run route answers, and a probe still adding
    one called the right button wrong. (None, why) when the route names no
    dormant session, since CONTINUE is then not what RUN should say at all."""
    status, prog = _api_json(page, f"/api/flows/{quote(flow)}/progress")
    if status != 200 or not isinstance(prog, dict):
        return None, f"GET /api/flows/{flow}/progress -> {status}"
    session = prog.get("session") or {}
    if session.get("status") != "dormant":
        return None, (f"the progress route names no dormant session for {flow!r} "
                      f"(session: {session or None}), so RUN should not read CONTINUE")
    try:
        blocks = prog.get("blocks") or []
        banked = sum(int(b["banked"]) for b in blocks)
        total = sum(int(b["total"]) for b in blocks)
    except (KeyError, TypeError, ValueError) as exc:
        return None, f"the progress route's numbers do not read as numbers ({exc!r})"
    night = _whole_number(session.get("continue_night"))
    status, rec = _api_json(page, f"/api/flows/{quote(flow)}")
    if status != 200 or not isinstance(rec, dict):
        return None, f"GET /api/flows/{flow} -> {status}"
    name = str(rec.get("name") or "").strip().upper()
    detail = (f"({banked}/{total} subs)" if night is None
              else f"(night {night}, {banked}/{total} subs)")
    text = " ".join(p for p in ("CONTINUE", name, detail) if p)
    return {"text": text, "night": night, "banked": banked, "total": total,
            "session": session.get("id")}, None


def _whole_number(value: Any) -> int | float | None:
    """A finite JSON number as runCopy.ts prints it (2, never 2.0), or None
    for anything else: its `finite` test, which a bool does not pass."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not math.isfinite(value):
        return None
    return int(value) if float(value).is_integer() else value


def _check_run_copy(page, spec: dict) -> tuple[dict, list[str]]:
    """`run_copy: {"selector", "flow", "night", "min_banked", "timeout_ms"}`:
    the RUN button's text holds exactly the CONTINUE line the progress route
    makes (`_run_copy_want`), read afresh on every poll. `night` pins the
    night the walk expects (a first run aborted this evening continues night
    1, #511; one moved onto an earlier observing night continues night 2), and
    `min_banked` that some subs were banked, so a button that printed 0 for
    every count could not pass a walk that shot one."""
    sel = spec["selector"]
    reasons: list[str] = []

    def attempt():
        want, why = _run_copy_want(page, spec["flow"])
        found = _visible_css_matches(page, sel)
        got = _text_of(found[0]) if found else None
        info = {"want": want, "why": why, "got": got}
        return (want is not None and got is not None and want["text"] in got), info

    ok, info = _poll(page, spec.get("timeout_ms", 6000), attempt)
    want = info["want"]
    if want is None:
        reasons.append(f"run_copy: {info['why']}")
    elif info["got"] is None:
        reasons.append(f"run_copy: control {sel!r} is not visible")
    elif not ok:
        reasons.append(f"run_copy: control {sel!r} reads {info['got']!r}, and the "
                       f"progress route says {want['text']!r}")
    if want is not None:
        if "night" in spec and want["night"] is None:
            reasons.append(f"run_copy: the progress route names no night for CONTINUE "
                           f"(no continue_night), and the walk expects night {spec['night']}")
        elif "night" in spec and want["night"] != spec["night"]:
            reasons.append(f"run_copy: the session continues night {want['night']}, "
                           f"and the walk expects night {spec['night']}")
        if want["banked"] < spec.get("min_banked", 0):
            reasons.append(f"run_copy: the session banked {want['banked']} subs, and the "
                           f"walk needs >= {spec['min_banked']} to show counts carry")
    return {"selector": sel, "ok": not reasons, **info}, reasons


# ------------------------------------------------- readouts, at one moment
#
# Module docstring point 7. `api_text` reads the page and the route one after
# the other, in two round trips, so on a live run "the page said 1-2 and the
# route said 1-2" can be two different moments that happened to agree, and a
# readout one publish behind can pass on the poll that lands between two
# publishes. This reads all of it in ONE page evaluation: the routes (the
# sequence state and the flow's progress, fetched together, by the page's own
# origin and cookies), then the DOM, then the routes again. The two route
# reads bracket the DOM read, and only a pair that AGREES, on every value a
# field renders and every value `require` names, says what the rig was at the
# moment the page was read; a pair that disagrees is the run moving, and is
# read again. The page must then EQUAL them: its text, whitespace collapsed,
# is the rendered template, whole (`"match": "holds"` asks only that it hold
# the template, `_holds_text`, for an element that carries a label too).
#
# Selectors here are CSS, because the DOM is read by the browser itself, not
# by Playwright's selector engine; `containing` picks the first visible match
# whose text contains a word (the PANELS row that says "shooting now", among
# rows that may say "set aside"), which is what `:has-text` did for a locator.

READOUTS_JS = """async ({flow, fields}) => {
  const get = async (path) => {
    try {
      const r = await fetch(path, {credentials: 'same-origin', cache: 'no-store'});
      let body = null;
      try { body = await r.json(); } catch (e) { body = null; }
      return {status: r.status, body};
    } catch (e) {
      return {status: 0, body: {error: String(e)}};
    }
  };
  const routes = async () => {
    const [state, progress] = await Promise.all([
      get('/api/sequence/state'),
      flow ? get('/api/flows/' + encodeURIComponent(flow) + '/progress')
           : Promise.resolve({status: 0, body: null})]);
    return {state, progress};
  };
  const shown = (el) => {
    const r = el.getBoundingClientRect();
    if (!(r.width > 0 && r.height > 0)) return false;
    return el.checkVisibility ? el.checkVisibility({checkOpacity: true, checkVisibilityCSS: true}) : true;
  };
  const text = (el) => (el.textContent || '').replace(/\\s+/g, ' ').trim();
  const before = await routes();
  const dom = fields.map((f) => {
    const all = Array.from(document.querySelectorAll(f.selector)).filter(shown);
    const el = f.containing ? all.find((e) => text(e).includes(f.containing)) : all[0];
    return el ? text(el) : null;
  });
  const after = await routes();
  return {before, dom, after};
}"""


def _read_side(read: dict | None, src: str) -> tuple[int, Any]:
    side = (read or {}).get(src) or {}
    return side.get("status", 0), side.get("body")


def _readouts_attempt(page, spec: dict) -> tuple[bool, dict]:
    """One reading (READOUTS_JS) and its verdict: (ok, info), where info holds
    the rendered values of both route reads and the DOM's text for every
    field, and `why` names the first problem. Never the routes' bodies: the
    sequence state carries site-derived fields (a meridian countdown), and a
    report is read by people who may not see them."""
    fields = spec.get("fields") or []
    flow = spec.get("flow")
    info: dict[str, Any] = {"rows": [], "why": None}
    if not fields:
        info["why"] = "readouts: no fields, so nothing on the page is held to the routes"
        return False, info
    args = {"flow": flow, "fields": [{"selector": f["selector"],
                                      "containing": f.get("containing")} for f in fields]}
    try:
        got = page.evaluate(READOUTS_JS, args)
    except Exception as exc:
        info["why"] = f"readouts: the page could not be read ({exc})"
        return False, info
    before, after = got.get("before"), got.get("after")
    srcs = {f.get("from", "state") for f in fields} | {"state"}
    srcs |= {k.partition(".")[0] for k in (spec.get("require") or {})}
    if flow or spec.get("same_session"):
        srcs.add("progress")
    for src in sorted(srcs):
        if src not in ("state", "progress"):
            info["why"] = f"readouts: {src!r} is neither route (state, progress)"
            return False, info
        if src == "progress" and not flow:
            info["why"] = "readouts: the progress route is read and no flow is named"
            return False, info
        for label, read in (("first", before), ("second", after)):
            status, _ = _read_side(read, src)
            if status != 200:
                path = "/api/sequence/state" if src == "state" else f"/api/flows/{flow}/progress"
                info["why"] = f"readouts: the {label} GET {path} -> {status}"
                return False, info
    problems: list[str] = []
    if spec.get("same_session"):
        # The readouts are this flow's run only while the session the rig is
        # writing is the one the flow's progress route counts (flowRunLive).
        ids = [(_field(_read_side(read, "state")[1], "session.id"),
                _field(_read_side(read, "progress")[1], "session.id"))
               for read in (before, after)]
        info["sessions"] = ids
        if any(not sid or sid != pid for sid, pid in ids):
            problems.append(f"readouts: the rig's session and the flow's are not one "
                            f"session in both reads ({ids})")
    for dotted, want in (spec.get("require") or {}).items():
        src, _, path = dotted.partition(".")
        vals = [_field(_read_side(read, src)[1], path) for read in (before, after)]
        info.setdefault("require", {})[dotted] = vals
        if any(v != want for v in vals):
            problems.append(f"readouts: the routes say {dotted} = {vals[0]!r} then "
                            f"{vals[1]!r}, and the walk needs {want!r}")
    dom = got.get("dom") or []
    for i, f in enumerate(fields):
        src = f.get("from", "state")
        want_1, holes_1 = _render(f["template"], _read_side(before, src)[1])
        want_2, holes_2 = _render(f["template"], _read_side(after, src)[1])
        text = dom[i] if i < len(dom) else None
        info["rows"].append({"selector": f["selector"], "from": src, "first": want_1,
                             "second": want_2, "dom": text})
        if holes_1 or holes_2:
            problems.append(f"readouts: {src} answered no {', '.join(holes_1 or holes_2)}, "
                            f"so {f['template']!r} has nothing to say")
        elif want_1 != want_2:
            problems.append(f"readouts: the two {src} reads disagree across the DOM read "
                            f"({want_1!r} then {want_2!r}): the run moved")
        elif text is None:
            problems.append(f"readouts: {f['selector']!r}"
                            + (f" containing {f['containing']!r}" if f.get("containing") else "")
                            + " is not visible")
        elif not (_holds_text(text, want_1) if f.get("match") == "holds" else text == want_1):
            problems.append(f"readouts: {f['selector']!r} reads {text!r}, and {src} says "
                            f"{want_1!r} in both reads")
    info["why"] = problems[0] if problems else None
    return not problems, info


def _check_readouts(page, spec: dict) -> tuple[dict, list[str]]:
    """`readouts: {"flow", "fields": [{"selector", "containing", "from":
    "state" | "progress", "template", "match"}], "same_session", "require":
    {"state.group.meridian_wait": true}, "timeout_ms"}`: see the section
    header. Polled until a reading passes or the time is up (the page and the
    rig move on their own clocks), and the last reading is reported whole:
    each field's value in the first and the second route read and on the
    page."""
    ok, info = _poll(page, spec.get("timeout_ms", 8000), lambda: _readouts_attempt(page, spec))
    return {"ok": ok, **info}, ([] if ok else [info["why"]])


# The page's own word for whether it is on a touch screen: a touch point to
# report and the touch event API to deliver it. A phone walk that ran in a
# desktop-shaped context would click where a person taps, and every layout
# decided by `(pointer: coarse)` or by touch would be the desktop's.
TOUCH_JS = """() => ({max_touch_points: navigator.maxTouchPoints || 0,
                     touch_events: 'ontouchstart' in window,
                     coarse: matchMedia('(pointer: coarse)').matches})"""


def _check_touch(page, want: bool) -> tuple[dict, list[str]]:
    """`touch: true | false`: the page reports a touch screen exactly when the
    route says it is on one (a touch point and the touch event API both)."""
    try:
        got = page.evaluate(TOUCH_JS)
    except Exception as exc:
        return {"ok": False, "error": str(exc)}, [f"touch: the page could not be asked ({exc})"]
    touch = bool(got.get("max_touch_points", 0) > 0 and got.get("touch_events"))
    ok = touch == bool(want)
    info = {"ok": ok, "want": bool(want), **got}
    if ok:
        return info, []
    if want:
        return info, [f"touch: this is a phone walk and the page reports no touch screen "
                      f"({got}): its taps would be a desktop's clicks"]
    return info, [f"touch: this is a desktop walk and the page reports a touch screen ({got})"]


def _check_count(page, specs: list[dict]) -> tuple[list[dict], list[str]]:
    """`count: [{"selector", "equals" | "min"}]`: how many VISIBLE matches. A
    view-only sheet has no DONE (`equals: 0`), a disabled fieldset
    (`min: 1`), and a wizard review of one TARGET one block (`equals: 1`)."""
    results: list[dict] = []
    reasons: list[str] = []
    for spec in specs:
        sel = spec["selector"]
        n = len(_visible_css_matches(page, sel))
        ok = (n == spec["equals"]) if "equals" in spec else n >= spec.get("min", 1)
        results.append({"selector": sel, "ok": ok, "count": n})
        if not ok:
            need = f"== {spec['equals']}" if "equals" in spec else f">= {spec.get('min', 1)}"
            reasons.append(f"count {sel!r} is {n}, need {need}")
    return results, reasons


def _check_reachable_all(page, specs: list[dict]) -> tuple[list[dict], list[str]]:
    """`reachable_all: [{"within", "selector", "min_height"}]`: EVERY visible
    `selector` (default "button") inside the first visible `within` answers
    the hit test once scrolled into view, as `reachable` asks of named
    controls, and with `min_height` is at least that tall. Named ones cover
    the controls a walk presses; this covers the ones nobody thought to name,
    which is where a covered control hides. One reason per spec, naming the
    first failure and the count, as `text_intact` does.

    The height floor is the probe-visible-is-not-sized class again (#189 S5):
    on 2026-09-28 the phone stage sheet's + ADD STAGE, a 52 px button, was
    drawn 20 px tall under a long stage list (a flex item of the sheet body's
    column shrinks to its content's height), and it answered the hit test at
    its centre like any other."""
    results: list[dict] = []
    reasons: list[str] = []
    for spec in specs:
        within = spec["within"]
        sel = spec.get("selector", "button")
        scope = _visible_css_matches(page, within)
        if not scope:
            results.append({"within": within, "ok": False, "why": "not visible"})
            reasons.append(f"reachable_all: container {within!r} is not visible")
            continue
        loc = scope[0].locator(sel)
        failed: list[str] = []
        seen = 0
        try:
            n = loc.count()
        except Exception:
            n = 0
        for i in range(n):
            el = loc.nth(i)
            try:
                if not el.is_visible():
                    continue
            except Exception:
                continue
            seen += 1
            _bring_into_view(page, el)
            hit = _hit_test(el)
            box = _box_for(el)
            if not (hit and hit.get("inside")):
                failed.append(f"{_text_of(el)[:40]!r} is covered: the point at its "
                              f"centre is not the control ({hit})")
            elif spec.get("min_height") and (box is None or box["height"] < spec["min_height"]):
                failed.append(f"{_text_of(el)[:40]!r} is {box and box['height']}px tall, "
                              f"need >= {spec['min_height']}px (box {box})")
        results.append({"within": within, "selector": sel, "ok": not failed and seen > 0,
                        "seen": seen, "failed": failed})
        if seen == 0:
            reasons.append(f"reachable_all: no visible {sel!r} inside {within!r}")
        elif failed:
            reasons.append(f"reachable_all: {failed[0]} ({len(failed)} of {seen} "
                           f"{sel!r} inside {within!r})")
    return results, reasons


# Where each element's TEXT sits vertically: the centre of its first glyph
# box (the element's own box says nothing - a flex row stretches every item
# to the line's height whatever its words do), with the box's top and bottom
# so the caller can tell which texts share a line.
TEXT_CENTRE_JS = """(els) => els.map((el) => {
  const range = document.createRange();
  range.selectNodeContents(el);
  const r = Array.from(range.getClientRects()).find(x => x.width > 0 && x.height > 0);
  const q = (v) => Math.round(v * 10) / 10;
  return r ? {text: (el.textContent || '').trim().slice(0, 30), top: q(r.top),
              bottom: q(r.bottom), centre: q((r.top + r.bottom) / 2)} : null;
})"""


def _check_aligned(page, specs: list[dict]) -> tuple[list[dict], list[str]]:
    """`aligned: [{"selector", "tolerance"}]`: the visible matches whose text
    shares a line (their glyph boxes overlap vertically) have their text
    centred within `tolerance` px (default 2) of each other. Found on the
    wizard's step rail (#189 S6): a step behind the current one is a 32 px
    button and the others plain words, and the plain ones sat at the top of a
    stretched flex line, below-the-line and above-the-line on one row, which
    every size and clip check reads as fine."""
    results: list[dict] = []
    reasons: list[str] = []
    for spec in specs:
        sel = spec["selector"]
        tol = spec.get("tolerance", 2)
        found = _visible_css_matches(page, sel)
        try:
            boxes = [b for b in page.evaluate(TEXT_CENTRE_JS, [el.element_handle() for el in found]) if b]
        except Exception as exc:
            results.append({"selector": sel, "ok": False, "error": str(exc)})
            reasons.append(f"aligned {sel!r} could not be measured: {exc}")
            continue
        lines: list[list[dict]] = []
        for b in sorted(boxes, key=lambda b: b["top"]):
            line = next((ln for ln in lines if any(b["top"] < o["bottom"] and o["top"] < b["bottom"]
                                                    for o in ln)), None)
            if line is None:
                lines.append([b])
            else:
                line.append(b)
        spreads = [max(b["centre"] for b in ln) - min(b["centre"] for b in ln) for ln in lines]
        worst = max(spreads, default=0.0)
        ok = len(boxes) >= spec.get("min_count", 2) and worst <= tol
        results.append({"selector": sel, "ok": ok, "lines": lines, "spread": round(worst, 1)})
        if len(boxes) < spec.get("min_count", 2):
            reasons.append(f"aligned {sel!r}: {len(boxes)} text(s) to line up, need >= "
                           f"{spec.get('min_count', 2)}")
        elif worst > tol:
            line = lines[spreads.index(worst)]
            reasons.append(f"aligned {sel!r}: text on one line is {round(worst, 1)}px out of "
                           f"line, need <= {tol}px ({[(b['text'], b['centre']) for b in line]})")
    return results, reasons


def _flow_ids(context, base: str) -> set[str] | None:
    """The ids the flow library holds (GET /api/flows), or None if unread."""
    try:
        resp = context.request.get(base.rstrip("/") + "/api/flows")
        rows = resp.json() if resp.ok else None
    except Exception:
        return None
    if isinstance(rows, dict):
        rows = rows.get("flows")
    if not isinstance(rows, list):
        return None
    return {str(r.get("id")) for r in rows if isinstance(r, dict)}


def _check_new_flow(page, spec: dict, before: set[str] | None) -> tuple[dict, list[str]]:
    """`new_flow: {"target_nodes"}`: the walk saved EXACTLY ONE flow (the
    library's ids after the walk, less those before it), and that flow's
    graph holds exactly `target_nodes` TARGET nodes. The wizard's GENERATE
    saves one flow whatever the framing (spec S6, Revision 2 ruling 4), and a
    mosaic is ONE TARGET with its panels, never one per panel; the review on
    screen is the server's compile of it, which this reads at the source."""
    reasons: list[str] = []
    after = _flow_ids(page.context, _origin(page))
    info: dict[str, Any] = {"ok": False}
    if before is None or after is None:
        reasons.append("new_flow: the flow library could not be read before and "
                       "after the walk")
        return info, reasons
    new = sorted(after - before)
    info["new"] = new
    if len(new) != 1:
        reasons.append(f"new_flow: the walk saved {len(new)} flow(s) ({new}), "
                       f"need exactly 1")
        return info, reasons
    status, rec = _api_json(page, f"/api/flows/{quote(new[0])}")
    nodes: list = []
    if status == 200 and isinstance(rec, dict):
        nodes = (rec.get("graph") or {}).get("nodes") or []
    targets = [n for n in nodes if isinstance(n, dict) and n.get("type") == "target"]
    info.update(id=new[0], name=(rec or {}).get("name"),
                types=[n.get("type") for n in nodes if isinstance(n, dict)],
                targets=[(n.get("params") or {}).get("name") for n in targets])
    want = spec.get("target_nodes", 1)
    if status != 200:
        reasons.append(f"new_flow: GET /api/flows/{new[0]} -> {status}")
    elif len(targets) != want:
        reasons.append(f"new_flow: the saved flow {new[0]!r} has {len(targets)} TARGET "
                       f"node(s) {info['targets']}, need exactly {want}")
    info["ok"] = not reasons
    return info, reasons


# The store's own key for the Plan the classic Plan editor shows (store.ts
# PLAN_KEY, written by `setPlan`). The side channel S6 deleted,
# `addTargetsToPlan`, wrote a door's panels there as Plan targets.
PLAN_KEY = "astrodeck-plan"


def _check_no_plan_targets(page) -> tuple[dict, list[str]]:
    """`no_plan_targets: true`: the browser holds no Plan with targets in it
    (PLAN_KEY absent, or its `targets` empty). A door that still fed the Plan
    beside the wizard would leave a TARGET there for every panel, and the
    wizard's review would read exactly the same."""
    try:
        raw = page.evaluate("(k) => { try { return localStorage.getItem(k); } catch (e) "
                            "{ return 'unreadable: ' + e; } }", PLAN_KEY)
    except Exception as exc:
        raw = f"unreadable: {exc}"
    if raw is None:
        return {"ok": True, "plan": None}, []
    if isinstance(raw, str) and raw.startswith("unreadable"):
        return {"ok": False, "plan": raw}, [f"no_plan_targets: localStorage {raw}"]
    try:
        targets = (json.loads(raw) or {}).get("targets") or []
    except (ValueError, AttributeError):
        return {"ok": False, "plan": raw[:200]}, [
            f"no_plan_targets: {PLAN_KEY} is not a plan: {raw[:120]!r}"]
    names = [t.get("name") for t in targets if isinstance(t, dict)]
    if targets:
        return {"ok": False, "targets": names}, [
            f"no_plan_targets: the Plan holds {len(targets)} target(s) {names[:6]}"]
    return {"ok": True, "targets": []}, []


def _glob_re(pattern: str) -> re.Pattern:
    """A URL glob as Playwright writes one: `**` any run of characters, `*`
    any run without a '/', everything else literal."""
    out = ""
    i = 0
    while i < len(pattern):
        if pattern.startswith("**", i):
            out += ".*"
            i += 2
        elif pattern[i] == "*":
            out += "[^/]*"
            i += 1
        else:
            out += re.escape(pattern[i])
            i += 1
    return re.compile(out + r"(\?.*)?$")


class _Forbidden:
    """`forbid_requests: [{"method", "url"}]`: requests the walk must never
    cause, recorded from the moment the page opens. START OVER behind a
    confirm is the case (spec 5.9): a CANCEL that still posted
    `{fresh: true}` would read the same on screen once the page settled."""

    def __init__(self, page, specs: list[dict]) -> None:
        self.rules = [(s.get("method", "").upper(), _glob_re(s["url"]), s) for s in specs]
        self.seen: list[dict] = []
        page.on("request", self._on_request)

    def _on_request(self, request) -> None:
        for method, rx, spec in self.rules:
            if (not method or request.method == method) and rx.match(request.url):
                self.seen.append({"method": request.method, "url": request.url})

    def reasons(self) -> list[str]:
        return [f"forbidden request made: {r['method']} {r['url']}" for r in self.seen]


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


# The checks a walk may make in the middle (`{"check": {...}}`), each the
# route-level check of the same name.
MID_WALK_CHECKS = {
    "aligned": lambda page, spec: _check_aligned(page, spec),
    "text_intact": lambda page, spec: _check_text_intact(page, spec),
    "text_expect": lambda page, spec: _check_text_expect(page, spec),
    "count": lambda page, spec: _check_count(page, spec),
    "boxes": lambda page, spec: _check_boxes(page, spec),
    "reachable": lambda page, spec: _check_reachable(page, spec),
    "reachable_all": lambda page, spec: _check_reachable_all(page, spec),
    "api_text": lambda page, spec: _check_api_text(page, spec),
    "readouts": lambda page, spec: _check_readouts(page, spec),
    "run_copy": lambda page, spec: _check_run_copy(page, spec),
    "contained": lambda page, spec: _check_contained(page, spec),
    "apart": lambda page, spec: _check_apart(page, spec),
    "scrollbar": lambda page, spec: _check_scrollbar(page, spec),
}


def _mid_walk_checks(page, checks: dict) -> list[str]:
    """Run the named checks NOW, on the view the walk is passing through, and
    return their reasons. A route's own checks run at the end of its walk,
    which is the wrong moment for a state the walk leaves: the wizard's step
    rail has buttons only for steps behind the current one and before
    GENERATE, so at the end of its walk there is nothing left to misalign.
    An unknown name is a reason, never a silent pass."""
    reasons: list[str] = []
    for name, spec in checks.items():
        fn = MID_WALK_CHECKS.get(name)
        if fn is None:
            reasons.append(f"unknown mid-walk check {name!r}")
            continue
        _, why = fn(page, spec)
        reasons.extend(why)
    return reasons


#: Values a walk remembered (`remember_api`), by name, for a later step to
#: compare against (`wait_api` `differs_from`), in this walk or a later route
#: of the same probe run: the S7 straddle remembers the pier side on the
#: phone, before the meridian, and the desktop walk waits for the flip to
#: change it. Emptied when a probe run starts (`main`).
_MEMO: dict[str, Any] = {}


def _wait_api(page, spec: dict, wait_ms: int) -> dict:
    """Poll GET `spec["get"]` until `spec["field"]` is >= `min`, == `equals`,
    or (`differs_from`, #189 S7) a value that is set, not one of `not_in` and
    not the one remembered under that name (a number the server did not send
    never satisfies `min`, and nothing remembered satisfies nothing)."""
    def attempt():
        status, body = _api_json(page, spec["get"])
        value = _field(body, spec["field"]) if status == 200 else None
        if "differs_from" in spec:
            name = spec["differs_from"]
            if name not in _MEMO:
                return False, value
            return (value is not None and value not in spec.get("not_in", [])
                    and value != _MEMO[name]), value
        if "equals" in spec:
            return value == spec["equals"], value
        ok = isinstance(value, (int, float)) and not isinstance(value, bool) \
            and value >= spec.get("min", 1)
        return ok, value

    ok, value = _poll(page, wait_ms, attempt)
    return {"ok": ok, "value": value}


def _remember_api(page, spec: dict, wait_ms: int) -> dict:
    """`remember_api: {"get", "field", "as", "not_in"}`: poll GET `get` until
    `field` holds a value that is set and not one of `not_in`, and remember
    it in `_MEMO` under `as`. A value the server never gave is remembered as
    nothing and fails the step: "it changed" is no claim about an unread
    side."""
    def attempt():
        status, body = _api_json(page, spec["get"])
        value = _field(body, spec["field"]) if status == 200 else None
        return value is not None and value not in spec.get("not_in", []), value

    ok, value = _poll(page, wait_ms, attempt)
    if ok:
        _MEMO[spec["as"]] = value
    return {"ok": ok, "value": value}


def _visible_text(page, selector: str) -> str | None:
    """The first visible match's text (`_text_of`), or None."""
    found = _visible_css_matches(page, selector)
    return _text_of(found[0]) if found else None


def _wait_change(page, selector: str, wait_ms: int) -> dict:
    """`{"wait_change": <selector>}`: read the first visible match's text now
    (it must be there and say something), then wait for it to say something
    else. A person following a run watches a readout move; a walk that
    graded the readouts once would pass a page that froze on its first
    publish."""
    first = _visible_text(page, selector)
    if not first:
        return {"ok": False, "from": first, "to": None}

    def attempt():
        now = _visible_text(page, selector)
        return bool(now) and now != first, now

    ok, now = _poll(page, wait_ms, attempt)
    return {"ok": ok, "from": first, "to": now}


def _run_clicks(page, clicks: list[dict], shot_dir: Path | None = None,
                shot_stem: str = "", tap: bool = False) -> list[dict]:
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
    framing was on screen before a link to another's).

    Four more (#189 S5, S6). `{"check": {<name>: <spec>}}` runs route checks
    at that moment (`_mid_walk_checks`), for a state the walk passes through
    and leaves. `{"fill": <selector>, "value": <text>}` types
    into a field, as a person does into the catalogue search or a wizard's
    PA box; it is required, like a door. `{"wait_api": {"get", "field",
    "min" | "equals"}}` waits, up to `wait_ms`, for the server's answer to
    say so (a premise the page does not show, such as the first banked sub
    on a desktop with no FRAMES readout); it is required too. `{"shot":
    <name>}` saves `<shot>-<name>.png` into `shot_dir` without clicking: the
    evidence of a state the walk passes through and leaves, such as START
    OVER's confirm before its CANCEL.

    Two more, and taps (#189 S7). `{"wait_change": <selector>}` waits, up to
    `wait_ms`, for a readout to say something other than it said when the
    step began (`_wait_change`). `{"remember_api": {"get", "field", "as",
    "not_in"}}` remembers what the rig says (`_remember_api`), for a later
    `wait_api` with `differs_from`. Both are required. With `tap` (a route
    with `"touch": true`, a phone walk) every click is a tap, as a finger
    makes it: a phone walk that clicked would pass a control that answers a
    mouse and not a finger."""
    log: list[dict] = []
    for i, step in enumerate(clicks):
        if "goto" in step:
            page.evaluate("(h) => { window.location.hash = h; }", step["goto"])
            page.wait_for_timeout(300)
            log.append({"text": step["goto"], "action": "goto"})
            continue
        if "shot" in step:
            name = "".join(c if c.isalnum() or c in "-_" else "_" for c in step["shot"])
            if shot_dir is not None:
                try:
                    page.screenshot(path=str(shot_dir / f"{shot_stem}-{name}.png"))
                    log.append({"text": step["shot"], "action": "shot"})
                except Exception as exc:
                    log.append({"text": step["shot"], "action": "shot-failed", "error": str(exc)})
            continue
        if "check" in step:
            why = _mid_walk_checks(page, step["check"])
            log.append({"text": "check " + ",".join(sorted(step["check"])),
                        "action": "check", "reasons": why})
            continue
        if "wait_api" in step:
            spec = step["wait_api"]
            desc = f"wait_api {spec.get('get')} {spec.get('field')}"
            got = _wait_api(page, spec, step.get("wait_ms", 8000))
            if got["ok"]:
                log.append({"text": desc, "action": "seen", "value": got["value"]})
                continue
            log.append({"text": desc, "action": "missing", "required": True,
                        "reason": f"the server still says {got['value']!r} after "
                                  f"{step.get('wait_ms', 8000)} ms"})
            log.extend({"text": _step_desc(s), "action": "not-run"} for s in clicks[i + 1:])
            break
        if "wait_change" in step:
            desc = f"wait_change {step['wait_change']}"
            got = _wait_change(page, step["wait_change"], step.get("wait_ms", 8000))
            if got["ok"]:
                log.append({"text": desc, "action": "changed", "from": got["from"],
                            "to": got["to"]})
                continue
            why = ("it was not visible, or said nothing, when the step began"
                   if not got["from"] else
                   f"it still read {got['to']!r} after {step.get('wait_ms', 8000)} ms")
            log.append({"text": desc, "action": "missing", "required": True, "reason": why})
            log.extend({"text": _step_desc(s), "action": "not-run"} for s in clicks[i + 1:])
            break
        if "remember_api" in step:
            spec = step["remember_api"]
            desc = f"remember_api {spec.get('get')} {spec.get('field')} as {spec.get('as')}"
            got = _remember_api(page, spec, step.get("wait_ms", 8000))
            if got["ok"]:
                log.append({"text": desc, "action": "remembered", "value": got["value"]})
                continue
            log.append({"text": desc, "action": "missing", "required": True,
                        "reason": f"the server said {got['value']!r} throughout "
                                  f"{step.get('wait_ms', 8000)} ms"})
            log.extend({"text": _step_desc(s), "action": "not-run"} for s in clicks[i + 1:])
            break
        if "wait_for" in step:
            step = {**step, "selector": step["wait_for"], "required": True}
        if "fill" in step:
            step = {**step, "selector": step["fill"], "required": True}
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
            if "fill" in step:
                matches[0].fill(str(step.get("value", "")), timeout=5000)
                page.wait_for_timeout(step.get("settle_ms", 800))
                log.append({"text": desc, "action": "fill", "value": step.get("value")})
                continue
            kwargs: dict[str, Any] = {"timeout": 5000}
            if step.get("position"):
                kwargs["position"] = step["position"]
            if tap:
                matches[0].tap(**kwargs)
            else:
                matches[0].click(**kwargs)
            page.wait_for_timeout(300)
            log.append({"text": desc, "action": "tap" if tap else "click",
                        "matched": len(matches)})
        except Exception as exc:
            log.append({"text": desc, "action": "click-failed", "error": str(exc),
                        "required": required})
            if required:
                log.extend({"text": _step_desc(s), "action": "not-run"}
                           for s in clicks[i + 1:])
                break
    return log


def _step_desc(step: dict) -> str:
    if "check" in step:
        return "check " + ",".join(sorted(step["check"]))
    if "wait_api" in step:
        return f"wait_api {step['wait_api'].get('get')} {step['wait_api'].get('field')}"
    if "remember_api" in step:
        return (f"remember_api {step['remember_api'].get('get')} "
                f"{step['remember_api'].get('field')}")
    if "wait_change" in step:
        return f"wait_change {step['wait_change']}"
    return (step.get("goto") or step.get("wait_for") or step.get("fill")
            or step.get("shot") or step.get("selector") or step.get("text", ""))


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
    # Likewise a request the walk must not cause, and the flow library as it
    # stood before the walk, so `new_flow` counts only what the walk saved.
    forbidden = _Forbidden(page, route["forbid_requests"]) if route.get("forbid_requests") else None
    flows_before = _flow_ids(page.context, base) if route.get("new_flow") else None
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
        # A route on a touch screen is walked by taps (module docstring 7),
        # and says so first: `touch` is graded before the first step, so a
        # phone walk in a context with no touch fails on that, by name,
        # rather than only on the tap it cannot make.
        if "touch" in route:
            usable["touch"], why = _check_touch(page, route["touch"])
            reasons.extend(why)
        click_log = _run_clicks(page, route.get("click", []), width_dir, shot_name,
                                tap=route.get("touch") is True)
        for i, step in enumerate(click_log):
            if step.get("required") and step["action"] in ("missing", "click-failed"):
                reasons.append(f"required step {step['text']!r} failed: "
                               f"{step.get('reason') or step.get('error')}")
            if step["action"] == "check":
                reasons.extend(f"mid-walk check at step {i + 1}: {r}" for r in step["reasons"])
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
            usable.update(_run_usable_checks(page, route, gate, width_dir, shot_name,
                                             reasons, flows_before))
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
    # Graded whatever the marker said: a forbidden request is something the
    # walk DID, not a measurement taken on the wrong screen, and START OVER
    # posting past its confirm matters most exactly when the page went wrong.
    if forbidden is not None:
        reasons.extend(forbidden.reasons())
        usable["forbidden_requests"] = list(forbidden.seen)

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
                       shot_name: str, reasons: list[str],
                       flows_before: set[str] | None = None) -> dict[str, Any]:
    """Module docstring points 5 and 6, in the order the Target modal needs:
    the gate first, because its hold must stay under the UI's own 15 s fetch
    timeout (see the gate section); then the page against the rig, while the
    rig is where the walk left it (a live run moves on); then the checks
    that look at the view as it stands, then the labels, whose scrolling
    moves it."""
    out: dict[str, Any] = {}
    if gate is not None:
        out["gate"], why = _check_gate(page, gate, width_dir, shot_name)
        reasons.extend(why)
    if route.get("readouts"):
        out["readouts"], why = _check_readouts(page, route["readouts"])
        reasons.extend(why)
    if route.get("api_text"):
        out["api_text"], why = _check_api_text(page, route["api_text"])
        reasons.extend(why)
    if route.get("run_copy"):
        out["run_copy"], why = _check_run_copy(page, route["run_copy"])
        reasons.extend(why)
    if route.get("text_expect"):
        out["text_expect"], why = _check_text_expect(page, route["text_expect"])
        reasons.extend(why)
    if route.get("count"):
        out["count"], why = _check_count(page, route["count"])
        reasons.extend(why)
    if route.get("new_flow"):
        out["new_flow"], why = _check_new_flow(page, route["new_flow"], flows_before)
        reasons.extend(why)
    if route.get("no_plan_targets"):
        out["no_plan_targets"], why = _check_no_plan_targets(page)
        reasons.extend(why)
    if route.get("boxes"):
        out["boxes"], why = _check_boxes(page, route["boxes"])
        reasons.extend(why)
    # Where boxes sit (module docstring point 8), before `reachable` and
    # `labels` scroll the view about: each waits for its own premise.
    if route.get("scrollbar"):
        out["scrollbar"], why = _check_scrollbar(page, route["scrollbar"])
        reasons.extend(why)
    if route.get("apart"):
        out["apart"], why = _check_apart(page, route["apart"])
        reasons.extend(why)
    if route.get("contained"):
        out["contained"], why = _check_contained(page, route["contained"])
        reasons.extend(why)
    if route.get("reachable"):
        out["reachable"], why = _check_reachable(page, route["reachable"])
        reasons.extend(why)
    if route.get("reachable_all"):
        out["reachable_all"], why = _check_reachable_all(page, route["reachable_all"])
        reasons.extend(why)
    if route.get("aligned"):
        out["aligned"], why = _check_aligned(page, route["aligned"])
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


def _seed(request, base: str, ops: list[dict], ctx: dict | None = None) -> list[dict]:
    """Seed the server before a walk, through the browser context's own
    request client, so an `--auth` run seeds as the signed-in role and a
    refused seed says which role was refused.

    Three ops: `require_sim` (see `_seed_require_sim`), `save_flow` (see
    `_seed_save_flow`) and `copy_flow: {"from",
    "id", "name", "folder", "set_params", "expect_node"}`, which reads a flow
    (a shipped Example), saves a copy under a new id through
    `POST /api/flows` and reads the copy back. The copy exists
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
    their TARGET's name passes vacuously if the names never differed.

    Four more (#189 S7): `set_site`, `daylight_site`, `run_flow` and
    `seed_session`, each described where it is implemented; `ctx` carries
    what `seed_session` needs of the private server (its directories and its
    venv), from probe.py's own arguments."""
    base = base.rstrip("/")
    log: list[dict] = []
    for op in ops:
        kinds = sorted(k for k in op if k not in ("widths", "routes"))
        if kinds == ["require_sim"]:
            log.append(_seed_require_sim(request, base))
            continue
        if kinds == ["save_flow"]:
            log.append(_seed_save_flow(request, base, op["save_flow"]))
            continue
        if kinds == ["set_site"]:
            log.append(_seed_set_site(request, base, op["set_site"]))
            continue
        if kinds == ["daylight_site"]:
            log.append(_seed_daylight_site(request, base, op["daylight_site"]))
            continue
        if kinds == ["run_flow"]:
            log.append(_seed_run_flow(request, base, op["run_flow"]))
            continue
        if kinds == ["seed_session"]:
            log.append(_seed_session(request, base, op["seed_session"], ctx or {}))
            continue
        spec = op.get("copy_flow")
        if spec is None or kinds != ["copy_flow"]:
            raise SeedError(f"unknown seed op {kinds!r}")
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
        _expect_node(spec["id"], stored, spec.get("expect_node"))
        log.append({"copy_flow": spec["id"], "from": spec["from"],
                    "readonly": stored.get("readonly")})
    return log


def _expect_node(flow_id: str, stored: dict, want: dict | None) -> None:
    """`expect_node`, read off the flow AS STORED (see `_seed`)."""
    if not want:
        return
    node = next((n for n in stored.get("graph", {}).get("nodes", [])
                 if n.get("id") == want["id"]), None)
    if node is None or node.get("type") != want.get("type", node.get("type")):
        raise SeedError(f"the copy {flow_id!r} has no {want.get('type')} "
                        f"node {want['id']!r} (got {node!r:.200})")
    params = node.get("params") or {}
    try:
        panels = int(params.get("rows", 1)) * int(params.get("cols", 1))
    except (TypeError, ValueError):
        panels = 0
    if panels < want.get("min_panels", 1):
        raise SeedError(f"the copy {flow_id!r}'s node {want['id']!r} is "
                        f"{panels} panel(s), and the walk grades a mosaic "
                        f"(need >= {want['min_panels']})")
    for key, value in want.get("params", {}).items():
        if params.get(key) != value:
            raise SeedError(f"the copy {flow_id!r}'s node {want['id']!r} "
                            f"stored {key}={params.get(key)!r}, not {value!r}")


# The engine states in which a run still owns the rig (lastSessionFrame.ts
# `runIsLive`: running, paused, a cloud hold, the wind-down of an abort).
LIVE_STATES = {"running", "paused", "holding", "aborting"}


def _seed_require_sim(request, base: str) -> dict:
    """`require_sim: true`: refuse to walk unless the server's rig is the
    simulator (`GET /api/status` says `mode` "sim", as server_ctl.py waits
    for). A route file whose walks press RUN moves whatever rig the server
    drives: s5-run-phone slews, rotates and exposes, and pointed by mistake at
    a real rig's server (a `--base`, or `--port 8800` on the rig's own PC) it
    would start a run on the telescope. `fresh` and `runnable` often refuse
    there too, but only by accident (a run already going, a field the TARGET
    was not framed for), so this says it on purpose, before anything is saved
    or pressed (found by the S56-PROBE verifier, 2026-09-28: the route file's
    "never the rig" was a sentence nothing kept). The refusal quotes the mode
    and nothing else of the status, whose body can carry the site."""
    try:
        resp = request.get(f"{base}/api/status")
        status = resp.status
        body = resp.json() if resp.ok else None
    except Exception as exc:
        raise SeedError(f"GET /api/status could not be read ({exc.__class__.__name__}), "
                        f"so nothing says this server's rig is the simulator") from None
    if not isinstance(body, dict):
        raise SeedError(f"GET /api/status -> {status}, so nothing says this server's "
                        f"rig is the simulator")
    mode = body.get("mode")
    if mode != "sim":
        raise SeedError(f"these walks press RUN, and this server's rig is mode={mode!r}, "
                        f"not the simulator: start one with `server_ctl.py start "
                        f"--fresh --port <any but 8800>`")
    return {"require_sim": True, "mode": mode}


def _seed_save_flow(request, base: str, spec: dict) -> dict:
    """`save_flow: {"id", "name", "folder", "graph", "expect_node", "runnable",
    "fresh"}`:
    save a flow DRAWN in the route file through `POST /api/flows` and read it
    back (#189 S5). A run-mode walk needs a flow it can start on the
    simulator and still catch live: the eighth Example waits for dusk (its
    DUSK node), autofocuses and guides, and its 120 s subs would keep a walk
    waiting minutes for a first frame; a TARGET, a FILTER CYCLE of short subs
    and a report start at once and bank a sub a minute in.

    `fresh: true` refuses a server on which the walk would not be the flow's
    first run: one whose progress route already names a session for this
    flow (RUN would read CONTINUE before the walk pressed anything, and a
    walk that asserts RUN, then STOP, then CONTINUE on night 2 would be
    grading an earlier walk's ledger), or whose engine is running something
    (RUN would be refused "already running"). Both are said in words, since
    the fix is to start the server again with `server_ctl.py start --fresh`.

    `runnable: true` compiles the stored flow on this server and refuses a
    compile that lists a LOSS (any `unmapped` entry but a `note`, as the run
    route's `losses` reads it): RUN would then stop on a question the walk
    does not answer, and the walk would fail three doors later on a readout
    that never came. The route's TARGET is framed for the simulator's field,
    and a changed simulator is exactly this case.

    `meridian: {"node", "minutes_east"}` (#189 S7, the straddle) writes that
    TARGET's RA before the save: the local sidereal time NOW at the site the
    server holds (GET /api/site, which `set_site` saved), plus `minutes_east`
    of sidereal time, so the block's centre crosses the meridian that many
    minutes after the seed, by the probe's clock. It is computed here and
    not written into the route file, because an RA fixed in the file
    straddles the meridian at one hour of one day; and from the server's
    site, never a second copy, so the walk's meridian is the engine's."""
    graph = spec["graph"]
    if spec.get("meridian"):
        graph = _graph_with_meridian_ra(request, base, graph, spec["meridian"])
    record = {"id": spec["id"], "name": spec["name"],
              "folder": spec.get("folder", "My flows"), "graph": graph}
    saved = request.post(f"{base}/api/flows", data={"flow": record})
    if not saved.ok:
        raise SeedError(f"POST /api/flows ({spec['id']!r}) -> {saved.status}: "
                        f"{saved.text()[:300]}")
    back = request.get(f"{base}/api/flows/{quote(spec['id'])}")
    if not back.ok:
        raise SeedError(f"the flow {spec['id']!r} does not read back: GET -> {back.status}")
    stored = back.json()
    _expect_node(spec["id"], stored, spec.get("expect_node"))
    if spec.get("runnable"):
        comp = request.post(f"{base}/api/flows/{quote(spec['id'])}/compile", data={})
        if not comp.ok:
            raise SeedError(f"POST /api/flows/{spec['id']}/compile -> {comp.status}")
        lost = [u for u in (comp.json() or {}).get("unmapped") or []
                if isinstance(u, dict) and u.get("level") != "note"]
        if lost:
            raise SeedError(f"{spec['id']!r} would not run without a question on this "
                            f"server: {lost[0].get('detail', lost[0])!r:.240}")
    if spec.get("fresh"):
        prog = request.get(f"{base}/api/flows/{quote(spec['id'])}/progress")
        session = (prog.json() or {}).get("session") if prog.ok else "unread"
        if session is not None:
            raise SeedError(f"the server already holds a session for {spec['id']!r} "
                            f"({session!r:.160}), so this walk would not be its first "
                            f"run: start the server again with `server_ctl.py start --fresh`")
        state = request.get(f"{base}/api/sequence/state")
        engine = (state.json() or {}).get("state") if state.ok else "unread"
        if engine in LIVE_STATES or engine == "unread":
            raise SeedError(f"the engine is {engine!r}, so RUN would be refused: stop "
                            f"the run, or start the server again with --fresh")
    return {"save_flow": spec["id"], "readonly": stored.get("readonly")}


# --------------------------------------------------- S7 seeds: sky and sessions
#
# Module docstring point 7. The four S7 scenarios need the server in states a
# drawn flow alone cannot put it in: a site (the straddle's meridian is a
# site's), a site where the sun is up (the CONTINUE walk's armed session must
# not be resumed under it), a flow that has run and banked frames, and that
# run moved onto an earlier observing night. Each is made through the
# server's own API, except the last, which only the server's own store code
# can write (seed_session.py, run by the server's venv).

def _lst_hours(lon_deg: float, t: float) -> float:
    """Local sidereal time in hours at `t`: the server's own formula
    (server/astrodeck/catalog/coords.py `lst_hours`), so a TARGET this seeds
    straddles the meridian the engine computes, to the second."""
    d = t / 86400.0 + 2440587.5 - 2451545.0
    return ((18.697374558 + 24.06570982441908 * d) % 24.0 + lon_deg / 15.0) % 24.0


def _sun_radec(t: float) -> tuple[float, float]:
    """The Sun's (RA hours, Dec deg) at `t`, to about 0.01 deg (the Astronomical
    Almanac's low-precision formula): enough to put a site under a noon sun,
    or to say that one is in the dark."""
    n = t / 86400.0 + 2440587.5 - 2451545.0
    L = (280.460 + 0.9856474 * n) % 360.0
    g = math.radians((357.528 + 0.9856003 * n) % 360.0)
    lam = math.radians(L + 1.915 * math.sin(g) + 0.020 * math.sin(2 * g))
    eps = math.radians(23.439 - 0.0000004 * n)
    ra = math.degrees(math.atan2(math.cos(eps) * math.sin(lam), math.cos(lam))) % 360.0
    dec = math.degrees(math.asin(math.sin(eps) * math.sin(lam)))
    return ra / 15.0, dec


def _sun_altitude(lat: float, lon: float, t: float) -> float:
    """The Sun's altitude in degrees at (`lat`, `lon`) and `t`."""
    ra, dec = _sun_radec(t)
    ha = math.radians((_lst_hours(lon, t) - ra) * 15.0)
    la, de = math.radians(lat), math.radians(dec)
    return math.degrees(math.asin(math.sin(la) * math.sin(de)
                                  + math.cos(la) * math.cos(de) * math.cos(ha)))


def _subsolar_lon(t: float) -> float:
    """The longitude where the Sun is on the meridian at `t`, east positive,
    in (-180, 180]."""
    ra, _ = _sun_radec(t)
    gmst = _lst_hours(0.0, t)
    lon = ((ra - gmst) * 15.0) % 360.0
    return lon - 360.0 if lon > 180.0 else lon


def _ra_text(hours: float) -> str:
    """`HHh MMm SSs`, the way a TARGET's RA param is typed (M31's is
    "00h 42m 44s"), rounded to the second."""
    total = int(round((hours % 24.0) * 3600.0)) % 86400
    return f"{total // 3600:02d}h {total % 3600 // 60:02d}m {total % 60:02d}s"


def _site_on_server(request, base: str) -> dict:
    """The site the server holds (GET /api/site), or SeedError."""
    got = request.get(f"{base}/api/site")
    body = got.json() if got.ok else None
    site = (body or {}).get("site") if isinstance(body, dict) else None
    if not isinstance(site, dict):
        raise SeedError(f"GET /api/site -> {got.status}, so the server holds no site "
                        f"to read")
    return site


def _save_site(request, base: str, lat: float, lon: float, name: str) -> dict:
    """PUT /api/site with a fixture site and read it back: the server must
    hold exactly these coordinates, as a saved (not default) site."""
    body = {"site": {"name": name, "latitude": lat, "longitude": lon,
                     "elevation_m": 0.0}}
    put = request.put(f"{base}/api/site", data=body)
    if not put.ok:
        raise SeedError(f"PUT /api/site ({name!r}) -> {put.status}: {put.text()[:200]}")
    site = _site_on_server(request, base)
    same = (abs(float(site.get("latitude", 999)) - lat) < 1e-6
            and abs(float(site.get("longitude", 999)) - lon) < 1e-6)
    if not same or site.get("is_default") is not False:
        raise SeedError(f"the server did not keep the fixture site {name!r} as a saved "
                        f"site (is_default={site.get('is_default')!r}, same={same})")
    return site


def _seed_set_site(request, base: str, spec: dict) -> dict:
    """`set_site: {"latitude", "longitude", "name", "night"}`: save a FIXTURE
    site through the API (#189 S7, the straddle: the harness's 40 N 74 W,
    never the observing site) and read it back. With `night`, refuse unless
    the Sun is more than 12 deg below that site's horizon now: the straddle
    walks a live run past the meridian, and in daylight the start's own Sun
    gate would refuse it three doors later, for a reason the walk does not
    name. The log names the fixture and never prints a coordinate: every
    site value that reaches a report is scanned against the observing
    site's, and none needs to be there."""
    lat, lon = float(spec["latitude"]), float(spec["longitude"])
    name = spec.get("name", "probe fixture")
    _save_site(request, base, lat, lon, name)
    out = {"set_site": name}
    if spec.get("night"):
        alt = _sun_altitude(lat, lon, time.time())
        if alt > -12.0:
            raise SeedError(f"the Sun is up at the fixture site {name!r}, or in its "
                            f"twilight: this walk runs past the meridian there, and needs "
                            f"its night (see the route file's `_wall_time`)")
        out["night"] = True
    return out


def _seed_daylight_site(request, base: str, spec: dict) -> dict:
    """`daylight_site: {"latitude", "name"}`: save a FIXTURE site where the Sun
    is on the meridian now, at `latitude`, its longitude the subsolar one
    rounded to a whole degree, and read it back (#189 S7, the CONTINUE walk).

    WHY DAYLIGHT. That walk grades CONTINUE and the replay notice over an
    ARMED dormant session, and auto-resume starts an armed session at its
    next tick whenever its window is open: on a server with no site it is
    open at any hour (schedule.dark_enough fails open), and it started the
    walk's session 34 s after it was armed (measured on a private server,
    2026-09-28). In daylight the window is shut for hours, which is also the
    case the notice exists for: the operator opening a flow in the afternoon
    before its second night. The Sun's altitude there is 90 - |latitude -
    its declination|, never below 26 deg at 40 N. Refuses if it is not well
    up, which would mean this arithmetic is wrong."""
    lat = float(spec.get("latitude", 40.0))
    now = time.time()
    lon = float(round(_subsolar_lon(now)))
    if lon <= -180.0:
        lon += 360.0
    name = spec.get("name", "probe fixture (daylight)")
    _save_site(request, base, lat, lon, name)
    if _sun_altitude(lat, lon, now) < 20.0:
        raise SeedError(f"the Sun is not well up at the daylight fixture {name!r}, so "
                        f"auto-resume could open a window under the walk")
    return {"daylight_site": name, "sun_up": True}


def _graph_with_meridian_ra(request, base: str, graph: dict, spec: dict) -> dict:
    """`graph` with the TARGET `spec["node"]`'s RA put `spec["minutes_east"]`
    minutes of sidereal time east of the meridian now, at the server's saved
    site (see `_seed_save_flow`)."""
    site = _site_on_server(request, base)
    if site.get("is_default") is not False:
        raise SeedError("the straddle's RA is computed at the server's saved site, and "
                        "it holds none: put a set_site op first")
    lon = float(site["longitude"])
    ra = (_lst_hours(lon, time.time()) + float(spec["minutes_east"]) / 60.0) % 24.0
    out = json.loads(json.dumps(graph))
    node = next((n for n in out.get("nodes", []) if n.get("id") == spec["node"]), None)
    if node is None or node.get("type") != "target":
        raise SeedError(f"the flow has no TARGET {spec['node']!r} to put on the meridian")
    node.setdefault("params", {})["ra"] = _ra_text(ra)
    return out


def _progress_of(request, base: str, flow: str) -> dict:
    got = request.get(f"{base}/api/flows/{quote(flow)}/progress")
    body = got.json() if got.ok else None
    if not isinstance(body, dict):
        raise SeedError(f"GET /api/flows/{flow}/progress -> {got.status}")
    return body


def _engine_state(request, base: str) -> str | None:
    got = request.get(f"{base}/api/sequence/state")
    return (got.json() or {}).get("state") if got.ok else None


def _seed_run_flow(request, base: str, spec: dict) -> dict:
    """`run_flow: {"flow", "min_banked", "timeout_ms"}`: run a saved flow on
    this server for real (POST /api/flows/{id}/run), wait for the progress
    route to count `min_banked` subs, abort it (POST /api/sequence/abort),
    and wait for the session to be dormant and the engine done (#189 S7, the
    CONTINUE walk's first night). The frames are the simulator's, banked by
    the engine, so the counts CONTINUE carries are a ledger's and not a
    number this wrote. Refuses a busy engine before it presses anything."""
    flow = spec["flow"]
    if _engine_state(request, base) in LIVE_STATES:
        raise SeedError(f"the engine is busy, so {flow!r} cannot be run for its first night")
    run = request.post(f"{base}/api/flows/{quote(flow)}/run", data={})
    if not run.ok:
        raise SeedError(f"POST /api/flows/{flow}/run -> {run.status}: {run.text()[:240]}")
    need = int(spec.get("min_banked", 1))
    deadline = time.monotonic() + spec.get("timeout_ms", 240000) / 1000.0
    banked = 0
    while time.monotonic() < deadline:
        blocks = _progress_of(request, base, flow).get("blocks") or []
        banked = sum(int(b.get("banked") or 0) for b in blocks)
        if banked >= need:
            break
        time.sleep(1.0)
    stop = request.post(f"{base}/api/sequence/abort", data={})
    if banked < need:
        raise SeedError(f"{flow!r} banked {banked} subs in {spec.get('timeout_ms', 240000)} "
                        f"ms, and its first night needs {need}")
    if not stop.ok:
        raise SeedError(f"POST /api/sequence/abort -> {stop.status}")
    deadline = time.monotonic() + 60.0
    session: dict = {}
    while time.monotonic() < deadline:
        session = _progress_of(request, base, flow).get("session") or {}
        if session.get("status") == "dormant" and _engine_state(request, base) not in LIVE_STATES:
            break
        time.sleep(0.5)
    else:
        raise SeedError(f"{flow!r}'s session is {session.get('status')!r} a minute after "
                        f"the abort, not dormant")
    return {"run_flow": flow, "banked": banked, "session": session.get("id")}


#: The server's own python, which seed_session.py needs (it imports astrodeck).
DEFAULT_VENV_PYTHON = REPO_ROOT / "server" / ".venv" / "Scripts" / "python.exe"


def _seed_session(request, base: str, spec: dict, ctx: dict) -> dict:
    """`seed_session: {"flow", "nights_ago", "arm"}`: move the flow's dormant
    session `nights_ago` nights back, and arm it with `arm`, through
    seed_session.py run by the server's venv against the private server's
    own directories (probe.py's `--config-dir`, `--capture-dir` and
    `--venv-python`, which this refuses to guess), then read the result back
    through the progress route: the same session, dormant, as armed as
    asked, and every run on a night before tonight (#189 S7: "seed a dormant
    session whose runs are stamped on an earlier observing night, written in
    the store's own shape through the server venv")."""
    missing = [k for k in ("config_dir", "capture_dir") if not ctx.get(k)]
    if missing:
        raise SeedError(f"seed_session writes the private server's session store, and "
                        f"probe.py was not told where it is: pass "
                        f"{', '.join('--' + k.replace('_', '-') for k in missing)}")
    venv = Path(ctx.get("venv_python") or DEFAULT_VENV_PYTHON)
    cmd = [str(venv), str(Path(__file__).resolve().parent / "seed_session.py"),
           "--config-dir", str(ctx["config_dir"]), "--capture-dir", str(ctx["capture_dir"]),
           "--flow", spec["flow"], "--nights-ago", str(int(spec.get("nights_ago", 1)))]
    if spec.get("arm"):
        cmd.append("--arm")
    try:
        done = subprocess.run(cmd, capture_output=True, text=True, timeout=180,
                              cwd=str(REPO_ROOT))
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SeedError(f"seed_session.py did not run ({exc.__class__.__name__}: {exc})")
    if done.returncode != 0:
        raise SeedError(f"seed_session.py exited {done.returncode}: "
                        f"{(done.stderr or done.stdout).strip()[-400:]}")
    try:
        out = json.loads(done.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        raise SeedError(f"seed_session.py printed no JSON: {done.stdout[-200:]!r}")
    session = _progress_of(request, base, spec["flow"]).get("session") or {}
    problems = []
    if session.get("id") != out.get("session"):
        problems.append(f"the progress route names session {session.get('id')!r}, not "
                        f"the one moved ({out.get('session')!r})")
    if session.get("status") != "dormant":
        problems.append(f"the session is {session.get('status')!r}, not dormant")
    if bool(session.get("armed")) != bool(spec.get("arm")):
        problems.append(f"the progress route says armed={session.get('armed')!r}")
    if out.get("tonight") in (out.get("observing_nights") or []):
        problems.append("a run is still on tonight's observing night")
    if problems:
        raise SeedError("seed_session: " + "; ".join(problems))
    return {"seed_session": spec["flow"], "session": out.get("session"),
            "runs": out.get("runs_after"), "nights": out.get("observing_nights"),
            "frames": out.get("frames"), "armed": out.get("armed")}


def _seed_for_width(ops: list[dict], width: int,
                    names: set[str] | None = None) -> list[dict]:
    """The seed ops that run at `width`: an op with `widths` runs only at
    those, as a route does (`_routes_for_width`). A flow a phone walk starts
    must be seeded once, before the phone walks: seeded again before the
    desktop walks, `fresh` would find the phone's own session and refuse.

    An op with `routes` runs only when one of those routes is walked at this
    width (`names`, the width's routes after `--only`), so one route file can
    hold four scenarios, each walked against its own private server with
    `--only`, and seed only its own (#189 S7). With `names` None every op
    runs, as before."""
    out = [op for op in ops if not op.get("widths") or width in op["widths"]]
    if names is not None:
        out = [op for op in out if not op.get("routes") or set(op["routes"]) & names]
    return out


def _resolve_routes_path(raw: str) -> Path:
    p = Path(raw)
    if p.is_file():
        return p
    alt = Path(__file__).resolve().parent / raw
    if alt.is_file():
        return alt
    raise FileNotFoundError(f"routes file not found: {raw!r} "
                            f"(tried {p} and {alt})")


def _console_safe(*streams) -> None:
    """Let the console print any character a page puts in a reason.

    A reason quotes the page's own text, and the page's text is not ASCII
    (the warning sign, U+26A0, before "Survey unreachable"; the middle dot in
    a STAGE readout, "M31 1-2 . pass 3"). On a Windows console the
    streams encode as cp1252 with `errors="strict"`, so the first such reason
    raised UnicodeEncodeError out of the result loop (measured 2026-09-28, the
    first walk of routes_s5_s6.json): the probe died after two routes,
    wrote no report.jsonl, and left the run it had started going on the
    simulator. A character the console cannot show is now written as its
    escape; the report file is UTF-8 and keeps it whole."""
    for stream in streams or (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="backslashreplace")
        except (AttributeError, ValueError):
            pass  # not a TextIOWrapper (a test's StringIO): it can hold anything


def main(argv: list[str] | None = None) -> int:
    _console_safe()
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
    ap.add_argument("--config-dir", default=None,
                    help="the private server's config dir (server_ctl.py start "
                        "--config-dir): read only by the seed_session seed op")
    ap.add_argument("--capture-dir", default=None,
                    help="the private server's capture dir (server_ctl.py start "
                        "--capture-dir): read only by the seed_session seed op")
    ap.add_argument("--venv-python", default=str(DEFAULT_VENV_PYTHON),
                    help="the server's venv python, which seed_session.py runs under")
    args = ap.parse_args(argv)

    base = args.base or f"http://127.0.0.1:{args.port}"
    # Before anything is read, launched or seeded: the rig's port is never a
    # probe server's (RIG_PORT).
    refused = _refuse_rig_port(base)
    if refused:
        print(f"ERROR: {refused}", file=sys.stderr)
        return 2
    _MEMO.clear()
    seed_ctx = {"config_dir": args.config_dir, "capture_dir": args.capture_dir,
                "venv_python": args.venv_python}
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
        # A browser per kind of screen: a desktop width draws its scrollbars,
        # a phone width does not (HIDE_SCROLLBARS, #535).
        browsers = _Browsers(p, headless=not args.headed)
        try:
            for width in widths:
                context = browsers.context(width)
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
                width_seed = _seed_for_width(seed_ops, width,
                                             {r.get("name") for r in width_routes})
                if width_seed and width_routes:
                    # Every width seeds afresh: the copy is an upsert, so a
                    # walk at 1440 grades the flow as seeded, never as the
                    # walk at 390 left it.
                    try:
                        seeded = _seed(context.request, base, width_seed, seed_ctx)
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
            browsers.close()

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
