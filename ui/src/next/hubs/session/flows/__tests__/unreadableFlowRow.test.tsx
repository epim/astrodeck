// unreadableFlowRow.test.tsx - SESSION / FLOWS draws a flow this build cannot
// open as a row that says why ONCE, and never opens, runs, targets or claims
// it (#153; spec 2026-09-23 section 3.6; mosaic slice S1-10, carry-overs 6
// and 7).
//
//   Run:  node --import ./test-css-stub.mjs --import tsx src/next/hubs/session/flows/__tests__/unreadableFlowRow.test.tsx   (from ui/)
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// WHAT IS WORTH GUARDING HERE
//
// `GET /api/flows` lists a file the store could not read as a row carrying
// `unreadable` (server `FlowStore._row`). Every other route answers 404 for its
// id, so nothing on this screen can open it or run it.
//
//   1. SAID ONCE (carry-over 6). The reason is the row's meta line and nothing
//      else on the row repeats it. The S0 row said it three times - the meta
//      line, the body's `title`, and a toast on every press - and put a locked
//      RUN beside it carrying it a fourth time. So: the body is aria-disabled
//      and `aria-describedby` the meta line, with no title; a press raises no
//      toast; and there is no RUN or RESUME at all, because a locked verb on a
//      row nothing can run is a control that exists only to be refused. That
//      one line wraps rather than ellipsising, since it is now the only copy.
//   2. THE ROW BODY DOES NOTHING: no toast, no `GET /api/flows/<id>`, no
//      navigation to a stage list or a canvas that could only 404.
//   3. OPEN THE FLOWS CANVAS NEVER TARGETS ONE (carry-over 7a), including when
//      `flows.record.id` - which outlives a library reload - is that row's id.
//   4. NEVER LIVE, NEVER THE CAMPAIGN (carry-over 7b). Both matches are loose:
//      LIVE by the running plan's NAME, the campaign by an id resolved from an
//      armed resume, whose ledger the campaign hook may still hold.
//   5. CONTROLS: a readable row keeps RUN, LIVE and RESUME exactly as before.
//
// Every case names the mutant it kills and quotes the failure that mutant
// produced when it was run from a byte-for-byte backup of the file it names.
//
// THE TOAST AND TITLE ASSERTIONS WERE REWRITTEN ON PURPOSE. Until S1-10 this
// file asserted `title === reason` on the body and on RUN, and a toast on both
// presses. Those were the repetitions carry-over 6 removes; they are now
// asserted ABSENT, and the one place the reason lives is asserted present.
//
// Convention: shell-and-tests.md section 4, harness shape from flowsDom.test.tsx.

/* eslint-disable @typescript-eslint/no-explicit-any */

// ------------------------------------------------------------------ css stub
// Same reason as flowsDom.test.tsx: the rebuilt areas import their own `.css`,
// which Node cannot load. run-tests.mjs also preloads test-css-stub.mjs.
{
  const { registerHooks } = await import("node:module");
  if (typeof registerHooks === "function") {
    registerHooks({
      load(url: string, context: any, nextLoad: any) {
        if (url.endsWith(".css")) {
          return { format: "module", shortCircuit: true, source: "export default {};" };
        }
        return nextLoad(url, context);
      },
    } as any);
  }
}

// ---------------------------------------------------------------- jsdom first
const { JSDOM } = await import("jsdom");
const dom = new JSDOM(
  `<!doctype html><html><body><div id="root"></div></body></html>`,
  { url: "http://local/", pretendToBeVisual: true },
);
const win = dom.window as any;

let viewportW = 390;
win.matchMedia = (q: string) => {
  const m = /min-width:\s*(\d+)px/.exec(q);
  return {
    matches: m ? viewportW >= Number(m[1]) : false,
    addEventListener() {}, removeEventListener() {},
    addListener() {}, removeListener() {},
  };
};
win.WebSocket = class { close() {} addEventListener() {} send() {} };
win.Element.prototype.setPointerCapture = function () { /* jsdom has none */ };
win.Element.prototype.releasePointerCapture = function () { /* jsdom has none */ };

