import {registerHooks} from "node:module";
registerHooks({load(url:string,context:any,next:any){if(url.endsWith("/guided/GuidedFocusSky.tsx"))return {format:"module",shortCircuit:true,source:"export function GuidedFocusSky(){return null}"};return next(url,context);}} as any);
import {JSDOM} from "jsdom";
const dom=new JSDOM('<div id="root"></div>',{url:"http://local/",pretendToBeVisual:true});const win=dom.window as any;
for(const key of ["window","document","navigator","HTMLElement","Event","MouseEvent","localStorage"])Object.defineProperty(globalThis,key,{value:key==="window"?win:win[key],configurable:true});
Object.assign(globalThis,{IS_REACT_ACT_ENVIRONMENT:true});
const field={ra_hours:8,dec_deg:40,alt:55,az:210,clearance_deg:25};
let plan={field,arc_deg:24,expires_at:Date.now()/1000+120,config_version:11,reason:null};
let moves=0,stops=0,autoArrive=true;
Object.assign(globalThis,{fetch:async(url:string)=>{
  if(url==="/api/mount/goto"){
    moves++;useStore.setState({status:{...useStore.getState().status,mount:{...field,slewing:true,parked:false}}} as never);
    if(autoArrive)useStore.setState({status:{...useStore.getState().status,mount:{...field,slewing:false,parked:false}}} as never);
  }
  if(url==="/api/mount/stop"){stops++;useStore.setState({status:{...useStore.getState().status,mount:{...field,slewing:false,parked:false}}} as never);}
  return {ok:true,status:200,json:async()=>url==="/api/guided/polar-field"?plan:{started:true}};
}});
const {createElement:h,act}=await import("react");const {createRoot}=await import("react-dom/client");
const {useStore}=await import("../../store");const {useGuidedSetup}=await import("../setup");const {GuidedFocusPrep}=await import("../GuidedFocusPrep");
useStore.setState({wsPhase:"up",telemetryStale:false,config:{version:11},principal:{role:"admin",caps:["control.mount"]},status:{mode:"sim",connected:{camera:{connected:true},telescope:{connected:true}},mount:{ra_hours:0,dec_deg:0,slewing:false,parked:false}}} as never);
useGuidedSetup.setState({field:null,location:true,horizon:true,focus:false,alignment:false,recovering:false});
const root=createRoot(win.document.getElementById("root"));await act(async()=>root.render(h(GuidedFocusPrep)));
const button=(text:string)=>[...win.document.querySelectorAll("button")].find((el:any)=>el.textContent.includes(text)) as HTMLButtonElement;
const click=async(text:string)=>act(async()=>button(text).click());
const tick=async()=>act(async()=>{await new Promise(r=>setTimeout(r,550));});
let passed=0,failed=0;const assert=(value:unknown)=>{if(!value)throw new Error("assertion failed");};function test(name:string,fn:()=>void){try{fn();passed++;}catch(e){failed++;console.log(name,e);}}
await click("Find an alignment field");
test("finding a field does not move the mount",()=>assert(moves===0&&button("Point telescope here").disabled));
await act(async()=>win.document.querySelector("input").click());
test("the user must confirm clearance before a move is offered",()=>assert(!button("Point telescope here").disabled));
await act(async()=>useStore.setState({config:{version:12}} as never));
test("changed configuration expires the suggested field before motion",()=>assert(button("Point telescope here").disabled&&moves===0&&win.document.body.textContent.includes("settings changed")));
plan={...plan,config_version:12,expires_at:Date.now()/1000-10};await click("Check the sky again");
test("an expired plan cannot be confirmed",()=>assert(win.document.querySelector("input").disabled&&button("Point telescope here").disabled));
plan={...plan,expires_at:Date.now()/1000+120};await click("Check the sky again");await act(async()=>win.document.querySelector("input").click());
await act(async()=>useStore.setState({sequence:{state:"paused"}} as never));
test("a competing paused sequence blocks the move with an inline reason",()=>assert(button("Point telescope here").disabled&&win.document.body.textContent.includes("imaging session")));
await act(async()=>useStore.setState({sequence:{state:"idle"}} as never));
await click("Point telescope here");
await act(async()=>useStore.setState({config:{version:13}} as never));await tick();
test("configuration changes during a move prevent field certification",()=>assert(moves===1&&!useGuidedSetup.getState().field&&win.document.body.textContent.includes("Settings changed during the move")));
plan={...plan,config_version:13};await click("Check the sky again");await act(async()=>win.document.querySelector("input").click());autoArrive=false;
await click("Point telescope here");await click("Stop mount");await tick();
test("cancelling a move never certifies an arrived field",()=>assert(stops===1&&!useGuidedSetup.getState().field));
autoArrive=true;await click("Point telescope here");await tick();
test("observed movement followed by current arrival enables focusing",()=>assert(useGuidedSetup.getState().field?.ra_hours===field.ra_hours&&moves===3));
await act(async()=>root.unmount());
console.log(`guided focus preparation: ${passed}/${passed+failed} passed`);export const result={passed,failed,total:passed+failed};if(failed)process.exitCode=1;
