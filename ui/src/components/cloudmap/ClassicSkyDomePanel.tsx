import { useEffect, useId, useRef, useState } from "react";
import { Panel } from "../ui";
import { ClassicLiveSky } from "./ClassicLiveSky";
import { ClassicSkyTools, type SkyTool } from "../sky/ClassicSkyTools";
import { altAzOf } from "../../lib/altaz";
import { trackDirections } from "../../lib/trackDirections";
import { projectAltAz } from "../../lib/domeProjection";

// An explicit design preview. These examples never enter the rig's store,
// weather model, horizon configuration, plan, or command APIs.
const START = Date.parse("2026-09-16T05:00:00Z") / 1000;
const OBJECTS = [
  { id: "M31", name: "Andromeda Galaxy", kind: "Galaxy · Andromeda", ra: .712, dec: 41.269, color: "#b8e5cf" },
  { id: "NGC 7000", name: "North America Nebula", kind: "Emission nebula · Cygnus", ra: 20.976, dec: 44.33, color: "#c5b7e8" },
  { id: "M27", name: "Dumbbell Nebula", kind: "Planetary nebula · Vulpecula", ra: 19.993, dec: 22.72, color: "#a4cdec" },
];
const timeLabel = (minutes: number) => `${String((22 + Math.floor(minutes / 60)) % 24).padStart(2, "0")}:${String(minutes % 60).padStart(2, "0")}`;
const path = (pts: {x:number; y:number}[]) => pts.map((p,i) => `${i ? "L" : "M"}${p.x.toFixed(2)},${p.y.toFixed(2)}`).join(" ");
const terrainAlt = (az: number) => {
  const a = ((az % 360) + 360) % 360;
  return 3 + 15 * Math.exp(-(((a - 235) / 24) ** 2)) + 10 * Math.exp(-(((a - 100) / 19) ** 2))
    + 3 * Math.abs(Math.sin(a * .31)) + (a > 300 && a < 330 ? 9 : 0);
};

