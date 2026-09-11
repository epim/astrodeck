// qr.ts - a QR Code encoder, byte mode, error correction level M, versions
// 1 to 10. Pure and React-free, so `next/lib/index.ts` may re-export it.
//
// WHY THIS IS HAND-WRITTEN. The one thing on the Connection sheet a user cannot
// check is a QR code: a symbol that scans to a subtly different address looks
// exactly like one that scans to the right one, and the person holding the
// second phone finds out by not reaching the rig. That risk is the reason this
// is a page of arithmetic in the repo rather than a dependency: every constant
// below is pinned to ISO/IEC 18004 and re-derived from the geometry at import
// time (`selfCheck`), and `__tests__/qr.test.ts` checks the output four
// independent ways - Reed-Solomon syndromes, BCH divisibility against the
// standard's own Annex C values, the standard's Annex I.2 worked example, and a
// decoder that reads the finished matrix back to the string it came from.
//
// SCOPE, DELIBERATELY SMALL. Byte mode only (no numeric/alphanumeric
// compaction), ECC M only, versions 1-10 only. The only caller encodes a relay
// pairing URL of 30-60 bytes; version 10 at 213 bytes is four times that, and
// every version above 10 needs a second alignment-pattern row that nothing here
// would ever exercise. `encodeQr` throws `QrTooLongError` rather than silently
// producing something else.
//
// TEXT ENCODING. Byte mode in the standard is ISO-8859-1, but every scanner
// written this century reads a byte-mode payload as UTF-8 when it is not valid
// Latin-1, and the payload here is an ASCII URL where the two agree exactly.
// UTF-8 is what is emitted, and the round-trip test carries a multibyte string.
//
// Sources for every table and polynomial below: ISO/IEC 18004 (Table 1 symbol
// sizes, Table 9 error correction characteristics, Table 13 mask patterns,
// Table 23 penalty scores, Annex C format/version information, Annex E
// alignment pattern centres). Cross-checked against Project Nayuki's
// QR-Code-generator (MIT) - see the test file's citations.

/** The four error correction levels. Only `M` is produced here; the type exists
 *  because `qrFormatBits` covers all four and the test walks every one. */
export type QrEcc = "L" | "M" | "Q" | "H";

/** A finished symbol. `modules[row][col]`, `true` = dark. No quiet zone: the
 *  margin is a rendering choice and lives in `qrPath`. */
export interface QrSymbol {
  /** 1..10. */
  readonly version: number;
  /** `4 * version + 17`, so 21..57. */
  readonly size: number;
  /** `[row][col]`, `true` = dark. */
  readonly modules: boolean[][];
  /** The data mask that won on penalty score, 0..7. */
  readonly mask: number;
}

/** Thrown by `encodeQr` for a payload that no version up to 10 can hold. Named
 *  so a caller can tell "too long" from a programming error. */
export class QrTooLongError extends Error {
  readonly bytes: number;
  constructor(bytes: number, max: number) {
    super(`a QR payload of ${bytes} bytes does not fit; the largest symbol here holds ${max}`);
    this.name = "QrTooLongError";
    this.bytes = bytes;
  }
}

// ---------------------------------------------------------------- the tables

/** The highest version this module builds. */
export const QR_MAX_VERSION = 10;

/** ISO/IEC 18004 Table 1, total codewords per symbol, versions 1..10. */
export const QR_TOTAL_CODEWORDS: readonly number[] = [
  26, 44, 70, 100, 134, 172, 196, 242, 292, 346,
];

export interface QrBlockSpec {
  /** Error correction codewords per block. */
  readonly ec: number;
  /** Data codewords per block, short blocks first (the standard's group 1
   *  before group 2), which is also the interleaving order. */
  readonly blocks: readonly number[];
}

