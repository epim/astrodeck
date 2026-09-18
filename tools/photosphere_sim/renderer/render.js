/**
 * The photosphere simulator's renderer: CONTRACT.md's scene drawn with
 * Three.js inside headless Chromium, driven from sim/render.py.
 *
 * This file implements the contract, never the application. It repaints the
 * background from the same declarative recipe sim/truth.py evaluates and
 * shares no code with it, which is the point: if the two disagree the tests
 * see it, and if they shared an implementation they could not.
 *
 * What the contract fixes and this file therefore may not choose:
 *
 * - World axes are ENU, x east, y north, z up. Three.js is y-up, so
 *   `toThree([e, n, u]) = [e, u, -n]`: Three's x is east, y is up and z is
 *   south. That map is a proper rotation, so a right-handed camera basis
 *   stays right-handed through it.
 * - The camera's world matrix has columns [right, up, -forward], the OpenGL
 *   frame, because a Three.js camera looks down its own -z. Feeding it
 *   [right, up, forward] instead is a sign error a determinant check will not
 *   catch; sim/geometry.py keeps the two frames deliberately apart and so
 *   does `poseMatrix` below.
 * - The vertical field of view comes from the contract's `fx`, not from the
 *   declared short-axis field of view directly: `vfov = 2 atan((H/2) / fx)`.
 *   For a portrait frame the short axis is the width, so the declared angle
 *   is the horizontal one and Three.js wants the vertical one.
 * - The background texture is the recipe in CONTRACT.md's Scene schema plus
 *   the four choices its "Texture conventions" section pins. It lives in
 *   `renderer/texture.js` so that it can be run without a browser, and
 *   `tests/test_render_texture.py` compares its output with Python's texel
 *   for texel.
 * - Colour is exact. Every material is `MeshBasicMaterial` (unlit), colour
 *   management is off, the output colour space is linear and textures carry
 *   no colour space, so an 8-bit colour goes in and the same 8-bit colour
 *   comes out. Nothing here may introduce a tone map or a transfer function.
 *
 * Two choices the contract leaves open, made here and stated so a reader does
 * not have to infer them:
 *
 * - The background sphere is re-centred on the camera every frame. The
 *   background is directional, so it must show no parallax; leaving the
 *   sphere at the world origin would move a disc by up to 0.011 degrees as
 *   the arc route carries the camera 0.75 m off the pivot.
 * - The ground plane follows the camera in the horizontal plane for the same
 *   reason: the truth's plane is infinite and a 4000 m square is not.
 *
 * This module needs a browser: it is Three.js and a canvas. The parts that do
 * not are in `renderer/texture.js`, which is what the Node-side tests import.
 */

import * as THREE from 'three';

// The texture recipe lives in its own module because it must run where
// Three.js and the DOM do not: see renderer/texture.js and
// tests/test_render_texture.py, which compares its output with Python's.
import {
  PALETTE,
  TEXTURE_HEIGHT,
  TEXTURE_WIDTH,
  backgroundTextureRGBA,
} from './texture.js';

const DEG = Math.PI / 180;

/** The ground plane's extent; see the module docstring on why it follows the camera. */
const PLANE_SIZE = 4000;

/** How far off its face a surface landmark's disc sits, in metres. */
const SURFACE_LANDMARK_OFFSET_M = 0.002;

const NEAR_M = 0.05;
const FAR_M = 20000;

function toThree(v) {
  return new THREE.Vector3(v[0], v[2], -v[1]);
}

function unit(v) {
  const n = Math.hypot(v[0], v[1], v[2]);
  return [v[0] / n, v[1] / n, v[2] / n];
}

function basicMaterial(colour, extra) {
  return new THREE.MeshBasicMaterial({
    color: new THREE.Color().setRGB(colour[0] / 255, colour[1] / 255, colour[2] / 255,
                                    THREE.NoColorSpace),
    // Unlit, and two-sided because the truth has no notion of facing: its
    // ground plane is a plane rather than a half-space and `_hit_plane`
    // reports a hit from either side, so a back-face cull would be this
    // renderer disagreeing with the oracle it is scored against the moment a
    // scene or a route put the camera on the other side of one. Nothing in
    // the chart yard needs it today (the camera is outside every object, and
    // `lookAt(centre + normal)` points a surface landmark's local +Z along
    // the outward normal, so its front face already looks out), and an unlit
    // material looks the same from either side.
    side: THREE.DoubleSide,
    ...extra,
  });
}

