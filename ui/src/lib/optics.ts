// Optics math — the ONLY client-side optics arithmetic (one multiply).
// Mirrors the backend constant in server/astrodeck/config.py EXACTLY (A.7).
// Persisted truth always comes from the server echo (optics_computed); this is
// for instant, network-free UI feedback while editing (settings spec §2.9).

export const ARCSEC_PER_RAD = 206.265; // 206265 arcsec/rad / 1000 (µm↔mm)

/** Image scale in arcsec/px. `bin` defaults to 1 (bin-independent FOV uses bin 1). */
export function scale(fl: number, px: number, bin = 1): number {
  return fl > 0 ? (ARCSEC_PER_RAD * px * bin) / fl : 0;
}

/** Field of view in degrees. Always computed at bin 1 (bin-independent, ASTAP-correct). */
export function fov(
  fl: number,
  px: number,
  w: number,
  h: number,
): { w: number; h: number; diag: number } {
  const s = scale(fl, px, 1);
  const fw = (s * w) / 3600;
  const fh = (s * h) / 3600;
  return { w: fw, h: fh, diag: Math.hypot(fw, fh) };
}
