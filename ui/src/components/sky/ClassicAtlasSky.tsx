import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react';
import { useStore } from '../../store';
import type { SkyRow } from '../../lib/skyRegion';
import { altAzOf } from '../../lib/altaz';
import { useSkyRegion } from '../../lib/skyRegion';
import { SkyCanvas } from '../atlas/SkyCanvas';
import { raDecFromAltAz } from '../../next/hubs/sky/finder/equatorial';
import { NxIcon } from '../../next/icons';
import { SkyView } from '../../next/hubs/sky/finder/SkyView';
import { useSkyModel } from '../../next/hubs/sky/finder/model';
import { KIND_LABEL, SKY_KINDS, kindOf } from '../../next/hubs/sky/finder/targets';
import { ClassicSkyTools } from './ClassicSkyTools';

export interface AtlasDisplay {
  accepts: (row: SkyRow) => boolean;
  imagery: boolean;
  objects: boolean;
  controls: ReactNode;
}

/** One atlas, with a survey or camera background and shared object filters. */
export function ClassicAtlasSky({ children }: { children: (display: AtlasDisplay) => ReactNode }) {
  const [camera, setCamera] = useState(false);
  const [panel, setPanel] = useState<'filters' | 'layers' | null>(null);
  const [horizon, setHorizon] = useState(false);
  const [imagery, setImagery] = useState(true);
  const [objects, setObjects] = useState(true);
  const [width, setWidth] = useState(540);
  const host = useRef<HTMLDivElement>(null);
  const model = useSkyModel(width, { initialMode: 'atlas' });
  const framing = useStore(s => s.framing);
  const site = useStore(s => s.status?.site);
  const toast = useStore(s => s.enqueueToast);

  useEffect(() => {
    const el = host.current;
    if (!el) return;
    const resize = () => setWidth(Math.max(220, Math.min(720, el.clientWidth)));
    resize();
    const observer = new ResizeObserver(resize);
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  const survey = () => {
    setPanel(null);
    model.setMode('atlas');
    setCamera(false);
  };
  const startCamera = () => {
    setPanel(null);
    if (!model.secureContext || !model.cameraSupported) {
      toast({ level: 'warning', title: 'AR needs a camera and HTTPS (or localhost). The survey is still available.' });
      return;
    }
    // Seed the visual aim from the survey; never send these approximate
    // horizontal coordinates to the mount or overwrite the framing session.
    if (!model.gyro && framing && typeof site?.latitude === 'number' && typeof site?.longitude === 'number') {
      const aim = altAzOf(framing.center.ra_hours, framing.center.dec_deg, site.latitude, site.longitude, Date.now() / 1000);
      model.setView({ az: aim.azDeg, alt: aim.altDeg, trackId: null });
    }
    model.setMode('cam');
    setCamera(true);
  };
  const editHorizon = () => { survey(); if (model.gyro) model.toggleGyro(); setPanel(null); setHorizon(true); };
  const toggleCompass = () => {
    if (!model.gyro && (typeof site?.latitude !== 'number' || typeof site?.longitude !== 'number')) {
      toast({level:'warning',title:'Set your observing location to align the atlas with your compass.'});
      return;
    }
    model.toggleGyro();
  };
  const compassCenter = model.gyro && typeof site?.latitude === 'number' && typeof site?.longitude === 'number'
    ? raDecFromAltAz(model.alt, model.az, site.latitude, site.longitude, Date.now() / 1000) : null;
  const accepts = (row: SkyRow) => {
    const kind = kindOf(row);
    return kind !== null && model.lens[kind] !== false;
  };
  const togglePanel = (next: 'filters' | 'layers') => setPanel(old => old === next ? null : next);
  const visibleModel = objects ? model : { ...model, markers: [], track: null };

  const controls = <div data-atlas-controls className="cst-on-sky"
    onPointerDown={e => e.stopPropagation()} onPointerUp={e => e.stopPropagation()}
    onPointerMove={e => e.stopPropagation()} onClick={e => e.stopPropagation()}
    onKeyDown={e => {
      e.stopPropagation();
      if (e.key === 'Escape') {
        e.currentTarget.querySelector<HTMLButtonElement>('[aria-expanded="true"]')?.focus();
        setPanel(null);
      }
    }}>
    <div className="cst-sky-icons" role="group" aria-label="Sky view controls">
      <button type="button" title="Object filters" aria-label="Object filters" aria-expanded={panel === 'filters'} onClick={() => togglePanel('filters')}><NxIcon name="funnel" size={20}/></button>
      <button type="button" title="Layers" aria-label="Layers" aria-expanded={panel === 'layers'} onClick={() => togglePanel('layers')}><NxIcon name="layers" size={20}/></button>
      <button type="button" title={camera ? 'Show survey atlas' : 'Switch to AR camera'} aria-label={camera ? 'Show survey atlas' : 'Switch to AR camera'} onClick={camera ? survey : startCamera}><NxIcon name={camera ? 'constellation' : 'aperture'} size={23}/></button>
      <button type="button" className="cst-compass" title={model.gyro ? 'Stop compass tracking' : 'Follow phone compass'} aria-label={model.gyro ? 'Stop compass tracking' : 'Follow phone compass'} aria-pressed={model.gyro} onClick={toggleCompass}><NxIcon name="compass-rose" size={25}/></button>
    </div>
    {panel && <section className="panel cst-atlas-options cst-sky-popover" aria-label={panel === 'filters' ? 'Object filters' : 'Atlas layers'}>
      <div className="cst-option-heading"><h2>{panel === 'filters' ? 'Object filters' : 'Layers'}</h2><button className="btn" aria-label="Close atlas options" onClick={() => setPanel(null)}>×</button></div>
      <div className="cst-option-grid">
        {panel === 'filters' ? SKY_KINDS.map(kind => <label key={kind}><input type="checkbox" checked={model.lens[kind]} onChange={e => model.setLens(kind, e.target.checked)}/>{KIND_LABEL[kind]}</label>) : <>
          {!camera && <label><input type="checkbox" checked={imagery} onChange={e => setImagery(e.target.checked)}/>Survey imagery</label>}
          <label><input type="checkbox" checked={objects} onChange={e => setObjects(e.target.checked)}/>Objects & targets</label>
          {camera && (['clouds', 'horizon', 'wind'] as const).map(layer => <label key={layer}><input type="checkbox" checked={model.layers[layer]} onChange={e => model.setLayer(layer, e.target.checked)}/>{layer === 'clouds' ? 'Cloud cover' : layer === 'horizon' ? 'Obstruction horizon' : 'Wind & drift'}</label>)}
        </>}
      </div>
      {panel === 'layers' && <div className="cst-layer-footer"><button className="btn" onClick={editHorizon}>Edit horizon line</button>{camera && <p>{model.layersNote}</p>}</div>}
    </section>}
  </div>;

  return <div className="cst-atlas" ref={host}>
    <h1 className="sr-only">Sky atlas</h1>
    {horizon && <button className="btn cst-back-atlas" onClick={() => setHorizon(false)}>Back to atlas</button>}
    <div hidden={camera || horizon || !!compassCenter}>{children({ accepts, imagery, objects, controls: camera || horizon || compassCenter ? null : controls })}</div>
    {!camera && !horizon && compassCenter && <CompassSurvey center={compassCenter} display={{accepts,imagery,objects,controls}}/>}
    {!camera && model.gyroError && <p role="status">{model.gyroError}</p>}
    {camera && !horizon && <section className="panel cst-atlas-camera" aria-label="Sky atlas camera">
      <SkyView model={visibleModel} boxPx={width} frameOn={false} hideTools overlayControls={controls}/>
      <div className="cst-camera-caption">
        <span>{model.lock ? model.lock.name : 'Drag to look around · tap an object to follow it'}</span>
      </div>
      <p>Camera alignment is approximate. Your survey framing is kept when you return.</p>
      {(model.cameraError || model.gyroError || model.placementNote) && <p role="status">{model.cameraError ?? model.gyroError ?? model.placementNote}</p>}
    </section>}
    {horizon && <ClassicSkyTools embedded initialTool="horizon" activeTool="horizon" onToolChange={() => setHorizon(false)} onClose={() => setHorizon(false)} initialAim={{ az: model.az, alt: model.alt }}/>}
  </div>;
}

/** Sensor browsing never changes the saved frame or exposes equipment commands. */
function CompassSurvey({center,display}:{center:{ra_hours:number;dec_deg:number};display:AtlasDisplay}) {
  const [zoom,setZoom]=useState(55);
  const [degraded,setDegraded]=useState(false);
  const surveyError=useCallback(()=>setDegraded(true),[]);
  const surveyLoad=useCallback(()=>setDegraded(false),[]);
  const config=useStore(s=>s.config);
  const night=useStore(s=>s.night);
  const framing=useStore(s=>s.framing);
  const region=useSkyRegion(center,zoom,true);
  return <section className="panel cst-atlas-camera" aria-label="Compass survey">
    <SkyCanvas center={center} rotationDeg={0} survey={framing?.survey === 'schematic' ? 'DSS2/color' : framing?.survey ?? 'DSS2/color'} stretch="linear" fovZoomDeg={zoom}
      optics={null} mosaic={{rows:1,cols:1,overlap:0}} night={night} mode={display.imagery?'survey':'schematic'} showFraming={false}
      onlineFetch={config?.survey?.online_fetch ?? false} surveyDegraded={degraded} onSurveyError={surveyError} onSurveyLoad={surveyLoad}
      skyRows={display.objects?region.rows.filter(display.accepts):[]} overlayControls={display.controls}
      onCenterChange={()=>{}} onRotate={()=>{}} onZoom={setZoom}/>
    <p>Following your phone · compass alignment is approximate. Turn the compass off to return to your saved framing.</p>
  </section>;
}
