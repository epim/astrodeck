"""UX review harness for AstroDeck.

Two jobs:
  1. Put PIXELS in front of the reviewer. `shot()` writes a PNG; the reviewer
     then Reads that PNG so the model actually SEES the rendered interface.
  2. Measure the things eyes miss or get wrong: WCAG contrast, overlapping
     text, sub-44px touch targets, clipped/truncated text, controls with no
     accessible name, and horizontal overflow.

Automated findings are EVIDENCE, not verdicts. A contrast ratio is a fact; the
judgement about whether a screen is usable at 2am is the reviewer's.

Usage (async):
    import asyncio, uxkit
    async def main():
        async with uxkit.session(port=8801, persona="novice") as ux:
            await ux.goto("Capture")
            await ux.shot("capture-desktop")
            print(ux.audit_summary(await ux.audit()))
    asyncio.run(main())
"""
from __future__ import annotations

import contextlib
import json
import os
import pathlib
from playwright.async_api import async_playwright

# Viewports that matter for this product. The TABLET is the primary field
# device — the user is standing in a dark field holding it — so it is not an
# afterthought here.
VIEWPORTS = {
    # Real flagship CSS viewports, not round numbers. Width is what breaks
    # layouts, and these three phones span 390-440 -- a 50px spread that can
    # straddle a breakpoint, which is exactly why "tested at 390" is not the
    # same as "tested on phones".
    "phone": (390, 844),            # iPhone 14/15/16 base — the narrow floor
    "s25ultra": (412, 915),         # Galaxy S25 Ultra
    "iphone-pro-max": (440, 956),   # iPhone 16 Pro Max — the wide end
    "tablet": (820, 1180),
    "desktop": (1440, 900),
}

VIEWS = ["Equipment", "Align", "Mount", "Focus", "Capture", "Guide",
         "Atlas", "Plan", "Power", "Monitor", "Tonight", "Settings"]

