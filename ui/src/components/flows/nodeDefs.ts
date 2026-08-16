// nodeDefs.ts — the Flows node vocabulary. Nineteen node types, and every
// other file on this surface reads them from here.
//
// THIS IS A SECOND TRANSCRIPTION, AND THAT IS THE WHOLE RISK.
// The server already owns the same table in server/astrodeck/flows/nodes.py,
// and NO endpoint serves it — MILESTONE2-CONTRACT.md §G-4 records that as an
// open question and this file as the consequence. Two hand-maintained copies of
// one contract drift silently: a port id that differs by a letter draws a wire
// the compiler will refuse, a default that drifts from `120` to `"120"` breaks
// the param coercion rule below, and neither shows up as an error anywhere.
// __tests__/nodeDefs.test.ts therefore PARSES nodes.py and compares — it is not
// a table of expectations typed out a third time. Do not weaken it into one.
//
// WHERE EACH FIELD COMES FROM (sources ranked as in the handoff brief):
//   type/label/cat/ins/outs/params  — nodes.py AND the prototype's DEFS, which
//                                     agree codepoint-for-codepoint (§C.0).
//   colorVar                        — README §"Design tokens" via §C.0's table.
//   fields/desc/sum                 — the prototype's DEFS (lines 658-754) ONLY.
//                                     nodes.py's NodeDef has no such fields;
//                                     this file is their first and only home.
//
// PARAM TYPES ARE LOAD-BEARING. `flowsSetParam` coerces user input by the type
// of the DEFAULT (contract §B.2, prototype lines 1047-1055): a numeric default
// makes the field numeric and reverts unparseable input to the default; a string
// default passes text through. That is why `capture.bin` is the STRING "1" and
// not the number 1 — binning is a select over "1"/"2"/"4" and a numeric default
// would silently rewrite the operator's choice. Changing a default's type here
// changes the behaviour of an input control three files away.
import type { FlowNodeType, PortKind } from "./flowsTypes";

/** One port on a node.
 *
 *  `kind` is the grammar, not decoration: a `flow` port carries the single run
 *  cursor ("then"), an `event` port fires any number of times ("whenever"), and
 *  nodes.py's module docstring is explicit that the two run through completely
 *  different engine machinery. Wiring is only ever kind-to-kind. */
export interface PortDef {
  id: string;
  label: string;
  kind: PortKind;
  /** May be left unwired without the doctor complaining.
   *
   *  Only `calib.panel` today (nodes.py `optional_ins=frozenset({"panel"})`): a
   *  calibration queue with no flat panel is a working queue that skips flats,
   *  and doctor rule 7 says that in its own words rather than as an
   *  "unwired input" complaint that would read like a mistake. */
  optional?: true;
}

/** One editable row in the inspector / edit sheet.
 *
 *  THREE controls exist, and the third arrived with the 2026-08-14 export.
 *  `select` is a closed option list, `text` is a free box, and neither has
 *  validation, min/max or a disabled state anywhere in the design's inspector
 *  (§C.8) — do not add one.
 *
 *  `cycleplan` is the exception, and it earns it by REMOVING a way to be wrong.
 *  The export requires the FILTER CYCLE's slot table to be "one row per filter
 *  in the RIG'S WHEEL (from the equipment panel - never a hand-typed filter
 *  name)". A text box there lets an operator type `Hα` or `OIII` on a rig whose
 *  wheel says `Oiii`, and the mismatch surfaces at 21:00 as a filter change that
 *  never happens. Rows come from the wheel, so an unreachable filter cannot be
 *  entered — the storage format stays the same string either way. */
export interface FieldDef {
  key: string;
  label: string;
  control: "select" | "text" | "cycleplan";
  /** Present iff `control === "select"`. The closed set of choices. */
  options?: readonly string[];
  /** Suffix rendered after the control, e.g. `min`, `°`, `h (0 = none)`. */
  unit?: string;
}