const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "HTMLInputElement",
  "Element", "Node", "Event", "CustomEvent", "MouseEvent", "KeyboardEvent",
  "PointerEvent", "FocusEvent",
  "localStorage", "getComputedStyle", "matchMedia", "WebSocket",
  "requestAnimationFrame", "cancelAnimationFrame", "location", "history",
]) {
  const v = k === "window" ? win : win[k];
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;

// ------------------------------------------------------------- the fake rig
const NEWER = "saved by a newer AstroDeck (schema 4); update to open it";

const GOOD = {
  id: "quick-m31", name: "Quick M31 LRGB", folder: "My flows",
  tagline: "12 subs each of L, R, G, B", readonly: false,
  stages: 7, wires: 6, last_run: 1_757_000_000, last_result: "ok",
  updated_ts: 1_757_000_500,
};
/** Server `FlowStore._row` for a file a newer build wrote: the card shape,
 *  read-only, no tagline, no run, plus the reason. */
const FUTURE = {
  id: "future-mosaic", name: "Mosaic NGC 7000", folder: "My flows", tagline: "",
  readonly: true, stages: 9, wires: 8, last_run: null, last_result: "",
  updated_ts: 1_758_000_000, unreadable: NEWER,
};
/** A second readable flow, listed AFTER the unreadable one, for the case in
 *  which "the open record" and "the first readable row" are different rows. */
const SECOND = {
  id: "dusk-flats", name: "Dusk flats", folder: "My flows", tagline: "five filters",
  readonly: false, stages: 4, wires: 3, last_run: null, last_result: "",
  updated_ts: 1_756_000_000,
};
const DEFAULT_CARDS: any[] = [GOOD, FUTURE];
let CARDS: any[] = DEFAULT_CARDS;

/** Flows whose `/tonight` route answers with a ledger. Empty by default, as the
 *  server is for an unreadable id. Seeding the unreadable id here stands in for
 *  the one way the client can still hold a campaign for such a row: the
 *  campaign hook keeps a ledger for ten minutes (`useCampaign.ts` REFETCH_MS),
 *  so one read while the file was readable outlives the library reload that
 *  lists it unreadable. */
const ledgerFor = new Set<string>();
const LEDGER = {
  ok: true, reason: "",
  budget: [{ filter: "Ha", goal_h: 10, banked_h: 2, tonight_h: 1, has_ledger: true }],
  night: { dusk_unix: 1_758_060_000, dawn_unix: 1_758_100_000, dark_start_unix: null, dark_end_unix: null },
};

const asked: { url: string; method: string }[] = [];
g.fetch = async (url: string, init?: { method?: string }) => {
  const method = init?.method ?? "GET";
  asked.push({ url, method });
  const ok = (data: unknown) => ({
    ok: true, status: 200, statusText: "OK",
    headers: { get: () => "application/json" },
    json: async () => data,
  });
  const notFound = () => ({
    ok: false, status: 404, statusText: "Not Found",
    headers: { get: () => "application/json" },
    json: async () => ({ detail: "no flow with that id" }),
  });
  if (url === "/api/flows") return ok(CARDS);
  if (url === "/api/flows/folders") return ok([{ name: "My flows", count: CARDS.length, readonly: false }]);
  if (url === "/api/flows/compile") return ok({ plan: {}, structural: [], issues: [], unmapped: [] });
  if (/^\/api\/flows\/[^/]+\/run$/.test(url) && method === "POST") {
    return ok({ started: true, flow_id: "quick-m31", frames: 48, unmapped: [] });
  }
  const tonight = /^\/api\/flows\/([^/]+)\/tonight$/.exec(url);
  if (tonight) return ledgerFor.has(tonight[1]) ? ok(LEDGER) : notFound();
  const one = /^\/api\/flows\/([^/]+)$/.exec(url);
  // As the server: an unreadable row's id is 404 on every route but the list.
  const card = one ? CARDS.find((c) => c.id === one[1] && !c.unreadable) : null;
  if (card) return ok({ ...card, graph: { nodes: [], edges: [] }, created_ts: 1 });
  return notFound();
};

// ------------------------------------------------------------------ imports
const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../../../../store");
const { runBlockedReason } = await import("../../../../../components/flows/flowRunControls");
const { resetRouterCacheForTests } = await import("../../../../router");
const { resetCampaignForTests } = await import("../../now/useCampaign");
const { resetSessionDataForTests } = await import("../../now/sessionData");
const { FlowsScreen, NO_CANVAS_TARGET, RUN_IN_PROGRESS_REASON } = await import("../FlowsScreen");
const { FlowRow } = await import("../FlowRow");

// ------------------------------------------------------------------ harness
let passed = 0;
let failed = 0;
const failures: string[] = [];
async function test(name: string, fn: () => void | Promise<void>): Promise<void> {
  try { await fn(); passed++; } catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }
function eq<T>(got: T, want: T, msg: string): void {
  if (got !== want) throw new Error(`${msg}\n  expected ${String(want)}\n  got      ${String(got)}`);
}

const container = win.document.getElementById("root") as any;
let root = createRoot(container);
const settle = async () => {
  for (let i = 0; i < 6; i++) {
    await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  }
};
const tid = (t: string): any => container.querySelector(`[data-testid="${t}"]`);
const click = async (el: any) => {
  await act(async () => {
    el.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true }));
  });
  await settle();
};
const toastTitles = (): string[] => useStore.getState().toasts.map((t: any) => String(t.title));
/** Requests that OPEN or RUN flow `id`. Its `/tonight` read is the campaign
 *  hook's, made whatever the row does, so it is not counted as the row's. */
const touched = (id: string) => asked.filter((a) => a.url === `/api/flows/${id}`
  || (a.url.startsWith(`/api/flows/${id}/`) && !a.url.endsWith("/tonight")));

/** Every place `needle` appears inside `el`: its text, and every attribute of
 *  every element (title, aria-label, aria-description, data-*...). The count is
 *  the claim carry-over 6 makes, so it is measured over the whole row rather
 *  than over the three attributes the S0 row happened to use. */
function whereSaid(el: any, needle: string): string[] {
  const out: string[] = [];
  const scan = (s: string, label: string) => {
    for (let i = s.indexOf(needle); i >= 0; i = s.indexOf(needle, i + needle.length)) out.push(label);
  };
  scan(String(el.textContent ?? ""), "text");
  for (const node of [el, ...el.querySelectorAll("*")]) {
    for (const a of Array.from(node.attributes as ArrayLike<any>)) {
      scan(String(a.value), `${String(node.tagName).toLowerCase()}[${String(a.name)}]`);
    }
  }
  return out;
}

/** A verb's WORD, for a failure message: `ActionButton` puts its glyph in the
 *  same text node run, and the word is the claim. */
const wordOf = (el: any): string => String(el?.textContent ?? "").replace(/[^\x20-\x7e]/g, "").trim();

/** A colour as jsdom serialises it, so a hex constant compares with `style`. */
const css = (color: string): string => {
  const p = win.document.createElement("span");
  p.style.background = color;
  return String(p.style.background);
};
/** The row's status dot (its first child, aria-hidden). */
const dotOf = (id: string): string => String(tid(`flow-row-${id}`).firstElementChild.style.background);
// `FlowsScreen`'s DOT_NEVER and DOT_CAMPAIGN. The dot is the one channel left
// that could still claim "campaign" on an unreadable row - its meta line is the
// reason and it has no verb - so it is read directly.
const DOT_NEVER = css("#7683a5");
const DOT_CAMPAIGN = css("#9B51E0");

