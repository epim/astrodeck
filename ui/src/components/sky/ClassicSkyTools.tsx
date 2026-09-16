import { useEffect, useRef, useState, type CSSProperties } from "react";
import { Overlay } from "../Overlay";
import { useStore } from "../../store";
import { useCapability } from "../../lib/caps";
import { applyLocation, listLocations } from "../../api/site";
import { useLock } from "../../next/lib/gateHook";
import type { SavedLocation } from "../../types";
import { HorizonSheet } from "../../next/hubs/sky/sheets/horizon";
import { SkyView } from "../../next/hubs/sky/finder/SkyView";
import { useSkyModel } from "../../next/hubs/sky/finder/model";
import { KIND_LABEL, SKY_KINDS } from "../../next/hubs/sky/finder/targets";

export type SkyTool = "finder" | "horizon";

// Classic owns the navigation and modal lifetime. The proven finder, sensor,
// horizon persistence and skyline tracing implementations are shared with next.
// Do not remove those modules when retiring the next shell.
export function ClassicSkyTools({ initialTool, onClose, embedded = false, activeTool, onToolChange, initialAim }: {
  initialTool: SkyTool;
  onClose: () => void;
  embedded?: boolean;
  activeTool?: SkyTool;
  onToolChange?: (tool: SkyTool) => void;
  initialAim?: { az: number; alt: number };
}) {
  const [localTool, setLocalTool] = useState(initialTool);
  const tool = activeTool ?? localTool;
  const setTool = (next: SkyTool) => { setLocalTool(next); onToolChange?.(next); };
  const [siteId, setSiteId] = useState("current");
  const [locations, setLocations] = useState<SavedLocation[]>([]);
  const [locationError, setLocationError] = useState<string | null>(null);
  const [aim, setAim] = useState<{az:number;alt:number} | null>(initialAim ?? null);
  const [editorBusy,setEditorBusy]=useState(false);
  const [applying,setApplying]=useState(false);
  const opticsLock=useLock({cap:'config.site_optics'});
  const safetyLock=useLock({cap:'config.safety'});
  const loadConfig=useStore(s=>s.loadConfig);
  const toast=useStore(s=>s.enqueueToast);
  const canLocations = useCapability("config.site_optics");
  useEffect(() => {
    if (!canLocations || tool !== "horizon") return;
    let live = true;
    void listLocations().then(rows => { if(live) {setLocations(rows);setLocationError(null);} })
      .catch(() => { if(live) setLocationError("Saved locations could not be loaded. The active horizon is still available."); });
    return () => { live=false; };
  }, [canLocations,tool]);
  const useLocation=async()=>{
    if(editorBusy||applying)return;
    if(opticsLock.lockedReason){opticsLock.onExplain(opticsLock.lockedReason);return;}
    setApplying(true);
    try {
      // Read after the editor's latest autosave, not the dropdown's old copy.
      const fresh=(await listLocations()).find(l=>l.id===siteId);
      if(!fresh)throw new Error('That saved location is no longer available.');
      if(fresh.horizon_points?.length && safetyLock.lockedReason){safetyLock.onExplain(safetyLock.lockedReason);return;}
      await applyLocation(fresh.id);
      await loadConfig();
      setSiteId('current');
      toast({level:'success',title:`Using ${fresh.name} and its horizon.`});
    } catch(e){toast({level:'error',title:e instanceof Error?e.message:'Could not apply the location.'});}
    finally {setApplying(false);}
  };
  const content = <>
    {!embedded && <nav className="cst-tabs" aria-label="Sky tools"><button className="btn" aria-pressed={tool==='finder'} onClick={()=>setTool('finder')}>AR camera & sky map</button><button className="btn" aria-pressed={tool==='horizon'} onClick={()=>setTool('horizon')}>Horizon line</button></nav>}
    {tool==='finder' ? <ClassicFinder onHorizon={next=>{setAim(next);setTool('horizon');}}/> : <>
      <div className="cst-profile"><label>Horizon profile<select aria-label="Horizon profile" value={siteId} disabled={editorBusy||applying} onChange={e=>setSiteId(e.target.value)}><option value="current">Active site · live obstruction line</option>{locations.map(l=><option key={l.id} value={l.id}>{l.name} · saved profile</option>)}</select></label><p>{siteId==='current' ? 'Edits update the active obstruction line used by the rig.' : 'Edits save to this profile. Use it below to change the rig’s active site and obstruction line.'}</p>{siteId!=='current' && <button className="btn" disabled={editorBusy||applying} onClick={()=>void useLocation()}>{applying?'Applying…':'Use this location and horizon'}</button>}{locationError && <p role="status">{locationError}</p>}</div>
      <HorizonSheet key={siteId} depth={0} params={{site:siteId,...(aim ? {az:String(aim.az),alt:String(aim.alt)} : {})}} onClose={()=>setTool('finder')} onBusyChange={setEditorBusy}/>
    </>}
  </>;
  if (embedded) return <section className="panel classic-sky-tools cst-embedded" aria-label={tool === 'finder' ? 'Atlas AR camera and sky map' : 'Atlas horizon editor'}>{content}</section>;
  return <Overlay open label="Sky tools" onClose={onClose} surfaceClassName="classic-sky-tools"
    bodyClassName="p-4"
    surfaceStyle={{"--ov-w":"940px","--ov-max-w":"96vw","--ov-max-h":"92dvh"} as CSSProperties}
    head={<div className="cst-head"><h2>Sky tools</h2><button className="btn" onClick={onClose}>Done</button></div>}>
    {content}
  </Overlay>;
}

