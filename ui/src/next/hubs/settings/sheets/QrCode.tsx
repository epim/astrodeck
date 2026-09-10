// QrCode.tsx - draws a `next/lib/qr.ts` symbol as one SVG path.
//
// NEVER THEMED, AND THAT IS THE POINT. Every other surface in this UI swaps its
// palette under `:root.night` and again under the design's dark ground. A QR
// code must not: a camera decoder looks for DARK modules on a LIGHT field, and
// the polarity is part of the symbol, not a style. Inverted or red-dimmed, the
// three finder patterns stop reading as finders and the phone simply never
// locks on. So the background rect is `#ffffff` and the modules are `#000000`,
// written as literals rather than tokens so nobody can theme them by accident.
// The four-module quiet zone is inside the viewBox for the same reason: a
// decoder needs that white margin, and a card background cannot be relied on to
// provide it once this ends up on a dark sheet.
//
// The whole symbol is one `<path>` of run-merged rects rather than a rect per
// module, which keeps a version 10 code (57x57 = 3249 modules) to a few hundred
// DOM-free path commands.

import { useMemo, type JSX } from "react";
import { QR_MAX_BYTES, encodeQr, qrByteLength, qrPath } from "../../../lib/qr";

/** Modules of white margin around the symbol. Four is the standard's minimum. */
const QUIET = 4;

/** Below this the modules of a version 10 symbol fall under one CSS pixel each
 *  and a phone camera cannot resolve them. */
const MIN_PX = 160;

export interface QrCodeProps {
  /** The payload. Renders nothing when empty. */
  text: string;
  /** Drawn size in CSS pixels, floored at 160. */
  px?: number;
  /** The accessible name. Defaults to naming the payload, because a screen
   *  reader user cannot point a camera at it and needs the address itself. */
  label?: string;
}

/**
 * A scannable QR code for `text`, or nothing at all when there is nothing to
 * encode. Callers that show a caption beside the code should ask
 * `qrByteLength(text) <= QR_MAX_BYTES` first, so the caption never outlives the
 * code it describes.
 */
export function QrCode({ text, px = 200, label }: QrCodeProps): JSX.Element | null {
  const sym = useMemo(() => {
    if (!text) return null;
    if (qrByteLength(text) > QR_MAX_BYTES) return null;
    try {
      return encodeQr(text);
    } catch {
      // The capacity check above is the only failure this encoder has, so this
      // is defence against a future one: draw nothing rather than take the
      // sheet down with it.
      return null;
    }
  }, [text]);

  if (sym == null) return null;

  const side = Math.max(MIN_PX, Math.round(px));
  const span = sym.size + QUIET * 2;

  return (
    <svg
      role="img"
      aria-label={label ?? `QR code for ${text}`}
      data-testid="conn-pair-qr"
      data-qr-version={sym.version}
      width={side}
      height={side}
      viewBox={`0 0 ${span} ${span}`}
      // Without this the browser antialiases module edges and a small code
      // reads as a grey blur to the camera.
      shapeRendering="crispEdges"
      style={{ display: "block", borderRadius: 8 }}
    >
      <rect x={0} y={0} width={span} height={span} fill="#ffffff" />
      <path d={qrPath(sym, QUIET)} fill="#000000" />
    </svg>
  );
}

export default QrCode;