const ADMIN = ["view.status", "view.preview", "control.mount", "control.capture", "view.site_derived"];
const VIEWER = ["view.status", "view.preview"];

interface Rig {
  sequence?: unknown;
  resumeArm?: unknown;
  /** What `flows.record` holds - the in-memory record, which a library reload
   *  does not touch. */
  record?: unknown;
  /** `flows.run.phase`, which `useFlowRunControls().running` reads. */
  phase?: string;
  cards?: any[];
  ledger?: string[];
}

function seed(role: string, caps: string[], rig: Rig = {}): void {
  CARDS = rig.cards ?? DEFAULT_CARDS;
  ledgerFor.clear();
  for (const id of rig.ledger ?? []) ledgerFor.add(id);
  act(() => {
    const f = useStore.getState().flows;
    useStore.setState({
      principal: { role, email: null, caps } as never,
      authGate: "open",
      sequence: (rig.sequence ?? { state: "idle" }) as never,
      // A connected camera, or `runBlockedReason` refuses for a reason that has
      // nothing to do with the row under test.
      status: { connected: { camera: { connected: true } } } as never,
      equipConnected: true,
      wsPhase: "up",
      resumeArm: (rig.resumeArm ?? null) as never,
      toasts: [] as never,
      flows: {
        ...f,
        record: (rig.record ?? null) as never,
        dirty: false,
        run: { ...f.run, phase: rig.phase ?? "idle" },
        ui: { ...f.ui, screen: "library", query: "", folderChip: "all", highlightId: null },
      } as never,
    } as never);
  });
  asked.length = 0;
}

async function mountAt(hash: string): Promise<void> {
  win.location.hash = hash;
  resetRouterCacheForTests();
  await act(async () => { root.unmount(); });
  // The campaign and session hooks keep module-level answers (shared across
  // their consumers by design). Cleared AFTER the old tree is gone, so its
  // cleanup cannot decrement a counter the reset already zeroed and strand the
  // campaign ticker that keeps this process alive.
  resetCampaignForTests();
  resetSessionDataForTests();
  root = createRoot(container);
  await act(async () => { root.render(createElement(FlowsScreen as any)); });
  await settle();
}

function recordOf(card: any): unknown {
  return { ...card, graph: { nodes: [], edges: [] }, created_ts: 1 };
}

// ======================================================= 1. said once (6)

// MUTANT "pass the reason as openReason/runReason again" (FlowsScreen's row
// props restored to S0's pair, `runReason={unreadableReason(r.card) ??
// listRunReason}` and `openReason={unreadableReason(r.card)}`).
// Observed, 16/23:
//   x the reason is said once, on the meta line, and the body is described by
//     it: the reason is said 2 times on the row: text, button[title]
//   x a body press raises no toast, asks nothing and goes nowhere: at 390 px
//     the press raised a toast - toasts: ["saved by a newer AstroDeck (schema
//     4); update to open it"]
//     expected 0
//     got      1
//   x for a viewer the unreadable row carries no capability sentence: the row's
//     own reason is not said exactly once for a viewer
//     expected 1
//     got      2
//   x an unreadable row named like the running plan is not LIVE: an unreadable
//     row named like the running plan does not say its reason exactly once
//     expected 1
//     got      2
//   x an unreadable row the running campaign names is not LIVE: an unreadable
//     row the live campaign names does not say its reason exactly once
//     expected 1
//     got      2
//   x an unreadable row an armed campaign names is not the campaign: an
//     unreadable row an armed campaign names does not say its reason exactly
//     once
//     expected 1
//     got      2
//   x an unreadable row the in-memory record names is not the started run: an
//     unreadable row the stale record names does not say its reason exactly
//     once
//     expected 1
//     got      2
//
// MUTANT "openReason again" (that pair's `openReason` half alone).
// Observed, 16/23: the same seven failures as the pair above, byte for byte
// (the two outputs were compared with `cmp`) - the `openReason` half is the
// whole of what the pair repeats.
//
// Its `runReason` half alone leaves all 23 cases green, and that is the design
// rather than a blind spot: an unreadable row renders no verb, so the reason
// has nothing to ride on. The verb's absence is section 2's claim, pinned by
// its own mutant.
//
// MUTANT "ignore unreadable" (FlowsScreen's `const unreadable =
// unreadableReason(card);` in the rows memo made `= null`, so the meta line is
// the readable card's `9 stages · 8 wires` and the LIVE/campaign guard is off
// with it). Observed, 17/23:
//   x the reason is said once, on the meta line, and the body is described by
//     it: the reason is said 0 times on the row
//   x for a viewer the unreadable row carries no capability sentence: the row's
//     own reason is not said exactly once for a viewer
//     expected 1
//     got      0
//   x an unreadable row named like the running plan is not LIVE: an unreadable
//     row named like the running plan says LIVE - "LIVE"
//   x an unreadable row the running campaign names is not LIVE: an unreadable
//     row the live campaign names says LIVE - "LIVE"
//   x an unreadable row an armed campaign names is not the campaign: an
//     unreadable row an armed campaign names wears a run's or the campaign's
//     dot
//     expected rgb(118, 131, 165)
//     got      rgb(155, 81, 224)
//   x an unreadable row the in-memory record names is not the started run: an
//     unreadable row the stale record names says LIVE - "LIVE"
//
// MUTANT "describedby dropped" (FlowRow's `aria-describedby` on the body made
// `undefined`). Observed, 22/23:
//   x the reason is said once, on the meta line, and the body is described by
//     it: the body is not described by the line that says why
//     expected flow-meta-future-mosaic
//     got      (no element)
//
// MUTANT "body not disabled" (FlowRow's `aria-disabled` for an unreadable row
// made `undefined`). Observed, 22/23:
//   x the reason is said once, on the meta line, and the body is described by
//     it: the row body is not honest-disabled
//     expected true
//     got      null
await test("the reason is said once, on the meta line, and the body is described by it", async () => {
  seed("admin", ADMIN);
  viewportW = 390;
  await mountAt("#/session/flows");
  const row = tid("flow-row-future-mosaic");
  assert(row != null, "precondition: the unreadable row rendered");
  const said = whereSaid(row, NEWER);
  assert(said.length === 1 && said[0] === "text",
    `the reason is said ${said.length} times on the row${said.length ? `: ${said.join(", ")}` : ""}`);
  const meta = tid("flow-meta-future-mosaic");
  assert(String(meta.textContent).includes(NEWER), "the one place it is said is not the meta line");
  const open = tid("flow-open-future-mosaic");
  eq(open.getAttribute("aria-disabled"), "true", "the row body is not honest-disabled");
  eq(open.getAttribute("title"), null, "the row body repeats the reason in a hover");
  const ref = open.getAttribute("aria-describedby");
  const described = ref ? win.document.getElementById(ref) : null;
  eq(described?.getAttribute("data-testid") ?? "(no element)", "flow-meta-future-mosaic",
    "the body is not described by the line that says why");
});

