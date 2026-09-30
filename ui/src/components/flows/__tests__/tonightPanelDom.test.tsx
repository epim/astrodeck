// tonightPanelDom.test.tsx — the Tonight overlay, MOUNTED.
//
//   Run directly:  npx tsx src/components/flows/__tests__/tonightPanelDom.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// Three promises are pinned here and they fail in three different ways.
//
// 1. THE HARNESS CONTRACT. `scripts/flows_visual_check.py` reaches states 04,
//    05 and 06 by clicking `get_by_role("tab", …).or_(get_by_role("button", …))`
//    named TIMELINE / STORY / PLAN and then waiting on
//    `[data-flows-tonight='{tab}']` becoming VISIBLE. The house
//    `SegmentedControl` emits `role="radio"`, which matches NEITHER of those
//    two roles, so a panel built on it renders perfectly and can never be
//    captured. Nothing in a screenshot review would say why.
//
// 2. THE REFUSAL IS THE PRODUCT. `/tonight` answers `{ok:false, reason:"…"}`
//    for a rig with no site or a sun that never sets, and tonight.py returns a
//    whole sentence explaining what to fix. Folding that into a generic error
//    state is the specific defect this panel exists to avoid, and a payload
//    with `ok:false` renders identically to a healthy one if the reason is
//    dropped — every field it would have drawn is null anyway.
//
// 3. IT PORTALS. `.panel { position: relative }` and `backdrop-filter` are
//    unlayered authored CSS that beat a Tailwind `fixed`, which is how four
//    reviewers found four dialogs off the bottom of the viewport. Overlay's
//    body-level host is the fix; a surface rendered inline in the view tree
//    measures fine in a DOM dump and lands somewhere nobody can reach.

/* eslint-disable @typescript-eslint/no-explicit-any */

// ---------------------------------------------------------------- jsdom first
const { JSDOM } = await import("jsdom");
const dom = new JSDOM(
  `<!doctype html><html><body><div id="root"></div></body></html>`,
  { url: "http://local/", pretendToBeVisual: true },
);
const win = dom.window as any;

// Overlay's useMediaQuery calls matchMedia during render; without it the import
// takes the file down before an assertion runs.
win.matchMedia = () => ({
  matches: true, addEventListener() {}, removeEventListener() {},
  addListener() {}, removeListener() {},
});

