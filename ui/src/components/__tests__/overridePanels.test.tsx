// overridePanels.test.tsx — the INTEGRATION half of #129: do the panels that
// showed the losing layer now show the winning one, and say whose it is?
//
//   Run directly:  npx tsx src/components/__tests__/overridePanels.test.tsx
//
// The unit tests next door prove the resolver picks the right layer and the
// disclosure component prints the right sentence. Neither can catch the bug
// that actually shipped, which was a WIRING bug: a panel reading the correct
// data structure from the wrong key. So these render the real TasksPanel and
// the real OpticsPanel against a config shaped exactly like /api/config's, with
// a profile pinning the simulator for polar alignment and replacing the whole
// optics block — the literal twelve-day scenario — and read the output.
//
// No jsdom and no layout: renderToStaticMarkup, and every assertion is about
// text or an attribute. What is asserted is what a user could read off the
// screen.
//
// The globals below are stubs, installed before the store is imported because
// the API client reads `window.location` at module scope. This file is run in
// its own process by run-tests.mjs, which is why that is safe here.

/* eslint-disable @typescript-eslint/no-explicit-any */
const g = globalThis as any;
g.window = {
  location: { pathname: "/", href: "http://local/", protocol: "http:", host: "local" },
  addEventListener() {},
  removeEventListener() {},
  matchMedia: () => ({
    matches: false, addEventListener() {}, removeEventListener() {},
    addListener() {}, removeListener() {},
  }),
  localStorage: { getItem: () => null, setItem() {}, removeItem() {} },
  setTimeout, clearTimeout,
};
g.localStorage = g.window.localStorage;
g.document = {
  documentElement: {
    dataset: {}, style: { setProperty() {} },
    classList: { add() {}, remove() {}, toggle() {} },
  },
  addEventListener() {}, removeEventListener() {}, body: {},
};
g.WebSocket = class { close() {} addEventListener() {} send() {} };

const { createElement } = await import("react");
const { renderToStaticMarkup } = await import("react-dom/server");
const { useStore } = await import("../../store");
const TasksPanel = (await import("../equipment/TasksPanel")).default;
const OpticsPanel = (await import("../settings/OpticsPanel")).default;
const { BANNER_KEYS } = await import("../settings/OpticsPanel");
const { OPTICS_KEYS } = await import("../../lib/effective");

// ------------------------------------------------------------------ harness
let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }

const entry = (p: Record<string, unknown>) => ({
  value: null, layer: "default", profile: null, config: null, default: null,
  profile_id: null, profile_name: null, reason: null, ...p,
});

const PROFILE = { profile_id: "p1", profile_name: "Backyard rig" };

/** /api/config as the server sends it under the twelve-day scenario: global
 *  config says AstroDeck native + a 530 mm scope; the ACTIVE PROFILE pins the
 *  simulator for polar align and carries a whole 250 mm optics block. */
const CONFIG = {
  version: 3,
  site: {},
  optics: {
    focal_length_mm: 530, pixel_size_um: 3.76, sensor_width_px: 6248,
    sensor_height_px: 4176, auto_from_camera: false,
    guide_focal_length_mm: null, telescope_name: "Askar FRA400",
  },
  optics_computed: { image_scale_arcsec_px: 1.98 },
  active_profile_id: "p1",
  providers: { autofocus: "auto", polar_align: "astrodeck", solve: "auto", guide: "auto" },
  effective: {
    "providers.autofocus": entry({ value: "auto", layer: "default", config: "auto" }),
    "providers.polar_align": entry({
      value: "sim", layer: "profile", profile: "sim", config: "astrodeck", ...PROFILE,
      reason: "override: profile 'Backyard rig' pins 'sim' for this capability",
    }),
    "providers.solve": entry({ value: "auto", layer: "default", config: "auto" }),
    "providers.guide": entry({ value: "auto", layer: "default", config: "auto" }),
    "optics.focal_length_mm": entry({ value: 250, layer: "profile", profile: 250, config: 530, ...PROFILE }),
    "optics.telescope_name": entry({ value: "", layer: "profile", profile: "", config: "Askar FRA400", ...PROFILE }),
    "optics.pixel_size_um": entry({ value: 2.4, layer: "profile", profile: 2.4, config: 3.76, ...PROFILE }),
    "optics.sensor_width_px": entry({ value: 4144, layer: "profile", profile: 4144, config: 6248, ...PROFILE }),
    "optics.sensor_height_px": entry({ value: 2822, layer: "profile", profile: 2822, config: 4176, ...PROFILE }),
    "optics.auto_from_camera": entry({ value: true, layer: "profile", profile: true, config: false, ...PROFILE }),
    "optics.guide_focal_length_mm": entry({ value: 200, layer: "profile", profile: 200, config: null, ...PROFILE }),
    // The two fields wave 2 added to the optics block. The server builds this
    // map from `Optics.model_fields` (`provenance.py:79`), so a real rig sends
    // them; the banner had no row for either until the wave-2 review.
    "optics.aperture_mm": entry({ value: 106, layer: "profile", profile: 106, config: 72, ...PROFILE }),
    "optics.reducer": entry({ value: 0.8, layer: "profile", profile: 0.8, config: 1, ...PROFILE }),
  },
};

