// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
/**
 * Object materials: the texel recipe of SPEC-v2 13.4, in JavaScript.
 *
 * A scene object may carry `"material"`, and the renderer paints it as a
 * 256 x 256 texture on the mesh's default geometry UVs. Three kinds exist:
 *
 * - `flat` is the object's own colour. There is no texture for it, and an
 *   object with no `material` at all is drawn exactly as it always was.
 * - `noise` multiplies the object's colour by
 *   `mod[0] + (mod[1] - mod[0]) * valueNoise(u, v)`, where `valueNoise` is the
 *   contract's lattice recipe (the one `renderer/texture.js` paints the sky
 *   with) divided by its ceiling, so it lies in [0, 1).
 * - `stripes` alternates two colours along u: `count` stripes, the first
 *   `duty` of each in the object's colour and the rest in `colour2`.
 *
 * Python truth ignores all of this, because a material changes no geometry:
 * `sim/truth.py` never reads it and `sim/scene.py` only checks that it is well
 * formed. That is also why this file is a second implementation of nothing in
 * `texture.js`: it imports the lattice hash and the lattice shape from there,
 * read-only, and builds only the part the sky does not need.
 *
 * Conventions the schema leaves open, made here and pinned so that the test
 * (`tests/test_scene_materials.py`) can rebuild the same texels independently:
 *
 * 1. Texel (x, y) is at u = (x + 0.5) / size and v = (y + 0.5) / size, with
 *    canvas row 0 at v = 0. THREE flips canvas textures, so row 0 ends up on
 *    the top of a box face, and at the north pole of a sphere.
 * 2. A noise octave's lattice is `texture.js`'s: `cells * 2**o` columns by
 *    `columns / 2 + 1` rows, sampled at `u * columns` (wrapping) and
 *    `v * (rows - 1)` (clamped), bilinear. The sum is divided by
 *    `2 - 0.5**(octaves - 1)`, which is the contract's 1.875 for the four
 *    octaves every scene uses and keeps the factor inside `mod` for any other
 *    count.
 * 3. A noise channel is `round(colour * factor)` clamped to 0..255. The sky's
 *    grey is truncated because its contract says so; this contract says only
 *    "multiplied and clamped".
 * 4. A stripe is on the object's colour where `fract(u * count) < duty`.
 *
 * The texel builders have no dependencies beyond `texture.js`, and
 * `materialTexture` is handed the THREE namespace rather than importing it, so
 * a test can run the recipe under plain `node` with no browser and no
 * `node_modules`.
 */

import { latticeGrid, octaveLatticeShape } from './texture.js';

/** The side of every material texture, in texels (SPEC-v2 13.4). */
export const MATERIAL_SIZE = 256;

/** True for the materials that need a texture: `flat` and no material do not. */
export function hasTexture(material) {
  return material !== undefined && material !== null
    && (material.kind === 'noise' || material.kind === 'stripes');
}

/**
 * The value noise at every texel of a `size x size` texture, as the octave sum
 * over its ceiling: row-major, row 0 first, each value in [0, 1).
 */
export function noiseField(material, size = MATERIAL_SIZE) {
  const cells = material.cells | 0;
  const octaves = material.octaves | 0;
  const seed = material.seed;
  const ceiling = 2 - 0.5 ** (octaves - 1);
  const total = new Float64Array(size * size);

  for (let o = 0; o < octaves; o++) {
    const [rows, columns] = octaveLatticeShape(cells, o);
    const lattice = latticeGrid(seed, o, rows, columns);
    const weight = 0.5 ** o;

    // The azimuth-like axis is the same on every row, so it is prepared once.
    const i0 = new Int32Array(size);
    const i1 = new Int32Array(size);
    const fu = new Float64Array(size);
    for (let x = 0; x < size; x++) {
      const u = ((x + 0.5) / size) * columns;
      const iu = Math.floor(u);
      fu[x] = u - iu;
      i0[x] = ((iu % columns) + columns) % columns;
      i1[x] = (i0[x] + 1) % columns;
    }

    for (let y = 0; y < size; y++) {
      const v = ((y + 0.5) / size) * (rows - 1);
      const j0 = Math.min(Math.max(Math.floor(v), 0), rows - 2);
      const fv = Math.min(Math.max(v - j0, 0), 1);
      const rowA = j0 * columns;
      const rowB = (j0 + 1) * columns;
      const base = y * size;
      for (let x = 0; x < size; x++) {
        const wu1 = fu[x];
        const wu0 = 1 - wu1;
        const upper = lattice[rowA + i0[x]] * wu0 + lattice[rowA + i1[x]] * wu1;
        const lower = lattice[rowB + i0[x]] * wu0 + lattice[rowB + i1[x]] * wu1;
        total[base + x] += (upper * (1 - fv) + lower * fv) * weight;
      }
    }
  }
  for (let n = 0; n < total.length; n++) total[n] /= ceiling;
  return total;
}

