// help.ts — plain-language definitions for cryptic capture/sequence fields
// (onboarding spec §1a). Copy is corrected per critique2 #10 / D14 / D15:
//   - the offset claim is fixed ("does not add real signal"),
//   - HFR / step-size drop absolute rig numbers and caveat by pixel scale,
//   - the OSC binning caveat is restored.
// One content registry backs both the inline InfoDot/Tooltip affordance and the
// per-panel "What do these mean?" reference sheet. Keyed by HelpKey; the
// Checklist's `CheckItem.help` and any HelpSheet pull strings from here.

export const HELP = {
  offset:
    "Camera offset is a fixed pedestal added to every pixel so read noise never clips at zero (which loses shadow detail). Use your camera's recommended value — too low clips the darkest pixels, too high wastes dynamic range. It shifts the whole image up by a constant; it does not add real signal to your subject.",
  hfr:
    "Half-Flux Radius — the radius of the circle containing half a star's light. Lower means sharper focus. What counts as 'good' depends on your pixel scale (focal length + pixel size + seeing), so watch the MINIMUM HFR your own rig reaches and refocus when it climbs ~20% above that, rather than chasing a fixed number like 1.5–3px.",
  stepSize:
    "Focuser step size: motor steps moved between autofocus samples. Larger gives a wider, coarser search; too large overshoots the V-curve, too small wastes time. The right value depends on your focuser and scope — start from your EAF/scope's recommended step and adjust.",
  dither:
    "Dither nudges the mount a few pixels between frames so hot pixels and noise land in different places and average out when stacking. 'Every N frames' sets how often; the pixels value sets the nudge size (a few pixels is typical).",
  hfrReject:
    "Flag soft frames: if a frame's HFR exceeds this multiple of the running median, it's flagged (e.g. 1.5× = 50% softer than typical). 0 = off. Catches wind gusts, passing cloud, and focus drift.",
  filterOffset:
    "Per-filter focus offset: filters can focus at slightly different points. When set, the focuser shifts automatically on a filter change so you don't have to refocus every time.",
  coolTo:
    "Cool the sensor to this temperature before lights and hold it there. Colder means less thermal noise and lets you reuse a dark library. Blank = no cooling (uncooled camera). The sequence cools and waits at the start — you don't need to pre-cool before pressing Run.",
  meridianFlip:
    "German equatorial mounts reach the pier as a target crosses the meridian (due south). A flip swaps the scope to the other side of the pier and re-centers by plate-solving. Turn it off for fork or alt-az mounts (which don't flip).",
  binning:
    "Binning combines N×N pixels into one: brighter, lower-resolution, less data. On mono cameras, 1× for final lights and 2× for fast framing/focus. On one-shot-colour (OSC) cameras, hardware binning destroys the Bayer pattern, so '2×' is usually done in software and changes colour handling — prefer 1× for OSC lights.",
} as const;

export type HelpKey = keyof typeof HELP;

/** Safe lookup — returns the definition for a key, or undefined for an unknown key. */
export function helpText(k: string | undefined): string | undefined {
  if (!k) return undefined;
  return (HELP as Record<string, string>)[k];
}
