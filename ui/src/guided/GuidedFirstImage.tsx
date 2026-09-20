import { useEffect, useRef, useState } from "react";
import { api, ApiError } from "../api";
import { u } from "../lib/base";
import { useCanControlCapture, useCanControlMount } from "../lib/caps";
import { useStore } from "../store";
import type { CatalogEntry, PreviewInfo } from "../types";
import { Icon } from "../components/icons";
import { atPosition, stepBlocker, useStepBlocker } from "./setup";
import { motionBlocker, slewAndWait } from "./motion";
import { useExperience } from "./experience";

/** Check current server ownership, including work started by another client. */
export function firstImageOperationBlocker(s=useStore.getState()): string|null {
  return motionBlocker(s);
}
const RECEIPT_KEY="astrodeck.guided.first-image.v1";
interface CaptureReceipt {requestId:string;target:CatalogEntry;requestedAt:number;exposure:number;}
export function readCaptureReceipt(storage:Pick<Storage,"getItem">,now=Date.now()):CaptureReceipt|null {
  try {
    const r=JSON.parse(storage.getItem(RECEIPT_KEY)??"null");
    if(typeof r?.requestId!=="string"||!r.requestId.startsWith("guided_")||!Number.isFinite(r.requestedAt)||now<r.requestedAt||now-r.requestedAt>12*3600000
      ||!Number.isFinite(r.exposure)||r.exposure<.1||r.exposure>30||typeof r.target?.id!=="string"
      ||!Number.isFinite(r.target.ra_hours)||!Number.isFinite(r.target.dec_deg))return null;
    return r;
  }catch{return null;}
}
function saveCaptureReceipt(receipt:CaptureReceipt|null){try{if(receipt)window.localStorage.setItem(RECEIPT_KEY,JSON.stringify(receipt));else window.localStorage.removeItem(RECEIPT_KEY);}catch{/* Recovery is optional when browser storage is unavailable. */}}

