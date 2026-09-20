import { useStore } from "../store";
import { SkyDomePanel, type DomeOverlayArgs } from "../components/cloudmap/SkyDomePanel";
import { DomeOverlay } from "../next/hubs/weather/dome/domeOverlay";
import { projectAltAz } from "../lib/domeProjection";
import { alignmentSky } from "./focusSky";
import type { SkyPosition } from "./setup";

function FieldOverlay({args, sky, expired}: {args: DomeOverlayArgs; sky: ReturnType<typeof alignmentSky>; expired: boolean}) {
  const g = args.geom;
  const project = (p: {alt:number;az:number}) => projectAltAz(p.alt,p.az,g.cx,g.cy,g.r,g.tiltDeg,g.yawDeg);
  const target = sky && project(sky.field);
  return <svg width={g.cssW} height={g.cssH} className="guided-focus-sky-overlay" role="img" aria-label={sky ? `Telescope at the center of the dome, with the alignment field${sky.arcs.length ? " and both possible measurement arcs" : ""}` : "Telescope at the center of the dome"}>
    <g transform={`translate(${g.cx} ${g.cy})`} stroke="var(--text)" fill="none" strokeWidth="1.8"><path d="m-9-2 15-8 4 7-15 8zM0 5v12m0-12-9 12M0 5l9 12"/></g>
    <g opacity={expired ? .38 : 1}>
      {sky?.arcs.map((arc, i) => <g key={i}>
        {arc.slice(1).map((p,j) => {
          const previous=arc[j]; if(p.alt<0||previous.alt<0)return null;
          const a=project(previous),b=project(p);
          return <path key={j} d={`M${a.x},${a.y}L${b.x},${b.y}`} fill="none" stroke="var(--focus-field)" strokeWidth="2" strokeDasharray="4 3" opacity={b.facing? .95:.5}/>;
        })}
        {[12,24].map(j=>{const p=arc[j];if(p.alt<0)return null;const q=project(p);return <circle key={j} cx={q.x} cy={q.y} r="3" fill="var(--focus-field)"/>;})}
      </g>)}
      {target && sky!.field.alt>=0 && <g>
        <path d={`M${g.cx},${g.cy}L${target.x},${target.y}`} stroke="var(--focus-field)" strokeOpacity=".25" strokeDasharray="2 5"/>
        <circle cx={target.x} cy={target.y} r="11" fill="var(--bg-raise)" fillOpacity=".8" stroke="var(--focus-field)" strokeWidth="2"/>
        <circle cx={target.x} cy={target.y} r="3" fill="var(--focus-field)"/>
      </g>}
    </g>
  </svg>;
}

export function GuidedFocusSky({field,arcDeg,now,expired,plannedSky}: {field:SkyPosition|null;arcDeg:number;now:number;expired:boolean;plannedSky?:NonNullable<ReturnType<typeof alignmentSky>>}) {
  const site=useStore(s=>s.site);
  const config=useStore(s=>s.config);
  const status=useStore(s=>s.status);
  const fresh=useStore(s=>s.wsPhase==="up"&&!s.telemetryStale);
  const horizon=(config?.safety?.horizon??site?.horizon_points??[]).map(([az,alt])=>({az,alt}));
  const liveSky=field&&site&&!site.is_default&&typeof site.latitude==="number"&&typeof site.longitude==="number" ? alignmentSky(field,arcDeg,site.latitude,site.longitude,now):null;
  const sky=liveSky??plannedSky??null;
  const mount=status?.mount;
  const pointing=fresh&&mount&&Number.isFinite(mount.alt)&&Number.isFinite(mount.az)?{alt:mount.alt,az:mount.az}:null;
  return <figure className="guided-focus-sky">
    <figcaption><strong>Your sky</strong><span>{site?.name||"Observing site"}</span></figcaption>
    <SkyDomePanel chrome="bare" compact height={270} showSessionTargets={false} pointing={pointing} overlay={args=><>
      <DomeOverlay args={args} horizon={horizon} wind={null} tracks={[]}/>
      <FieldOverlay args={args} sky={sky} expired={expired}/>
    </>}/>
    <div className="guided-focus-sky-key">
      {horizon.length>0&&<span><i className="horizon"/>Saved horizon</span>}
      {sky&&<span><i className="field"/>{expired?"Expired suggestion":"Alignment field"} · {sky.field.alt.toFixed(0)}° high</span>}
      {pointing&&<span><i className="pointing"/>Mount pointing</span>}
    </div>
    <p>{horizon.length ? "The shaded edge follows your saved obstruction line." : "No obstruction line is saved for this site."} {sky?.arcs.length ? "The dotted arcs show the two directions alignment may use. Each dot is a measurement position." : sky ? "The ring marks your alignment field." : "Find a field to see where focusing and alignment will happen."}</p>
    {!fresh&&<p>Mount position is hidden until the connection returns.</p>}
    {!liveSky&&plannedSky&&<p>Field positions are from the last sky check.</p>}
  </figure>;
}
