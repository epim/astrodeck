// palette.ts — the add-stage palette's shape: which groups exist, the order the
// rail renders them in, and the order of node types inside each group.
//
// Why this is a separate file from `nodeDefs.ts`, and why it is not derived
// from a node's `cat`: it CANNOT be derived from `cat`. DOME CONTROL and FLAT
// PANEL carry `cat="RIG"` (nodes.py:104,109) while the rail files them under
// EQUIPMENT, and SESSION REPORT carries `cat="SINK"` while the rail folds sinks
// into ACTIONS + SINKS. §C.8 records that both readings are correct — the
// inspector's category chip shows `cat`, the rail shows THIS — and that the
// divergence is deliberate, not a bug. A palette built by grouping on `cat`
// would produce five wrong headings and two misfiled stages.
//
// Sources: MILESTONE2-CONTRACT.md §C.7 (group names + order, and the §G-3
// dispute), README.md §3 line 65, "AstroDeck Flows.dc.html" line 1456,
// server/astrodeck/flows/nodes.py `PALETTE_GROUPS`.
import type { FlowNodeType } from "./flowsTypes";

export interface PaletteGroup {
  /** The rail's group heading, rendered VERBATIM.
   *
   *  The uppercase lives in the data, not in CSS: all three sources spell these
   *  uppercase, and §C.7's header classes (`font-display font-semibold
   *  text-[9.5px] tracking-[0.2em] text-faint`) carry no `uppercase` utility.
   *  That is the opposite of the inspector's field captions, which §C.8 says are
   *  uppercased by `.label`'s `text-transform` and must NOT be uppercased in the
   *  data. A component rendering these must not re-case them either way. */
  label: string;
  /** Node types in the order the group lists them, top to bottom. */
  types: readonly FlowNodeType[];
}

/**
 * ⚠ AMBIGUOUS — §G-3, still OPEN. Item order inside LOGIC and ACTIONS + SINKS.
 *
 * Group names and group order are agreed by all three sources (§C.7). The item
 * order inside the last two groups is not, and the contract's instruction is
 * **"do not pick one"** — meaning do not adopt a single source wholesale,
 * because no source is right about both groups. Every reading any source
 * actually states is recorded here as data so that the ruling, when it comes
 * (the user stating an order, or a desktop-tier ≥1080px screenshot of the rail
 * — neither exists today), is a one-line edit to `PALETTE_GROUPS` with the
 * alternatives already spelled out beside it.
 *
 * Note `readmeProse` — README.md line 30's overview sentence says "logic
 * (condition, target pool)", contradicting README's own node-vocabulary table.
 * The contract's §G-3 evidence cites only the table, so this is a fourth data
 * point it does not list; it is recorded rather than discarded because it turns
 * LOGIC from a 2–1 majority into a 2–2 tie.
 */
export const PALETTE_ITEM_ORDER_SOURCES = {
  LOGIC: {
    // FILTER CYCLE post-dates every source below — it is not in the prototype,
    // the README prose or the README table, because it did not exist when they
    // were written. It is listed only where a source can actually speak for it:
    // nodes.py, which is the authority the resolver already prefers.
    /** "AstroDeck Flows.dc.html" line 1456 — `["LOGIC", ["condition", "pool"]]`. */
    prototype: ["condition", "pool"],
    /** README.md line 30 — "logic (condition, target pool)". */
    readmeProse: ["condition", "pool"],
    /** README.md node-vocabulary table — TARGET POOL row precedes CONDITION. */
    readmeTable: ["pool", "condition"],
    /** nodes.py `PALETTE_GROUPS` — `("pool", "cycle", "condition")`. */
    server: ["pool", "cycle", "condition"],
  },
  "ACTIONS + SINKS": {
    /** "AstroDeck Flows.dc.html" line 1456. */
    prototype: ["notify", "refocus", "holdresume", "abort", "report"],
    /** README.md line 30 — "actions/sinks (notify, refocus, hold/resume,
     *  abort+park, session report)". Agrees with the table and the prototype. */
    readmeProse: ["notify", "refocus", "holdresume", "abort", "report"],
    /** README.md node-vocabulary table — NOTIFY, REFOCUS, HOLD / RESUME,
     *  ABORT + PARK, SESSION REPORT. */
    readmeTable: ["notify", "refocus", "holdresume", "abort", "report"],
    /** nodes.py `PALETTE_GROUPS` — hoists `holdresume` to the front. */
    server: ["holdresume", "notify", "refocus", "abort", "report"],
  },
} as const satisfies Record<string, Record<string, readonly FlowNodeType[]>>;