function SkyIllustration() {
  const uid = useId().replace(/:/g, "");
  const [selected, setSelected] = useState(0);
  const [minutes, setMinutes] = useState(0);
  const [yaw, setYaw] = useState(0);
  const [layers, setLayers] = useState({ clouds:true, horizon:true, paths:true });
  const drag = useRef<{x:number; yaw:number; id:number} | null>(null);
  const previewRoot = useRef<HTMLDivElement>(null);
  useEffect(() => { previewRoot.current?.closest('section')?.scrollIntoView({block:'start'}); }, []);
  const p = (alt:number, az:number) => projectAltAz(alt, az, 450, 345, 300, 32, yaw);
  const position = (index:number, minute:number) => {
    const t = OBJECTS[index];
    return altAzOf(t.ra, t.dec, 45, -122, START + minute * 60);
  };
  const ring = (alt:number) => path(Array.from({length:121}, (_,i) => p(alt, i*3))) + "Z";
  const outline = "M150 345 A300 300 0 0 1 750 345 A300 158.976 0 0 1 150 345Z";
  const cloudShape = (az:number, alt:number, width:number, height:number) => path(Array.from({length:73}, (_,i) => {
    const t = i * Math.PI / 36;
    return p(alt + Math.sin(t) * height * (1 + .14 * Math.sin(t * 5)), az + Math.cos(t) * width);
  })) + "Z";
  const profile = (near:boolean) => <g className="csd-terrain">
    {Array.from({length:120}, (_,i) => {
      const az = i*3;
      if (p(0,az+1.5).facing !== near) return null;
      return <path key={i} d={path([p(0,az),p(terrainAlt(az),az),p(terrainAlt(az+3),az+3),p(0,az+3)])+"Z"} fill={near ? `url(#${uid}-land)` : "#263b3b"} opacity={near ? .96 : .55}/>;
    })}
    {Array.from({length:120}, (_,i) => {
      const az = i*3;
      if (p(0,az+1.5).facing !== near) return null;
      return <path key={i} d={path([p(terrainAlt(az)+3,az),p(terrainAlt(az+3)+3,az+3)])} fill="none" stroke="#d4b58b" strokeWidth="1.5" strokeDasharray="3 3" opacity={near ? .85 : .4}/>;
    })}
  </g>;
  const active = OBJECTS[selected];
  const where = position(selected,minutes);
  return <>
      <div className="csd-map" ref={previewRoot}>
        <svg viewBox="80 20 740 525" role="group" aria-label="Illustrative sky with clouds, obstruction horizon, and object paths" className="csd-dome"
          onPointerDown={e => { if ((e.target as Element).closest('[role="button"]')) return; drag.current={x:e.clientX,yaw,id:e.pointerId}; e.currentTarget.setPointerCapture(e.pointerId); }}
          onPointerMove={e => { if(drag.current?.id===e.pointerId) setYaw(drag.current.yaw+(e.clientX-drag.current.x)/e.currentTarget.getBoundingClientRect().width*360); }}
          onPointerUp={() => {drag.current=null;}} onPointerCancel={() => {drag.current=null;}}>
          <defs>
            <radialGradient id={`${uid}-sky`} cx="44%" cy="32%" r="78%"><stop stopColor="#244653"/><stop offset=".58" stopColor="#122634"/><stop offset="1" stopColor="#101b28"/></radialGradient>
            <linearGradient id={`${uid}-land`} gradientUnits="userSpaceOnUse" x1="0" y1="290" x2="0" y2="505"><stop stopColor="#34514a"/><stop offset="1" stopColor="#152b29"/></linearGradient>
            <radialGradient id={`${uid}-cloud`}><stop stopColor="#d2e0e5" stopOpacity=".62"/><stop offset=".6" stopColor="#a7c2d1" stopOpacity=".35"/><stop offset="1" stopColor="#91aaba" stopOpacity=".02"/></radialGradient>
            <filter id={`${uid}-soft`} x="-30%" y="-50%" width="160%" height="200%"><feGaussianBlur stdDeviation="7"/></filter>
            <clipPath id={`${uid}-clip`}><path d={outline}/></clipPath>
          </defs>
          <g clipPath={`url(#${uid}-clip)`}>
            <path d={outline} fill={`url(#${uid}-sky)`}/>
            {Array.from({length:120}, (_,i) => {const s=p(8+(i*37)%79,(i*137.508)%360);return <circle key={i} cx={s.x} cy={s.y} r={i%17===0 ? 1.5 : .75} fill="#d6e7ea" opacity={s.facing ? .25+(i%4)*.11 : .12}/>;})}
            {[0,15,30,45,60,75].map(alt => <path key={alt} d={ring(alt)} className="csd-grid"/>)}
            {Array.from({length:12},(_,i)=><path key={i} d={path(Array.from({length:46},(_,j)=>p(j*2,i*30)))} className="csd-grid"/>)}
            <ellipse cx="450" cy="345" rx="300" ry="158.976" fill={`url(#${uid}-land)`} opacity=".35"/>
            {layers.horizon && profile(false)}
            {layers.clouds && <g data-testid="preview-clouds">
              {[[252,34,43,13],[272,45,30,9],[88,18,32,8]].map(([az,alt,w,h],i)=><g key={i}><path d={cloudShape(az+minutes/12,alt,w,h)} fill={`url(#${uid}-cloud)`} filter={`url(#${uid}-soft)`}/><path d={cloudShape(az+minutes/12,alt,w*.86,h*.68)} fill={`url(#${uid}-cloud)`} opacity=".38"/></g>)}
            </g>}
            {layers.paths && OBJECTS.map((t,index) => {
              const samples = Array.from({length:49},(_,i)=>{const a=position(index,i*10);return {...p(Math.max(0,a.altDeg),a.azDeg),alt:a.altDeg};});
              const clocks:{x:number;y:number}[]=[];
              return <g key={t.id} data-testid="preview-object-path" fill="none" stroke={t.color} opacity={index===selected ? .9 : .48}>
                {samples.slice(1).map((pt,i)=> samples[i].alt<0 || pt.alt<0 ? null : <path key={i} d={path([samples[i],pt])} strokeWidth={index===selected ? 2 : 1.3} strokeDasharray={i*10<minutes ? "2 5" : "7 5"}/>)}
                {samples.slice(1).flatMap((pt,i)=>samples[i].alt<0||pt.alt<0||i%3!==0?[]:trackDirections([samples[i],pt],12).slice(0,1).map((a,j)=><path key={`dir-${i}-${j}`} d="M-4 -3L0 0L-4 3" transform={`translate(${a.x} ${a.y}) rotate(${a.angle})`} strokeWidth="1.5"/>))}
                {[0,60,120,180,240,300,360,420,480].map(min=>{const a=position(index,min);if(a.altDeg<0)return null;const v=p(a.altDeg,a.azDeg);const nearObject=OBJECTS.some((_,j)=>{const b=position(j,minutes);const dot=p(b.altDeg,b.azDeg);return Math.hypot(dot.x-v.x,dot.y-v.y)<65;});const show=index===selected&&!nearObject&&!clocks.some(c=>Math.hypot(c.x-v.x,c.y-v.y)<62);if(show)clocks.push(v);return <g key={min}><circle cx={v.x} cy={v.y} r="2.4" fill={t.color} stroke="none"/>{show && <g transform={`translate(${v.x} ${v.y-16})`}><path d="M0 8V14" stroke={t.color}/><rect x="-23" y="-8" width="46" height="16" rx="8" fill="#101a24" stroke={t.color} strokeOpacity=".35"/><text textAnchor="middle" y="4" className="csd-time-label csd-hour-label" fill={t.color} stroke="none">{timeLabel(min)}</text></g>}</g>;})}
              </g>;
            })}
            {layers.horizon && profile(true)}
          </g>
          <path d={outline} fill="none" stroke="#9dbdb8" strokeOpacity=".35"/>
          <path d={ring(0)} fill="none" stroke="#b3d6c3" strokeWidth="1.4" strokeOpacity=".45"/>
          {OBJECTS.map((t,index)=>{
            const a=position(index,minutes);if(a.altDeg<0)return null;
            const v=p(a.altDeg,a.azDeg), chosen=index===selected;
            const left=v.x>590;
            return <g key={t.id} transform={`translate(${v.x},${v.y})`} role="button" tabIndex={0} aria-label={`Select ${t.name}`} aria-pressed={chosen}
              onClick={()=>setSelected(index)} onKeyDown={e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();setSelected(index);}}} className="csd-object" style={{color:t.color}}>
              <circle r="24" fill="transparent"/><circle r={chosen ? 15 : 8} fill="#11232c" stroke="currentColor" strokeOpacity=".85"/><circle r="3" fill="currentColor"/>
              {chosen && <path d="M-22 0h8m28 0h8M0-22v8m0 28v8" stroke="currentColor"/>}
              <path d={`M${left ? -22 : 22} 0h${left ? -12 : 12}`} stroke="currentColor" opacity=".5"/>
              <text x={left ? -40 : 40} y="-2" textAnchor={left ? 'end':'start'} className="csd-object-label">{t.id}</text>
              <text x={left ? -40 : 40} y="15" textAnchor={left ? 'end':'start'} className="csd-object-alt">{Math.round(a.altDeg)}° up{layers.horizon && a.altDeg<terrainAlt(a.azDeg)+3 ? ' · obstructed' : ''}</text>
            </g>;
          })}
          {([['N',0],['E',90],['S',180],['W',270]] as const).map(([name,az])=>{const v=p(0,az);const dx=v.x-450,dy=v.y-345,len=Math.hypot(dx,dy)||1;return <text key={name} x={v.x+dx/len*21} y={v.y+dy/len*21+4} textAnchor="middle" className="csd-cardinal">{name}</text>;})}
          <g transform="translate(450 345)" data-testid="preview-telescope" stroke="#c5d9cf" fill="none" opacity=".85"><path d="m-12 0 20-12 6 9-20 12zM1 8v17m0-17-13 17M1 8l13 17" strokeWidth="1.5"/><text y="44" textAnchor="middle" stroke="none" fill="#aac4b9" className="csd-time-label">YOUR TELESCOPE</text></g>
          {layers.clouds && <text x={p(52,252+minutes/12).x-15} y={p(52,252+minutes/12).y-12} textAnchor="middle" className="csd-annotation">Cloud bank</text>}
          {layers.horizon && <g><path d="M646 458h45l18-22" stroke="#d4b58b" opacity=".6" fill="none"/><text x="640" y="462" textAnchor="end" className="csd-annotation" fill="#d4b58b">Trees, roof & clearance</text></g>}
        </svg>
        <div className="csd-map-footer"><span>Drag to turn the sky</span><button onClick={()=>setYaw(0)}>Reset orientation</button></div>
      </div>
    <p className="csd-selection">{active.id} · {active.name} <span>{Math.abs(Math.round(where.altDeg))}° {where.altDeg>=0 ? 'up' : 'below horizon'} · {timeLabel(minutes)}</span></p>
    <details className="csd-controls">
      <summary>Sky layers & time</summary>
      <div className="csd-layers" aria-label="Sky layers">{([['clouds','Clouds'],['horizon','Obstructions'],['paths','Object paths']] as const).map(([key,label]) => <button key={key} aria-pressed={layers[key]} onClick={() => setLayers({...layers,[key]:!layers[key]})}><span aria-hidden="true">{layers[key] ? '✓' : '+'}</span>{label}</button>)}</div>
      <div className="csd-targets" aria-label="Example objects">{OBJECTS.map((t,i)=><button key={t.id} aria-label={`Follow ${t.name}`} aria-pressed={selected===i} onClick={()=>setSelected(i)}>{t.id}</button>)}</div>
      <div className="csd-timeline"><div><label htmlFor={`${uid}-time`}>Explore the night</label><output htmlFor={`${uid}-time`}>{timeLabel(minutes)}</output></div><input id={`${uid}-time`} type="range" min="0" max="360" step="10" value={minutes} onChange={e=>setMinutes(Number(e.target.value))}/><div className="csd-time-ticks"><span>22:00</span><span>Midnight</span><span>02:00</span><span>04:00</span></div></div>
      <p className="csd-footnote">Example site: 45° N, 122° W · September 15–16. Paths use these coordinates; clouds and obstructions are illustrations.</p>
    </details>
    <p className="csd-footnote">Sample sky · illustrated clouds and obstruction line.</p>
  </>;
}

