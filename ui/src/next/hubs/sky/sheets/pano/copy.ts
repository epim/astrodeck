// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// Every sentence the panorama scanner shows (SPEC-v2 2.9, 2.11, 2.12), keyed, so the scanner, ScanView, PanoCapture
// and the review read one copy of each and a test can name the text it expects without retyping it.
//
// Placeholders are written in braces and filled by `fill`: {n} a count, {d} degrees, {from} and {to} the ends of an
// azimuth span, {s} a north sigma. The texts are the spec's words. Two are not in the spec's tables and are written
// here in the same voice: the `cameraStopped` error, for a camera whose track ended mid-scan (section 6 names the
// situation and asks for "the reason"), and the `acceptLow` and `northIsRight` button labels, which 2.9 shows in
// brackets and in its prose.
import type { CueKey } from './types';

/** 2.12, by key. 'error' carries no text of its own: its text is the specific error (2.3, 2.11). */
export const CUE_TEXT: Readonly<Record<CueKey, string>> = Object.freeze({
  error: '',
  ready: 'Hold the phone upright at your chest. Tilt it until the rail is green, then tap Start and turn slowly on the spot.',
  portrait: 'Turn the phone upright. The scan uses the tall side of the picture.',
  paused: 'Paused. Tap Resume to carry on.',
  'stalled-camera': 'The camera image stopped. Hold still for a moment, or tap Review to keep what you have.',
  'stalled-sensor': 'The motion sensor stopped reporting. Hold still for a second; the scan carries on when it returns.',
  stale: 'The motion sensor is lagging. Turn a little more slowly.',
  gap: 'Go back {d} degrees to fill the gap.',
  'too-fast': 'Too fast: the picture is smearing. Go back a little.',
  'tilt-up': 'Tilt up a little.',
  'tilt-down': 'Tilt down a little.',
  roll: 'Hold the phone upright.',
  mismatch: 'Turn more slowly and keep the phone steady.',
  dark: 'Too dark to find the treeline here. Keep turning; you can draw it yourself afterwards.',
  cap: "That's plenty. Tap Review.",
  turning: 'Keep turning. {d} deg to go.',
  almost: 'Almost round. Keep going past where you started.',
  'done-tall': 'Full circle. Something goes above the picture in {n} places; those are saved as blocked. Tap Review.',
  done: 'Full circle. Tap Review.',
});

/** 2.11, and the track-ended error of section 6. `farbled` is a note under Start, never a cue. */
export const ERROR_TEXT: Readonly<{ noOrientation: string; motionDenied: string; motionBlocked: string; farbled: string; cameraStopped: string }> = Object.freeze({
  noOrientation: "This browser does not report the phone's tilt. Draw the horizon by hand.",
  motionDenied: 'Motion access was denied. Allow motion sensors to scan, or draw the horizon by hand.',
  motionBlocked: 'Motion sensors are blocked for this site. In Brave or Chrome, open Site settings > Motion sensors and allow this site, then reopen the scan.',
  farbled: 'This browser changes camera pixels for privacy, which can blur the stitching. In Brave, set Shields fingerprinting to Standard for this site.',
  cameraStopped: 'The camera stopped sending pictures. Tap Review to keep what was scanned, or close the scan and open it again.',
});

/** 2.9: the notes under the strip, the north card and the review's other notes. */
export const REVIEW_TEXT: Readonly<{ lowSpans: string; acceptLow: string; unseen: string; unseenKept: string; dark: string; tall: string;
  northScan: string; northUnstable: string; northTrue: string; northMagnetic: string; noCompass: string; northIsRight: string;
  lowLight: string; hiddenEnd: string; gyroClosure: string; partial: string }> = Object.freeze({
  lowSpans: 'Traced but not certain: {n} places, {d} deg in all. Saved as blocked unless you accept the traced line.',
  acceptLow: 'Use the traced line for the uncertain parts',
  unseen: 'Not photographed: {from}-{to} deg. Saved as blocked.',
  unseenKept: 'Not photographed: {from}-{to} deg. Your earlier line is kept there.',
  dark: 'Too dark to read: {from}-{to} deg. Saved as blocked.',
  tall: 'Taller than the photo: {from}-{to} deg (at least {d} deg). Saved as blocked.',
  northScan: 'North: compass averaged over the scan, about \u00b1{s} deg. Metal near a mount can bias it by a fixed amount.',
  northUnstable: 'Unstable compass: about \u00b1{s} deg. Line N up with something you know.',
  northTrue: 'Corrected to true north.',
  northMagnetic: 'North is magnetic.',
  noCompass: 'No compass reading. Line up N with something you know is north, then tap North is right.',
  northIsRight: 'North is right',
  lowLight: 'Too dark to find the horizon automatically here. Draw it over the photo, or scan again before dark.',
  hiddenEnd: 'The scan stopped when the screen was switched away.',
  gyroClosure: 'The loop closed without a picture match.',
  partial: 'The ring did not close: north and lens are not refined.',
});

/** `template` with each {name} that `values` holds replaced by its value. A placeholder with no value is left as it
 *  is, so a caller that forgot one shows the brace on screen and in a test, rather than an empty gap in a sentence. */
export function fill(template: string, values: Readonly<Record<string, string | number>>): string {
  return template.replace(/\{(\w+)\}/g, (whole: string, name: string) =>
    (Object.prototype.hasOwnProperty.call(values, name) ? String(values[name]) : whole));
}