// THE ONE COPY IS WHOLE. With no title and no toast, the meta line is the only
// place a phone shows the reason, and at 390 px it gets 309 px: 54 characters
// of 9.5 px IBM Plex Mono (FlowRow's own arithmetic, at the meta line's style).
// The reason under test is 56 characters, and the server's "unreadable: ..."
// reasons run to 172, so an ellipsised line would cut the one sentence
// carry-over 6 keeps. jsdom lays nothing out, so the claim is read off the
// style that makes the line wrap, with the readable row beside it as the
// control that keeps its single ellipsised row.
//
// MUTANT "meta line never wraps" (FlowRow's meta `whiteSpace: unreadable ?
// "normal" : "nowrap"` made `"nowrap"`, which is the S1-10 row as first
// written). Observed, 22/23:
//   x the unreadable row's meta line wraps, so the one copy of the reason is
//     whole on a phone: the one copy of the reason is cut to a single
//     ellipsised line
//     expected normal
//     got      nowrap
//
// MUTANT "long token not broken" (FlowRow's meta `overflowWrap: unreadable ?
// "anywhere" : undefined` made `undefined`). Observed, 22/23:
//   x the unreadable row's meta line wraps, so the one copy of the reason is
//     whole on a phone: a reason with a long unbroken token cannot break
//     inside it
//     expected anywhere
//     got      (unset)
//
// MUTANT "every meta line wraps" (the same `whiteSpace` made `"normal"`).
// Observed, 22/23:
//   x the unreadable row's meta line wraps, so the one copy of the reason is
//     whole on a phone: control: the readable row's meta line wraps now
//     expected nowrap
//     got      normal
await test("the unreadable row's meta line wraps, so the one copy of the reason is whole on a phone", async () => {
  seed("admin", ADMIN);
  viewportW = 390;
  await mountAt("#/session/flows");
  assert(NEWER.length > 54, "precondition: the reason is longer than a 390 px meta line holds");
  const meta = tid("flow-meta-future-mosaic");
  eq(meta.style.whiteSpace, "normal", "the one copy of the reason is cut to a single ellipsised line");
  // jsdom reads an unset property as "", which a failure line cannot show.
  eq(meta.style.overflowWrap || "(unset)", "anywhere",
    "a reason with a long unbroken token cannot break inside it");
  // Control: the readable row's meta line keeps its one ellipsised row.
  const good = tid("flow-meta-quick-m31");
  eq(good.style.whiteSpace, "nowrap", "control: the readable row's meta line wraps now");
  eq(good.style.overflowWrap || "(unset)", "(unset)",
    "control: the readable row's meta line breaks inside words now");
  eq(good.style.textOverflow, "ellipsis", "control: the readable row's meta line lost its ellipsis");
});

// MUTANT "body press opens" (FlowRow's body `onClick` loses `if (unreadable)
// return;`, so the press falls through to `onOpen()`). Observed, 22/23:
//   x a body press raises no toast, asks nothing and goes nowhere: at 390 px
//     the press navigated to a screen that could only 404
//     expected #/session/flows
//     got      #/session/flows/flowStages?open=future-mosaic
//
// The reason-again mutants above fail this case too: `openReason` locks the
// body the honest way, and that way toasts.
await test("a body press raises no toast, asks nothing and goes nowhere", async () => {
  for (const width of [390, 1024]) {
    seed("admin", ADMIN);
    viewportW = width;
    await mountAt("#/session/flows");
    const hash = win.location.hash;
    await click(tid("flow-open-future-mosaic"));
    eq(toastTitles().length, 0,
      `at ${width} px the press raised a toast - toasts: ${JSON.stringify(toastTitles())}`);
    eq(touched("future-mosaic").length, 0,
      `at ${width} px the press asked the server for a flow it listed as unreadable`);
    eq(win.location.hash, hash, `at ${width} px the press navigated to a screen that could only 404`);
  }
});

// ================================================== 2. no verb (6, continued)