export default function GuidedFirstImage() {
  const returning=useExperience(s=>s.journey==="returning");
  const [picks,setPicks]=useState<CatalogEntry[]>([]);
  const [target,setTarget]=useState<CatalogEntry|null>(null);
  const [arrived,setArrived]=useState(false);
  const [busy,setBusy]=useState<string|null>(null);
  const [error,setError]=useState<string|null>(null);
  const [reason,setReason]=useState<string|null>(null);
  const [simulation,setSimulation]=useState(false);
  const [frame,setFrame]=useState<PreviewInfo|null>(null);
  const [exposure,setExposure]=useState("5");
  const [receipt,setReceipt]=useState<CaptureReceipt|null>(()=>{try{return readCaptureReceipt(window.localStorage);}catch{return null;}});
  const [receiptDeadlinePassed,setReceiptDeadlinePassed]=useState(false);
  const controller=useRef<AbortController|null>(null);
  const inFlight=useRef(false);
  const canMove=useCanControlMount(),canCapture=useCanControlCapture();
  const blocked=useStepBlocker("image");
  const status=useStore(s=>s.status);
  const previews=useStore(s=>s.previews);
  const live=useStore(s=>s.wsPhase==="up"&&!s.telemetryStale);
  useStore(s=>s.sequence);useStore(s=>s.focus);useStore(s=>s.polar);useStore(s=>s.filterOffsetsLearn);
  const operationBlock=firstImageOperationBlocker();
  const load=async()=>{if(inFlight.current)return;inFlight.current=true;setBusy("finding");setError(null);setTarget(null);setArrived(false);try{const result=await api.get<{picks:CatalogEntry[];reason:string|null;simulation?:boolean}>("/api/guided/first-targets");setPicks(result.picks);setReason(result.reason);setSimulation(result.simulation===true);}catch{setPicks([]);setError("We couldn't load targets. Check the connection and try again.");}finally{setBusy(null);inFlight.current=false;}};
  useEffect(()=>{if(!receipt)void load();return()=>controller.current?.abort();},[]);
  useEffect(()=>{
    if(!receipt)return;
    setTarget(receipt.target);
    const deadline=receipt.requestedAt+(receipt.exposure+90)*1000;
    setReceiptDeadlinePassed(Date.now()>=deadline);
    const timer=setTimeout(()=>setReceiptDeadlinePassed(true),Math.max(0,deadline-Date.now()));
    return()=>clearTimeout(timer);
  },[receipt]);
  useEffect(()=>{
    if(!receipt||!live)return;
    const saved=previews.find(p=>p.capture_request_id===receipt.requestId&&p.capture_saved===true);
    if(saved)setFrame(saved);
  },[receipt,previews,live]);
  const point=async()=>{
    if(!target||busy||blocked||!canMove||inFlight.current)return;
    const blocker=stepBlocker("image")??firstImageOperationBlocker();
    if(blocker){setError(blocker);return;}
    inFlight.current=true;setBusy("moving");setError(null);setArrived(false);setFrame(null);
    const abort=new AbortController();controller.current=abort;
    try{await slewAndWait(target,true,abort.signal);setArrived(true);}catch(e){setError(e instanceof Error?e.message:"Pointing wasn't confirmed.");}finally{setBusy(null);inFlight.current=false;}
  };
  const capture=async()=>{
    const seconds=Number(exposure);
    if(!target||!arrived||blocked||!canCapture||busy||inFlight.current||!Number.isFinite(seconds)||seconds<.1||seconds>30)return;
    const blocker=stepBlocker("image")??firstImageOperationBlocker();
    if(blocker){setError(blocker);return;}
    if(!atPosition(useStore.getState().status?.mount,target)){setArrived(false);setError("The telescope moved. Point at the target again before taking an image.");return;}
    inFlight.current=true;setBusy("capturing");setError(null);setFrame(null);
    const abort=new AbortController();controller.current=abort;
    const before=Math.max(-1, ...useStore.getState().previews.map(p=>p.id));
    const requestId = `guided_${Date.now()}_${Math.random().toString(36).slice(2)}`;
    const settings=useStore.getState().frameSettings.capture;
    const pendingReceipt={requestId,target,requestedAt:Date.now(),exposure:seconds};
    saveCaptureReceipt(pendingReceipt);setReceipt(pendingReceipt);
    try{
      await api.post("/api/capture",{request_id:requestId,exposure_s:seconds,gain:settings.gain,offset:settings.offset,binning:settings.binning,save:true,target:target.name||target.id,frame_type:"Light"});
      const deadline=Date.now()+(seconds+90)*1000;
      while(!abort.signal.aborted && Date.now()<deadline){
        await new Promise(resolve=>setTimeout(resolve,500));
        if(abort.signal.aborted)break;
        const state=useStore.getState();
        if(state.wsPhase!=="up"||state.telemetryStale)throw new Error("The connection was lost. Check the camera before trying again.");
        const saved=state.previews.find(p=>p.id>before && p.capture_request_id===requestId && p.capture_saved===true);
        if(saved){setFrame(saved);return;}
      }
      throw new Error(abort.signal.aborted?"Capture stopped. Check the camera before trying again.":"No saved image arrived. Check the camera and storage, then try again.");
    }catch(e){
      // A definite refusal created no exposure. A dropped connection is
      // different: retain its receipt so a later saved frame can reconcile it.
      if(e instanceof ApiError&&e.status>=400&&e.status<500&&e.status!==408){setReceipt(null);saveCaptureReceipt(null);}
      setError(e instanceof Error?e.message:"The image wasn't confirmed.");
    }finally{setBusy(null);inFlight.current=false;}
  };
  const atTarget=!!target&&atPosition(status?.mount,target)&&!status?.mount?.slewing;
  return <div className="guided-bespoke">
    {frame ? <section className="guided-choice-card guided-first-success" role="status">
      <Icon name="check" size={32}/><h2>{status?.mode==="sim"?"Your practice image is saved.":returning?"Tonight's image is saved.":"Congratulations — you took your first image!"}</h2>
      <p>{target?.name || target?.id} · {frame.exposure_s} seconds. {status?.mode==="sim"?"This is a simulated frame.":"Your image is saved on the telescope computer."}</p>
      <img src={u(`/api/preview/${frame.id}`)} alt={`Saved first exposure of ${target?.name||target?.id}`}/>
      <p>A single short exposure is a starting point. Next time, try several longer exposures and combine them to reveal fainter detail.</p>
      <button className="btn" onClick={()=>{setFrame(null);setReceipt(null);saveCaptureReceipt(null);void load();}}>Take another image</button>
    </section> : receipt && !busy ? <section className="guided-choice-card" role="status"><h2>Checking your previous exposure</h2><p>Looking for the saved image of {receipt.target.name||receipt.target.id}. Reopening this page won't take another exposure.</p><p>{!live?"Reconnect to the telescope to check the result.":operationBlock??(receiptDeadlinePassed?"The saved frame hasn't arrived. Check the camera and storage before trying again.":"Waiting for the saved image to arrive.")}</p><button className="btn" disabled={!live||!!operationBlock||!receiptDeadlinePassed} onClick={()=>{setReceipt(null);saveCaptureReceipt(null);setError(null);void load();}}>Choose a target and try again</button></section> : <>
      <section className="guided-choice-card"><h2>Start with something easy to find</h2><p>These brighter targets are above your saved horizon now and thirty minutes from now. Choose one, then let AstroDeck point and check its position from a camera image.</p>
        {simulation&&<p role="status">You're using simulated equipment, so you can practice during daylight. Horizon and Sun-clearance checks still apply.</p>}
        <div className="guided-target-list">{picks.map(p=><button key={p.id} className="guided-target-card" aria-pressed={target?.id===p.id} disabled={!!busy} onClick={()=>{setTarget(p);setArrived(false);setError(null);}}><Icon name="atlas" size={24}/><span><strong>{p.name||p.id}</strong><small>{p.id} · {p.type} · {p.alt?.toFixed(0)}° above the horizon</small></span></button>)}</div>
        {reason&&<p role="status">{reason}</p>}
        <button className="btn" disabled={!!busy} onClick={()=>void load()}>{busy==="finding"?"Finding targets…":"Refresh targets"}</button>
      </section>
      {target&&<section className="guided-choice-card"><h2>{arrived&&atTarget?"Ready for a first exposure":`Point at ${target.name||target.id}`}</h2>
        {!arrived||!atTarget?<><p>Check that cables can move freely and the telescope has room to turn. This moves the mount and takes short images to center the target.</p><button className="btn btn-accent" disabled={!!busy||!!blocked||!!operationBlock||!canMove} onClick={()=>void point()}>{busy==="moving"?"Pointing and checking…":"Point telescope at this target"}</button></>:<>
          <label className="guided-field">Exposure in seconds<input type="number" min=".1" max="30" step=".1" value={exposure} disabled={!!busy} onChange={e=>setExposure(e.target.value)}/></label>
          <p>Try 5 seconds first. This keeps the first image quick; faint detail may need longer exposures later. Your camera's current gain and binning are retained.</p>
          <button className="btn btn-accent" disabled={!!busy||!!blocked||!!operationBlock||!canCapture||!exposure.trim()||!Number.isFinite(Number(exposure))||Number(exposure)<.1||Number(exposure)>30} onClick={()=>void capture()}><Icon name="capture" size={22}/>{busy==="capturing"?"Taking and saving your image…":returning?"Take tonight's first image":"Take my first image"}</button>
        </>}
      </section>}
    </>}
    {error&&<p role="alert" className="guided-warning">{error}</p>}
    {blocked&&<p role="status">{blocked}</p>}
    {(!canMove||!canCapture)&&<p role="status">Controlling the telescope and camera requires operator or admin access.</p>}
    {!busy&&operationBlock&&<p role="status">{operationBlock}</p>}
    {!busy&&receipt&&!frame&&status?.busy_lanes?.includes("capture")&&<button className="btn" disabled={!canCapture} onClick={()=>void api.post("/api/capture/stop").catch(()=>setError("Stop wasn't confirmed. Check the camera."))}>Stop capture</button>}
    {busy==="moving"&&<button className="btn" disabled={!canMove} onClick={()=>{controller.current?.abort();void api.post("/api/mount/stop").catch(()=>setError("Stop wasn't confirmed. Check the mount."));}}>Stop mount</button>}
    {busy==="capturing"&&<button className="btn" disabled={!canCapture} onClick={()=>{controller.current?.abort();void api.post("/api/capture/stop").catch(()=>setError("Stop wasn't confirmed. Check the camera."));}}>Stop capture</button>}
  </div>;
}
