// weatherModel.ts - every decision and every sentence the rebuilt Weather
// settings panel prints (wave R7, T-R7-15; plan section 3.F18).
//
// Pure: no React, no store, no fetch. It is here rather than inline in the
// panel so a test grades the copy the screen prints instead of copy the test
// typed itself, and so the bounds this UI enforces sit next to the server
// bounds they mirror.
//
// THE BOUNDS ARE THE SERVER'S, RESTATED. `config.py` WeatherConfig has
// `cloud_threshold_pct` ge=0 le=100 and `sustain_minutes` ge=15 le=240. A value
// outside them is a 422 at binding, not a clamp, so the field clamps first and
// the operator gets a number rather than an error. If the server ever widens
// them, this file is the one place to follow.

/** Percent of sky covered that counts as clouded over. Server: ge=0, le=100. */
export const THRESHOLD_MIN = 0;
export const THRESHOLD_MAX = 100;

/** How long the forecast has to stay above the threshold before it counts.
 *  Server: ge=15, le=240. The floor is 15 because the forecast series itself is
 *  quarter-hourly - a shorter window cannot contain two samples. */
export const SUSTAIN_MIN = 15;
export const SUSTAIN_MAX = 240;

export const WX_TITLE = "Weather";

export const WX_INTRO =
  "Cloud forecast, radar and the high-cloud night warning. One threshold drives both the warning "
  + "and the auto-resume hold, so a number changed here changes both. Switching this on makes the "
  + "home server fetch forecasts for the configured site.";

export const WX_ENABLED_NOTE =
  "Off means no forecast is fetched at all - the conditions screen, the radar tiles and the night "
  + "warning all go quiet.";

/** The default site is a placeholder, not a location. Open-Meteo answers for it
 *  perfectly happily, which is the trap: the forecast looks real. */
export const WX_DEFAULT_SITE_WARN =
  "The site is still the default one, so the forecast will describe somewhere the rig is not. "
  + "Set the observing site before trusting anything on the conditions screen.";

export const WX_EMPTY_TITLE = "NO WEATHER BLOCK YET";
export const WX_EMPTY_HINT =
  "The rig has not sent its weather configuration, so these settings cannot be read or changed. "
  + "They appear as soon as the link brings the config through.";

export const ASTRO_LABEL = "Astrospheric API key";

export const ASTRO_NOTE =
  "Optional, North America only: seeing and transparency, which Open-Meteo does not carry. Polled "
  + "every 6 h at 5 credits a call against a 100-a-day Pro budget - never faster.";

/** The one sentence that explains why the box is empty even when a key is
 *  stored. Without it an empty box reads as "no key", and the operator retypes
 *  a key they did not need to. */
export const ASTRO_SET_HINT =
  "Stored on the rig. It is never sent back to this screen, so the box stays empty - leave it that "
  + "way to keep the key you already saved.";

export const ASTRO_UNSET_HINT =
  "Sent once and never read back. Without it the seeing and transparency tiles say they are not "
  + "measured rather than guessing.";

export const ASTRO_PLACEHOLDER_SET = "(unchanged)";
export const ASTRO_PLACEHOLDER_UNSET = "optional - Astrospheric Pro membership";

/** Why SAVE KEY refuses while the box is empty. An empty write means "keep the
 *  stored key", so the button would be a no-op that looked like a save. */
export const ASTRO_EMPTY_REASON =
  "type a key first - an empty box already means keep the one on the rig";

export const ASTRO_CLEAR_LABEL = "CLEAR KEY";
export const ASTRO_CLEAR_ARM = "CONFIRM CLEAR";
export const ASTRO_SAVE_LABEL = "SAVE KEY";

export const WX_SAVE_FAILED = "Could not save. The rig still has the settings shown.";

export const WX_CONFLICT =
  "The configuration changed somewhere else while this was open. It has been reloaded - re-apply "
  + "the edit.";

export const WX_KEY_SAVED = "Key sent. The rig holds it now; this screen cannot read it back.";
export const WX_KEY_CLEARED = "Key cleared. Seeing and transparency go back to not measured.";

/** "set" / "not set" - the ONLY thing the server will say about a stored
 *  secret (`astrospheric_configured`), and the difference between a blank box
 *  that means "nothing saved" and a blank box that means "saved, not shown". */
export function keyStateLabel(configured: boolean): string {
  return configured ? "set" : "not set";
}

/** What the two numbers currently mean, as one sentence. The pair is a policy,
 *  and reading "50" and "30" in two boxes does not say what the rig will do
 *  with them. */
export function policyLine(thresholdPct: number, sustainMinutes: number): string {
  return `Cloud at or above ${thresholdPct}% for a solid ${sustainMinutes} min counts as clouded `
    + "over: that is what the night warning names and what auto-resume waits out.";
}

/** The read-only sentence for `LockNote`. Lower-case because `LockNote`
 *  prefixes "Read-only - ", and it names the capability so the reason can be
 *  matched against the server's own refusal. */
export function weatherLockSentence(reason: string): string {
  return `changing the weather settings ${reason} (config.site_optics)`;
}
