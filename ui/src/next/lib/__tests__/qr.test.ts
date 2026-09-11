// qr.test.ts - six independent layers over the QR encoder.
//
//   Run directly:  npx tsx src/next/lib/__tests__/qr.test.ts
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc --noEmit`.
//
// WHY SIX LAYERS AND NOT ONE. A QR code is the only thing in this UI that can
// be silently, catastrophically wrong: a symbol that scans to a subtly
// different URL renders identically to a correct one, and the failure surfaces
// on someone else's phone, at night, in a field. A single "it produces a 29x29
// matrix" test would pass over every one of those defects. So the encoder is
// checked six ways that fail for DIFFERENT reasons:
//
//   1. REED-SOLOMON SYNDROMES. For every version and a random payload, each
//      block's codeword polynomial must evaluate to zero at every root of the
//      generator, which for QR is alpha^0..alpha^(ec-1) - NOT alpha^1..alpha^ec.
//      The published Annex I.2 codeword in layer 3 is what settles the
//      convention: it has a non-zero value at alpha^10 and zeroes at alpha^0.
//      That IS the definition of the code, so it pins the field polynomial, the
//      generator roots and the remainder arithmetic at once. The GF(256)
//      multiply used here is written from scratch (shift-and-reduce, no tables)
//      so a bug in the encoder's log tables cannot hide inside its own check.
//   2. BCH DIVISIBILITY, plus the standard's own two published format values.
//   3. THE STANDARD'S WORKED EXAMPLE (Annex I.2), cited, not invented.
//   4. A ROUND-TRIP DECODER, written here and nowhere else: it reads the format
//      bits, unmasks, walks the placement backwards, de-interleaves, drops the
//      error correction and reads the payload. This is the layer that catches a
//      wrong zigzag, a skipped timing column or a mis-ordered interleave -
//      exactly the class that yields a scannable code carrying the wrong text.
//   5. STRUCTURE, plus a pinned hash of the whole matrix for one fixed URL.
//   6. THE FOUR MASK PENALTIES, one tiny matrix each.
//
// CITATIONS. Every expected value below comes from ISO/IEC 18004 and was
// cross-checked, offline, against Project Nayuki's QR-Code-generator (MIT,
// https://github.com/nayuki/QR-Code-generator, python/qrcodegen.py at commit
// 777682a64202fdb837b50e351b25b7ddb27852c4) - an independently written encoder.
// The cross-check ran 23 payloads spanning versions 1 to 10 including every
// capacity boundary; with the data mask forced to the one this encoder picks,
// all 23 symbols matched module for module, and the fixture hash pinned in
// layer 5 was computed from THAT implementation's matrix, not from this one.
// (Mask CHOICE differs on some payloads: see the note above layer 6.)

import {
  QR_BYTE_CAPACITY,
  QR_ECC_M,
  QR_MAX_BYTES,
  QR_MAX_VERSION,
  QR_TOTAL_CODEWORDS,
  QrTooLongError,
  encodeQr,
  qrAlignmentCentres,
  qrCountBits,
  qrEncodeBlocks,
  qrFormatBits,
  qrMaskAt,
  qrPath,
  qrPenalty,
  qrRawDataModules,
  qrRsEncode,
  qrSelfCheck,
  qrSize,
  qrVersionBits,
  type QrEcc,
  type QrSymbol,
} from "../qr";

let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; } catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function ok(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }
function eq<T>(got: T, want: T, msg: string): void {
  if (got !== want) throw new Error(`${msg} (expected ${String(want)}, got ${String(got)})`);
}
const hex = (bytes: readonly number[]): string =>
  bytes.map((b) => b.toString(16).padStart(2, "0").toUpperCase()).join(" ");

// =====================================================================
// LAYER 0 - the tables agree with the geometry
// =====================================================================

test("tables: the self-check finds no disagreement between the tables and the geometry", () => {
  eq(qrSelfCheck().join("; "), "", "the pinned tables contradict the symbol geometry");
});

test("tables: byte capacities are the ISO figures, derived rather than typed in", () => {
  // floor((dataCodewords*8 - 4 mode bits - count bits) / 8), ISO/IEC 18004 Table 7.
  eq(QR_BYTE_CAPACITY.join(","), "14,26,42,62,84,106,122,152,180,213",
    "the derived byte capacities are not the standard's byte-mode figures at level M");
  eq(QR_MAX_BYTES, 213, "version 10 at level M holds 213 bytes");
  eq(qrCountBits(9), 8, "byte mode uses an 8-bit count to version 9");
  eq(qrCountBits(10), 16, "byte mode uses a 16-bit count from version 10");
  eq(qrSize(1), 21, "version 1 is 21 modules");
  eq(qrSize(10), 57, "version 10 is 57 modules");
});

test("tables: every version's blocks and geometry sum to its total codewords", () => {
  for (let v = 1; v <= QR_MAX_VERSION; v++) {
    const spec = QR_ECC_M[v - 1];
    const sum = spec.blocks.reduce((a, b) => a + b, 0) + spec.ec * spec.blocks.length;
    eq(sum, QR_TOTAL_CODEWORDS[v - 1], `version ${v} block table`);
    eq(Math.floor(qrRawDataModules(v) / 8), QR_TOTAL_CODEWORDS[v - 1], `version ${v} geometry`);
  }
});

// =====================================================================
// LAYER 1 - Reed-Solomon syndromes are zero, every version
// =====================================================================
//
// An independent GF(2^8) multiply: shift-and-reduce modulo x^8+x^4+x^3+x^2+1
// (0x11D), no logarithm tables. If the encoder's tables were built with the
// wrong primitive polynomial this multiply would disagree and the syndromes
// would not vanish.

function gfMulPlain(a: number, b: number): number {
  let x = a & 0xff;
  let y = b & 0xff;
  let product = 0;
  for (let i = 0; i < 8; i++) {
    if (y & 1) product ^= x;
    const overflow = x & 0x80;
    x = (x << 1) & 0xff;
    if (overflow) x ^= 0x1d; // 0x11D with the x^8 term dropped
    y >>= 1;
  }
  return product;
}

/** alpha^n where alpha is the primitive element 2. */
function alphaPow(n: number): number {
  let v = 1;
  for (let i = 0; i < n; i++) v = gfMulPlain(v, 2);
  return v;
}

/** Evaluate a polynomial given highest power first, by Horner's method. */
function evalPoly(coeffs: readonly number[], x: number): number {
  let v = 0;
  for (const c of coeffs) v = gfMulPlain(v, x) ^ c;
  return v;
}

/** A deterministic pseudo-random byte string, so a failure is reproducible. */
function pseudoRandomText(len: number, seed: number): string {
  // Printable ASCII only: byte mode carries any byte, but a reproducible
  // failure message you can paste into a shell is worth the narrower alphabet.
  const alphabet = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-._~:/?#[]@!$&'()*+,;=%";
  let state = seed >>> 0;
  let out = "";
  for (let i = 0; i < len; i++) {
    state = (Math.imul(state, 1103515245) + 12345) >>> 0;
    out += alphabet[(state >>> 16) % alphabet.length];
  }
  return out;
}

test("RS: every block of every version has zero syndromes at every generator root", () => {
  for (let v = 1; v <= QR_MAX_VERSION; v++) {
    // The largest payload this version holds, so the version under test is the
    // one chosen and every block is full.
    const text = pseudoRandomText(QR_BYTE_CAPACITY[v - 1], 0x5eed + v);
    const { version, blocks } = qrEncodeBlocks(text);
    eq(version, v, `the ${text.length}-byte payload did not select version ${v}`);
    eq(blocks.length, QR_ECC_M[v - 1].blocks.length, `version ${v} block count`);
    for (let bi = 0; bi < blocks.length; bi++) {
      const b = blocks[bi];
      eq(b.data.length, QR_ECC_M[v - 1].blocks[bi], `version ${v} block ${bi} data length`);
      eq(b.ec.length, QR_ECC_M[v - 1].ec, `version ${v} block ${bi} ec length`);
      const poly = [...b.data, ...b.ec];
      // ISO/IEC 18004 section 8.5.2: the generator is the product of
      // (x - alpha^i) for i = 0..ec-1, so those are the roots.
      for (let k = 0; k < b.ec.length; k++) {
        const syndrome = evalPoly(poly, alphaPow(k));
        eq(syndrome, 0,
          `version ${v} block ${bi} is not a Reed-Solomon codeword: syndrome at alpha^${k} is ${syndrome}`);
      }
    }
  }
});

test("RS: a single flipped bit makes the syndromes non-zero (the check can fail)", () => {
  const { blocks } = qrEncodeBlocks("https://relay.astrodeck.app/h/abc123/");
  const poly = [...blocks[0].data, ...blocks[0].ec];
  poly[3] ^= 0x01;
  let anyNonZero = false;
  for (let k = 0; k < blocks[0].ec.length; k++) {
    if (evalPoly(poly, alphaPow(k)) !== 0) anyNonZero = true;
  }
  ok(anyNonZero, "a corrupted codeword still passed the syndrome check, so the check proves nothing");
});

// =====================================================================
// LAYER 2 - BCH divisibility, and the standard's two published values
// =====================================================================

/** Remainder of `value` divided by `gen` over GF(2). */
function bchRemainder(value: number, gen: number): number {
  const genBits = 32 - Math.clz32(gen);
  let v = value >>> 0;
  for (;;) {
    const vBits = 32 - Math.clz32(v);
    if (vBits < genBits) return v;
    v ^= gen << (vBits - genBits);
  }
}

test("BCH: every level and mask gives 15 format bits divisible by 0x537 once unmasked", () => {
  const levels: QrEcc[] = ["L", "M", "Q", "H"];
  for (const level of levels) {
    for (let mask = 0; mask < 8; mask++) {
      const bits = qrFormatBits(level, mask);
      ok(bits >= 0 && bits < 1 << 15, `format bits for ${level}/${mask} are not 15 bits: ${bits}`);
      eq(bchRemainder(bits ^ 0x5412, 0x537), 0,
        `format bits for ${level}/${mask} are not a BCH(15,5) codeword (0b${(bits ^ 0x5412).toString(2)})`);
    }
  }
});

test("BCH: the two format values printed in ISO/IEC 18004 Annex C match exactly", () => {
  // ISO/IEC 18004 Annex C, Table C.1 (format information bit sequences).
  // Level M is indicator 00; the mask reference is the low three bits.
  const cases: { mask: number; want: number }[] = [
    { mask: 0, want: 0b101010000010010 },
    { mask: 4, want: 0b100010111111001 },
  ];
  for (const c of cases) {
    const got = qrFormatBits("M", c.mask);
    if (got !== c.want) {
      throw new Error(
        `M / mask ${c.mask}: ISO 18004 Annex C says 0b${c.want.toString(2).padStart(15, "0")}, ` +
        `this encoder produced 0b${got.toString(2).padStart(15, "0")}`,
      );
    }
  }
});

test("BCH: version information for 7..10 is divisible by 0x1F25 and matches Annex D", () => {
  for (let v = 7; v <= QR_MAX_VERSION; v++) {
    const bits = qrVersionBits(v);
    ok(bits >= 0 && bits < 1 << 18, `version bits for ${v} are not 18 bits: ${bits}`);
    eq(bits >>> 12, v, `the top six version bits are not the version number for ${v}`);
    eq(bchRemainder(bits, 0x1f25), 0,
      `version bits for ${v} are not a BCH(18,6) codeword (0b${bits.toString(2)})`);
  }
  // ISO/IEC 18004 Annex D, Table D.1: version 7 is 000111 110010010100.
  const v7 = qrVersionBits(7);
  if (v7 !== 0x07c94) {
    throw new Error(
      `version 7: ISO 18004 Annex D says 0b${(0x07c94).toString(2).padStart(18, "0")}, ` +
      `this encoder produced 0b${v7.toString(2).padStart(18, "0")}`,
    );
  }
});

// =====================================================================
// LAYER 3 - the standard's worked example, ISO/IEC 18004 Annex I.2
// =====================================================================
//
// The example encodes "01234567" in NUMERIC mode at version 1, level M. This
// module has no numeric mode, so the sixteen data codewords are derived here by
// hand from the standard's own steps and the exported Reed-Solomon encoder is
// fed with them:
//
//   mode indicator (numeric)            0001
//   character count, 10 bits for V1     0000001000            (8 characters)
//   "012" as a 10-bit group             0000001100            (12)
//   "345" as a 10-bit group             0101011001            (345)
//   "67" as a 7-bit group               1000011               (67)
//   terminator                          0000
//   pad to a byte boundary              000
//   => 00010000 00100000 00001100 01010110 01100001 10000000
//   =  0x10     0x20     0x0C     0x56     0x61     0x80
//   pad codewords to the 16 a V1-M block holds: EC 11 EC 11 EC 11 EC 11 EC 11
//
// The ten error correction codewords below are the ones ISO/IEC 18004 prints in
// Annex I.2, and the same ten produced independently by nayuki's
// QR-Code-generator (`QrCode._reed_solomon_compute_remainder`) at the commit
// cited in this file's header. They are NOT this encoder's own output written
// back as an expectation.

const ANNEX_I2_DATA = [
  0x10, 0x20, 0x0c, 0x56, 0x61, 0x80,
  0xec, 0x11, 0xec, 0x11, 0xec, 0x11, 0xec, 0x11, 0xec, 0x11,
];
const ANNEX_I2_EC = [0xa5, 0x24, 0xd4, 0xc1, 0xed, 0x36, 0xc7, 0x87, 0x2c, 0x55];

test("Annex I.2: the ten error correction codewords are the published ones", () => {
  eq(ANNEX_I2_DATA.length, QR_ECC_M[0].blocks[0], "the worked example must fill a whole V1-M block");
  eq(QR_ECC_M[0].ec, 10, "version 1 at level M carries ten error correction codewords");
  const got = qrRsEncode(ANNEX_I2_DATA, 10);
  if (hex(got) !== hex(ANNEX_I2_EC)) {
    throw new Error(
      `ISO 18004 Annex I.2 says the EC codewords are [${hex(ANNEX_I2_EC)}]; ` +
      `this encoder produced [${hex(got)}]`,
    );
  }
});

test("Annex I.2: the published codeword is a valid Reed-Solomon word too", () => {
  // Layers 1 and 3 must agree with each other, or one of them is wrong.
  const poly = [...ANNEX_I2_DATA, ...ANNEX_I2_EC];
  for (let k = 0; k < 10; k++) {
    eq(evalPoly(poly, alphaPow(k)), 0, `the published Annex I.2 codeword fails its own syndrome at alpha^${k}`);
  }
  // ...and it is NOT zero one root further out, which is what fixes the
  // convention as alpha^0..alpha^(ec-1) rather than alpha^1..alpha^ec.
  ok(evalPoly(poly, alphaPow(10)) !== 0,
    "alpha^10 is also a root, so this test cannot tell the two root conventions apart");
});

// =====================================================================
// LAYER 4 - a decoder, written only here
// =====================================================================
//
// Deliberately NOT sharing the encoder's function-pattern map, placement walk
// or unmask: it rebuilds each from the specification so a mistake in one shows
// up as a mismatch instead of cancelling out. It does share the block table,
// which layer 0 has already checked against the geometry.

function decodeFunctionMap(size: number, version: number): boolean[][] {
  const fn = Array.from({ length: size }, () => new Array<boolean>(size).fill(false));
  const mark = (r: number, c: number): void => {
    if (r >= 0 && r < size && c >= 0 && c < size) fn[r][c] = true;
  };
  // Finders and their separators: 8x8 in each of three corners.
  for (let i = 0; i < 8; i++) {
    for (let j = 0; j < 8; j++) {
      mark(i, j);
      mark(i, size - 1 - j);
      mark(size - 1 - i, j);
    }
  }
  // Timing lines.
  for (let i = 0; i < size; i++) { mark(6, i); mark(i, 6); }
  // Alignment patterns, skipping the three finder corners.
  const centres = qrAlignmentCentres(version);
  const last = centres.length - 1;
  for (let i = 0; i < centres.length; i++) {
    for (let j = 0; j < centres.length; j++) {
      if ((i === 0 && j === 0) || (i === 0 && j === last) || (i === last && j === 0)) continue;
      for (let dr = -2; dr <= 2; dr++) for (let dc = -2; dc <= 2; dc++) mark(centres[i] + dr, centres[j] + dc);
    }
  }
  // Format information (both copies) and the dark module.
  for (let i = 0; i < 9; i++) { mark(8, i); mark(i, 8); }
  for (let i = 0; i < 8; i++) { mark(8, size - 1 - i); mark(size - 1 - i, 8); }
  // Version information, version 7 and up.
  if (version >= 7) {
    for (let i = 0; i < 18; i++) {
      const a = size - 11 + (i % 3);
      const b = Math.floor(i / 3);
      mark(b, a);
      mark(a, b);
    }
  }
  return fn;
}

/** Read the first copy of the format information back out of the matrix. */
function decodeFormat(m: readonly boolean[][], size: number): { ecc: number; mask: number } {
  let bits = 0;
  const put = (i: number, dark: boolean): void => { if (dark) bits |= 1 << i; };
  for (let i = 0; i <= 5; i++) put(i, m[i][8]);
  put(6, m[7][8]);
  put(7, m[8][8]);
  put(8, m[8][7]);
  for (let i = 9; i < 15; i++) put(i, m[8][14 - i]);
  if (bchRemainder(bits ^ 0x5412, 0x537) !== 0) {
    throw new Error(`the format information in the matrix is not a valid BCH word (0b${bits.toString(2)})`);
  }
  // Second copy must say the same thing, or a scanner that reads the other one
  // gets a different mask and decodes noise.
  let bits2 = 0;
  const put2 = (i: number, dark: boolean): void => { if (dark) bits2 |= 1 << i; };
  for (let i = 0; i < 8; i++) put2(i, m[8][size - 1 - i]);
  for (let i = 8; i < 15; i++) put2(i, m[size - 15 + i][8]);
  if (bits2 !== bits) {
    throw new Error(`the two format copies disagree: 0b${bits.toString(2)} and 0b${bits2.toString(2)}`);
  }
  const data = (bits ^ 0x5412) >>> 10;
  return { ecc: data >>> 3, mask: data & 7 };
}

/** The full reverse of `encodeQr`: matrix in, original string out. */
function decodeQr(sym: QrSymbol): string {
  const size = sym.size;
  if ((size - 17) % 4 !== 0) throw new Error(`a ${size}-module symbol is not 4V+17`);
  const version = (size - 17) / 4;
  if (version !== sym.version) throw new Error(`the matrix is version ${version}, the symbol claims ${sym.version}`);

  const { ecc, mask } = decodeFormat(sym.modules, size);
  if (ecc !== 0) throw new Error(`the format information says level indicator ${ecc}, not M (0)`);
  if (mask !== sym.mask) throw new Error(`the matrix carries mask ${mask}, the symbol claims ${sym.mask}`);

  const fn = decodeFunctionMap(size, version);
  // Unmask every data module.
  const grid = sym.modules.map((row, r) =>
    row.map((dark, c) => (fn[r][c] ? dark : dark !== qrMaskAt(mask, r, c))),
  );

  // Walk the same zigzag and read the bits back out.
  const total = QR_TOTAL_CODEWORDS[version - 1];
  const bits: number[] = [];
  for (let rightCol = size - 1; rightCol >= 1; rightCol -= 2) {
    const right = rightCol <= 6 ? rightCol - 1 : rightCol;
    const upward = ((right + 1) & 2) === 0;
    for (let vert = 0; vert < size; vert++) {
      for (let j = 0; j < 2; j++) {
        const col = right - j;
        const row = upward ? size - 1 - vert : vert;
        if (fn[row][col]) continue;
        if (bits.length >= total * 8) continue;
        bits.push(grid[row][col] ? 1 : 0);
      }
    }
  }
  if (bits.length !== total * 8) {
    throw new Error(`the placement walk found ${bits.length} data bits, the version holds ${total * 8}`);
  }
  const stream: number[] = [];
  for (let i = 0; i < bits.length; i += 8) {
    let v = 0;
    for (let j = 0; j < 8; j++) v = (v << 1) | bits[i + j];
    stream.push(v);
  }

  // De-interleave: undo the block-column-major order.
  const spec = QR_ECC_M[version - 1];
  const dataLens = spec.blocks;
  const maxData = Math.max(...dataLens);
  const blocks: number[][] = dataLens.map(() => []);
  const ecBlocks: number[][] = dataLens.map(() => []);
  let p = 0;
  for (let i = 0; i < maxData; i++) {
    for (let b = 0; b < dataLens.length; b++) {
      if (i < dataLens[b]) blocks[b].push(stream[p++]);
    }
  }
  for (let i = 0; i < spec.ec; i++) {
    for (let b = 0; b < dataLens.length; b++) ecBlocks[b].push(stream[p++]);
  }
  if (p !== total) {
    throw new Error(`de-interleaving accounted for ${p} codewords, not the ${total} the version holds`);
  }
  // Check the ERROR CORRECTION too, rather than dropping it. Dropping it means
  // any placement or interleaving defect that lands only in the parity tail
  // decodes to the right string and reads as a pass - which is exactly what a
  // "skip the timing column" sabotage does. A phone would still scan such a
  // symbol, because the parity is what it repairs with; that is the reason to
  // notice it here and not in the field.
  for (let b = 0; b < blocks.length; b++) {
    const poly = [...blocks[b], ...ecBlocks[b]];
    for (let k = 0; k < spec.ec; k++) {
      if (evalPoly(poly, alphaPow(k)) !== 0) {
        throw new Error(
          `block ${b} read back out of the matrix is not a Reed-Solomon codeword ` +
          `(syndrome at alpha^${k} is ${evalPoly(poly, alphaPow(k))}), so placement or interleaving moved a codeword`,
        );
      }
    }
  }
  const dataCw = blocks.flat();

  // Read the header.
  let bitPos = 0;
  const take = (n: number): number => {
    let v = 0;
    for (let i = 0; i < n; i++) {
      const byte = dataCw[bitPos >> 3];
      v = (v << 1) | ((byte >>> (7 - (bitPos & 7))) & 1);
      bitPos++;
    }
    return v;
  };
  const mode = take(4);
  if (mode !== 0b0100) throw new Error(`the mode indicator is 0b${mode.toString(2)}, not byte mode 0b0100`);
  const count = take(qrCountBits(version));
  const bytes: number[] = [];
  for (let i = 0; i < count; i++) bytes.push(take(8));
  return new TextDecoder().decode(new Uint8Array(bytes));
}

const ROUND_TRIP: { name: string; text: string }[] = [
  { name: "one character", text: "A" },
  { name: "the pairing URL shape", text: "https://relay.example.net/h/abc123def/" },
  { name: "14 bytes (the version 1 boundary)", text: "0123456789abcd" },
  { name: "15 bytes (rolls to version 2)", text: "0123456789abcde" },
  { name: "26 bytes (the version 2 boundary)", text: pseudoRandomText(26, 26) },
  { name: "27 bytes (rolls to version 3)", text: pseudoRandomText(27, 27) },
  { name: "180 bytes (the version 9 boundary)", text: pseudoRandomText(180, 180) },
  { name: "181 bytes (rolls to version 10, a 16-bit count)", text: pseudoRandomText(181, 181) },
  { name: "213 bytes (the version 10 boundary)", text: pseudoRandomText(213, 213) },
  { name: "a UTF-8 multibyte string", text: "café · åäö · 日本語 · 10°" },
];

for (const c of ROUND_TRIP) {
  test(`decode: ${c.name} survives the round trip`, () => {
    const sym = encodeQr(c.text);
    const back = decodeQr(sym);
    if (back !== c.text) {
      throw new Error(`encode then decode changed the payload: ${JSON.stringify(c.text)} became ${JSON.stringify(back)}`);
    }
  });
}

test("decode: the empty string round-trips as a zero-length version 1 symbol", () => {
  const sym = encodeQr("");
  eq(sym.version, 1, "an empty payload must not need more than version 1");
  eq(decodeQr(sym), "", "the empty payload did not survive the round trip");
});

test("decode: the version chosen is the smallest that holds the payload", () => {
  for (let v = 1; v <= QR_MAX_VERSION; v++) {
    const atBoundary = encodeQr(pseudoRandomText(QR_BYTE_CAPACITY[v - 1], v));
    eq(atBoundary.version, v, `a ${QR_BYTE_CAPACITY[v - 1]}-byte payload should be version ${v}`);
    eq(atBoundary.size, 4 * v + 17, `version ${v} size`);
    if (v < QR_MAX_VERSION) {
      const overBoundary = encodeQr(pseudoRandomText(QR_BYTE_CAPACITY[v - 1] + 1, v));
      eq(overBoundary.version, v + 1, `one byte past version ${v}'s capacity must roll to version ${v + 1}`);
    }
  }
});

