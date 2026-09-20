import { JSDOM } from "jsdom";
const dom=new JSDOM('<div id="root"></div>',{url:"http://local/",pretendToBeVisual:true});
const win=dom.window as any;
win.matchMedia=()=>({matches:false,addEventListener(){},removeEventListener(){}});
Object.defineProperty(win,"isSecureContext",{value:true});
Object.defineProperty(win.navigator,"geolocation",{value:{getCurrentPosition(ok:any){ok({coords:{latitude:35,longitude:-115,altitude:100,accuracy:8}});}}});
for(const key of ["window","document","navigator","HTMLElement","HTMLInputElement","Event","MouseEvent","localStorage"]){
 Object.defineProperty(globalThis,key,{value:key==="window"?win:win[key],configurable:true});
}
Object.assign(globalThis,{IS_REACT_ACT_ENVIRONMENT:true});
let creates=0,applies=0,advanced=0,failApply=true;
const writes:string[]=[];
Object.assign(globalThis,{fetch:async(url:string,init:any={})=>{
 const method=init.method??"GET"; if(method!=="GET")writes.push(url);
 let data:any={};
 if(url.includes("mount-gps"))data={available:false,detected:false};
 else if(url==="/api/locations"&&method==="POST"){creates++;data={id:"fictional-test-site"};}
 else if(url.includes("/apply")){applies++;if(failApply)return {ok:false,status:503,json:async()=>({detail:"test failure"})};}
 else if(url==="/api/site")data={site:{name:"Test site",latitude:35,longitude:-115,is_default:false}};
 return {ok:true,status:200,json:async()=>data};
}});
const {act,createElement:h}=await import("react");
const {createRoot}=await import("react-dom/client");
const {useStore}=await import("../../store");
const {useGuidedSetup,observeSetupChecks}=await import("../setup");
const GuidedLocation=(await import("../GuidedLocation")).default;
useStore.setState({principal:{role:"admin",caps:["config.site_optics"]},loadConfig:async()=>{}} as never);
const root=createRoot(win.document.getElementById("root"));
await act(async()=>{root.render(h(GuidedLocation,{onContinue:()=>{advanced++;}}));});
let passed=0,failed=0;
function test(name:string,fn:()=>void){try{fn();passed++;}catch(e){failed++;console.log(name,e);}}
function assert(v:unknown){if(!v)throw new Error("assertion failed");}
const button=(text:string)=>[...win.document.querySelectorAll("button")].find((b:any)=>b.textContent===text) as HTMLButtonElement;
test("browser location is default; manual coordinates are folded away",()=>{assert(button("Use my location"));assert(!win.document.querySelector('input[type="number"]'));assert(win.document.querySelector('input[required]').value==="");});
test("no GPS means disabled with a readable tooltip",()=>{assert(button("Use mount GPS").disabled);assert(button("Use mount GPS").title==="no gps detected");});
await act(async()=>{win.document.querySelector("form").dispatchEvent(new win.Event("submit",{bubbles:true,cancelable:true}));});
test("blank name never creates a site",()=>{assert(creates===0);assert(win.document.querySelector('[role="alert"]').textContent.includes("name"));});
await act(async()=>{button("Use my location").click();const input=win.document.querySelector("input");Object.getOwnPropertyDescriptor(win.HTMLInputElement.prototype,"value")!.set!.call(input,"Test site");input.dispatchEvent(new win.Event("input",{bubbles:true}));});
await act(async()=>{win.document.querySelector("form").dispatchEvent(new win.Event("submit",{bubbles:true,cancelable:true}));});
test("site creation followed by failed activation stays on location",()=>{assert(creates===1&&applies===1&&advanced===0);assert(!useGuidedSetup.getState().location);});
failApply=false;
await act(async()=>{win.document.querySelector("form").dispatchEvent(new win.Event("submit",{bubbles:true,cancelable:true}));});
test("retry activates the same site without duplicate creation",()=>{assert(creates===1&&applies===2&&advanced===1);assert(useGuidedSetup.getState().location);assert(useGuidedSetup.getState().siteId==="fictional-test-site");});
let confirm!: (value:boolean)=>void;
observeSetupChecks(()=>new Promise<boolean>(resolve=>{confirm=resolve;}));
await act(async()=>{win.document.querySelector("form").dispatchEvent(new win.Event("submit",{bubbles:true,cancelable:true}));});
test("navigation waits for the controller's setup checkpoint",()=>{assert(advanced===1);});
await act(async()=>{confirm(true);});
test("successful checkpoint advances after acknowledgement",()=>{assert(advanced===2);});
await act(async()=>{win.document.querySelector("form").dispatchEvent(new win.Event("submit",{bubbles:true,cancelable:true}));});
await act(async()=>{confirm(false);});
test("rejected checkpoint leaves location open for review",()=>{assert(advanced===2);});
observeSetupChecks(null);
await act(async()=>root.unmount());
console.log(`guided location: ${passed}/${passed+failed} passed`);
export const result={passed,failed,total:passed+failed};if(failed)process.exitCode=1;