_AUDIT_JS = r"""
() => {
  const px = v => parseFloat(v) || 0;
  const vis = e => {
    const r = e.getBoundingClientRect();
    const cs = getComputedStyle(e);
    return r.width > 0 && r.height > 0 && cs.visibility !== 'hidden' &&
           cs.display !== 'none' && px(cs.opacity) > 0.05;
  };
  const desc = e => {
    const cls = (e.className || '').toString().slice(0, 60);
    const txt = (e.innerText || e.textContent || '').trim().replace(/\s+/g,' ').slice(0, 40);
    return `<${e.tagName.toLowerCase()}${cls ? ' class="'+cls+'"' : ''}> ${txt}`;
  };
  const rectOf = e => { const r = e.getBoundingClientRect();
    return {x:Math.round(r.x), y:Math.round(r.y), w:Math.round(r.width), h:Math.round(r.height)}; };

  // ---- colour helpers (WCAG 2.1 relative luminance) ----------------------
  const parse = c => {
    const m = (c||'').match(/rgba?\(([^)]+)\)/); if (!m) return null;
    const p = m[1].split(',').map(s => parseFloat(s));
    return {r:p[0], g:p[1], b:p[2], a:p.length>3 ? p[3] : 1};
  };
  const lum = c => {
    const f = v => { v/=255; return v<=0.03928 ? v/12.92 : Math.pow((v+0.055)/1.055, 2.4); };
    return 0.2126*f(c.r) + 0.7152*f(c.g) + 0.0722*f(c.b);
  };
  const over = (fg, bg) => ({           // composite fg (with alpha) onto bg
    r: fg.r*fg.a + bg.r*(1-fg.a),
    g: fg.g*fg.a + bg.g*(1-fg.a),
    b: fg.b*fg.a + bg.b*(1-fg.a), a: 1});
  // walk ancestors for the first opaque-enough background
  const bgOf = e => {
    let n = e, acc = null;
    while (n && n !== document.documentElement) {
      const c = parse(getComputedStyle(n).backgroundColor);
      if (c && c.a > 0.001) { acc = acc ? over(acc, c) : c; if (acc.a > 0.99) return acc; }
      n = n.parentElement;
    }
    const root = parse(getComputedStyle(document.body).backgroundColor) || {r:0,g:0,b:0,a:1};
    return acc ? over(acc, root) : root;
  };
  const ratio = (a, b) => { const l1=lum(a), l2=lum(b);
    return (Math.max(l1,l2)+0.05)/(Math.min(l1,l2)+0.05); };

  const out = {contrast:[], tiny:[], touch:[], overlap:[], truncated:[],
               unnamed:[], overflow:[], counts:{}};

  // elements that directly own visible text
  const textEls = [...document.body.querySelectorAll('*')].filter(e => {
    if (!vis(e)) return false;
    return [...e.childNodes].some(n => n.nodeType === 3 && n.textContent.trim().length > 1);
  });

  for (const e of textEls) {
    const cs = getComputedStyle(e);
    const size = px(cs.fontSize);
    const weight = parseInt(cs.fontWeight) || 400;
    const fg0 = parse(cs.color); if (!fg0) continue;
    const bg = bgOf(e);
    const fg = fg0.a < 1 ? over(fg0, bg) : fg0;
    const r = ratio(fg, bg);
    // WCAG "large text" = >=24px, or >=18.66px bold
    const large = size >= 24 || (size >= 18.66 && weight >= 700);
    const need = large ? 3.0 : 4.5;
    if (r < need) out.contrast.push({el:desc(e), rect:rectOf(e), ratio:+r.toFixed(2),
      need, size:+size.toFixed(1), color:cs.color, bg:`rgb(${Math.round(bg.r)},${Math.round(bg.g)},${Math.round(bg.b)})`});
    if (size < 12) out.tiny.push({el:desc(e), rect:rectOf(e), size:+size.toFixed(1)});
    // clipped / ellipsised text
    if (e.scrollWidth > e.clientWidth + 1 && cs.overflow !== 'visible')
      out.truncated.push({el:desc(e), rect:rectOf(e), scrollW:e.scrollWidth, clientW:e.clientWidth});
  }

  // interactive controls: tap size + accessible name
  const INTERACTIVE = 'button,a[href],input,select,textarea,[role=button],[role=switch],[role=tab],[tabindex]:not([tabindex="-1"])';
  const controls = [...document.body.querySelectorAll(INTERACTIVE)].filter(vis);
  for (const e of controls) {
    const r = e.getBoundingClientRect();
    if (r.width < 44 || r.height < 44)
      out.touch.push({el:desc(e), rect:rectOf(e), w:Math.round(r.width), h:Math.round(r.height)});
    const name = (e.getAttribute('aria-label') || e.getAttribute('title') ||
                  (e.innerText||'').trim() || e.getAttribute('alt') ||
                  (e.labels && e.labels.length ? e.labels[0].innerText : '') || '').trim();
    if (!name) out.unnamed.push({el:desc(e), rect:rectOf(e), html:e.outerHTML.slice(0,120)});
  }

  // overlapping TEXT (a real rendering fault, not just adjacency).
  // Only flag when neither element contains the other and both own text.
  const boxes = textEls.map(e => ({e, r:e.getBoundingClientRect()}));
  for (let i=0;i<boxes.length;i++) for (let j=i+1;j<boxes.length;j++) {
    const A=boxes[i], B=boxes[j];
    if (A.e.contains(B.e) || B.e.contains(A.e)) continue;
    const csA=getComputedStyle(A.e), csB=getComputedStyle(B.e);
    // Intentional layering. Walk ANCESTORS too: a static <span> inside the
    // fixed bottom nav is itself position:static, so checking only the element
    // counted every nav label against whatever content happened to be scrolled
    // under the nav — a large class of false positives.
    const layered = e => { let n=e;
      while (n && n !== document.body) {
        const p = getComputedStyle(n).position;
        if (p==='fixed'||p==='absolute'||p==='sticky') return true;
        n = n.parentElement;
      } return false; };
    if (layered(A.e) || layered(B.e)) continue;
    // A WRAPPED inline element (e.g. an inline <code>/<span class=mono> inside a
    // paragraph) has a single bounding box spanning every line it touches, so
    // two of them in the same paragraph "overlap" while rendering perfectly.
    // getClientRects().length > 1 is the reliable wrap signal.
    if (A.e.getClientRects().length > 1 || B.e.getClientRects().length > 1) continue;
    const ix = Math.min(A.r.right,B.r.right) - Math.max(A.r.left,B.r.left);
    const iy = Math.min(A.r.bottom,B.r.bottom) - Math.max(A.r.top,B.r.top);
    if (ix > 2 && iy > 2) {
      const area = ix*iy, small = Math.min(A.r.width*A.r.height, B.r.width*B.r.height);
      if (small > 0 && area/small > 0.25)
        out.overlap.push({a:desc(A.e), b:desc(B.e), overlapPx:Math.round(area),
                          aRect:rectOf(A.e), bRect:rectOf(B.e)});
    }
  }

  // horizontal overflow: content the user cannot reach because an ancestor
  // clips it and nothing scrolls.
  const mainEl = document.querySelector('main') || document.body;
  out.overflow.push({where:'main', scrollW:mainEl.scrollWidth, clientW:mainEl.clientWidth,
                     clipped: mainEl.scrollWidth > mainEl.clientWidth});
  const de = document.documentElement;
  out.overflow.push({where:'document', scrollW:de.scrollWidth, clientW:de.clientWidth,
                     clipped: de.scrollWidth > de.clientWidth});

  for (const k of ['contrast','tiny','touch','overlap','truncated','unnamed'])
    out.counts[k] = out[k].length;
  // keep payloads readable
  for (const k of ['contrast','tiny','touch','overlap','truncated','unnamed'])
    out[k] = out[k].slice(0, 25);
  return out;
}
"""


