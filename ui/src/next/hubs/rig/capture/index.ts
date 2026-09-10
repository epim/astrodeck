// hubs/rig/capture/index.ts - what the RIG hub's sub-nav mounts for `capture`.
//
// One named export. The RIG hub task wires `#/rig/capture` to it; nothing here
// reaches into the hub's own files, so the two tasks never share a line.

export { CaptureScreen, CAPTURE_NOTE, SAVE_OFF_NOTE, aimPlan } from "./CaptureScreen";
export { VIDEO_LOCK_REASON } from "./captureGate";
