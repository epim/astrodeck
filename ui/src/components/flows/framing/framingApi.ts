// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// framingApi.ts - what the Target modal asks the server, and how it reads the
// answers (#189 S4 item 1; spec 2026-09-23 flows mosaic, 2.3-2.5, 6.9).
//
// TWO KINDS OF THING LIVE HERE, both at the server boundary:
//
//   1. The calls: `POST /api/framing/mosaic` (where the panels are, and the
//      re-frame verdict against the block's anchor) and the draft compile
//      (the RUN numbers for the block AS FRAMED, before DONE writes it).
//      Both sit on one mutable object, `framingApi`, so a test replaces the
//      network with a promise it controls, as flowsSlice.test.ts does for
//      `flowsApi`.
//   2. The readers: the compile route answers `readouts` and `rig` as JSON,
//      and `lib/flowsApi` types the compile as four lists that predate them.
//      A reader here checks every key the TypeScript types in framingModel
//      declare against the value that arrived, and says WHICH key is wrong
//      when one is. The key lists are typed `satisfies` over the model's
//      types, so `tsc -b` fails if a type gains or loses a key the reader
//      does not check; and framingReadoutsFixture.test.ts runs the route's
//      recorded answer (server/tests/fixtures/flow_readouts_m31.json)
//      through the reader to the rendered RUN lines, so a field renamed on
//      either side shows as a missing key there, not as "undefined filters"
//      in front of the operator.
//
// NO SITE DATA IS COMPUTED OR KEPT HERE. The campaign line reads the night's
// length off the Tonight answer, which only a holder of `view.site_derived`
// can fetch, and says nothing for anyone else (spec 6.9): the length of a
// night at a known date is a function of the latitude.

import { api } from "../../../api";
import { flowsApi, type FlowCompileResult } from "../../../lib/flowsApi";
import type { MosaicPanel } from "../../../types";
import { withLoop } from "../panelLane";
import type { FlowGraphRec } from "../flowsTypes";
import type {
  MosaicRequest, Params, ReframeAnswer, RigBlock, RunReadouts,
} from "./framingModel";

// ------------------------------------------------------------------ calls

export const FRAMING_MOSAIC = "/api/framing/mosaic";

/** `POST /api/framing/mosaic`'s answer, with the `reframe` key the route adds
 *  when the request carried the block's anchor (spec 3.3). */
export interface MosaicAnswerBody {
  panels: MosaicPanel[];
  total_fov_x_deg: number;
  total_fov_y_deg: number;
  frame_fov_x_deg: number;
  frame_fov_y_deg: number;
  pixel_scale_arcsec?: number;
  reframe?: ReframeAnswer | null;
}

/** How long the draft must sit still before the modal asks the server: the
 *  settle of spec 2.3 ("on every settle the modal calls POST
 *  /api/framing/mosaic"). The Atlas's MosaicNight and the #/next
 *  MosaicNightCard wait the same 400 ms for the same route. A test sets it
 *  to 0; nothing else writes it. */
export const framingTiming = { settleMs: 400 };

export const framingApi = {
  /** The panels for `req`. `transitAlt` asks for each panel's peak altitude
   *  tonight as well, for the PANELS altitude column: the route answers only
   *  a holder of `view.site_derived`, and the sheet asks only for one. */
  mosaic: (req: MosaicRequest, transitAlt: boolean): Promise<MosaicAnswerBody> =>
    api.post<MosaicAnswerBody>(FRAMING_MOSAIC, transitAlt ? { ...req, transit_alt: true } : req),
  /** The compile of the flow AS THE MODAL WOULD LEAVE IT, for the RUN
   *  section's numbers. Its answer is the sheet's own and never reaches
   *  `store.flows.compiled`, which stays the answer for the graph on the
   *  canvas until DONE writes the framing and compiles once. */
  compileDraft: (graph: FlowGraphRec, name: string): Promise<FlowCompileResult> =>
    flowsApi.compileDraft(graph, name),
};

// --------------------------------------------------------------- readers

/** One key's rule. `?` is "may be absent": `efficiency` is present only with
 *  a measured hop, and absent (not null) otherwise. */
type Kind =
  | "string" | "number" | "number|null" | "number?" | "boolean" | "boolean|null"
  | "mode" | "focus" | "fov";

