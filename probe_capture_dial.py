"""Is the Capture camera dial actually on screen? Look, do not infer.

The DOM tests cannot answer this: jsdom has no layout, so CameraDial's
ResizeObserver never fires, `box` stays null, `dialRadius` returns the full
DIAL_R and `fits` is true in every test that has ever run. Whether the disc
renders in a real browser depends on a measurement jsdom does not make.
"""
import asyncio, os, sys
from playwright.async_api import async_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8800"
OUT = "c:/Users/bear/astro/dial-evidence"


async def report(page, label):
    info = await page.evaluate("""() => {
        const root = document.querySelector('[data-camera-dial]');
        const disc = document.querySelector('[data-dial-disc]');
        const out = {root: !!root, disc: !!disc};
        if (root) {
            const r = root.getBoundingClientRect();
            out.rootBox = {w: Math.round(r.width), h: Math.round(r.height)};
            const p = root.parentElement;
            if (p) {
                const pr = p.getBoundingClientRect();
                out.parentBox = {w: Math.round(pr.width), h: Math.round(pr.height)};
            }
        }
        if (disc) {
            const d = disc.getBoundingClientRect();
            out.discBox = {x: Math.round(d.x), y: Math.round(d.y),
                           w: Math.round(d.width), h: Math.round(d.height)};
            const cs = getComputedStyle(disc);
            out.discVisible = cs.display !== 'none' && cs.visibility !== 'hidden'
                              && cs.opacity !== '0';
        }
        // the radius rule: needs >= 188px in BOTH axes to clear DIAL_MIN_R 124
        return out;
    }""")
    print(f"  [{label}] {info}")
    return info


async def main():
    os.makedirs(OUT, exist_ok=True)
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        for name, vp in [("desktop", {"width": 1440, "height": 900}),
                         ("phone", {"width": 390, "height": 844})]:
            ctx = await browser.new_context(viewport=vp, service_workers="block")
            page = await ctx.new_page()
            page.on("console", lambda m: print(f"    console[{m.type}]: {m.text[:160]}")
                    if m.type in ("error", "warning") else None)
            await page.goto(BASE, wait_until="networkidle")
            await asyncio.sleep(2)
            await page.screenshot(path=f"{OUT}/{name}-01-landing.png", full_page=False)
            print(f"[{name}] title={await page.title()!r} url={page.url}")

            body = (await page.inner_text("body"))[:300].replace("\n", " | ")
            print(f"[{name}] body: {body}")

            # Navigation is store-driven (setView), so there is no URL to visit
            # and a hidden desktop rail duplicates every nav label. Require
            # VISIBLE, scope to the primary nav, and verify the view actually
            # changed -- a probe that never left Equipment reads as a missing
            # dial, which is the same trap in the opposite direction.
            nav = page.locator('nav[aria-label="Primary"]')
            clicked = False
            for sel in [nav.get_by_text("CAPTURE", exact=True),
                        page.locator("button:visible, a:visible").filter(has_text="CAPTURE")]:
                n = await sel.count()
                print(f"[{name}] candidates={n}")
                if n >= 1:
                    try:
                        await sel.first.click(timeout=5000)
                        await asyncio.sleep(2.5)
                        clicked = True
                        break
                    except Exception as e:
                        print(f"[{name}] click failed: {str(e)[:120]}")
            if not clicked:
                # last resort: drive the store the way the app itself does
                await page.evaluate("() => window.__adSetView && window.__adSetView('capture')")
                await asyncio.sleep(2)
            head = (await page.inner_text("body"))[:160].replace("\n", " | ")
            print(f"[{name}] after nav: {head}")
            await page.screenshot(path=f"{OUT}/{name}-02-capture.png", full_page=False)
            await report(page, name)
            await ctx.close()
        await browser.close()

asyncio.run(main())
