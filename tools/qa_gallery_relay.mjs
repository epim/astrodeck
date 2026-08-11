// tools/qa_gallery_relay.mjs — gallery QA against the RELAY, not localhost.
//
// WHY THE RELAY. The rig binds 127.0.0.1 and every remote client's traffic is
// tunnelled through one WebSocket, so the relay is the only path that exercises
// what an operator actually experiences: request serialisation through a single
// tunnel, the real round-trip, and the real TLS. A localhost run passes on a
// machine where none of those exist — see docs/superpowers memory
// `astrodeck-ui-probe-traps`, where a broken probe read as a passing app.
//
// CREDENTIALS ARE READ FROM FILES AND NEVER PRINTED. `mint-token.ps1` mints a
// 30-minute admin session JWT; it belongs in the `ad_session` COOKIE, not the
// `?token=` query (which compares against the shared admin token and never
// matches). The home routing key is a shared secret too. Both are passed by
// path so neither can land in a transcript or a CI log.
//
// Usage:
//   node tools/qa_gallery_relay.mjs --secrets <dir> --out <dir> [--filter Ha]

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
if (!SECRETS) throw new Error("--secrets <dir> is required");

const homeId = fs.readFileSync(path.join(SECRETS, "home_id.txt"), "utf8").trim();
const token = fs.readFileSync(path.join(SECRETS, "session_token.txt"), "utf8").trim();
const base = `${RELAY}/h/${homeId}/`;
fs.mkdirSync(OUT, { recursive: true });

const report = { base: `${RELAY}/h/<home>/`, steps: [], thumbs: [], errors: [] };
const note = (step, ok, detail) => {
  report.steps.push({ step, ok, detail });
  console.log(`${ok ? "PASS" : "FAIL"}  ${step}${detail ? " — " + detail : ""}`);
};

/** The library's own count, off the "N frames, X GB · showing M" strip. Reading
 *  the SERVER's number rather than counting tiles: the grid is paged (200 of
 *  642), so tile counts cannot tell a narrowed filter from a shorter page. */
async function readShowing(page) {
  const txt = await page.locator("body").innerText();
  const m = txt.match(/([\d,]+)\s+frames?,/i);
  return m ? Number(m[1].replace(/,/g, "")) : null;
}

const browser = await chromium.launch({ headless: true });
// A desktop viewport is the whole point: the stall was reported on desktop,
// where a wide grid puts far more tiles on screen than a phone does.
const ctx = await browser.newContext({
  viewport: { width: 1920, height: 1080 },
  deviceScaleFactor: 2,          // HiDPI: a 256px thumb is upscaled here
  ignoreHTTPSErrors: false,
});
await ctx.addCookies([{
  name: "ad_session", value: token, url: RELAY,
  httpOnly: false, secure: true, sameSite: "Lax",
}]);

const page = await ctx.newPage();

// Every thumbnail response, with timing — this is the stall measurement.
const thumbTimes = new Map();
page.on("request", (r) => {
  if (r.url().includes("/api/gallery/thumb")) thumbTimes.set(r.url(), Date.now());
});
page.on("response", async (res) => {
  const u = res.url();
  if (!u.includes("/api/gallery/thumb")) return;
  const started = thumbTimes.get(u) || Date.now();
  let bytes = 0;
  try { bytes = (await res.body()).length; } catch { /* aborted */ }
  report.thumbs.push({
    status: res.status(), ms: Date.now() - started, bytes,
    w: new URL(u).searchParams.get("w"),
  });
});
page.on("pageerror", (e) => report.errors.push(String(e).slice(0, 300)));
page.on("console", (m) => {
  if (m.type() === "error") report.errors.push(m.text().slice(0, 300));
});