/** ISO/IEC 18004 Table 9, error correction level M, versions 1..10. */
export const QR_ECC_M: readonly QrBlockSpec[] = [
  { ec: 10, blocks: [16] },
  { ec: 16, blocks: [28] },
  { ec: 26, blocks: [44] },
  { ec: 18, blocks: [32, 32] },
  { ec: 24, blocks: [43, 43] },
  { ec: 16, blocks: [27, 27, 27, 27] },
  { ec: 18, blocks: [31, 31, 31, 31] },
  { ec: 22, blocks: [38, 38, 39, 39] },
  { ec: 22, blocks: [36, 36, 36, 37, 37] },
  { ec: 26, blocks: [43, 43, 43, 43, 44] },
];

/** ISO/IEC 18004 Annex E, alignment pattern centre coordinates, versions 1..10.
 *  Version 1 has none. */
const QR_ALIGN: readonly (readonly number[])[] = [
  [],
  [6, 18], [6, 22], [6, 26], [6, 30], [6, 34],
  [6, 22, 38], [6, 24, 42], [6, 26, 46], [6, 28, 50],
];

/** Copy of the alignment centres for `version`, so a caller cannot mutate the
 *  table it is checking. */
export function qrAlignmentCentres(version: number): number[] {
  requireVersion(version);
  return [...QR_ALIGN[version - 1]];
}

/** Character-count field width for byte mode: 8 bits to version 9, 16 from
 *  version 10 (ISO/IEC 18004 Table 3). */
export function qrCountBits(version: number): number {
  requireVersion(version);
  return version <= 9 ? 8 : 16;
}

/** Symbol width in modules. */
export function qrSize(version: number): number {
  requireVersion(version);
  return version * 4 + 17;
}

/** Modules available to data and error correction codewords, i.e. the whole
 *  symbol minus every function pattern. Derived from the GEOMETRY, not from a
 *  table, because it is the independent check on `QR_TOTAL_CODEWORDS`. */
export function qrRawDataModules(version: number): number {
  requireVersion(version);
  let n = (16 * version + 128) * version + 64;
  if (version >= 2) {
    // Each alignment pattern costs 25 modules, less the overlaps with the two
    // timing lines and the three it is never drawn at (the finder corners).
    const align = Math.floor(version / 7) + 2;
    n -= (25 * align - 10) * align - 55;
    if (version >= 7) n -= 36; // the two version-information blocks
  }
  return n;
}

/** Payload bytes each version holds in byte mode at level M. DERIVED from the
 *  block table and the header width, never typed in: a transcription slip in a
 *  capacity table is invisible until the symbol overflows in the field. */
export const QR_BYTE_CAPACITY: readonly number[] = QR_ECC_M.map((spec, i) => {
  const version = i + 1;
  const dataCw = spec.blocks.reduce((a, b) => a + b, 0);
  return Math.floor((dataCw * 8 - 4 - qrCountBits(version)) / 8);
});

/** The largest payload any symbol here holds: version 10 at level M. */
export const QR_MAX_BYTES = QR_BYTE_CAPACITY[QR_MAX_VERSION - 1];

function requireVersion(version: number): void {
  if (!Number.isInteger(version) || version < 1 || version > QR_MAX_VERSION) {
    throw new Error(`QR version ${version} is outside the 1..${QR_MAX_VERSION} this module builds`);
  }
}

/** Every disagreement between the pinned tables and the geometry, as sentences.
 *  Empty means the tables are self-consistent. Run at import time below. */
