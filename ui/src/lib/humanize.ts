// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
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
// Each entry answers the two questions the refusal raises - what IS running,
// and what do I do - because "already running" alone is a dead end when the
// control that says it looks idle.
//
// AND IT NAMES THE ACTION, NEVER A PAGE. Two front-ends share this file: the
// classic one (Mount / Focus / Capture / Guide pages) and the new one (a Rig hub
// of device sheets with a Capture sub-screen, no such pages at all). "stop it on
// the Focus page" was therefore a direction to somewhere half the users of this
// string cannot go, and a rename of any screen silently makes the other half
// wrong too. "stop the autofocus first" is the same instruction, tied to the
// thing that is running rather than to where the button happens to live this
// month - so it survives both shells and any future one.
// Guarded by __tests__/laneConflict.test.ts, which fails on the word "page" in
// any sentence here.
const LANE_CONFLICT: Record<string, string> = {
  goto: "The mount is already slewing. Wait for it to arrive, or stop the mount first.",
  solve: "A plate-solve is already running. It takes a few seconds - wait for that one to answer.",
  autofocus: "An autofocus run is already going. Wait for it to finish, or stop the autofocus first.",
  focuser: "The focuser is already travelling to a position. Wait for it to get there.",
  filter_offsets:
    "Filter offsets are already being measured. That run steps through every filter - wait for it to finish.",
  filterwheel: "The filter wheel is already changing filters. Wait for it to settle.",
  capture: "An exposure is already in flight. Wait for it to finish, or abort the capture first.",
  looping: "The capture loop is already running. Stop the loop before starting something else on the camera.",
  egain: "The gain and read-noise measurement is already running. Wait for it to finish.",
  guide: "Guiding is already starting up. Star search and calibration take a minute - wait, or stop guiding first.",
  dither: "A dither is already settling. Wait for the guider to report it settled.",
  guide_assistant:
    "The guiding assistant is already running. It measures for two to four minutes - wait for it, or stop the assistant first.",
  rotator: "The rotator is already turning. Wait for it to reach its angle.",
  // The roof lane parks the mount before it closes, so this can be several
  // minutes of travel with nothing visibly happening at the shutter.
  dome: "The roof is already opening or closing. It parks the mount first, so give it a few minutes.",
  rotate_to_pa: "The rotator is already turning to a position angle. Wait for it to get there.",
  "system.update": "An update is already installing. Leave the controller alone and watch its progress in Settings.",
  // Not a `_spawn` lane — app.py's _spawn_connect writes this one by hand for
  // the connect-by-profile task it keeps outside hub._busy.
  profile: "A profile is already being activated. Connecting the rig takes a few seconds - wait for it to finish.",
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

// ------------------------------------------------------- keyword rewrites
//
// A rewrite REPLACES the line it matches, so it may only fire on a line that IS
// the report it was written for. The plate-solve and guiding rules below, and
// the HTTP 5xx arm of the NINA rule, are keyed on that report's own shape, never
// on two words that happen to sit somewhere in the same text. The bare keyword
// pairs these replaced rewrote honest copy they were never written for, and
// showToast routes EVERY message through here:
//
//   "... a plate-solve sync, or TRUST POSITION, unlocks them"   (#792)
//       became "Plate-solve failed - check focus/exposure", the opposite of it;
//   "re-centring after guiding was lost: the mount refused the sync ..." (#850)
//       became "Guiding was lost - recovering", hiding the refusal and the stop;
//   "slow request GET /api/nina/health: still waiting after 15.0 s"
//       became "NINA reported an error" because "15.0" holds a 5.
//
// A new line that mentions a plate solve or guiding in its own words need not
// avoid the words. Two arms are still bare words: "nina" beside "http" or
// "error", and "camera" beside "not responding", "timeout" or "disconnect" (the
// camera rule in humanizeLog below). A server line that can hold those pairs
// still breaks them apart (api/slow_requests.py `_HUMANIZER_KEYS`).

/** A plate solve that FAILED, as a bare statement, and nothing else in the line.
 *  The replacement sends the operator to focus and exposure: right for a failure
 *  that names no cause, wrong for one that does. "... failed: no light: the
 *  optic is capped" and "... failed: no plate solver is available on this rig"
 *  ARE the answer, and replacing them sent an operator with a lens cap on, or a
 *  rig with no solver, to the focuser (#960). So the line must BE the failure:
 *  an optional "label: " (a source or a stage, "solve failed: "), an optional
 *  article, the failure phrase, and closing punctuation. Anything after the
 *  phrase is a cause or a consequence ("... failed: <cause>", "... failed (no
 *  stars); using raw GoTo", "... error: timed out") and the line is kept as
 *  written.
 *
 *  "plate solve" with no failure after it ("plate solve: filter L -> Lum", "a
 *  plate-solve sync") is not a failure, and neither is a hypothetical ("if the
 *  plate solve fails"). Past tense or the noun, because that is how the server
 *  and NINA word a failure. A label starts on a non-space and holds no colon of
 *  its own, so the repeat cannot split one run of spaces two ways. */
const PLATE_SOLVE_FAILED =
  /^\s*(?:[^\s:;][^:;]{0,29}:\s*)*(?:(?:the|a)\s+)?plate[- ]?solv(?:e|ing)\s+(?:failed|failure|error|timed out)\b[\s.!]*$/;

/** An HTTP 5xx status as a TOKEN: three digits starting with 5, standing alone
 *  and not followed by a unit or a decimal part. The 5 in "15.0 s" or "5 in
 *  flight" is not one, nor is "500 ms", "a reply of 512 bytes", "after 523.4 s"
 *  (the slow-request formatter writes its elapsed time with one decimal, on the
 *  closing line of a request that SUCCEEDED) or "500,000 bytes". */
const HTTP_5XX = /\b5\d\d\b(?![.,]\d)(?!\s*(?:(?:ms|s|sec|secs|bytes|kb|mb|gb)\b|%))/;

/** Guiding reported lost, and NOTHING else in the line. The replacement claims
 *  "recovering", so a line that goes on to say something else ("... lost: the
 *  mount refused the sync", "... lost and did not recover") can contradict it,
 *  and a line that only mentions guiding being lost on the way to another point
 *  ("re-centring after guiding was lost") is not this report at all. What is
 *  allowed around the report: a short "label: " before it (a source or a
 *  device), and a bracketed note ("(reacquire 1/3)") or stop after it. */
const GUIDING_LOST =
  /^(?:[^:;]{0,30}:\s*)?(?:native\s+)?guid(?:ing|er|e)(?:\s+(?:was|has been|is))?\s+lost\b(?:\s+the\s+guide\s+star)?\s*(?:\([^)]*\))?[\s.!]*$/;

