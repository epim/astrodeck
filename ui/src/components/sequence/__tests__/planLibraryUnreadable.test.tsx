// planLibraryUnreadable.test.tsx - a plan file that no longer reads as a plan,
// in the classic Plan panel's saved-plan list (#378). MOUNTED.
//
//   Run directly:  node --import ./test-css-stub.mjs --import tsx src/components/sequence/__tests__/planLibraryUnreadable.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// GRADED ON THE SERVER'S RECORDED ANSWER, READ, NOT COPIED:
// server/tests/fixtures/plan_list_unreadable.json holds a real `GET /api/plans`
// for a library of one good plan and one hand-edited so that its first step's
// frame_type is 'Snapshot' (#334), recorded and graded byte for byte by
// server/tests/test_plan_library_unreadable.py. Its unreadable row is
// `{id, name, status: "unreadable", unreadable, mtime}`: no frames, no
// integration, no targets. Before #378 the server skipped the file, so the
// panel had never seen such a row, and read as a plan it printed
// "undefinedt · undefinedf · NaNm" beside a LOAD that answers 422 and an
// EXPORT that answers 422. Since S7 (#478) the recording also holds a file
// that is not JSON, listed last and naming nothing; `BAD` is the first
// unreadable row, the Snapshot plan's.
//
// What is held here, on the three things this change owns:
//   * `types.ts` `PlanRow` declares every key the recorded rows carry, the
//     unreadable row's own keys optional.
//   * `lib/planLibrary.ts`: the one reading of which row is a plan
//     (`planUnreadableReason`), and `planRowSummary` answering the reason.
//   * `api/plans.ts` `listPlans`: a row whose file gives no name is named by
//     its id, as the session store names its unreadable row.
//   * `PlanLibraryPanel`: the unreadable row is listed with its reason and a
//     DELETE gated like any plan delete (control.capture), and no LOAD or
//     EXPORT. The classic panel has no RUN on any row; the row's exact
//     control list would refuse one.
//
// NAMED MUTANTS, each run in a private copy of ui/ (scratchpad
// S5-PLANS-UI/mut), never in the shared tree; the observed failure is quoted
// at the test it turned red.
//   M1  "row read as a plan"            planUnreadableReason answers null
//   M1s "row read as a plan" (summary)  planRowSummary skips its reason check
//   M1p "row read as a plan" (panel)    the panel's unreadable branch is skipped
//   M2  "ungate the unreadable delete"  its `canWrite &&` becomes `true &&`
//   M3  "name left as sent"             listPlans passes a nameless row through
//   M4  "status alone"                  the reader ignores the `unreadable` key
//   M5  "empty reason read as sent"     no fallback for a blank or odd reason
//   M6  "shutter_min undeclared"        PlanRow loses shutter_min
//   M7  "confirm without the file"      the unreadable delete's body is dropped
//   M10 "reason dropped" (panel)        the row says unreadable and not why
//   V3  "cancel still deletes"          del ignores the confirm's answer
//   V9  "badge kept"                    the row wears LOADED for the editor's plan
//   F1  "unreadable rows filtered out"  the panel keeps only rows the reader passes
// (V3 and V9 were added by the task's verifier, run in its own private copy,
// scratchpad S5-PLANS-UI-verify-mut, which re-measured every tally below
// against these 14 cases. F1 and M3's second quote were run for S7's two
// deliberate pin changes, in scratchpad S7-STORE-mut.)

/* eslint-disable @typescript-eslint/no-explicit-any */

// ---------------------------------------------------------------- jsdom first
const { JSDOM } = await import("jsdom");
const dom = new JSDOM(
  `<!doctype html><html><body><div id="root"></div></body></html>`,
  { url: "http://local/", pretendToBeVisual: true },
);
const win = dom.window as any;
win.matchMedia = () => ({
  matches: false, addEventListener() {}, removeEventListener() {},
  addListener() {}, removeListener() {},
});
win.WebSocket = class { close() {} addEventListener() {} send() {} };

