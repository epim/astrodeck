import { JSDOM } from "jsdom";
const dom=new JSDOM("<div/>",{url:"http://local/"});
for(const key of ["window","document","localStorage","navigator"]){
 Object.defineProperty(globalThis,key,{value:key==="window"?dom.window:(dom.window as any)[key],configurable:true});
}
const {useStore}=await import("../../store");
const {useGuidedSetup,stepBlocker,atPosition}=await import("../setup");
let passed=0,failed=0;
function test(name:string,fn:()=>void){try{fn();passed++;}catch(e){failed++;console.log(name,e);}}
function assert(v:unknown){if(!v)throw new Error("assertion failed");}
const connected={telescope:{connected:true},camera:{connected:true}};
useStore.setState({wsPhase:"up",telemetryStale:false,status:{connected,backend_links:[]}} as never);
test("saved equipment alone never certifies later steps",()=>{assert(!stepBlocker("location"));assert(stepBlocker("horizon"));assert(stepBlocker("image"));});
test("missing mount blocks even with camera",()=>{useStore.setState({status:{connected:{camera:{connected:true}}}} as never);assert(stepBlocker("location")?.includes("mount"));});
test("missing camera blocks even with mount",()=>{useStore.setState({status:{connected:{telescope:{connected:true}}}} as never);assert(stepBlocker("location")?.includes("camera"));});
test("disconnected live backend overrides old device flags",()=>{useStore.setState({status:{connected,backend_links:[{role:"telescope",connected:false}]}} as never);assert(stepBlocker("location")?.includes("mount"));});
useStore.setState({status:{connected},telemetryStale:false} as never);
test("each confirmed prerequisite releases only its next step",()=>{useGuidedSetup.getState().complete("location");assert(!stepBlocker("horizon"));assert(stepBlocker("focus"));useGuidedSetup.getState().complete("horizon");assert(!stepBlocker("focus"));assert(stepBlocker("alignment"));useGuidedSetup.getState().complete("focus");assert(!stepBlocker("alignment"));assert(stepBlocker("image"));});
test("stale telemetry closes completed steps",()=>{useStore.setState({telemetryStale:true});assert(stepBlocker("alignment"));useStore.setState({telemetryStale:false});});
test("editing terrain invalidates field, focus and alignment",()=>{useGuidedSetup.getState().setField({ra_hours:2,dec_deg:10});useGuidedSetup.getState().complete("focus");useGuidedSetup.getState().complete("alignment");useGuidedSetup.getState().invalidate("horizon");assert(!useGuidedSetup.getState().field);assert(!useGuidedSetup.getState().focus);assert(!useGuidedSetup.getState().alignment);});
test("arrival wraps RA through zero and rejects wrong declination",()=>{assert(atPosition({ra_hours:23.999,dec_deg:20},{ra_hours:.001,dec_deg:20}));assert(!atPosition({ra_hours:0,dec_deg:10},{ra_hours:0,dec_deg:20}));assert(!atPosition(undefined,{ra_hours:0,dec_deg:20}));});
console.log(`guided prerequisites: ${passed}/${passed+failed} passed`);
export const result={passed,failed,total:passed+failed};if(failed)process.exitCode=1;