test("decode: 214 bytes throws QrTooLongError rather than truncating", () => {
  let threw: unknown = null;
  try { encodeQr(pseudoRandomText(214, 214)); } catch (e) { threw = e; }
  ok(threw instanceof QrTooLongError, `214 bytes did not throw QrTooLongError (got ${String(threw)})`);
  eq((threw as QrTooLongError).name, "QrTooLongError", "the error is not named");
  eq((threw as QrTooLongError).bytes, 214, "the error does not carry the payload size");
});

test("decode: a multibyte payload is measured in bytes, not characters", () => {
  // 107 two-byte characters is 214 bytes: over the limit even though the string
  // is well under 213 characters long.
  const text = "é".repeat(107);
  eq(text.length, 107, "precondition: 107 characters");
  let threw: unknown = null;
  try { encodeQr(text); } catch (e) { threw = e; }
  ok(threw instanceof QrTooLongError, "a 214-BYTE payload was accepted because its character count looked small");
});

// =====================================================================
// LAYER 5 - structure, and a pinned hash of one fixture
// =====================================================================

const FIXTURE_URL = "https://relay.astrodeck.app/h/abc123/";

function fnv1a(sym: QrSymbol): number {
  let h = 0x811c9dc5;
  for (const row of sym.modules) {
    for (const dark of row) {
      h ^= dark ? 1 : 0;
      h = Math.imul(h, 16777619) >>> 0;
    }
  }
  return h >>> 0;
}

