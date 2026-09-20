import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import { useStore } from "../store";
import { useCanControlMount } from "../lib/caps";
import { useGuidedSetup } from "./setup";

export function AlignmentFinish() {
  const polar = useStore(s=>s.polar) as ReturnType<typeof useStore.getState>["polar"] & {reading_ts?:number;stale_updates?:number;flags?:string[];phase?:string};
  const status = useStore(s=>s.status);
  const fresh = useStore(s=>s.wsPhase==="up" && !s.telemetryStale);
  const canMove=useCanControlMount();
  const complete=useGuidedSetup(s=>s.alignment);
  const savingChecks=useGuidedSetup(s=>s.savingChecks);
  const [lockedAt,setLockedAt]=useState<number|null>(null);
  const [terminalConfirmed,setTerminalConfirmed]=useState(false);
  const [busy,setBusy]=useState(false),[error,setError]=useState<string|null>(null);
  const finishing=useRef(false);
  const [now,setNow]=useState(Date.now());
  useEffect(()=>{const timer=setInterval(()=>setNow(Date.now()),1000);return()=>clearInterval(timer);},[]);
  useEffect(()=>{if(polar.state==="idle"||polar.phase==="measuring"){setLockedAt(null);setTerminalConfirmed(false);}},[polar.state,polar.phase]);
  const ended=polar.state==="done";
  const measured=!!polar.reading_ts&&Number.isFinite(polar.total_error)&&polar.total_error>=0&&polar.total_error<=2;
  const age=now-(polar.reading_ts??0)*1000;
  const recent=age>=-5000&&age< (ended?300000:30000);
  // A running session must produce another solve AFTER securing the locks.
  // An engine that auto-finishes can instead accept explicit confirmation
  // that the locks were already secure during its final measured frame.
  const locked=lockedAt!==null&&((polar.reading_ts??0)>lockedAt || (ended&&terminalConfirmed&&polar.reading_ts===lockedAt));
  const readingGood=fresh&&(polar.source!=="sim"||status?.mode==="sim")&&(polar.state==="running"||ended)&&polar.phase!=="measuring"&&measured&&recent&&locked&&!(polar.stale_updates??0)&&!(polar.flags?.length)&&!status?.mount?.slewing;
  const finish=async()=>{
    if(!readingGood||!canMove||finishing.current)return;
    const checked=polar.reading_ts;
    finishing.current=true;
    setBusy(true);setError(null);
    try {
      await api.post("/api/polar/stop");
      // HTTP and telemetry travel separately. Give the stopped state a short
      // window to arrive before deciding whether the confirmation is usable.
      const stopDeadline=Date.now()+2000;
      while(useStore.getState().polar.state==="running"&&Date.now()<stopDeadline)await new Promise(resolve=>setTimeout(resolve,50));
      const s=useStore.getState(),p=s.polar as typeof polar;
      // A final solve can land while Stop is in flight. Accept a newer good
      // measurement from the same stopped session rather than requiring an
      // exact timestamp match and stranding the user after a successful stop.
      const finalAge=Date.now()-(p.reading_ts??0)*1000;
      if(s.wsPhase!=="up"||s.telemetryStale||s.status?.mount?.slewing
        ||!s.principal?.caps.includes("control.mount")
        ||!["idle","done"].includes(p.state)
        ||p.source!==polar.source||p.phase==="measuring"
        ||!Number.isFinite(p.total_error)||p.total_error<0||p.total_error>2
        ||(p.reading_ts??0)<(checked??Infinity)||finalAge < -5000||finalAge >= (ended?300000:30000)
        ||p.flags?.length||p.stale_updates)throw new Error();
      if(!await useGuidedSetup.getState().complete("alignment"))throw new Error();
    }catch{setError("Alignment wasn't confirmed as finished. Check the mount and try again.");}finally{setBusy(false);finishing.current=false;}
  };
  if(complete)return <section className="guided-choice-card" role="status"><h2>{savingChecks?"Saving the alignment check…":"Alignment checked"}</h2><p>{savingChecks?"The session has stopped. Waiting for the controller to confirm the final check.":"The alignment session has stopped. Continue to choose a target for your first image."}</p></section>;
  if((polar.state!=="running"&&!ended)||polar.phase==="measuring"||!measured)return error?<section className="guided-choice-card" role="alert"><h2>Check alignment again</h2><p>{error} Start a new alignment session for a fresh measurement.</p></section>:null;
  return <section className="guided-choice-card"><h2>{ended?"Your alignment measurement is ready":"Check the final alignment"}</h2>
    <p>{ended?`The session finished at ${polar.total_error.toFixed(1)}′. Were the mount's locks already secure when it took that reading? If you moved anything or tightened a lock afterward, run alignment again to check the new position.`:"Secure the mount's locks, then wait for a fresh measurement of 2′ or less. Tightening a lock can shift the alignment."}</p>
    <label><input type="checkbox" checked={lockedAt!==null} onChange={e=>{setLockedAt(e.target.checked?(polar.reading_ts??0):null);setTerminalConfirmed(e.target.checked&&ended);}}/> {ended?"The locks were secure for this reading. Nothing has moved since.":"The locks are secure. Check the next measurement."}</label>
    <div className="guided-button-row"><button className="btn btn-accent" disabled={!readingGood||!canMove||busy} onClick={()=>void finish()}>{busy?"Finishing…":"Finish alignment"}</button></div>
    {!readingGood&&<p role="status">{!recent?"This reading is too old to confirm. Start alignment again for a fresh check.":!fresh?"Waiting for the connection to the rig.":polar.flags?.length||polar.stale_updates?"Resolve the measurement warning, then run alignment again.":lockedAt===null?"Confirm the locks before finishing.":"Waiting for a fresh reading within 2′, without measurement warnings."}</p>}
    <small>Two arcminutes is a starting target for Guided setup. Your imaging setup may need a tighter result.</small>
    {polar.source==="sim"&&<p>Practice mode: these alignment readings are simulated.</p>}
    {error&&<p role="alert">{error}</p>}
  </section>;
}
