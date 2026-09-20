/**
 * The background texture, in JavaScript, from CONTRACT.md's recipe.
 *
 * This is the half of the renderer that has to agree with Python exactly, so
 * it is kept apart from the half that needs a browser: nothing here imports
 * Three.js or touches the DOM. `renderer/render.js` imports it to paint the
 * background sphere's canvas, and two things that are not browsers import it
 * too:
 *
 * - `renderer/hash.test.mjs` checks the lattice integers and the palette.
 * - `renderer/dump_texture.mjs` writes a whole texture out for
 *   `tests/test_render_texture.py` to compare, texel for texel, with
 *   `sim.truth.background_texture`. That comparison is the reason this file
 *   has no dependencies at all: the Python suite can run it with nothing but
 *   `node` on PATH, so a divergence in the noise, the stripes or the discs
 *   fails a test rather than waiting to be noticed in a picture.
 *
 * The recipe is CONTRACT.md's Scene schema plus the four choices its "Texture
 * conventions" section pins, and the pinned choices are the ones worth
 * naming because each is a plausible thing to get wrong:
 *
 * 1. An octave's lattice has `cells * 2**o` columns by `columns / 2 + 1`
 *    rows, sampled at `u = az / 360 * columns` (wrapping) and
 *    `v = (90 - alt) / 180 * (rows - 1)` (clamped), bilinear.
 * 2. The grey is truncated, not rounded: `Math.floor`.
 * 3. A stripe's pole is
 *    `[cos(az_k) cos(tilt), -sin(az_k) cos(tilt), -sin(tilt)]`, so every
 *    stripe leans the same way, and a direction is on it when
 *    `|d . n| < sin(width / 2)`.
 * 4. Discs are painted from the last declared to the first, so the painter's
 *    "later covers earlier" agrees with the ray caster's "earlier wins".
 */

const DEG = Math.PI / 180;

/** The contract's equirectangular background size, matching truth.py's sample. */
export const TEXTURE_WIDTH = 4096;
export const TEXTURE_HEIGHT = 2048;

/** sim/palette.py's levels and its fixed order: red slowest, blue fastest, greys left out. */
export const LEVELS = [0, 128, 255];

export const PALETTE = (() => {
  const out = [];
  for (const r of LEVELS) {
    for (const g of LEVELS) {
      for (const b of LEVELS) {
        if (!(r === g && g === b)) out.push([r, g, b]);
      }
    }
  }
  return out;
})();

/**
 * CONTRACT.md's uint32 lattice hash, the one number Python and JavaScript
 * must agree on exactly. Every product is truncated to 32 bits with
 * `Math.imul` and every intermediate is forced unsigned with `>>> 0`, because
 * JavaScript's own bitwise operators work on signed 32-bit integers and the
 * final division by 2**32 would otherwise be negative half the time.
 * renderer/hash.test.mjs checks it against the integers tests/test_truth.py
 * pins.
 */
export function latticeHash(seed, octave, i, j) {
  let h = ((seed >>> 0)
    ^ Math.imul(octave, 0x9E3779B1)
    ^ Math.imul(i, 0x85EBCA77)
    ^ Math.imul(j, 0xC2B2AE3D)) >>> 0;
  h = (h ^ (h >>> 16)) >>> 0;
  h = Math.imul(h, 0x7FEB352D) >>> 0;
  h = (h ^ (h >>> 15)) >>> 0;
  h = Math.imul(h, 0x846CA68B) >>> 0;
  h = (h ^ (h >>> 16)) >>> 0;
  return h;
}

/** `[rows, columns]` of one octave's lattice: square cells, pole to pole. */
export function octaveLatticeShape(cells, octave) {
  const columns = cells * 2 ** octave;
  return [Math.floor(columns / 2) + 1, columns];
}

/** One octave's lattice as values in [0, 1), row-major, `rows * columns` long. */
export function latticeGrid(seed, octave, rows, columns) {
  const grid = new Float64Array(rows * columns);
  for (let j = 0; j < rows; j++) {
    const base = j * columns;
    for (let i = 0; i < columns; i++) {
      grid[base + i] = latticeHash(seed, octave, i, j) / 4294967296;
    }
  }
  return grid;
}

/** The world direction at azimuth `azDeg` clockwise from north, altitude `altDeg`. */
export function skyVector(azDeg, altDeg) {
  const az = azDeg * DEG;
  const alt = altDeg * DEG;
  const ca = Math.cos(alt);
  return [Math.sin(az) * ca, Math.cos(az) * ca, Math.sin(alt)];
}

/**
 * The background as RGBA bytes for a `width x height` equirectangular image:
 * column x is azimuth `(x + 0.5) / width * 360`, row y is altitude
 * `90 - (y + 0.5) / height * 180`, so row 0 is the zenith.
 *
 * Value noise into the declared grey range, then the stripe great circles,
 * then the landmark discs from the last declared to the first. The disc
 * predicate is the exact `d . centre > cos(radius)` truth.py uses rather than
 * a polygon approximation of it, so the two images agree texel for texel;
 * only the rows a disc can reach are visited.
 */