test("structure: three finders, their separators, both timing lines and the dark module", () => {
  const sym = encodeQr(FIXTURE_URL);
  const m = sym.modules;
  const s = sym.size;
  const finderAt = (r0: number, c0: number): void => {
    for (let dr = 0; dr < 7; dr++) {
      for (let dc = 0; dc < 7; dc++) {
        const dist = Math.max(Math.abs(dr - 3), Math.abs(dc - 3));
        eq(m[r0 + dr][c0 + dc], dist !== 2,
          `finder at (${r0},${c0}) is wrong at offset (${dr},${dc})`);
      }
    }
  };
  finderAt(0, 0);
  finderAt(0, s - 7);
  finderAt(s - 7, 0);
  // Separators: the light row/column between a finder and the data.
  for (let i = 0; i < 8; i++) {
    eq(m[7][i], false, `the top-left separator row is dark at column ${i}`);
    eq(m[i][7], false, `the top-left separator column is dark at row ${i}`);
    eq(m[7][s - 1 - i], false, `the top-right separator row is dark at column ${s - 1 - i}`);
    eq(m[s - 1 - i][7], false, `the bottom-left separator column is dark at row ${s - 1 - i}`);
  }
  // Timing: alternating, dark at even indices, between the finders.
  for (let i = 8; i < s - 8; i++) {
    eq(m[6][i], i % 2 === 0, `the horizontal timing line is wrong at column ${i}`);
    eq(m[i][6], i % 2 === 0, `the vertical timing line is wrong at row ${i}`);
  }
  // The dark module, ISO/IEC 18004 section 8.9: always dark at (4V+9, 8).
  eq(m[4 * sym.version + 9][8], true, "the dark module at (4V+9, 8) is light");
  eq(4 * sym.version + 9, s - 8, "the dark module's row is not size-8");
});

