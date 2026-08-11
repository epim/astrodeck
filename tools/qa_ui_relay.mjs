// tools/qa_ui_relay.mjs — walk the operator's actual screens over the RELAY and
// capture what they LOOK like, not what their DOM claims.
//
// WHY RENDERED, NOT PARSED. A DOM assertion says an <img> exists with a src; it
// cannot say the picture arrived, that it is not four times upscaled, or that a
// tile is a grey box. Every finding this session came from looking: the gallery
// was a wall of empty frames whose markup was perfectly well-formed, and the
// live tile's markup was equally well-formed while showing a 160px thumbnail in
// a 700px slot. So this script's product is SCREENSHOTS plus a handful of
// measurements a screenshot cannot give (natural vs displayed pixels, HTTP
// status counts), and a human — or a model that can see — reads them.
//
// WHY THE RELAY. The rig binds 127.0.0.1 and every remote client is tunnelled
// through one WebSocket. Request serialisation, the per-IP token bucket and the
// real round-trip only exist on that path. A localhost run passes on a machine
// where none of them do.
//
// THE DWELL. The gallery is loaded, then left alone for 30 s and re-examined.
// A screen that is correct on arrival and has quietly fallen into "telemetry
// catching up" thirty seconds later is the 2026-08-10 defect, and it is
// invisible to any check that only looks once.
//
// Usage:
//   node tools/qa_ui_relay.mjs --secrets <dir> --out <dir> [--dwell 30]

import { chromium } from "playwright";
import fs from "node:fs";
import path from "node:path";

const args = Object.fromEntries(
  process.argv.slice(2).reduce((acc, a, i, arr) => {
    if (a.startsWith("--")) acc.push([a.slice(2), arr[i + 1]]);
    return acc;
  }, []),
);
const SECRETS = args.secrets;
const OUT = args.out || ".";
const RELAY = args.relay || "https://astrodeck-relay.fly.dev";
const DWELL_MS = Number(args.dwell || 30) * 1000;
if (!SECRETS) throw new Error("--secrets <dir> is required");

const homeId = fs.readFileSync(path.join(SECRETS, "home_id.txt"), "utf8").trim();
const token = fs.readFileSync(path.join(SECRETS, "session_token.txt"), "utf8").trim();
const base = `${RELAY}/h/${homeId}/`;
fs.mkdirSync(OUT, { recursive: true });

const report = { steps: [], shots: [], http: {}, errors: [] };
const note = (step, ok, detail) => {
  report.steps.push({ step, ok, detail });
  console.log(`${ok === null ? "INFO" : ok ? "PASS" : "FAIL"}  ${step}${detail ? " — " + detail : ""}`);
};
const shot = async (page, name, caption) => {
  const p = path.join(OUT, `${name}.png`);
  await page.screenshot({ path: p, fullPage: false });
  report.shots.push({ name, caption });
  console.log(`  [shot] ${name}.png — ${caption}`);
};

const browser = await chromium.launch({ headless: true });
const ctx = await browser.newContext({
  viewport: { width: 1920, height: 1080 },
  deviceScaleFactor: 2,               // HiDPI: upscaling shows up here
});
await ctx.addCookies([{
  name: "ad_session", value: token, url: RELAY,
  httpOnly: false, secure: true, sameSite: "Lax",
}]);
const page = await ctx.newPage();

const status = {};
page.on("response", (r) => {
  const u = r.url();
  const key = u.includes("/api/gallery/thumb") ? "gallery-thumb"
    : /\/api\/preview\/\d+$/.test(u) ? "preview-display"
    : u.includes("/api/preview/") && u.includes("thumb.jpg") ? "preview-thumb"
    : null;
  if (!key) return;
  status[key] = status[key] || {};
  status[key][r.status()] = (status[key][r.status()] || 0) + 1;
});
page.on("pageerror", (e) => report.errors.push(String(e).slice(0, 200)));
page.on("console", (m) => { if (m.type() === "error") report.errors.push(m.text().slice(0, 200)); });

/** Every visible banner/pill the operator would actually read. */
async function banners(page) {
  return page.locator("[role=status], [role=alert]").filter({ visible: true })
    .allInnerTexts().catch(() => []);
}

/** Measure upscaling: an image drawn larger than its own pixels is blurry. */
async function imageScale(page, sel) {
  return page.locator(sel).evaluateAll((els) => els
    .filter((e) => e.naturalWidth > 0)
    .map((e) => {
      const r = e.getBoundingClientRect();
      return {
        natural: e.naturalWidth,
        css: Math.round(r.width),
        device: Math.round(r.width * (window.devicePixelRatio || 1)),
        upscale: +(Math.round(r.width * (window.devicePixelRatio || 1)) / e.naturalWidth).toFixed(2),
        src: e.currentSrc.split("/").slice(-2).join("/").slice(0, 60),
      };
    }));
}