// The length past which an unrecognised line is shortened, and the one past
// which even a single sentence is (a line that long is a dump, not a sentence).
// It was 140, which by a static count of the server's `bus.log("error", ...)`
// literals cut about one error line in eight and, in a refusal, cut the clause
// that names the cause or the repair. The longest of those lines is roughly 430
// characters; 400 shows all but that one whole.
export const CLIP_AT = 400;
const CLIP_MAX = 2 * CLIP_AT;

/** Shorten a line that is too long for a toast WITHOUT cutting a sentence in
 *  half. The old rule cut at 137 characters wherever that fell, and what falls
 *  there in a refusal is the clause that names the cause or the repair
 *  ("... clear the protection for this port in Power se..."). Whole sentences
 *  only: keep as many leading sentences as fit in CLIP_AT, or the first one
 *  whole when it alone is longer, and say that more follows. A line break ends a
 *  sentence too, so a stack trace keeps its first line. Only a single sentence
 *  past CLIP_MAX is cut mid-way, at a word, as the dump it must be. A line that
 *  fits CLIP_AT is never touched, so a cause and its repair that share a toast
 *  both reach it. */
function clip(message: string): string {
  if (message.length <= CLIP_AT) return message;
  const ends: number[] = [];
  const stop = /[.!?]+(?=\s)|\n/g;
  for (let s = stop.exec(message); s; s = stop.exec(message)) {
    const at = s.index + (s[0] === "\n" ? 0 : s[0].length);
    if (at > 0) ends.push(at);
  }
  ends.push(message.length);
  const fits = ends.filter((e) => e <= CLIP_AT);
  const end = fits.length ? fits[fits.length - 1] : ends[0];
  if (end >= message.length && end <= CLIP_MAX) return message;
  if (end <= CLIP_MAX) return `${message.slice(0, end).trimEnd()} …`;
  return `${message.slice(0, CLIP_MAX).replace(/\s+\S*$/, "")}…`;
}

/** Map a raw log line to a short human sentence; falls back to the raw message.
 *
 *  The mappings are for the report each one names; a line that merely mentions
 *  the same words is passed through (see the keyword rewrites above).
 *
 *  `opts.verbatim` keeps the fall-back WHOLE. The shortening below exists for
 *  the log stream, where a raw line can be a stack trace and the full text is
 *  one tap away in the drawer. A server REFUSAL is the opposite case: it is a
 *  complete sentence written to be read, its repair is usually the last clause
 *  ("... stop the run first"), and there is no drawer behind a toast holding
 *  the rest of it. Callers that know they are showing a refusal pass
 *  `{ verbatim: true }`; every mapping above still applies, because a lane
 *  conflict is better read as its sentence whoever asked. */
export function humanizeLog(input: LogInput, opts?: { verbatim?: boolean }): string {
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
  if (m.includes("nina") && (HTTP_5XX.test(m) || m.includes("http") || m.includes("error"))) {
    return "NINA reported an error. Check NINA on the imaging PC.";
  }
  if (PLATE_SOLVE_FAILED.test(m)) {
    return "Plate-solve failed - check focus/exposure, or solve manually.";
  }
  if (GUIDING_LOST.test(m)) {
    return "Guiding was lost - recovering.";
  }
  // Unknown - keep the raw message (shortened to whole sentences, unless the
  // caller asked for it whole). Raw text always lives in the log.
  const kept = opts?.verbatim ? message : clip(message);
  return kept || "Something went wrong.";
}

/** Map a sequence error detail to a plain sentence + suggested action. */
export function humanizeSeqError(detail?: string): string {
  if (!detail) return "The run stopped unexpectedly. Check the log for details.";
  const laneConflict = humanizeLaneConflict(detail);
  if (laneConflict) return laneConflict;
  const d = detail.toLowerCase();
  // "Rig page" is left standing deliberately: the new IA HAS a Rig hub, and it
  // is the one surface a disconnected camera is actually fixed on. Only the
  // screens the new shell does not have were reworded.
  if (d.includes("camera")) return "Camera isn't responding - check the Rig page.";
  if (d.includes("plate") && d.includes("solve")) {
    return "Plate-solve failed - check focus/exposure or solve manually.";
  }
  // Same rule as the lane table above: name the thing to do, not the screen to
  // do it on - these three sentences are read on both front-ends.
  if (d.includes("mount") || d.includes("slew")) {
    return "Mount move failed - check the mount is connected, unparked and tracking.";
  }
  if (d.includes("focus")) return "Autofocus failed - re-run autofocus, or set focus by hand.";
  if (d.includes("guid")) return "Guiding failed - re-run the calibration, or pick a brighter guide star.";
  if (d.includes("cool")) return "Cooler didn't reach target - check the camera.";
  return detail.length > 160 ? `${detail.slice(0, 157)}…` : detail;
}
