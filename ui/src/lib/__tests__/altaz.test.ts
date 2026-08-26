// altaz.test.ts — the port is checked against the PYTHON, not against itself.
//
// Every expected value below came out of the server's own
// astrodeck/catalog/coords.py at a fixed instant:
//
//     from astrodeck.catalog.coords import altaz
//     altaz(ra_hours, dec_deg, 40.0, -105.0, 1787800000.0)
//
// That matters more than the arithmetic being pretty. Two implementations of
// spherical astronomy in one product is how a target gets drawn in the wrong
// half of the sky with a green suite, and a test that only checks this file
// against its own reasoning would agree with any consistent mistake.
import { altAzOf, lstHours } from "../altaz";

let passed = 0;
let failed = 0;
const failures: string[] = [];

function test(name: string, fn: () => void): void {
  try {
    fn();
    passed += 1;
  } catch (e) {
    failed += 1;
    failures.push(`✗ ${name}: ${(e as Error).message}`);
  }
}

function close(got: number, want: number, tol: number, what: string): void {
  if (!(Math.abs(got - want) <= tol)) {
    throw new Error(`${what} expected ${want} +/- ${tol}, got ${got}`);
  }
}

/** INVENTED, not the rig's. A test file is a public artefact and a latitude
 *  and longitude in one is a home address: this project has already had a
 *  viewer geolocate the rig to 2.9 km from a value nobody thought of as a
 *  coordinate. The privacy hook refused the first version of this file for
 *  exactly that, and it was right to. 40 N 105 W is a plausible northern-
 *  hemisphere site and nobody's. */
const LAT = 40.0;
const LON = -105.0;
/** One fixed unix instant, so the expectations are constants and not clocks. */
const T = 1787800000.0;

//: [name, raHours, decDeg, altDeg, azDeg] straight out of coords.py.
const ORACLE: [string, number, number, number, number][] = [
  ["NGC 7129", 21.716397, 66.112972, 52.4136, 29.9203],
  ["NGC 6946", 20.581, 60.15, 61.8841, 33.6838],
  ["M31", 0.712, 41.27, 22.7945, 54.4529],
  ["M42", 5.5881, -5.3911, -53.4025, 22.4470],
  ["Polaris", 2.5303, 89.264, 39.6190, 0.8197],
];

test("altAzOf matches the server's coords.altaz on five real targets", () => {
  for (const [name, ra, dec, wantAlt, wantAz] of ORACLE) {
    const got = altAzOf(ra, dec, LAT, LON, T);
    // A thousandth of a degree: the port is the same formula, so anything
    // looser would hide a genuine divergence rather than absorb float noise.
    close(got.altDeg, wantAlt, 1e-3, `${name} altitude`);
    close(got.azDeg, wantAz, 1e-3, `${name} azimuth`);
  }
});

test("a target below the horizon reports a NEGATIVE altitude, not zero", () => {
  // M42 is 57 degrees under the ground at this instant. Clamping would draw it
  // sitting ON the horizon, which is a different and wrong claim.
  const got = altAzOf(5.5881, -5.3911, LAT, LON, T);
  if (!(got.altDeg < -50)) {
    throw new Error(`expected well below the horizon, got ${got.altDeg}`);
  }
});

test("Polaris sits near due north at this site, whatever the hour", () => {
  // A latitude check that does not depend on the oracle instant: from 40 N,
  // Polaris is always within a degree and a half of north and within a degree
  // and a half of the latitude in altitude. If a sign flips in the azimuth
  // branch this is the test that notices.
  for (const t of [T, T + 6 * 3600, T + 12 * 3600, T + 18 * 3600]) {
    const got = altAzOf(2.5303, 89.264, LAT, LON, t);
    close(got.altDeg, LAT, 1.5, "Polaris altitude tracks the latitude");
    const offNorth = Math.min(got.azDeg, 360 - got.azDeg);
    if (!(offNorth < 1.5)) {
      throw new Error(`Polaris should be near due north, got az ${got.azDeg}`);
    }
  }
});

test("azimuth stays inside [0, 360) either side of the meridian", () => {
  // sin(ha) > 0 flips the branch; a target swept through a whole day must
  // never leave the interval every consumer is promised.
  for (let i = 0; i < 48; i += 1) {
    const got = altAzOf(20.581, 60.15, LAT, LON, T + i * 1800);
    if (!(got.azDeg >= 0 && got.azDeg < 360)) {
      throw new Error(`az out of range at step ${i}: ${got.azDeg}`);
    }
  }
});

test("lstHours is positive across the epoch, where JS %% is not", () => {
  // The one real porting hazard: JavaScript's % keeps the sign of the
  // dividend and Python's does not, so a naive port returns a NEGATIVE
  // sidereal time for some longitudes and instants -- which then lands the
  // target a full sky away.
  for (const t of [0, 1e9, T, T + 12 * 3600]) {
    for (const lon of [-105.0, 0, 105.0, 179.9, -179.9]) {
      const lst = lstHours(lon, t);
      if (!(lst >= 0 && lst < 24)) {
        throw new Error(`lst out of range: ${lst} at lon ${lon}, t ${t}`);
      }
    }
  }
});

const total = passed + failed;
// eslint-disable-next-line no-console
console.log(`\naltaz.test: ${passed}/${total} passed`);
if (failures.length) {
  // eslint-disable-next-line no-console
  console.error(failures.join("\n"));
  process.exitCode = 1;
}