/**
 * Dismiss any blocking modal before navigating.
 *
 * The 2026-08-11 run timed out clicking "Capture" and reported a crash. The
 * page was fine: a "HIGH CLOUD FORECAST TONIGHT" dialog was open, and a modal
 * intercepts pointer events for the whole app. A harness that cannot tell a
 * dialog from a broken page will keep manufacturing failures — and, worse,
 * would hide a real one behind the same message.
 */
async function dismissModals(page) {
  for (let i = 0; i < 3; i++) {
    const ok = page.getByRole("button", { name: /^(ok|dismiss|got it|close)$/i })
      .filter({ visible: true }).first();
    if (!(await ok.count())) return i;
    await ok.click().catch(() => {});
    await page.waitForTimeout(600);
  }
  return 3;
}

async function goView(name, rx) {
  await dismissModals(page);
  const link = page.getByRole("link", { name: rx }).or(page.getByRole("button", { name: rx }))
    .filter({ visible: true }).first();
  if (await link.count()) { await link.click(); return true; }
  return false;
}

try {
  await page.goto(base, { waitUntil: "domcontentloaded", timeout: 60_000 });
  await page.waitForTimeout(5000);
  const body = await page.locator("body").innerText();
  const noLink = /NO LINK/i.test(body);
  note("websocket is live (not 'NO LINK')", !noLink,
       noLink ? "session rejected — every later check would be meaningless" : "");
  if (noLink) throw new Error("no live link");
  await shot(page, "01-landing", "first screen after the relay serves the app");

  // ---------------------------------------------------------------- GALLERY
  await goView("gallery", /gallery/i);
  await page.waitForTimeout(12_000);
  await shot(page, "02-gallery-load", "gallery ~12s after opening it");
  const gImgs = await imageScale(page, "img[src*='gallery/thumb']");
  const gLoaded = gImgs.length;
  const gTotal = await page.locator("img[src*='gallery/thumb']").count();
  note("gallery tiles decode", gTotal > 0 && gLoaded === gTotal,
       `${gLoaded}/${gTotal} decoded`);
  if (gImgs.length) {
    const worst = gImgs.reduce((a, b) => (b.upscale > a.upscale ? b : a));
    note("gallery tiles are not upscaled", worst.upscale <= 1.05,
         `worst ${worst.upscale}x (natural ${worst.natural}px into ${worst.device} device px)`);
  }
  note("gallery banners on arrival", null, JSON.stringify(await banners(page)));

  // THE DWELL. Correct on arrival is not the claim under test.
  console.log(`  …dwelling ${DWELL_MS / 1000}s on the gallery`);
  await page.waitForTimeout(DWELL_MS);
  const after = await banners(page);
  const stale = after.join(" ").match(/CATCHING UP|SHOWING DATA FROM|DISCONNECTED/i);
  note(`gallery has NOT fallen stale after ${DWELL_MS / 1000}s`, !stale,
       stale ? `banner says: ${JSON.stringify(after)}` : JSON.stringify(after));
  await shot(page, "03-gallery-after-dwell",
             `gallery ${DWELL_MS / 1000}s later — the staleness check`);

  // ------------------------------------------------------------- LIVE / MONITOR
  await goView("monitor", /monitor|live/i);
  await page.waitForTimeout(10_000);
  await shot(page, "04-monitor-live", "live session view during the running plan");
  const mImgs = await imageScale(page, "img[src*='/api/preview/']");
  if (mImgs.length) {
    const t = mImgs.reduce((a, b) => (b.device > a.device ? b : a));
    note("live tile is not an upscaled thumbnail", t.upscale <= 1.6,
         `${t.upscale}x — natural ${t.natural}px into ${t.device} device px (${t.src})`);
  } else {
    note("live tile is not an upscaled thumbnail", null, "no preview image on screen");
  }
  note("monitor banners", null, JSON.stringify(await banners(page)));

  // -------------------------------------------------------------- CAPTURE
  await goView("capture", /^capture$/i);
  await page.waitForTimeout(10_000);
  await shot(page, "05-capture", "capture screen with the plan running");
  const cImgs = await imageScale(page, "img[src*='/api/preview/']");
  if (cImgs.length) {
    const t = cImgs.reduce((a, b) => (b.device > a.device ? b : a));
    note("capture preview resolution", t.upscale <= 1.6,
         `${t.upscale}x — natural ${t.natural}px into ${t.device} device px`);
  }
  note("capture banners", null, JSON.stringify(await banners(page)));

  note("no uncaught page errors", report.errors.length === 0,
       report.errors.slice(0, 2).join(" | "));
} catch (e) {
  note("run completed without throwing", false, String(e).split("\n")[0]);
  try { await shot(page, "99-crash", "state when the run threw"); } catch { /* */ }
} finally {
  report.http = status;
  fs.writeFileSync(path.join(OUT, "report.json"), JSON.stringify(report, null, 2));
  await browser.close();
}

console.log("\nHTTP by asset class:", JSON.stringify(status));
const failed = report.steps.filter((s) => s.ok === false);
console.log(`${report.steps.filter((s) => s.ok === true).length} passed, ${failed.length} failed, ` +
            `${report.steps.filter((s) => s.ok === null).length} informational`);
process.exit(failed.length ? 1 : 0);
