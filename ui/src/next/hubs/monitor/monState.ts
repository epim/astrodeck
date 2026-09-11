// monState.ts - the one mono line under MONITOR / LOG / ALERTS.
//
// The prototype computes `a_monState` from its simulated run (`logic.js:591`);
// this is the same sentence built from the engine's own state, and it is shared
// by all three screens so the Log tab can never describe a different night from
// the Live tab.
//
// PURE. No store, no React: the hub screens pass in what they already read.

/** The engine's state as a word an operator uses. `nina_native` says who is
 *  driving rather than pretending this UI is: a run we are not running is not
 *  the same claim as a run we are. */
export function phaseWord(state: string): string {
  switch (state) {
    case "running": return "capturing";
    case "holding": return "holding";
    case "paused": return "paused";
    // "aborting" is a LIVE state for the ~210 s wind-down (types.ts) - the rig
    // is still moving, so the word has to be a verb in progress, not "stopped".
    case "aborting": return "stopping";
    case "complete": return "complete";
    case "aborted": return "stopped";
    case "error": return "failed";
    case "nina_native": return "NINA is driving";
    default: return "idle";
  }
}

/** `cloud hold · holding · M31`, `capturing · M31`, or `idle · nothing running`.
 *  The incident title leads when there is one, because that is the thing the
 *  operator opened the phone for. */
export function monState(inp: {
  state: string;
  target?: string | null;
  incidentTitle?: string | null;
}): string {
  const parts: string[] = [];
  if (inp.incidentTitle) parts.push(inp.incidentTitle.toLowerCase());
  parts.push(phaseWord(inp.state));
  if (inp.target) parts.push(inp.target);
  else if (inp.state === "idle") parts.push("nothing running");
  return parts.join(" · ");
}
