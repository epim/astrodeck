// RECONSTRUCTED (lane 1A owns this file) — restored to spec after an isolation
// type-check overwrote the original. 1A's canonical version takes precedence at
// merge. See reliability spec §11.

type LogInput = string | { source?: string; message?: string };

// ------------------------------------------------------- busy-lane conflicts
//
// THE STRING THIS EXISTS FOR. Every long operation on the rig runs as a named
// background task, and asking for a second one on the same name is refused:
//
//     raise HTTPException(409, f"'{name}' is already running")
//         — server/astrodeck/api/app.py, _spawn()
//
// `name` is an INTERNAL lane id. api.ts lifts the 409's detail straight into
// ApiError.message and the call sites hand that to showToast, so the reward for
// pressing a control the rig had already taken was a red toast reading
// `'guide_assistant' is already running` or `'system.update' is already
// running`. It names a Python identifier, not the thing that is happening, and
// it says nothing at all about what to do next.
//
// The mapping is a TABLE rather than a chain of ifs for one reason: the lanes
// are the server's, they change when a route is added, and
// __tests__/laneConflict.test.ts reads every `_spawn("…")` literal out of
// app.py and fails if one of them has no entry here. A new lane is a red test,
// not a raw identifier in front of a user at 2 a.m.
//
// Each entry answers the two questions the refusal raises — what IS running,
// and what do I do — because "already running" alone is a dead end when the
// control that says it looks idle.
const LANE_CONFLICT: Record<string, string> = {
  goto: "The mount is already moving. Wait for it to arrive, or stop it from the Mount page first.",
  solve: "A plate-solve is already running. It takes a few seconds — wait for that one to answer.",
  autofocus: "An autofocus run is already going. Wait for it to finish, or stop it on the Focus page.",
  focuser: "The focuser is already travelling to a position. Wait for it to get there.",
  filter_offsets:
    "Filter offsets are already being measured. That run steps through every filter — wait for it to finish.",
  filterwheel: "The filter wheel is already changing filters. Wait for it to settle.",
  capture: "A frame is already being exposed. Wait for the exposure to finish, or abort it on the Capture page.",
  looping: "The capture loop is already running. Stop the loop before starting something else on the camera.",
  egain: "The gain and read-noise measurement is already running. Wait for it to finish.",
  guide: "Guiding is already starting up. Star search and calibration take a minute — wait, or stop guiding first.",
  dither: "A dither is already settling. Wait for the guider to report it settled.",
  guide_assistant:
    "The guiding assistant is already running. It measures for two to four minutes — wait, or stop it on the Guide page.",
  rotator: "The rotator is already turning. Wait for it to reach its angle.",
  // The roof lane parks the mount before it closes, so this can be several
  // minutes of travel with nothing visibly happening at the shutter.
  dome: "The roof is already opening or closing. It parks the mount first, so give it a few minutes.",
  rotate_to_pa: "The rotator is already turning to a position angle. Wait for it to get there.",
  "system.update": "An update is already installing. Leave the controller alone and watch its progress in Settings.",
  // Not a `_spawn` lane — app.py's _spawn_connect writes this one by hand for
  // the connect-by-profile task it keeps outside hub._busy.
  profile: "A profile is already being activated. Connecting the rig takes a few seconds — wait for it to land.",
};

// Anchored: only the server's exact refusal shape, so nothing else in a detail
// string can be swallowed and rewritten. Lane ids are python identifiers plus
// the one dotted name ("system.update").
const LANE_CONFLICT_RE = /^'([A-Za-z0-9_.-]+)' is already running$/;

/** Turn the server's `'<lane>' is already running` 409 into a sentence, or
 *  null when `message` is something else entirely.
 *
 *  Returns null rather than a fallback string on a non-match so the caller can
 *  tell "not my business" from "mapped": every other error message must pass
 *  through untouched. An UNKNOWN lane still gets a sentence — a lane id is
 *  never the right thing to show a person, even one this table has not met. */
export function humanizeLaneConflict(message: string): string | null {
  const m = LANE_CONFLICT_RE.exec(message.trim());
  if (!m) return null;
  return (
    LANE_CONFLICT[m[1]] ??
    "The rig is already busy with another job. Wait for it to finish, then try again."
  );
}

/** The lanes this table names, for the test that checks it against the server's
 *  own `_spawn` calls. Not for runtime branching — use `humanizeLaneConflict`. */
export const MAPPED_BUSY_LANES: readonly string[] = Object.keys(LANE_CONFLICT);

function parts(input: LogInput): { source: string; message: string } {
  if (typeof input === "string") return { source: "", message: input };
  return { source: input.source ?? "", message: input.message ?? "" };
}

/** Map a raw log line to a short human sentence; falls back to the raw message. */
export function humanizeLog(input: LogInput): string {
  const { source, message } = parts(input);
  // A lane refusal can arrive here too, not only as a 409 body: a route that
  // 409s inside a sequence step is logged verbatim and the store toasts every
  // error line. Same string, same treatment.
  const laneConflict = humanizeLaneConflict(message);
  if (laneConflict) return laneConflict;
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
  const laneConflict = humanizeLaneConflict(detail);
  if (laneConflict) return laneConflict;
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