/** Every key of `RunReadouts`, and its rule (server `flows/readouts.py`
 *  `_block`). `satisfies` over `Required<RunReadouts>`: a key the type gains
 *  that this list does not check, or a key checked here that the type does
 *  not have, is a `tsc -b` error, so the reader and the type are one list. */
export const READOUT_KINDS = {
  node_id: "string", mode: "mode", panels: "number", steps: "number",
  rounds: "number|null", subs_per_panel: "number", subs_total: "number",
  pass_s: "number|null", panel_s: "number", total_s: "number",
  passes: "number|null", visit_min_s: "number|null", visit_passes: "number|null",
  visits_per_panel: "number|null", visits_total: "number|null",
  // `hop_measured` is null on a single target, which makes no hop (see
  // RunReadouts, #409); the rig block's flag below is always a boolean.
  hop_s: "number|null", hop_measured: "boolean|null", efficiency: "number?",
  preflip_idle_s: "number|null", angle_tolerance_deg: "number|null",
  focus: "focus", autofocus_every: "number", refocus_delta_c: "number",
} as const satisfies { readonly [K in keyof Required<RunReadouts>]: Kind };

/** Every key of `RigBlock` (server `rig_readout`), the same way. */
export const RIG_KINDS = {
  fov_deg: "fov", fov_from: "string", has_rotator: "boolean|null",
  hop_s: "number|null", hop_samples: "number", hop_measured: "boolean",
} as const satisfies { readonly [K in keyof Required<RigBlock>]: Kind };

const finite = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);

function fits(kind: Kind, v: unknown, has: boolean): boolean {
  switch (kind) {
    case "string": return typeof v === "string";
    case "number": return finite(v);
    case "number|null": return v === null || finite(v);
    case "number?": return !has || finite(v);
    case "boolean": return typeof v === "boolean";
    case "boolean|null": return v === null || typeof v === "boolean";
    case "mode": return v === "rotate" || v === "sequential" || v === "single";
    case "focus": return v === "temperature" || v === "frames" || v === "once";
    case "fov": return v === null || (Array.isArray(v) && v.length === 2 && v.every(finite));
  }
}

/** A value read against a key table: the value, or the first key that is not
 *  what the table says, named so a reader can tell a renamed field from a
 *  missing block. */
export type Read<T> = { ok: true; value: T } | { ok: false; why: string };

function readAgainst<T>(v: unknown, kinds: Record<string, Kind>, what: string): Read<T> {
  if (v === null || typeof v !== "object" || Array.isArray(v)) {
    return { ok: false, why: `${what} is not an object` };
  }
  const o = v as Record<string, unknown>;
  for (const [key, kind] of Object.entries(kinds)) {
    const has = Object.prototype.hasOwnProperty.call(o, key);
    if (!has && !kind.endsWith("?")) return { ok: false, why: `${what} has no ${key}` };
    if (!fits(kind, o[key], has)) return { ok: false, why: `${what}.${key} is not a ${kind}` };
  }
  return { ok: true, value: o as unknown as T };
}

/** The compile answer's two S4 keys, typed loosely because they arrive as
 *  JSON; read them only through `compiledReadouts` and `compiledRig`. */
type WithS4Keys = FlowCompileResult & { readouts?: unknown; rig?: unknown };

/** This block's RUN numbers from a compile answer (`readouts[node_id]`), or
 *  why they cannot be read. A block the compile has no readout for (a
 *  refused compile answers `{}`, a pool has none) is "no readout", not an
 *  error in the answer's shape. */
export function compiledReadouts(
  compiled: FlowCompileResult | null | undefined, nodeId: string,
): Read<RunReadouts> | null {
  const all = (compiled as WithS4Keys | null | undefined)?.readouts;
  if (all === undefined || all === null) return null;
  if (typeof all !== "object" || Array.isArray(all)) return { ok: false, why: "readouts is not an object" };
  const block = (all as Record<string, unknown>)[nodeId];
  if (block === undefined) return null;
  return readAgainst<RunReadouts>(block, READOUT_KINDS, `readouts.${nodeId}`);
}

/** The compile answer's rig block, or null when it has none or it is not the
 *  shape `RigBlock` declares (a reader then says nothing about the rig,
 *  which is what every "unknown" in it means anyway). */
