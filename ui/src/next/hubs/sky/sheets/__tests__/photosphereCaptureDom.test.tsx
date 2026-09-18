// Real component and capture driver, with camera/sensor fixtures only.
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
const pixels = new Uint8ClampedArray(32 * 120 * 4);
for (let i = 0; i < pixels.length; i += 4) pixels[i] = pixels[i + 1] = pixels[i + 2] = i < pixels.length / 2 ? 240 : 30;
w.HTMLCanvasElement.prototype.getContext = () => ({ drawImage() {},
  getImageData: (_x: number,_y: number,width: number,height: number) => {
    const data=new Uint8ClampedArray(width*height*4);
    // These workflow tests use a textureless camera. Image registration has
    // separate landmark/overlap tests with actual projected world scenes.
    for(let i=0;i<data.length;i+=4){data[i]=data[i+1]=data[i+2]=120;data[i+3]=255;}
    return {data};
  }, createImageData: (width:number,height:number)=>({data:new Uint8ClampedArray(width*height*4)}), putImageData() {},
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
    const track = { stop: () => { stops++; }, getSettings: () => ({ deviceId: constraints.video.deviceId?.exact ?? "ultra" }), addEventListener() {} };
    return { getTracks: () => [track], getVideoTracks: () => [track] };
  },
}, configurable: true });
const ok = (data: unknown) => ({ ok: true, status: 200, json: async () => data });
const writes: string[]=[];
g.fetch = async (url: string,options?:{method?:string}) => {
  if(options?.method && options.method!=='GET')writes.push(String(url));
  return String(url).includes("/api/site") ? ok({ site: { name: "Phone test", horizon_points: [] } }) : ok({});
};
const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../../../../store");
const { HorizonSheet } = await import("../horizon");
const { cameraPose, foldSweepColumns, preferredRearCamera, PhotosphereSweep } = await import("../photosphere");
const { DOME_CELLS } = await import('../photosphereGeometry');
useStore.setState({ principal: { role: "admin", caps: ["config.safety", "config.site_optics"] }, wsPhase: "up",
  equipConnected: true, status: { mode: "sim", connected: {}, busy_lanes: [] }, config: { safety: { horizon: [] } },
} as never);
const root = createRoot(document.getElementById("root")!);
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
  for (const fn of [...intervals.values()]) fn();
}); };
const heading = (az: number, absolute = true, beta = 90) => {
  const ev = new w.Event(absolute ? "deviceorientationabsolute" : "deviceorientation");
  sensorNow+=50;Object.defineProperty(ev,'timeStamp',{value:sensorNow});
  Object.assign(ev, { alpha: (360 - az) % 360, beta, gamma: 0, absolute }); w.dispatchEvent(ev);
};
let passed = 0;
async function test(name: string, fn: () => void | Promise<void>) { await fn(); passed++; console.log(`PASS ${name}`); }

