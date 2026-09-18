// A PNG codec, small and complete enough for the replay and nothing more.
//
// The replay runs under Node with no browser image decoder, so the recorded
// frames of a case have to be turned into pixels here. `node:zlib` does the
// compression; the rest of PNG is a chunk container, a per-scanline filter and
// a CRC, all of which are short enough to state exactly.
//
// Read side: 8-bit RGB and RGBA, non-interlaced, every one of the five filter
// types - the encoder that wrote the frames picks a filter per scanline, and a
// decoder that handles only the ones a particular writer happened to emit is a
// decoder that breaks when the writer is upgraded. Anything else (16-bit, a
// palette, interlacing) throws rather than guessing.
//
// Write side: RGBA with filter 0 on every row. One filter, because the file
// this writes is read by a scorer and diffed between runs: the same pixels must
// always produce the same bytes, and an adaptive filter chooser is one more
// thing that could quietly stop doing that.
import { deflateSync, inflateSync } from 'node:zlib';

export interface Raster {
  width: number;
  height: number;
  /** RGBA, row-major, top row first. */
  pixels: Uint8ClampedArray;
}

const SIGNATURE = [0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a];

const CRC_TABLE = (() => {
  const table = new Int32Array(256);
  for (let n = 0; n < 256; n++) {
    let c = n;
    for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
    table[n] = c;
  }
  return table;
})();

function crc32(bytes: Uint8Array): number {
  let c = 0xffffffff;
  for (let i = 0; i < bytes.length; i++) c = CRC_TABLE[(c ^ bytes[i]) & 0xff] ^ (c >>> 8);
  return (c ^ 0xffffffff) >>> 0;
}

/** `a` left, `b` above, `c` above-left - the PNG predictor, byte for byte. */
function paeth(a: number, b: number, c: number): number {
  const p = a + b - c, pa = Math.abs(p - a), pb = Math.abs(p - b), pc = Math.abs(p - c);
  return pa <= pb && pa <= pc ? a : pb <= pc ? b : c;
}

function asBuffer(bytes: Uint8Array): Buffer {
  return Buffer.isBuffer(bytes) ? bytes : Buffer.from(bytes.buffer, bytes.byteOffset, bytes.byteLength);
}

/** Decode an 8-bit, non-interlaced RGB or RGBA PNG into RGBA pixels. */
export function decodePng(bytes: Uint8Array): Raster {
  const data = asBuffer(bytes);
  if (data.length < 8 || SIGNATURE.some((b, i) => data[i] !== b)) throw new Error('not a PNG: bad signature');
  let width = 0, height = 0, channels = 0, seenHeader = false;
  const parts: Buffer[] = [];
  let at = 8;
  while (at + 8 <= data.length) {
    const length = data.readUInt32BE(at), type = data.toString('latin1', at + 4, at + 8);
    const body = data.subarray(at + 8, at + 8 + length);
    if (at + 12 + length > data.length) throw new Error(`PNG chunk ${type} runs past the end of the file`);
    if (type === 'IHDR') {
      width = body.readUInt32BE(0); height = body.readUInt32BE(4);
      const depth = body[8], colour = body[9], compression = body[10], filter = body[11], interlace = body[12];
      if (depth !== 8) throw new Error(`PNG bit depth ${depth} is not supported (8 only)`);
      if (colour !== 2 && colour !== 6) throw new Error(`PNG colour type ${colour} is not supported (2 and 6 only)`);
      if (compression !== 0 || filter !== 0) throw new Error('PNG uses a non-standard compression or filter method');
      if (interlace !== 0) throw new Error('interlaced PNG is not supported');
      channels = colour === 6 ? 4 : 3;
      seenHeader = true;
    } else if (type === 'IDAT') {
      parts.push(Buffer.from(body));
    } else if (type === 'IEND') {
      break;
    }
    at += 12 + length;
  }
  if (!seenHeader) throw new Error('PNG has no IHDR');
  if (!parts.length) throw new Error('PNG has no image data');

  const raw = inflateSync(Buffer.concat(parts));
  const stride = width * channels;
  if (raw.length < height * (stride + 1)) throw new Error('PNG image data is short');
  const pixels = new Uint8ClampedArray(width * height * 4);
  let previous = new Uint8Array(stride), current = new Uint8Array(stride);
  let read = 0;
  for (let y = 0; y < height; y++) {
    const filter = raw[read++];
    if (filter > 4) throw new Error(`PNG filter type ${filter} is not defined`);
    for (let x = 0; x < stride; x++) {
      const value = raw[read + x];
      const a = x >= channels ? current[x - channels] : 0;
      const b = previous[x];
      const c = x >= channels ? previous[x - channels] : 0;
      current[x] = filter === 0 ? value
        : filter === 1 ? value + a
          : filter === 2 ? value + b
            : filter === 3 ? value + ((a + b) >> 1)
              : value + paeth(a, b, c);
    }
    read += stride;
    let out = y * width * 4;
    for (let x = 0; x < stride; x += channels) {
      pixels[out++] = current[x]; pixels[out++] = current[x + 1]; pixels[out++] = current[x + 2];
      pixels[out++] = channels === 4 ? current[x + 3] : 255;
    }
    const swap = previous; previous = current; current = swap;
  }
  return { width, height, pixels };
}

function chunk(type: string, body: Uint8Array): Buffer {
  const out = Buffer.alloc(body.length + 12);
  out.writeUInt32BE(body.length, 0);
  out.write(type, 4, 'latin1');
  Buffer.from(body.buffer, body.byteOffset, body.byteLength).copy(out, 8);
  out.writeUInt32BE(crc32(out.subarray(4, 8 + body.length)), 8 + body.length);
  return out;
}

/** Encode RGBA pixels as an 8-bit RGBA PNG, filter 0 on every scanline. */
export function encodePng(pixels: Uint8Array | Uint8ClampedArray, width: number, height: number): Buffer {
  if (pixels.length !== width * height * 4) throw new Error('pixel buffer does not match the given size');
  const stride = width * 4;
  const raw = Buffer.alloc(height * (stride + 1));
  for (let y = 0; y < height; y++) {
    raw[y * (stride + 1)] = 0;
    for (let x = 0; x < stride; x++) raw[y * (stride + 1) + 1 + x] = pixels[y * stride + x];
  }
  const header = Buffer.alloc(13);
  header.writeUInt32BE(width, 0); header.writeUInt32BE(height, 4);
  header[8] = 8; header[9] = 6; header[10] = 0; header[11] = 0; header[12] = 0;
  return Buffer.concat([
    Buffer.from(SIGNATURE),
    chunk('IHDR', header),
    chunk('IDAT', deflateSync(raw)),
    chunk('IEND', Buffer.alloc(0)),
  ]);
}
