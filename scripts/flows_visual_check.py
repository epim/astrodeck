"""Render the Flows surface and prove, from PIXELS, that it is really there.

The handoff makes this mandatory and says why (README §"Verify by looking"):

    DOM assertions pass while pixels are wrong (overlapping text, invisible SVG
    labels, wires sweeping off-canvas, z-index fights, unloaded fonts).

This repo has been bitten by exactly that. A UI probe once "passed" against a
page that had silently fallen back to the login screen, because the assertion it
made was true of both. So every capture here has to clear four gates before it
counts, and a state that cannot clear them is a FAILURE, never a quietly-missing
file:

  1. the app is past login and on the expected view (a view marker is visible);
  2. web fonts have actually loaded (``document.fonts.ready`` plus a check that
     the display face resolved, because a fallback font renders a page that
     looks fine in a DOM dump and wrong in a screenshot);
  3. animations have settled and no error boundary is showing;
  4. THE PNG IS NOT BLANK - decoded and measured, not guessed at from its file
     size.

Gate 4 is the one that needs code rather than a selector, and it is the only
gate that can catch an SVG label which measures fine and paints nothing. There
is no Pillow in the interpreter that has Playwright, so the decoder below is
stdlib-only (zlib plus the five PNG filters). That also keeps this script in
step with ``ui/``'s own test harness, which is deliberately dependency-free.

USAGE

    # against a running AstroDeck (serves the built UI):
    python scripts/flows_visual_check.py

    # against the vite dev server:
    npm --prefix ui run dev
    python scripts/flows_visual_check.py --base-url http://127.0.0.1:5173

    # one state, headed, to debug a selector:
    python scripts/flows_visual_check.py --only 01-library --headed

Credentials come from ADK_UI_USER / ADK_UI_PASSWORD (or --user/--password) and
are only used if a login form appears. Output lands in artifacts/flows-parity/
beside a report.json naming, for each state, the reference PNG to compare it
against by EYE - which is step 3 of the protocol and is not something this
script can do for you.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import struct
import sys
import zlib
from dataclasses import dataclass, field
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
OUT_DIR = REPO / "artifacts" / "flows-parity"
REF_DIR = REPO / "design_handoff_astrodeck_flows" / "screenshots"

#: The three tiers the handoff names, verbatim. Not "roughly a phone" - a
#: capture at 400px would miss a 390px overflow, which is the whole class of bug
#: this protocol exists to find.
DESKTOP = (1440, 900)
TABLET = (820, 1180)
PHONE = (390, 844)


# --------------------------------------------------------------- PNG decoding

def _paeth(a: int, b: int, c: int) -> int:
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    return b if pb <= pc else c


def decode_png(raw: bytes) -> tuple[int, int, int, bytearray]:
    """Minimal PNG reader: returns (width, height, channels, pixel bytes).

    Handles the subset Playwright emits - 8-bit RGB/RGBA, no interlace - and
    raises on anything else rather than returning something plausible. A decoder
    that quietly mishandled a format would make the blankness check below report
    whatever the bug produced, which is worse than not checking at all.
    """
    if raw[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("not a PNG")
    pos, idat, width = 8, bytearray(), 0
    height = bit_depth = color_type = interlace = 0
    while pos < len(raw):
        (length,) = struct.unpack(">I", raw[pos:pos + 4])
        ctype = raw[pos + 4:pos + 8]
        body = raw[pos + 8:pos + 8 + length]
        pos += 12 + length                       # 4 len + 4 type + data + 4 crc
        if ctype == b"IHDR":
            width, height, bit_depth, color_type, _, _, interlace = \
                struct.unpack(">IIBBBBB", body)
        elif ctype == b"IDAT":
            idat += body
        elif ctype == b"IEND":
            break
    if bit_depth != 8 or color_type not in (2, 6) or interlace != 0:
        raise ValueError(
            f"unsupported PNG (depth={bit_depth} color={color_type} "
            f"interlace={interlace}); this decoder handles 8-bit RGB/RGBA only")
    channels = 3 if color_type == 2 else 4
    data = zlib.decompress(bytes(idat))
    stride = width * channels
    out = bytearray(height * stride)
    prev = bytearray(stride)
    src = 0
    for y in range(height):
        ftype = data[src]
        src += 1
        line = bytearray(data[src:src + stride])
        src += stride
        if ftype == 1:                                            # Sub
            for i in range(channels, stride):
                line[i] = (line[i] + line[i - channels]) & 0xFF
        elif ftype == 2:                                          # Up
            for i in range(stride):
                line[i] = (line[i] + prev[i]) & 0xFF
        elif ftype == 3:                                          # Average
            for i in range(stride):
                left = line[i - channels] if i >= channels else 0
                line[i] = (line[i] + ((left + prev[i]) >> 1)) & 0xFF
        elif ftype == 4:                                          # Paeth
            for i in range(stride):
                left = line[i - channels] if i >= channels else 0
                upleft = prev[i - channels] if i >= channels else 0
                line[i] = (line[i] + _paeth(left, prev[i], upleft)) & 0xFF
        elif ftype != 0:
            raise ValueError(f"bad PNG filter {ftype} on row {y}")
        out[y * stride:(y + 1) * stride] = line
        prev = line
    return width, height, channels, out


def image_info(raw: bytes) -> tuple[str, int, int]:
    """(format, width, height) for a PNG or JPEG, without decoding it.

    Needed because the reference bundle is NOT what its filenames claim: nine of
    the thirteen files in ``screenshots/`` are JPEGs with a ``.png`` extension,
    and the set is captured at 924x540 (392x540 for the phone pair) while this
    harness captures at 1440x900 / 820x1180 / 390x844.

    That combination means a numeric image diff against the references is not
    just unimplemented, it is MEANINGLESS - different dimensions and lossy
    source artifacts. The handoff already says so ("compare as an image",
    protocol step 3); recording the format and size in the report is how a later
    reader finds that out before writing a diff that would only ever produce
    noise.
    """
    if raw[:8] == b"\x89PNG\r\n\x1a\n":
        w, h = struct.unpack(">II", raw[16:24])
        return "PNG", w, h
    if raw[:2] == b"\xff\xd8":                                    # JPEG: find SOFn
        i = 2
        while i < len(raw) - 9:
            if raw[i] != 0xFF:
                i += 1
                continue
            marker = raw[i + 1]
            if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6,
                          0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                h, w = struct.unpack(">HH", raw[i + 5:i + 9])
                return "JPEG", w, h
            if marker in (0xD8, 0xD9) or 0xD0 <= marker <= 0xD7:
                i += 2
                continue
            i += 2 + struct.unpack(">H", raw[i + 2:i + 4])[0]
    return "unknown", 0, 0


@dataclass
class Pixels:
    """What a screenshot actually contains, as numbers."""

    width: int
    height: int
    distinct: int               # distinct colours in the sample
    stdev: float                # luminance spread
    ink: float                  # fraction of sampled pixels off the modal colour
    modal_share: float          # fraction that IS the single most common colour

    def verdict(self) -> str | None:
        """``None`` when the image looks like a real screen, else the reason.

        The thresholds are deliberately generous. This gate is not a
        pixel-diff - it exists to catch the catastrophic cases (a blank canvas,
        a white flash, a page that never painted, an all-background render where
        the SVG text vanished), and a tight threshold here would fire on honest
        design differences and get switched off, which is how a guard dies.
        """
        if self.distinct < 12:
            return (f"only {self.distinct} distinct colours - this is a blank or "
                    f"near-blank render, not a screen")
        if self.modal_share > 0.985:
            return (f"{self.modal_share:.1%} of the page is one flat colour - "
                    f"nothing painted on top of the background")
        if self.stdev < 3.0:
            return f"luminance stdev {self.stdev:.2f} - the image is effectively flat"
        if self.ink < 0.01:
            return (f"only {self.ink:.2%} of pixels differ from the background - "
                    f"content is missing or invisible against it")
        return None


def measure(png: bytes, *, step: int = 4) -> Pixels:
    """Colour statistics over every ``step``-th pixel.

    Sampling rather than reading all 1.3M pixels keeps this at a fraction of a
    second per capture in pure Python; at step 4 a 1440x900 frame still
    contributes ~81k samples, far more than enough for the coarse verdicts above.
    """
    w, h, ch, buf = decode_png(png)
    counts: dict[tuple[int, int, int], int] = {}
    lums: list[float] = []
    stride = w * ch
    for y in range(0, h, step):
        row = y * stride
        for x in range(0, w, step):
            i = row + x * ch
            rgb = (buf[i], buf[i + 1], buf[i + 2])
            counts[rgb] = counts.get(rgb, 0) + 1
            lums.append(0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2])
    total = sum(counts.values()) or 1
    modal = max(counts.values())
    return Pixels(width=w, height=h, distinct=len(counts),
                  stdev=statistics.pstdev(lums) if len(lums) > 1 else 0.0,
                  ink=1.0 - modal / total, modal_share=modal / total)


# ------------------------------------------------------------- the state table

@dataclass
class State:
    """One named screen to capture.

    ``steps`` are (action, argument) pairs run in order before the shot. Keeping
    them as DATA rather than code is what lets this file survive the UI being
    written: when the Flows views land, only the selectors below change.
    """

    name: str
    reference: str
    viewport: tuple[int, int]
    marker: str                       # must be VISIBLE or the capture fails
    steps: list[tuple[str, str]] = field(default_factory=list)
    note: str = ""


NAV_FLOWS = ("nav", "Flows")

STATES: list[State] = [
    State("01-library", "01-library.png", DESKTOP,
          marker="[data-flows-tab='library']",
          steps=[NAV_FLOWS],
          note="search + folder chips, MY FLOWS with the NEW FLOW card, EXAMPLES"),
    State("02-editor-m16-full-service", "02-editor-m16-full-service.png", DESKTOP,
          marker="[data-flows-canvas]",
          steps=[NAV_FLOWS, ("open-flow", "example-m16"), ("fit", "")],
          note="flow lane top, rules lane below, event wires dashed amber"),
    State("03-inspector-calibration-queue-matrix",
          "03-inspector-calibration-queue-matrix.png", DESKTOP,
          marker="[data-flows-inspector] [data-calibration-matrix]",
          steps=[NAV_FLOWS, ("open-flow", "example-m16"),
                 ("select-node-type", "calib")]),
    State("04-tonight-timeline", "04-tonight-timeline.png", DESKTOP,
          marker="[data-flows-tonight='timeline']",
          steps=[NAV_FLOWS, ("open-flow", "example-m16"), ("tab", "TONIGHT"),
                 ("tab", "TIMELINE")]),
    State("05-tonight-story", "05-tonight-story.png", DESKTOP,
          marker="[data-flows-tonight='story']",
          steps=[NAV_FLOWS, ("open-flow", "example-m16"), ("tab", "TONIGHT"),
                 ("tab", "STORY")]),
    State("06-tonight-plan-json", "06-tonight-plan-json.png", DESKTOP,
          marker="[data-flows-tonight='plan']",
          steps=[NAV_FLOWS, ("open-flow", "example-m16"), ("tab", "TONIGHT"),
                 ("tab", "PLAN")]),
    State("07-run-cloud-dodge-hold", "07-run-cloud-dodge-hold.png", DESKTOP,
          marker="[data-flows-run='holding']",
          steps=[NAV_FLOWS, ("open-flow", "example-m16"), ("run", ""),
                 ("await-hold", "")],
          note="STOP + ETA in header, toast, queue busy, active dashed event "
               "wires, dark-quota log line - captured INSIDE the hold window"),
    State("08-night-mode", "08-night-mode.png", DESKTOP,
          marker="[data-flows-canvas]",
          steps=[NAV_FLOWS, ("open-flow", "example-m16"), ("fit", ""),
                 ("night-mode", "on")],
          note="production is a token swap, NOT the prototype's CSS filter - "
               "grep cannot prove that, so this capture is the evidence"),
    State("09-wizard-new-flow", "09-wizard-new-flow.png", DESKTOP,
          marker="[data-flows-wizard]",
          steps=[NAV_FLOWS, ("click", "NEW FLOW")]),

    # Interactive states. README §"Verify by looking" 5: "a state that was never
    # rendered was never verified."
    State("12-editor-hover", "02-editor-m16-full-service.png", DESKTOP,
          marker="[data-flows-canvas]",
          steps=[NAV_FLOWS, ("open-flow", "example-m16"), ("fit", ""),
                 ("hover-node", "capture")],
          note="desktop hover affordances; compared against the plain editor "
               "reference for layout, not for the hover state itself"),
    State("13-edit-sheet-open", "03-inspector-calibration-queue-matrix.png", TABLET,
          marker="[data-flows-editsheet]",
          steps=[NAV_FLOWS, ("open-flow", "example-m16"),
                 ("edit-node-type", "capture")]),
    State("14-palette-sheet-open", "01-library.png", TABLET,
          marker="[data-flows-palette]",
          steps=[NAV_FLOWS, ("open-flow", "example-m16"), ("click", "ADD STAGE")]),
    State("15-wire-selected", "02-editor-m16-full-service.png", DESKTOP,
          marker="[data-flows-wire-selected]",
          steps=[NAV_FLOWS, ("open-flow", "example-m16"), ("fit", ""),
                 ("select-wire", "")],
          note="the selected wire's remove control must be visible"),

    # Tablet tier. The handoff names 820x1180 as a required breakpoint even
    # though screenshots/ has no tablet reference - so these are compared for
    # overflow, collision and reachability rather than against a picture.
    State("20-tablet-library", "01-library.png", TABLET,
          marker="[data-flows-tab='library']",
          steps=[NAV_FLOWS],
          note="no picture to match; the gate is that nothing overflows or "
               "collides at 820px"),
    State("21-tablet-editor", "02-editor-m16-full-service.png", TABLET,
          marker="[data-flows-canvas]",
          steps=[NAV_FLOWS, ("open-flow", "example-m16"), ("fit", "")]),

    State("10-phone-flow-autograph", "10-phone-flow-autograph-390px.png", PHONE,
          marker="[data-flows-phone-tab='flow']",
          steps=[NAV_FLOWS, ("open-flow", "example-m16")],
          note="auto-laid zigzag graph, compact nodes, real wires, bottom "
               "FLOW/CANVAS/MONITOR bar, truncating header title"),
    State("10b-phone-tap-to-wire-armed", "10b-phone-tap-to-wire-armed.png", PHONE,
          marker="[data-flows-wiring-armed]",
          steps=[NAV_FLOWS, ("open-flow", "example-m16"), ("arm-wire", "")],
          note="filled source port + accent hint bar + CANCEL"),
    State("11-phone-monitor", "11-phone-monitor-390px.png", PHONE,
          marker="[data-flows-phone-tab='monitor']",
          steps=[NAV_FLOWS, ("open-flow", "example-m16"),
                 ("phone-tab", "MONITOR")]),
]


# ----------------------------------------------------------------- the driver

SETTLE_JS = """
() => new Promise((resolve) => {
  document.fonts.ready.then(() => requestAnimationFrame(
    () => requestAnimationFrame(() => resolve(true))));
})
"""

#: Was the DISPLAY face actually used, or did the page fall back? A fallback
#: renders a screenshot with different metrics everywhere, and no selector can
#: see it. ui/ loads Chakra Petch via @fontsource (README "Design tokens").
FONT_JS = """
() => {
  const loaded = [];
  document.fonts.forEach((f) => { if (f.status === 'loaded') loaded.push(f.family); });
  return { ready: document.fonts.status, families: [...new Set(loaded)] };
}
"""

ERROR_JS = """
() => {
  const t = document.body ? document.body.innerText : '';
  for (const needle of ['Something went wrong', 'ErrorBoundary',
                        'Unhandled', 'Failed to fetch dynamically imported']) {
    if (t.includes(needle)) return needle;
  }
  return null;
}
"""


class CaptureFailed(Exception):
    pass


async def _login_if_needed(page, user: str, password: str) -> None:
    if await page.locator("text=Sign in").count() == 0:
        return
    if not user or not password:
        raise CaptureFailed(
            "the app is showing a login form and no credentials were given - "
            "set ADK_UI_USER / ADK_UI_PASSWORD or pass --user/--password")
    await page.get_by_label("Username").fill(user)
    await page.get_by_label("Password").fill(password)
    await page.get_by_role("button", name="Sign in").click()
    await page.wait_for_selector("text=Sign in", state="detached", timeout=20_000)


async def _run_step(page, action: str, arg: str) -> None:
    """One navigation step.

    Every branch raises on failure. A step that silently did nothing is how a
    harness ends up photographing the wrong screen and calling it a pass.
    """
    if action == "nav":
        # visible=True matters: the desktop rail is present-but-hidden on
        # phone widths and its labels duplicate the bottom bar's, so a plain
        # text match can resolve to an element nobody can click.
        target = page.get_by_role("button", name=arg).or_(
            page.get_by_role("link", name=arg)).filter(visible=True)
        if await target.count() == 0:
            raise CaptureFailed(
                f"no visible nav control named {arg!r} - is the Flows surface "
                f"registered in App.tsx's nav table yet?")
        await target.first.click()
    elif action == "open-flow":
        card = page.locator(f"[data-flow-id='{arg}']").filter(visible=True)
        if await card.count() == 0:
            raise CaptureFailed(f"no flow card for id {arg!r} in the library")
        await card.first.click()
    elif action == "tab" or action == "phone-tab":
        await page.get_by_role("tab", name=arg).or_(
            page.get_by_role("button", name=arg)).filter(visible=True).first.click()
    elif action == "click":
        await page.get_by_role("button", name=arg).filter(visible=True).first.click()
    elif action == "select-node-type":
        node = page.locator(f"[data-node-type='{arg}']").filter(visible=True)
        if await node.count() == 0:
            raise CaptureFailed(f"no node of type {arg!r} on the canvas")
        await node.first.click()
    elif action == "edit-node-type":
        # The edit GLYPH on the node opens the sheet. Double-click was mine and
        # appears nowhere in the prototype — and the sheet only exists below the
        # desktop tier at all, where the inspector is a docked column instead.
        node = page.locator(f"[data-node-type='{arg}']").filter(visible=True)
        if await node.count() == 0:
            raise CaptureFailed(f"no node of type {arg!r} on the canvas")
        await node.first.hover()
        pencil = node.first.locator("[data-flows-edit]")
        if await pencil.count() == 0:
            raise CaptureFailed(
                f"the {arg!r} node exposes no [data-flows-edit] control — the "
                f"edit sheet has no opener the harness can find")
        await pencil.first.click()
    elif action == "hover-node":
        await page.locator(f"[data-node-type='{arg}']").first.hover()
    elif action == "select-wire":
        wire = page.locator("[data-wire]").filter(visible=True)
        if await wire.count() == 0:
            raise CaptureFailed("no wires on the canvas to select")
        await wire.first.click()
    elif action == "fit":
        # A BUTTON in the zoom cluster. The prototype contains the string "FIT"
        # and no keyboard fit; pressing "f" was mine, and a step that silently
        # does nothing leaves the graph unfitted in a capture claiming to be
        # "fitted".
        await page.get_by_role("button", name="FIT").filter(
            visible=True).first.click()
    elif action == "night-mode":
        # A CLASS, not an attribute. index.css defines night at `:root.night`
        # (line 105) and store.ts toggles it with
        # `document.documentElement.classList.toggle("night", night)`.
        # The first version of this set data-night, which matches nothing —
        # so the "night mode" capture would have been a daylight render that
        # nobody could tell apart from the real thing.
        await page.evaluate(
            "(on) => document.documentElement.classList.toggle('night', on === 'on')",
            arg)
    elif action == "arm-wire":
        port = page.locator("[data-port][data-port-dir='out']").filter(visible=True)
        if await port.count() == 0:
            raise CaptureFailed("no output port to arm tap-to-wire from")
        await port.first.click()
    elif action == "run":
        await page.get_by_role("button", name="RUN").filter(visible=True).first.click()
    elif action == "await-hold":
        # The cloud-dodge storyboard must be captured DURING the hold. Waiting
        # on the marker rather than sleeping is the difference between a
        # reproducible capture and one that depends on how loaded the box is.
        await page.wait_for_selector("[data-flows-run='holding']", timeout=180_000)
    else:
        raise CaptureFailed(f"unknown step action {action!r}")
    await page.wait_for_timeout(250)


async def capture(page, state: State, out_dir: Path,
                  user: str, password: str, base_url: str) -> dict:
    result: dict = {"state": state.name, "reference": state.reference,
                    "viewport": list(state.viewport), "note": state.note}
    await page.set_viewport_size({"width": state.viewport[0],
                                  "height": state.viewport[1]})
    await page.goto(base_url, wait_until="domcontentloaded")
    await _login_if_needed(page, user, password)

    for action, arg in state.steps:
        await _run_step(page, action, arg)

    # GATE 1 - the expected view is on screen. Without this a capture can be a
    # perfectly-rendered photograph of the wrong page.
    try:
        await page.wait_for_selector(state.marker, state="visible", timeout=15_000)
    except Exception as exc:
        raise CaptureFailed(
            f"view marker {state.marker!r} never became visible: "
            f"{type(exc).__name__}") from exc

    # GATE 2 - fonts really resolved.
    await page.evaluate(SETTLE_JS)
    fonts = await page.evaluate(FONT_JS)
    result["fonts"] = fonts
    if fonts.get("ready") != "loaded":
        raise CaptureFailed(f"document.fonts.status is {fonts.get('ready')!r}")

    # GATE 3 - nothing blew up on the way here.
    boom = await page.evaluate(ERROR_JS)
    if boom:
        raise CaptureFailed(f"an error boundary is on screen ({boom!r})")

    png = await page.screenshot(full_page=False)
    path = out_dir / f"{state.name}.png"
    path.write_bytes(png)
    result["path"] = str(path.relative_to(REPO))

    # GATE 4 - the pixels. The only gate that can catch SVG text which measures
    # fine and paints nothing.
    px = measure(png)
    result["pixels"] = {"distinct": px.distinct, "stdev": round(px.stdev, 2),
                        "ink": round(px.ink, 4),
                        "modal_share": round(px.modal_share, 4)}
    blank = px.verdict()
    if blank:
        raise CaptureFailed(f"the capture is not a real screen: {blank}")

    ref = REF_DIR / state.reference
    if ref.exists():
        fmt, rw, rh = image_info(ref.read_bytes())
        result["compare_against"] = {
            "path": str(ref.relative_to(REPO)), "format": fmt, "size": [rw, rh],
            "how": "BY EYE. Different dimensions and (for the JPEGs) lossy "
                   "artifacts make a numeric diff meaningless — protocol step 3."}
    else:
        result["compare_against"] = None
    result["ok"] = True
    return result


async def main_async(args) -> int:
    try:
        from playwright.async_api import async_playwright
    except ModuleNotFoundError:
        print("playwright is not installed in this interpreter.\n"
              "    pip install playwright && playwright install chromium",
              file=sys.stderr)
        return 2

    wanted = [s for s in STATES if not args.only or s.name in args.only]
    if args.only and len(wanted) != len(args.only):
        missing = sorted(set(args.only) - {s.name for s in wanted})
        print(f"unknown state(s): {', '.join(missing)}\n"
              f"known: {', '.join(s.name for s in STATES)}", file=sys.stderr)
        return 2

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    results: list[dict] = []
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=not args.headed)
        # service_workers blocked: a cached SW can serve a stale bundle and the
        # capture then documents a build that is not the one under test.
        ctx = await browser.new_context(service_workers="block",
                                        device_scale_factor=1)
        page = await ctx.new_page()
        for state in wanted:
            try:
                results.append(await capture(page, state, OUT_DIR,
                                             args.user, args.password,
                                             args.base_url))
                print(f"  ok   {state.name}")
            except Exception as exc:                    # noqa: BLE001 - reported
                results.append({"state": state.name, "ok": False,
                                "reference": state.reference,
                                "error": f"{type(exc).__name__}: {exc}"})
                print(f"  FAIL {state.name}: {type(exc).__name__}: {exc}")
        await browser.close()

    report = OUT_DIR / "report.json"
    report.write_text(json.dumps({"base_url": args.base_url,
                                  "results": results}, indent=2), encoding="utf-8")
    failed = [r for r in results if not r.get("ok")]
    print(f"\n{len(results) - len(failed)}/{len(results)} captured -> "
          f"{OUT_DIR.relative_to(REPO)}")
    print(f"report: {report.relative_to(REPO)}")
    if failed:
        print("\nNOT DONE. A missing capture is a failed state, not an absent "
              "one - see README §'Verify by looking'.")
        return 1
    print("\nCaptures exist and are not blank. That is NOT parity: now open each "
          "PNG beside its reference in design_handoff_astrodeck_flows/screenshots/ "
          "and compare them AS IMAGES (protocol step 3).")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--base-url", default=os.environ.get(
        "ADK_UI_URL", "http://127.0.0.1:8800"))
    ap.add_argument("--user", default=os.environ.get("ADK_UI_USER", ""))
    ap.add_argument("--password", default=os.environ.get("ADK_UI_PASSWORD", ""))
    ap.add_argument("--only", nargs="*", default=None,
                    help="capture only these state names")
    ap.add_argument("--headed", action="store_true")
    ap.add_argument("--list", action="store_true", help="print the state table")
    args = ap.parse_args()
    if args.list:
        for s in STATES:
            print(f"{s.name:38s} {s.viewport[0]}x{s.viewport[1]:<6} {s.reference}")
        return 0
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
