// campaignNullQuota.test.tsx - a refused campaign quota is no count, not 0
// (#424; mosaic slice S7).
//
//   Run directly:  node --import ./test-css-stub.mjs --import tsx
//                  src/next/hubs/session/flows/tonight/__tests__/campaignNullQuota.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// Since #362 item 4 `tonight._campaign` answers `quota: null`, and null on
// every member, when the stored POOL quota is a number but no finite count
// above 0 ("inf", 0, a negative), and its `note` says why. The #/next reader
// read that null as `?? 0`, so the card headed its ledger "4 pool members · 0
// cycles each" right above a note saying the quota is no count, and every
// member bar announced a maximum of 0. It is the class `banked` null already
// guards against in the same reader: a missing figure is not a zero.
//
// So the reader keeps the null, the card heads the ledger with the member
// count alone, leaving the reason to the note, and a bar with no quota has no
// maximum to announce. A quota of 45 reads exactly as before.
//
// Every case was run RED under a named mutation in a private copy of ui/, and
// each "Observed" quote is the run's failure line verbatim, wrapped, less the
// harness's leading marker.

/* eslint-disable @typescript-eslint/no-explicit-any */

// ---------------------------------------------------------------- jsdom first
// react-dom decides once, at import, whether it has a DOM.
const { JSDOM } = await import("jsdom");
const dom = new JSDOM(`<!doctype html><html><body><div id="root"></div></body></html>`,
  { url: "http://local/", pretendToBeVisual: true });
