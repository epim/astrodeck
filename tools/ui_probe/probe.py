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

# A marker or testid used to count as "visible" the instant Playwright's own
# is_visible() said so -- true for a 0-opacity element AND for one collapsed
# to a couple of pixels by layout (measured 2026-09-10: every Dial on the
# mount sheet at 820px had a 328 x 2 px box, its own children taller than
# that, because `.nx-dial { overflow: hidden }` zeroed a flex item's
# automatic min-height in `.nx-sheet-body`'s flex column -- see
# ui/src/next/next.css and shellCss.test.ts). Playwright's is_visible() does
# not look at size at all, so this shipped on every route and every width
# without failing the probe. MIN_VISIBLE_PX is the floor below which a
# "visible" element is almost certainly a collapsed control rather than a
# small-but-real one -- 16px is smaller than any real tap target or readout
# in this UI (the smallest deliberate glyph is the 44px touch target's own
# icon), so it flags a genuine collapse without flagging legitimate small
# elements.
MIN_VISIBLE_PX = 16


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
    """A box counts as genuinely visible only if BOTH dimensions clear
    MIN_VISIBLE_PX -- a 328 x 2 px box (the measured dial defect) is wide
    enough to look fine in a width-only check and must fail on height."""
    return box is not None and box["width"] >= min_px and box["height"] >= min_px


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
    """Polls for a marker that is both VISIBLE and at least MIN_VISIBLE_PX
    square. Returns (element, box):
      - (None, None)   -- no visible match ever appeared at all
      - (element, box) -- a visible match appeared; the caller must still
        check `_large_enough(box)`, because a match that is visible but
        never grows past MIN_VISIBLE_PX is returned here too (as the last
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
            if _large_enough(last_box):
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


# --------------------------------------------------------------- clicking

def _run_clicks(page, clicks: list[dict]) -> list[dict]:
    """Best-effort click sequence. A step whose text has NO visible match is
    logged as skipped rather than failing the route outright -- at some
    widths a step is legitimately not applicable (e.g. 'More' only exists on
    the phone bottom nav). The MARKER assertion afterwards is the real gate:
    if a skipped step mattered, the route lands somewhere wrong and the
    marker will not appear."""
    log: list[dict] = []
    for step in clicks:
        text = step.get("text", "")
        require_visible = step.get("visible", True)
        exact = step.get("exact", True)
        matches = _visible_matches(page, text, exact=exact) if require_visible else \
            [page.get_by_text(text, exact=exact).first]
        if not matches:
            log.append({"text": text, "action": "skip",
                       "reason": "no visible match at this width"})
            continue
        try:
            matches[0].click(timeout=5000)
            page.wait_for_timeout(300)
            log.append({"text": text, "action": "click", "matched": len(matches)})
        except Exception as exc:
            log.append({"text": text, "action": "click-failed", "error": str(exc)})
    return log


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

    t0 = time.monotonic()
    reasons: list[str] = []
    click_log: list[dict] = []
    overflow_info: dict[str, Any] = {}
    marker_ok = False
    testid_box: dict[str, float] | None = None
    marker_box: dict[str, float] | None = None

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
            elif not _large_enough(marker_box):
                marker_ok = False
                h = marker_box["height"] if marker_box else "?"
                w = marker_box["width"] if marker_box else "?"
                reasons.append(
                    f"marker {marker!r} is {h}px tall - present but collapsed "
                    f"(box {w} x {h}px, need >= {MIN_VISIBLE_PX} x {MIN_VISIBLE_PX}px; "
                    f"click log: {click_log})")
            else:
                marker_ok = True
        else:
            marker_ok = True  # no text marker required for this route

        if not testid and not marker:
            marker_ok = False
            reasons.append("route defines neither 'testid' nor 'marker' -- "
                           "nothing to assert, so it cannot pass")

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

    width_dir = out_dir / str(width)
    width_dir.mkdir(parents=True, exist_ok=True)
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
        "click_log": click_log, "overflow": overflow_info,
        "console_errors": console_errors, "failed_requests": failed_requests,
        "screenshot": str(shot_path), "duration_s": round(time.monotonic() - t0, 2),
    }


# --------------------------------------------------------------------- main

def _load_routes(path: Path, include_pending: bool) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    routes = data["routes"]
    if not include_pending:
        routes = [r for r in routes if not r.get("_pending")]
    return routes


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
                        for route in routes:
                            all_results.append({
                                "width": width, "route": route.get("name"),
                                "url": route.get("url"), "passed": False,
                                "reasons": [f"login failed: {exc}"],
                            })
                        context.close()
                        continue

                for route in routes:
                    result = _run_route(page, base, route, out_dir, width)
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