/**
 * The background sphere's texture, and why it is addressed backwards.
 *
 * `SphereGeometry` runs its u from the -x axis round through +z, which in
 * this world's axes means u increases as azimuth DECREASES:
 * `az = (0.75 - u) * 360`. The texture is painted in the contract's own
 * layout (column x is azimuth ascending), so the uv transform undoes the
 * difference exactly: `repeat.x = -1`, `offset.x = 0.75`. A mirrored lookup
 * is exact, unlike repainting the texture in the geometry's convention, which
 * would leave the simulator with two layouts to keep straight.
 */
function backgroundTexture(sceneJson) {
  const canvas = document.createElement('canvas');
  canvas.width = TEXTURE_WIDTH;
  canvas.height = TEXTURE_HEIGHT;
  const context = canvas.getContext('2d', { willReadFrequently: false });
  const data = backgroundTextureRGBA(sceneJson, TEXTURE_WIDTH, TEXTURE_HEIGHT);
  context.putImageData(new ImageData(data, TEXTURE_WIDTH, TEXTURE_HEIGHT), 0, 0);

  const texture = new THREE.CanvasTexture(canvas);
  texture.colorSpace = THREE.NoColorSpace;
  texture.generateMipmaps = false;
  // Linear, not nearest: the frame is only 1.4x coarser than the texture, so
  // there is no aliasing to hide from, and a blended edge puts a disc's
  // centroid nearer its true centre than a staircase does. Mipmaps are off
  // because a mip level would wash a 1 degree disc into the background.
  texture.minFilter = THREE.LinearFilter;
  texture.magFilter = THREE.LinearFilter;
  texture.wrapS = THREE.RepeatWrapping;
  texture.wrapT = THREE.ClampToEdgeWrapping;
  texture.repeat.set(-1, 1);
  texture.offset.set(0.75, 0);
  texture.flipY = true;
  texture.needsUpdate = true;
  return texture;
}

function buildObjects(sceneJson, scene) {
  const grounds = [];
  for (const obj of sceneJson.objects || []) {
    const material = basicMaterial(obj.colour);
    let mesh;
    if (obj.kind === 'plane') {
      mesh = new THREE.Mesh(new THREE.PlaneGeometry(PLANE_SIZE, PLANE_SIZE), material);
      mesh.rotation.x = -Math.PI / 2;
      mesh.userData.groundY = obj.z;
      grounds.push(mesh);
    } else if (obj.kind === 'box') {
      const [e0, n0, u0] = obj.min;
      const [e1, n1, u1] = obj.max;
      mesh = new THREE.Mesh(
        new THREE.BoxGeometry(Math.abs(e1 - e0), Math.abs(u1 - u0), Math.abs(n1 - n0)),
        material);
      mesh.position.copy(toThree([(e0 + e1) / 2, (n0 + n1) / 2, (u0 + u1) / 2]));
    } else if (obj.kind === 'cylinder') {
      mesh = new THREE.Mesh(
        new THREE.CylinderGeometry(obj.radius, obj.radius, obj.height, 48, 1, false),
        material);
      mesh.position.copy(toThree([obj.base[0], obj.base[1], obj.base[2] + obj.height / 2]));
    } else if (obj.kind === 'sphere') {
      mesh = new THREE.Mesh(new THREE.SphereGeometry(obj.radius, 48, 32), material);
      mesh.position.copy(toThree(obj.centre));
    } else {
      throw new Error(`unsupported object kind ${obj.kind}`);
    }
    mesh.name = obj.id;
    scene.add(mesh);
  }

  for (const landmark of sceneJson.surface_landmarks || []) {
    const normal = unit(landmark.normal);
    const centre = landmark.centre;
    const offset = [
      centre[0] + SURFACE_LANDMARK_OFFSET_M * normal[0],
      centre[1] + SURFACE_LANDMARK_OFFSET_M * normal[1],
      centre[2] + SURFACE_LANDMARK_OFFSET_M * normal[2],
    ];
    const mesh = new THREE.Mesh(
      new THREE.CircleGeometry(landmark.radius_m, 48),
      basicMaterial(PALETTE[landmark.palette], {
        // 2 mm of clearance is under one depth-buffer step at the hill's
        // 119 m, so the disc is also drawn last and biased towards the
        // viewer: without both it would fight with the face it lies on.
        polygonOffset: true,
        polygonOffsetFactor: -1,
        polygonOffsetUnits: -2,
      }));
    mesh.position.copy(toThree(offset));
    mesh.lookAt(toThree([centre[0] + normal[0], centre[1] + normal[1], centre[2] + normal[2]]));
    mesh.renderOrder = 1;
    mesh.name = landmark.id;
    scene.add(mesh);
  }
  return grounds;
}