/** Group labels whose item order is provisional pending §G-3.
 *
 *  Exported so the dispute is visible to code and tests, not just to a reader
 *  of this comment: the test asserts that each flagged group's shipped order is
 *  one a source actually states, which is what stops a later edit from quietly
 *  inventing a fifth ordering nobody wrote down. */
export const PALETTE_ITEM_ORDER_DISPUTED: readonly string[] = [
  "LOGIC",
  "ACTIONS + SINKS",
];

/**
 * The palette, in rail order.
 *
 * Group names and group order: agreed by all three sources (§C.7), no choice
 * made here. Item order in SOURCES, EQUIPMENT and RIG OPS: likewise agreed
 * verbatim by all three.
 *
 * The two ⚠ groups below are the §G-3 dispute. What is shipped, and why:
 *
 *   LOGIC → [pool, condition]. A 2–2 tie between sources, broken on authority
 *     WITHIN README: its normative node-vocabulary contract table outranks its
 *     one-line overview prose, and the server (`nodes.py`) states the same
 *     order. The prototype disagrees.
 *   ACTIONS + SINKS → [notify, refocus, holdresume, abort, report]. README's
 *     table, README's prose and the prototype all state this; only the server
 *     differs, by hoisting `holdresume`.
 *
 * Neither is a ruling — both are provisional readings of sources that disagree,
 * chosen so that no order appears here that no source states. See
 * `PALETTE_ITEM_ORDER_SOURCES` above and §G-3.
 */
export const PALETTE_GROUPS = [
  { label: "SOURCES", types: ["dusk", "target", "safety", "cloudwatch"] },
  { label: "EQUIPMENT", types: ["dome", "flatpanel"] },
  { label: "RIG OPS", types: ["slew", "autofocus", "guide", "capture", "duskflats", "calib"] },
  // ⚠ §G-3 — provisional, see above.
  { label: "LOGIC", types: ["pool", "cycle", "condition"] },
  // ⚠ §G-3 — provisional, see above.
  { label: "ACTIONS + SINKS", types: ["notify", "refocus", "holdresume", "abort", "report"] },
] as const satisfies readonly PaletteGroup[];

/** Every node type the palette offers, flattened in rail order.
 *
 *  A type missing from this list is a node the operator cannot create — the
 *  graph can still contain one (a preset, a saved flow, the server), so the
 *  failure is silent: the stage renders on the canvas and simply cannot be
 *  added again. */
export const PALETTE_TYPES: readonly FlowNodeType[] =
  PALETTE_GROUPS.flatMap((g) => g.types);

type PalettedNodeType = (typeof PALETTE_GROUPS)[number]["types"][number];

/** Node types in the union that no group offers. `never` while the palette is
 *  complete. */
export type NodeTypeMissingFromPalette = Exclude<FlowNodeType, PalettedNodeType>;
/** Anything in the palette that is not a node type. `never` while the palette
 *  is honest. */
export type PaletteTypeWithNoNode = Exclude<PalettedNodeType, FlowNodeType>;

/**
 * Compile-time proof that the palette covers `FlowNodeType` exactly.
 *
 * The annotation resolves to `true` only while both gap types are `never`; add a
 * 20th member to `FlowNodeType` and forget it here, and the annotation becomes
 * `false`, the initialiser stops assigning, and `tsc --noEmit` fails on THIS
 * line rather than nowhere at all.
 *
 * Exported for two reasons: `noUnusedLocals` is on, so a local guard would have
 * to be deleted; and `tsx` strips types without checking them, so `npm test`
 * would never see this. The runtime half of the same check lives in
 * `__tests__/palette.test.ts`, which reads the union out of `flowsTypes.ts`.
 * Nothing reads the value.
 */
export const PALETTE_COVERS_EVERY_NODE_TYPE: [NodeTypeMissingFromPalette] extends [never]
  ? [PaletteTypeWithNoNode] extends [never] ? true : false
  : false = true;