export function ClassicSkyDomePanel({pointing}:{pointing?:{alt:number;az:number}|null}) {
  const [preview,setPreview] = useState(()=>typeof location!=='undefined' && new URLSearchParams(location.hash.split('?')[1]).get('sky')==='preview');
  const [tool,setTool]=useState<SkyTool|null>(null);
  const [revision,setRevision]=useState(0);
  useEffect(() => {
    const onHash = () => setPreview(new URLSearchParams(location.hash.split('?')[1]).get('sky')==='preview');
    window.addEventListener('hashchange',onHash);
    return () => window.removeEventListener('hashchange',onHash);
  },[]);
  const chooseMode = (next:boolean) => {
    setPreview(next);
    history.replaceState(null,'',`#/classic/monitor${next ? '?sky=preview' : ''}`);
  };
  return <Panel title="Sky dome" className="col-span-full sm:col-span-2 lg:col-span-6 csd-panel" right={<div className="csd-mode" aria-label="Sky dome data source"><button aria-pressed={!preview} onClick={()=>chooseMode(false)}>Live readings</button><button aria-pressed={preview} onClick={()=>chooseMode(true)}>Design preview</button></div>}>
    {preview ? <SkyIllustration/> : <ClassicLiveSky key={revision} pointing={pointing}/>}
    <div className="csd-tools"><button onClick={()=>{window.location.hash='/classic/atlas';}}>Sky atlas</button><button onClick={()=>setTool('horizon')}>Edit horizon</button></div>
    {tool && <ClassicSkyTools initialTool={tool} onClose={()=>{setTool(null);setRevision(r=>r+1);}}/>}
  </Panel>;
}
