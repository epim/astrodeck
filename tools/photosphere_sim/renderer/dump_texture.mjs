/**
 * Write the JavaScript background texture out as raw bytes, for Python to
 * compare with sim.truth.background_texture.
 *
 *     node renderer/dump_texture.mjs <scene.json> <width> <height> <out.raw>
 *
 * The output is `width * height * 3` bytes, RGB, row 0 the zenith, the same
 * layout and order as the numpy array background_texture returns, so the
 * comparison is a memcmp rather than an interpretation.
 *
 * It imports renderer/texture.js and nothing else: no Three.js, no DOM, no
 * node_modules. tests/test_render_texture.py therefore needs only `node` on
 * PATH, and a failure of that test means the two implementations of the
 * recipe have actually diverged rather than that a dependency is missing.
 */

import fs from 'node:fs';
import { backgroundTextureRGBA } from './texture.js';

const [scenePath, widthArg, heightArg, outPath] = process.argv.slice(2);
if (!scenePath || !widthArg || !heightArg || !outPath) {
  console.error('usage: node renderer/dump_texture.mjs <scene.json> <width> <height> <out.raw>');
  process.exit(2);
}

const width = Number.parseInt(widthArg, 10);
const height = Number.parseInt(heightArg, 10);
const scene = JSON.parse(fs.readFileSync(scenePath, 'utf8'));
const rgba = backgroundTextureRGBA(scene, width, height);

const rgb = Buffer.allocUnsafe(width * height * 3);
for (let i = 0, j = 0; i < width * height; i++, j += 3) {
  rgb[j] = rgba[i * 4];
  rgb[j + 1] = rgba[i * 4 + 1];
  rgb[j + 2] = rgba[i * 4 + 2];
}
fs.writeFileSync(outPath, rgb);