const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "HTMLInputElement", "Element", "Node",
  "Event", "CustomEvent", "MouseEvent", "KeyboardEvent", "localStorage", "getComputedStyle",
  "matchMedia", "WebSocket", "requestAnimationFrame", "cancelAnimationFrame",
]) {
  const v = k === "window" ? win : win[k];
  if (v === undefined) continue;
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;

// ------------------------------------------------------------- the fixture
const { readFileSync } = await import("node:fs");
const FIXTURE = JSON.parse(readFileSync(
  new URL("../../../../../server/tests/fixtures/plan_list_unreadable.json", import.meta.url),
  "utf8") as string);
const LIST: any[] = FIXTURE.list;
const GOOD = LIST.find((r) => r.status === undefined);
const BAD = LIST.find((r) => r.status === "unreadable");
if (!GOOD || !BAD) throw new Error("the fixture no longer holds one good and one unreadable row");
const REASON: string = BAD.unreadable;
const BAD_NAME: string = BAD.name;
const TYPES_TS = readFileSync(new URL("../../../types.ts", import.meta.url), "utf8") as string;

// ------------------------------------------------------------- the fake rig
/** What `GET /api/plans` answers now. Mutable: a DELETE takes the row away,
 *  as the server's file removal does, and a case may swap the whole list. */
let listNow: any[] = LIST.map((r) => ({ ...r }));
const asked: { url: string; method: string }[] = [];
g.fetch = async (url: string, init?: { method?: string }) => {
  const method = (init?.method ?? "GET").toUpperCase();
  const u = String(url);
  asked.push({ url: u, method });
  const json = (data: unknown, status = 200) => ({
    ok: status < 400, status, statusText: status < 400 ? "OK" : "Error",
    headers: { get: () => "application/json" },
    json: async () => data, text: async () => JSON.stringify(data),
    blob: async () => new Blob([JSON.stringify(data)]),
  });
  if (u === "/api/plans" && method === "GET") return json(listNow);
  const m = /^\/api\/plans\/([^/]+)$/.exec(u);
  if (m && method === "DELETE") {
    listNow = listNow.filter((r) => r.id !== m[1]);
    return json({ deleted: m[1] });
  }
  // The recorded 422s, for the reads this panel must never make of that row.
  if (u === FIXTURE.get.url) return json(FIXTURE.get.body, FIXTURE.get.status);
  if (u === FIXTURE.export.url) return json(FIXTURE.export.body, FIXTURE.export.status);
  return json({ detail: "not in this test" }, 404);
};

// ------------------------------------------------------------------ imports
const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../../store");
const PlanLibraryPanel = (await import("../PlanLibraryPanel")).default;
const {
  planRowSummary, planUnreadableReason, PLAN_UNREADABLE_FALLBACK, unreadablePlanDeleteBody,
} = await import("../../../lib/planLibrary");
const { listPlans } = await import("../../../api/plans");

// ------------------------------------------------------------------ harness
let passed = 0;
let failed = 0;
const failures: string[] = [];
async function testAsync(name: string, fn: () => Promise<void> | void): Promise<void> {
  try { await fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }
function eq<T>(got: T, want: T, msg: string): void {
  if (got !== want) throw new Error(`${msg}\n  expected ${String(want)}\n  got      ${String(got)}`);
}
const settle = async () => {
  for (let i = 0; i < 6; i++) {
    await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  }
};

const container = win.document.getElementById("root") as any;
const root = createRoot(container);

const unreadableRow = (id = BAD.id) =>
  container.querySelector(`[data-testid="plan-unreadable-${id}"]`) as any;
/** The good row: the bordered two-line block that carries its name. */
function goodRow(): any {
  return [...container.querySelectorAll("div.border")].find((d: any) =>
    d.querySelector("span")?.textContent === GOOD.name) ?? null;
}
const buttonsIn = (el: any): string[] =>
  [...el.querySelectorAll("button")].map((b: any) =>
    (b.getAttribute("aria-label") || b.textContent || "").trim());

async function mount(caps: string[], loadedPlanId: string | null = null): Promise<void> {
  await act(async () => { root.render(createElement("div")); });
  await act(async () => {
    useStore.setState({
      principal: { role: caps.includes("control.capture") ? "operator" : "viewer", email: null, caps },
      loadedPlanId,
      editorDirty: false,
      confirm: null,
    } as never);
  });
  await act(async () => { root.render(createElement(PlanLibraryPanel)); });
  await settle();
}

// ===================================================== the type and the reader

await testAsync("PlanRow declares every key the recorded rows carry, the unreadable row's own optional", () => {
  // M6 "shutter_min undeclared", observed (13/14 passed):
  //   x PlanRow declares every key the recorded rows carry, the unreadable
  //   row's own optional: the recorded row carries shutter_min, which PlanRow
  //   does not declare
  const at = TYPES_TS.indexOf("export interface PlanRow {");
  assert(at >= 0, "types.ts has no `export interface PlanRow`");
  const body = TYPES_TS.slice(at, TYPES_TS.indexOf("\n}", at));
  const declared = new Map<string, string>();
  for (const m of body.matchAll(/^ {2}(\w+)(\??):\s*([^;]+);/gm)) declared.set(m[1], `${m[2]}${m[3]}`);
  for (const row of LIST) {
    for (const key of Object.keys(row)) {
      assert(declared.has(key), `the recorded row carries ${key}, which PlanRow does not declare`);
    }
  }
  // Keys only one kind of row carries cannot be required on the type both share.
  for (const key of ["status", "unreadable", "shutter_min"]) {
    assert((declared.get(key) ?? "").startsWith("?"),
      `PlanRow.${key} is required, but a recorded row leaves it out: ${declared.get(key)}`);
  }
  eq(declared.get("status"), `?"unreadable"`, "PlanRow.status is typed as:");
});

await testAsync("the reader: the recorded unreadable row is one, and says the server's reason as sent", () => {
  // M1 "row read as a plan", observed (3/14 passed; the summary and
  // either-key cases below failed with it, and every panel case about the row,
  // each on its missing row, as under M1p):
  //   x the reader: the recorded unreadable row is one, and says the server's
  //   reason as sent: the reason:
  //     expected fails validation: targets.0.steps.0.frame_type: frame type
  //     'Snapshot' is not one of Light, Dark, Bias, Flat (in any case)
  //     got      null
  eq(planUnreadableReason(GOOD), null, "control: the good plan is read as unreadable:");
  eq(planUnreadableReason(BAD), REASON, "the reason:");
});

await testAsync("the summary answers the reason, never \"undefinedt\"", () => {
  // M1s "row read as a plan" (summary), observed (13/14 passed; M1 failed
  // this case with the same two lines):
  //   x the summary answers the reason, never "undefinedt": the unreadable
  //   row's summary:
  //     expected unreadable: fails validation: targets.0.steps.0.frame_type:
  //     frame type 'Snapshot' is not one of Light, Dark, Bias, Flat (in any case)
  //     got      undefinedt · undefinedf · NaNm
  eq(planRowSummary(GOOD), `${GOOD.targets}t · ${GOOD.frames}f · ${Math.round(GOOD.integration_min)}m`,
    "control: the good plan's summary:");
  eq(planRowSummary(BAD), `unreadable: ${REASON}`, "the unreadable row's summary:");
});

await testAsync("either key marks the row, and a reason that is no reason still says unreadable", () => {
  // M4 "status alone", observed (13/14 passed):
  //   x either key marks the row, and a reason that is no reason still says
  //   unreadable: the reason key without a status:
  //     expected the file is damaged
  //     got      null
  // M5 "empty reason read as sent", observed (13/14 passed):
  //   x either key marks the row, and a reason that is no reason still says
  //   unreadable: a status with no reason:
  //     expected this AstroDeck cannot read this plan
  //     got      undefined
  eq(planUnreadableReason({ unreadable: "the file is damaged" }), "the file is damaged",
    "the reason key without a status:");
  eq(planUnreadableReason({ status: "unreadable" }), PLAN_UNREADABLE_FALLBACK, "a status with no reason:");
  eq(planUnreadableReason({ status: "unreadable", unreadable: "   " }), PLAN_UNREADABLE_FALLBACK,
    "a blank reason:");
  eq(planUnreadableReason({ status: "unreadable", unreadable: 7 as never }), PLAN_UNREADABLE_FALLBACK,
    "a reason that is not text:");
  eq(planUnreadableReason({}), null, "control: a row with neither key is a plan:");
  eq(planUnreadableReason({ unreadable: null as never }), null, "control: a null reason and no status:");
});

await testAsync("listPlans: a row whose file gives no name is named by its id; every other row as sent", async () => {
  // M3 "name left as sent", observed (12/14 passed; the panel's nameless case
  // below failed with it):
  //   x listPlans: a row whose file gives no name is named by its id; every
  //   other row as sent: the nameless row's name:
  //     expected 0b9e8d7c6a5f4e3d2c1b0a9f8e7d6c5b
  //     got      undefined
  // The shared list is put back in a `finally`: a failure here must not hand
  // the nameless list to every panel case below (it did, under M3, before).
  // DELIBERATE PIN CHANGE (S7, #478): the recording gained a file that is not
  // JSON, which gives no name, so the recorded list read back is no longer
  // the list as sent. Its named rows still are; the nameless one is named by
  // its id, graded here on the server's own row rather than one made by
  // dropping BAD's name. M3 "name left as sent", observed on the re-pin
  // (S7-STORE-mut; 12/14 passed, the panel's nameless case red with it):
  //   x listPlans: a row whose file gives no name is named by its id; every
  //   other row as sent: the recorded row 3a9d7e5c1b0f4e2d8c6b4a2f0e8d6c4b,
  //   read back:
  //     expected {"id":"3a9d7e5c1b0f4e2d8c6b4a2f0e8d6c4b","status":"unreadable",
  //     "unreadable":"not valid JSON","mtime":1790400000,
  //     "name":"3a9d7e5c1b0f4e2d8c6b4a2f0e8d6c4b"}
  //     got      {"id":"3a9d7e5c1b0f4e2d8c6b4a2f0e8d6c4b","status":"unreadable",
  //     "unreadable":"not valid JSON","mtime":1790400000}
  try {
    listNow = LIST.map((r) => ({ ...r }));
    const asSent = await listPlans();
    eq(asSent.length, LIST.length, "the recorded list, read back, has:");
    for (const [i, row] of LIST.entries()) {
      const want = typeof row.name === "string" ? row : { ...row, name: row.id };
      eq(JSON.stringify(asSent[i]), JSON.stringify(want), `the recorded row ${row.id}, read back:`);
    }
    assert(LIST.some((r) => r.name === undefined),
      "precondition: the recording holds no nameless row, so nothing above named one");
    const { name: _dropped, ...nameless } = BAD;
    listNow = [{ ...GOOD }, nameless];
    const read = await listPlans();
    eq(read[1].name, BAD.id, "the nameless row's name:");
    eq(JSON.stringify({ ...read[1], name: undefined }), JSON.stringify({ ...nameless, name: undefined }),
      "the nameless row's other keys:");
    eq(JSON.stringify(read[0]), JSON.stringify(GOOD), "control: the good row:");
  } finally {
    listNow = LIST.map((r) => ({ ...r }));
  }
});

// ================================================================= the panel

await mount(["view.status", "control.capture"]);

await testAsync("precondition and control: the good plan's row is unchanged", () => {
  const row = goodRow();
  assert(row != null, "no row for the good plan - the fixture is wrong, not the panel");
  assert(row.textContent.includes(`${GOOD.targets}t · ${GOOD.frames}f · 40m`),
    `the good row lost its numbers: ${row.textContent}`);
  eq(buttonsIn(row).join(" | "), `load | export | Delete plan ${GOOD.name}`, "the good row's controls:");
});

await testAsync("the unreadable file is listed and counted, never hidden", () => {
  // M1p "row read as a plan" (panel), observed (6/14 passed; the seven panel
  // cases below failed with it, each on "no row for the unreadable file", its
  // "precondition:" form, "no row for the nameless unreadable file" or "a
  // viewer must still see the file, with its reason"):
  //   x the unreadable file is listed and counted, never hidden: no row for
  //   the unreadable file
  const header = [...container.querySelectorAll("button")].find((b: any) =>
    /Saved plans/.test(b.textContent)) as any;
  assert(header != null, "precondition: the saved-plans header is gone");
  // DELIBERATE PIN CHANGE (S7, #478): the recording's three rows, where it
  // held two, pinned to the recording's length rather than a number. F1
  // "unreadable rows filtered out" (the panel keeping only the rows
  // `planUnreadableReason` passes), observed on the re-pin (S7-STORE-mut;
  // 6/14 passed, every case about an unreadable row red with it):
  //   x the unreadable file is listed and counted, never hidden: the files
  //   are not all counted: Saved plans (1)▾
  assert(header.textContent.includes(`Saved plans (${LIST.length})`),
    `the files are not all counted: ${header.textContent}`);
  assert(unreadableRow() != null, "no row for the unreadable file");
});

await testAsync("its row carries its name, the word and the reason as sent, and nothing to take for a plan", () => {
  // M10 "reason dropped" (panel), observed (10/14 passed; the nameless and
  // LOADED-badge cases failed the same way and the viewer's as "the viewer does not see the
  // reason"):
  //   x its row carries its name, the word and the reason as sent, and
  //   nothing to take for a plan: the row's whole text:
  //     expected NGC 7000 Haunreadablefails validation: targets.0.steps.0.
  //     frame_type: frame type 'Snapshot' is not one of Light, Dark, Bias,
  //     Flat (in any case)
  //     got      NGC 7000 Haunreadable
  const row = unreadableRow();
  assert(row != null, "precondition: no row for the unreadable file");
  const t = row.textContent as string;
  assert(!/undefined|NaN/.test(t), `the row reads fields the file does not have: ${t}`);
  assert(!/\d+t · \d+f/.test(t), `the row carries plan numbers: ${t}`);
  // Exact: a date off the file's mtime or a placeholder count would be a
  // claim about a plan nobody can read.
  eq(t, `${BAD_NAME}unreadable${REASON}`, "the row's whole text:");
});

await testAsync("its only control is DELETE: no LOAD, EXPORT or RUN", () => {
  // Exact, so a RUN is refused as surely as a LOAD: the classic panel has no
  // RUN on any row today, and this is the line that would say so if one came.
  const row = unreadableRow();
  assert(row != null, "precondition: no row for the unreadable file");
  eq(buttonsIn(row).join(" | "), `Delete unreadable plan ${BAD_NAME}`, "the row's only control:");
});

await testAsync("control: a DELETE answered no removes nothing", async () => {
  // The confirm is the only thing between this row's one verb and a file
  // that cannot be put back; the case below answers it yes, so without this
  // one a delete that went ahead on either answer passed.
  // V3 "cancel still deletes" (del's `if (!ok) return;` removed), observed
  // (12/12 passed before this case existed; with it, 12/14, the case below
  // failing on "precondition: no row for the unreadable file" because the
  // refused delete had already removed it):
  //   x control: a DELETE answered no removes nothing: answering no must send
  //   nothing
  //     expected 0
  //     got      1
  const row = unreadableRow();
  assert(row != null, "precondition: no row for the unreadable file");
  const del = row.querySelector(`button[aria-label="Delete unreadable plan ${BAD_NAME}"]`);
  assert(del != null, "precondition: no delete control on the row");
  const before = asked.filter((a) => a.method === "DELETE").length;
  await act(async () => { del.dispatchEvent(new win.MouseEvent("click", { bubbles: true })); });
  await settle();
  assert((useStore.getState() as any).confirm != null, "precondition: no confirm was raised");
  await act(async () => { (useStore.getState() as any).resolveConfirm(false); });
  await settle();
  eq(asked.filter((a) => a.method === "DELETE").length - before, 0, "answering no must send nothing");
  assert(unreadableRow() != null, "the row went although the delete was refused");
});

await testAsync("DELETE confirms naming the file, then deletes it and the row goes", async () => {
  // M7 "confirm without the file", observed (13/14 passed):
  //   x DELETE confirms naming the file, then deletes it and the row goes:
  //   the confirm does not name the file it removes: undefined
  const row = unreadableRow();
  assert(row != null, "precondition: no row for the unreadable file");
  const del = row.querySelector(`button[aria-label="Delete unreadable plan ${BAD_NAME}"]`);
  assert(del != null, "no delete control on the row");
  await act(async () => { del.dispatchEvent(new win.MouseEvent("click", { bubbles: true })); });
  await settle();
  const req = (useStore.getState() as any).confirm;
  assert(req != null, "no confirm was raised - delete must never be one tap");
  assert(String(req.title).includes(BAD_NAME), `the confirm does not name the plan: ${req.title}`);
  assert(String(req.body).includes(`${BAD.id}.json`),
    `the confirm does not name the file it removes: ${req.body}`);
  eq(req.body, unreadablePlanDeleteBody(BAD), "the confirm's body:");
  const before = asked.filter((a) => a.method === "DELETE").length;
  await act(async () => { (useStore.getState() as any).resolveConfirm(true); });
  await settle();
  const sent = asked.filter((a) => a.method === "DELETE");
  eq(sent.length, before + 1, "confirming must issue the request");
  eq(sent[sent.length - 1].url, `/api/plans/${BAD.id}`, "and against this file");
  eq(unreadableRow(), null, "the row stayed after its file was deleted");
  assert(goodRow() != null, "control: the good plan's row went with it");
});

await testAsync("a file that gives no name is listed under its id", async () => {
  // M3 "name left as sent", observed here too (cut at "..."; the reason is
  // the fixture's):
  //   x a file that gives no name is listed under its id: the row's whole text:
  //     expected 0b9e8d7c6a5f4e3d2c1b0a9f8e7d6c5bunreadablefails validation: ...
  //     got      unreadablefails validation: ...
  const { name: _dropped, ...nameless } = BAD;
  listNow = [{ ...GOOD }, nameless];
  await mount(["view.status", "control.capture"]);
  const row = unreadableRow();
  assert(row != null, "no row for the nameless unreadable file");
  eq(row.textContent, `${BAD.id}unreadable${REASON}`, "the row's whole text:");
  eq(buttonsIn(row).join(" | "), `Delete unreadable plan ${BAD.id}`, "its delete names:");
});

await testAsync("gated like any plan delete: without control.capture there is none, on either row", async () => {
  // M2 "ungate the unreadable delete", observed (13/14 passed):
  //   x gated like any plan delete: without control.capture there is none, on
  //   either row: a delete control for a viewer on the unreadable row:
  //     expected
  //     got      Delete unreadable plan NGC 7000 Ha
  listNow = LIST.map((r) => ({ ...r }));
  await mount(["view.status"]);
  const row = unreadableRow();
  assert(row != null, "a viewer must still see the file, with its reason");
  assert(row.textContent.includes(REASON), "the viewer does not see the reason");
  eq(buttonsIn(row).join(" | "), "", "a delete control for a viewer on the unreadable row:");
  const ok = goodRow();
  assert(ok != null, "precondition: the good row is gone for a viewer");
  eq(buttonsIn(ok).join(" | "), "load | export",
    "control: the good row's delete is not hidden from a viewer, so the gate compared is wrong:");
});

await testAsync("no LOADED badge on it, even when the editor's plan was loaded from that file", async () => {
  // The editor keeps a plan loaded from a file that has since been damaged,
  // but the tie is gone: the server's `_plan_exists` answers false for that
  // file, so a Save mints a new plan beside it. A LOADED badge would claim a
  // tie the server no longer honours.
  // V9 "badge kept" (the unreadable branch draws the good row's LOADED badge
  // when `r.id === loadedPlanId`), observed (13/14 passed; reason cut at
  // "..."):
  //   x no LOADED badge on it, even when the editor's plan was loaded from
  //   that file: the row's whole text:
  //     expected NGC 7000 Haunreadablefails validation: ...(in any case)
  //     got      NGC 7000 Haunreadableloadedfails validation: ...(in any case)
  listNow = LIST.map((r) => ({ ...r }));
  await mount(["view.status", "control.capture"], GOOD.id);
  const good = goodRow();
  assert(good != null, "precondition: no row for the good plan");
  // Control: this harness does see the badge where it belongs, so its absence
  // below is the panel's answer, not a harness that cannot see one.
  assert(/loaded/.test(good.textContent), `control: the loaded good plan shows no badge: ${good.textContent}`);
  await mount(["view.status", "control.capture"], BAD.id);
  const row = unreadableRow();
  assert(row != null, "precondition: no row for the unreadable file");
  eq(row.textContent, `${BAD_NAME}unreadable${REASON}`, "the row's whole text:");
});

await act(async () => { root.unmount(); });

// ------------------------------------------------------------------ summary
const total = passed + failed;
console.log(`planLibraryUnreadable: ${passed}/${total} passed`);
for (const f of failures) console.error(f);
export const result = { passed, failed, total };