export interface NodeDef {
  type: FlowNodeType;
  label: string;
  cat: "SOURCE" | "RIG" | "LOGIC" | "ACTION" | "SINK";
  /** The CSS custom-property NAME (with the leading `--`), used as
   *  `var(${def.colorVar})`.
   *
   *  PER-NODE, NOT PER-CATEGORY, and that is deliberate. The server's
   *  `CATEGORY_TOKEN` maps ACTION → `--warn` flat, but README §"Design tokens"
   *  says "actions `--warn`, **abort `--bad`**". ABORT + PARK parks the mount
   *  and warms the camera; drawing it the same amber as NOTIFY would make the
   *  one destructive node in the vocabulary look like a message. A category
   *  lookup cannot express that exception, so this field exists. §C.0. */
  colorVar: string;
  /** Input ports, in the order they are drawn. Inputs render before outputs,
   *  never interleaved (§C.5). */
  ins: readonly PortDef[];
  /** Output ports, in the order they are drawn. */
  outs: readonly PortDef[];
  /** Starting parameters for a freshly dropped node. Types matter — see the
   *  file header. */
  params: Readonly<Record<string, string | number>>;
  /** Editable rows, in the order the inspector lists them (§C.8 item 4:
   *  "one FlowFieldRow per def.fields, in DEFS order"). */
  fields: readonly FieldDef[];
  /** One line of prose: the rail's `title=`, and the inspector's description. */
  desc: string;
  /** The node card's footer line, computed from the CURRENT params. */
  sum: (p: Record<string, string | number>) => string;
}

// ---------------------------------------------------------------- port sugar
// `_f`/`_e` mirror nodes.py's helpers of the same names, so the two tables read
// alike side by side when someone diffs them by eye.
const _f = (id: string, label: string): PortDef => ({ id, label, kind: "flow" });
const _e = (id: string, label: string): PortDef => ({ id, label, kind: "event" });

// ------------------------------------------------------------- summary sugar
// The prototype's `sum()` bodies do bare `p.foo.toLowerCase()` on values typed
// here as `string | number`, so each needs a cast at minimum. These helpers do
// that AND swallow a missing key: models.py validates params permissively on
// purpose (a vocabulary that gains a field must not make every saved flow
// unopenable), so a graph saved before a field existed reaches this code with
// the key absent. The prototype would print the literal word "undefined" into
// the card footer; "" is the honest rendering of a value that is not there.
const txt = (v: string | number | undefined): string => String(v ?? "");
const low = (v: string | number | undefined): string => txt(v).toLowerCase();
/** Only ever used for a SIGN TEST, never for display — the displayed value is
 *  always the raw param, so `-30` prints as `-30` exactly as it was typed. */
const num = (v: string | number | undefined): number =>
  typeof v === "number" ? v : parseFloat(txt(v));

/** One slot of a FILTER CYCLE plan: which filter, and for how long. */
export interface CycleSlot { filter: string; exposure_s: number }

/** Decode the FILTER CYCLE slot table.
 *
 *  Mirrors the server's `nodes.parse_cycle_plan` and the prototype's
 *  `parsePlan`, INCLUDING the tolerant regex: anchored at the start, whole
 *  seconds, trailing text ignored. A parser stricter than the writer would
 *  silently empty a table that the server reads perfectly well, and the card
 *  footer would announce "0 filters" for a cycle that runs seven.
 *
 *  Unparseable entries are DROPPED rather than defaulted, for the same reason
 *  the server drops them: a slot nobody can read is a slot nobody can shoot, and
 *  inventing an exposure for it puts frames on disk under a filter the operator
 *  never asked for. */
export function parseCyclePlan(plan: string | number | undefined): CycleSlot[] {
  const out: CycleSlot[] = [];
  for (const chunk of txt(plan).split(",")) {
    const m = /^(\S+)\s+(\d+)/.exec(chunk.trim());
    if (m) out.push({ filter: m[1], exposure_s: Number(m[2]) });
  }
  return out;
}

/** Re-encode a slot table, preserving the caller's order.
 *
 *  The editor always hands these in WHEEL order, because the row order is the
 *  shooting order and re-sorting here would re-plan the night from a place
 *  nobody would think to look. */
export function formatCyclePlan(slots: readonly CycleSlot[]): string {
  return slots.map((s) => `${s.filter} ${s.exposure_s}`).join(", ");
}

/** The whole vocabulary. Grouped and ordered as nodes.py declares them
 *  (SOURCES → EQUIPMENT → RIG OPS → LOGIC → ACTIONS + SINKS); note this is a
 *  declaration order for reading, NOT the palette's item order, which is
 *  disputed between the prototype and the server and lives in palette.ts
 *  (§G-3). Nothing should iterate `Object.keys(NODE_DEFS)` to draw a menu. */
