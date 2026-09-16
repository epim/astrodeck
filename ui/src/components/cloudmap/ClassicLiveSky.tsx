import { useState } from "react";
import { useWeather } from "../../store";
import { SkyDomePanel } from "./SkyDomePanel";
import { useSkyModel } from "../../next/hubs/sky/finder/model";
import { DomeOverlay, windSummary, type DrawnMarks } from "../../next/hubs/weather/dome/domeOverlay";

/** The classic monitor uses the same live horizon and paths as the AR finder. */
export function ClassicLiveSky({pointing}:{pointing?:{alt:number;az:number}|null}) {
  const model=useSkyModel(370,{initialMode:'map'});
  const weather=useWeather();
  const [drawn,setDrawn]=useState<DrawnMarks>({horizon:false,ghosts:false,tracks:0,states:[],aimed:null});
  const wind=windSummary(weather?.now);
  return <div data-testid="classic-live-sky">
    <SkyDomePanel chrome="bare" height={280} pointing={pointing} overlay={args=><>
      <DomeOverlay args={args} horizon={model.horizonPoints} wind={wind} tracks={model.dome.tracks} onDrawn={setDrawn}/>
      <svg width={args.geom.cssW} height={args.geom.cssH} style={{position:'absolute',inset:0}} aria-label="Telescope at the center of the horizon" role="img"><g transform={`translate(${args.geom.cx} ${args.geom.cy})`} stroke="var(--text-dim)" fill="none"><path d="m-8 0 13-8 4 6-13 8zM0 5v10m0-10-8 10M0 5l8 10"/></g></svg>
    </>}/>
    <p className="csd-footnote">{drawn.horizon?'Active obstruction line shown.':'No drawn obstruction profile at this site.'} {drawn.tracks>0 ? `${drawn.tracks} object paths to dawn.` : 'Object paths appear when site and night data are available.'}{drawn.ghosts?' Dashed cloud outlines show the model’s 30-minute drift.':''}</p>
    {drawn.tracks>0 && <p className="csd-footnote">{model.dome.tracks.map(t=>t.label).join(' · ')}</p>}
  </div>;
}
