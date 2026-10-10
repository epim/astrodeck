// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// atlasLastField.ts - the last field the Atlas looked at, kept in THIS browser.
//
// What it is for (#956). A free-roam Atlas needs somewhere to open when the
// mount cannot say where it points (`position_known` false, or no mount block).
// The answer is where THIS viewer was last looking, and failing that one fixed
// field (`store.openFraming`'s 0h +0). It is never computed from anything else.
//
// WHAT IT MUST NOT BE DERIVED FROM: the saved site. A view centre made from the
// site's zenith is (local sidereal time, latitude), so it would hand the site's
// latitude to everything that reads the centre, and the centre leaves the rig:
// the opt-in online survey fetch sends it to CDS, the wizard's prefill writes it
// into a flow's TARGET, GOTO slews to it (the #19 / #140 class). Nothing in this
// module takes a site, a longitude or a clock, and the test that guards that
// (w21AtlasViewNoPosition) opens the view under two different saved sites.
//
// It is a client preference in the same sense as `astrodeck-survey-bright`:
// per browser, so per viewer, never sent anywhere, and every access is wrapped
// because storage can be absent, full or blocked. It is dropped when the sign-in
// gate closes over a session (`store.setAuthGate`), with the framing it
// belonged to.

import { wrapRaHours } from "./framing";

/** The one localStorage key. Exported so a test can seed and read the same
 *  place the store does. */
export const LAST_FIELD_KEY = "astrodeck-atlas-last-field";

export interface LastField {
  ra_hours: number;
  dec_deg: number;
}

/** The stored field, or `null` when there is none, storage is unavailable, or
 *  what is there is not a sky position (a hand-edited or older value must not
 *  open the page on NaN). */
export function readLastField(): LastField | null {
  try {
    const raw = localStorage.getItem(LAST_FIELD_KEY);
    if (!raw) return null;
    const p = JSON.parse(raw) as Partial<LastField> | null;
    if (!p) return null;
    const { ra_hours, dec_deg } = p;
    if (typeof ra_hours !== "number" || !Number.isFinite(ra_hours) || ra_hours < 0 || ra_hours >= 24) return null;
    if (typeof dec_deg !== "number" || !Number.isFinite(dec_deg) || Math.abs(dec_deg) > 90) return null;
    return { ra_hours, dec_deg };
  } catch {
    return null;
  }
}

/** Remember `c` as the last field viewed. A pan fires this dozens of times a
 *  second, so an unchanged value is not rewritten. RA is folded into [0, 24) as
 *  the Atlas's own `setCenter` does; a value that is not a sky position at all
 *  is not stored. */
export function rememberLastField(c: LastField): void {
  if (!Number.isFinite(c.ra_hours)) return;
  if (!Number.isFinite(c.dec_deg) || Math.abs(c.dec_deg) > 90) return;
  const serialized = JSON.stringify({ ra_hours: wrapRaHours(c.ra_hours), dec_deg: c.dec_deg });
  try {
    if (localStorage.getItem(LAST_FIELD_KEY) === serialized) return;
    localStorage.setItem(LAST_FIELD_KEY, serialized);
  } catch {
    /* quota / unavailable - the field is simply not remembered */
  }
}

/** Forget the remembered field. */
export function forgetLastField(): void {
  try {
    localStorage.removeItem(LAST_FIELD_KEY);
  } catch {
    /* unavailable - nothing to forget */
  }
}
