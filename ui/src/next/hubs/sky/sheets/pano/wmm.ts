// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// Magnetic declination from the World Magnetic Model WMM2025 (public domain,
// NOAA NCEI; the coefficients and their provenance are in wmm2025.ts).
//
// The panorama scanner reads north from a compass, which gives MAGNETIC north.
// The sheet adds this declination, at the site it already loads, to make the
// azimuths true. It is evaluated in the sheet and kept inside a closure there.
// A declination worked out for a real site is a function of that site's
// position, so nothing in this file logs, prints or stores one, and a bad input
// is refused without quoting it.
//
// The synthesis follows the WMM technical report: the geodetic position is
// converted to geocentric spherical coordinates, the Schmidt semi-normalised
// associated Legendre functions are built to degree 12 with their derivatives
// in latitude, the Gauss coefficients are advanced from the 2025.0 epoch by
// their secular variation, the north, east and down components are summed, and
// the north and down are rotated back to the geodetic frame.
//
//   D = atan2(Y, X), in degrees, east positive.
import { WMM2025 } from './wmm2025';

const DEG = Math.PI / 180;
// WGS-84 ellipsoid, kilometres.
const SEMI_MAJOR_KM = 6378.137;
const FLATTENING = 1 / 298.257223563;
const E2 = FLATTENING * (2 - FLATTENING);
// The geomagnetic reference radius of the model, kilometres.
const REFERENCE_RADIUS_KM = 6371.2;
const MAX_DEGREE = 12;
// The model is valid for decimal years 2025.0 to 2030.0, ends included.
const VALID_FROM = 2025.0;
const VALID_TO = 2030.0;

/** Index of the (n, m) term in the triangular arrays, n >= m >= 0. */
const tri = (n: number, m: number): number => (n * (n + 1)) / 2 + m;

/**
 * Magnetic declination, degrees east of true north, at a geodetic position and
 * time. `heightKm` is above the WGS-84 ellipsoid; a site's height above mean
 * sea level is within a hundred metres of it, which is far below the model's
 * accuracy. Throws a RangeError outside decimal years 2025.0 to 2030.0, and for
 * a latitude beyond +-90 or a non-finite number.
 */
export function declinationDeg(latDeg: number, lonDeg: number, heightKm: number, decimalYear: number): number {
  if (!(decimalYear >= VALID_FROM && decimalYear <= VALID_TO)) {
    throw new RangeError(`WMM2025 is valid for 2025.0 to 2030.0, not ${decimalYear}`);
  }
  // No value is quoted here: these are a site's position.
  if (!Number.isFinite(latDeg) || Math.abs(latDeg) > 90) throw new RangeError('latitude must be a number within +-90');
  if (!Number.isFinite(lonDeg) || !Number.isFinite(heightKm)) throw new RangeError('longitude and height must be finite numbers');

  // Geodetic to geocentric.
  const lat = latDeg * DEG;
  const sinLat = Math.sin(lat);
  const cosLat = Math.cos(lat);
  const primeVertical = SEMI_MAJOR_KM / Math.sqrt(1 - E2 * sinLat * sinLat);
  const p = (primeVertical + heightKm) * cosLat;
  const z = (primeVertical * (1 - E2) + heightKm) * sinLat;
  const r = Math.hypot(p, z);
  const sinPsi = z / r;
  const cosPsi = p / r;
  const psi = Math.atan2(z, p);

  // Schmidt semi-normalised associated Legendre functions of sin(psi), and
  // their derivatives with respect to psi.
  const size = tri(MAX_DEGREE, MAX_DEGREE) + 1;
  const P = new Float64Array(size);
  const dP = new Float64Array(size);
  P[0] = 1;
  for (let n = 1; n <= MAX_DEGREE; n++) {
    for (let m = 0; m <= n; m++) {
      const i = tri(n, m);
      if (m === n) {
        const k = n === 1 ? 1 : Math.sqrt((2 * n - 1) / (2 * n));
        const j = tri(n - 1, n - 1);
        P[i] = k * cosPsi * P[j];
        dP[i] = k * (cosPsi * dP[j] - sinPsi * P[j]);
      } else {
        const j1 = tri(n - 1, m);
        const scale = 1 / Math.sqrt(n * n - m * m);
        let lowerP = 0;
        let lowerDP = 0;
        let lowerK = 0;
        if (m <= n - 2) {
          const j2 = tri(n - 2, m);
          lowerP = P[j2];
          lowerDP = dP[j2];
          lowerK = Math.sqrt((n - 1) * (n - 1) - m * m);
        }
        P[i] = ((2 * n - 1) * sinPsi * P[j1] - lowerK * lowerP) * scale;
        dP[i] = ((2 * n - 1) * (cosPsi * P[j1] + sinPsi * dP[j1]) - lowerK * lowerDP) * scale;
      }
    }
  }

  // Sum the field. (a/r)^(n+2), cos(m lambda) and sin(m lambda) are tabled.
  const ratio = REFERENCE_RADIUS_KM / r;
  const radial: number[] = [];
  for (let n = 0; n <= MAX_DEGREE; n++) radial.push(ratio ** (n + 2));
  const lambda = lonDeg * DEG;
  const cosM: number[] = [];
  const sinM: number[] = [];
  for (let m = 0; m <= MAX_DEGREE; m++) {
    cosM.push(Math.cos(m * lambda));
    sinM.push(Math.sin(m * lambda));
  }
  const dt = decimalYear - WMM2025.epoch;
  let xGeocentric = 0;
  let yGeocentric = 0;
  let zGeocentric = 0;
  for (const [n, m, g0, h0, gDot, hDot] of WMM2025.rows) {
    const g = g0 + gDot * dt;
    const h = h0 + hDot * dt;
    const i = tri(n, m);
    const cosTerm = g * cosM[m] + h * sinM[m];
    const sinTerm = g * sinM[m] - h * cosM[m];
    xGeocentric -= radial[n] * cosTerm * dP[i];
    yGeocentric += radial[n] * m * sinTerm * P[i];
    zGeocentric -= radial[n] * (n + 1) * cosTerm * P[i];
  }
  // At a pole cos(psi) is about 1e-16, not 0, in floating point, and the order-1
  // terms carry that factor in P, so the quotient is the finite limit.
  yGeocentric /= cosPsi;

  // Rotate north and down from geocentric to geodetic; east is unchanged.
  const dPsi = psi - lat;
  const north = xGeocentric * Math.cos(dPsi) - zGeocentric * Math.sin(dPsi);
  return Math.atan2(yGeocentric, north) / DEG;
}

/**
 * The decimal year of a date: its UTC year plus the fraction of that year that
 * has elapsed, so 2027-07-02T12:00:00Z is 2027.5.
 */
export function decimalYear(d: Date): number {
  const year = d.getUTCFullYear();
  const start = Date.UTC(year, 0, 1);
  const end = Date.UTC(year + 1, 0, 1);
  return year + (d.getTime() - start) / (end - start);
}