const state = {
  renderer: null,
  target: null,
  scene: null,
  camera: null,
  sky: null,
  grounds: [],
  width: 0,
  height: 0,
  pixels: null,
  rgb: null,
};

/**
 * Build the scene, the camera and the render target, once per page.
 *
 * Returns what the driver logs when something has to be explained: the
 * vertical field of view it derived, how long the background took, and which
 * rasteriser Chromium handed it.
 */
function load(sceneJson, cameraJson) {
  if (state.renderer !== null) throw new Error('simRender.load has already run on this page');
  THREE.ColorManagement.enabled = false;

  const width = cameraJson.width;
  const height = cameraJson.height;
  const fx = cameraJson.fx;

  const started = performance.now();
  const scene = new THREE.Scene();
  const sky = new THREE.Mesh(
    new THREE.SphereGeometry(sceneJson.background.distance_m, 256, 128),
    new THREE.MeshBasicMaterial({ map: backgroundTexture(sceneJson), side: THREE.BackSide }));
  scene.add(sky);
  const grounds = buildObjects(sceneJson, scene);
  const textureMs = performance.now() - started;

  const camera = new THREE.PerspectiveCamera(
    (2 * Math.atan((height / 2) / fx)) / DEG, width / height, NEAR_M, FAR_M);
  camera.matrixAutoUpdate = false;
  camera.updateProjectionMatrix();

  const renderer = new THREE.WebGLRenderer({ antialias: false, alpha: false, stencil: false });
  renderer.outputColorSpace = THREE.LinearSRGBColorSpace;
  renderer.toneMapping = THREE.NoToneMapping;
  renderer.setPixelRatio(1);
  renderer.setSize(width, height, false);
  const target = new THREE.WebGLRenderTarget(width, height, {
    minFilter: THREE.NearestFilter,
    magFilter: THREE.NearestFilter,
    depthBuffer: true,
    samples: 0,
  });
  target.texture.colorSpace = THREE.NoColorSpace;

  state.renderer = renderer;
  state.target = target;
  state.scene = scene;
  state.camera = camera;
  state.sky = sky;
  state.grounds = grounds;
  state.width = width;
  state.height = height;
  state.pixels = new Uint8Array(width * height * 4);
  state.rgb = new Uint8Array(width * height * 3);

  const context = renderer.getContext();
  return {
    width,
    height,
    vfov_deg: camera.fov,
    three: THREE.REVISION,
    texture_ms: Math.round(textureMs),
    renderer: context.getParameter(context.RENDERER),
    meshes: scene.children.length,
  };
}

const _matrix = new THREE.Matrix4();

function poseMatrix(pose, position) {
  // Columns [right, up, -forward]: a Three.js camera looks down its own -z.
  _matrix.makeBasis(
    toThree(pose.right),
    toThree(pose.up),
    toThree([-pose.forward[0], -pose.forward[1], -pose.forward[2]]));
  _matrix.setPosition(position);
  return _matrix;
}

function toBase64(bytes) {
  let binary = '';
  const chunk = 0x8000;
  for (let i = 0; i < bytes.length; i += chunk) {
    binary += String.fromCharCode.apply(null, bytes.subarray(i, Math.min(i + chunk, bytes.length)));
  }
  return btoa(binary);
}

/**
 * One frame from one pose, as base64 of packed RGB bytes, top row first.
 *
 * The directional parts of the world follow the camera: see the module
 * docstring on the background sphere and the ground plane.
 */
function render(pose) {
  const { renderer, target, scene, camera, width, height, pixels, rgb } = state;
  if (renderer === null) throw new Error('simRender.load has not been called');

  const position = toThree(pose.position);
  state.sky.position.copy(position);
  for (const ground of state.grounds) {
    ground.position.set(position.x, ground.userData.groundY, position.z);
  }
  camera.matrix.copy(poseMatrix(pose, position));
  camera.matrixWorldNeedsUpdate = true;

  renderer.setRenderTarget(target);
  renderer.render(scene, camera);
  renderer.readRenderTargetPixels(target, 0, 0, width, height, pixels);

  // WebGL hands back the bottom row first; the contract's frames are top row
  // first. Flip, and drop the alpha the frames do not carry.
  for (let y = 0; y < height; y++) {
    let source = (height - 1 - y) * width * 4;
    let destination = y * width * 3;
    for (let x = 0; x < width; x++) {
      rgb[destination] = pixels[source];
      rgb[destination + 1] = pixels[source + 1];
      rgb[destination + 2] = pixels[source + 2];
      source += 4;
      destination += 3;
    }
  }
  return toBase64(rgb);
}

if (typeof window !== 'undefined') {
  window.simRender = { load, render };
}
