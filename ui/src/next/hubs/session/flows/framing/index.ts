// framing/index.ts - the barrel for the #/next framing area (#189 S4 item 5;
// spec 2026-09-23 flows mosaic, 2.1).
//
// One sheet, `flowFrame`, and the two helpers every door into it uses. The
// modal itself is NOT here and is not re-implemented anywhere under `next/`:
// `FlowFrameSheet` mounts the shared `components/flows/framing` sheet through
// its lazy door, so importing this barrel costs the adapter and the router,
// never the modal.
//
// The session hub's registry imports `./reg`, not this barrel (see
// `session/sheets/index.ts`): the barrel re-exports the sheet component, and a
// registry that imported it would put the component in the entry chunk.

export {
  FlowFrameSheet, FLOW_FRAME_SHEET, FRAME_LOADING, flowFrameParams, openFlowFrame,
} from "./FlowFrameSheet";
export { flowFrameSheets } from "./reg";
