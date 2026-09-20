import { JSDOM } from "jsdom";
import { strict as assert } from "node:assert";
const dom=new JSDOM('<div id="root"></div>',{url:"http://localhost/"});
for(const key of ["window","document","navigator","HTMLElement","HTMLCanvasElement"]){Object.defineProperty(globalThis,key,{value:key==="window"?dom.window:(dom.window as any)[key],configurable:true});}
Object.assign(globalThis,{IS_REACT_ACT_ENVIRONMENT:true});
let width=713;
Object.defineProperty(dom.window.HTMLElement.prototype,"clientWidth",{get:()=>width,configurable:true});
dom.window.HTMLCanvasElement.prototype.getContext=(()=>null) as any;
let onResize=()=>{};
Object.assign(globalThis,{ResizeObserver:class{constructor(cb:()=>void){onResize=cb;}observe(){}disconnect(){}}});
const {createElement:h,act}=await import("react");
const {createRoot}=await import("react-dom/client");
const {SkyDome}=await import("../../components/cloudmap/SkyDome");
const root=createRoot(document.getElementById("root")!);
let passed=0,failed=0;
function test(name:string,fn:()=>void){try{fn();passed++;}catch(e){failed++;console.error(name,e);}}
await act(async()=>root.render(h(SkyDome,{grid:null,overlay:g=>h("svg",{"data-width":g.cssW,width:g.cssW})})));
test("overlay wrapper takes available width instead of locking previous canvas width",()=>assert.equal(document.querySelector<HTMLElement>("[data-dome-wrap]")!.style.width,"100%"));
test("canvas and overlay begin with shared container geometry",()=>{assert.equal(document.querySelector("canvas")!.style.width,"713px");assert.equal(document.querySelector("svg")!.getAttribute("width"),"713");});
await act(async()=>{width=280;onResize();});
test("resize to phone repaints canvas and overlay without telemetry or a new prop",()=>{assert.equal(document.querySelector("canvas")!.style.width,"280px");assert.equal(document.querySelector("svg")!.getAttribute("width"),"280");});
await act(async()=>{width=190;onResize();});
test("narrow split panes are not forced to a 220px minimum",()=>assert.equal(document.querySelector("canvas")!.style.width,"190px"));
await act(async()=>root.unmount());
console.log(`guided dome resize: ${passed}/${passed+failed} passed`);
export const result={passed,failed,total:passed+failed};if(failed)process.exitCode=1;