await test("Camera selection avoids a named ultrawide; unknown lens order is not guessed", () => {
  assert.equal(preferredRearCamera(cameras), "main");
  assert.equal(preferredRearCamera([{ deviceId: "a", label: "camera 0" }, { deviceId: "b", label: "camera 1" }]), undefined);
  assert.equal(cameraPose({ alpha: 0, beta: 90, gamma: 0, absolute: false }), null);
  assert.ok(Math.abs(cameraPose({ alpha: 270, beta: 90, gamma: 0, absolute: true })!.az - 90) < .001);
  assert.ok(Math.abs(cameraPose({ alpha: 270, beta: 0, gamma: 90, absolute: true })!.az) < .001);
  assert.deepEqual(foldSweepColumns([{ bin: 1, column: [1] }], 3), [[], [1], []]);
});
await act(async () => root.render(createElement(HorizonSheet, { depth: 0, params: { site: "current" }, guided: true })));
await settle();
await test("Camera permission opens a visible preview, guidance, and camera picker", async () => {
  await click(byTest("capture-photosphere"));
  assert.equal(byTest("photosphere-capturing").hidden, false);
  assert.equal(byTest("photosphere-video").style.display, "");
  assert.ok((byTest("photosphere-video") as HTMLVideoElement).srcObject);
  assert.ok(document.querySelector('[aria-label="Camera for horizon scan"]'));
  assert.match(byTest("photosphere-capturing").textContent!, /Waiting for the compass/);
  assert.equal(requested[0].video.deviceId.exact, "main");
});
await test("No compass or relative-only events cannot invent sweep progress", async () => {
  heading(80, false); await tick(); await click(byTest("start-horizon-scan"));
  for (let i = 0; i < 35; i++) await tick();
  assert.equal(document.querySelector('[role="progressbar"]')?.getAttribute("aria-valuenow"), "0");
  assert.equal(byTest("start-horizon-scan").getAttribute("aria-disabled"), "true");
});
await test("A chosen lens is requested explicitly and the old camera is released", async () => {
  const select = document.querySelector<HTMLSelectElement>("select")!;
  await act(async () => { select.value = "other"; select.dispatchEvent(new w.Event("change", { bubbles: true })); }); await settle();
  assert.equal(requested.at(-1).video.deviceId.exact, "other");
  assert.equal(stops, 1);
});
await test("A level sweep is incomplete; vertical scans and overhead unlock Review", async () => {
  heading(6); await tick(); await click(byTest("start-horizon-scan")); await tick();
  assert.equal(byTest("stop-and-trace").getAttribute("aria-disabled"), null);
  const firstProgress=Number(document.querySelector('[role="progressbar"]')?.getAttribute('aria-valuenow'));
  for (let i = 0; i < 35; i++) await tick();
  assert.equal(Number(document.querySelector('[role="progressbar"]')?.getAttribute("aria-valuenow")), firstProgress);
  for (let i = 1; i < 30; i++) { heading(i * 12 + 6); await tick(); }
  assert.ok(Number(document.querySelector('[role="progressbar"]')?.getAttribute("aria-valuenow"))<100);
  assert.equal(byTest("stop-and-trace").getAttribute("aria-disabled"), null);
  assert.match(byTest("photosphere-capturing").textContent!, /blue dot/);
  // Revisit precisely the same bearings higher up. Low frames must survive.
  for (const cell of DOME_CELLS.filter(c=>c.alt<89)) { heading(cell.az,true,90+cell.alt);await tick(); }
  assert.equal(document.querySelector('[role="progressbar"]')?.getAttribute("aria-valuenow"), "98");
  assert.equal(byTest("stop-and-trace").getAttribute("aria-disabled"), null);
  assert.match(byTest("photosphere-capturing").textContent!, /Point the rear camera straight up/);
  assert.ok(byTest("capture-overhead"));
  const now = Date.now;
  Date.now = () => now() + 3000; // The absolute compass has stopped updating.
  const tilt = new w.Event("deviceorientation");
  Object.assign(tilt, { alpha: null, beta: -176, gamma: 0, absolute: false });
  w.dispatchEvent(tilt); await tick(); await tick();
  assert.equal(document.querySelector('[role="progressbar"]')?.getAttribute("aria-valuenow"), "100");
  assert.equal(byTest("stop-and-trace").getAttribute("aria-disabled"), null);
  assert.match(document.querySelector(".photosphere-bearing")!.textContent!, /86° up · Overhead/);
  assert.match(document.querySelector(".photosphere-hint")!.textContent!, /Overhead captured/);
  Date.now = now;
  await click(byTest("stop-and-trace"));
  assert.ok(byTest("photosphere-card")); assert.equal(stops, 2);
  assert.match(byTest("photosphere-card").textContent!, /estimated heights/);
  assert.match(byTest('horizon-panorama').getAttribute('href')!, /^data:image\/png/);
  assert.ok(byTest('editable-horizon-line').getAttribute('d'));
  const report=document.querySelector<HTMLAnchorElement>('a[download="astrodeck-scan-alignment.json"]')!;
  const diagnostic=JSON.parse(decodeURIComponent(report.getAttribute('href')!.split(',')[1]));
  assert.ok(diagnostic.samples.length>0 && diagnostic.samples.length<=16);
  assert.ok(diagnostic.samples.every((s:any)=>s.image && s.basis && s.sensor));
  assert.equal(JSON.stringify(diagnostic).includes('deviceId'),false);
  assert.equal(document.querySelectorAll('[data-testid="horizon-edit-point"]').length,30);
  assert.equal(byTest('horizon-strip').parentElement!.style.width,'1040px');
  assert.equal(byTest('horizon-strip').style.height,'300px');
  assert.ok(document.querySelector('[aria-label="Panorama position"]'));
  assert.equal(writes.length,0,'Review applied an unconfirmed horizon to the rig');
});
await test('The detected points can be dragged over the photograph before saving',async()=>{
  const strip=byTest('horizon-strip'),photo=byTest('horizon-panorama').getAttribute('href');
  strip.getBoundingClientRect=()=>({left:0,top:0,width:340,height:150,right:340,bottom:150,x:0,y:0,toJSON(){}});
  const point=document.querySelector('[data-testid="horizon-edit-point"]')!;
  const x=Number(point.getAttribute('cx')),y=Number(point.getAttribute('cy'));
  const original=byTest('editable-horizon-line').getAttribute('d');
  const pointer=async(type:string,yy:number)=>{await act(async()=>strip.dispatchEvent(new w.MouseEvent(type,{bubbles:true,clientX:x,clientY:yy})));};
  await pointer('pointerdown',y);await pointer('pointermove',y+8);await pointer('pointerup',y+8);
  assert.notEqual(byTest('editable-horizon-line').getAttribute('d'),original);
  assert.equal(byTest('horizon-panorama').getAttribute('href'),photo);
  assert.equal(writes.length,0,'Dragging saved before Save horizon');
});
await test("Manual overhead captures a real frame even with unavailable tilt and heading", async () => {
  await click(byTest("capture-photosphere"));
  heading(6); await tick(); await click(byTest("start-horizon-scan"));
  assert.equal(byTest("capture-overhead"), null);
  for (const cell of DOME_CELLS.filter(c=>c.alt<89)) { heading(cell.az,true,90+cell.alt);await tick(); }
  const now = Date.now; Date.now = () => now() + 3000;
  lastSensor=null;
  await tick();
  assert.match(document.querySelector(".photosphere-hint")!.textContent!, /tap Capture overhead/);
  await click(byTest("capture-overhead"));
  assert.equal(document.querySelector('[role="progressbar"]')?.getAttribute("aria-valuenow"), "100");
  assert.equal(byTest("capture-overhead"), null);
  await click(byTest("stop-and-trace"));
  assert.match(byTest("photosphere-card").textContent!, /captured overhead by hand/);
  Date.now = now;
});
await test("Manual capture cannot fabricate a zenith frame when the camera read fails", async () => {
  const sweep = new PhotosphereSweep(1);
  await sweep.start(document.createElement("video"), document.createElement("canvas"));
  assert.equal(sweep.captureOverhead(), false); // Preview alone cannot capture.
  heading(6); sweep.begin();
  for (const altitude of [0, 35, 70]) { heading(6, true, 90 + altitude); await tick(); }
  const original = w.HTMLCanvasElement.prototype.getContext;
  w.HTMLCanvasElement.prototype.getContext = () => ({ drawImage() { throw new Error("Camera failed"); } });
  assert.equal(sweep.captureOverhead(), false);
  assert.equal(sweep.overheadCaptured, false);
  assert.equal(sweep.complete, false);
  assert.match(sweep.error!, /Could not read/);
  w.HTMLCanvasElement.prototype.getContext = original;
  sweep.stop();
  assert.equal(sweep.captureOverhead(), false);
});
await test('A centred dot reports hold, actual capture, and already captured instead of silence',async()=>{
  const sweep=new PhotosphereSweep();
  await sweep.start(document.createElement('video'),document.createElement('canvas'));
  const cell=sweep.cells.find(c=>c.alt>20&&c.alt<60)!;
  heading(cell.az,true,90+cell.alt);
  assert.match(sweep.captureCue,/Tap Start scan/);assert.equal(sweep.frameCount,0);
  sweep.begin();
  assert.equal(sweep.aimTarget?.id,cell.id);assert.match(sweep.captureCue,/Hold here/);
  await tick();
  assert.ok(sweep.cells.find(c=>c.id===cell.id)?.captured);
  assert.match(sweep.captureCue,/^Captured/);
  const now=Date.now;
  try {
    Date.now=()=>now()+1100;assert.match(sweep.captureCue,/Already captured/);
    // 3 s of wall-clock silence with the source still healthy is not staleness
    // (issue #37): the cue must not regress to waiting for the compass.
    Date.now=()=>now()+3000;assert.match(sweep.captureCue,/Already captured/);
  } finally {Date.now=now;sweep.stop();}
});
await test("Permission denial stays on screen with a useful retry explanation", async () => {
  denied = true; await click(byTest("capture-photosphere"));
  assert.match(document.querySelector('[role="alert"]')!.textContent!, /Camera access was blocked/);
  assert.equal(byTest("photosphere-capturing").hidden, true);
});
await test('Timestamped video frames taken during movement are rejected; settled frames then capture',async()=>{
  denied=false;
  const video=document.createElement('video');
  let callback:VideoFrameRequestCallback|undefined,requestId=0,cancelled=0;
  video.requestVideoFrameCallback=(fn)=>{callback=fn;return ++requestId;};
  video.cancelVideoFrameCallback=(id)=>{cancelled=id;};
  const sweep=new PhotosphereSweep();await sweep.start(video,document.createElement('canvas'));
  const a=sweep.cells.find(c=>c.alt>20&&c.alt<60)!;
  const b=sweep.cells.find(c=>c.alt>20&&c.alt<60&&Math.abs(c.az-a.az)>120)!;
  let capturedAt=0;
  for(let i=0;i<=10;i++){
    const cell=i<6?a:b;heading(cell.az,true,90+cell.alt);
    if(i===5)capturedAt=sensorNow;
  }
  sweep.begin();assert.ok(callback);
  callback(sensorNow,{captureTime:capturedAt,mediaTime:1,presentationTime:sensorNow,expectedDisplayTime:sensorNow,width:640,height:480,presentedFrames:1});
  assert.equal(sweep.frameCount,0,'A timestamp bypassed the steady-hold requirement');
  assert.equal(sweep.cells.find(c=>c.id===b.id)?.captured,false,'Newer phone pose misplaced an older frame');
  assert.equal(sweep.aimTarget?.id,a.id,'Overlay drifted ahead of the displayed frame');
  for(let i=0;i<13;i++)heading(b.az,true,90+b.alt);
  callback!(sensorNow,{captureTime:sensorNow,mediaTime:2,presentationTime:sensorNow,expectedDisplayTime:sensorNow,width:640,height:480,presentedFrames:2});
  assert.ok(sweep.cells.find(c=>c.id===b.id)?.captured,'Settled frame failed to capture');
  sweep.stop();assert.equal(cancelled,requestId);
});
await test('Frames between dots and repeated completed patches do not enter the mosaic',async()=>{
  const sweep=new PhotosphereSweep();await sweep.start(document.createElement('video'),document.createElement('canvas'));
  // Find a visible direction outside every 8-degree target, rather than relying
  // on the source ordering of the geodesic cells.
  let target:{az:number;alt:number}|null=null;
  for(let az=0;az<360&&!target;az+=3)for(let alt=0;alt<80&&!target;alt+=3){
    heading(az,true,90+alt);if(!sweep.aimTarget)target={az,alt};
  }
  assert.ok(target);sweep.begin();await tick();assert.equal(sweep.frameCount,0);
  const cell=sweep.cells.find(c=>c.alt>20&&c.alt<60)!;
  heading(cell.az,true,90+cell.alt);await tick();assert.equal(sweep.frameCount,1);
  await tick();assert.equal(sweep.frameCount,1);sweep.stop();
});
await test('The capture driver anchors relative motion and ignores subsequent absolute compass jumps',async()=>{
  const sweep=new PhotosphereSweep();await sweep.start(document.createElement('video'),document.createElement('canvas'));
  heading(100,true);heading(30,false);
  assert.ok(Math.abs(sweep.currentHeading-100)<.001);
  heading(150,true);assert.ok(Math.abs(sweep.currentHeading-100)<.001);
  heading(31,false);assert.ok(Math.abs(sweep.currentHeading-101)<.001);
  sweep.stop();
});
await test('View-angle correction persists only for the chosen camera and cannot change mid-scan',async()=>{
  const sweep=new PhotosphereSweep(),video=document.createElement('video'),canvas=document.createElement('canvas');
  await sweep.start(video,canvas,'main');assert.equal(sweep.cameraViewAngle,60);
  assert.equal(sweep.setCameraViewAngle(41.1),true);
  assert.equal(sweep.setCameraViewAngle(NaN),false);assert.equal(sweep.setCameraViewAngle(0),false);
  assert.equal(sweep.hasLensCalibration,true);
  heading(6);sweep.begin();assert.equal(sweep.setCameraViewAngle(45),false);sweep.stop();
  await sweep.start(video,canvas,'main');assert.equal(sweep.cameraViewAngle,41.1);
  await sweep.start(video,canvas,'other');assert.equal(sweep.cameraViewAngle,60);assert.equal(sweep.hasLensCalibration,false);
  sweep.stop();
});
await act(async () => root.unmount());
await test('A setup link offers the fitted angle but applies it only after a tap',async()=>{
  w.location.hash='#/classic/tonight?experience=guided&cameraFov=41.1';
  const previewRoot=createRoot(document.getElementById('root')!);
  await act(async()=>previewRoot.render(createElement(HorizonSheet,{depth:0,params:{site:'current'},guided:true})));
  await settle();await click(byTest('capture-photosphere'));
  const input=document.querySelector<HTMLInputElement>('[aria-label="Camera view angle in degrees"]')!;
  assert.equal(input.value,'60','URL silently changed lens geometry');
  const apply=[...document.querySelectorAll('button')].find(b=>b.textContent?.includes('Try scan angle'))!;
  assert.ok(apply);await click(apply);assert.equal(input.value,'41.1');
  assert.match(byTest('photosphere-capturing').textContent!,/saved view angle/);
  await act(async()=>previewRoot.unmount());
});
await test("A grant arriving after cancellation is released", async () => {
  let grant: ((stream: any) => void) | undefined;
  w.navigator.mediaDevices.getUserMedia = () => new Promise(resolve => { grant = resolve; });
  const sweep = new PhotosphereSweep();
  const pending = sweep.start(document.createElement("video"), document.createElement("canvas"));
  while (!grant) await Promise.resolve();
  sweep.stop(); let released = 0;
  grant({ getTracks: () => [{ stop: () => released++ }] });
  await pending; assert.equal(released, 1); assert.equal(sweep.previewReady, false);
});
console.log(`photosphereCaptureDom.test: ${passed}/${passed} passed`);
export const result = { passed, failed: 0, total: passed };