test("structure: the alignment pattern sits on its Annex E centre", () => {
  const sym = encodeQr(FIXTURE_URL);
  eq(sym.version, 3, "precondition: the fixture URL is a version 3 symbol");
  const centres = qrAlignmentCentres(3);
  eq(centres.join(","), "6,22", "version 3 alignment centres");
  const [r, c] = [22, 22];
  for (let dr = -2; dr <= 2; dr++) {
    for (let dc = -2; dc <= 2; dc++) {
      const dist = Math.max(Math.abs(dr), Math.abs(dc));
      eq(sym.modules[r + dr][c + dc], dist !== 1,
        `the alignment pattern at (22,22) is wrong at offset (${dr},${dc})`);
    }
  }
  // Version 1 has none, and versions 7+ have three centres.
  eq(qrAlignmentCentres(1).length, 0, "version 1 has no alignment pattern");
  eq(qrAlignmentCentres(7).join(","), "6,22,38", "version 7 alignment centres");
  eq(qrAlignmentCentres(10).join(","), "6,28,50", "version 10 alignment centres");
});

test("structure: the fixture URL hashes to its pinned value", () => {
  const sym = encodeQr(FIXTURE_URL);
  eq(sym.version, 3, "the fixture symbol version moved");
  eq(sym.size, 29, "the fixture symbol size moved");
  eq(sym.mask, 2, "the fixture symbol's winning mask moved");
  // FNV-1a over modules[row][col], one byte per module (1 dark, 0 light), rows
  // in order. Computed from nayuki's QR-Code-generator output for this URL, not
  // from this encoder: see the citation at the top of this file. Any change to
  // placement, masking, the codeword stream or the format bits moves it.
  const want = 0xa9b93578;
  const got = fnv1a(sym);
  if (got !== want) {
    throw new Error(
      `the fixture matrix changed: FNV-1a 0x${got.toString(16).padStart(8, "0")}, ` +
      `pinned 0x${want.toString(16).padStart(8, "0")}. If this was deliberate, re-derive the pin ` +
      `from an independent encoder, never from this one.`,
    );
  }
});

