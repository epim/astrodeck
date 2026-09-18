/**
 * The half of render.js that has to agree with Python bit for bit, checked
 * against the integers tests/test_truth.py pins.
 *
 *     node renderer/hash.test.mjs      # from tools/photosphere_sim
 *
 * CONTRACT.md says a port must reproduce these integers rather than merely a
 * similar picture, because dropping the final shift of the hash changes the
 * background by less than one grey level. This file exists so a future port
 * of the renderer can rerun that comparison without a browser: it imports
 * render.js directly under Node, which is possible because render.js installs
 * window.simRender only when a window exists.
 */
import { PALETTE, latticeHash, octaveLatticeShape } from './render.js';

let failures = 0;

function check(label, got, expected) {
  const ok = Object.is(got, expected) || String(got) === String(expected);
  if (!ok) failures += 1;
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${label} = ${got}${ok ? '' : `  expected ${expected}`}`);
}

// tests/test_truth.py, the lattice_hash(7, ...) pins.
check('latticeHash(7, 0, 0, 0)', latticeHash(7, 0, 0, 0), 2492178918);
check('latticeHash(7, 0, 1, 0)', latticeHash(7, 0, 1, 0), 3390191309);
check('latticeHash(7, 0, 0, 1)', latticeHash(7, 0, 0, 1), 420868019);
check('latticeHash(7, 1, 5, 3)', latticeHash(7, 1, 5, 3), 1763849442);
check('latticeHash(7, 2, 63, 32)', latticeHash(7, 2, 63, 32), 4028108650);
check('latticeHash(7, 3, 127, 64)', latticeHash(7, 3, 127, 64), 3154280637);

// The hash is a uint32; the lattice value is it over 2**32, so in [0, 1).
check('latticeHash(7, 0, 0, 0) / 2**32', latticeHash(7, 0, 0, 0) / 4294967296,
      2492178918 / 4294967296);

// CONTRACT.md's "Texture conventions", choice 1: cells * 2**o columns by
// columns / 2 + 1 rows, so the lattice cells are square.
check('octaveLatticeShape(16, 0)', String(octaveLatticeShape(16, 0)), '9,16');
check('octaveLatticeShape(16, 1)', String(octaveLatticeShape(16, 1)), '17,32');
check('octaveLatticeShape(16, 2)', String(octaveLatticeShape(16, 2)), '33,64');
check('octaveLatticeShape(16, 3)', String(octaveLatticeShape(16, 3)), '65,128');

// sim/palette.py's fixed order: red slowest, blue fastest, the three greys
// left out. Scenes address colours by index, so the order is the contract.
check('PALETTE.length', PALETTE.length, 24);
check('PALETTE[0]', String(PALETTE[0]), '0,0,128');
check('PALETTE[8]', String(PALETTE[8]), '128,0,0');
check('PALETTE[23]', String(PALETTE[23]), '255,255,128');
check('PALETTE greys', PALETTE.filter((c) => c[0] === c[1] && c[1] === c[2]).length, 0);

console.log(failures === 0 ? 'all checks passed' : `${failures} check(s) failed`);
process.exit(failures === 0 ? 0 : 1);