const STATUS = {
  providers: {
    autofocus: { kind: "astrodeck", label: "AstroDeck native", reason: "native autofocus" },
    polar_align: { kind: "sim", label: "Simulator", reason: "override: built-in simulator" },
    solve: { kind: "astap", label: "ASTAP", reason: "the local ASTAP binary" },
    // The guide row as hub.poll_status sends it (providers.guide_provider_options):
    // this rig has an imaging camera but NO guide camera assigned, so the native
    // guider is NOT selectable — the case that used to be OFFERED anyway and then
    // silently discarded by the resolver.
    guide: {
      kind: "backend", label: "PHD2",
      reason: "no guide camera connected — using the PHD2 bridge",
      eligible: ["auto", "backend"],
      options: [
        { value: "auto", eligible: true, reason: null },
        {
          value: "astrodeck", eligible: false,
          reason: "AstroDeck native needs a guide camera assigned and connected — "
            + "assign one to the guide camera role on Equipment",
        },
        { value: "backend", eligible: true, reason: null },
      ],
    },
  },
};

// zustand feeds React's SERVER snapshot from `getInitialState()`, which returns
// the object captured when the store was created — `setState` replaces the
// state object and leaves that one behind, so a plain setState is invisible to
// renderToStaticMarkup. Mutate the initial object in place instead. Without
// this the panels render as if nothing had loaded, and every assertion below
// would pass against an empty page for the wrong reason.
Object.assign(useStore.getInitialState(), {
  config: CONFIG as never,
  status: STATUS as never,
  principal: { caps: ["config.backend", "config.site_optics"] } as never,
});