try {
  const t0 = Date.now();
  await page.goto(base, { waitUntil: "domcontentloaded", timeout: 60_000 });
  note("relay serves the app", true, `${Date.now() - t0} ms`);

  // TRAP (memory: astrodeck-ui-probe-traps). Without a valid session the app
  // renders its no-live-data state and every check below would pass or fail for
  // the wrong reason. Assert the link is real before trusting anything else.
  await page.waitForTimeout(4000);
  const body = await page.locator("body").innerText();
  const noLink = /NO LINK/i.test(body);
  note("websocket is live (header is not 'NO LINK')", !noLink,
       noLink ? "session rejected — re-mint the token" : "");
  if (noLink) throw new Error("no live link; the rest of the run would be meaningless");

  // Navigate to the gallery. The desktop rail duplicates nav labels in a hidden
  // element, so match only what is visible.
  const navT0 = Date.now();
  const link = page.getByRole("link", { name: /gallery/i }).or(
    page.getByRole("button", { name: /gallery/i })).filter({ visible: true }).first();
  if (await link.count()) {
    await link.click();
  } else {
    await page.goto(base + "#/gallery", { waitUntil: "domcontentloaded" });
  }
  await page.waitForTimeout(2000);

  // A view marker, not a screenshot-shaped guess: the gallery is only "loaded"
  // when its own controls exist.
  const marker = page.locator("text=/frames?\\b/i").first();
  const haveMarker = await marker.count().catch(() => 0);
  note("gallery view rendered", haveMarker > 0, `${Date.now() - navT0} ms to view`);

  // THE STALL MEASUREMENT. Wait for the grid to settle, then count what
  // actually arrived versus what was asked for.
  const settleMs = 45_000;
  const deadline = Date.now() + settleMs;
  let lastCount = -1, stableFor = 0;
  while (Date.now() < deadline) {
    await page.waitForTimeout(1500);
    const n = report.thumbs.length;
    if (n === lastCount) { stableFor += 1500; if (stableFor >= 6000) break; }
    else { stableFor = 0; lastCount = n; }
  }

  const imgs = await page.locator("img").evaluateAll((els) =>
    els.filter((e) => e.src && e.src.includes("gallery/thumb"))
       .map((e) => ({
         complete: e.complete,
         natural: e.naturalWidth,
         css: Math.round(e.getBoundingClientRect().width),
       })));
  const loaded = imgs.filter((i) => i.complete && i.natural > 0).length;
  note("every requested thumbnail rendered", imgs.length > 0 && loaded === imgs.length,
       `${loaded}/${imgs.length} <img> decoded`);

  if (imgs.length) {
    const nat = imgs.find((i) => i.natural > 0);
    if (nat) {
      // devicePixelRatio 2 means a 147 CSS px tile wants ~294 real pixels.
      const wanted = nat.css * 2;
      note("thumbnail resolution >= the tile's device pixels",
           nat.natural >= wanted,
           `natural ${nat.natural}px for a ${nat.css} CSS px tile @2x (wants ${wanted})`);
    }
  }

  const bad = report.thumbs.filter((t) => t.status >= 400);
  note("no thumbnail request failed", bad.length === 0,
       bad.length ? `${bad.length} failed, e.g. ${bad[0].status}` : "");

  const times = report.thumbs.filter((t) => t.status === 200).map((t) => t.ms).sort((a, b) => a - b);
  if (times.length) {
    const p50 = times[Math.floor(times.length * 0.5)];
    const p95 = times[Math.floor(times.length * 0.95)];
    report.timing = { n: times.length, p50, p95, max: times[times.length - 1] };
    note("thumbnail p95 under 2 s", p95 < 2000,
         `n=${times.length} p50=${p50}ms p95=${p95}ms max=${times[times.length - 1]}ms`);
  } else {
    note("thumbnail p95 under 2 s", false, "no thumbnail responses at all");
  }

  await page.screenshot({ path: path.join(OUT, "gallery-grid.png"), fullPage: false });

  // FILTERS. The gallery filters through a SEARCH box (target / filter /
  // filename) and a night range, not a dropdown — so exercise the real control
  // and prove the RESULT SET changed, not merely that a control took a click.
  const filterName = args.filter || "Ha";
  const totalBefore = await readShowing(page);
  const search = page.getByPlaceholder(/target, filter, filename/i).first();
  let filtered = false;
  if (await search.count()) {
    await search.fill(filterName);
    // The search is debounced (SEARCH_DEBOUNCE_MS = 300) and then walks the
    // library on the far end; give it the round trip.
    await page.waitForTimeout(8000);
    filtered = true;
  }
  const totalAfter = await readShowing(page);
  note(`search '${filterName}' narrows the library`,
       filtered && totalAfter !== null && totalBefore !== null
         && totalAfter < totalBefore && totalAfter > 0,
       filtered ? `${totalBefore} frames -> ${totalAfter}` : "no search box found");

  // And the narrowed grid must still RENDER — a filter that returns the right
  // count over a wall of broken tiles is not a working filter.
  await page.waitForTimeout(3000);
  const fImgs = await page.locator("img").evaluateAll((els) =>
    els.filter((e) => e.src && e.src.includes("gallery/thumb"))
       .map((e) => ({ ok: e.complete && e.naturalWidth > 0 })));
  const fLoaded = fImgs.filter((i) => i.ok).length;
  note("the filtered grid renders too", fImgs.length > 0 && fLoaded === fImgs.length,
       `${fLoaded}/${fImgs.length} decoded`);
  await page.screenshot({ path: path.join(OUT, "gallery-filtered.png") });

  // Clear it again, so the run leaves the app as it found it.
  const clear = page.getByRole("button", { name: /clear filter/i })
    .filter({ visible: true }).first();
  if (await clear.count()) await clear.click();

  note("no uncaught page errors", report.errors.length === 0,
       report.errors.length ? report.errors[0] : "");
} catch (e) {
  note("run completed without throwing", false, String(e).split("\n")[0]);
  try { await page.screenshot({ path: path.join(OUT, "gallery-crash.png") }); } catch { /* */ }
} finally {
  fs.writeFileSync(path.join(OUT, "report.json"), JSON.stringify(report, null, 2));
  await browser.close();
}

const failed = report.steps.filter((s) => !s.ok);
console.log(`\n${report.steps.length - failed.length}/${report.steps.length} checks passed`);
if (report.timing) console.log("thumb timing:", JSON.stringify(report.timing));
process.exit(failed.length ? 1 : 0);
