import {JSDOM} from "jsdom";
const dom=new JSDOM("<div/>",{url:"http://local/#/classic/focus?experience=guided"});
for(const key of ["window","document","navigator","localStorage"])Object.defineProperty(globalThis,key,{value:key==="window"?dom.window:(dom.window as any)[key],configurable:true});
const {useStore}=await import("../../store");const {useGuidedSetup}=await import("../setup");const {startGuidedRecovery}=await import("../recovery");const {api}=await import("../../api");
let timer:()=>unknown=()=>{};
dom.window.setInterval=((callback:()=>unknown)=>{timer=callback;return 1;}) as never;
dom.window.clearInterval=(()=>{}) as never;
const originalGet=api.get,originalPost=api.post;
let passed=0,failed=0;
const assert=(v:unknown)=>{if(!v)throw new Error("assertion failed");};const flush=()=>new Promise(r=>setTimeout(r,5));
async function test(name:string,fn:()=>unknown){try{await fn();passed++;}catch(e){failed++;console.log(name,e);}}
const ready={location:true,horizon:true,focus:true,alignment:true};
const blank={location:false,horizon:false,focus:false,alignment:false};
const base={boot_id:"boot",context:"same",revision:1,facts:ready,reason:"Ready",busy:[]};
function seed(){useStore.setState({wsPhase:"up",telemetryStale:false,status:{connected:{camera:{connected:true},telescope:{connected:true}},busy_lanes:[]},config:{site:{name:"test"},safety:{horizon:[]}},focus:null,polar:{state:"idle"}} as never);useGuidedSetup.setState({...blank,field:null,savingChecks:false,recovering:false});}
try {
 await test("new controller context wins over local readiness even if a focus event arrived during lookup",async()=>{
  seed();api.get=async()=>base as never;const stop=startGuidedRecovery();await flush();
  assert(useGuidedSetup.getState().focus&&useGuidedSetup.getState().alignment);
  let reply!:(value:unknown)=>void;api.get=(()=>new Promise(r=>{reply=r;})) as never;timer();
  useStore.setState({focus:{state:"done",points:[],best:{position:100,hfr:2}}} as never);
  reply({...base,context:"new-device",revision:2,facts:blank});await flush();
  const s=useGuidedSetup.getState();stop();assert(!s.location&&!s.horizon&&!s.focus&&!s.alignment);
 });
 await test("another client's setup invalidation clears a browser-only manual focus check",async()=>{
  seed();let answer={...base,facts:{...ready,focus:false,alignment:false}};
  api.get=async()=>answer as never;const stop=startGuidedRecovery();await flush();
  await useGuidedSetup.getState().complete("focus","manual");assert(useGuidedSetup.getState().focus);
  answer={...answer,revision:2,facts:blank};timer();await flush();
  const afterInvalidation=useGuidedSetup.getState().focus;
  answer={...answer,revision:3,facts:{location:true,horizon:true,focus:false,alignment:false}};timer();await flush();
  const afterReview=useGuidedSetup.getState().focus;stop();assert(!afterInvalidation&&!afterReview);
 });
 await test("remote invalidation wins over an in-flight websocket event in the same context",async()=>{
  seed();api.get=async()=>base as never;const stop=startGuidedRecovery();await flush();
  let reply!:(value:unknown)=>void;api.get=(()=>new Promise(r=>{reply=r;})) as never;timer();
  useStore.setState({focus:{state:"done",points:[],best:{position:100,hfr:2}}} as never);
  reply({...base,revision:2,facts:{...ready,focus:false,alignment:false}});await flush();
  const s=useGuidedSetup.getState();stop();assert(!s.focus&&!s.alignment);
 });
} finally {api.get=originalGet;api.post=originalPost;}
console.log(`guided recovery independent review: ${passed}/${passed+failed} passed`);export const result={passed,failed,total:passed+failed};if(failed)process.exitCode=1;
