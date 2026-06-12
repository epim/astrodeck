// UI verification screenshots: day + night mode, several views.
import { chromium } from "playwright";

const base = "http://127.0.0.1:8800";
const out = ".remember/tmp";

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });

await page.goto(base, { waitUntil: "networkidle" });
await page.waitForTimeout(1500);
await page.screenshot({ path: `${out}/ui-connect.png` });

// Capture view (preview should be populated from earlier captures)
await page.click("text=CAPTURE");
await page.waitForTimeout(800);
await page.screenshot({ path: `${out}/ui-capture.png` });

// Trigger a capture to refresh preview
await page.click("text=Single");
await page.waitForTimeout(3500);
await page.screenshot({ path: `${out}/ui-capture-frame.png` });

// Focus view
await page.click("text=FOCUS");
await page.waitForTimeout(500);
await page.screenshot({ path: `${out}/ui-focus.png` });

// Mount view
await page.click("text=MOUNT");
await page.waitForTimeout(800);
await page.screenshot({ path: `${out}/ui-mount.png` });

// Sequence view
await page.click("text=PLAN");
await page.waitForTimeout(500);
await page.screenshot({ path: `${out}/ui-sequence.png` });

// Power view
await page.click("text=POWER");
await page.waitForTimeout(800);
await page.screenshot({ path: `${out}/ui-power.png` });

// Night mode
await page.click("text=NIGHT");
await page.waitForTimeout(400);
await page.click("text=CAPTURE");
await page.waitForTimeout(600);
await page.screenshot({ path: `${out}/ui-night.png` });

await browser.close();
console.log("screenshots written");
