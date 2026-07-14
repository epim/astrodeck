// tileGL.ts — thin WebGL1 wrapper for the tile engine (tile-engine spec §4).
// The only file that touches the GL API. initTileGL returns null on failure so
// the caller falls back to the <img> pipeline. One textured-quad shader pair;
// LINEAR + CLAMP_TO_EDGE, NO Y-flip on upload (texcoord v=0 = tile top, matching
// hips_local _JPG_FLIP_Y=False); a keyed GPU-texture LRU (128).
//
// UNPACK_FLIP_Y_WEBGL is never set (Task 3 review contract): the GL default is
// no-flip, so texture row 0 = JPEG row 0 = tile top, matching the pinned
// pixUV2ang convention. This module owns the context exclusively, so the
// default cannot be disturbed by other code.

export interface TileDraw {
  positions: Float32Array; // device px, origin top-left, x right / y down
  uvs: Float32Array;
  indices: Uint16Array;
  texture: WebGLTexture;
}

export interface TileGL {
  texFor(key: string): WebGLTexture | undefined;
  uploadTile(key: string, bitmap: ImageBitmap): WebGLTexture;
  drawTiles(draws: TileDraw[], sizePx: number): void;
  dispose(): void;
}

const VERT = `
attribute vec2 a_pos;
attribute vec2 a_uv;
uniform vec2 u_viewport;
varying vec2 v_uv;
void main() {
  vec2 clip = vec2(a_pos.x / u_viewport.x * 2.0 - 1.0,
                   1.0 - a_pos.y / u_viewport.y * 2.0);
  gl_Position = vec4(clip, 0.0, 1.0);
  v_uv = a_uv;
}`;

const FRAG = `
precision mediump float;
uniform sampler2D u_tex;
varying vec2 v_uv;
void main() { gl_FragColor = texture2D(u_tex, v_uv); }`;

const GPU_LRU = 128;

function compile(gl: WebGLRenderingContext, type: number, src: string): WebGLShader | null {
  const sh = gl.createShader(type);
  if (!sh) return null;
  gl.shaderSource(sh, src);
  gl.compileShader(sh);
  if (!gl.getShaderParameter(sh, gl.COMPILE_STATUS)) {
    gl.deleteShader(sh);
    return null;
  }
  return sh;
}

export function initTileGL(canvas: HTMLCanvasElement): TileGL | null {
  const glMaybe = (canvas.getContext("webgl", { premultipliedAlpha: false })
    || canvas.getContext("experimental-webgl")) as WebGLRenderingContext | null;
  if (!glMaybe) return null;
  // Non-nullable rebind so the hoisted inner functions see a narrowed type.
  const gl: WebGLRenderingContext = glMaybe;
  const vs = compile(gl, gl.VERTEX_SHADER, VERT);
  const fs = compile(gl, gl.FRAGMENT_SHADER, FRAG);
  if (!vs || !fs) return null;
  const prog = gl.createProgram();
  if (!prog) return null;
  gl.attachShader(prog, vs);
  gl.attachShader(prog, fs);
  gl.linkProgram(prog);
  if (!gl.getProgramParameter(prog, gl.LINK_STATUS)) return null;

  const aPos = gl.getAttribLocation(prog, "a_pos");
  const aUv = gl.getAttribLocation(prog, "a_uv");
  const uViewport = gl.getUniformLocation(prog, "u_viewport");
  const uTex = gl.getUniformLocation(prog, "u_tex");
  const posBuf = gl.createBuffer();
  const uvBuf = gl.createBuffer();
  const idxBuf = gl.createBuffer();

  // Keyed GPU-texture LRU (128). Map preserves insertion order for eviction.
  const textures = new Map<string, WebGLTexture>();

  function texFor(key: string): WebGLTexture | undefined {
    const t = textures.get(key);
    if (t) { textures.delete(key); textures.set(key, t); } // promote
    return t;
  }

  function uploadTile(key: string, bitmap: ImageBitmap): WebGLTexture {
    const tex = gl.createTexture() as WebGLTexture;
    gl.bindTexture(gl.TEXTURE_2D, tex);
    // No UNPACK_FLIP_Y_WEBGL: v=0 = tile top (GL default, no flip).
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, bitmap);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
    textures.set(key, tex);
    while (textures.size > GPU_LRU) {
      const oldest = textures.keys().next().value as string;
      const ev = textures.get(oldest);
      textures.delete(oldest);
      if (ev) gl.deleteTexture(ev);
    }
    return tex;
  }

  function drawTiles(draws: TileDraw[], sizePx: number): void {
    gl.viewport(0, 0, sizePx, sizePx);
    gl.clearColor(0, 0, 0, 1);
    gl.clear(gl.COLOR_BUFFER_BIT);
    if (draws.length === 0) return;
    gl.useProgram(prog);
    gl.uniform2f(uViewport, sizePx, sizePx);
    gl.uniform1i(uTex, 0);
    gl.activeTexture(gl.TEXTURE0);
    gl.enableVertexAttribArray(aPos);
    gl.enableVertexAttribArray(aUv);
    for (const d of draws) {
      gl.bindBuffer(gl.ARRAY_BUFFER, posBuf);
      gl.bufferData(gl.ARRAY_BUFFER, d.positions, gl.DYNAMIC_DRAW);
      gl.vertexAttribPointer(aPos, 2, gl.FLOAT, false, 0, 0);
      gl.bindBuffer(gl.ARRAY_BUFFER, uvBuf);
      gl.bufferData(gl.ARRAY_BUFFER, d.uvs, gl.DYNAMIC_DRAW);
      gl.vertexAttribPointer(aUv, 2, gl.FLOAT, false, 0, 0);
      gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, idxBuf);
      gl.bufferData(gl.ELEMENT_ARRAY_BUFFER, d.indices, gl.DYNAMIC_DRAW);
      gl.bindTexture(gl.TEXTURE_2D, d.texture);
      gl.drawElements(gl.TRIANGLES, d.indices.length, gl.UNSIGNED_SHORT, 0);
    }
  }

  function dispose(): void {
    for (const t of textures.values()) gl.deleteTexture(t);
    textures.clear();
    gl.deleteBuffer(posBuf);
    gl.deleteBuffer(uvBuf);
    gl.deleteBuffer(idxBuf);
    gl.deleteProgram(prog);
    gl.deleteShader(vs);
    gl.deleteShader(fs);
    const lose = gl.getExtension("WEBGL_lose_context");
    lose?.loseContext();
  }

  return { texFor, uploadTile, drawTiles, dispose };
}