export function qrSelfCheck(): string[] {
  const problems: string[] = [];
  for (let v = 1; v <= QR_MAX_VERSION; v++) {
    const spec = QR_ECC_M[v - 1];
    const total = QR_TOTAL_CODEWORDS[v - 1];
    const sum = spec.blocks.reduce((a, b) => a + b, 0) + spec.ec * spec.blocks.length;
    if (sum !== total) {
      problems.push(`version ${v}: the M block table sums to ${sum} codewords, Table 1 says ${total}`);
    }
    const fromGeometry = Math.floor(qrRawDataModules(v) / 8);
    if (fromGeometry !== total) {
      problems.push(`version ${v}: the geometry leaves room for ${fromGeometry} codewords, Table 1 says ${total}`);
    }
    const centres = QR_ALIGN[v - 1];
    if (v >= 2 && (centres.length !== Math.floor(v / 7) + 2 || centres[0] !== 6)) {
      problems.push(`version ${v}: ${centres.length} alignment centres, the geometry expects ${Math.floor(v / 7) + 2} starting at 6`);
    }
  }
  return problems;
}

{
  // A corrupted constant table cannot be allowed to reach a phone camera, so
  // this throws at import rather than returning a symbol nobody can scan.
  const problems = qrSelfCheck();
  if (problems.length > 0) throw new Error(`qr.ts tables are inconsistent: ${problems.join("; ")}`);
}

// ------------------------------------------------------------- GF(256) and RS

// The QR field: GF(2^8) modulo x^8 + x^4 + x^3 + x^2 + 1 (0x11D), primitive
// element 2. `GF_EXP` is doubled so a log sum never needs a modulo.
const GF_EXP = new Uint8Array(512);
const GF_LOG = new Uint8Array(256);
{
  let x = 1;
  for (let i = 0; i < 255; i++) {
    GF_EXP[i] = x;
    GF_LOG[x] = i;
    x <<= 1;
    if (x & 0x100) x ^= 0x11d;
  }
  for (let i = 255; i < 512; i++) GF_EXP[i] = GF_EXP[i - 255];
}

function gfMul(a: number, b: number): number {
  if (a === 0 || b === 0) return 0;
  return GF_EXP[GF_LOG[a] + GF_LOG[b]];
}

/** The generator polynomial of degree `degree`, highest power first, monic.
 *  Product of (x - alpha^i) for i = 0..degree-1. */
function rsGenerator(degree: number): number[] {
  let poly = [1];
  for (let i = 0; i < degree; i++) {
    const root = GF_EXP[i];
    const next = new Array<number>(poly.length + 1).fill(0);
    for (let j = 0; j < poly.length; j++) {
      next[j] ^= poly[j];
      next[j + 1] ^= gfMul(poly[j], root);
    }
    poly = next;
  }
  return poly;
}

/** The `ecLen` Reed-Solomon error correction codewords for one data block:
 *  the remainder of the data polynomial divided by the generator. */
export function qrRsEncode(data: readonly number[], ecLen: number): number[] {
  if (!Number.isInteger(ecLen) || ecLen < 1) throw new Error(`ecLen ${ecLen} is not a positive integer`);
  const gen = rsGenerator(ecLen);
  const rem = new Array<number>(ecLen).fill(0);
  for (const byte of data) {
    const factor = byte ^ rem[0];
    rem.shift();
    rem.push(0);
    for (let i = 0; i < ecLen; i++) rem[i] ^= gfMul(gen[i + 1], factor);
  }
  return rem;
}

// ---------------------------------------------------- format and version bits

const ECC_INDICATOR: Record<QrEcc, number> = { L: 1, M: 0, Q: 3, H: 2 };

/** The 15 format bits: two level bits and three mask bits, extended by a
 *  BCH(15,5) code with generator 0x537 and masked with 0x5412 so an all-zero
 *  format never occurs (ISO/IEC 18004 section 8.9). */
export function qrFormatBits(ecc: QrEcc, mask: number): number {
  if (!Number.isInteger(mask) || mask < 0 || mask > 7) throw new Error(`mask ${mask} is not 0..7`);
  const data = (ECC_INDICATOR[ecc] << 3) | mask;
  let rem = data;
  for (let i = 0; i < 10; i++) rem = (rem << 1) ^ ((rem >>> 9) * 0x537);
  return ((data << 10) | rem) ^ 0x5412;
}

