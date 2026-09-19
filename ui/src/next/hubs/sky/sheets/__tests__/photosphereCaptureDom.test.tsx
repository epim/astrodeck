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
    const track = { stop: () => { stops++; }, getSettings: () => ({ deviceId: constraints.video.deviceId?.exact ?? "ultra" }), addEventListener() {}, readyState: "live", muted: false };
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
const { DOME_CELLS, SkyPanorama } = await import('../photosphereGeometry');
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
  // The camera delivers one more frame and the user presses on it. Without
  // that the press lands on the picture the sweep above already captured and
  // is refused as `frame-already-captured` - a wait for the next frame, not a
  // fault, and a different case from the failing read this one is about.
  mediaTime += 0.1;
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
  const ordinaryGetUserMedia = w.navigator.mediaDevices.getUserMedia;
  let grant: ((stream: any) => void) | undefined;
  w.navigator.mediaDevices.getUserMedia = () => new Promise(resolve => { grant = resolve; });
  try {
    const sweep = new PhotosphereSweep();
    const pending = sweep.start(document.createElement("video"), document.createElement("canvas"));
    while (!grant) await Promise.resolve();
    sweep.stop(); let released = 0;
    grant({ getTracks: () => [{ stop: () => released++ }] });
    await pending; assert.equal(released, 1); assert.equal(sweep.previewReady, false);
  } finally {
    // Restore the mock this test replaced (issue #51: everything after it hung).
    w.navigator.mediaDevices.getUserMedia = ordinaryGetUserMedia;
  }
});
await test('The capture log names a rejection reason before an accepted capture, with basis and cell',async()=>{
  const sweep=new PhotosphereSweep();
  await sweep.start(document.createElement('video'),document.createElement('canvas'));
  const cell=sweep.cells.find(c=>c.alt>20&&c.alt<60)!;
  heading(cell.az,true,90+cell.alt);
  sweep.begin();
  // A bare timer fire with no camera frame behind it. The media clock has not
  // moved since the baseline taken when the preview started playing, so this
  // is refused on the IMAGE now, before any pose test runs - a timer firing is
  // not a camera delivering (review 17, P1). It used to reach the pose and
  // record alignment-wait.
  await act(async()=>{for(const fn of [...intervals.values()])fn();});
  assert.ok(sweep.captureLog.some(r=>r.outcome==='stale-image'),
    'a timer fire with no delivered frame reached the pose tests');
  // Now a frame arrives, so the pose gets its turn - and the compass only just
  // locked on, so the pose has not yet covered the 500 ms settle a capture
  // needs. That is the rejection this case is about.
  mediaTime+=0.1;
  await act(async()=>{for(const fn of [...intervals.values()])fn();});
  await tick();
  assert.ok(sweep.cells.find(c=>c.id===cell.id)?.captured);
  const log=sweep.captureLog;
  assert.ok(log.some(r=>r.outcome==='alignment-wait'));
  const last=log[log.length-1];
  assert.equal(last.outcome,'accepted');
  assert.equal(last.cell,cell.id);
  assert.ok(last.basis);
  sweep.stop();
});
await test('A session with no accepted pose stays diagnosable; panoramaPixels is null until a real capture lands',async()=>{
  const sweep=new PhotosphereSweep();
  await sweep.start(document.createElement('video'),document.createElement('canvas'));
  // A direction outside every DOME_CELLS target: the pose is perfectly valid
  // and settled, but nothing here can ever be accepted.
  let target:{az:number;alt:number}|null=null;
  for(let az=0;az<360&&!target;az+=3)for(let alt=0;alt<80&&!target;alt+=3){
    heading(az,true,90+alt);if(!sweep.aimTarget)target={az,alt};
  }
  assert.ok(target);
  sweep.begin();
  for(let i=0;i<20;i++)await tick();
  assert.equal(sweep.captureLog.length,20);
  assert.ok(sweep.captureLog.every(r=>r.outcome!=='accepted'));
  // Read into a local rather than asserting on sweep.panoramaPixels directly:
  // TS narrows a getter-backed access through an `asserts` call, and the SAME
  // property read further down must not inherit that (now stale) narrowing.
  const beforeCapture=sweep.panoramaPixels;
  assert.equal(beforeCapture,null);
  const cell=sweep.cells.find(c=>c.alt>20&&c.alt<60)!;
  heading(cell.az,true,90+cell.alt);
  await tick();
  assert.ok(sweep.cells.find(c=>c.id===cell.id)?.captured);
  const pixels=sweep.panoramaPixels;
  assert.ok(pixels);
  assert.equal(pixels!.width,1080);
  assert.equal(pixels!.height,300);
  assert.ok(Array.from(pixels!.pixels).some((v,i)=>i%4===3&&v===255));
  sweep.stop();
});
await test('A browser with no DeviceOrientationEvent can still capture the overhead by hand (issue #42 item 3)', async () => {
  const originalDOE = w.DeviceOrientationEvent;
  delete w.DeviceOrientationEvent;
  try {
    const sweep = new PhotosphereSweep();
    await sweep.start(document.createElement('video'), document.createElement('canvas'));
    assert.equal(sweep.usedOrientation, false, 'a compass reading arrived with no constructor to have delivered it');
    // begin() itself requires compassReady, i.e. hasOrientation - a reading
    // this browser structurally can never produce, so it can never be pressed
    // in the real app (filed as its own finding: begin()'s gate keeps this
    // fix unreachable through the UI on such a browser). What is under test
    // here is narrower and does not depend on that: whether grabFrame, given
    // a session already recording, treats a browser with no pose stream as
    // healthy rather than stalled. recording/panorama are the two fields
    // begin() sets that start() does not, so they are set directly to reach
    // that state without a compass reading that cannot exist.
    (sweep as unknown as { recording: boolean }).recording = true;
    (sweep as unknown as { panorama: unknown }).panorama = new SkyPanorama();
    for (let i = 0; i < 3; i++) await tick();
    // Mutation (issue #42 item 4): drop the `|| !this.orientationSupported`
    // term from sourceHealthy. `listening` then stays false for the whole
    // test (no constructor ever attached it), sourceHealthy is false, and
    // grabFrame refuses this call as 'unhealthy' - captureOverhead() returns
    // false and overheadCaptured stays false. Observed red under that
    // mutation and on pre-fix HEAD alike, both being the same code.
    assert.equal(sweep.captureOverhead(), true, 'a manual overhead press was refused with no pose stream to be unhealthy about');
    assert.equal(sweep.overheadCaptured, true);
    sweep.stop();
  } finally {
    w.DeviceOrientationEvent = originalDOE;
  }
});
await test('The mocks are the ordinary ones again', async () => {
  // Must stay last. This pins issue #51: "A grant arriving after
  // cancellation is released" used to replace getUserMedia with a mock that
  // only resolved by hand and never restored it, so sweep.start() below
  // would hang forever rather than fail an assertion (Node exits 13 on an
  // unsettled top-level await). Any future test between here and that one
  // that leaks its own getUserMedia mock the same way is caught right here,
  // not blamed on whatever unrelated test happens to run after it.
  const sweep = new PhotosphereSweep();
  await sweep.start(document.createElement('video'), document.createElement('canvas'));
  assert.equal(sweep.previewReady, true);
  sweep.stop();
});
console.log(`photosphereCaptureDom.test: ${passed}/${passed} passed`);
export const result = { passed, failed: 0, total: passed };