// MUTANT "row renders the verb for an unreadable row" (FlowRow's verb gate
// `!unreadable || live` made `true`). It fails the four LIVE-and-campaign cases
// of section 4 too, on their "shows a verb" line: with the verb back, those
// rows offer RUN. Observed, 16/23:
//   x an unreadable row renders no RUN or RESUME: an unreadable row offers RUN
//     - "RUN"
//   x for a viewer the unreadable row carries no capability sentence: a viewer
//     is told to ask for access to run a flow nobody can run
//     expected 0
//     got      1
//   x the row itself renders no RUN or RESUME for an unreadable card, whatever
//     it is handed: the row offers RUN on a card nothing can open - "RUN"
//   x an unreadable row named like the running plan is not LIVE: an unreadable
//     row named like the running plan shows a verb - "RUN"
//   x an unreadable row the running campaign names is not LIVE: an unreadable
//     row the live campaign names shows a verb - "RUN"
//   x an unreadable row an armed campaign names is not the campaign: an
//     unreadable row an armed campaign names shows a verb - "RUN"
//   x an unreadable row the in-memory record names is not the started run: an
//     unreadable row the stale record names shows a verb - "RUN"
await test("an unreadable row renders no RUN or RESUME", async () => {
  seed("admin", ADMIN);
  viewportW = 390;
  await mountAt("#/session/flows");
  const verb = tid("flow-verb-future-mosaic");
  assert(verb == null, `an unreadable row offers RUN - "${wordOf(verb)}"`);
  // Control: the readable row beside it still has its verb.
  assert(/RUN/.test(tid("flow-verb-quick-m31")?.textContent ?? ""),
    "control: the readable row lost its RUN");
});

await test("for a viewer the unreadable row carries no capability sentence", async () => {
  seed("viewer", VIEWER);
  viewportW = 390;
  await mountAt("#/session/flows");
  const capability = runBlockedReason(false, true, false) as string;
  assert(capability != null, "precondition: a viewer's RUN is refused for a capability");
  const row = tid("flow-row-future-mosaic");
  eq(whereSaid(row, capability).length, 0,
    "a viewer is told to ask for access to run a flow nobody can run");
  eq(whereSaid(row, NEWER).length, 1, "the row's own reason is not said exactly once for a viewer");
  eq(tid("flow-verb-quick-m31").getAttribute("title"), capability,
    "control: the readable row still carries the capability reason");
});

await test("the row itself renders no RUN or RESUME for an unreadable card, whatever it is handed", async () => {
  seed("admin", ADMIN);
  const host = win.document.createElement("div");
  win.document.body.appendChild(host);
  const probe = createRoot(host);
  try {
    for (const verb of ["run", "resume"] as const) {
      await act(async () => {
        probe.render(createElement(FlowRow, {
          card: FUTURE as any, meta: NEWER, dotColor: "#7683a5", verb,
          runReason: null, openReason: null,
          onRun: () => {}, onResume: () => {}, onLive: () => {}, onOpen: () => {},
          onExplain: () => {},
        }));
      });
      const el = host.querySelector('[data-testid="flow-verb-future-mosaic"]') as any;
      assert(el == null,
        `the row offers ${verb.toUpperCase()} on a card nothing can open - "${wordOf(el)}"`);
    }
  } finally {
    await act(async () => { probe.unmount(); });
    host.remove();
  }
});

// ======================================= 3. OPEN THE FLOWS CANVAS (7a)

// MUTANT "canvas target ignores unreadable" (both `canvasTarget` branches read
// `visible` again, so the first row wins). Observed, 20/23:
//   x OPEN THE FLOWS CANVAS never picks an unreadable row as its flow: with
//     only an unreadable row showing, OPEN THE FLOWS CANVAS is live
//     expected true
//     got      null
//   x with a stale record naming an unreadable row, the canvas opens the first
//     readable row: OPEN THE FLOWS CANVAS opened the stale record's unreadable
//     flow
//     expected #/session/flows?open=quick-m31
//     got      #/session/flows?open=future-mosaic
//   x with a stale record naming the unreadable row and it alone showing, the
//     canvas says it has nothing to open: OPEN THE FLOWS CANVAS is live on a
//     stale record's unreadable flow
//     expected true
//     got      null
await test("OPEN THE FLOWS CANVAS never picks an unreadable row as its flow", async () => {
  seed("admin", ADMIN);
  viewportW = 1024;
  await mountAt("#/session/flows");
  // Filter down to the unreadable row alone, the one case in which it is first.
  await act(async () => { useStore.getState().flowsSetUi({ query: "NGC 7000" }); });
  await settle();
  assert(tid("flow-row-future-mosaic") != null && tid("flow-row-quick-m31") == null,
    "precondition: only the unreadable row is showing");
  const canvas = tid("flows-open-canvas");
  eq(canvas.getAttribute("aria-disabled"), "true",
    "with only an unreadable row showing, OPEN THE FLOWS CANVAS is live");
  eq(canvas.getAttribute("title"), NO_CANVAS_TARGET, "and it does not say why");
  const hash = win.location.hash;
  await click(canvas);
  eq(win.location.hash, hash, "OPEN THE FLOWS CANVAS navigated to an unreadable flow");
  eq(touched("future-mosaic").length, 0, "and asked the server for it");
});

// MUTANT "drop the readable filter on the openRecordId branch" (`canvasTarget`'s
// first branch reads `visible.find((c) => c.id === openRecordId)` instead of
// `readableVisible.find(...)`). Observed, 21/23:
//   x with a stale record naming an unreadable row, the canvas opens the first
//     readable row: OPEN THE FLOWS CANVAS opened the stale record's unreadable
//     flow
//     expected #/session/flows?open=quick-m31
//     got      #/session/flows?open=future-mosaic
//   x with a stale record naming the unreadable row and it alone showing, the
//     canvas says it has nothing to open: OPEN THE FLOWS CANVAS is live on a
//     stale record's unreadable flow
//     expected true
//     got      null
await test("with a stale record naming an unreadable row, the canvas opens the first readable row", async () => {
  seed("admin", ADMIN, { record: recordOf(FUTURE) });
  viewportW = 1024;
  await mountAt("#/session/flows");
  eq(useStore.getState().flows.record?.id, "future-mosaic",
    "precondition: the in-memory record names the unreadable row");
  const canvas = tid("flows-open-canvas");
  eq(canvas.getAttribute("aria-disabled"), null, "precondition: a readable row is showing, so OPEN is live");
  await click(canvas);
  eq(win.location.hash, "#/session/flows?open=quick-m31",
    "OPEN THE FLOWS CANVAS opened the stale record's unreadable flow");
  eq(touched("future-mosaic").length, 0, "and asked the server for it");
});