/** The 18 version bits for version 7 and up: the version number extended by a
 *  BCH(18,6) code with generator 0x1F25, unmasked (ISO/IEC 18004 section 8.10). */
export function qrVersionBits(version: number): number {
  requireVersion(version);
  let rem = version;
  for (let i = 0; i < 12; i++) rem = (rem << 1) ^ ((rem >>> 11) * 0x1f25);
  return (version << 12) | rem;
}

// --------------------------------------------------------------- the bit path

function utf8Bytes(text: string): number[] {
  return Array.from(new TextEncoder().encode(text));
}

/** Payload size in bytes as the encoder counts it, which is UTF-8 bytes and not
 *  `String.length`. The Connection sheet asks before it draws. */
export function qrByteLength(text: string): number {
  return utf8Bytes(text).length;
}

export interface QrBlock {
  readonly data: number[];
  readonly ec: number[];
}

/** The chosen version and its Reed-Solomon blocks, before interleaving. Exposed
 *  so the test can check every block's syndromes without re-deriving them from
 *  the finished matrix. */
export function qrEncodeBlocks(text: string): { version: number; blocks: QrBlock[] } {
  const bytes = utf8Bytes(text);
  if (bytes.length > QR_MAX_BYTES) throw new QrTooLongError(bytes.length, QR_MAX_BYTES);
  let version = 1;
  while (bytes.length > QR_BYTE_CAPACITY[version - 1]) version++;

  const spec = QR_ECC_M[version - 1];
  const dataCw = spec.blocks.reduce((a, b) => a + b, 0);
  const capacityBits = dataCw * 8;

  const bits: number[] = [];
  const push = (value: number, len: number): void => {
    for (let i = len - 1; i >= 0; i--) bits.push((value >>> i) & 1);
  };
  push(0b0100, 4); // byte mode
  push(bytes.length, qrCountBits(version));
  for (const b of bytes) push(b, 8);
  // Terminator: up to four zero bits, fewer if the capacity runs out first.
  for (let i = 0; i < 4 && bits.length < capacityBits; i++) bits.push(0);
  while (bits.length % 8 !== 0) bits.push(0);

  const codewords: number[] = [];
  for (let i = 0; i < bits.length; i += 8) {
    let v = 0;
    for (let j = 0; j < 8; j++) v = (v << 1) | bits[i + j];
    codewords.push(v);
  }
  // Pad codewords, alternating, from ISO/IEC 18004 section 8.4.9.
  for (let pad = 0xec; codewords.length < dataCw; pad = pad === 0xec ? 0x11 : 0xec) {
    codewords.push(pad);
  }

  const blocks: QrBlock[] = [];
  let offset = 0;
  for (const n of spec.blocks) {
    const data = codewords.slice(offset, offset + n);
    offset += n;
    blocks.push({ data, ec: qrRsEncode(data, spec.ec) });
  }
  return { version, blocks };
}

/** Block-column-major interleaving (ISO/IEC 18004 section 8.6): the first data
 *  codeword of every block, then the second, and so on; a short block simply
 *  has nothing to contribute on the last pass. Then the same over the error
 *  correction codewords, which are all the same length. */
export function qrInterleave(blocks: readonly QrBlock[]): number[] {
  const out: number[] = [];
  const maxData = blocks.reduce((m, b) => Math.max(m, b.data.length), 0);
  for (let i = 0; i < maxData; i++) {
    for (const b of blocks) if (i < b.data.length) out.push(b.data[i]);
  }
  const ecLen = blocks.length === 0 ? 0 : blocks[0].ec.length;
  for (let i = 0; i < ecLen; i++) {
    for (const b of blocks) out.push(b.ec[i]);
  }
  return out;
}

// ------------------------------------------------------------- the matrix

interface Grid {
  size: number;
  modules: boolean[][];
  /** `true` where a function pattern lives: never masked, never carries data. */
  fn: boolean[][];
}