export const NODE_DEFS: Record<FlowNodeType, NodeDef> = {
  // ---------------------------------------------------------------- SOURCES
  dusk: {
    type: "dusk",
    label: "DUSK WINDOW",
    cat: "SOURCE",
    colorVar: "--accent",
    ins: [],
    outs: [_f("window", "window opens"), _e("nightend", "night ends")],
    params: { start: "Astro dusk", offset: -30, stop: "Dawn", minAlt: 30, repeat: "Single night" },
    fields: [
      { key: "start", label: "Start", control: "select", options: ["Astro dusk", "Nautical dusk", "Civil dusk", "Clock time"] },
      { key: "offset", label: "Offset", control: "text", unit: "min" },
      { key: "stop", label: "Stop", control: "select", options: ["Dawn", "Clock time", "None"] },
      { key: "minAlt", label: "Min target altitude", control: "text", unit: "°" },
      { key: "repeat", label: "Repeat", control: "select", options: ["Single night", "Nightly until pool complete", "Nightly ×30"] },
    ],
    desc: "Autorun window from the scheduler: sun-altitude dusk/dawn events at the configured site, with a per-target altitude gate. 'Night ends' fires before dawn; with Repeat set, dawn is a scheduled hold - the capture cursor persists and the flow re-arms at the next dusk, mid-cycle.",
    // The "+" is printed only for a non-negative offset, so the default -30
    // reads "Astro dusk -30m → dawn" rather than "+-30m". The "· nightly" tail
    // appears only for a repeat, because on a single night it would be noise on
    // every card in the library.
    sum: (p) => txt(p.start) + " " + (num(p.offset) >= 0 ? "+" : "") + txt(p.offset) + "m → " + low(p.stop)
      + (p.repeat && txt(p.repeat) !== "Single night" ? " · nightly" : ""),
  },
  target: {
    type: "target",
    label: "TARGET",
    cat: "SOURCE",
    colorVar: "--accent",
    ins: [_f("arm", "arm")],
    outs: [_f("target", "target")],
    // RA/Dec are TEXT, in the sexagesimal forms the server's parser accepts —
    // including the typographic prime/double-prime and U+2212 minus that these
    // very defaults carry. (parse_dec could not read them until 136be93.)
    params: { name: "M31 - Andromeda", ra: "00h 42m 44s", dec: "+41° 16′ 09″", rotation: 23.4 },
    fields: [
      { key: "name", label: "Name", control: "text" },
      { key: "ra", label: "RA", control: "text" },
      { key: "dec", label: "Dec", control: "text" },
      { key: "rotation", label: "Camera angle", control: "text", unit: "°" },
    ],
    desc: "A plan target from the Sky Atlas: coordinates, camera angle, and identity for the multi-night session ledger.",
    sum: (p) => txt(p.name),
  },
  safety: {
    type: "safety",
    label: "SAFETY MONITOR",
    cat: "SOURCE",
    colorVar: "--accent",
    ins: [],
    outs: [_e("unsafe", "unsafe")],
    params: { source: "Cloud + rain sensor", watch: "Clouds + rain + wind (standalone)", stale: "Unsafe (fail closed)" },
    fields: [
      { key: "source", label: "Source", control: "select", options: ["Cloud + rain sensor", "Weather API", "Manual switch"] },
      // WHAT THIS TIER CLAIMS. Safety aborts and never holds; CLOUD WATCH holds
      // and never aborts. Both watching clouds means they race and the
      // recoverable one always loses, so this scopes safety off clouds when a
      // CLOUD WATCH is present (doctor rule 13).
      { key: "watch", label: "Watch for", control: "select", options: ["Rain + wind + power (pair with Cloud Watch)", "Clouds + rain + wind (standalone)"] },
      // A one-option select, on purpose: fail-closed is not negotiable, and a
      // control that shows the rule and offers no way out says so louder than
      // no control at all.
      { key: "stale", label: "Stale reading is", control: "select", options: ["Unsafe (fail closed)"] },
    ],
    desc: "Fail-closed safety gate for NON-RECOVERABLE conditions - it aborts, it never holds. Scope it to rain/wind/power and let CLOUD WATCH ride out clouds; watching clouds here too makes them race, and safety always wins. A stale reading is UNSAFE, never safe.",
    // The scope, not the source: which sensor it reads matters less on a 188px
    // card than which failures it will end the night for.
    sum: (p) => (txt(p.watch).indexOf("Rain") === 0 ? "rain/wind/power" : "clouds/rain/wind") + " · fail closed",
  },
  cloudwatch: {
    type: "cloudwatch",
    label: "CLOUD WATCH",
    cat: "SOURCE",
    colorVar: "--accent",
    ins: [],
    outs: [_e("in", "clouds in"), _e("clear", "clouds clear")],
    params: { source: "Sky quality sensor", threshold: 40, clearFor: 4 },
    fields: [
      { key: "source", label: "Source", control: "select", options: ["Sky quality sensor", "Weather API", "IR all-sky camera"] },
      { key: "threshold", label: "Clouds in above", control: "text", unit: "% cover" },
      { key: "clearFor", label: "Clear must hold", control: "text", unit: "min" },
    ],
    desc: "Transient-weather sensor, distinct from the fail-closed safety monitor: 'clouds in' fires when cover crosses the threshold; 'clouds clear' fires after the sky has stayed clear for the debounce time.",
    sum: (p) => "in >" + txt(p.threshold) + "% · clear " + txt(p.clearFor) + "m",
  },
  // -------------------------------------------------------------- EQUIPMENT
  // `cat` is RIG for both of these while the palette files them under
  // EQUIPMENT. Both sides agree and it is not a bug (§C.8 item 1) — the
  // category chip in the inspector reads RIG.
  dome: {
    type: "dome",
    label: "DOME CONTROL",
    cat: "RIG",
    colorVar: "--sky",
    // THE ONLY NODE WHOSE PORT ID AND LABEL DIFFER: the id is `run` (so the
    // generic flow-wiring code sees the same id it sees everywhere) while the
    // operator reads "open". Renaming the id to match the label would silently
    // orphan every saved edge that targets `dome|run`.
    ins: [_f("run", "open")],
    outs: [_f("open", "shutter open")],
    // BIND, never "slave" — the 2026-08-14 do-not list names UI labels, code
    // identifiers, API fields and comments, and a param key is all four at once.
    params: { bind: "Bind to mount", onUnsafe: "Close (fail closed)", timeout: 120 },
    fields: [
      { key: "bind", label: "Azimuth", control: "select", options: ["Bind to mount", "Manual"] },
      { key: "onUnsafe", label: "On unsafe", control: "select", options: ["Close (fail closed)"] },
      { key: "timeout", label: "Shutter timeout", control: "text", unit: "s" },
    ],
    desc: "Opens the shutter and binds the dome to the mount. Closing is fail-closed: an unsafe or stale safety reading closes the shutter regardless of pipe state.",
    sum: (p) => low(p.bind) + " · fail closed",
  },
  flatpanel: {
    type: "flatpanel",
    label: "FLAT PANEL",
    cat: "RIG",
    colorVar: "--sky",
    ins: [],
    outs: [_e("ready", "panel ready")],
    params: { position: "Dust-cover panel", adu: 28500, solve: "Solve per filter" },
    fields: [
      { key: "position", label: "Position", control: "select", options: ["Dust-cover panel", "Dome-mounted", "Handheld"] },
      { key: "adu", label: "ADU target", control: "text" },
      { key: "solve", label: "Brightness", control: "select", options: ["Solve per filter", "Fixed"] },
    ],
    desc: "A dimmable flat panel (dust-cover, dome-mounted or handheld). Wire 'panel ready' into a calibration queue to unlock flats; brightness is solved per filter to the ADU target.",
    sum: (p) => low(p.position) + " · " + txt(p.adu) + " ADU",
  },
  // ---------------------------------------------------------------- RIG OPS
  slew: {
    type: "slew",
    label: "SLEW + CENTER",
    cat: "RIG",
    colorVar: "--sky",
    ins: [_f("run", "run")],
    outs: [_f("centered", "centered")],
    params: { tol: 0.5, retries: 3, solver: "ASTAP" },
    fields: [
      { key: "tol", label: "Tolerance", control: "text", unit: "′" },
      { key: "retries", label: "Max iterations", control: "text" },
      { key: "solver", label: "Solver", control: "select", options: ["ASTAP", "NINA (bridge)", "Simulator"] },
    ],
    desc: "Slew to the target, then plate-solve and iterate until pointing is within tolerance (ASTAP).",
    sum: (p) => "±" + txt(p.tol) + "′ · " + txt(p.solver),
  },
  autofocus: {
    type: "autofocus",
    label: "AUTOFOCUS",
    cat: "RIG",
    colorVar: "--sky",
    ins: [_f("run", "run")],
    outs: [_f("focused", "focused")],
    params: { method: "V-curve sweep", step: 12, samples: 9 },
    fields: [
      { key: "method", label: "Method", control: "select", options: ["V-curve sweep", "Native (delegate)"] },
      { key: "step", label: "Step size", control: "text" },
      { key: "samples", label: "Samples", control: "text" },
    ],
    desc: "V-curve sweep: measure median HFR per step, fit a parabola, approach best focus from below for backlash consistency.",
    sum: (p) => txt(p.method) + " · " + txt(p.samples) + " pts",
  },
  guide: {
    type: "guide",
    label: "GUIDE",
    cat: "RIG",
    colorVar: "--sky",
    ins: [_f("run", "run")],
    outs: [_f("guiding", "guiding")],
    params: { provider: "PHD2", settle: 1.5, dither: 3 },
    fields: [
      { key: "provider", label: "Provider", control: "select", options: ["PHD2", "NINA (bridge)", "Simulator"] },
      { key: "settle", label: "Settle below", control: "text", unit: "″" },
      { key: "dither", label: "Dither every", control: "text", unit: "frames" },
    ],
    desc: "Start guiding and wait for settle. RMS is reported in arcseconds; dither cadence is applied between frames.",
    sum: (p) => txt(p.provider) + " · settle " + txt(p.settle) + "″",
  },
  capture: {
    type: "capture",
    label: "CAPTURE LOOP",
    cat: "RIG",
    colorVar: "--sky",
    ins: [_f("run", "run")],
    // The only node with one port of each kind on the same side: `complete`
    // advances the run cursor once, `frame` fires per graded frame and is what
    // a CONDITION listens to.
    outs: [_f("complete", "complete"), _e("frame", "frame graded")],
    // `bin` IS A STRING. See the file header — it is a select and a numeric
    // default would make flowsSetParam coerce the operator's choice away.
    params: { filter: "L", exposure: 120, gain: 100, bin: "1", count: 24, reject: 3.5, goal: 12 },
    fields: [
      { key: "filter", label: "Filter", control: "select", options: ["L", "R", "G", "B", "Ha", "OIII", "SII"] },
      { key: "exposure", label: "Exposure", control: "text", unit: "s" },
      { key: "gain", label: "Gain", control: "text" },
      { key: "bin", label: "Binning", control: "select", options: ["1", "2", "4"] },
      { key: "count", label: "Count", control: "text", unit: "frames" },
      { key: "reject", label: "Reject HFR above", control: "text", unit: "″" },
      // The unit string really is "h (0 = none)" — the zero sentinel has to be
      // legible at the control, because "0 hours" otherwise reads as "shoot
      // nothing" rather than "no integration goal".
      { key: "goal", label: "Integration goal", control: "text", unit: "h (0 = none)" },
    ],
    desc: "An exposure step: loop count frames through the filter with per-filter focus offsets. Each frame is graded (HFR) and emits an event.",
    sum: (p) => txt(p.filter) + " · " + txt(p.exposure) + "s · g" + txt(p.gain) + " · ×" + txt(p.count),
  },
  cycle: {
    type: "cycle",
    label: "FILTER CYCLE",
    cat: "RIG",
    colorVar: "--sky",
    // A SIBLING OF CAPTURE LOOP, not a container around one. The 2026-08-14
    // export: "no loop construct exists at graph level - the graph stays
    // acyclic, loops live inside stages". So the same port pair as `capture`,
    // and the interleaving is inside the stage where the graph cannot see it.
    ins: [_f("run", "run")],
    outs: [_f("complete", "complete"), _e("frame", "frame graded")],
    // `plan` is the slot table in the server's storage format: "<filter>
    // <seconds>", comma separated, wheel order. NOTHING TYPES IT — see the
    // cycleplan control below.
    params: { plan: "L 60, R 60, G 60, B 60, Ha 180, OIII 180, SII 180", cycles: 45, perCycle: 1, gain: 100, bin: "1", reject: 3.5 },
    fields: [
      { key: "plan", label: "Cycle plan - rig filter wheel", control: "cycleplan" },
      { key: "cycles", label: "Total cycles", control: "text", unit: "passes" },
      { key: "perCycle", label: "Subs per filter per pass", control: "text" },
      { key: "gain", label: "Gain", control: "text" },
      { key: "bin", label: "Binning", control: "select", options: ["1", "2", "4"] },
      { key: "reject", label: "Reject HFR above", control: "text", unit: "″" },
    ],
    desc: "Interleaved capture: shoots the slot table in order - one sub per filter per pass - and repeats until every slot hits the cycle count. Channels grow evenly, so a half night still stacks. Per-filter focus offsets apply on each change; a hold resumes at the same slot mid-pass.",
    sum: (p) => String(parseCyclePlan(p.plan).length) + " filters · 1/pass · ×" + txt(p.cycles),
  },
  duskflats: {
    type: "duskflats",
    label: "DUSK FLATS",
    cat: "RIG",
    colorVar: "--sky",
    ins: [_f("run", "run")],
    outs: [_f("done", "flats done")],
    // "Sun −2° … −8°" carries U+2212 MINUS and U+2026 ELLIPSIS, not "-" and
    // "...". nodes.py stores the identical string and the compiler matches on
    // it, so an ASCII "fix" here would stop the window resolving.
    params: { method: "Translucent lens cap", window: "Sun −2° … −8°", filters: "Tonight's plan only", adu: 28500, count: 15 },
    fields: [
      { key: "method", label: "Method", control: "select", options: ["Translucent lens cap", "Flat panel", "Twilight sky"] },
      { key: "window", label: "Wait for", control: "select", options: ["Sun −2° … −8°", "Sun 0° … −6°", "Now"] },
      { key: "filters", label: "Filters", control: "select", options: ["All in wheel", "Tonight's plan only"] },
      { key: "adu", label: "ADU target", control: "text" },
      { key: "count", label: "Count per filter", control: "text" },
    ],
    desc: "Holds the flow until the twilight window (sun altitude band), then shoots the flat set - translucent lens cap, panel, or twilight sky - solving exposure to the ADU target per filter before darkness is wasted on it.",
    // `window` is NOT lowercased: it starts with "Sun", a proper noun here.
    sum: (p) => low(p.method) + " · " + txt(p.window) + " · ×" + txt(p.count),
  },
  calib: {
    type: "calib",
    label: "CALIBRATION QUEUE",
    cat: "RIG",
    colorVar: "--sky",
    // ALL THREE OPTIONAL — nodes.py `optional_ins={"panel","do","stop"}`.
    // `do` and `stop` joined `panel` when the doctor stopped demanding four
    // wires the engine does not consult: the calibration queue starts itself on
    // a cloud hold and exits at the frame boundary, so neither wire is what
    // makes it run. Doctor rule 1 (unwired input) skips them; rule 7 still
    // explains the missing flats.
    ins: [
      { id: "do", label: "do", kind: "event", optional: true },
      { id: "stop", label: "stop", kind: "event", optional: true },
      { id: "panel", label: "panel", kind: "event", optional: true },
    ],
    outs: [],
    params: {
      darks: "If library stale", bias: "If library stale",
      flats: "If stale + panel wired", blackSlot: "Rotate to black slot",
      quota: 20, dest: "captures/Calibration/",
    },
    fields: [
      { key: "darks", label: "Darks", control: "select", options: ["If library stale", "Always", "Skip"] },
      { key: "bias", label: "Bias", control: "select", options: ["If library stale", "Always", "Skip"] },
      { key: "flats", label: "Flats", control: "select", options: ["If stale + panel wired", "Skip"] },
      { key: "blackSlot", label: "Cover method", control: "select", options: ["Rotate to black slot", "Close dome shutter", "Leave in place"] },
      { key: "quota", label: "Sufficient quantity", control: "text", unit: "frames each" },
      { key: "dest", label: "Destination", control: "text" },
    ],
    desc: "Opportunistic calibration while the sky is unusable. Rotates the wheel to the black slot, then fills the library in order - darks if needed, bias if needed, flats if needed AND a flat panel is wired to 'panel' - until each quota is met, then waits. A 'stop' mid-queue exits cleanly at the frame boundary.",
    // The order is fixed by the engine, so it is stated rather than derived
    // from the three policy params — those decide IF each kind runs, not when.
    sum: (p) => "darks → bias → flats · ×" + txt(p.quota) + " each, then wait",
  },
  // ------------------------------------------------------------------ LOGIC
  pool: {
    type: "pool",
    label: "TARGET POOL",
    cat: "LOGIC",
    colorVar: "--accent-dim",
    // OPTIONAL — nodes.py `optional_ins=frozenset({"advance"})`. A single-night
    // pool never advances; doctor rule 11 asks for the wire only once the DUSK
    // WINDOW says the night comes back.
    ins: [_f("arm", "arm"), { id: "advance", label: "advance", kind: "event", optional: true }],
    outs: [_f("target", "best target"), _e("floor", "floor hit")],
    params: {
      members: "M16, M17, M8, NGC 6946",
      strategy: "Best available (alt × moon)",
      quota: 45, minAlt: 30, onFloor: "Advance now; retry it next night",
      moonSep: 40, maxHA: 4,
    },
    fields: [
      { key: "members", label: "Candidates", control: "text" },
      { key: "strategy", label: "Strategy", control: "select", options: ["Best available (alt × moon)", "Priority order", "Round robin"] },
      { key: "quota", label: "Per-target quota", control: "text", unit: "cycles" },
      { key: "minAlt", label: "Min altitude", control: "text", unit: "°" },
      { key: "onFloor", label: "At altitude floor", control: "select", options: ["Advance now; retry it next night", "Keep imaging (not recommended)"] },
      { key: "moonSep", label: "Min moon separation", control: "text", unit: "°" },
      { key: "maxHA", label: "Max hour angle", control: "text", unit: "h" },
    ],
    desc: "Holds candidates and hands the flow whichever scores best right now - altitude × moon separation × hour angle. 'Advance' marks the active target done and re-scores the REMAINING members; done targets are never re-selected. The scheduler watches the active target's altitude: at the floor it fires 'floor hit', suspends that target's cursor (NOT done - it retries next night), and hands out the next best.",
    // "Best available (alt × moon)" lowercased would print the "×" formula in
    // the footer and overflow a 188px card, so that one strategy gets a short
    // form and the other two are lowercased whole.
    sum: (p) => String(txt(p.members).split(",").length) + " candidates · "
      + (txt(p.strategy).indexOf("Best") === 0 ? "best available" : low(p.strategy))
      + " · quota ×" + txt(p.quota),
  },
  condition: {
    type: "condition",
    label: "CONDITION",
    cat: "LOGIC",
    colorVar: "--accent-dim",
    ins: [_e("events", "events")],
    outs: [_e("fire", "fire")],
    params: { when: "HFR above", threshold: 3.2, window: "3 frames", once: "Every time" },
    fields: [
      // A CLOSED predicate set, deliberately. nodes.py's docstring: the graph
      // compiles to what the engine already runs, so "just add a script node"
      // is refused by construction.
      { key: "when", label: "When", control: "select", options: ["HFR above", "FWHM above", "Guide RMS above", "Guide star lost", "Frame rejected", "Star count below", "Sky background above", "Wind gust above", "Dew margin below", "Sensor temp off setpoint", "Disk space below", "Airmass above", "Meridian flip within", "Target complete"] },
      { key: "threshold", label: "Threshold", control: "text" },
      { key: "window", label: "Within", control: "select", options: ["1 frame", "3 frames", "5 frames"] },
      { key: "once", label: "Fire", control: "select", options: ["Every time", "Once per run"] },
    ],
    desc: "A bounded when-clause over the closed predicate set - no scripting runtime. Fires its action wires when the clause holds.",
    sum: (p) => low(p.when) + " " + txt(p.threshold) + " · " + txt(p.window),
  },
  // -------------------------------------------------------- ACTIONS + SINKS
  holdresume: {
    type: "holdresume",
    label: "HOLD / RESUME",
    cat: "ACTION",
    colorVar: "--warn",
    // OPTIONAL `resume` — nodes.py `optional_ins=frozenset({"resume"})`. The
    // hold releases itself when the sky clears, so the wire is redundant rather
    // than missing (to_plan.REDUNDANT_PORTS says so in the same words).
    ins: [_e("pause", "pause"),
          { id: "resume", label: "resume", kind: "event", optional: true }],
    outs: [],
    params: {
      whilePaused: "Keep tracking, park guider", maxHold: 45,
      onTimeout: "Abort + park",
      cooler: "Re-cool + stabilize before capture",
      recenter: "Re-center (plate solve)",
      refocus: "If HFR drifted",
    },
    fields: [
      { key: "whilePaused", label: "While paused", control: "select", options: ["Keep tracking, park guider", "Keep tracking + guiding", "Stop tracking"] },
      { key: "maxHold", label: "Max hold", control: "text", unit: "min" },
      { key: "onTimeout", label: "If exceeded", control: "select", options: ["Abort + park", "Keep holding"] },
      // FIRST IN THE RESUME CHECKLIST, and the row order is the run order. A
      // hold can outlive whatever was keeping the sensor cold; pointing and
      // focus are worth nothing on a frame the temperature already ruined.
      { key: "cooler", label: "On resume, cooler", control: "select", options: ["Re-cool + stabilize before capture", "Skip check"] },
      { key: "recenter", label: "On resume, pointing", control: "select", options: ["Re-center (plate solve)", "Trust tracking"] },
      { key: "refocus", label: "On resume, focus", control: "select", options: ["If HFR drifted", "Always", "Never"] },
    ],
    desc: "Pauses the capture loop at the next frame boundary; on resume the cooler gate runs first: if cooling stopped or drifted (daybreak park, power cycle), capture is BLOCKED until the sensor is back at setpoint and stable (ramp-limited). Then filter restore, re-center per policy, refocus if called for, and the loop continues in place.",
    // ⚠ PROTOTYPE BUG, REPRODUCED DELIBERATELY. "re-center" is hardcoded and
    // `p.recenter` is ignored, so a node set to "Trust tracking" still claims
    // it will re-center. The handoff (§C.0) names this and says reproduce it —
    // the prototype outranks a local fix. Reported in the milestone summary; do
    // not "fix" it here without a ruling, because the footer text is one of the
    // things the screenshot parity pass compares.
    sum: (p) => "resume: re-center · refocus " + low(p.refocus),
  },
  notify: {
    type: "notify",
    label: "NOTIFY",
    cat: "ACTION",
    colorVar: "--warn",
    ins: [_e("do", "do")],
    outs: [],
    params: { sink: "ntfy", channel: "rig-alerts", level: "warning" },
    fields: [
      { key: "sink", label: "Sink", control: "select", options: ["ntfy", "Telegram", "Webhook"] },
      { key: "channel", label: "Channel", control: "text" },
      // Lowercase on purpose: these are the alerting module's level ids, not
      // display names.
      { key: "level", label: "Level", control: "select", options: ["info", "warning", "error"] },
    ],
    desc: "Push through a configured alert sink. Severity picks the channel escalation.",
    sum: (p) => txt(p.sink) + " · " + txt(p.channel),
  },
  refocus: {
    type: "refocus",
    label: "REFOCUS",
    cat: "ACTION",
    colorVar: "--warn",
    ins: [_e("do", "do")],
    outs: [],
    params: { boundary: "Next frame boundary" },
    fields: [
      // One option, one row. Interrupting mid-exposure would bin the frame, so
      // there is nothing else to offer — but the row states the rule.
      { key: "boundary", label: "Interrupt at", control: "select", options: ["Next frame boundary"] },
    ],
    desc: "Interrupt the loop at the next frame boundary, run autofocus, resume where it left off.",
    // Takes no params — the prototype's body is a constant.
    sum: () => "autofocus, then resume",
  },
  parkclose: {
    type: "parkclose",
    label: "PARK + CLOSE",
    cat: "ACTION",
    // --warn, NOT --bad. It sits beside ABORT + PARK and does much of the same
    // physical work, but the colour is the difference between "the night ended"
    // and "the night went wrong", and an operator scanning a canvas at 04:00
    // reads the colour before the label.
    colorVar: "--warn",
    // OPTIONAL `do` — nodes.py `optional_ins=frozenset({"do"})`. Every
    // flow-derived plan sets `park_when_done` and every wind-down closes the
    // cover, so the night ends parked and shut whether or not anything is wired
    // here.
    ins: [{ id: "do", label: "do", kind: "event", optional: true }],
    outs: [_e("closed", "closed")],
    params: { closure: "Dust flap + dome", cooler: "Hold cold (day darks)", tracking: "Park" },
    fields: [
      { key: "closure", label: "Closure", control: "select", options: ["Dust flap + dome", "Dome shutter", "Dust flap", "Roll-off roof"] },
      // HOLD COLD is the default because of what `closed` chains into: a day of
      // darks is only worth taking if the sensor is at the temperature the
      // night's lights were shot at.
      { key: "cooler", label: "Camera cooler", control: "select", options: ["Hold cold (day darks)", "Warm up"] },
      { key: "tracking", label: "Mount", control: "select", options: ["Park"] },
    ],
    desc: "Scheduled end-of-night shutdown - not an abort. Parks the mount, closes the closure, and either warms the camera or holds it cold so capped day-darks stay matched. The campaign cursor is preserved for the next window; safety can still slam everything shut independently.",
    sum: (p) => low(p.closure) + " · " + (txt(p.cooler).indexOf("Hold") === 0 ? "hold cold" : "warm up"),
  },
  abort: {
    type: "abort",
    label: "ABORT + PARK",
    cat: "ACTION",
    // NOT --warn. See NodeDef.colorVar: this is the ACTION category's one
    // exception, and __tests__/nodeDefs.test.ts pins it because a category
    // lookup would quietly re-derive it as amber.
    colorVar: "--bad",
    ins: [_e("do", "do")],
    outs: [],
    params: { park: "Yes", warm: "Yes", message: "Safety monitor unsafe" },
    fields: [
      { key: "park", label: "Park mount", control: "select", options: ["Yes", "No"] },
      { key: "warm", label: "Warm camera", control: "select", options: ["Yes", "No"] },
      { key: "message", label: "Reason", control: "text" },
    ],
    desc: "Stop the sequence, park the mount, warm the camera. The reason is written to the session report.",
    sum: (p) => "park " + low(p.park) + " · warm " + low(p.warm),
  },
  report: {
    type: "report",
    label: "SESSION REPORT",
    cat: "SINK",
    colorVar: "--good",
    ins: [_f("session", "session")],
    // The campaign's loop-back source. It fires when the ACTIVE target's quota
    // is met, and wiring it to a pool's `advance` is the whole mechanism — an
    // event, so the backward wire is legal and the flow lane stays a DAG.
    outs: [_e("done", "target done")],
    params: { format: "JSON + FITS index", dest: "captures/sessions/" },
    fields: [
      { key: "format", label: "Format", control: "select", options: ["JSON + FITS index", "JSON only"] },
      { key: "dest", label: "Destination", control: "text" },
    ],
    desc: "Append-only session ledger: per-filter integration, accepted/rejected counts, median HFR, safety events, end reason. 'Target done' fires when the active target's quota is met - wire it back to a pool's 'advance' to run a campaign.",
    sum: (p) => txt(p.dest),
  },
};