class UX:
    def __init__(self, page, outdir, port):
        self.page, self.outdir, self.port = page, outdir, port
        self.console: list[str] = []
        self.shots: list[str] = []

    # -------------------------------------------------------------- driving
    async def goto(self, view: str, timeout=6000) -> bool:
        """Switch to a named view (the nav buttons carry the visible label).
        Falls back to the phone 'More' sheet. Returns success."""
        for _ in range(2):
            try:
                await self.page.get_by_role("button", name=view, exact=True
                                            ).first.click(timeout=timeout)
                await self.page.wait_for_timeout(1200)
                return True
            except Exception:
                try:
                    await self.page.get_by_role("button", name="More"
                                                ).first.click(timeout=2500)
                    await self.page.wait_for_timeout(500)
                except Exception:
                    pass
        return False

    async def click(self, name: str, exact=False, timeout=5000) -> dict:
        """Click a control BY ITS VISIBLE NAME and report what happened.
        Returns {ok, error, new_console}. The rig is SIMULATED — pressing
        things is safe and expected."""
        before = len(self.console)
        try:
            await self.page.get_by_role("button", name=name, exact=exact
                                        ).first.click(timeout=timeout)
            await self.page.wait_for_timeout(900)
            return {"ok": True, "error": None,
                    "new_console": self.console[before:]}
        except Exception as e:
            return {"ok": False, "error": str(e)[:200],
                    "new_console": self.console[before:]}

    async def resize(self, name: str):
        w, h = VIEWPORTS[name]
        await self.page.set_viewport_size({"width": w, "height": h})
        await self.page.wait_for_timeout(900)

    async def set_night(self, on: bool):
        """Night (red) mode is a hard product constraint: white light ruins
        dark adaptation. Reviewers should audit BOTH themes."""
        try:
            await self.page.get_by_role("button", name="NIGHT").first.click(timeout=3000)
            await self.page.wait_for_timeout(700)
            return True
        except Exception:
            return False

    # ----------------------------------------------------------- gestures
    async def tap(self, name: str, exact=False, timeout=5000) -> dict:
        """Tap by visible name using a REAL touch event.

        Not the same as click(): Playwright treats aria-disabled="true" as
        non-actionable, so honest-disabled controls time out on locator.tap().
        We resolve the box ourselves and dispatch a touchscreen tap at its
        centre, which is what a finger actually does — and which is the only way
        to verify that a blocked control explains itself when pressed."""
        before = len(self.console)
        try:
            loc = self.page.get_by_role("button", name=name, exact=exact).first
            box = await loc.bounding_box(timeout=timeout)
            if box is None:
                return {"ok": False, "error": "no box (not rendered?)",
                        "new_console": self.console[before:]}
            await self.page.touchscreen.tap(box["x"] + box["width"] / 2,
                                            box["y"] + box["height"] / 2)
            await self.page.wait_for_timeout(900)
            return {"ok": True, "error": None, "new_console": self.console[before:]}
        except Exception as e:
            return {"ok": False, "error": str(e)[:200],
                    "new_console": self.console[before:]}

    async def swipe(self, x1, y1, x2, y2, steps=12):
        """A finger drag. Use it to scroll, and to test whether a canvas or map
        swallows the gesture."""
        await self.page.touchscreen.tap(x1, y1)  # ensure touch is the active input
        await self.page.mouse.move(x1, y1)
        await self.page.mouse.down()
        for i in range(1, steps + 1):
            await self.page.mouse.move(x1 + (x2 - x1) * i / steps,
                                       y1 + (y2 - y1) * i / steps)
        await self.page.mouse.up()
        await self.page.wait_for_timeout(500)

    async def can_scroll_from(self, x, y) -> dict:
        """THE scroll-trap test. Put a finger at (x,y), drag upward, and report
        whether the PAGE moved. If it did not, the user is stranded there."""
        before = await self.page.evaluate(
            "() => (document.querySelector('main')||document.body).scrollTop")
        h = (await self.page.evaluate("() => innerHeight"))
        await self.swipe(x, y, x, max(20, y - int(h * 0.45)))
        after = await self.page.evaluate(
            "() => (document.querySelector('main')||document.body).scrollTop")
        return {"scrolled": after != before, "from": before, "to": after}

    async def rotate(self):
        """Landscape <-> portrait. Phones and tablets rotate; layouts that only
        work in portrait are half-tested."""
        vp = self.page.viewport_size
        await self.page.set_viewport_size({"width": vp["height"], "height": vp["width"]})
        await self.page.wait_for_timeout(900)

    async def occlusion(self) -> dict:
        """How much of the viewport is persistent chrome rather than the work
        area? A bar that covers half a phone screen while telling you to operate
        the screen underneath is self-defeating — this makes that measurable."""
        return await self.page.evaluate("""() => {
          const vw = innerWidth, vh = innerHeight, area = vw * vh;
          let covered = 0; const parts = [];
          for (const e of document.body.querySelectorAll('*')) {
            const cs = getComputedStyle(e);
            if (cs.position !== 'fixed' && cs.position !== 'sticky') continue;
            if (cs.visibility === 'hidden' || cs.display === 'none') continue;
            if (parseFloat(cs.opacity) < 0.05) continue;
            if (e.parentElement && ['fixed','sticky'].includes(
                  getComputedStyle(e.parentElement).position)) continue; // outermost only
            const r = e.getBoundingClientRect();
            const w = Math.max(0, Math.min(r.right, vw) - Math.max(r.left, 0));
            const h = Math.max(0, Math.min(r.bottom, vh) - Math.max(r.top, 0));
            if (w * h < 400) continue;
            // Skip pass-through hosts: a transparent, text-free, non-interactive
            // full-screen wrapper occludes NOTHING. Counting them produced a
            // nonsense 106% reading. Real chrome either paints or holds content.
            const bg = cs.backgroundColor || '';
            const paints = !(bg === 'transparent' || bg.startsWith('rgba(0, 0, 0, 0)'));
            const holds = (e.innerText || '').trim().length > 0;
            if (!paints && !holds) continue;
            if (cs.pointerEvents === 'none' && !holds) continue;
            covered += w * h;
            parts.push({txt: (e.innerText||'').split(String.fromCharCode(10)).join(' ').slice(0,40),
                        h: Math.round(h), pct: +(w*h/area*100).toFixed(1)});
          }
          return {viewport: [vw, vh], occludedPct: +(covered/area*100).toFixed(1),
                  parts: parts.sort((a,b)=>b.pct-a.pct).slice(0,5)};
        }""")

    async def screenfuls(self, tag: str, maxn=6) -> list:
        """Capture the view one SCREENFUL at a time, top to bottom.

        A viewport screenshot shows what the user can see NOW, not what exists.
        Defects below the fold (a button wrapping onto its own line at the bottom
        of a long panel) are invisible to a single capture and still real."""
        out = []
        await self.page.evaluate(
            "() => {const m=document.querySelector('main')||document.body; m.scrollTop=0;}")
        await self.page.wait_for_timeout(400)
        for i in range(maxn):
            out.append(await self.shot(f"{tag}-screen{i+1}"))
            more = await self.page.evaluate("""() => {
              const m = document.querySelector('main') || document.body;
              const before = m.scrollTop;
              m.scrollTop = before + innerHeight * 0.9;
              return m.scrollTop > before + 4;
            }""")
            await self.page.wait_for_timeout(450)
            if not more:
                break
        return out

    # ------------------------------------------------------------- evidence
    async def shot(self, name: str, full_page=False) -> str:
        """Screenshot to PNG. READ the returned path afterwards — that is how
        you actually SEE the interface.

        Defaults to a VIEWPORT capture, which is what the user really sees.
        Only pass full_page=True to audit long scrolling content, and NEVER
        trust a full_page capture for a modal/overlay: `position: fixed`
        elements are rendered at the scroll offset in a full-page capture, so
        a dialog will appear clipped or floating in the wrong place. That is a
        screenshot artifact, not a layout bug — do not report it as one."""
        p = str(pathlib.Path(self.outdir) / f"{name}.png")
        await self.page.screenshot(path=p, full_page=full_page)
        self.shots.append(p)
        return p

    async def audit(self) -> dict:
        return await self.page.evaluate(_AUDIT_JS)

    @staticmethod
    def audit_summary(a: dict) -> str:
        c = a["counts"]
        ov = [o for o in a["overflow"] if o["clipped"]]
        return (f"contrast<AA:{c['contrast']} tiny<12px:{c['tiny']} "
                f"touch<44px:{c['touch']} textOverlap:{c['overlap']} "
                f"truncated:{c['truncated']} unnamed:{c['unnamed']} "
                f"clipped:{'YES '+str(ov) if ov else 'no'}")

    async def audit_all_views(self, tag: str) -> dict:
        """Sweep every view: screenshot + audit. Returns {view: audit}."""
        res = {}
        for v in VIEWS:
            if not await self.goto(v):
                res[v] = {"error": "could not navigate"}
                continue
            await self.shot(f"{tag}-{v.lower()}")
            res[v] = await self.audit()
        return res