function newGrid(size: number): Grid {
  const make = (): boolean[][] =>
    Array.from({ length: size }, () => new Array<boolean>(size).fill(false));
  return { size, modules: make(), fn: make() };
}

function setFn(g: Grid, row: number, col: number, dark: boolean): void {
  g.modules[row][col] = dark;
  g.fn[row][col] = true;
}

/** A finder pattern and its separator, centred on (row, col). The separator is
 *  the light ring at Chebyshev distance 4, clipped at the symbol edge. */
function drawFinder(g: Grid, row: number, col: number): void {
  for (let dr = -4; dr <= 4; dr++) {
    for (let dc = -4; dc <= 4; dc++) {
      const r = row + dr;
      const c = col + dc;
      if (r < 0 || r >= g.size || c < 0 || c >= g.size) continue;
      const dist = Math.max(Math.abs(dr), Math.abs(dc));
      // Dark at 0-1 (the core) and 3 (the border); light at 2 (the inner
      // ring) and 4 (the separator).
      setFn(g, r, c, dist !== 2 && dist !== 4);
    }
  }
}

/** A 5x5 alignment pattern centred on (row, col). */
function drawAlignment(g: Grid, row: number, col: number): void {
  for (let dr = -2; dr <= 2; dr++) {
    for (let dc = -2; dc <= 2; dc++) {
      setFn(g, row + dr, col + dc, Math.max(Math.abs(dr), Math.abs(dc)) !== 1);
    }
  }
}

/** Writes the 15 format bits into both of their homes: once around the
 *  top-left finder, once split between the other two, plus the always-dark
 *  module at (4V+9, 8). Called with a placeholder mask while the matrix is
 *  being built, purely to reserve the cells, and again with the winner. */
function drawFormat(g: Grid, ecc: QrEcc, mask: number): void {
  const bits = qrFormatBits(ecc, mask);
  const bit = (i: number): boolean => ((bits >>> i) & 1) !== 0;

  // Copy 1, around the top-left finder.
  for (let i = 0; i <= 5; i++) setFn(g, i, 8, bit(i));
  setFn(g, 7, 8, bit(6));
  setFn(g, 8, 8, bit(7));
  setFn(g, 8, 7, bit(8));
  for (let i = 9; i < 15; i++) setFn(g, 8, 14 - i, bit(i));

  // Copy 2, under the top-right finder and beside the bottom-left one.
  for (let i = 0; i < 8; i++) setFn(g, 8, g.size - 1 - i, bit(i));
  for (let i = 8; i < 15; i++) setFn(g, g.size - 15 + i, 8, bit(i));

  // The dark module. Row 4V+9 is `size - 8`.
  setFn(g, g.size - 8, 8, true);
}

/** The two 6x3 version-information blocks, version 7 and up. */
function drawVersion(g: Grid, version: number): void {
  if (version < 7) return;
  const bits = qrVersionBits(version);
  for (let i = 0; i < 18; i++) {
    const dark = ((bits >>> i) & 1) !== 0;
    const a = g.size - 11 + (i % 3);
    const b = Math.floor(i / 3);
    setFn(g, b, a, dark); // above the bottom-left finder... (row b, col a)
    setFn(g, a, b, dark); // ...and its mirror left of the top-right finder
  }
}

function drawFunctionPatterns(g: Grid, version: number): void {
  // Timing first, so the finders overwrite their corners with the same values
  // they would have had anyway and every cell ends up flagged exactly once.
  for (let i = 0; i < g.size; i++) {
    setFn(g, 6, i, i % 2 === 0);
    setFn(g, i, 6, i % 2 === 0);
  }
  drawFinder(g, 3, 3);
  drawFinder(g, 3, g.size - 4);
  drawFinder(g, g.size - 4, 3);

  const centres = QR_ALIGN[version - 1];
  const last = centres.length - 1;
  for (let i = 0; i < centres.length; i++) {
    for (let j = 0; j < centres.length; j++) {
      // The three corners already hold finders.
      if ((i === 0 && j === 0) || (i === 0 && j === last) || (i === last && j === 0)) continue;
      drawAlignment(g, centres[i], centres[j]);
    }
  }

  drawFormat(g, "M", 0); // placeholder: reserves the cells
  drawVersion(g, version);
}