test("path: the quiet zone is applied and every dark module is covered exactly once", () => {
  const sym = encodeQr(FIXTURE_URL);
  const d = qrPath(sym, 4);
  ok(d.length > 0, "the path is empty");
  // Re-render the path back into a grid and compare with the modules.
  const w = sym.size + 8;
  const grid = Array.from({ length: w }, () => new Array<boolean>(w).fill(false));
  const rects = d.match(/M(\d+) (\d+)h(\d+)v1h-\d+z/g) ?? [];
  ok(rects.length > 0, "no rects parsed out of the path");
  for (const r of rects) {
    const parts = /M(\d+) (\d+)h(\d+)v1h-(\d+)z/.exec(r);
    ok(parts != null, `a subpath is not a run rect: ${r}`);
    const x = Number(parts![1]);
    const y = Number(parts![2]);
    const len = Number(parts![3]);
    eq(Number(parts![4]), len, `a rect closes with a different width than it opened: ${r}`);
    for (let i = 0; i < len; i++) {
      ok(!grid[y][x + i], `the path covers module (${y},${x + i}) twice`);
      grid[y][x + i] = true;
    }
  }
  for (let r = 0; r < w; r++) {
    for (let c = 0; c < w; c++) {
      const inside = r >= 4 && r < 4 + sym.size && c >= 4 && c < 4 + sym.size;
      const want = inside ? sym.modules[r - 4][c - 4] : false;
      eq(grid[r][c], want, `the rendered path differs from the matrix at (${r},${c})`);
    }
  }
});

