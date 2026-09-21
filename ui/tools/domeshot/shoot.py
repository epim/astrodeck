import pathlib
import sys

from playwright.sync_api import sync_playwright

here = pathlib.Path(__file__).resolve().parent
url = (here / "dome.html").as_uri()
out = here / "dome.png"

with sync_playwright() as p:
    b = p.chromium.launch()
    page = b.new_page(viewport={"width": 1200, "height": 1500},
                      device_scale_factor=2)
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    page.goto(url)
    page.wait_for_function("document.title === 'ready'", timeout=15000)
    page.locator("#row").screenshot(path=str(out))
    b.close()

if errors:
    print("PAGE ERRORS:", *errors, sep="\n  ")
    sys.exit(1)
print("wrote", out)
