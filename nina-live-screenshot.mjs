// Screenshot AstroDeck bridged to the LIVE NINA at astrotown.lan, incl. discovery.
import { chromium } from "playwright";

const base = "http://127.0.0.1:8800";
const out = ".remember/tmp";
const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1440, height: 980 } });

await page.goto(base, { waitUntil: "networkidle" });
await page.waitForTimeout(1500);

// Type the real host hint and scan the network for NINA instances.
const hostInput = page.locator('input').filter({ hasText: "" }).nth(0);
await page.getByText("NINA host (or hint for scan)").locator("xpath=following-sibling::input").fill("astrotown.lan").catch(() => {});
await page.click("text=Scan Network");
await page.waitForTimeout(5000);                 // discovery sweep ~3.5s
await page.screenshot({ path: `${out}/nina-live-connect.png`, fullPage: true });

await browser.close();
console.log("live nina screenshot written");