export function backgroundTextureRGBA(sceneJson, width, height) {
  const texture = sceneJson.background.texture;
  const cells = texture.cells | 0;
  const octaves = texture.octaves | 0;
  const seed = texture.seed | 0;
  const grey0 = texture.grey[0];
  const grey1 = texture.grey[1];
  const ceiling = 2 - 0.5 ** (octaves - 1);
  const stripes = texture.stripes;
  const stripeGrey = stripes.grey;

  const azDeg = new Float64Array(width);
  const sinAz = new Float64Array(width);
  const cosAz = new Float64Array(width);
  for (let x = 0; x < width; x++) {
    const az = ((x + 0.5) / width) * 360;
    azDeg[x] = az;
    sinAz[x] = Math.sin(az * DEG);
    cosAz[x] = Math.cos(az * DEG);
  }

  // Azimuth weights are the same on every row, so they are prepared once.
  const octave = [];
  for (let o = 0; o < octaves; o++) {
    const [rows, columns] = octaveLatticeShape(cells, o);
    const i0 = new Int32Array(width);
    const i1 = new Int32Array(width);
    const fu = new Float64Array(width);
    for (let x = 0; x < width; x++) {
      const u = (azDeg[x] / 360) * columns;
      const iu = Math.floor(u);
      fu[x] = u - iu;
      i0[x] = ((iu % columns) + columns) % columns;
      i1[x] = (i0[x] + 1) % columns;
    }
    octave.push({
      rows,
      columns,
      lattice: latticeGrid(seed, o, rows, columns),
      i0,
      i1,
      fu,
      weight: 0.5 ** o,
      rowA: 0,
      rowB: 0,
      wv0: 0,
      wv1: 0,
    });
  }

  // A stripe's pole; the azimuth half of the dot product is per column.
  const cosTilt = Math.cos(stripes.tilt_deg * DEG);
  const poleZ = -Math.sin(stripes.tilt_deg * DEG);
  const halfWidth = Math.sin((stripes.width_deg / 2) * DEG);
  const stripePoles = [];
  for (let k = 0; k < stripes.count; k++) {
    const a = ((k * 360) / stripes.count) * DEG;
    const nx = Math.cos(a) * cosTilt;
    const ny = -Math.sin(a) * cosTilt;
    const horizontal = new Float64Array(width);
    for (let x = 0; x < width; x++) horizontal[x] = sinAz[x] * nx + cosAz[x] * ny;
    stripePoles.push(horizontal);
  }

  const data = new Uint8ClampedArray(width * height * 4);
  for (let y = 0; y < height; y++) {
    const alt = 90 - ((y + 0.5) / height) * 180;
    const sinAlt = Math.sin(alt * DEG);
    const cosAlt = Math.cos(alt * DEG);

    for (let o = 0; o < octaves; o++) {
      const s = octave[o];
      const v = ((90 - alt) / 180) * (s.rows - 1);
      const j0 = Math.min(Math.max(Math.floor(v), 0), s.rows - 2);
      const fv = Math.min(Math.max(v - j0, 0), 1);
      s.rowA = j0 * s.columns;
      s.rowB = (j0 + 1) * s.columns;
      s.wv0 = 1 - fv;
      s.wv1 = fv;
    }

    const rowBase = y * width * 4;
    for (let x = 0; x < width; x++) {
      let total = 0;
      for (let o = 0; o < octaves; o++) {
        const s = octave[o];
        const a = s.i0[x];
        const b = s.i1[x];
        const wu1 = s.fu[x];
        const wu0 = 1 - wu1;
        const upper = s.lattice[s.rowA + a] * wu0 + s.lattice[s.rowA + b] * wu1;
        const lower = s.lattice[s.rowB + a] * wu0 + s.lattice[s.rowB + b] * wu1;
        total += (upper * s.wv0 + lower * s.wv1) * s.weight;
      }
      let value = Math.floor(grey0 + (total / ceiling) * (grey1 - grey0));
      for (let k = 0; k < stripePoles.length; k++) {
        if (Math.abs(cosAlt * stripePoles[k][x] + sinAlt * poleZ) < halfWidth) {
          value = stripeGrey;
          break;
        }
      }
      const p = rowBase + x * 4;
      data[p] = value;
      data[p + 1] = value;
      data[p + 2] = value;
      data[p + 3] = 255;
    }
  }

  const landmarks = sceneJson.landmarks || [];
  const rowMargin = 180 / height;
  for (let n = landmarks.length - 1; n >= 0; n--) {
    const landmark = landmarks[n];
    const radius = landmark.radius_deg;
    const cosRadius = Math.cos(radius * DEG);
    const centre = skyVector(landmark.az, landmark.alt);
    const colour = PALETTE[landmark.palette];
    for (let y = 0; y < height; y++) {
      const alt = 90 - ((y + 0.5) / height) * 180;
      if (Math.abs(alt - landmark.alt) > radius + rowMargin) continue;
      const sinAlt = Math.sin(alt * DEG);
      const cosAlt = Math.cos(alt * DEG);
      const rowBase = y * width * 4;
      for (let x = 0; x < width; x++) {
        const dot = cosAlt * (sinAz[x] * centre[0] + cosAz[x] * centre[1]) + sinAlt * centre[2];
        if (dot <= cosRadius) continue;
        const p = rowBase + x * 4;
        data[p] = colour[0];
        data[p + 1] = colour[1];
        data[p + 2] = colour[2];
      }
    }
  }
  return data;
}
