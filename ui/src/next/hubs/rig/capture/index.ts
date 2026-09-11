// hubs/rig/capture/index.ts - what the RIG hub's sub-nav mounts for `capture`.
//
// One named export. The RIG hub task wires `#/rig/capture` to it; nothing here
// reaches into the hub's own files, so the two tasks never share a line.

export { CaptureScreen, CAPTURE_NOTE, SAVE_OFF_NOTE, aimPlan } from "./CaptureScreen";
// `VIDEO_LOCK_REASON` used to be re-exported here. It is gone with the
// shortfall it described: the SER recorder landed (D-RIG-1), the mode is
// route state (`?mode=video`), and its refusals live on `captureGate`'s
// `videoRefusal`, which only VIDEO mode itself calls.
