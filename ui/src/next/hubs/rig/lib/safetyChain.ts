// safetyChain.ts - the WHEN A LIMIT TRIPS chain, BUILT FROM CONFIG.
//
// Plan hub-rig.md B.9 item 2, deviation E12. The prototype
// (`seams/proto/device-safety.html`) draws a constant five-node chain -
// STOP CAPTURE -> PARK -> CLOSE -> WARM COOLER -> NOTIFY - with the last link
// un-wired to anything. On a rig whose `on_unsafe` is "warn", that picture is a
// promise the engine will not keep: nothing stops, nothing parks, no roof moves.
//
// So the nodes here are derived from the four things that actually decide what
// happens on a confirmed unsafe reading:
//
//   safety.on_unsafe            warn | pause | park | abort_park_warm
//   safety.close_dome_on_unsafe plus a CONNECTED dome (the flag alone closes
//                               nothing - the server needs a device to drive)
//   cooling.warm_ramp           false cuts the TEC dead instead of ramping
//   any alert sink configured   with none, NOTIFY reaches a browser tab
//
// A node the engine will not execute renders DIM AND SAYS WHY, rather than
// disappearing: "the roof does not close" is information, and a chain that
// silently shortens teaches nothing. That is why every node carries a `reason`
// and never a bare boolean.
//
// Pure: no React, no store, no fetch. `npx tsx` runnable, tested by
// `__tests__/safetyChain.test.ts`.

import type { SafetyConfig } from "../../../../types";

export type ChainNodeId = "log" | "pause" | "stop" | "park" | "close" | "warm" | "notify";

export interface ChainNode {
  id: ChainNodeId;
  /** The node's word, as the design draws it. */
  label: string;
  /** True when the engine will actually execute this step. */
  lit: boolean;
  /** Why it is dim. `null` exactly when `lit`. */
  reason: string | null;
}

export interface ChainInput {
  onUnsafe: SafetyConfig["on_unsafe"];
  closeDomeOnUnsafe: boolean;
  /** `getDomeState().connected` - the flag is inert without a device. */
  domeConnected: boolean;
  /** `cooling.warm_ramp`. An older server omits the whole block; the caller
   *  degrades to `true` (the shipped default) rather than blanking the node. */
  warmRamp: boolean;
  /** Any alert sink at all (`config.alerts`), enabled or not - a disabled sink
   *  is a channel that exists and can be switched back on, which is a different
   *  situation from having none. */
  hasSink: boolean;
}

// Reasons, one per way a node can be dim. Exported because the sheet prints
// them under the chain and the test asserts against them rather than against a
// substring it typed itself.
export const NO_ROOF_REASON = "CLOSE is off - no roof is connected";
export const CLOSE_FLAG_OFF_REASON = "CLOSE is off - close roof on unsafe is switched off";
export const NO_SINK_REASON = "NOTIFY is off - no alert channel is configured";
export const NO_RAMP_REASON =
  "WARM COOLER is off - the warm ramp is switched off, so the cooler is cut dead instead";

/** The fragment's own explanatory line, verbatim, because it is true of the
 *  engine: a cloudy verdict engages the self-releasing hold (stand down the
 *  guider, probe, resume on a clear streak), not the unsafe/park path. */
export const CHAIN_NOTE =
  "Fail-closed: the engine parks first and asks questions later. Clouds are a hold, "
  + "not a trip - they wait for clearing; rain, wind and power are trips.";

function notifyNode(hasSink: boolean): ChainNode {
  return { id: "notify", label: "NOTIFY", lit: hasSink, reason: hasSink ? null : NO_SINK_REASON };
}

function closeNode(inp: ChainInput): ChainNode {
  // Capability first, then the flag: "no roof is connected" is the reason that
  // does not go away by changing a setting, so it is the one to show.
  const reason = !inp.domeConnected
    ? NO_ROOF_REASON
    : !inp.closeDomeOnUnsafe
      ? CLOSE_FLAG_OFF_REASON
      : null;
  return { id: "close", label: "CLOSE", lit: reason == null, reason };
}

/** The nodes the rig will actually execute, in order. Never empty: every
 *  `on_unsafe` value does at least one thing. */
export function safetyChain(inp: ChainInput): ChainNode[] {
  switch (inp.onUnsafe) {
    case "warn":
      // Nothing stops. The design's chain would be a lie here, so the whole
      // teardown is absent rather than drawn dim - it is not "off", it is not
      // what this setting means.
      return [
        { id: "log", label: "LOG IT", lit: true, reason: null },
        notifyNode(inp.hasSink),
      ];
    case "pause":
      return [
        { id: "pause", label: "PAUSE CAPTURE", lit: true, reason: null },
        notifyNode(inp.hasSink),
      ];
    case "park":
      return [
        { id: "stop", label: "STOP CAPTURE", lit: true, reason: null },
        { id: "park", label: "PARK", lit: true, reason: null },
        closeNode(inp),
        notifyNode(inp.hasSink),
      ];
    case "abort_park_warm":
    default:
      return [
        { id: "stop", label: "STOP CAPTURE", lit: true, reason: null },
        { id: "park", label: "PARK", lit: true, reason: null },
        closeNode(inp),
        {
          id: "warm",
          label: "WARM COOLER",
          lit: inp.warmRamp,
          reason: inp.warmRamp ? null : NO_RAMP_REASON,
        },
        notifyNode(inp.hasSink),
      ];
  }
}

/** The dim nodes' reasons, in chain order - what the card prints under the row.
 *  Empty when every node is lit. */
export function chainReasons(nodes: ChainNode[]): string[] {
  return nodes.filter((n) => !n.lit && n.reason != null).map((n) => n.reason as string);
}