function clampByte(value) {
  return Math.min(255, Math.max(0, Math.round(value)));
}

/**
 * A material's texture as RGBA bytes, `size x size`, row 0 first. Only for a
 * material that has one (see `hasTexture`); `colour` is the object's colour.
 */
export function materialTextureRGBA(material, colour, size = MATERIAL_SIZE) {
  if (!hasTexture(material)) {
    throw new Error(`material ${material && material.kind} has no texture`);
  }
  const data = new Uint8ClampedArray(size * size * 4);

  if (material.kind === 'noise') {
    const field = noiseField(material, size);
    const lo = material.mod[0];
    const span = material.mod[1] - material.mod[0];
    for (let n = 0; n < size * size; n++) {
      const factor = lo + span * field[n];
      const p = n * 4;
      data[p] = clampByte(colour[0] * factor);
      data[p + 1] = clampByte(colour[1] * factor);
      data[p + 2] = clampByte(colour[2] * factor);
      data[p + 3] = 255;
    }
    return data;
  }

  // stripes: the colour depends on u only, so one row is worked out and copied.
  const count = material.count;
  const duty = material.duty;
  const other = material.colour2;
  const row = new Uint8ClampedArray(size * 4);
  for (let x = 0; x < size; x++) {
    const t = ((x + 0.5) / size) * count;
    const on = t - Math.floor(t) < duty;
    const source = on ? colour : other;
    row[x * 4] = source[0];
    row[x * 4 + 1] = source[1];
    row[x * 4 + 2] = source[2];
    row[x * 4 + 3] = 255;
  }
  for (let y = 0; y < size; y++) data.set(row, y * size * 4);
  return data;
}

/**
 * The THREE texture for a material, or `null` when the material needs none.
 *
 * `THREE` is the namespace, passed in so that this file imports nothing but
 * `texture.js`. The texels carry the final colour, so the mesh's own colour
 * must be white: `render.js` does that, and colour management is off, so an
 * 8-bit texel comes out as the same 8-bit value, as everywhere in the
 * renderer. The filters are trilinear, unlike the sky's: a fence's stripes are
 * several times finer than a frame pixel at a grazing angle, and a nearest or
 * plain linear lookup turns them into shimmer that a real lens would not
 * produce. The wrap is repeating in u so a sphere's seam is not a line.
 */
export function materialTexture(THREE, material, colour) {
  if (!hasTexture(material)) return null;
  const canvas = document.createElement('canvas');
  canvas.width = MATERIAL_SIZE;
  canvas.height = MATERIAL_SIZE;
  const context = canvas.getContext('2d', { willReadFrequently: false });
  const data = materialTextureRGBA(material, colour, MATERIAL_SIZE);
  context.putImageData(new ImageData(data, MATERIAL_SIZE, MATERIAL_SIZE), 0, 0);

  const texture = new THREE.CanvasTexture(canvas);
  texture.colorSpace = THREE.NoColorSpace;
  texture.generateMipmaps = true;
  texture.minFilter = THREE.LinearMipmapLinearFilter;
  texture.magFilter = THREE.LinearFilter;
  texture.wrapS = THREE.RepeatWrapping;
  texture.wrapT = THREE.ClampToEdgeWrapping;
  texture.flipY = true;
  texture.needsUpdate = true;
  return texture;
}
