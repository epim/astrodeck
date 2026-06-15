// RECONSTRUCTED (lane 1A owns this file) — restored to spec after an isolation
// type-check overwrote the original. 1A's canonical version takes precedence at
// merge. See reliability spec §11.

type LogInput = string | { source?: string; message?: string };

function parts(input: LogInput): { source: string; message: string } {
  if (typeof input === "string") return { source: "", message: input };
  return { source: input.source ?? "", message: input.message ?? "" };
}

/** Map a raw log line to a short human sentence; falls back to the raw message. */
export function humanizeLog(input: LogInput): string {
  const { source, message } = parts(input);
  const m = message.toLowerCase();
  if (source === "capture" || m.includes("camera")) {
    if (m.includes("not responding") || m.includes("timeout") || m.includes("disconnect")) {
      return "Camera isn't responding. Check the camera connection on the Rig page.";
    }
  }
  if (m.includes("nina") && (m.includes("5") || m.includes("http") || m.includes("error"))) {
    return "NINA reported an error. Check NINA on the imaging PC.";
  }
  if (m.includes("plate") && m.includes("solve")) {
    return "Plate-solve failed — check focus/exposure, or solve manually.";
  }
  if (m.includes("guid") && m.includes("lost")) {
    return "Guiding was lost — recovering.";
  }
  // Unknown — keep the raw message (truncated). Raw text always lives in the log.
  const trimmed = message.length > 140 ? `${message.slice(0, 137)}…` : message;
  return trimmed || "Something went wrong.";
}

/** Map a sequence error detail to a plain sentence + suggested action. */
export function humanizeSeqError(detail?: string): string {
  if (!detail) return "The run stopped unexpectedly. Check the log for details.";
  const d = detail.toLowerCase();
  if (d.includes("camera")) return "Camera isn't responding — check the Rig page.";
  if (d.includes("plate") && d.includes("solve")) {
    return "Plate-solve failed — check focus/exposure or solve manually.";
  }
  if (d.includes("mount") || d.includes("slew")) return "Mount move failed — check the Mount page.";
  if (d.includes("focus")) return "Autofocus failed — check the Focus page.";
  if (d.includes("guid")) return "Guiding failed — check the Guide page.";
  if (d.includes("cool")) return "Cooler didn't reach target — check the camera.";
  return detail.length > 160 ? `${detail.slice(0, 157)}…` : detail;
}
