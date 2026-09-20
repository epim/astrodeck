import { useEffect, useRef, useState } from "react";
import { applyLocation, getMountGps, getSite, listLocations, saveLocation, type MountGps } from "../api/site";
import type { SavedLocation } from "../types";
import { useStore } from "../store";
import { useCan } from "../lib/caps";
import { Icon } from "../components/icons";
import { useGuidedSetup } from "./setup";

export default function GuidedLocation({ onContinue, active = true }: { onContinue: () => void; active?: boolean }) {
  const canEdit = useCan("config.site_optics");
  const [name, setName] = useState("");
  const [manual, setManual] = useState(false);
  const [lat, setLat] = useState(""); const [lon, setLon] = useState(""); const [elev, setElev] = useState("0");
  const [gps, setGps] = useState<MountGps | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [accuracy, setAccuracy] = useState<number | null>(null);
  const [sites,setSites]=useState<SavedLocation[]>([]);
  const [showSites,setShowSites]=useState(false);
  const created = useRef<string | null>(null);
  const activeRef = useRef(active); activeRef.current = active;
  const revision = useRef(0);
  useEffect(()=>{if(!canEdit||!active)return;let live=true;void listLocations().then(rows=>{if(live&&Array.isArray(rows))setSites(rows);}).catch(()=>{});return()=>{live=false;};},[active,canEdit]);
  const changed = () => { revision.current++; created.current = null; useGuidedSetup.getState().invalidate("location"); };
  useEffect(() => {
    if (!canEdit || !active) return;
    let live = true;
    const probe = () => { void getMountGps(true).then(value => { if (live) setGps(value); }).catch(() => { if (live) setGps({available:false,detail:"GPS check failed. Check the connection and try again."}); }); };
    probe(); const timer = setInterval(probe, 15000);
    return () => { live = false; clearInterval(timer); };
  }, [canEdit, active]);
  const locate = () => {
    setError(null);
    if (!window.isSecureContext || !navigator.geolocation) { setError("Browser location needs HTTPS or localhost. You can enter your location manually below."); return; }
    changed(); const requested = revision.current; setBusy("locating");
    navigator.geolocation.getCurrentPosition(fix => {
      setBusy(null);
      if (revision.current !== requested) return;
      setLat(fix.coords.latitude.toFixed(6)); setLon(fix.coords.longitude.toFixed(6));
      if (fix.coords.altitude != null) setElev(String(Math.round(fix.coords.altitude)));
      setAccuracy(Math.round(fix.coords.accuracy));
    }, () => { setBusy(null); setError("We couldn't get your location. Allow location access in your browser, or enter it manually."); }, {enableHighAccuracy:true,timeout:10000,maximumAge:0});
  };
  const submit = async (event: React.FormEvent) => {
    event.preventDefault(); if (busy || !canEdit) return;
    if (!name.trim()) { setError("Give this observing site a name."); return; }
    const latitude = lat.trim() ? Number(lat) : NaN, longitude = lon.trim() ? Number(lon) : NaN, elevation = elev.trim() ? Number(elev) : NaN;
    if (![latitude, longitude, elevation].every(Number.isFinite) || Math.abs(latitude)>90 || Math.abs(longitude)>180 || elevation < -430 || elevation > 9000) {
      setError("Choose a location source or enter valid coordinates and elevation."); return;
    }
    setBusy("saving"); setError(null);
    try {
      if (!created.current) created.current = (await saveLocation({name:name.trim(),latitude,longitude,elevation_m:elevation})).id;
      await applyLocation(created.current);
      await useStore.getState().loadConfig();
      const current = await getSite(); useStore.setState({site:current.site});
      useGuidedSetup.setState({siteId:created.current});
      const checked = await useGuidedSetup.getState().complete("location");
      if(checked && activeRef.current) onContinue();
    } catch { setError(created.current ? "The site was created, but couldn't be activated. Try Save and continue again." : "The site couldn't be saved. Check the connection and use a name that isn't already saved."); }
    finally { setBusy(null); }
  };
  const gpsReason = gps === null ? "Checking for GPS…" : gps.available ? undefined : gps.detected ? "GPS detected; waiting for a fix" : "no gps detected";
  return <form id="guided-location-form" onSubmit={event => void submit(event)} className="guided-bespoke">
    <label className="guided-field">Name this observing site<input required maxLength={80} placeholder="For example, Backyard" value={name} disabled={!canEdit || busy === "saving"} onChange={e => {changed();setName(e.target.value);}} autoComplete="off"/></label>
    <p>You'll use this name to find the location and its horizon next time.</p>
    {sites.length>0 && <section><button type="button" className="guided-text-button" aria-expanded={showSites} onClick={()=>setShowSites(v=>!v)}>Use a saved observing site</button>{showSites && <div className="guided-button-row">{sites.map(site=><button key={site.id} type="button" className="btn" disabled={!!busy||!canEdit} onClick={()=>{changed();created.current=site.id;setName(site.name);setLat(String(site.latitude));setLon(String(site.longitude));setElev(String(site.elevation_m));setAccuracy(null);setShowSites(false);}}>{site.name}</button>)}</div>}</section>}
    <section className="guided-choice-card">
      <Icon name="mount" size={28}/><h2>Are you beside the telescope?</h2>
      <p>Use this device's location if you are. If the telescope is somewhere else, use a GPS attached to it or enter its coordinates.</p>
      <div className="guided-button-row">
        <button type="button" className="btn btn-accent" disabled={!canEdit || !!busy} onClick={locate}>{busy === "locating" ? "Finding your location…" : "Use my location"}</button>
        <span title={gpsReason} tabIndex={gpsReason ? 0 : undefined}><button type="button" className="btn" title={gpsReason} disabled={!canEdit || !!busy || !gps?.available} onClick={() => {if (!gps?.available) return;changed();setLat(String(gps.latitude));setLon(String(gps.longitude));setElev(String(gps.elevation_m ?? 0));setAccuracy(null);}}>{gps?.source === "usb" ? "Use USB GPS" : "Use mount GPS"}</button></span>
      </div>
      {gpsReason && <small>{gpsReason}</small>}
      <button type="button" className="guided-text-button" disabled={busy === "saving"} onClick={() => setManual(v => !v)} aria-expanded={manual}>{manual ? "Hide manual entry" : "Enter location manually"}</button>
    </section>
    {manual && <div className="guided-coordinate-fields">
      <label className="guided-field">Latitude<input required type="number" step="any" min={-90} max={90} value={lat} disabled={!canEdit || !!busy} onChange={e => {changed();setLat(e.target.value);}}/><small>North is positive; south is negative.</small></label>
      <label className="guided-field">Longitude<input required type="number" step="any" min={-180} max={180} value={lon} disabled={!canEdit || !!busy} onChange={e => {changed();setLon(e.target.value);}}/><small>East is positive; west is negative.</small></label>
      <label className="guided-field">Elevation in metres<input required type="number" min={-430} max={9000} step="any" value={elev} disabled={!canEdit || !!busy} onChange={e => {changed();setElev(e.target.value);}}/></label>
    </div>}
    {lat && lon && <p role="status">Location filled: {Number(lat).toFixed(4)}°, {Number(lon).toFixed(4)}° · {elev} m{accuracy != null ? ` · about ${accuracy} m accuracy` : ""}. Check that this is where the telescope is.</p>}
    {error && <p role="alert" className="guided-warning">{error}</p>}
    {!canEdit && <p role="status">An administrator needs to create the observing site.</p>}
    {busy === "saving" && <p role="status">Saving your site…</p>}
  </form>;
}
