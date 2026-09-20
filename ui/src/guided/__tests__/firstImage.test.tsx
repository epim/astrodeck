const {JSDOM}=await import("jsdom");const dom=new JSDOM('<div id="root"></div>',{url:"http://local/",pretendToBeVisual:true});const win=dom.window as any;
for(const key of ["window","document","navigator","HTMLElement","Event","MouseEvent","localStorage"])Object.defineProperty(globalThis,key,{value:key==="window"?win:win[key],configurable:true});
Object.assign(globalThis,{IS_REACT_ACT_ENVIRONMENT:true});
const target={id:"M 13",name:"Test cluster",type:"Globular cluster",ra_hours:16,dec_deg:36,alt:60,az:200};
let failRanking=true;let request:any=null;let captures=0;
Object.assign(globalThis,{fetch:async(url:string,init:any={})=>{
 if(url==="/api/guided/first-targets"){if(failRanking)return{ok:false,status:504,json:async()=>({detail:"timeout"})};return{ok:true,status:200,json:async()=>({picks:[target],reason:null,simulation:true})};}
 if(url==="/api/capture"){request=JSON.parse(init.body);captures++;}
 if(url==="/api/mount/goto"){
   useStore.setState({status:{...useStore.getState().status,mount:{ra_hours:16,dec_deg:36,slewing:true,parked:false,pointing:{verified:false}}}} as never);
   useStore.setState({status:{...useStore.getState().status,mount:{ra_hours:16,dec_deg:36,slewing:false,parked:false,pointing:{verified:true}}}} as never);
 }
 return{ok:true,status:200,json:async()=>({started:"capture"})};
}});
const {createElement:h,act}=await import("react");const {createRoot}=await import("react-dom/client");
const {useStore}=await import("../../store");const {useGuidedSetup}=await import("../setup");
const FirstImage=(await import("../GuidedFirstImage")).default;
useStore.setState({wsPhase:"up",telemetryStale:false,status:{mode:"sim",mount:{ra_hours:16,dec_deg:36,slewing:false},connected:{camera:{connected:true},telescope:{connected:true}}},principal:{role:"admin",caps:["control.mount","control.capture"]},previews:[],livePreviewId:null} as never);
useGuidedSetup.setState({location:true,horizon:true,focus:true,alignment:true});
const root=createRoot(win.document.getElementById("root"));await act(async()=>root.render(h(FirstImage)));
const button=(t:string)=>[...win.document.querySelectorAll("button")].find((b:any)=>b.textContent.includes(t)) as HTMLButtonElement;
let passed=0,failed=0;function test(n:string,f:()=>void){try{f();passed++;}catch(e){failed++;console.log(n,e);}}function assert(v:unknown){if(!v)throw new Error("assertion failed");}
test("a failed target lookup has an inline retry",()=>{assert(win.document.querySelector('[role="alert"]'));assert(button("Refresh targets"));});
failRanking=false;await act(async()=>button("Refresh targets").click());
test("retry shows target without moving the mount",()=>{assert(button("Test cluster"));assert(request===null);});
test("daylight practice is identified as simulated equipment",()=>assert(win.document.body.textContent.includes("you can practice during daylight")));
await act(async()=>button("Test cluster").click());
await act(async()=>useStore.setState({sequence:{state:"paused"}} as never));
test("another client's paused session blocks pointing with an explanation",()=>assert(button("Point telescope at this target").disabled&&win.document.body.textContent.includes("imaging session is using")));
await act(async()=>useStore.setState({sequence:{state:"idle"}} as never));
await act(async()=>{button("Point telescope at this target").click();await new Promise(r=>setTimeout(r,550));});
await act(async()=>useStore.setState({status:{...useStore.getState().status,busy_lanes:["filter_offsets"]}} as never));
test("filter calibration blocks first capture before another command can be issued",()=>assert(button("Take my first image").disabled&&request===null));
await act(async()=>useStore.setState({status:{...useStore.getState().status,busy_lanes:[]}} as never));
await act(async()=>button("Take my first image").click());
test("capture acceptance alone never congratulates",()=>{assert(request?.save===true&&request.request_id);assert(!win.document.querySelector("img"));});
await act(async()=>{useStore.setState({previews:[{id:1,capture_request_id:"another-client",capture_saved:true}]} as never);await new Promise(r=>setTimeout(r,550));});
test("another client's saved image cannot complete this capture",()=>assert(!win.document.querySelector("img")));
await act(async()=>{useStore.setState({previews:[{id:2,capture_request_id:request.request_id,capture_saved:false}]} as never);await new Promise(r=>setTimeout(r,550));});
test("an unsaved matching frame cannot complete this capture",()=>assert(!win.document.querySelector("img")));
await act(async()=>{useStore.setState({previews:[{id:3,capture_request_id:request.request_id,capture_saved:true,exposure_s:5}]} as never);await new Promise(r=>setTimeout(r,550));});
test("the matching saved frame shows success and its preview",()=>{assert(win.document.querySelector('img[src="/api/preview/3"]'));assert(win.document.body.textContent.includes("practice image is saved"));});
await act(async()=>root.unmount());
const resumed=createRoot(win.document.getElementById("root"));
await act(async()=>resumed.render(h(FirstImage)));
test("reopening the page recovers the matched saved exposure without recapturing",()=>assert(win.document.querySelector('img[src="/api/preview/3"]')&&captures===1));
await act(async()=>resumed.unmount());
const {readCaptureReceipt}=await import("../GuidedFirstImage");
const receipt=JSON.parse(win.localStorage.getItem("astrodeck.guided.first-image.v1"));
test("old receipts do not resurrect an earlier night's capture",()=>assert(readCaptureReceipt({getItem:()=>JSON.stringify({...receipt,requestedAt:Date.now()-13*3600000})})===null));
test("malformed receipts are ignored",()=>assert(readCaptureReceipt({getItem:()=>'{broken'})===null));
await act(async()=>useStore.setState({previews:[]} as never));
const pending=createRoot(win.document.getElementById("root"));await act(async()=>pending.render(h(FirstImage)));
test("a receipt without a saved frame reports waiting instead of success or a new capture",()=>assert(!win.document.querySelector("img")&&win.document.body.textContent.includes("Checking your previous exposure")&&captures===1));
await act(async()=>pending.unmount());
console.log(`guided first image: ${passed}/${passed+failed} passed`);export const result={passed,failed,total:passed+failed};if(failed)process.exitCode=1;