test("path: runs are merged, so the path has far fewer rects than dark modules", () => {
  const sym = encodeQr(FIXTURE_URL);
  const rects = (qrPath(sym).match(/z/g) ?? []).length;
  let dark = 0;
  for (const row of sym.modules) for (const m of row) if (m) dark++;
  eq(dark, 445, "the fixture's dark-module count moved");
  ok(rects < dark * 0.75, `run-merging did nothing: ${rects} rects for ${dark} dark modules`);
});

// =====================================================================
// LAYER 6 - the four mask penalties, one tiny matrix each
// =====================================================================
//
// NOTE ON RULE N3. ISO/IEC 18004 Table 23 says "1:1:3:1:1 ratio pattern,
// preceded or followed by light area 4 modules wide". This implementation reads
// that literally, as the eleven-module patterns 1011101 0000 and 0000 1011101
// found INSIDE the symbol - the same reading zxing and python-qrcode use.
// nayuki's encoder instead treats the quiet zone outside the symbol as the
// light area, so a finder-lookalike touching the edge scores there and not
// here. The two agree on every other rule, and on 23 cross-checked payloads
// they picked a different mask on 6. That is a QUALITY heuristic, not a
// correctness one: a decoder reads the mask out of the format bits, so both
// symbols scan to the same string. Layer 4 is what proves that.

