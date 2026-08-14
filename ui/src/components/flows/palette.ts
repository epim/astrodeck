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
 * §G-3 — SETTLED 2026-08-14 by the new export. Item order inside LOGIC and
 * ACTIONS + SINKS.
 *
 * It was open because no source was right about both groups: the prototype, the
 * README's prose and the README's table each stated an order, and the server
 * stated a fourth. The contract's instruction was "do not pick one".
 *
 * The 2026-08-14 prototype resolves it by being the only source that names EVERY
 * current type. FILTER CYCLE moved to RIG OPS and PARK + CLOSE arrived, and no
 * other reading mentions either — README §3 still specifies group names and
 * group order only, exactly as it always did. So the prototype now speaks for
 * the whole vocabulary while every other reading speaks for a subset of it, and
 * `nodes.py` has been set to agree with it.
 *
 * The readings are kept rather than deleted. They are what the test compares
 * against, and the guarantee they buy never depended on the dispute being open:
 * a shipped order must be one that some source actually states, so that nobody
 * quietly invents a fifth by re-ordering to taste.
 */
export const PALETTE_ITEM_ORDER_SOURCES = {
  LOGIC: {
    /** "AstroDeck Flows.dc.html" 2026-08-14 — `["LOGIC", ["condition", "pool"]]`. */
    prototype: ["condition", "pool"],
    /** README.md overview prose — "logic (condition, target pool)". */
    readmeProse: ["condition", "pool"],
    /** README.md node-vocabulary table — TARGET POOL row precedes CONDITION. */
    readmeTable: ["pool", "condition"],
    /** nodes.py `PALETTE_GROUPS`, now the prototype's order. */
    server: ["condition", "pool"],
  },
  "ACTIONS + SINKS": {
    /** "AstroDeck Flows.dc.html" 2026-08-14. The only reading that names
     *  PARK + CLOSE, because it is the only one written after it existed. */
    prototype: ["notify", "refocus", "holdresume", "parkclose", "abort", "report"],
    /** README.md overview prose — "actions/sinks (notify, refocus, hold/resume,
     *  abort+park, session report)". Predates PARK + CLOSE. */
    readmeProse: ["notify", "refocus", "holdresume", "abort", "report"],
    /** README.md node-vocabulary table — NOTIFY, REFOCUS, HOLD / RESUME,
     *  ABORT + PARK, SESSION REPORT. Predates PARK + CLOSE. */
    readmeTable: ["notify", "refocus", "holdresume", "abort", "report"],
    /** nodes.py `PALETTE_GROUPS`, now the prototype's order. */
    server: ["notify", "refocus", "holdresume", "parkclose", "abort", "report"],
  },
} as const satisfies Record<string, Record<string, readonly FlowNodeType[]>>;

/** Group labels whose item order came from the §G-3 record rather than from a
 *  single obvious source.
 *
 *  Exported so the rule is visible to code and tests, not just to a reader of
 *  this comment: the test asserts that each flagged group's shipped order is one
 *  a source actually states, which is what stops a later edit from quietly
 *  inventing an ordering nobody wrote down. Kept after the ruling — the ruling
 *  chose between the readings, it did not license ignoring them. */
export const PALETTE_ITEM_ORDER_DISPUTED: readonly string[] = [
  "LOGIC",
  "ACTIONS + SINKS",
];

/**
 * The palette, in rail order.
 *
 * Group names and group order: agreed by every source (§C.7), no choice made
 * here. Item order: the 2026-08-14 prototype's `groups` array verbatim, which is
 * now also what `nodes.py` states. See `PALETTE_ITEM_ORDER_SOURCES` for why that
 * source and not another.
 *
 * FILTER CYCLE sits in RIG OPS beside CAPTURE LOOP because that is what it is:
 * a capture stage that interleaves, not a loop construct. It used to be filed
 * under LOGIC, which read as though the graph had a loop primitive — the export
 * is explicit that it does not.
 */
export const PALETTE_GROUPS = [
  { label: "SOURCES", types: ["dusk", "target", "safety", "cloudwatch"] },
  { label: "EQUIPMENT", types: ["dome", "flatpanel"] },
  { label: "RIG OPS", types: ["slew", "autofocus", "guide", "capture", "cycle", "duskflats", "calib"] },
  { label: "LOGIC", types: ["condition", "pool"] },
  { label: "ACTIONS + SINKS", types: ["notify", "refocus", "holdresume", "parkclose", "abort", "report"] },
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
