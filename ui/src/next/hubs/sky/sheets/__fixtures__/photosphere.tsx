import {orientationBasis,cameraLens,unit,skyAngles,type V3} from '../photosphereGeometry';
import {orientationChanged,type OrientationAngles} from './orientationChange';
import {createRoot} from 'react-dom/client';
// Static, because a DYNAMIC css import is a value and `tsc -b` type-checks this
// file now that it lives under `src` - there is no ambient `*.css` module
// declaration in this project on purpose (see `next/env.d.ts`). A side-effect
// import needs no declaration, which is how every real entry point loads its
// stylesheets (`src/main.tsx`).
import '../../../../../index.css';
import '../../../../next.css';
import '../../../../../components/sky/classicSkyTools.css';
import '../photosphere.css';

// Camera and sensor fixture. Production React, capture, dome and editor code.
//
// Served by `ui/.probe/photosphere.html`, which is the only thing that loads
// it; it lives here, under `src`, because `.probe/` is gitignored and a
// fixture that exists only in one working tree is a fixture that is lost.
//
// Dispatch is change-driven, mirroring Chromium's own 0.1 degree threshold
// (see orientationChange.ts): a phone that has stopped moving emits nothing,
// exactly like the real sensor. Query knobs: ?delayMs= delays each dispatch
// after a change; ?latencyMs= ages the drawn video frame behind the current
// pose; ?scene=holdstill runs a scripted 500 ms approach to a target, then
// stays silent.

// The root element carries `classic-sky-tools` and nothing else. It used to
// also carry `nx-root`, which no stylesheet in this repository defines - the
// new UI's root class is `nx-app` (NextApp.tsx) - so it styled nothing and
// r7Css.test.ts flags it the moment this file is somewhere that test can see.
// Dropped rather than swapped: adopting the app shell would restyle the whole
// page, and what this fixture is for is the sheet.
//
// Everything below touches the DOM or the store, all via dynamic import so a
// plain node import of this file never runs any of it.
if(typeof document!=='undefined'){
const {HorizonSheet}=await import('../horizon');
const {useStore}=await import('../../../../../store');

const params=new URLSearchParams(location.search);
const delayMs=Number(params.get('delayMs'))||0, latencyMs=Number(params.get('latencyMs'))||0, scene=params.get('scene');

const camera=document.createElement('canvas');camera.width=320;camera.height=240;
const ctx=camera.getContext('2d')!;let az=0,alt=0,roll=0,lastSent:OrientationAngles|null=null;
let poseHistory:{at:number;az:number;alt:number;roll:number}[]=[];

function angles(a:number,h:number,r:number):OrientationAngles{
  const alpha=r?270-a:360-a,beta=r?90-r:90+h,gamma=r?90:0;
  return {alpha:((alpha%360)+360)%360,beta,gamma};
}
function draw(a:number,h:number,r:number){
 const {alpha,beta,gamma}=angles(a,h,r);
 const basis=orientationBasis(alpha,beta,gamma),lens=cameraLens(320,240),im=ctx.createImageData(320,240);
 for(let y=0;y<240;y++)for(let x=0;x<320;x++){
  const sx=((x+.5)/320*2-1)*lens.tanX,sy=(1-(y+.5)/240*2)*lens.tanY;
  const v=unit(basis.forward.map((n,i)=>n+basis.right[i]*sx+basis.up[i]*sy) as V3),p=skyAngles(v);
  const house=p.az>20&&p.az<85,tree=Math.abs(p.az-160)<16||Math.abs(p.az-250)<10;
  const horizon=house?55+(p.az<53?(p.az-20):85-p.az)*.5:tree?35+Math.sin(p.az*2)*8:7+3*Math.sin(p.az/13);
  const cloud=p.alt>15&&Math.sin(p.az/17+p.alt/8)>.8;
  let color=p.alt<horizon ? house ? [125+(Math.floor(p.az)%8)*3,103,77] : [25,51+Math.sin(p.az*1.1)*10,36] : cloud?[207,219,225]:[81+p.alt*.4,146+p.alt*.45,194+p.alt*.5];
  if(house&&p.alt<horizon&&p.alt>6&&Math.floor(p.az/5)%3===0&&Math.floor(p.alt/6)%3===1)color=[45,59,66];
  im.data.set([...color,255],(y*320+x)*4);
 }ctx.putImageData(im,0,0);
}
function send(a:number,h:number,r:number){
 const next=angles(a,h,r);
 if(!orientationChanged(lastSent,next))return;
 lastSent=next;
 // The event's time is when the PHONE MOVED, not when the browser got round to
 // delivering it. photosphere.ts derives the pose time from e.timeStamp, so
 // constructing the Event inside the delayed callback stamped it with the
 // delivery time and made ?delayMs= invisible to the only code it exists to
 // exercise - the knob tested nothing but itself.
 const at=performance.now();
 const fire=()=>{const ev=new Event('deviceorientationabsolute');Object.assign(ev,{...next,absolute:true});
  Object.defineProperty(ev,'timeStamp',{value:at});window.dispatchEvent(ev);};
 delayMs>0?setTimeout(fire,delayMs):fire();
}
function poseAt(t:number){let chosen={az,alt,roll};for(const p of poseHistory)if(p.at<=t)chosen=p;return chosen;}
function pose(a=az,h=alt,r=roll){
 az=a;alt=h;roll=r;
 if(latencyMs>0){
  const now=performance.now();
  poseHistory.push({at:now,az:a,alt:h,roll:r});
  poseHistory=poseHistory.filter(p=>now-p.at<=latencyMs+500);
  const shown=poseAt(now-latencyMs);draw(shown.az,shown.alt,shown.roll);
 } else draw(a,h,r);
 send(a,h,r);
}

Object.defineProperty(navigator,'mediaDevices',{value:{enumerateDevices:async()=>[{kind:'videoinput',deviceId:'fixture',label:'Synthetic landscape'}],getUserMedia:async()=>camera.captureStream(24)}});
window.fetch=async()=>new Response(JSON.stringify({site:{name:'Test meadow',latitude:1,longitude:2,horizon_points:[]}}),{status:200,headers:{'Content-Type':'application/json'}});
useStore.setState({principal:{role:'admin',caps:['config.safety','config.site_optics']},wsPhase:'up',equipConnected:true,status:{mode:'sim',connected:{},busy_lanes:[]},config:{safety:{horizon:[]}},loadConfig:async()=>{}} as any);

pose();
// A still phone sends nothing after its first reading; ?scene=holdstill
// models settling onto a tripod instead of waiting for a button press.
if(scene==='holdstill')[10,8,6,4,2,0].forEach((a,i)=>setTimeout(()=>pose(a,20,0),i*100));
async function sweep(){for(const h of [0,35,70])for(let a=0;a<360;a+=24){pose(a,h,0);await new Promise(r=>setTimeout(r,430));}pose(0,90,0);}
createRoot(document.getElementById('root')!).render(<><div style={{padding:10,background:'#223340',display:'flex',gap:8,flexWrap:'wrap',position:'sticky',top:0,zIndex:5}}><b>Camera fixture</b><button onClick={()=>pose(0,0,0)}>North</button><button onClick={()=>pose(45,30,0)}>Up toward house</button><button onClick={()=>pose(0,0,20)}>Roll phone</button><button onClick={()=>void sweep()}>Sweep scene</button></div><main className="classic-sky-tools" style={{maxWidth:780,margin:'auto',padding:12}}><HorizonSheet depth={0} params={{site:'current'}} guided/></main></>);
}
