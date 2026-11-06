// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// w3_4LegacyNorthDom.test.tsx - WP 3.4 (#905): the sheet finishes the legacy scan's north, and only a role that may see
// precise site coordinates gets a declination. The real sheet and the real capture driver, with camera/sensor fixtures
// only (set up as photosphereCaptureDom.test.tsx sets up its own). The site is made up.
//
// Named mutant (an edit to horizon.tsx, from a byte backup): the declination closure passed whatever the role, which
// the viewer case catches.
import assert from "node:assert/strict";
import { JSDOM } from "jsdom";
const dom = new JSDOM('<html><body><div id="root"></div></body></html>', { url: "https://localhost/", pretendToBeVisual: true });
const w = dom.window as any, g = globalThis as any;
for (const k of ["window", "document", "navigator", "HTMLElement", "HTMLVideoElement", "Element", "Node", "Event", "CustomEvent", "MouseEvent", "localStorage", "sessionStorage", "getComputedStyle", "requestAnimationFrame", "cancelAnimationFrame"]) {
  Object.defineProperty(g, k, { value: k === "window" ? w : w[k], writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;
w.matchMedia = () => ({ matches: false, addEventListener() {}, removeEventListener() {} });
g.matchMedia = w.matchMedia;
g.WebSocket = w.WebSocket = class { close() {} addEventListener() {} send() {} };
w.DeviceOrientationEvent = class {};
Object.defineProperty(w, "isSecureContext", { value: true });
Object.defineProperty(w.HTMLVideoElement.prototype, "videoWidth", { get: () => 640 });
Object.defineProperty(w.HTMLVideoElement.prototype, "videoHeight", { get: () => 480 });
w.HTMLVideoElement.prototype.play = async function () {};
// A camera that is actually DELIVERING pictures, which every workflow case here
// has always assumed and none of them said. A bare jsdom video is paused, at
// readyState 0, with a media clock frozen at 0 - in production terms an element
// that has never shown a frame - and capture now requires a delivered image on
// every path (review 17, P1), as it always should have. `mediaTime` advances
// with the frame tick below; a case that needs a stalled camera freezes it.
let mediaTime = 0;
Object.defineProperty(w.HTMLVideoElement.prototype, "currentTime", { get: () => mediaTime, configurable: true });
Object.defineProperty(w.HTMLVideoElement.prototype, "paused", { get: () => false, configurable: true });
Object.defineProperty(w.HTMLVideoElement.prototype, "ended", { get: () => false, configurable: true });
Object.defineProperty(w.HTMLVideoElement.prototype, "readyState", { get: () => 2, configurable: true });
const pixels = new Uint8ClampedArray(32 * 120 * 4);
for (let i = 0; i < pixels.length; i += 4) pixels[i] = pixels[i + 1] = pixels[i + 2] = i < pixels.length / 2 ? 240 : 30;
// The workflow cases see a WORLD: the luminance of the sky in the direction the
// phone's last orientation event points at, rendered through the same 60 degree
// pinhole the scanner assumes. Since #904 a frame is green only once a later
// frame's overlap agrees with it, and a uniform grey agrees with nothing (the
// overlap test reads it as 'unknown'), so a textureless camera can no longer
// stand in for a scan. With no orientation yet the camera shows plain grey.
const worldLuma = (az: number, alt: number): number => {
  const d = Math.PI / 180;
  return 128 + 55 * Math.sin(az * d * 5 + .7) * Math.cos(alt * d * 4) + 40 * Math.sin(az * d * 11 + alt * d * 7)
    + 25 * Math.sin(alt * d * 13 - az * d * 3);
};
const worldFrame = (width: number, height: number): Uint8ClampedArray => {
  const data = new Uint8ClampedArray(width * height * 4);
  const pose = lastSensor, d = Math.PI / 180;
  const basis = pose ? worldBasis(pose.alpha ?? 0, pose.beta ?? 0, pose.gamma ?? 0) : null;
  const tanY = Math.tan(30 * d), tanX = tanY * width / height;
  for (let y = 0; y < height; y++) for (let x = 0; x < width; x++) {
    let value = 120;
    if (basis) {
      const sx = (x + .5) / width, sy = (y + .5) / height;
      const ray = [0, 1, 2].map(k => basis.forward[k] + basis.right[k] * (sx * 2 - 1) * tanX + basis.up[k] * (1 - sy * 2) * tanY);
      const norm = Math.hypot(ray[0], ray[1], ray[2]);
      const alt = Math.asin(ray[2] / norm) / d, az = (Math.atan2(ray[0], ray[1]) / d + 360) % 360;
      value = worldLuma(az, alt);
    }
    const i = (y * width + x) * 4;
    data[i] = data[i + 1] = data[i + 2] = Math.max(0, Math.min(255, value));
    data[i + 3] = 255;
  }
  return data;
};
let worldBasis: (alpha: number, beta: number, gamma: number) => { forward: number[]; right: number[]; up: number[] };
w.HTMLCanvasElement.prototype.getContext = () => ({ drawImage() {},
  getImageData: (_x: number,_y: number,width: number,height: number) => ({ data: worldFrame(width, height) }), createImageData: (width:number,height:number)=>({data:new Uint8ClampedArray(width*height*4)}), putImageData() {},
});
w.HTMLCanvasElement.prototype.toDataURL = () => 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=';
const intervals = new Map<number, () => void>(); let intervalId = 0;
g.setInterval = (fn: () => void) => { intervals.set(++intervalId, fn); return intervalId; };
g.clearInterval = (id: number) => intervals.delete(id);
const cameras = [
  { kind: "videoinput", deviceId: "ultra", label: "Back ultra wide camera" },
  { kind: "videoinput", deviceId: "main", label: "Back main wide camera" },
  { kind: "videoinput", deviceId: "other", label: "Back camera 2" },
];
let denied = false, stops = 0;
const requested: any[] = [];
Object.defineProperty(w.navigator, "mediaDevices", { value: {
  enumerateDevices: async () => cameras,
  getUserMedia: async (constraints: any) => {
    requested.push(constraints);
    if (denied) throw new w.DOMException("Permission denied", "NotAllowedError");
    const track = { stop: () => { stops++; }, getSettings: () => ({ deviceId: constraints.video.deviceId?.exact ?? "ultra" }), addEventListener() {}, readyState: "live", muted: false };
    return { getTracks: () => [track], getVideoTracks: () => [track] };
  },
}, configurable: true });
const ok = (data: unknown) => ({ ok: true, status: 200, json: async () => data });
const writes: string[]=[];
g.fetch = async (url: string,options?:{method?:string}) => {
  if(options?.method && options.method!=='GET')writes.push(String(url));
  return String(url).includes("/api/site") ? ok({ site: { name: "Phone test", horizon_points: [], latitude: 12.5, longitude: -45.25, elevation_m: 100 } }) : ok({});
};
const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../../../../store");
const { HorizonSheet } = await import("../horizon");
const { orientationBasis } = await import('../photosphereGeometry');
worldBasis = (alpha, beta, gamma) => orientationBasis(alpha, beta, gamma, 0);
useStore.setState({ principal: { role: "admin", caps: ["config.safety", "config.site_optics", "view.site_precise"] }, wsPhase: "up",
  equipConnected: true, status: { mode: "sim", connected: {}, busy_lanes: [] }, config: { safety: { horizon: [] } },
} as never);
const settle = async () => { await act(async () => { await Promise.resolve(); await Promise.resolve(); }); };
const byTest = (id: string) => document.querySelector<HTMLElement>(`[data-testid="${id}"]`)!;
const click = async (el: HTMLElement) => { assert.ok(el); await act(async () => el.click()); await settle(); };
let sensorNow=10000;
Object.defineProperty(performance,'now',{value:()=>sensorNow,configurable:true});
let lastSensor:{type:string;alpha:number|null;beta:number|null;gamma:number|null;absolute:boolean}|null=null;
for(const type of ['deviceorientation','deviceorientationabsolute'])w.addEventListener(type,(e:any)=>{lastSensor={type,alpha:e.alpha,beta:e.beta,gamma:e.gamma,absolute:e.absolute};});
const tick = async () => { await act(async () => {
  // A real camera frame after 600 ms of sensor observations, not instantaneous
  // sensor teleportation followed by a fabricated simultaneous video frame.
  for(let i=0;i<6;i++){
    sensorNow+=100;
    if(lastSensor){const {type,...pose}=lastSensor;const event=new w.Event(type);Object.assign(event,pose);Object.defineProperty(event,'timeStamp',{value:sensorNow});w.dispatchEvent(event);}
  }
  // 600 ms of camera at the same time as 600 ms of sensor: the frames a real
  // preview delivered while those events arrived.
  mediaTime += 0.6;
  for (const fn of [...intervals.values()]) fn();
}); };
const heading = (az: number, absolute = true, beta = 90) => {
  const ev = new w.Event(absolute ? "deviceorientationabsolute" : "deviceorientation");
  sensorNow+=50;Object.defineProperty(ev,'timeStamp',{value:sensorNow});
  Object.assign(ev, { alpha: (360 - az) % 360, beta, gamma: 0, absolute }); w.dispatchEvent(ev);
};
let passed = 0;
async function test(name: string, fn: () => void | Promise<void>) { await fn(); passed++; console.log(`PASS ${name}`); }

// Same abilities as the admin below but not the precise site location: the capability the declination is gated on.
const withoutSiteCaps = { role: "admin", caps: ["config.safety", "config.site_optics"] };
const adminCaps = { role: "admin", caps: ["config.safety", "config.site_optics", "view.site_precise"] };

/** A short scan in the sheet, finished by Review, and the north block of the alignment report it leaves. */
async function northOf(principal: unknown) {
  useStore.setState({ principal } as never);
  const previousHash = w.location.hash;
  w.location.hash = "#/classic/tonight?experience=guided";
  const scanRoot = createRoot(document.getElementById("root")!);
  try {
    await act(async () => scanRoot.render(createElement(HorizonSheet, { depth: 0, params: { site: "current" }, guided: true })));
    await settle();
    await click(byTest("capture-photosphere"));
    heading(6); await tick(); await click(byTest("start-horizon-scan")); await tick();
    heading(18); await tick(); heading(30); await tick();
    await click(byTest("stop-and-trace"));
    const link = document.querySelector<HTMLAnchorElement>('a[download="astrodeck-scan-alignment.json"]')!;
    assert.ok(link, "the review offered no alignment report");
    return JSON.parse(decodeURIComponent(link.getAttribute("href")!.split(",")[1])).north;
  } finally {
    await act(async () => scanRoot.unmount());
    w.location.hash = previousHash;
  }
}

await test("a role that may see the site gets a declination, and the report says so without carrying it", async () => {
  const north = await northOf(adminCaps);
  assert.equal(north.declinationApplied, true);
  assert.equal(north.source, "absolute-only");
  assert.deepEqual(Object.keys(north).sort(), ["declinationApplied", "samples", "source", "spreadDeg"]);
});
await test("a role without view.site_precise gets the scan's magnetic azimuths and the report says so", async () => {
  const north = await northOf(withoutSiteCaps);
  assert.equal(north.declinationApplied, false);
});

console.log(`w3_4LegacyNorthDom.test: ${passed}/${passed} passed`);
export const result = { passed, failed: 0, total: passed };
