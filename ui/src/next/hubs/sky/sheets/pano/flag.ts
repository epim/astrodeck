// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// T25: the flag behind which everything new in the panorama scanner sits (SPEC-v2 2.1, D27). It has three values:
//   - 'off', the default: today's scanner and editor, unchanged (D24);
//   - 'pano': the new scanner and the v1 review;
//   - 'pano-sensor': the new scanner with alignment disabled, the control of 7.7, for side-by-side checks on a phone.
//
// How it is set. The hash query parameter `scanner` sets it, read the way the sheet reads `cameraFov`
// (`#/...?scanner=pano`), and the value persists to localStorage so a later visit without the parameter keeps it.
// `scanner=legacy` clears it. Any other value is no instruction: the stored value stands. Storage can be absent,
// blocked or full (private windows, cleared site data), so every read and write is wrapped and a failure only means
// the flag does not persist.

export type PanoFlag = 'off' | 'pano' | 'pano-sensor';

export const PANO_FLAG_KEY = 'astrodeck.pano.scanner';

function stored(): string | null {
  try { return localStorage.getItem(PANO_FLAG_KEY); } catch { return null; }
}

function remember(value: PanoFlag | null): void {
  try {
    if (value === null) localStorage.removeItem(PANO_FLAG_KEY);
    else localStorage.setItem(PANO_FLAG_KEY, value);
  } catch { /* private browsing: the flag simply does not persist */ }
}

/** The flag for a hash (`window.location.hash` when none is given). A `scanner=pano` or `scanner=pano-sensor`
 *  parameter wins and is stored, `scanner=legacy` clears the stored value and answers 'off', and without either the
 *  stored value answers, 'off' when there is none or it is not one of the two. */
export function panoFlag(hash?: string): PanoFlag {
  const h = hash ?? (typeof window === 'undefined' ? '' : window.location.hash);
  const q = h.indexOf('?');
  const asked = new URLSearchParams(q < 0 ? '' : h.slice(q + 1)).get('scanner');
  if (asked === 'pano' || asked === 'pano-sensor') { remember(asked); return asked; }
  if (asked === 'legacy') { remember(null); return 'off'; }
  const was = stored();
  return was === 'pano' || was === 'pano-sensor' ? was : 'off';
}
