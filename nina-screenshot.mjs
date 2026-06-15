// Screenshots of AstroDeck running in NINA bridge mode (server already bridged).
import { chromium } from "playwright";

const base = "http://127.0.0.1:8800";
const out = ".remember/tmp";
const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });

await page.goto(base, { waitUntil: "networkidle" });
await page.waitForTimeout(1500);
await page.screenshot({ path: `${out}/nina-connect.png` });   // NINA badge + bridge panel

await page.click("text=CAPTURE");
await page.waitForTimeout(600);
await page.click("text=Single");
await page.waitForTimeout(3500);                               // NINA frame + HFR/stars
await page.screenshot({ path: `${out}/nina-capture.png` });

await page.click("text=FOCUS");
await page.waitForTimeout(600);
await page.screenshot({ path: `${out}/nina-focus.png` });     // NINA V-curve from last AF

await browser.close();
console.log("nina screenshots written");