const win = dom.window as any;
win.matchMedia = () => ({ matches: false, addEventListener() {}, removeEventListener() {} });
// The model reads the caps table, which imports the store, which reads
// localStorage and opens no socket until asked; both are stubbed as
// tonightDom.test.tsx stubs them.
win.WebSocket = class { close() {} addEventListener() {} send() {} };
const g = globalThis as any;
for (const k of ["window", "document", "navigator", "HTMLElement", "HTMLInputElement", "Element", "Node",
  "Event", "CustomEvent", "MouseEvent", "KeyboardEvent", "localStorage", "getComputedStyle", "matchMedia",
  "WebSocket", "requestAnimationFrame", "cancelAnimationFrame", "location", "history"]) {
  Object.defineProperty(g, k, { value: k === "window" ? win : win[k], writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;

const { createElement: h, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { readCampaign, readTonight, campaignHead } = await import("../tonightModel");
const { TonightCampaignCard } = await import("../TonightCampaignCard");

// ---------------------------------------------------------------- harness
let passed = 0;
let failed = 0;
const failures: string[] = [];
async function test(name: string, fn: () => void | Promise<void>): Promise<void> {
  try { await fn(); passed++; } catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: unknown, msg: string): void { if (!cond) throw new Error(msg); }
function eq(got: unknown, want: unknown, what: string): void {
  const a = JSON.stringify(got);
  const b = JSON.stringify(want);
  if (a !== b) throw new Error(`${what}: expected ${b}, got ${a}`);
}

// ---------------------------------------------------------------- payloads
/** The server's answer for a POOL whose stored quota is "inf", as
 *  server/tests/test_flows_tonight_counts_finite.py pins it: quota null, and
 *  every member's quota, banked and pct null, the reason in `note`. */
const REFUSED = {
  is_campaign: true, has_pool: true, has_ledger: true, quota: null,
  note: "TARGET POOL 'n20' holds 'inf' as its quota, and a count must be a finite number above 0, "
    + "so no member's cycles are counted against it.",
  members: [
    { name: "NGC 7331", banked: null, quota: null, done: false, pct: null },
    { name: "NGC 604", banked: null, quota: null, done: false, pct: null },
  ],
};
/** The same pool with a quota of 45, one member counted and one not. */
const COUNTED = {
  is_campaign: true, has_pool: true, has_ledger: true, quota: 45,
  note: "Shortfall first: the engine re-reads the ledger at each dusk.",
  members: [
    { name: "NGC 7331", banked: 23, quota: 45, done: false, pct: 51 },
    { name: "NGC 604", banked: null, quota: 45, done: false, pct: null },
  ],
};

const container = win.document.getElementById("root");
function render(raw: Record<string, unknown>): { el: any; unmount(): void } {
  const root = createRoot(container);
  act(() => { root.render(h(TonightCampaignCard, { campaign: readCampaign(raw) })); });
  return { el: container, unmount: () => act(() => { root.unmount(); }) };
}
const headText = (el: any): string =>
  String(el.querySelector(".nx-tn-camp-head")?.textContent ?? "").trim();
const bars = (el: any): any[] => [...el.querySelectorAll('[role="progressbar"]')];

// ---------------------------------------------------------------- cases

// MUTANT "quota read as 0" (tonightModel.ts readCampaign: both reads back to
// `num(...) ?? 0`, the code before #424). Observed:
//   x a refused quota is read as null, the campaign's and every member's:
//     the campaign's quota: expected null, got 0
await test("a refused quota is read as null, the campaign's and every member's", () => {
  const c = readCampaign(REFUSED)!;
  eq(c.quota, null, "the campaign's quota");
  eq(c.members.map((m) => m.quota), [null, null], "each member's quota");
  eq(c.members.map((m) => m.banked), [null, null], "each member's banked");
  // A quota the server left out is no count either, not a zero.
  const { quota: _q, ...noQuota } = REFUSED;
  eq(readCampaign(noQuota)!.quota, null, "a campaign with no quota key");
  // And the whole payload's reader hands the same campaign on.
  eq(readTonight({ ok: true, campaign: REFUSED })?.campaign?.quota, null, "readTonight's campaign quota");
});

// MUTANT "quota read as 0" also turns this red. Observed:
//   x a refused quota heads the ledger with the member count alone:
//     campaignHead: expected "2 pool members", got "2 pool members · 0
//     cycles each"
// MUTANT "the head prints the quota regardless" (TonightCampaignCard.tsx: the
// head back to the inline `${n} pool members · ${campaign.quota} cycles each`,
// with the reader's null kept). Observed:
//   x a refused quota heads the ledger with the member count alone: the
//     ledger's head: expected "CAMPAIGN LEDGER2 pool members", got
//     "CAMPAIGN LEDGER2 pool members · null cycles each"
await test("a refused quota heads the ledger with the member count alone", () => {
  eq(campaignHead(readCampaign(REFUSED)!), "2 pool members", "campaignHead");
  const r = render(REFUSED);
  try {
    eq(headText(r.el), "CAMPAIGN LEDGER2 pool members", "the ledger's head");
    assert(!/\bcycles each\b/.test(headText(r.el)), "a refused quota still claims a number of cycles each");
    // The reason is the note's to give, and it is on the card.
    assert(String(r.el.textContent).includes(REFUSED.note), "the note's refusal sentence is not on the card");
  } finally { r.unmount(); }
});

// MUTANT "quota read as 0". Observed:
//   x a member bar with no quota announces no maximum: NGC 7331's bar has
//     aria-valuemax="0", a maximum of 0 for a count nobody could make
// MUTANT "the bar keeps m.quota" (TonightCampaignCard.tsx: aria-valuemax back
// to {m.quota}, with the reader's null kept) never reaches this case: tsc
// refuses it. Observed (`tsc -p tsconfig.json`, private copy):
//   TonightCampaignCard.tsx(60,8): error TS2322: ... Types of property
//   '"aria-valuemax"' are incompatible. Type 'number | null' is not
//   assignable to type 'number | undefined'.
await test("a member bar with no quota announces no maximum", () => {
  const r = render(REFUSED);
  try {
    const bs = bars(r.el);
    eq(bs.length, 2, "progress bars");
    for (const b of bs) {
      const who = String(b.getAttribute("aria-label")).replace(/ campaign progress$/, "");
      const max = b.getAttribute("aria-valuemax");
      assert(max === null, `${who}'s bar has aria-valuemax="${max}", a maximum of 0 for a count nobody could make`);
      eq(b.getAttribute("aria-valuenow"), null, `${who}'s aria-valuenow`);
      eq(b.getAttribute("aria-valuetext"), "not counted", `${who}'s aria-valuetext`);
    }
    // The rows say "not counted", as a null banked always did.
    for (const name of ["NGC 7331", "NGC 604"]) {
      const row = r.el.querySelector(`[data-testid="tonight-member-${name}"]`);
      eq(String(row?.querySelector(".nx-tn-member-status")?.textContent), "not counted", `${name}'s status`);
    }
  } finally { r.unmount(); }
});

// A count with no quota. The server never sends one (it nulls `banked` with
// the quota), but the card's reader would pass it on, and the row must then
// give the count alone: there is no "of N" to print, and "7/0" claims one.
// MUTANT "a count with no quota reads of 0" (TonightCampaignCard.tsx statusOf:
// every member handed to `memberStatus` with `quota: m.quota ?? 0`).
// Observed:
//   x a count with no quota says the count alone and announces no maximum:
//     NGC 7331's aria-valuetext: expected "7 cycles", got "7/0 cycles"
// MUTANT "quota read as 0" also turns this red, on the same line.
// Since the S7 integration there is no `statusOf`: the card calls the shared
// TonightCampaign.tsx `memberStatus`, which takes the null quota itself for
// this card and the classic panel. The same mutant made there (memberStatus's
// null-quota line removed and the count printed against `m.quota ?? 0`),
// in the integration's private copy (the session scratchpad's
// S7-INTEG-r2-mut, from a byte backup, sha256 checked after), 4/5, the same
// line:
//   x a count with no quota says the count alone and announces no maximum:
//     NGC 7331's aria-valuetext: expected "7 cycles", got "7/0 cycles"
await test("a count with no quota says the count alone and announces no maximum", () => {
  const r = render({ ...REFUSED, members: [
    { name: "NGC 7331", banked: 7, quota: null, done: false, pct: null },
    { name: "NGC 604", banked: 9, quota: null, done: true, pct: null },
  ] });
  try {
    const bs = bars(r.el);
    eq(bs.length, 2, "progress bars");
    eq(bs[0].getAttribute("aria-valuetext"), "7 cycles", "NGC 7331's aria-valuetext");
    eq(bs[1].getAttribute("aria-valuetext"), "9 cycles · DONE", "NGC 604's aria-valuetext");
    eq(bs.map((b) => b.getAttribute("aria-valuemax")), [null, null], "each bar's aria-valuemax");
    eq(bs.map((b) => b.getAttribute("aria-valuenow")), ["7", "9"], "each bar's aria-valuenow");
    const status = (name: string): string => String(r.el.querySelector(
      `[data-testid="tonight-member-${name}"] .nx-tn-member-status`)?.textContent);
    eq(status("NGC 7331"), "7 cycles", "NGC 7331's status");
    eq(status("NGC 604"), "9 cycles · DONE", "NGC 604's status");
  } finally { r.unmount(); }
});

// CONTROL: a quota of 45 reads exactly as it did before #424. It stayed green
// under every mutant above (with the count-with-no-quota case added, 'quota
// read as 0' passed 1/5, 'the head prints the quota regardless' 4/5 and 'a
// count with no quota reads of 0' 4/5, this case among them each time),
// which is the point: only the null moved.
await test("control: a quota of 45 heads the ledger and every bar with 45", () => {
  const c = readCampaign(COUNTED)!;
  eq(c.quota, 45, "the campaign's quota");
  eq(c.members.map((m) => m.quota), [45, 45], "each member's quota");
  eq(campaignHead(c), "2 pool members · 45 cycles each", "campaignHead");
  const r = render(COUNTED);
  try {
    eq(headText(r.el), "CAMPAIGN LEDGER2 pool members · 45 cycles each", "the ledger's head");
    eq(bars(r.el).map((b) => b.getAttribute("aria-valuemax")), ["45", "45"], "each bar's aria-valuemax");
    eq(bars(r.el).map((b) => b.getAttribute("aria-valuetext")), ["23/45 cycles", "not counted"],
      "each bar's aria-valuetext");
    eq(bars(r.el).map((b) => b.getAttribute("aria-valuenow")), ["23", null], "each bar's aria-valuenow");
  } finally { r.unmount(); }
  // No members is still its own sentence, with or without a quota.
  eq(campaignHead(readCampaign({ ...COUNTED, members: [] })!), "no pool members", "an empty pool");
  eq(campaignHead(readCampaign({ ...REFUSED, members: [] })!), "no pool members", "an empty pool, refused");
});

// ---------------------------------------------------------------- report
const total = passed + failed;
// eslint-disable-next-line no-console
console.log(`\ncampaignNullQuota.test: ${passed}/${total} passed`);
if (failures.length) {
  // eslint-disable-next-line no-console
  console.error(failures.join("\n"));
}
export const result = { passed, failed, total };
