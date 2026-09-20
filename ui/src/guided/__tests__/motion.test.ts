import { JSDOM } from "jsdom";
import { strict as assert } from "node:assert";
const dom=new JSDOM('<div/>',{url:"http://localhost/"});
for(const key of ["window","document","localStorage","navigator"]){Object.defineProperty(globalThis,key,{value:key==="window"?dom.window:(dom.window as any)[key],configurable:true});}
const {useStore}=await import("../../store");
const {api}=await import("../../api");
const {slewAndWait}=await import("../motion");
const target={ra_hours:4,dec_deg:30};
const status={mount:{...target,parked:false,slewing:false,pointing:{verified:true}},connected:{camera:{connected:true},telescope:{connected:true}},busy_lanes:[]};
const reset=()=>useStore.setState({status:{...status},wsPhase:"up",telemetryStale:false,sequence:{state:"idle"},polar:{state:"idle"},focus:null,filterOffsetsLearn:{state:"idle"}} as never);
let calls=0,passed=0,failed=0;
api.post=(async()=>{calls++;return {};}) as typeof api.post;
async function test(name:string,fn:()=>Promise<void>){try{reset();await fn();passed++;}catch(error){failed++;console.error(name,error);}}
await test("already cancelled move sends no command",async()=>{calls=0;const c=new AbortController();c.abort();await assert.rejects(slewAndWait(target,false,c.signal),/cancelled/);assert.equal(calls,0);});
await test("another operation owns equipment before command",async()=>{calls=0;useStore.setState({status:{...status,busy_lanes:["capture"]}} as never);await assert.rejects(slewAndWait(target,false,new AbortController().signal),/another task/);assert.equal(calls,0);});
await test("old centered flag plus unrelated telemetry cannot confirm a new centered command",async()=>{
 const c=new AbortController();const waiting=slewAndWait(target,true,c.signal);
 useStore.setState({status:{...status}} as never);
 const stop=setTimeout(()=>c.abort(),550);
 await assert.rejects(waiting,/Waiting stopped/);clearTimeout(stop);
});
await test("observed movement then fresh verified arrival completes centered command",async()=>{
 const waiting=slewAndWait(target,true,new AbortController().signal);
 useStore.setState({status:{...status,busy_lanes:["goto"],mount:{...status.mount,slewing:true,pointing:{verified:false}}}} as never);
 useStore.setState({status:{...status}} as never);
 await waiting;
});
console.log(`guided motion: ${passed}/${passed+failed} passed`);
export const result={passed,failed,total:passed+failed};if(failed)process.exitCode=1;