function ClassicFinder({onHorizon}:{onHorizon:(aim:{az:number;alt:number})=>void}) {
  const host = useRef<HTMLDivElement>(null);
  const [width,setWidth] = useState(370);
  const [settings,setSettings] = useState(false);
  const model = useSkyModel(width,{initialMode:'map'});
  const toast = useStore(s=>s.enqueueToast);
  useEffect(()=>{
    const el=host.current;
    if(!el) return;
    const size=()=>setWidth(Math.max(220,Math.min(540,el.clientWidth)));
    size();
    const ro=new ResizeObserver(size);ro.observe(el);
    return ()=>ro.disconnect();
  },[]);
  const camera=()=>{
    if(model.mode==='cam'){model.setMode('map');return;}
    if(!model.secureContext || !model.cameraSupported){toast({level:'warning',title:'AR camera needs HTTPS (or localhost) and a device camera. Sky map still works.'});return;}
    model.setMode('cam');
  };
  const gyro=()=>{
    if(!model.secureContext || !model.gyroSupported){toast({level:'warning',title:'Compass tracking needs a supported orientation sensor and a secure connection. Drag the map to look around.'});return;}
    model.toggleGyro();
  };
  return <div className="cst-finder" data-testid="classic-sky-finder">
    <div className="cst-finder-map" ref={host}><SkyView model={model} boxPx={width} frameOn={false} onOpenLens={()=>setSettings(v=>!v)} onOpenLayers={()=>setSettings(v=>!v)}/></div>
    <div className="cst-finder-controls"><div className="cst-actions"><button className="btn" aria-pressed={model.mode==='cam'} onClick={camera}>{model.mode==='cam' ? 'Stop camera' : 'AR camera'}</button><button className="btn" aria-pressed={model.gyro} onClick={gyro}>{model.gyro ? 'Stop compass' : 'Follow compass'}</button><button className="btn" onClick={()=>onHorizon({az:model.az,alt:model.alt})}>Edit horizon at this direction</button></div>
      <p className="cst-position">{model.siteName} · {model.azStr} · {model.altStr}</p>
      <p>Point your phone at the sky, or drag the map. Tap an object to follow its path.</p>
      <p>Camera and compass alignment are approximate; they do not command the telescope.</p>
      {(model.cameraError || model.gyroError) && <p role="status">{model.cameraError ?? model.gyroError}</p>}
      {model.placementNote && <p role="status">{model.placementNote}</p>}
      {model.rankingError && <p role="status">{model.rankingError}</p>}
      {model.lock && <p className="cst-position">{model.lock.name} · {model.lockNote}</p>}
      <button className="btn" aria-expanded={settings} onClick={()=>setSettings(v=>!v)}>Layers & object types</button>
      {settings && <div className="cst-options">{(['clouds','horizon','wind'] as const).map(k=><label key={k}><input type="checkbox" checked={model.layers[k]} onChange={e=>model.setLayer(k,e.target.checked)}/>{k==='clouds'?'Cloud cover':k==='horizon'?'Obstruction horizon':'Wind & drift'}</label>)}
        {SKY_KINDS.map(k=><label key={k}><input type="checkbox" checked={model.lens[k]} onChange={e=>model.setLens(k,e.target.checked)}/>{KIND_LABEL[k]}</label>)}
        <label><input type="checkbox" checked={model.floorOnly} onChange={e=>model.setFloorOnly(e.target.checked)}/>Only objects above the observing limit</label>
        <p>{model.layersNote}</p>{model.windNote && <p>{model.windNote}</p>}
      </div>}
      {model.reachList.length>0 && <div className="cst-reach" aria-label="Objects in reach">{model.reachList.slice(0,8).map(t=><button key={t.id} className="btn" onClick={()=>model.setView({az:t.azNow,alt:t.altNow,trackId:t.id})}>{t.name}</button>)}</div>}
    </div>
  </div>;
}

export function ClassicSkyToolsLauncher() {
  const [tool,setTool]=useState<SkyTool|null>(null);
  return <><div className="flex flex-wrap gap-2"><button className="btn" onClick={()=>setTool('finder')}>AR camera & sky map</button><button className="btn" onClick={()=>setTool('horizon')}>Horizon line</button></div>{tool && <ClassicSkyTools initialTool={tool} onClose={()=>setTool(null)}/>}</>;
}
