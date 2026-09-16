/* Integration checks for the classic host, with browser sensors and rig APIs
 * replaced by fixtures. No camera or equipment is accessed by this test. */
const { JSDOM } = await import('jsdom');
const dom = new JSDOM('<html><body><div id="root"></div></body></html>',{url:'http://localhost/#/classic/monitor',pretendToBeVisual:true});
const w=dom.window as any;
w.matchMedia=()=>({matches:true,addEventListener(){},removeEventListener(){}});
w.ResizeObserver=class {observe(){} disconnect(){}};
w.DeviceOrientationEvent=class {};
w.WebSocket=class {close(){} addEventListener(){} send(){}};
Object.defineProperty(w,'isSecureContext',{value:true});
const g=globalThis as any;
for(const k of ['window','document','navigator','HTMLElement','HTMLVideoElement','Element','Node','Event','CustomEvent','MouseEvent','KeyboardEvent','localStorage','location','history','getComputedStyle','matchMedia','WebSocket','ResizeObserver','requestAnimationFrame','cancelAnimationFrame']) {
  Object.defineProperty(g,k,{value:k==='window'?w:w[k],writable:true,configurable:true});
}
g.IS_REACT_ACT_ENVIRONMENT=true;
let cameraStarts=0, cameraStops=0;
Object.defineProperty(w.navigator,'mediaDevices',{value:{getUserMedia:async()=>{cameraStarts++;return {getTracks:()=>[{stop:()=>cameraStops++}]};}},configurable:true});
const asked:string[]=[];
const activePoints=[[0,12],[180,18],[359,12]];
const ok=(data:unknown)=>({ok:true,status:200,json:async()=>data});
g.fetch=async(url:string,init?:{method?:string})=>{
  const u=String(url);asked.push(`${init?.method??'GET'} ${u}`);
  if(u.includes('/api/site'))return ok({site:{name:'Test site',latitude:45,longitude:-122,is_default:false,horizon_min_deg:15,horizon_points:activePoints}});
  if(u.includes('/api/locations'))return ok([{id:'saved',name:'Hilltop',latitude:46,longitude:-121,elevation_m:100,horizon_min_deg:10,horizon_points:[[90,20],[270,5]]}]);
  if(u.includes('/api/cloudmap/dome'))return ok({enabled:false,rows:[],alt_start:3,alt_step:6,az_step:10,stale:true});
  if(u.includes('/api/cloudmap'))return ok({enabled:false,observed_at:null,motion:null,credit:{source:'Fixture'}});
  if(u.includes('/api/catalog/tonight'))return ok({picks:[]});
  if(u.includes('/api/catalog/region'))return ok({rows:[]});
  if(u.includes('/api/catalog'))return ok({results:[]});
  return ok({});
};
const {createElement,act,Fragment}=await import('react');
const {createRoot}=await import('react-dom/client');
const {useStore}=await import('../../../store');
const {ClassicSkyTools}=await import('../ClassicSkyTools');
useStore.setState({principal:{role:'admin',caps:['view.status','view.weather','view.site_precise','view.site_derived','config.site_optics','config.safety']},wsPhase:'up',equipConnected:true,
  config:{version:1,site:{name:'Test site',latitude:45,longitude:-122,horizon_min_deg:15},safety:{horizon:activePoints}},
  status:{site:{name:'Test site',latitude:45,longitude:-122,is_default:false,horizon_min_deg:15},connected:{},looping:false,mode:'sim',busy:null,busy_lanes:[]},
} as never);
localStorage.setItem('astrodeck-next-sky-mode','cam');
const root=createRoot(document.getElementById('root')!);
const settle=async()=>{await act(async()=>{await new Promise(r=>setTimeout(r,25));});};
const click=async(label:string)=>{const b=Array.from(document.querySelectorAll('button')).find(el=>el.textContent?.trim()===label);if(!b)throw new Error(`Missing button: ${label}`);await act(async()=>b.click());await settle();};
let passed=0,failed=0;
const failures:string[]=[];
function assert(value:unknown,message:string):asserts value {if(!value)throw new Error(message);}
async function test(name:string,fn:()=>void|Promise<void>){try{await fn();passed++;}catch(e){failed++;failures.push(`${name}: ${String(e)}`);}}
let closed=false;
await act(async()=>root.render(createElement(ClassicSkyTools,{initialTool:'finder',onClose:()=>{closed=true;}})));
await settle();
await test('Classic opens in map mode even when AR was remembered',()=>{
  assert(document.querySelector('[data-sky-view]'),'Finder missing');
  assert(!document.querySelector('[data-sky-camera]'),'Camera opened on mount');
  assert(cameraStarts===0,'Opening classic requested camera permission');
});
await test('Explicit camera action starts the shared AR stream',async()=>{
  await click('AR camera');
  assert(cameraStarts===1,'Camera not requested exactly once');
  assert(document.querySelector('[data-sky-camera]'),'Camera view missing');
});
await test('Moving to the horizon editor releases the camera and keeps classic',async()=>{
  await click('Edit horizon at this direction');
  assert(cameraStops===1,'Camera track survived leaving the finder');
  assert(document.querySelector('[data-testid="horizon-strip"]'),'Horizon editor missing');
  assert(document.querySelector('[data-testid="point-at-finder"]'),'Finder aim not handed to horizon editor');
  assert(location.hash==='#/classic/monitor','Editor navigated out of classic');
});
await test('Saved-profile editing loads its own points without applying the location',async()=>{
  const select=document.querySelector('select')!;
  await act(async()=>{select.value='saved';select.dispatchEvent(new w.Event('change',{bubbles:true}));});await settle();
  assert(document.body.textContent?.includes('HORIZON · Hilltop'),'Wrong horizon profile');
  assert(!asked.some(a=>a.startsWith('POST')||a.startsWith('PUT')),'Opening a profile wrote rig configuration');
});
await test('Editor Done returns to classic finder without restarting the camera',async()=>{
  const done=document.querySelector<HTMLButtonElement>('[aria-label="Back to done"]');
  assert(done,'Editor back button missing');await act(async()=>done.click());await settle();
  assert(document.querySelector('[data-sky-view]'),'Did not return to finder');
  assert(location.hash==='#/classic/monitor','Done escaped classic');
  assert(cameraStarts===1,'Returning to map reopened the camera');
});
await test('Closing tools releases an active camera',async()=>{
  await click('AR camera');
  await click('Done');assert(closed,'Close callback missing');
  await act(async()=>root.unmount());
  assert(cameraStops===2,'Camera track survived closing tools');
});
await test('A late photosphere camera grant is released after the editor closes',async()=>{
  const {PhotosphereSweep}=await import('../../../next/hubs/sky/sheets/photosphere');
  let grant:((stream:unknown)=>void)|undefined;
  const original=w.navigator.mediaDevices.getUserMedia;
  w.navigator.mediaDevices.getUserMedia=()=>new Promise(resolve=>{grant=resolve;});
  const sweep=new PhotosphereSweep();
  const video=document.createElement('video');
  const pending=sweep.start(video,document.createElement('canvas'));
  sweep.stop();
  let stopped=0;
  grant!({getTracks:()=>[{stop:()=>stopped++}]});
  await pending;
  assert(stopped===1,'Late camera stream leaked');
  assert(!video.srcObject,'Late camera attached to a removed editor');
  w.navigator.mediaDevices.getUserMedia=original;
});
await test('Classic live dome draws and refreshes the active horizon without starting AR',async()=>{
  const {ClassicLiveSky}=await import('../../cloudmap/ClassicLiveSky');
  const liveRoot=createRoot(document.getElementById('root')!);
  const starts=cameraStarts;
  await act(async()=>liveRoot.render(createElement(ClassicLiveSky,{})));await settle();
  const horizon=()=>document.querySelector('[data-testid="wx-dome-horizon"]')?.innerHTML;
  const before=horizon();assert(before,'Saved horizon not drawn on live classic dome');
  activePoints[1]=[180,35];
  await act(async()=>useStore.setState({config:{...useStore.getState().config,safety:{horizon:[...activePoints]}}} as never));
  await settle();
  assert(horizon()!==before,'Live dome kept the old horizon after a saved edit');
  assert(cameraStarts===starts,'Live monitor opened the AR camera');
  await act(async()=>liveRoot.unmount());
});
const {ClassicAtlasSky}=await import('../ClassicAtlasSky');
const atlasRoot=createRoot(document.getElementById('root')!);
history.replaceState(null,'','#/classic/atlas');
const galaxy={id:'M31',label:'Andromeda',kind:'dso',type:'Galaxy',ra_hours:0.7,dec_deg:41} as import('../../../lib/skyRegion').SkyRow;
const nebula={...galaxy,id:'M42',label:'Orion',type:'Nebula'};
await act(async()=>atlasRoot.render(createElement(ClassicAtlasSky,{children:display=>createElement(Fragment,null,
  createElement('input',{'aria-label':'Framing draft',defaultValue:'M31 mosaic'}),
  createElement('span',{'data-testid':'imagery'},String(display.imagery)),
  createElement('div',{'data-testid':'survey-viewport'},display.controls),
  ...[galaxy,nebula].filter(r=>display.objects&&display.accepts(r)).map(r=>createElement('span',{key:r.id,'data-object':r.id},r.label)),
)})));
await settle();
const draft=document.querySelector<HTMLInputElement>('[aria-label="Framing draft"]')!;
draft.value='M31 adjusted mosaic';
const icon=async(label:string)=>{const b=document.querySelector<HTMLButtonElement>(`button[aria-label="${label}"]`)!;assert(b,`Missing ${label}`);await act(async()=>b.click());await settle();};
await test('One atlas defaults to survey with filtered objects and no camera request',()=>{
  assert(document.querySelector('[data-testid="survey-viewport"] [aria-label="Switch to AR camera"]'),'Survey has no aperture switch over the viewport');
  assert(document.querySelector('[data-testid="survey-viewport"] [aria-label="Object filters"]'),'Filters are not over the survey');
  assert(document.querySelector('[data-testid="survey-viewport"] [aria-label="Layers"]'),'Layers are not over the survey');
  assert(document.querySelector('[data-object="M31"]')&&document.querySelector('[data-object="M42"]'),'Survey objects missing');
  assert(!document.querySelector('[data-sky-camera]'),'Camera opened automatically');
  assert(!document.body.textContent?.includes('AR camera & sky map'),'Separate map mode remains');
});
await test('The filter icon controls survey objects without a second settings button',async()=>{
  await icon('Object filters');
  const checkbox=Array.from(document.querySelectorAll('label')).find(l=>l.textContent==='GALAXIES')?.querySelector('input');
  assert(checkbox,'Galaxy filter missing');await act(async()=>checkbox.click());await settle();
  assert(!document.querySelector('[data-object="M31"]')&&document.querySelector('[data-object="M42"]'),'Survey ignored the filter');
  assert(!document.body.textContent?.includes('Layers & object types'),'Duplicate settings button remains');
  await icon('Layers');
  const imagery=Array.from(document.querySelectorAll('label')).find(l=>l.textContent==='Survey imagery')?.querySelector('input');
  assert(imagery,'Survey layer missing');await act(async()=>imagery.click());await settle();
  assert(document.querySelector('[data-testid="imagery"]')?.textContent==='false','Imagery toggle ignored');
});
await test('Layers opens the horizon editor within Atlas',async()=>{
  await click('Edit horizon line');
  assert(document.querySelector('[aria-label="Atlas horizon editor"]'),'Embedded editor missing');
  assert(document.querySelector('[data-testid="horizon-strip"]'),'Horizon strip missing');
  assert(!document.querySelector('[role="dialog"]'),'Atlas opened a modal');
  assert(draft.parentElement?.hidden,'Framing remained visible beneath the editor');
});
await test('Atlas camera releases its stream when editing the horizon',async()=>{
  await click('Back to atlas');
  await icon('Switch to AR camera');
  const stops=cameraStops;
  await icon('Layers');
  await click('Edit horizon line');
  assert(document.querySelector('[data-testid="point-at-finder"]'),'Finder aim was lost');
  assert(cameraStops===stops+1,'Horizon editing left camera running');
  assert(location.hash==='#/classic/atlas','Finder escaped Atlas');
});
await test('Returning to Atlas framing releases AR and preserves framing controls',async()=>{
  await click('Back to atlas');
  await icon('Switch to AR camera');
  const stops=cameraStops;
  assert(document.querySelector('[data-sky-lens]')?.hasAttribute('hidden'),'Camera has duplicate filter icon');
  assert(document.querySelector('[data-sky-view] [aria-label="Show survey atlas"]'),'Constellation switch is not over the camera');
  await icon('Show survey atlas');
  assert(cameraStops===stops+1,'Atlas mode switch left camera running');
  assert(!document.querySelector('[data-sky-view]'),'Hidden finder stayed mounted');
  assert(document.querySelector('[aria-label="Framing draft"]')===draft,'Framing controls were remounted');
  assert(draft.value==='M31 adjusted mosaic' && !draft.parentElement?.hidden,'Framing draft was lost or hidden');
});
await test('Compass rose reports tracking on and off within the camera view',async()=>{
  await icon('Switch to AR camera');
  assert(document.querySelector('[data-sky-view] .cst-compass')?.getAttribute('aria-pressed')==='false','Compass is not initially off');
  await icon('Follow phone compass');
  assert(document.querySelector('.cst-compass')?.getAttribute('aria-pressed')==='true','Compass did not reflect enabled tracking');
  await icon('Stop compass tracking');
  assert(document.querySelector('.cst-compass')?.getAttribute('aria-pressed')==='false','Compass did not return to dim state');
  await icon('Show survey atlas');
});
await test('Compass-only survey follows the phone without camera access or changing framing',async()=>{
  const starts=cameraStarts;
  const before=useStore.getState().framing;
  await icon('Follow phone compass');
  assert(document.querySelector('[aria-label="Compass survey"]'),'Compass did not open a following survey');
  assert(cameraStarts===starts,'Compass-only survey requested a camera');
  assert(useStore.getState().framing===before,'Compass changed the saved framing');
  const event=new w.Event('deviceorientation');
  Object.assign(event,{alpha:100,beta:80,gamma:0});
  await act(async()=>w.dispatchEvent(event));await settle();
  assert(useStore.getState().framing===before,'Sensor update changed the saved framing');
  await icon('Switch to AR camera');
  assert(document.querySelector('.cst-compass')?.getAttribute('aria-pressed')==='true','Switching background lost compass tracking');
  await icon('Show survey atlas');
  assert(document.querySelector('[aria-label="Compass survey"]'),'Returning from AR lost compass survey');
  await icon('Stop compass tracking');
  assert(!document.querySelector('[aria-label="Compass survey"]'),'Stopping compass left sensor view open');
  assert(draft.value==='M31 adjusted mosaic'&&!draft.parentElement?.hidden,'Saved framing was not restored');
});
await act(async()=>atlasRoot.unmount());
console.log(`classicSkyToolsDom.test: ${passed}/${passed+failed} passed`);
failures.forEach(f=>console.error(f));
export const testResult={passed,failed,failures};
dom.window.close();
if(failed)process.exitCode=1;
