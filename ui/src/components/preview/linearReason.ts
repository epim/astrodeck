// Why a linear-data tool is unavailable — the TRUE reason, not a guess.
//
// `linearEnabled = !!preview && !isNina && preview.data_is_linear` has three
// ways to be false, and the magnifier and full-res-export tooltips both blamed
// exactly one of them unconditionally:
//
//     "The magnifier needs linear data — this frame came from NINA"
//
// Seen on the rig 2026-08-19, which has no NINA and, at that moment, no frame
// at all: the screen named a cause that could not be true. A wrong "why" is
// worse than none — it sends the operator to look at something that is not
// there.

export interface LinearState {
  hasFrame: boolean;
  isNina: boolean;
  isLinear: boolean;
}

/** null when the tool IS available. */
export function linearUnavailableReason(s: LinearState, tool: string): string | null {
  if (!s.hasFrame) return `${tool} needs a frame — none has been taken yet`;
  if (s.isNina) return `${tool} needs linear data — this frame came from NINA already stretched`;
  if (!s.isLinear) return `${tool} needs linear data — this frame is already stretched`;
  return null;
}
