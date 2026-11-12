# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""probe_node_sheet_undo.py -- the phone's nested stage-editor sheet has UNDO
and REDO, in a real touch Chromium at 390 x 844 (#1153, WP 11.5).

Drives a private simulator server (server_ctl.py start --port <not 8800>) that
already holds a saved flow. Opens the flow's stage list, taps a CAPTURE LOOP
stage to open the nested `flowNode` sheet, then:

  1. measures the UNDO and REDO controls from the DOM: inside the viewport,
     at least 40 px tall and wide;
  2. types a new exposure and commits it (blur), reads the field;
  3. taps UNDO and reads the field (the original);
  4. taps REDO and reads the field (the typed value);
  5. saves a screenshot of the open sheet.

Prints one line per check and exits 1 on any failure. Usage:

    python probe_node_sheet_undo.py --base http://127.0.0.1:5611 \
        --flow probe-115 --shot <path.png>

Refuses the rig's port, like the rest of this harness.
"""
from __future__ import annotations

import argparse
import sys
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright

RIG_PORT = 8800
MIN_TOUCH_PX = 40
TYPED = "123"

failures: list[str] = []


def check(ok: bool, what: str) -> None:
    print(f"{'PASS' if ok else 'FAIL'}  {what}", flush=True)
    if not ok:
        failures.append(what)


def box_of(page, testid: str) -> dict:
    return page.evaluate(
        """(t) => {
            const el = document.querySelector(`[data-testid="${t}"]`);
            if (!el) return null;
            const r = el.getBoundingClientRect();
            return {x: r.x, y: r.y, w: r.width, h: r.height,
                    vw: window.innerWidth, vh: window.innerHeight};
        }""",
        testid,
    )


def field_value(page) -> str:
    return page.locator('[data-testid="flow-field-exposure"] input').first.input_value()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True)
    ap.add_argument("--flow", required=True)
    ap.add_argument("--shot", required=True)
    args = ap.parse_args()
    if urlparse(args.base).port == RIG_PORT:
        print(f"refusing port {RIG_PORT}: that is the rig's server", file=sys.stderr)
        return 2

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        ctx = browser.new_context(
            viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True)
        page = ctx.new_page()
        page.goto(f"{args.base}/#/session/flows")
        page.locator(f'[data-testid="flow-open-{args.flow}"]').tap(timeout=20000)
        rows =page.locator('[data-testid="flow-stage-row"]')
        rows.first.wait_for(timeout=20000)
        check(rows.count() >= 2, f"the phone stage list shows the flow's stages ({rows.count()})")

        rows.filter(has_text="CAPTURE").first.locator("button, [role=button]").first.tap()
        page.locator('[data-testid="session-flow-node"]').wait_for(timeout=10000)
        page.locator('[data-testid="flow-field-exposure"] input').wait_for(timeout=10000)

        for tid in ("flow-node-undo", "flow-node-redo"):
            b = box_of(page, tid)
            check(b is not None, f"{tid} is in the open sheet")
            if b is None:
                continue
            print(f"      {tid}: x={b['x']:.1f} y={b['y']:.1f} w={b['w']:.1f} h={b['h']:.1f}"
                  f" viewport={b['vw']}x{b['vh']}", flush=True)
            check(b["w"] >= MIN_TOUCH_PX and b["h"] >= MIN_TOUCH_PX,
                  f"{tid} is at least {MIN_TOUCH_PX} px each way")
            check(b["x"] >= 0 and b["y"] >= 0
                  and b["x"] + b["w"] <= b["vw"] and b["y"] + b["h"] <= b["vh"],
                  f"{tid} is inside the viewport")

        original = field_value(page)
        field = page.locator('[data-testid="flow-field-exposure"] input').first
        field.fill(TYPED)
        field.blur()
        page.wait_for_timeout(300)
        check(field_value(page) == TYPED, f"the typed exposure shows ({field_value(page)})")

        page.locator('[data-testid="flow-node-undo"]').tap()
        page.wait_for_timeout(300)
        check(field_value(page) == original,
              f"UNDO restores the exposure ({field_value(page)} vs {original})")

        page.locator('[data-testid="flow-node-redo"]').tap()
        page.wait_for_timeout(300)
        check(field_value(page) == TYPED, f"REDO puts the edit back ({field_value(page)})")

        page.screenshot(path=args.shot)
        print(f"screenshot {args.shot}", flush=True)
        browser.close()
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
