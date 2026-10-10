// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// The lossless frame encoder, for the replay and the tests (SPEC-v2 T16).
//
// The phone records JPEG, which is lossy, so a recording made on a phone cannot
// be compared pixel for pixel with the frames that went in. The replay and the
// recorder's own tests can: they run under Node, where `./png` already writes
// and reads PNG, and a recording made with this encoder decodes back to exactly
// the pixels that were recorded. Same pixels in, same bytes out, since `encodePng`
// picks one filter for every row. Never imported by production code.
import type { FrameEncoder } from '../pano/types';
import { encodePng } from './png';

export const pngEncoder: FrameEncoder = {
  mime: 'image/png',
  encode(rgba: Uint8ClampedArray, w: number, h: number): string {
    return encodePng(rgba, w, h).toString('base64');
  },
};
