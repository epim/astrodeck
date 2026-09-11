// session/flows/index.ts - what the SESSION hub mounts for `#/session/flows`.
//
// `SessionHub.tsx` is another task's file, so this barrel is the seam: the hub
// imports `FlowsScreen` from here and the sub-nav switch is one line. The
// screen handles the canvas itself - `?open=<id>` swaps the hub body for
// `FlowsCanvasHost` at 768 px and up - so the hub never has to know that the
// Flows sub has two faces.
//
// The four rebuilt areas (`canvas/`, `inspector/`, `tonight/`, `create/`) are
// NOT re-exported from here. Their sheets reach the router through the SESSION
// hub's own registry (`../sheets/index.ts`), and their components are composed
// by `FlowsCanvasHost`; a barrel that also re-exported them would put the whole
// canvas in the import graph of anything that wanted one constant.

export {
  FlowsScreen, FLOWS_FOOTER, CANVAS_PHONE_REASON, CREATE_PHONE_HINT,
  FILTER_PLACEHOLDER, NO_CANVAS_TARGET, NO_MATCH_HINT,
} from "./FlowsScreen";
export { FlowsCanvasHost, CANVAS_LIBRARY, CANVAS_NO_FLOW_HINT } from "./FlowsCanvasHost";
export { FlowRow, JUST_SAVED, type FlowVerb, type FlowRowProps, type FlowOpenTarget } from "./FlowRow";
