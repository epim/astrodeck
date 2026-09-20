import { JSDOM } from "jsdom";
const dom = new JSDOM("<div/>",{url:"http://local/#/classic/focus?experience=guided"});
for(const key of ["window","document","localStorage","navigator"]){
 Object.defineProperty(globalThis,key,{value:key==="window"?dom.window:(dom.window as any)[key],configurable:true});
}
const {useStore}=await import("../../store");
const {useGuidedSetup,stepBlocker}=await import("../setup");
const {initialWalkthrough}=await import("../experience");
const {readField,startGuidedRecovery}=await import("../recovery");
const {api,ApiError}=await import("../../api");
let passed=0,failed=0;
function assert(v:unknown,message="assertion failed"){if(!v)throw new Error(message);}
async function test(name:string,fn:()=>unknown){try{await fn();passed++;}catch(e){failed++;console.log(name,e);}}
const flush=()=>new Promise(resolve=>setTimeout(resolve,10));
const storage=(value:unknown)=>({getItem:()=>JSON.stringify(value)});
const field={ra_hours:5,dec_deg:40};
await test("only same-context recent field drafts restore",()=>{
 assert(readField(storage({context:"same",at:100,field}),"same",200)?.ra_hours===5);
 assert(!readField(storage({context:"old",at:100,field}),"same",200));
 assert(!readField(storage({context:"same",at:100,field}),"same",13*3600000));
 assert(!readField(storage({context:"same",at:300,field}),"same",200));
 assert(!readField(storage({context:"same",at:100,field:{...field,dec_deg:100}}),"same",200));
});
await test("corrupt or blocked storage cannot restore a field",()=>{
 assert(!readField({getItem:()=>"broken"},"same"));
 assert(!readField({getItem:()=>{throw new Error();}},"same"));
});
await test("walkthrough presentation resumes only at its saved route",()=>{
 const value={wizard:"focus",home:false,path:"#/classic/focus"};
 assert(initialWalkthrough(storage(value),"#/classic/focus?experience=guided").wizard==="focus");
 assert(initialWalkthrough(storage(value),"#/classic/atlas").wizard===null);
 assert(initialWalkthrough(storage({...value,wizard:"nonsense"}),"#/classic/focus").wizard===null);
});
const base={boot_id:"boot",context:"context",revision:1,facts:{location:true,horizon:true,focus:true,alignment:false},reason:"Recovered",busy:[]};
function seed(){useStore.setState({wsPhase:"up",telemetryStale:false,status:{connected:{camera:{connected:true},telescope:{connected:true}},busy_lanes:[]},config:{site:{name:"test"},safety:{horizon:[]}},focus:null,polar:{state:"idle"}} as never);useGuidedSetup.setState({location:false,horizon:false,focus:false,alignment:false,field:null,recovering:false,savingChecks:false});}
const originalGet=api.get,originalPost=api.post;
let posts:string[]=[];
try{
 await test("server checkpoints restore checks with no motion or capture requests",async()=>{
  seed();posts=[];api.get=async()=>base as never;api.post=async(path)=>{posts.push(path);return base as never;};
  const stop=startGuidedRecovery();await flush();
  assert(useGuidedSetup.getState().focus);assert(!useGuidedSetup.getState().alignment);assert(posts.length===0);stop();
 });
 await test("snapshot cannot overwrite a newer websocket autofocus event",async()=>{
  seed();let resolve!:(value:any)=>void;api.get=(()=>new Promise(r=>{resolve=r;})) as never;
  const stop=startGuidedRecovery();
  const newer={state:"running",points:[],best:null};useStore.setState({focus:newer} as never);
  resolve({...base,focus:{state:"done",points:[],best:{position:100,hfr:2}}});await flush();
  assert(useStore.getState().focus===newer);assert(!useGuidedSetup.getState().focus);stop();
 });
 await test("snapshot cannot overwrite a newer polar measurement",async()=>{
  seed();let resolve!:(value:any)=>void;api.get=(()=>new Promise(r=>{resolve=r;})) as never;
  const stop=startGuidedRecovery();const newer={state:"running",reading_ts:200};useStore.setState({polar:newer} as never);
  resolve({...base,facts:{...base.facts,alignment:true},polar:{state:"done",reading_ts:100}});await flush();
  assert((useStore.getState().polar as unknown)===newer);assert(!useGuidedSetup.getState().alignment);stop();
 });
 await test("server rejection clears automatic success instead of bypassing validation",async()=>{
  seed();api.get=async()=>({...base,facts:{...base.facts,focus:false}}) as never;
  api.post=async()=>{throw new Error("409 proof invalid");};
  const stop=startGuidedRecovery();await flush();useGuidedSetup.getState().complete("focus");
  assert(stepBlocker("alignment")?.includes("Saving"));await flush();
  assert(!useGuidedSetup.getState().focus);assert(!!stepBlocker("alignment"));stop();
 });
 await test("manual focus is explicitly browser-only and cleared on disconnect",async()=>{
  seed();posts=[];api.get=async()=>({...base,facts:{...base.facts,focus:false}}) as never;api.post=async(path)=>{posts.push(path);return base as never;};
  const stop=startGuidedRecovery();await flush();useGuidedSetup.getState().complete("focus","manual");await flush();
  assert(useGuidedSetup.getState().focus);assert(posts.length===0);useStore.setState({telemetryStale:true});
  assert(!useGuidedSetup.getState().focus);stop();
 });
 await test("a later accepted check replaces an earlier rejection message",async()=>{
  seed();let server={...base,facts:{...base.facts,focus:false}};
  api.get=async()=>server as never;api.post=async()=>{throw new Error("409 conflict");};
  const stop=startGuidedRecovery();await flush();
  assert(!await useGuidedSetup.getState().complete("focus"));
  assert(useGuidedSetup.getState().recoveryMessage?.includes("couldn't confirm"));
  api.post=async()=>{server={...server,revision:2,facts:{...server.facts,focus:true},reason:"Setup checks recovered from this controller."};return server as never;};
  assert(await useGuidedSetup.getState().complete("focus"));
  assert(useGuidedSetup.getState().focus);
  assert(useGuidedSetup.getState().recoveryMessage===server.reason);stop();
 });
 await test("duplicate invalidation conflict is confirmed by a read without another write",async()=>{
  seed();let reads=0,writes=0;
  const cleared={...base,revision:2,facts:{...base.facts,focus:false,alignment:false},reason:"Focus changed. Check the stars before continuing."};
  api.get=async()=>{reads++;return (writes?cleared:base) as never;};
  api.post=async()=>{writes++;throw new ApiError("revision changed",409);};
  const stop=startGuidedRecovery();await flush();useGuidedSetup.getState().invalidate("focus");await flush();
  assert(writes===1&&reads>=3);assert(!useGuidedSetup.getState().focus);assert(!useGuidedSetup.getState().alignment);
  assert(useGuidedSetup.getState().recoveryMessage===cleared.reason);stop();
 });
 await test("invalidation conflict with newer valid facts is not silently accepted",async()=>{
  seed();let writes=0;api.get=async()=>base as never;
  api.post=async()=>{writes++;throw new ApiError("revision changed",409);};
  const stop=startGuidedRecovery();await flush();useGuidedSetup.getState().invalidate("focus");await flush();
  assert(writes===1);assert(useGuidedSetup.getState().recoveryMessage?.includes("couldn't confirm"));stop();
 });
 await test("a lost connection while saving cannot leave a completed flag behind",async()=>{
  seed();api.get=async()=>({...base,facts:{...base.facts,focus:false}}) as never;
  const stop=startGuidedRecovery();await flush();useGuidedSetup.getState().complete("focus");useStore.setState({telemetryStale:true});await flush();
  assert(!useGuidedSetup.getState().focus);assert(!useGuidedSetup.getState().savingChecks);stop();
 });
 await test("filter calibration blocks alignment even if one filter has finished",()=>{
  seed();useGuidedSetup.setState({location:true,horizon:true,focus:true});
  useStore.setState({status:{...useStore.getState().status,busy_lanes:["filter_offsets"]}} as never);
  assert(stepBlocker("alignment")?.includes("focus measurements"));
 });
}finally{api.get=originalGet;api.post=originalPost;}
console.log(`guided recovery: ${passed}/${passed+failed} passed`);
export const result={passed,failed,total:passed+failed};if(failed)process.exitCode=1;