@contextlib.asynccontextmanager
async def session(port: int, persona: str, viewport="desktop", night=False):
    outdir = pathlib.Path(os.environ.get("UX_OUT", ".")) / persona
    outdir.mkdir(parents=True, exist_ok=True)
    w, h = VIEWPORTS[viewport]
    async with async_playwright() as p:
        b = await p.chromium.launch()
        # A desktop browser resized to phone dimensions is NOT a phone. Without
        # has_touch the page gets a mouse, `locator.click()` never swipes, and an
        # entire class of defect (a canvas that swallows every vertical swipe so
        # the page can no longer be scrolled) becomes physically impossible to
        # trigger. That is exactly how a real scroll-trap survived a 130-screenshot
        # review and was found by a human in ten minutes.
        phone_like = viewport in ("phone", "s25ultra", "iphone-pro-max")
        page = await b.new_page(viewport={"width": w, "height": h},
                                device_scale_factor=2,
                                has_touch=True,
                                is_mobile=phone_like)
        ux = UX(page, outdir, port)
        page.on("console", lambda m: ux.console.append(f"{m.type}: {m.text}")
                if m.type in ("error", "warning") else None)
        page.on("pageerror", lambda e: ux.console.append(f"pageerror: {e}"))
        page.on("requestfailed", lambda r: ux.console.append(
            f"requestfailed: {r.url} {r.failure}"))
        await page.goto(f"http://127.0.0.1:{port}", wait_until="networkidle")
        await page.wait_for_timeout(2500)
        if night:
            await ux.set_night(True)
        try:
            yield ux
        finally:
            await b.close()


def save(obj, path):
    pathlib.Path(path).write_text(json.dumps(obj, indent=1), encoding="utf-8")
