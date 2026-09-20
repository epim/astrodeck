import { altAzOf } from "../lib/altaz";
import type { SkyPosition } from "./setup";

/** Display geometry only. The server plans and checks both possible RA arcs. */
export function alignmentSky(field: SkyPosition, arcDeg: number, latitude: number, longitude: number, now: number) {
  if (![field.ra_hours, field.dec_deg, arcDeg, latitude, longitude, now].every(Number.isFinite)) return null;
  const position = (ra: number) => {
    const p = altAzOf((ra % 24 + 24) % 24, field.dec_deg, latitude, longitude, now);
    return { alt: p.altDeg, az: p.azDeg };
  };
  return {
    field: position(field.ra_hours),
    arcs: arcDeg > 0 ? [-1, 1].map(direction => Array.from({length: 25}, (_, i) => position(field.ra_hours + direction * arcDeg / 15 * i / 24))) : [],
  };
}