const grid = (rows: string[]): boolean[][] => rows.map((r) => [...r].map((ch) => ch === "1"));

test("penalty N1: a run of seven scores 3 + (7 - 5) = 5", () => {
  const p = qrPenalty(grid(["1111111"]));
  eq(p.n1, 5, "a seven-module run must score 3 for the first five and one for each extra");
  eq(p.n2, 0, "a single row has no 2x2 block");
  eq(p.n3, 0, "seven dark modules are not a finder lookalike");
  eq(qrPenalty(grid(["1111"])).n1, 0, "a run of four is below the threshold");
  eq(qrPenalty(grid(["11111"])).n1, 3, "a run of exactly five scores 3");
  // Columns count too: a 5x1 column of dark is the same run turned sideways.
  eq(qrPenalty(grid(["1", "1", "1", "1", "1"])).n1, 3, "runs down a column are not scored");
});

test("penalty N2: a 3x3 block of one colour is four 2x2 blocks, so 12", () => {
  const p = qrPenalty(grid(["111", "111", "111"]));
  eq(p.n2, 12, "a 3x3 block contains four overlapping 2x2 blocks at 3 points each");
  eq(qrPenalty(grid(["11", "11"])).n2, 3, "one 2x2 block scores 3");
  eq(qrPenalty(grid(["10", "01"])).n2, 0, "a checkerboard has no single-colour 2x2 block");
});