await test("with a stale record naming the unreadable row and it alone showing, the canvas says it has nothing to open", async () => {
  seed("admin", ADMIN, { record: recordOf(FUTURE) });
  viewportW = 1024;
  await mountAt("#/session/flows");
  await act(async () => { useStore.getState().flowsSetUi({ query: "NGC 7000" }); });
  await settle();
  assert(tid("flow-row-future-mosaic") != null && tid("flow-row-quick-m31") == null,
    "precondition: only the unreadable row is showing");
  const canvas = tid("flows-open-canvas");
  eq(canvas.getAttribute("aria-disabled"), "true",
    "OPEN THE FLOWS CANVAS is live on a stale record's unreadable flow");
  eq(canvas.getAttribute("title"), NO_CANVAS_TARGET, "and it does not say why");
});

// CONTROL. The open record still wins over the first row when it is readable,
// so the filter narrowed the branch rather than removing it.
//
// MUTANT "drop the openRecordId branch" (`canvasTarget`'s first branch made
// `undefined`, so it is `readableVisible[0]?.id` alone). Observed, 22/23:
//   x control: a readable open record still outranks the first row: OPEN THE
//     FLOWS CANVAS no longer opens the flow already loaded
//     expected #/session/flows?open=dusk-flats
//     got      #/session/flows?open=quick-m31
await test("control: a readable open record still outranks the first row", async () => {
  seed("admin", ADMIN, { cards: [GOOD, FUTURE, SECOND], record: recordOf(SECOND) });
  viewportW = 1024;
  await mountAt("#/session/flows");
  assert(tid("flow-row-dusk-flats") != null, "precondition: the third row rendered");
  await click(tid("flows-open-canvas"));
  eq(win.location.hash, "#/session/flows?open=dusk-flats",
    "OPEN THE FLOWS CANVAS no longer opens the flow already loaded");
});

// ================================================ 4. LIVE and campaign (7b)

/** Carry-over 7b's claim about one unreadable row, checked in the order that
 *  makes a failure say what happened: it does not say LIVE (the claim), it does
 *  not wear a run's or the campaign's dot (the one channel left that could
 *  still claim the campaign - its meta is the reason and it has no verb), and
 *  it has no verb at all (carry-over 6, which a LIVE-free row could still break
 *  with a RUN). */
function assertNotTheRun(id: string, how: string): void {
  const verb = tid(`flow-verb-${id}`);
  const word = wordOf(verb);
  assert(!/LIVE/.test(word), `an unreadable row ${how} says LIVE - "${word}"`);
  eq(dotOf(id), DOT_NEVER, `an unreadable row ${how} wears a run's or the campaign's dot`);
  assert(verb == null, `an unreadable row ${how} shows a verb - "${word}"`);
  eq(whereSaid(tid(`flow-row-${id}`), NEWER).length, 1,
    `an unreadable row ${how} does not say its reason exactly once`);
}

// MUTANT "remove the guard" (FlowsScreen's `readable &&` dropped from BOTH
// `isCampaign` and `isLive`). Observed, 19/23:
//   x an unreadable row named like the running plan is not LIVE: an unreadable
//     row named like the running plan says LIVE - "LIVE"
//   x an unreadable row the running campaign names is not LIVE: an unreadable
//     row the live campaign names says LIVE - "LIVE"
//   x an unreadable row an armed campaign names is not the campaign: an
//     unreadable row an armed campaign names wears a run's or the campaign's
//     dot
//     expected rgb(118, 131, 165)
//     got      rgb(155, 81, 224)
//   x an unreadable row the in-memory record names is not the started run: an
//     unreadable row the stale record names says LIVE - "LIVE"
//
// Its halves, each run alone from the backup:
//
// MUTANT "remove the guard from isLive only". Observed, 21/23:
//   x an unreadable row named like the running plan is not LIVE: an unreadable
//     row named like the running plan says LIVE - "LIVE"
//   x an unreadable row the in-memory record names is not the started run: an
//     unreadable row the stale record names says LIVE - "LIVE"
//
// MUTANT "remove the guard from isCampaign only". A live campaign is still not
// LIVE here - `isLive` keeps its own guard - so what gives it away is the dot.
// Observed, 21/23:
//   x an unreadable row the running campaign names is not LIVE: an unreadable
//     row the live campaign names wears a run's or the campaign's dot
//     expected rgb(118, 131, 165)
//     got      rgb(155, 81, 224)
//   x an unreadable row an armed campaign names is not the campaign: an
//     unreadable row an armed campaign names wears a run's or the campaign's
//     dot
//     expected rgb(118, 131, 165)
//     got      rgb(155, 81, 224)
await test("an unreadable row named like the running plan is not LIVE", async () => {
  seed("admin", ADMIN, { sequence: { state: "running", plan_name: FUTURE.name } });
  viewportW = 390;
  await mountAt("#/session/flows");
  assertNotTheRun("future-mosaic", "named like the running plan");
});

await test("an unreadable row the running campaign names is not LIVE", async () => {
  seed("admin", ADMIN, {
    // A run whose plan name matches no card, so the ONLY route to this row is
    // the campaign's id.
    sequence: { state: "running", plan_name: "Some other plan" },
    resumeArm: { armed: { id: "sess-9", name: "Mosaic", origin: "flow", origin_id: FUTURE.id, auto_resume: true } },
    ledger: [FUTURE.id],
  });
  viewportW = 390;
  await mountAt("#/session/flows");
  assert(asked.some((a) => a.url === `/api/flows/${FUTURE.id}/tonight`),
    "precondition: the campaign hook read a ledger for the unreadable id");
  assertNotTheRun("future-mosaic", "the live campaign names");
});