const html = (el: unknown) => renderToStaticMarkup(el as never);
const text = (h: string) =>
  h.replace(/<[^>]+>/g, " ")
    .replace(/&quot;/g, '"').replace(/&#x27;/g, "'").replace(/&amp;/g, "&")
    .replace(/\s+/g, " ").trim();

const tasksHtml = html(createElement(TasksPanel as never, { drivers: [] } as never));
const tasksText = text(tasksHtml);
const opticsHtml = html(createElement(OpticsPanel as never));
const opticsText = text(opticsHtml);

// ----------------------------------------------------- the harness is honest
// A blank render would satisfy several "does not contain" assertions below.
test("GUARD: the panels actually rendered with the seeded config", () => {
  assert(tasksText.includes("Polar align"), `tasks panel rendered: ${tasksText.slice(0, 200)}`);
  assert(
    !opticsText.includes("Loading optics"),
    `optics panel got its config: ${opticsText.slice(0, 200)}`,
  );
});

// ------------------------------------------------------------- TasksPanel

test("the Polar align row shows the SIMULATOR, not global's AstroDeck native", () => {
  // The exact control that displayed the wrong provider for twelve days. The
  // select's value is the winning layer's.
  assert(
    /<option[^>]*value="sim"[^>]*selected/.test(tasksHtml) ||
      /value="sim"[^>]*selected/.test(tasksHtml),
    `the sim value is the selected one: ${tasksHtml.slice(tasksHtml.indexOf("Polar align"), tasksHtml.indexOf("Polar align") + 900)}`,
  );
});

test("the row names the profile and the value it is shadowing", () => {
  assert(tasksText.includes("Backyard rig"), `names the profile: ${tasksText}`);
  assert(tasksText.includes("astrodeck"), `names what would run without it: ${tasksText}`);
});

test("a pinned row offers the way out", () => {
  assert(tasksText.includes("Clear the profile pin"), `offers the clear: ${tasksText}`);
});

test("a pinned select is EDITABLE — the save is routed to the pin (#132)", () => {
  // It used to be disabled, because the only write route targeted the GLOBAL
  // block that the profile beats: a save that succeeded and changed nothing was
  // worse than the display bug. There is now a route that writes the profile's
  // own providers entry, so the row edits the layer it displays instead of
  // making the user destroy the pin in order to change it.
  const row = tasksHtml.slice(tasksHtml.indexOf("Polar align"));
  const select = row.slice(row.indexOf("<select"), row.indexOf("</select>"));
  assert(!select.includes("disabled"), `the pinned select is live: ${select}`);
});

test("the pinned row SAYS where the save will land, before it is made", () => {
  // The one fact a user cannot get any other way. Getting it wrong is how the
  // original bug felt: pick, save, nothing changes, no explanation.
  assert(
    tasksText.includes("Saving changes the profile “Backyard rig”, not the global setting"),
    `states the write target: ${tasksText}`,
  );
  // ...and only on the pinned row. Saying it under all four would be copy
  // describing the default mental model, which is how a line stops being read.
  assert(
    (tasksText.match(/Saving changes the profile/g) ?? []).length === 1,
    `stated exactly once: ${tasksText}`,
  );
});

test("UNpinned rows stay editable and carry no override copy", () => {
  const row = tasksHtml.slice(tasksHtml.indexOf("Autofocus"));
  const select = row.slice(row.indexOf("<select"), row.indexOf("</select>"));
  assert(!select.includes("disabled"), `autofocus is still editable: ${select}`);
  // exactly one PROFILE chip in the whole panel — the polar row's
  assert(
    (tasksHtml.match(/layer-chip-profile/g) ?? []).length === 1,
    "only the pinned row is badged",
  );
});

// ----------------------------------------------- the fourth row (UX-02)

test("Equipment carries a GUIDING row — the fourth pinnable capability", () => {
  // It existed only on the Guide view, i.e. nowhere on the screen whose entire
  // subject is task routing. A user concluded the product could not guide.
  assert(tasksText.includes("Guiding"), `the guide row renders: ${tasksText}`);
  assert(
    tasksHtml.includes('aria-label="Guide provider override"'),
    "the guide choice group is present and named",
  );
});

test("an ineligible guide provider is shown WITH ITS REASON, not omitted", () => {
  // The correctness half: the offer used to accept the imaging camera while the
  // resolver required a guide camera, so "AstroDeck native" was offered, the
  // write succeeded, and the badge came back PHD2. Now it is offered as blocked,
  // and the blocker says what to do about it.
  assert(
    tasksText.includes("AstroDeck native needs a guide camera assigned and connected"),
    `the blocker is stated in words: ${tasksText}`,
  );
  const group = tasksHtml.slice(tasksHtml.indexOf('aria-label="Guide provider override"'));
  assert(
    /aria-disabled="true"/.test(group),
    "the blocked chip is aria-disabled, not natively disabled",
  );
  // Scan the WHOLE group. This previously searched
  // `group.slice(0, chip.length + 800)` where `chip` came from
  // `group.indexOf("astrodeck")` — but the chips render LABELS, not values, so
  // that indexOf was -1, `chip` was the whole group, and the window happened to
  // land at 801 characters of a 2531-character group. It covered the first chip
  // and a half, never reached the third, and passed by arithmetic accident. A
  // test that cannot fail on the thing it names is worse than no test, because
  // it is counted as coverage.
  assert(
    !/<button[^>]*\sdisabled/.test(group),
    "no native disabled attribute anywhere in the group",
  );
});

test("the panel says up-front that the rig is running on profile pins", () => {
  assert(
    tasksText.includes("routed by the active equipment profile"),
    `panel-level banner present: ${tasksText}`,
  );
});

test("the simulator badge is visually distinct from the native one", () => {
  // The pairing that went unnoticed. Shape, not hue: the sim chip carries its
  // own class, and the pinned chip carries the footnote marker.
  assert(tasksHtml.includes("prov-sim"), `sim variant applied: ${tasksHtml}`);
  assert(tasksHtml.includes("prov-pin"), `profile-pin marker applied: ${tasksHtml}`);
  assert(
    tasksHtml.includes("SIMULATED, not a real device"),
    "the accessible name says it out loud",
  );
});

// ------------------------------------------------------------- OpticsPanel

test("the optics banner names the profile and both focal lengths", () => {
  assert(opticsText.includes("Backyard rig"), `names the profile: ${opticsText}`);
  assert(opticsText.includes("250 mm"), `what runs: ${opticsText}`);
  assert(opticsText.includes("530 mm"), `what the panel shows: ${opticsText}`);
});

test("the banner explains the WHOLE-BLOCK swap, which is why a scope name vanished", () => {
  assert(
    opticsText.includes("replaces every field at once"),
    `states the whole-block rule: ${opticsText}`,
  );
  // telescope_name: the profile never set one, and the global "Askar FRA400" is
  // shadowed by the profile's empty default. That is the surprise worth naming.
  assert(opticsText.includes("Askar FRA400"), `names the shadowed value: ${opticsText}`);
  assert(
    opticsText.includes("Telescope name: not set"),
    `lists the field AND what it became: ${opticsText}`,
  );
  // The banner covers all seven fields, not the five the user was looking at:
  // the whole point of a whole-block swap is that it reaches ones nobody
  // thought they were changing.
  assert(
    opticsText.includes("Guide scope focal length: 200 mm"),
    `covers the fields outside the visible form too: ${opticsText}`,
  );
});

test("the banner lists EVERY field the block carries, not the ones it used to", () => {
  // The banner's whole claim is "a profile optics block replaces every field at
  // once, including the ones it never set" - so a field missing from this list
  // is the banner making that claim and then not keeping it. `aperture_mm` and
  // `reducer` were exactly that from the day wave 2 added them until the wave-2
  // review. Derived from OPTICS_KEYS so a TENTH field fails here too.
  const listed = BANNER_KEYS.map(([k]) => k);
  for (const k of OPTICS_KEYS) {
    assert(listed.includes(k), `the banner has no row for optics.${k}`);
  }
  assert(
    listed.length === OPTICS_KEYS.length,
    `the banner lists ${listed.length} rows for ${OPTICS_KEYS.length} fields`,
  );
  // And they reach the rendered output, with their units, not just the table.
  assert(opticsText.includes("Aperture: 106 mm"), `aperture row rendered: ${opticsText}`);
  assert(opticsText.includes("Reducer: 0.8x"), `reducer row rendered: ${opticsText}`);
  assert(
    opticsText.includes("this panel shows 72 mm"),
    `and each names what this panel would have shown: ${opticsText}`,
  );
});

test("the optics panel offers the way out", () => {
  assert(
    opticsText.includes("Drop the profile's optics"),
    `offers the clear: ${opticsText}`,
  );
});

test("Save warns that saving will not change what the rig runs", () => {
  // The moment the false belief gets formed: a green "Saved" beside numbers the
  // rig will never read.
  assert(
    opticsText.includes("will keep overriding them"),
    `honest save copy: ${opticsText}`,
  );
});

test("each box carries a TERSE running-value note, not a copy of the banner", () => {
  // Field-local fact only. Restating the why under all seven fields turned the
  // panel into a wall the eye slides off, which is the same failure as saying
  // nothing at all.
  assert(opticsText.includes("Rig runs 250 mm"), `per-field disclosure: ${opticsText}`);
  assert(opticsText.includes("Rig runs 2.4 µm"), `and on the sensor fields too`);
  assert(
    (opticsText.match(/replaces every field at once/g) ?? []).length === 1,
    "the whole-block explanation is stated exactly once",
  );
  assert(
    (opticsText.match(/Backyard rig/g) ?? []).length <= 3,
    `the profile name is not repeated under every field: ${opticsText}`,
  );
});

test("the panel header carries the layer chip", () => {
  assert(opticsHtml.includes("layer-chip-profile"), "header chip present");
});

// ----------------------------------------------------------------- report
const total = passed + failed;
// eslint-disable-next-line no-console
console.log(`\noverridePanels.test: ${passed}/${total} passed`);
if (failures.length) {
  // eslint-disable-next-line no-console
  console.error(failures.join("\n"));
}

export const result = { passed, failed, total };