/** Two-column zigzag from the right edge, upward then downward, skipping the
 *  vertical timing line at column 6 (ISO/IEC 18004 section 8.7). Bits beyond
 *  the codeword stream - the remainder bits - are left light. */
function drawCodewords(g: Grid, data: readonly number[]): void {
  let i = 0;
  for (let rightCol = g.size - 1; rightCol >= 1; rightCol -= 2) {
    // Column 6 is the timing line; the pair to its left is (5, 4).
    const right = rightCol <= 6 ? rightCol - 1 : rightCol;
    const upward = ((right + 1) & 2) === 0;
    for (let vert = 0; vert < g.size; vert++) {
      for (let j = 0; j < 2; j++) {
        const col = right - j;
        const row = upward ? g.size - 1 - vert : vert;
        if (g.fn[row][col]) continue;
        if (i >= data.length * 8) continue;
        g.modules[row][col] = ((data[i >>> 3] >>> (7 - (i & 7))) & 1) !== 0;
        i++;
      }
    }
  }
}

/** ISO/IEC 18004 Table 10, the eight data mask conditions. `true` = invert. */
export function qrMaskAt(mask: number, row: number, col: number): boolean {
  switch (mask) {
    case 0: return (row + col) % 2 === 0;
    case 1: return row % 2 === 0;
    case 2: return col % 3 === 0;
    case 3: return (row + col) % 3 === 0;
    case 4: return (Math.floor(row / 2) + Math.floor(col / 3)) % 2 === 0;
    case 5: return ((row * col) % 2) + ((row * col) % 3) === 0;
    case 6: return (((row * col) % 2) + ((row * col) % 3)) % 2 === 0;
    case 7: return (((row + col) % 2) + ((row * col) % 3)) % 2 === 0;
    default: throw new Error(`mask ${mask} is not 0..7`);
  }
}

/** XOR the mask over every data module. Applying it twice restores the matrix,
 *  which is how the eight candidates are scored on one grid. */
function applyMask(g: Grid, mask: number): void {
  for (let r = 0; r < g.size; r++) {
    for (let c = 0; c < g.size; c++) {
      if (!g.fn[r][c] && qrMaskAt(mask, r, c)) g.modules[r][c] = !g.modules[r][c];
    }
  }
}

// ------------------------------------------------------------- the penalties

export interface QrPenalty {
  /** Runs of five or more of one colour. */
  readonly n1: number;
  /** 2x2 blocks of one colour. */
  readonly n2: number;
  /** Finder-lookalike patterns. */
  readonly n3: number;
  /** Deviation from an even mix of dark and light. */
  readonly n4: number;
  readonly total: number;
}

/** The finder-lookalike, both ways round: 1:1:3:1:1 dark-light with four light
 *  modules on one side of it. */
const N3_PATTERNS: readonly (readonly number[])[] = [
  [1, 0, 1, 1, 1, 0, 1, 0, 0, 0, 0],
  [0, 0, 0, 0, 1, 0, 1, 1, 1, 0, 1],
];

function runScore(len: number): number {
  return len >= 5 ? 3 + (len - 5) : 0;
}

function scanRuns(get: (i: number) => boolean, len: number): number {
  if (len === 0) return 0;
  let score = 0;
  let run = 1;
  for (let i = 1; i < len; i++) {
    if (get(i) === get(i - 1)) run++;
    else {
      score += runScore(run);
      run = 1;
    }
  }
  return score + runScore(run);
}