export function compiledRig(compiled: FlowCompileResult | null | undefined): RigBlock | null {
  const rig = (compiled as WithS4Keys | null | undefined)?.rig;
  if (rig === undefined || rig === null) return null;
  const r = readAgainst<RigBlock>(rig, RIG_KINDS, "rig");
  return r.ok ? r.value : null;
}

// ------------------------------------------------ MATCH CAMERA's source

/** The rig as MATCH CAMERA reads it (spec 2.4): the compile's rig block with
 *  its field taken from `effectiveOptics` at bin 1, the profile-aware
 *  resolver every other optics reader on this side uses (#129), because the
 *  draft compile ran when the flow was opened and the optics may have
 *  changed since. `where` is the profile supplying the optics, or null for
 *  the rig's own; the words match the server's `_rig_facts`, so a field
 *  matched here reads the same as one the server recorded. A field that is
 *  not finite and positive is no field (the compile's field stands). */
export function liveRig(
  rig: RigBlock | null,
  fov: { fov_x_deg: number; fov_y_deg: number } | null,
  where: string | null,
  today: string,
): RigBlock | null {
  const ok = !!fov && finite(fov.fov_x_deg) && finite(fov.fov_y_deg)
    && fov.fov_x_deg > 0 && fov.fov_y_deg > 0;
  if (!ok) return rig;
  const base: RigBlock = rig ?? {
    fov_deg: null, fov_from: "", has_rotator: null, hop_s: null, hop_samples: 0, hop_measured: false,
  };
  return {
    ...base,
    fov_deg: [fov!.fov_x_deg, fov!.fov_y_deg],
    fov_from: `${where ? `profile ${where}` : "the rig's optics"}, matched ${today}`,
  };
}

// ------------------------------------------------- the framed draft graph

/** The graph as DONE would leave it, for the draft compile: the node's
 *  params with `patch` over them (already coerced by framingPatch), the loop
 *  wire as `withLoop` sets it, and the flow setting. Pure; the new loop
 *  wire's id is a fixed placeholder, because this graph is compiled and
 *  thrown away, never written. */
export function framedGraph(
  graph: FlowGraphRec, nodeId: string, patch: Params, loop: boolean | undefined,
  settings: Record<string, string> | null,
): FlowGraphRec {
  const nodes = graph.nodes.map((n) => (n.id === nodeId && Object.keys(patch).length
    ? { ...n, params: { ...n.params, ...patch } } : n));
  const g: FlowGraphRec = { ...graph, nodes };
  const edges = withLoop(g, nodeId, loop, () => "framing-draft-loop");
  return {
    ...g, edges,
    ...(settings ? { settings: { ...graph.settings, ...settings } } : {}),
  };
}

// ----------------------------------------------------- the campaign line

const hours1 = (seconds: number) => (seconds / 3600).toFixed(1);

/** RUN's campaign line (spec 2.4): "this is a campaign: about 7.8 nights of
 *  7.5 h before hops. The session stays armed and resumes at the next dusk."
 *
 *  The block's shutter time is the compile's (`total_s`); the night is the
 *  Tonight answer's, dusk to dawn at the flow's twilight. NULL FOR ANYONE
 *  WITHOUT `view.site_derived` (spec 6.9: a night's length at a known date
 *  gives the latitude), for an answer that could not lay out a night, and for
 *  a block that fits in one night, which is not a campaign. The resume clause
 *  is what the engine does today: `engine.start` arms auto-resume on every
 *  run, so the old "Set DUSK to repeat nightly" advice changed nothing and is
 *  not given. */
export function campaignLine(
  readouts: RunReadouts | null, tonight: unknown, canViewSiteDerived: boolean,
): string | null {
  if (!canViewSiteDerived || !readouts || !finite(readouts.total_s)) return null;
  if (tonight === null || typeof tonight !== "object") return null;
  const t = tonight as { ok?: unknown; night?: { dusk_unix?: unknown; dawn_unix?: unknown } | null };
  if (t.ok !== true || !t.night) return null;
  const dusk = t.night.dusk_unix;
  const dawn = t.night.dawn_unix;
  if (!finite(dusk) || !finite(dawn) || !(dawn > dusk)) return null;
  const nightS = dawn - dusk;
  const nights = readouts.total_s / nightS;
  if (!(nights > 1)) return null;
  return `this is a campaign: about ${nights.toFixed(1)} nights of ${hours1(nightS)} h before hops. `
    + "The session stays armed and resumes at the next dusk.";
}