const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "Element", "Node", "Event",
  "CustomEvent", "MouseEvent", "localStorage", "getComputedStyle", "matchMedia",
  "WebSocket",
]) {
  const v = k === "window" ? win : win[k];
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.requestAnimationFrame = (cb: (t: number) => void) => setTimeout(() => cb(0), 0);
g.IS_REACT_ACT_ENVIRONMENT = true;

// ------------------------------------------------------------------- imports
import React from "react";
import { createRoot } from "react-dom/client";
import { act } from "react";

let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
const assert = {
  ok(cond: unknown, msg?: string) { if (!cond) throw new Error(msg ?? "not ok"); },
  equal(a: unknown, b: unknown, msg?: string) {
    if (a !== b) throw new Error(msg ?? `${String(a)} !== ${String(b)}`);
  },
};

const { useStore } = await import("../../../store");
const { FLOWS_INIT } = await import("../flowsSlice");
const TonightPanel = (await import("../TonightPanel")).default;

// ------------------------------------------------------------------- fixture
const DUSK = 1_700_000_000;
const H = 3600;

/** A resolved night with one placed target — the M16 shape of capture 04. */
const OK_PAYLOAD: Record<string, unknown> = {
  ok: true,
  reason: "",
  night: {
    dusk_unix: DUSK, dawn_unix: DUSK + 8 * H,
    dark_start_unix: DUSK + 40 * 60, dark_end_unix: DUSK + 8 * H - 40 * 60,
    window_start_unix: DUSK - 30 * 60, window_stop_unix: DUSK + 8 * H,
  },
  flats: { start_unix: DUSK + 600, end_unix: DUSK + 1500 },
  moon: { illumination: 0.71, rise_unix: DUSK + 3 * H, set_unix: null },
  targets: [{
    label: "M16", name: "M16 — Eagle", resolved: true,
    window: { start_unix: DUSK + H, end_unix: DUSK + 6 * H },
    curve: [[DUSK + H, 32], [DUSK + 3 * H, 71], [DUSK + 6 * H, 33]],
    meridian_flip_unix: DUSK + 4 * H,
  }],
  budget: [],
  story: [
    { t_unix: DUSK, label: "", msg: "Autorun window opens", tone: "text" },
    { t_unix: null, label: "ANY", msg: "IF unsafe → abort, park, warm, close", tone: "bad" },
  ],
  brief: "This flow arms at astronomical dusk (−30 min). It captures Ha 180 s × 20.",
  campaign: {
    is_campaign: true, has_pool: true, has_ledger: true, quota: 45,
    members: [
      { name: "M33", banked: 45, quota: 45, done: true, pct: 100 },
      { name: "NGC 7331", banked: 23, quota: 45, done: false, pct: 51 },
      { name: "M45", banked: 0, quota: 45, done: false, pct: 0 },
    ],
    note: "135 cycles left across the pool. Nights to finish are not forecast.",
  },
};

const NO_SITE_REASON =
  "No observatory site is set, so there is no night to resolve - nothing "
  + "below would be about where you are.";

/** What tonight.py's `_cannot` actually returns: every time-bearing key null,
 *  the reason carried both at top level and as the single story row. */
const REFUSAL_PAYLOAD: Record<string, unknown> = {
  ok: false, reason: NO_SITE_REASON, now_unix: DUSK, twilight_deg: -12,
  night: null, flats: null, moon: null, targets: [], budget: [],
  story: [{ t_unix: null, label: "—", msg: NO_SITE_REASON, tone: "warn" }],
};

let fetches = 0;
const root = createRoot(win.document.getElementById("root"));

/** Seed the store and mount. `flowsFetchTonight` is replaced by a counter: the
 *  real one reaches the network, and this file is about what renders. */
function render(flows: Record<string, unknown>): void {
  act(() => root.render(null));
  act(() => {
    useStore.setState({
      flows: {
        ...FLOWS_INIT,
        record: {
          id: "example-m16", name: "M16 — full-service night", folder: "Examples",
          tagline: "", graph: { nodes: [], edges: [] }, created_ts: 0,
          updated_ts: 0, last_run: null, last_result: "", readonly: true,
        },
        ui: { ...FLOWS_INIT.ui, tonightOpen: true },
        ...flows,
      },
      flowsFetchTonight: async () => { fetches++; },
    } as any);
  });
  act(() => root.render(React.createElement(TonightPanel)));
}

/** The overlay renders through a body-level portal, so nothing below queries
 *  the mount point — that is the point of assertion 3. */
const q = (sel: string): any => win.document.querySelector(sel);
const qa = (sel: string): any[] =>
  Array.from(win.document.querySelectorAll(sel));

function clickTab(label: string): void {
  const el = qa("[role=tab]").find((t) => t.textContent.trim() === label);
  if (!el) throw new Error(`no role=tab named ${label}`);
  act(() => { el.dispatchEvent(new win.MouseEvent("click", { bubbles: true })); });
}

// ─────────────────────────────────────────────────── 1. the harness contract

test("the four pills are role=tab with the harness's exact names", () => {
  // CAMPAIGN is the 2026-08-14 export's fourth tab and it goes LAST, because it
  // is the only one of the four that describes something other than tonight.
  // The order is pinned rather than the count: the parity harness clicks these
  // by their visible text, so a reorder silently re-points every capture step.
  render({ tonight: OK_PAYLOAD });
  const names = qa("[role=tab]").map((t) => t.textContent.trim());
  assert.equal(names.join("|"), "TIMELINE|STORY|PLAN|CAMPAIGN",
    `pills verbatim and in order, got ${names.join("|")}`);
  // A role=radio (what SegmentedControl emits) matches neither of the two roles
  // the harness tries, and the capture step would fail with no useful message.
  assert.equal(qa("[role=radio]").length, 0, "no radio roles in this panel");
  assert.ok(q("[role=tablist]"), "the tabs live in a tablist");
});

test("the body marker names the open tab and moves with it", () => {
  render({ tonight: OK_PAYLOAD, compiled: { plan: { name: "M16" }, structural: [], issues: [], unmapped: [] } });
  assert.ok(q("[data-flows-tonight='timeline']"), "opens on TIMELINE (states 04)");

  clickTab("STORY");
  assert.ok(q("[data-flows-tonight='story']"), "state 05");
  assert.ok(!q("[data-flows-tonight='timeline']"), "and only one marker at a time");

  clickTab("PLAN");
  assert.ok(q("[data-flows-tonight='plan']"), "state 06");
});

test("the marked element has content, so the harness's visibility wait passes", () => {
  render({ tonight: OK_PAYLOAD });
  const body = q("[data-flows-tonight='timeline']");
  // A zero-child marker is `state="visible"` false in Playwright, and the
  // capture times out 15 s later saying nothing about why.
  assert.ok(body.childElementCount > 0, "the timeline drew something");
  assert.ok(body.querySelector("svg"), "…an svg");
  // README §7: text inside the SVG measures fine and paints nothing, which is
  // exactly what harness gate 4 (blank-PNG detection) exists to catch.
  assert.equal(body.querySelectorAll("svg text").length, 0,
    "no <text> inside the drawing — labels live in the HTML layer");
  assert.ok(body.textContent.includes("DUSK") && body.textContent.includes("DAWN"),
    "and those labels are real DOM text");
});

// ────────────────────────────────────────────── 2. the refusal is the product

test("ok:false renders the server's reason as prose on TIMELINE", () => {
  render({ tonight: REFUSAL_PAYLOAD });
  const body = q("[data-flows-tonight='timeline']");
  assert.ok(body.textContent.includes(NO_SITE_REASON),
    `the whole sentence, not an error chip: got ${body.textContent.slice(0, 120)}`);
  assert.ok(!body.querySelector("svg"),
    "and no axis drawn for a night that was never resolved");
});

test("ok:false reaches STORY through the server's own row", () => {
  render({ tonight: REFUSAL_PAYLOAD });
  clickTab("STORY");
  const body = q("[data-flows-tonight='story']");
  assert.ok(body.textContent.includes(NO_SITE_REASON), "the reason row is rendered");
  assert.ok(body.textContent.includes("—"), "with the server's own '—' label");
});

test("a transport failure is NOT the same statement as a refusal", () => {
  // "the request did not arrive" and "there is no night here" are different
  // facts, and showing the first as the second would send the operator to fix
  // a site that is already set.
  render({ tonight: null, tonightError: "Failed to fetch" });
  const body = q("[data-flows-tonight='timeline']");
  assert.ok(body.textContent.includes("Failed to fetch"), "the transport error is quoted");
  assert.ok(!body.textContent.includes("No observatory site"), "and not editorialised");
});

test("nothing is drawn for a field the server did not return", () => {
  // A night with no moon block and no flats: the prototype drew a moon bar from
  // a fixture minute to the edge and a FLATS block regardless.
  render({
    tonight: {
      ...OK_PAYLOAD,
      moon: null,
      flats: { start_unix: null, end_unix: null },
      targets: [],
    },
  });
  const body = q("[data-flows-tonight='timeline']");
  assert.ok(!body.textContent.includes("FLATS"), "no flats block");
  assert.ok(!/moon up \(\d+%\)/.test(body.textContent),
    "the legend states no illumination it was not given");
  assert.ok(body.textContent.includes("moon up"), "…while the key still names the band");
});

// ─────────────────────────────────────────────────────── 3. it portals, etc.

test("the surface renders through the body-level overlay host", () => {
  render({ tonight: OK_PAYLOAD });
  const host = win.document.getElementById("ad-overlay-root");
  assert.ok(host, "Overlay's host exists");
  assert.ok(host.querySelector("[data-flows-tonight]"),
    "and the panel is inside it, not in the view tree");
  assert.ok(!win.document.getElementById("root").querySelector("[data-flows-tonight]"),
    "nothing of it is left behind under #root");
});

test("closed renders nothing at all", () => {
  render({ tonight: OK_PAYLOAD, ui: { ...FLOWS_INIT.ui, tonightOpen: false } });
  assert.ok(!q("[data-flows-tonight]"), "no marker for a panel nobody opened");
});

test("opening re-resolves the night rather than reusing a stale answer", () => {
  // The payload is an answer about one instant. A panel reopened two hours
  // later would otherwise draw a window that has already closed.
  fetches = 0;
  render({ tonight: OK_PAYLOAD });
  assert.equal(fetches, 1, "one fetch on open");
});

test("PLAN shows the compiled plan alone, and says so when there is none", () => {
  render({ tonight: OK_PAYLOAD, compiled: null });
  clickTab("PLAN");
  const body = q("[data-flows-tonight='plan']");
  assert.ok(!body.querySelector("pre"), "no JSON block without a compile");
  assert.ok(!body.querySelector("button"), "and nothing to press that would copy nothing");

  render({
    tonight: null,
    compiled: { plan: { name: "M16" }, structural: [], issues: [], unmapped: ["x"] },
    ui: { ...FLOWS_INIT.ui, tonightOpen: true, tonightTab: "plan" },
  });
  const pre = q("[data-flows-tonight='plan'] pre");
  assert.ok(pre, "the plan renders with no /tonight payload at all — it is a different endpoint");
  assert.ok(pre.textContent.includes("\"name\": \"M16\""), "pretty-printed plan");
  assert.ok(!pre.textContent.includes("unmapped"),
    "payload.plan ONLY — the doctor's lists are not part of what the engine runs");
});

// ───────────────────────────────────────── 4. the campaign tab and the brief

test("CAMPAIGN draws a row per pool member, with its own status word", () => {
  render({ tonight: OK_PAYLOAD });
  clickTab("CAMPAIGN");
  const body = q("[data-flows-tonight='campaign']");
  assert.ok(body, "the fourth tab has a marker like the other three");
  assert.ok(body.childElementCount > 0, "…and drew something into it");

  const rows = qa("[data-campaign-member]");
  assert.equal(rows.length, 3, "one row per member");
  const text = body.textContent;
  assert.ok(text.includes("45/45 cycles · DONE"), `a finished member says DONE: ${text}`);
  assert.ok(text.includes("23/45 cycles"), "a partial member shows its count");
  assert.ok(text.includes("0/45 cycles"), "and a member with nothing banked shows a real zero");
});

test("a null banked figure is NOT drawn as zero", () => {
  // "0 of 45 banked" says the rig looked and found nothing; "no ledger" says
  // nobody looked. Only one of those should make an operator re-plan a month,
  // and an empty bar at 0% would tell them the wrong one.
  render({
    tonight: {
      ...OK_PAYLOAD,
      campaign: {
        is_campaign: true, has_pool: true, has_ledger: false, quota: 45,
        members: [{ name: "M33", banked: null, quota: 45, done: false, pct: null }],
        note: "No session ledger available, so nothing here claims a banked figure.",
      },
    },
    ui: { ...FLOWS_INIT.ui, tonightOpen: true, tonightTab: "campaign" },
  });
  const body = q("[data-flows-tonight='campaign']");
  assert.ok(body.textContent.includes("not counted"), body.textContent);
  assert.ok(!body.textContent.includes("0/45"), "a missing figure was rendered as zero");
  const bar = body.querySelector("[role=progressbar]");
  assert.ok(bar, "the bar is still drawn");
  assert.equal(bar.getAttribute("aria-valuenow"), null,
    "…with no value, because there is no value");
  assert.equal(bar.childElementCount, 0, "and no fill");
});

// A REFUSED QUOTA IS NO COUNT, NOT 0 (#424, the classic half, finished by the
// S7 integration). `tonight._campaign` answers `quota: null`, the campaign's
// and every member's, when the stored POOL quota is no finite count above 0
// ("inf", 0, a negative), as server/tests/test_flows_tonight_counts_finite.py
// pins it. This panel's reader read that null as `?? 0`, so every bar
// announced a maximum of 0 for a count nobody could make.
//
// Each mutant below was run in the integration's private copy of ui/ (the
// session scratchpad's S7-INTEG-r2-mut, from a byte backup, sha256 checked
// after), and each failure is quoted as the run printed it, wrapped.
// MUTANT "quota read as 0" (TonightPanel.tsx readCampaign: both quota reads
// back to `num(...) ?? 0`, the code before #424). Observed, 17/18:
//   x a refused quota gives no bar a maximum, and a count with no quota says
//   the count alone: NGC 7331 campaign progress announces aria-valuemax="0"
// MUTANT "a count with no quota reads of 0" (TonightCampaign.tsx
// memberStatus: its null-quota line removed and the count printed against
// `m.quota ?? 0`). Observed, 17/18 (and campaignNullQuota.test.tsx 4/5):
//   x a refused quota gives no bar a maximum, and a count with no quota says
//   the count alone: M45's aria-valuetext: expected "7 cycles", got
//   "7/0 cycles"
// MUTANT "the bar keeps m.quota" (TonightCampaign.tsx: aria-valuemax back to
// {m.quota}) never reaches a case: tsc refuses it. Observed (`tsc -p
// tsconfig.json --noEmit`):
//   src/components/flows/TonightCampaign.tsx(82,8): error TS2322: ...
//   "aria-valuemax": number | null; ... is not assignable to type
//   'DetailedHTMLProps<HTMLAttributes<HTMLDivElement>, HTMLDivElement>'.
// The control below stayed green under all three.
const REFUSED_CAMPAIGN = {
  is_campaign: true, has_pool: true, has_ledger: true, quota: null,
  members: [
    { name: "NGC 7331", banked: null, quota: null, done: false, pct: null },
    { name: "M45", banked: 7, quota: null, done: false, pct: null },
  ],
  note: "TARGET POOL 'n20' holds 'inf' as its quota, and a count must be a finite number above 0, "
    + "so no member's cycles are counted against it.",
};

test("a refused quota gives no bar a maximum, and a count with no quota says the count alone", () => {
  render({
    tonight: { ...OK_PAYLOAD, campaign: REFUSED_CAMPAIGN },
    ui: { ...FLOWS_INIT.ui, tonightOpen: true, tonightTab: "campaign" },
  });
  const body = q("[data-flows-tonight='campaign']");
  assert.ok(body, "the campaign tab did not render");
  const bars = [...body.querySelectorAll("[role=progressbar]")] as any[];
  assert.equal(bars.length, 2, "one bar per member");
  for (const b of bars) {
    assert.equal(b.getAttribute("aria-valuemax"), null,
      `${b.getAttribute("aria-label")} announces aria-valuemax="${b.getAttribute("aria-valuemax")}"`);
  }
  // Each message carries what was read: this harness prints the message alone.
  const said = bars.map((b) => b.getAttribute("aria-valuetext"));
  assert.equal(said[0], "not counted", `NGC 7331's aria-valuetext: expected "not counted", got "${said[0]}"`);
  assert.equal(said[1], "7 cycles", `M45's aria-valuetext: expected "7 cycles", got "${said[1]}"`);
  assert.ok(!/\/0|\/null/.test(body.textContent),
    `a count against no quota printed an "of N": ${body.textContent}`);
  assert.ok(body.textContent.includes(REFUSED_CAMPAIGN.note), "the note's refusal sentence is not on the tab");
});

test("control: a quota of 45 still gives every bar a maximum of 45", () => {
  render({ tonight: OK_PAYLOAD, ui: { ...FLOWS_INIT.ui, tonightOpen: true, tonightTab: "campaign" } });
  const bars = qa("[data-flows-tonight='campaign'] [role=progressbar]") as any[];
  assert.equal(bars.map((b) => b.getAttribute("aria-valuemax")).join(","), "45,45,45",
    "each bar's aria-valuemax");
});

test("CAMPAIGN survives a refusal, because it does not depend on the ephemeris", () => {
  // `_campaign` reads the GRAPH. A tab that blanked when the SITE is unset
  // would look like a broken campaign rather than a missing site.
  render({
    tonight: {
      ...REFUSAL_PAYLOAD,
      campaign: {
        is_campaign: false, has_pool: true, has_ledger: false, quota: 45,
        members: [{ name: "M33", banked: null, quota: 45, done: false, pct: null }],
        note: "Single-night flow - set DUSK WINDOW → Repeat to make this a campaign.",
      },
    },
    ui: { ...FLOWS_INIT.ui, tonightOpen: true, tonightTab: "campaign" },
  });
  const body = q("[data-flows-tonight='campaign']");
  assert.ok(body.textContent.includes("set DUSK WINDOW"), body.textContent);
});

test("STORY leads with the generated brief", () => {
  render({ tonight: OK_PAYLOAD });
  clickTab("STORY");
  const brief = q("[data-tonight-brief]");
  assert.ok(brief, "the brief block is rendered");
  assert.ok(brief.textContent.includes("BRIEF - GENERATED FROM THE GRAPH"),
    "with the export's heading, verbatim");
  assert.ok(brief.textContent.includes("Ha 180 s × 20"),
    "and the server's prose, not a second generator's");

  // It leads: the brief must come BEFORE the first timed row in document order.
  const body = q("[data-flows-tonight='story']");
  const pos = body.textContent.indexOf("BRIEF");
  assert.ok(pos >= 0 && pos < body.textContent.indexOf("Autorun window opens"),
    "the brief opens the tab");
});

test("no brief means no empty bordered box", () => {
  // A plan dict has no graph, so the server sends "". An empty box with a
  // heading and nothing under it reads as a failed load.
  render({
    tonight: { ...OK_PAYLOAD, brief: "" },
    ui: { ...FLOWS_INIT.ui, tonightOpen: true, tonightTab: "story" },
  });
  assert.ok(!q("[data-tonight-brief]"), "nothing drawn for an absent brief");
});

// ───────────────────────────────── 5. BUDGET says whose hours it banks (#536)
//
// `GET /api/flows/{id}/tonight` folds only this flow's own targets' reports
// into BUDGET's banked hours (#536, H4 orchestrator ruling 6); it used to fold
// the whole archive, so an M31 flow's Ha bar held M16's nights. The server's
// BUDGET row says so, "1 h banked for these targets", and STORY is where this
// panel draws it.
//
// THE SERVER'S WORDS, READ, NOT COPIED: the answer is
// `tonight_budget_for_these_targets.json` in the #/next sheet's __tests__,
// which server/tests/test_h4_budget_for_these_targets.py keeps equal to
// `resolve_tonight`'s answer for an M31 flow with M31's and M16's reports in
// the ledger; budgetForTheseTargets.test.tsx mounts the same file on #/next.
//
// MUTANT "the words dropped" (tonight.py `_story`: `_FOR_THESE` left out of
// both BUDGET sentences, the fixture rewritten by the server test under the
// mutant), run in the private copy scratchpad H4-ROUTES-A-mut from a byte
// backup. Observed:
//   tonightPanelDom.test: 19/20 passed
//     x every BUDGET row on STORY says its banked hours are for these targets: a
//       BUDGET row on the classic panel does not say whose hours it banks: "Ha: 1
//       h banked / 2 h goal — tonight adds ≈2 h; the session ledger resumes the
//       remainder next clear night"
// The control before it stays green under that mutant. It can fail: MUTANT
// "BUDGET rows not drawn" (TonightStory.tsx mapping
// `story.filter((r) => r.label !== "BUDGET")`), same copy. Observed:
//   tonightPanelDom.test: 18/20 passed
//     x control: STORY draws the server's two BUDGET rows, stamped BUDGET,
//       numbers and all: STORY drew 0 BUDGET rows, not the server's 2
const { readFileSync } = await import("node:fs");
const BUDGET_FIXTURE =
  "../../../next/hubs/session/flows/tonight/__tests__/tonight_budget_for_these_targets.json";

/** The server's answer; unreadable FAILS the file rather than skipping it. */
function readBudgetAnswer(): Record<string, unknown> {
  let text: string;
  try {
    text = readFileSync(new URL(BUDGET_FIXTURE, import.meta.url), "utf8") as string;
  } catch (e) {
    throw new Error(`cannot read ${BUDGET_FIXTURE}: ${(e as Error).message}`);
  }
  const response = (JSON.parse(text) as { response?: Record<string, unknown> }).response;
  if (!response || !Array.isArray(response.story) || response.story.length === 0) {
    throw new Error(`${BUDGET_FIXTURE} holds no BUDGET story rows`);
  }
  return response;
}
const BUDGET_ANSWER = readBudgetAnswer();
const BUDGET_ROWS = (BUDGET_ANSWER.story as { label: string; msg: string }[])
  .filter((r) => r.label === "BUDGET");

/** STORY's drawn rows as `[stamp, sentence]`, each row's two spans. */
function storyRows(): [string, string][] {
  const body = q("[data-flows-tonight='story']");
  if (!body) throw new Error("STORY never rendered");
  return (Array.from(body.querySelectorAll("span")) as any[])
    .filter((s) => s.nextElementSibling && s.parentElement?.children.length === 2
      && s === s.parentElement.children[0])
    .map((s) => [s.textContent.trim(), s.nextElementSibling.textContent]);
}

test("control: STORY draws the server's two BUDGET rows, stamped BUDGET, numbers and all", () => {
  render({
    tonight: { ...OK_PAYLOAD, ...BUDGET_ANSWER },
    ui: { ...FLOWS_INIT.ui, tonightOpen: true, tonightTab: "story" },
  });
  assert.equal(BUDGET_ROWS.length, 2, "precondition: the answer holds two BUDGET rows");
  const budget = storyRows().filter(([stamp]) => stamp === "BUDGET");
  assert.equal(budget.length, 2, `STORY drew ${budget.length} BUDGET rows, not the server's 2`);
  assert.ok(budget[0][1].startsWith("Ha: 1 h banked"), `the capture row's figure: "${budget[0][1]}"`);
  assert.ok(budget[1][1].includes("0.2 h banked in its filters"), `the cycle row's figure: "${budget[1][1]}"`);
});

test("every BUDGET row on STORY says its banked hours are for these targets", () => {
  render({
    tonight: { ...OK_PAYLOAD, ...BUDGET_ANSWER },
    ui: { ...FLOWS_INIT.ui, tonightOpen: true, tonightTab: "story" },
  });
  const budget = storyRows().filter(([stamp]) => stamp === "BUDGET");
  for (const [, sentence] of budget) {
    assert.ok(/ h banked (in its filters )?for these targets/.test(sentence),
      `a BUDGET row on the classic panel does not say whose hours it banks: "${sentence}"`);
  }
  for (const row of BUDGET_ROWS) {
    assert.ok(budget.some(([, sentence]) => sentence === row.msg),
      `the server's row is not drawn as the server wrote it: "${row.msg}"`);
  }
});

// ------------------------------------------------------------------- report
act(() => { root.unmount(); });
const total = passed + failed;
// eslint-disable-next-line no-console
console.log(`tonightPanelDom.test: ${passed}/${total} passed`);
// eslint-disable-next-line no-console
for (const f of failures) console.log("  " + f);
export default { passed, failed, total };