await test("an unreadable row an armed campaign names is not the campaign", async () => {
  seed("admin", ADMIN, {
    resumeArm: { armed: { id: "sess-9", name: "Mosaic", origin: "flow", origin_id: FUTURE.id, auto_resume: true } },
    ledger: [FUTURE.id],
  });
  viewportW = 390;
  await mountAt("#/session/flows");
  assert(asked.some((a) => a.url === `/api/flows/${FUTURE.id}/tonight`),
    "precondition: the campaign hook read a ledger for the unreadable id");
  assertNotTheRun("future-mosaic", "an armed campaign names");
});

await test("an unreadable row the in-memory record names is not the started run", async () => {
  seed("admin", ADMIN, { phase: "running", record: recordOf(FUTURE) });
  viewportW = 390;
  await mountAt("#/session/flows");
  assertNotTheRun("future-mosaic", "the stale record names");
});

// ================================================================ controls

// CONTROLS FOR THE GUARD. A readable row in each of the four situations above
// keeps the verb it had before S1-10.
//
// MUTANT "guard inverted" (FlowsScreen's `readable &&` made `!readable &&` in
// both `isCampaign` and `isLive`). Observed, 15/23:
//   x an unreadable row named like the running plan is not LIVE: an unreadable
//     row named like the running plan says LIVE - "LIVE"
//   x an unreadable row the running campaign names is not LIVE: an unreadable
//     row the live campaign names says LIVE - "LIVE"
//   x an unreadable row an armed campaign names is not the campaign: an
//     unreadable row an armed campaign names wears a run's or the campaign's
//     dot
//     expected rgb(118, 131, 165)
//     got      rgb(155, 81, 224)
//   x an unreadable row the in-memory record names is not the started run: an
//     unreadable row the stale record names says LIVE - "LIVE"
//   x control: a readable row named like the running plan is LIVE and goes to
//     SESSION / NOW: the readable row named like the running plan lost LIVE -
//     "RUN"
//   x control: a readable row the running campaign names is LIVE: the readable
//     campaign row lost LIVE - "RUN"
//   x control: a readable row an armed campaign names offers RESUME and says it
//     is parked: the readable armed campaign lost RESUME - "RUN"
//   x control: a readable row the started run's record names is LIVE: the
//     started flow's row lost LIVE - "RUN"
await test("control: a readable row named like the running plan is LIVE and goes to SESSION / NOW", async () => {
  seed("admin", ADMIN, { sequence: { state: "running", plan_name: GOOD.name } });
  viewportW = 390;
  await mountAt("#/session/flows");
  const verb = tid("flow-verb-quick-m31");
  assert(/LIVE/.test(verb?.textContent ?? ""),
    `the readable row named like the running plan lost LIVE - "${wordOf(verb)}"`);
  eq(verb.getAttribute("aria-disabled"), null, "LIVE is a navigation and must never be locked");
  await click(verb);
  eq(win.location.hash, "#/session/now", "LIVE no longer goes to SESSION / NOW");
});

await test("control: a readable row the running campaign names is LIVE", async () => {
  seed("admin", ADMIN, {
    sequence: { state: "running", plan_name: "Some other plan" },
    resumeArm: { armed: { id: "sess-9", name: "M31", origin: "flow", origin_id: GOOD.id, auto_resume: true } },
    ledger: [GOOD.id],
  });
  viewportW = 390;
  await mountAt("#/session/flows");
  const verb = tid("flow-verb-quick-m31");
  assert(/LIVE/.test(verb?.textContent ?? ""),
    `the readable campaign row lost LIVE - "${wordOf(verb)}"`);
  assert(/campaign · night \d+ of ~\d+ · running now/.test(tid("flow-meta-quick-m31").textContent),
    `the readable campaign row's meta changed: "${String(tid("flow-meta-quick-m31").textContent)}"`);
});

await test("control: a readable row an armed campaign names offers RESUME and says it is parked", async () => {
  seed("admin", ADMIN, {
    resumeArm: { armed: { id: "sess-9", name: "M31", origin: "flow", origin_id: GOOD.id, auto_resume: true } },
    ledger: [GOOD.id],
  });
  viewportW = 390;
  await mountAt("#/session/flows");
  const verb = tid("flow-verb-quick-m31");
  assert(/RESUME/.test(verb?.textContent ?? ""),
    `the readable armed campaign lost RESUME - "${wordOf(verb)}"`);
  eq(verb.getAttribute("aria-disabled"), null, "RESUME is locked for an admin with nothing running");
  assert(/campaign · night \d+ of ~\d+ · parked · resumes at dusk/.test(tid("flow-meta-quick-m31").textContent),
    `the readable campaign row's meta changed: "${String(tid("flow-meta-quick-m31").textContent)}"`);
  eq(dotOf("quick-m31"), DOT_CAMPAIGN, "the readable campaign row lost the campaign's dot");
});

await test("control: a readable row the started run's record names is LIVE", async () => {
  seed("admin", ADMIN, { phase: "running", record: recordOf(GOOD) });
  viewportW = 390;
  await mountAt("#/session/flows");
  const verb = tid("flow-verb-quick-m31");
  assert(/LIVE/.test(verb?.textContent ?? ""),
    `the started flow's row lost LIVE - "${wordOf(verb)}"`);
});

