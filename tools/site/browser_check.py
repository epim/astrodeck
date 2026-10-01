"""Independent static-site browser review. Local server only; no app or rig access."""
from __future__ import annotations
import argparse
from pathlib import Path
from playwright.sync_api import sync_playwright

PAGES = ("index", "features", "flows", "hardware", "weather", "getting-started", "releases")
ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / ".probe" / "site-review"
REPORT = ROOT / "tools" / "site" / "render-review.md"
LAYOUT = """() => {
 const root = document.documentElement, width = innerWidth;
 const images = [...document.images].map(x => ({
  src: x.getAttribute('src'), loaded: x.complete && x.naturalWidth > 0
 }));
 const wraps = [...document.querySelectorAll('main .wrap')].map(x => {
  const r=x.getBoundingClientRect(); return {left:r.left,right:width-r.right};
 });
 const offenders = [...document.querySelectorAll('main *')].filter(x => {
  const r=x.getBoundingClientRect();
  const parent=x.closest('.table-wrap, .tablewrap, pre');
  return !parent && r.width && (r.left < -1 || r.right > width+1);
 }).slice(0,8).map(x=>({tag:x.tagName, cls:x.className}));
 return {overflow:Math.max(root.scrollWidth,document.body.scrollWidth)-width,
         images, wraps, offenders,
         h1:document.querySelectorAll('h1').length,
         main:document.querySelectorAll('main').length};
}"""

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:8896")
    args = parser.parse_args()
    if not args.base.startswith("http://127.0.0.1:"):
        raise SystemExit("Review server must be localhost")
    OUT.mkdir(parents=True, exist_ok=True)
    cases, checks, faults = [], [], []
    def check(name, ok, detail=""):
        checks.append((name, bool(ok), detail))
        if not ok:
            faults.append(f"{name}: {detail}")
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        for width in (320, 390, 1440):
            for theme in ("light", "dark"):
                ctx = browser.new_context(viewport={"width": width, "height": 950},
                                          color_scheme=theme, reduced_motion="reduce")
                ctx.add_init_script(f"localStorage.setItem('astrodeck-theme', {theme!r})")
                page = ctx.new_page()
                errors = []
                page.on("pageerror", lambda exc: errors.append(str(exc)))
                for slug in PAGES:
                    errors.clear()
                    page.goto(f"{args.base}/{slug}.html", wait_until="networkidle")
                    page.evaluate("document.fonts.ready")
                    for img in page.locator("img").all():
                        img.scroll_into_view_if_needed()
                    page.locator("footer").scroll_into_view_if_needed()
                    page.evaluate("window.scrollTo(0,0)")
                    page.wait_for_function("() => [...document.images].every(i=>i.complete)")
                    state = page.evaluate(LAYOUT)
                    key = f"{slug}-{width}-{theme}"
                    page.screenshot(path=str(OUT / f"{key}.png"), full_page=True)
                    if width in (390, 1440):
                        page.screenshot(path=str(OUT / f"{key}-top.png"))
                    gutter = min((min(w["left"], w["right"]) for w in state["wraps"]), default=0)
                    bad_images = [x["src"] for x in state["images"] if not x["loaded"]]
                    ok = state["overflow"] <= 1 and not bad_images and not errors
                    if width < 640:
                        ok = ok and gutter >= 15.5
                    cases.append((key, ok, state["overflow"], round(gutter, 1),
                                  len(state["images"]), "; ".join(errors)))
                    if not ok:
                        faults.append(f"{key}: overflow={state['overflow']}, "
                                      f"gutter={gutter}, images={bad_images}, "
                                      f"errors={errors}, offenders={state['offenders']}")
                    print(f"{key}: {'PASS' if ok else 'FAIL'}", flush=True)
                ctx.close()
        ctx = browser.new_context(viewport={"width": 390, "height": 844})
        page = ctx.new_page()
        page.goto(f"{args.base}/index.html", wait_until="networkidle")
        page.keyboard.press("Tab")
        check("First keyboard focus is skip link",
              page.locator(".skip-link").evaluate("(e)=>e===document.activeElement"))
        check("Keyboard focus has visible outline",
              page.locator(".skip-link").evaluate("(e)=>getComputedStyle(e).outlineStyle!=='none'"))
        page.keyboard.press("Enter")
        check("Skip link moves focus to main",
              page.locator("main").evaluate("(e)=>e===document.activeElement"))
        menu = page.locator("[data-menu-toggle]")
        menu.focus()
        page.keyboard.press("Enter")
        check("Mobile menu opens with keyboard", menu.get_attribute("aria-expanded") == "true")
        page.keyboard.press("Tab")
        check("Menu tab enters navigation",
              page.evaluate("document.activeElement.closest('#site-nav')!==null"))
        page.keyboard.press("Escape")
        check("Escape closes menu and restores focus",
              menu.get_attribute("aria-expanded") == "false"
              and menu.evaluate("(e)=>e===document.activeElement"))
        menu.click()
        toggle = page.locator("[data-theme-toggle]")
        check("Initial theme uses system", toggle.inner_text() == "Theme: System")
        toggle.click()
        check("Theme cycles to light", page.locator("html").get_attribute("data-theme") == "light")
        toggle.click()
        check("Theme cycles to dark", page.locator("html").get_attribute("data-theme") == "dark")
        page.reload(wait_until="networkidle")
        check("Theme persists after reload", page.locator("html").get_attribute("data-theme") == "dark")
        menu.click()
        toggle.click()
        check("Theme returns to system", page.locator("html").get_attribute("data-theme") is None)
        page.emulate_media(color_scheme="dark")
        dark = page.locator("body").evaluate("(e)=>getComputedStyle(e).backgroundColor")
        page.emulate_media(color_scheme="light")
        light = page.locator("body").evaluate("(e)=>getComputedStyle(e).backgroundColor")
        check("System theme follows OS preference", dark != light, f"{dark} / {light}")
        ctx.close()
        ctx = browser.new_context(viewport={"width": 320, "height": 844})
        ctx.add_init_script("""Object.defineProperty(window,'localStorage',{
          configurable:true,get(){throw new DOMException('Denied','SecurityError')}
        });""")
        page = ctx.new_page()
        denied_errors = []
        page.on("pageerror", lambda exc: denied_errors.append(str(exc)))
        page.goto(f"{args.base}/index.html", wait_until="networkidle")
        page.locator("[data-menu-toggle]").click()
        page.locator("[data-theme-toggle]").click()
        check("Theme works with denied storage",
              page.locator("html").get_attribute("data-theme") == "light"
              and not denied_errors, str(denied_errors))
        page.screenshot(path=str(OUT / "storage-denied-320.png"), full_page=True)
        ctx.close()
        ctx = browser.new_context(viewport={"width": 320, "height": 844},
                                  java_script_enabled=False, color_scheme="light")
        page = ctx.new_page()
        for slug in PAGES:
            page.goto(f"{args.base}/{slug}.html", wait_until="networkidle")
            visible = all(a.is_visible() for a in page.locator("#site-nav a").all())
            check(f"{slug} navigation without JavaScript", visible)
            state = page.evaluate(LAYOUT)
            check(f"{slug} no-JS page reflows", state["overflow"] <= 1,
                  f"overflow={state['overflow']}")
        page.goto(f"{args.base}/index.html", wait_until="networkidle")
        page.screenshot(path=str(OUT / "no-javascript-320.png"), full_page=True)
        ctx.close()
        for width in (320, 1440):
            ctx = browser.new_context(viewport={"width": width, "height": 950},
                                      color_scheme="light")
            page = ctx.new_page()
            for slug in PAGES:
                page.goto(f"{args.base}/{slug}.html", wait_until="networkidle")
                page.add_style_tag(content="html { font-size: 200% !important; }")
                state = page.evaluate(LAYOUT)
                check(f"{slug} 200-percent-text at {width}px",
                      state["overflow"] <= 1, f"overflow={state['overflow']}")
                page.screenshot(path=str(OUT / f"{slug}-{width}-text200.png"), full_page=True)
            ctx.close()
        browser.close()
    lines = [
        "# Independent site render review", "",
        "Local static server only. No astronomy server, live rig or real configuration was accessed.",
        "Automated matrix: all seven pages at 320, 390 and 1440 CSS pixels in both light and dark themes.",
        "Screenshots are under .probe/site-review/. Text-resize checks set the root font size to 200 percent.",
        "", "## Layout matrix", "",
        "| Page / width / theme | Result | Page overflow px | Minimum gutter px | Images | Browser errors |",
        "|---|---|---:|---:|---:|---|"]
    for key, ok, overflow, gutter, images, errors in cases:
        lines.append(f"| {key} | {'PASS' if ok else 'FAIL'} | {overflow} | {gutter} | {images} | {errors or 'None'} |")
    lines += ["", "## Interaction and fallback checks", "",
              "| Check | Result | Detail |", "|---|---|---|"]
    for name, ok, detail in checks:
        lines.append(f"| {name} | {'PASS' if ok else 'FAIL'} | {detail} |")
    lines += ["", "## Findings", ""]
    lines += [f"- {f}" for f in faults] or ["No automated failures."]
    lines += ["", "## Visual inspection", "",
              "Pending screenshot review; automated layout checks do not establish visual quality.", ""]
    REPORT.write_text("\n".join(lines), encoding="utf-8")
    print(f"Matrix cases: {len(cases)}; interaction/fallback checks: {len(checks)}; failures: {len(faults)}")
    if faults:
        raise SystemExit(1)

if __name__ == "__main__":
    main()