function scanPatterns(get: (i: number) => boolean, len: number): number {
  let score = 0;
  for (const pat of N3_PATTERNS) {
    for (let start = 0; start + pat.length <= len; start++) {
      let hit = true;
      for (let k = 0; k < pat.length; k++) {
        if (get(start + k) !== (pat[k] === 1)) { hit = false; break; }
      }
      if (hit) score += 40;
    }
  }
  return score;
}

/** ISO/IEC 18004 Table 23. The four penalties are returned separately because
 *  the tests assert one rule at a time on a matrix built to trip only it. */
export function qrPenalty(modules: readonly (readonly boolean[])[]): QrPenalty {
  const rows = modules.length;
  const cols = rows === 0 ? 0 : modules[0].length;

  let n1 = 0;
  for (let r = 0; r < rows; r++) n1 += scanRuns((i) => modules[r][i], cols);
  for (let c = 0; c < cols; c++) n1 += scanRuns((i) => modules[i][c], rows);

  let n2 = 0;
  for (let r = 0; r + 1 < rows; r++) {
    for (let c = 0; c + 1 < cols; c++) {
      const v = modules[r][c];
      if (modules[r][c + 1] === v && modules[r + 1][c] === v && modules[r + 1][c + 1] === v) n2 += 3;
    }
  }

  let n3 = 0;
  for (let r = 0; r < rows; r++) n3 += scanPatterns((i) => modules[r][i], cols);
  for (let c = 0; c < cols; c++) n3 += scanPatterns((i) => modules[i][c], rows);

  let dark = 0;
  for (let r = 0; r < rows; r++) for (let c = 0; c < cols; c++) if (modules[r][c]) dark++;
  const total = rows * cols;
  // |dark/total*100 - 50| / 5, in integers so no rounding ever decides a mask.
  const n4 = total === 0 ? 0 : Math.floor(Math.abs(dark * 20 - total * 10) / total) * 10;

  return { n1, n2, n3, n4, total: n1 + n2 + n3 + n4 };
}

// ------------------------------------------------------------------ the API

/**
 * Encode `text` as a byte-mode, level-M QR symbol in the smallest version from
 * 1 to 10 that holds it. Throws `QrTooLongError` above `QR_MAX_BYTES`.
 */
export function encodeQr(text: string): QrSymbol {
  const { version, blocks } = qrEncodeBlocks(text);
  const data = qrInterleave(blocks);
  const g = newGrid(qrSize(version));
  drawFunctionPatterns(g, version);
  drawCodewords(g, data);

  let best = 0;
  let bestScore = Infinity;
  for (let m = 0; m < 8; m++) {
    applyMask(g, m);
    drawFormat(g, "M", m);
    const score = qrPenalty(g.modules).total;
    if (score < bestScore) {
      bestScore = score;
      best = m;
    }
    applyMask(g, m); // XOR again to undo
  }
  applyMask(g, best);
  drawFormat(g, "M", best);

  return { version, size: g.size, modules: g.modules, mask: best };
}

/**
 * One SVG path covering every dark module, in MODULE units, offset by a
 * `quiet`-module margin. Horizontal runs are merged into single rects, which
 * turns a version-3 symbol from about 700 subpaths into about 200 and keeps
 * the DOM small enough to re-render on every keystroke.
 *
 * Pair it with `viewBox="0 0 W W"` where `W = sym.size + 2 * quiet`.
 */
export function qrPath(sym: QrSymbol, quiet = 4): string {
  const parts: string[] = [];
  for (let r = 0; r < sym.size; r++) {
    let c = 0;
    while (c < sym.size) {
      if (!sym.modules[r][c]) { c++; continue; }
      let end = c;
      while (end + 1 < sym.size && sym.modules[r][end + 1]) end++;
      const len = end - c + 1;
      parts.push(`M${c + quiet} ${r + quiet}h${len}v1h-${len}z`);
      c = end + 1;
    }
  }
  return parts.join("");
}