// THE RESUME LOCK, at the row, on a READABLE card. Until S1-10 this case drove
// an unreadable card; an unreadable row now has no verb to lock, so the claim
// it pinned - RESUME honours `runReason` as RUN does - is pinned where it still
// applies.
//
// MUTANT "row locks RUN only" (FlowRow's `const reason = live ? null :
// runReason;` made `verb === "run" ? runReason : null`). Observed, 22/23:
//   x control: a RESUME verb is locked by its run reason, and its press resumes
//     nothing: RESUME is live on a row whose run is refused
//     expected true
//     got      null
await test("control: a RESUME verb is locked by its run reason, and its press resumes nothing", async () => {
  seed("admin", ADMIN);
  const host = win.document.createElement("div");
  win.document.body.appendChild(host);
  const probe = createRoot(host);
  let resumed = 0;
  try {
    await act(async () => {
      probe.render(createElement(FlowRow, {
        card: GOOD as any, meta: "campaign", dotColor: "#9B51E0", verb: "resume",
        runReason: RUN_IN_PROGRESS_REASON, openReason: null,
        onRun: () => {}, onResume: () => { resumed++; }, onLive: () => {}, onOpen: () => {},
        onExplain: (r: string) => useStore.getState().enqueueToast({ level: "warning", title: r }),
      }));
    });
    const verb = host.querySelector('[data-testid="flow-verb-quick-m31"]') as any;
    assert(/RESUME/.test(verb?.textContent ?? ""), "precondition: the verb reads RESUME");
    eq(verb.getAttribute("aria-disabled"), "true", "RESUME is live on a row whose run is refused");
    eq(verb.getAttribute("title"), RUN_IN_PROGRESS_REASON, "RESUME's lock does not carry the reason");
    await act(async () => {
      verb.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true }));
    });
    eq(resumed, 0, "the locked RESUME resumed a session");
    assert(toastTitles().includes(RUN_IN_PROGRESS_REASON), "the locked RESUME was silent");
  } finally {
    await act(async () => { probe.unmount(); });
    host.remove();
  }
});

// CONTROLS FOR THE ROW. These four predate S1-10; they are what "a readable row
// is untouched" means at the row, now that `FlowRow` reads the card itself.
//
// MUTANT "row treats every card as unreadable" (FlowRow's `const unreadable =
// unreadableReason(card) !== null;` made `= true`). Observed, 15/23:
//   x the unreadable row's meta line wraps, so the one copy of the reason is
//     whole on a phone: control: the readable row's meta line wraps now
//     expected nowrap
//     got      normal
//   x an unreadable row renders no RUN or RESUME: control: the readable row
//     lost its RUN
//   x for a viewer the unreadable row carries no capability sentence: Cannot
//     read properties of null (reading 'getAttribute')
//   x control: a readable row an armed campaign names offers RESUME and says it
//     is parked: the readable armed campaign lost RESUME - ""
//   x control: a RESUME verb is locked by its run reason, and its press resumes
//     nothing: precondition: the verb reads RESUME
//   x control: the readable row's meta, open and RUN are unlocked: the readable
//     row body is locked
//     expected null
//     got      true
//   x control: a phone tap on the readable row opens its stage list: the
//     readable row no longer opens its stage list
//     expected #/session/flows/flowStages?open=quick-m31
//     got      #/session/flows
//   x control: RUN on the readable row opens it and posts to its run route:
//     Cannot read properties of null (reading 'dispatchEvent')
await test("control: the readable row's meta, open and RUN are unlocked", async () => {
  seed("admin", ADMIN);
  viewportW = 390;
  await mountAt("#/session/flows");
  const meta = tid("flow-meta-quick-m31").textContent as string;
  assert(/7 stages · 6 wires/.test(meta) && /completed clean/.test(meta),
    `the readable row's meta changed: "${meta}"`);
  eq(tid("flow-open-quick-m31").getAttribute("aria-disabled"), null, "the readable row body is locked");
  eq(tid("flow-verb-quick-m31").getAttribute("aria-disabled"), null, "the readable row's RUN is locked");
  eq(tid("flows-run-reason"), null, "a list-level run reason appeared for an admin with a camera");
});

await test("control: a phone tap on the readable row opens its stage list", async () => {
  seed("admin", ADMIN);
  viewportW = 390;
  await mountAt("#/session/flows");
  await click(tid("flow-open-quick-m31"));
  eq(win.location.hash, "#/session/flows/flowStages?open=quick-m31",
    "the readable row no longer opens its stage list");
  eq(toastTitles().length, 0, "the readable row's open raised a toast");
});

await test("control: RUN on the readable row opens it and posts to its run route", async () => {
  seed("admin", ADMIN);
  viewportW = 390;
  await mountAt("#/session/flows");
  await click(tid("flow-verb-quick-m31"));
  assert(asked.some((a) => a.url === "/api/flows/quick-m31" && a.method === "GET"),
    "RUN no longer opens the readable flow");
  eq(asked.filter((a) => a.method === "POST" && /\/run$/.test(a.url)).map((a) => a.url).join(","),
    "/api/flows/quick-m31/run", "RUN no longer posts exactly one run for the readable flow");
});

// MUTANT "header count excludes unreadable" (FlowsScreen's `const count =
// libraryLoaded ? cards.length : null;` made to count only the readable cards).
// Observed, 22/23:
//   x control: the header count includes the unreadable row: the header does
//     not count the row it draws: "2 of 1 shown · quick sessions land here"
await test("control: the header count includes the unreadable row", async () => {
  seed("admin", ADMIN);
  viewportW = 390;
  await mountAt("#/session/flows");
  assert(/2 saved/.test(tid("flows-summary").textContent),
    `the header does not count the row it draws: "${tid("flows-summary").textContent}"`);
});

// Unmount before the tally: a mounted jsdom tree keeps a rAF loop alive and the
// process never exits (flowsDom.test.tsx, foot). The campaign hook's ticker is
// cleared with it.
await act(async () => { root.unmount(); });
resetCampaignForTests();

// ------------------------------------------------------------------- tally
const total = passed + failed;
console.log(`unreadableFlowRow.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
if (failed) (globalThis as any).process.exitCode = 1;
export default { passed, failed, total };
export { passed, failed, total };
