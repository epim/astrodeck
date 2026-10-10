// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// WP-151 / #791: the guided focus-and-alignment sky while the mount does not
// know where it points.
//
// After a power cycle the AM5 reports its HOME position, the pole, wherever the
// tube is (`status.mount.position_known === false`, #144). `GuidedFocusSky`
// built its "Mount pointing" marker from `status.mount.alt/az` without reading
// the flag, so the first-night sky showed the scope aimed at the pole and said
// "Mount pointing" under it, on the screen a beginner is told to trust.
//
// This mounts the real component with the mount block in four states and reads
// the legend line ("Mount pointing") and the explanation under the dome:
//   known (flag true, or ABSENT as an engine older than #144 sends it): the
//     legend is there and no hidden-position note is - the control;
//   unknown (flag false): no legend, and a note that says why;
//   stale link (the connection dropped): the existing "until the connection
//     returns" note, and NOT the new one - the two causes are different.
//
// Named mutant (reads the raw angles again): in GuidedFocusSky.tsx replace
// `const pointing=fresh?believedPointing(mount):null;` with
// `const pointing=fresh&&mount?{alt:mount.alt,az:mount.az}:null;`.
import { JSDOM } from "jsdom";
const dom=new JSDOM('<div id="root"></div>',{url:"http://local/",pretendToBeVisual:true});const win=dom.window as any;
win.matchMedia=()=>({matches:false,addEventListener(){},removeEventListener(){},addListener(){},removeListener(){}});
win.ResizeObserver=class{observe(){}unobserve(){}disconnect(){}};
win.HTMLCanvasElement.prototype.getContext=function(){return null;};
for(const key of ["window","document","navigator","HTMLElement","Event","MouseEvent","localStorage","getComputedStyle","matchMedia","ResizeObserver","requestAnimationFrame","cancelAnimationFrame"])Object.defineProperty(globalThis,key,{value:key==="window"?win:win[key],configurable:true,writable:true});
Object.assign(globalThis,{IS_REACT_ACT_ENVIRONMENT:true});
// The dome panel behind the legend polls the cloud model; a 404 for all of it is
// a quiet failure the panel already survives, and nothing here grades the dome.
Object.assign(globalThis,{fetch:async()=>({ok:false,status:404,statusText:"Not Found",headers:{get:()=>"application/json"},json:async()=>({}),text:async()=>""})});
const {createElement:h,act}=await import("react");const {createRoot}=await import("react-dom/client");
const {useStore}=await import("../../store");const {GuidedFocusSky}=await import("../GuidedFocusSky");

let passed=0,failed=0;const failures:string[]=[];
function test(name:string,fn:()=>void){try{fn();passed++;}catch(e){failed++;failures.push(`x ${name}: ${(e as Error).message}`);}}
function assert(cond:boolean,msg:string){if(!cond)throw new Error(msg);}

const MOUNT={ra_hours:3.5,dec_deg:40,ra_str:"03:30:00",dec_str:"+40:00:00",tracking:true,parked:false,slewing:false,alt:45,az:120};
function seed(mount:Record<string,unknown>,link:"up"|"down"="up"){
  act(()=>{useStore.setState({
    wsPhase:link,telemetryStale:false,
    // Somewhere in Colorado - deliberately nowhere near the real rig.
    site:{name:"Back lawn",latitude:40.0,longitude:-105.25,is_default:false,horizon_min_deg:15},
    config:{version:1},
    status:{mode:"sim",connected:{},mount},
  } as never);});
}
const root=createRoot(win.document.getElementById("root"));
async function show(mount:Record<string,unknown>,link:"up"|"down"="up"):Promise<string>{
  seed(mount,link);
  await act(async()=>root.render(h(GuidedFocusSky,{field:null,arcDeg:24,now:Date.now()/1000,expired:false})));
  await act(async()=>{await new Promise(r=>setTimeout(r,0));});
  return String(win.document.querySelector(".guided-focus-sky")?.textContent??"");
}
const LEGEND=/Mount pointing/;
const HIDDEN=/hidden until the mount knows where it points/;
const LINK=/hidden until the connection returns/;

let t=await show({...MOUNT,position_known:true});
test("control: the component rendered its figure at all",()=>assert(/Your sky/.test(t),`got ${JSON.stringify(t)}`));
test("control: a mount that knows where it points gets the 'Mount pointing' legend and no hidden note",()=>{
  assert(LEGEND.test(t),`no legend for a known position - got ${JSON.stringify(t)}`);
  assert(!HIDDEN.test(t)&&!LINK.test(t),"a known position carries a hidden-position note");
});
t=await show({...MOUNT});
test("control: an ABSENT flag (an engine older than #144) reads as known",()=>assert(LEGEND.test(t)&&!HIDDEN.test(t),`got ${JSON.stringify(t)}`));

t=await show({...MOUNT,position_known:false});
test("position_known false: no 'Mount pointing' legend for the mount's home reading",()=>assert(!LEGEND.test(t),`the legend names a pointing the mount cannot vouch for: ${JSON.stringify(t)}`));
test("position_known false: the sky says why nothing is marked",()=>assert(HIDDEN.test(t)&&!LINK.test(t),`got ${JSON.stringify(t)}`));

t=await show({...MOUNT,position_known:false},"down");
test("a dropped link keeps its own note, not the position one",()=>assert(LINK.test(t)&&!HIDDEN.test(t)&&!LEGEND.test(t),`got ${JSON.stringify(t)}`));

t=await show({...MOUNT,position_known:true});
test("clearing the flag brings the legend back",()=>assert(LEGEND.test(t)&&!HIDDEN.test(t),`got ${JSON.stringify(t)}`));

await act(async()=>root.unmount());
const total=passed+failed;
console.log(`w18FocusSkyPositionUnknown: ${passed}/${total} passed`);
for(const f of failures)console.log("  "+f);
export const result={passed,failed,total};
