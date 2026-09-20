import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import { useStore } from "../store";
import { useCanControlMount } from "../lib/caps";
import { Icon } from "../components/icons";
import { useGuidedSetup, stepBlocker, type SkyPosition } from "./setup";
import { motionBlocker, slewAndWait } from "./motion";
import { GuidedFocusSky } from "./GuidedFocusSky";
import type { alignmentSky } from "./focusSky";

interface FieldPlan {field: (SkyPosition & {alt:number;az:number;clearance_deg:number}) | null; expires_at:number; config_version:number; arc_deg:number; reason:string|null; simulation?:boolean;sky?:NonNullable<ReturnType<typeof alignmentSky>>;}
export function GuidedFocusPrep() {
  const [plan,setPlan] = useState<FieldPlan|null>(null);
  const [busy,setBusy] = useState<string|null>(null); const [error,setError] = useState<string|null>(null);
  const [clear,setClear] = useState(false);
  const field = useGuidedSetup(s=>s.field);
  const canMove=useCanControlMount();
  const controller=useRef<AbortController|null>(null);
  const inFlight=useRef(false), mounted=useRef(true), generation=useRef(0);
  const version=useStore(s=>s.config?.version);
  useStore(s=>s.status); useStore(s=>s.wsPhase); useStore(s=>s.telemetryStale);
  useStore(s=>s.focus); useStore(s=>s.polar); useStore(s=>s.sequence); useStore(s=>s.filterOffsetsLearn);
  const [now,setNow]=useState(()=>Date.now()/1000);
  useEffect(()=>{mounted.current=true;const clock=setInterval(()=>setNow(Date.now()/1000),1000);return()=>{mounted.current=false;generation.current++;controller.current?.abort();clearInterval(clock);};},[]);
  const expired=!!plan && (now>=plan.expires_at || version!==plan.config_version);
  const blocker=stepBlocker("focus")??motionBlocker();
  const find = async()=>{
    if(inFlight.current)return;
    inFlight.current=true;const request=++generation.current;
    setBusy("finding");setError(null);setClear(false);setPlan(null);
    try{const next=await api.get<FieldPlan>("/api/guided/polar-field");if(mounted.current&&request===generation.current)setPlan(next);}
    catch{if(mounted.current)setError("We couldn't check the sky. Check the connection and try again.");}
    finally{inFlight.current=false;if(mounted.current)setBusy(null);}
  };
  const point=async()=>{
    if (!plan?.field || !clear || !canMove || inFlight.current) return;
    const blocked=stepBlocker("focus")??motionBlocker();if(blocked){setError(blocked);return;}
    if(Date.now()/1000>=plan.expires_at || useStore.getState().config?.version!==plan.config_version){setError("The sky check has expired or your settings changed. Find a field again.");return;}
    inFlight.current=true;
    setBusy("moving");setError(null);useGuidedSetup.getState().setField(null);
    const abort=new AbortController();controller.current=abort;
    try{
      await slewAndWait(plan.field,false,abort.signal);
      if(!mounted.current||abort.signal.aborted)return;
      if(useStore.getState().config?.version!==plan.config_version){setError("Settings changed during the move. Check the sky again before focusing.");return;}
      useGuidedSetup.getState().setField(plan.field);
    }catch(e){if(mounted.current)setError(e instanceof Error?e.message:"The move failed.");}
    finally{inFlight.current=false;if(mounted.current)setBusy(null);}
  };
  return <section className="guided-choice-card guided-focus-prep">
    <h2>{field ? "The telescope is at the alignment field" : "First, find a place to focus"}</h2>
    <p>We'll use the same patch of sky for focusing and three-point polar alignment (TPPA). AstroDeck checks the drawn horizon along both possible measurement arcs.</p>
    <div className="guided-focus-prep-layout">
    <GuidedFocusSky field={field??plan?.field??null} arcDeg={plan?.arc_deg??0} now={now} expired={!field&&expired} plannedSky={plan?.sky}/>
    <div className="guided-focus-prep-actions">
    {plan?.simulation && <p role="status">You're using simulated equipment, so you can practice during daylight. Horizon and Sun-clearance checks still apply.</p>}
    {field && <button className="btn" disabled={!!busy||!!blocker} onClick={()=>{useGuidedSetup.getState().setField(null);setPlan(null);setClear(false);}}>Choose a new alignment field</button>}
    {!field && <>
      <button className="btn btn-accent" disabled={!!busy} onClick={()=>void find()}><Icon name="atlas" size={22}/>{busy==="finding"?"Checking the sky…":plan?.field?"Check the sky again":"Find an alignment field"}</button>
      {plan?.field && <div className="guided-field-plan"><p>Suggested field: {plan.field.alt.toFixed(0)}° above the horizon, compass bearing {plan.field.az.toFixed(0)}°. The {plan.arc_deg.toFixed(0)}° measurement arc clears the saved horizon.</p>
        {expired&&!busy&&<p role="status">This sky check has expired or your settings changed. Check the sky again before moving.</p>}
        <label><input type="checkbox" checked={clear} disabled={!!busy||expired} onChange={e=>setClear(e.target.checked)}/> I've roughly positioned the equatorial mount and checked cables and clearance for this move.</label>
        <button className="btn btn-accent" disabled={!!busy||!clear||!canMove||!!blocker||expired} onClick={()=>void point()}>{busy==="moving"?"Moving to the field…":"Point telescope here"}</button>
      </div>}
      {plan?.reason && <p role="status">{plan.reason}</p>}
    </>}
    {error && <p role="alert" className="guided-warning">{error}</p>}
    {!busy&&blocker&&<p role="status">{blocker}</p>}
    <button className="btn" disabled={!canMove} onClick={()=>{controller.current?.abort();void api.post("/api/mount/stop",{}).catch(()=>setError("Stop wasn't confirmed. Check the mount."));}}>Stop mount</button>
    </div></div>
  </section>;
}