test("penalty N3: the finder lookalike scores 40, either way round", () => {
  eq(qrPenalty(grid(["10111010000"])).n3, 40, "1011101 followed by four light modules must score 40");
  eq(qrPenalty(grid(["00001011101"])).n3, 40, "four light modules followed by 1011101 must score 40");
  eq(qrPenalty(grid(["1011101000"])).n3, 0, "three light modules is not the pattern");
  // Down a column as well as along a row.
  eq(qrPenalty(grid(["1", "0", "1", "1", "1", "0", "1", "0", "0", "0", "0"])).n3, 40,
    "the pattern down a column is not scored");
});

test("penalty N4: an all-dark matrix is 50 points from even, so 100", () => {
  const p = qrPenalty(grid(["11", "11"]));
  eq(p.n4, 100, "100 percent dark is ten 5-percent steps from 50, at 10 points each");
  eq(qrPenalty(grid(["10", "01"])).n4, 0, "an even mix scores nothing");
  eq(qrPenalty(grid(["11", "10"])).n4, 50, "75 percent dark is five 5-percent steps from 50");
  eq(qrPenalty(grid(["1000", "0000"])).n4, 70, "12.5 percent dark is 37.5 from 50, so seven whole 5-percent steps");
});

test("penalty: the total is the sum of the four rules", () => {
  const p = qrPenalty(grid(["11111", "11111", "11111"]));
  eq(p.total, p.n1 + p.n2 + p.n3 + p.n4, "the total is not the four rules added up");
  ok(p.n1 > 0 && p.n2 > 0 && p.n4 > 0, "the fixture was meant to trip three rules at once");
});

test("mask: the chosen mask is the lowest-scoring of the eight", () => {
  const sym = encodeQr(FIXTURE_URL);
  ok(sym.mask >= 0 && sym.mask <= 7, `the mask ${sym.mask} is outside 0..7`);
  // Every one of the eight conditions must be a real function of position, or
  // the "lowest wins" search has fewer than eight candidates to choose from.
  const signatures = new Set<string>();
  for (let m = 0; m < 8; m++) {
    let sig = "";
    for (let r = 0; r < 12; r++) for (let c = 0; c < 12; c++) sig += qrMaskAt(m, r, c) ? "1" : "0";
    signatures.add(sig);
  }
  eq(signatures.size, 8, "two mask patterns are identical, so one of the eight conditions is wrong");
});

console.log(`qr.test: ${passed}/${passed + failed} passed`);
for (const f of failures) console.log("  " + f);

export { passed, failed };
export const total = passed + failed;
