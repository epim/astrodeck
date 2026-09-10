// session/flows/index.ts - what the SESSION hub mounts for `#/session/flows`.
//
// `SessionHub.tsx` is another task's file, so this barrel is the seam: the hub
// imports `FlowsScreen` from here and the sub-nav switch is one line. The
// screen handles the canvas itself - `?open=<id>` swaps the hub body for
// `FlowsCanvasHost` at 768 px and up - so the hub never has to know that the
// Flows sub has two faces.

export { FlowsScreen, FLOWS_FOOTER, CANVAS_PHONE_REASON, CREATE_PHONE_HINT } from "./FlowsScreen";
export { FlowsCanvasHost, CANVAS_LIBRARY } from "./FlowsCanvasHost";
export { FlowRow, type FlowVerb, type FlowRowProps } from "./FlowRow";
