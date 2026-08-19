// How big a render to ask for when someone OPENS a frame.
//
// Not the grid. The grid tile is a scanning aid pinned to 256px; this is the
// picture the operator deliberately opened to look at, so it is sized to the
// screen in front of them — and re-sized when a phone is rotated, which changes
// which dimension the image is fitted by.

/** Kept in lockstep with `gallery.VIEW_WIDTH_STEPS` on the server; a server
 *  test asserts they match. Rounding to rungs keeps the render cache to a
 *  handful of keys instead of one per pixel of resize. */
export const VIEW_WIDTH_STEPS = [640, 960, 1280, 1600, 2048, 2560] as const;

/** Device pixels the image will actually occupy, given the box it must fit
 *  inside and its own aspect ratio.
 *
 *  THE ORIENTATION IS THE WHOLE POINT. A 3:2 frame in a portrait phone is
 *  limited by the box WIDTH; rotate to landscape and it is limited by the box
 *  HEIGHT, which asks for a different — usually larger — render. Fitting by the
 *  wrong axis is how you end up requesting a 640px image for a screen showing
 *  it 1600 device pixels wide. */
export function neededDeviceWidth(boxW: number, boxH: number,
                                  aspect: number, dpr: number): number {
  const w = Math.max(1, boxW), h = Math.max(1, boxH);
  const a = aspect > 0 ? aspect : 1.5;
  const fittedCssWidth = Math.min(w, h * a);   // `object-fit: contain`
  return Math.ceil(fittedCssWidth * Math.max(1, dpr));
}

/** Round up to a rung, capped at the largest. */
export function viewWidthFor(need: number): number {
  for (const step of VIEW_WIDTH_STEPS) if (need <= step) return step;
  return VIEW_WIDTH_STEPS[VIEW_WIDTH_STEPS.length - 1];
}

/** Should we fetch again after a resize/rotation?
 *
 *  UPGRADE ONLY. Rotating to an orientation that needs FEWER pixels leaves a
 *  sharper image than requested already on screen; re-fetching a smaller one
 *  would spend a render to make the picture worse. So "the appropriate size for
 *  the rotation" is honoured as "at least the appropriate size". */
export function shouldRefetch(currentWidth: number | null, needWidth: number): boolean {
  return currentWidth === null || needWidth > currentWidth;
}

export const viewPath = (path: string, width: number, v?: number): string =>
  `/api/gallery/view?path=${encodeURIComponent(path)}&w=${width}`
  + (v ? `&v=${Math.round(v)}` : "");
