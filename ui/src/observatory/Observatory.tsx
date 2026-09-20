import { useEffect, useRef, useState } from "react";
import Logo from "../components/Logo";
import { api } from "../api";
import { DEFAULT_OPTIONS, TARGETS, durationLabel, previewTime, stepsFor, type Options, type Target } from "./model";
import "./observatory.css";
import FlowStudio from './FlowStudio';

export function Icon({ name, size = 20 }: { name: string; size?: number }) {
  const paths: Record<string, string> = {
    sky: "M3 17a9 9 0 0 1 18 0M3 17h18M7 17a16 16 0 0 1 5-12 16 16 0 0 1 5 12M5 11h14M12 3v2",
    flow: "M4 4h6v6H4zM14 14h6v6h-6zM7 10v7h7M14 4h6v6h-6zM10 7h4",
    camera: "M3 7h5l2-3h4l2 3h5v13H3zM16 13a4 4 0 1 1-8 0 4 4 0 0 1 8 0",
    rig: "m5 13 11-9 4 5-11 9zM11 16l-4 6m4-6 4 6M4 12l-2 2 4 5 2-2",
    moon: "M20 14A8 8 0 0 1 10 4a8 8 0 1 0 10 10Z",
    sun: "M16 12a4 4 0 1 1-8 0 4 4 0 0 1 8 0M12 2v2m0 16v2M2 12h2m16 0h2M5 5l2 2m10 10 2 2M5 19l2-2M17 7l2-2",
    target: "M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0M12 1v6m0 10v6M1 12h6m10 0h6",
    focus: "M3 8V3h5m8 0h5v5M3 16v5h5m8 0h5v-5M16 12a4 4 0 1 1-8 0 4 4 0 0 1 8 0",
    activity: "M2 12h4l3-7 5 14 3-7h5",
    cloud: "M6 18a4 4 0 1 1 1-8 6 6 0 0 1 11-1 4.5 4.5 0 0 1 0 9Z",
    check: "m5 12 4 4L19 6",
    arrow: "M4 12h15m-6-6 6 6-6 6",
    search: "M16 10a6 6 0 1 1-12 0 6 6 0 0 1 12 0m-2 4 6 6",
    tune: "M4 6h16M4 12h16M4 18h16M8 3v6m8 0v6m-6 0v6",
    close: "m6 6 12 12M6 18 18 6",
    play: "m8 4 12 8-12 8Z",
    book: "M3 4h7l2 2 2-2h7v15h-7l-2 2-2-2H3zM12 6v15",
    shield: "m12 3 8 3v6c0 5-8 9-8 9s-8-4-8-9V6zM8 12l3 3 5-6",
  };
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d={paths[name] || paths.sky} /></svg>;
}

// Orthographic hemisphere, tilted toward the observer. Only example coordinates.
function point(alt: number, az: number) {
  const a = alt * Math.PI / 180, z = az * Math.PI / 180;
  return [450 + 338 * Math.cos(a) * Math.sin(z), 326 - 276 * Math.sin(a) - 106 * Math.cos(a) * Math.cos(z)];
}
function line(points: number[][]) { return points.map((p, i) => `${i ? "L" : "M"}${p[0].toFixed(1)},${p[1].toFixed(1)}`).join(" "); }
function targetPoint(t: Target, time: number) { return point(t.alt - Math.sin(time / 200) * 12, t.az + time / 8); }
export function Dome({ target, select, time, clouds, horizon }: { target: Target; select: (t: Target) => void; time: number; clouds: boolean; horizon: boolean }) {
  return <svg className="obs-dome" viewBox="0 0 900 470" role="group" aria-label="Illustrative SkyDome. Select a target to plan it.">
    <defs>
      <radialGradient id="obs-atmosphere"><stop stopColor="var(--o-sky-glow)" stopOpacity=".6"/><stop offset="1" stopColor="var(--o-bg)" stopOpacity="0"/></radialGradient>
      <linearGradient id="obs-ground" x2="0" y2="1"><stop stopColor="var(--o-land)"/><stop offset="1" stopColor="var(--o-bg)"/></linearGradient>
      <linearGradient id="obs-cloud" x2="1" y2="1"><stop stopColor="var(--o-cloud)" stopOpacity=".01"/><stop offset=".5" stopColor="var(--o-cloud)" stopOpacity=".22"/><stop offset="1" stopColor="var(--o-cloud)" stopOpacity=".02"/></linearGradient>
      <filter id="obs-soft"><feGaussianBlur stdDeviation="13"/></filter>
      <clipPath id="obs-hemisphere"><path d="M112 326A338 296 0 0 1 788 326A338 106 0 0 1 112 326"/></clipPath>
    </defs>
    <ellipse cx="450" cy="255" rx="420" ry="240" fill="url(#obs-atmosphere)"/>
    <g clipPath="url(#obs-hemisphere)">
      {Array.from({ length: 140 }, (_, i) => <circle key={i} cx={110 + ((i * 137.508) % 680)} cy={38 + ((i * 63.743) % 380)} r={i % 13 === 0 ? 1.6 : .7} fill="var(--o-text)" opacity={.12 + (i % 5) * .12} />)}
      <path d="M240 410Q550 320 525 100T650-40" stroke="var(--o-sky-glow)" strokeWidth="92" opacity=".13" fill="none" filter="url(#obs-soft)"/>
      {[0, 15, 30, 45, 60, 75].map(alt => <path key={alt} d={line(Array.from({length: 121}, (_, i) => point(alt, i * 3)))} className="obs-gridline"/>)}
      {Array.from({length:12}, (_, i) => <path key={i} d={line(Array.from({length:46}, (_, j) => point(j * 2, i * 30)))} className="obs-gridline"/>)}
      {clouds && <g transform={`translate(${time / 9},0)`}>
        <path d="M105 212Q166 161 242 198T420 233L436 309Q326 317 231 270T92 269Z" fill="url(#obs-cloud)" filter="url(#obs-soft)"/>
        <path d="M103 215Q186 199 237 221T404 256M135 240Q195 216 277 254" stroke="var(--o-cloud)" strokeOpacity=".16" fill="none"/>
      </g>}
      {TARGETS.map((t, i) => <path key={t.id} d={line(Array.from({length:65}, (_, j) => targetPoint(t, -120 + j * 10)))} className={`obs-target-path ${t.id === target.id ? "selected" : ""}`} strokeDasharray={i ? "3 6" : undefined}/>)}
    </g>
    <ellipse cx="450" cy="326" rx="338" ry="106" fill="url(#obs-ground)" fillOpacity=".85" stroke="var(--o-line)"/>
    <path d="M112 326A338 296 0 0 1 788 326" fill="none" stroke="var(--o-line)"/>
    {horizon && <><path d="M114 324 144 299 167 311 190 283 208 288 221 264 236 287 254 293 265 319 283 324 299 313 318 329Q460 391 617 331L634 303 647 297 655 272 669 298 684 288 703 312 721 287 735 307 750 298 786 325" fill="none" stroke="var(--o-warn)" strokeOpacity=".65" strokeWidth="1.5" strokeDasharray="4 4"/><text x="620" y="387" className="obs-map-note">Your obstruction line</text></>}
    <path d="m441 353 10-11 12 8-10 12zM450 360l-9 15m9-15 9 15" stroke="var(--o-muted)" fill="none" strokeWidth="1.5"/>
    <text x="450" y="396" textAnchor="middle" className="obs-map-note">YOUR TELESCOPE</text>
    {[['N',450,32],['E',808,334],['S',450,458],['W',88,334]].map(([s,x,y]) => <text key={s} x={x} y={y} textAnchor="middle" className="obs-cardinal">{s}</text>)}
    <text x="461" y="72" className="obs-map-note">90°</text>
    {TARGETS.map(t => { const [x,y] = targetPoint(t, time); const active = target.id === t.id; return <g key={t.id} role="button" tabIndex={0} aria-label={`Select ${t.name}`} aria-pressed={active} onClick={() => select(t)} onKeyDown={e => { if(e.key === "Enter" || e.key === " ") { e.preventDefault(); select(t); } }} className={`obs-sky-target ${active ? "active" : ""}`} transform={`translate(${x},${y})`}>
      <circle r="46" fill="transparent"/><circle r={active ? 16 : 7} fill="var(--o-accent)" fillOpacity=".08" stroke="currentColor" strokeOpacity=".6"/><circle r="3" fill="currentColor"/>
      {active && <path d="M-22 0h8M14 0h8M0-22v8M0 14v8" stroke="currentColor"/>}
      <text x="25" y="-3" className="obs-target-label">{t.id}</text><text x="25" y="14" className="obs-map-note">{t.constellation}</text>
    </g>; })}
  </svg>;
}

function Toggle({ label, detail, checked, change, icon }: { label: string; detail: string; checked: boolean; change: () => void; icon: string }) {
  return <button className="obs-toggle-row" role="switch" aria-checked={checked} onClick={change}><Icon name={icon}/><span><strong>{label}</strong><small>{detail}</small></span><span className={`obs-switch ${checked ? "on" : ""}`}><i/></span></button>;
}

export default function Observatory() {
  const [view, setView] = useState(() => new URLSearchParams(location.hash.split("?")[1]).get("view") === "flows" ? "flows" : "sky");
  const goView = (next: string) => {
    setView(next);
    document.querySelector('.observatory')?.scrollTo({ top: 0 });
    window.history.replaceState(null, '', `#/observatory${next === 'flows' ? '?view=flows' : ''}`);
  };
  const [target, setTarget] = useState<Target>(TARGETS[0]);
  const [options, setOptions] = useState<Options>({ ...DEFAULT_OPTIONS });
  const [time, setTime] = useState(0);
  const [clouds, setClouds] = useState(true);
  const [horizon, setHorizon] = useState(true);
  const [advanced, setAdvanced] = useState(false);
  const [night, setNight] = useState(false);
  const [flowView, setFlowView] = useState("story");
  const [frames, setFrames] = useState(40);
  const [exposure, setExposure] = useState(180);
  const [query, setQuery] = useState("");
  const [rehearsal, setRehearsal] = useState<"idle" | "running" | "paused" | "done">("idle");
  const [stage, setStage] = useState(0);
  const [hold, setHold] = useState(false);
  const [message, setMessage] = useState("");
  const [dialog, setDialog] = useState(false);
  const modal = useRef<HTMLDialogElement>(null);
  const [devices, setDevices] = useState<{ role: string; connected: boolean }[]>([]);
  const [rigState, setRigState] = useState("Checking connection");
  const steps = stepsFor(options, target, frames, exposure);
  const locked = rehearsal === "running" || rehearsal === "paused";
  const total = durationLabel(frames, exposure);
  const changeOption = (key: keyof Options) => { if (locked) { setMessage("Finish or reset the rehearsal before changing its plan."); return; } setOptions(o => ({ ...o, [key]: !o[key] })); };
  const choose = (t: Target) => { if (locked) { setMessage("Reset the rehearsal to choose another target."); return; } setTarget(t); };
  useEffect(() => {
    let alive = true;
    const poll = async () => {
      try {
        const s = await api.get<{ mode?: string; connected?: Record<string, { connected?: boolean }> }>("/api/status");
        if (!alive) return;
        setDevices(Object.entries(s.connected || {}).map(([role, value]) => ({ role, connected: value.connected === true })));
        setRigState(s.mode === "sim" ? "Simulator connected" : "Server connected");
      } catch { if (alive) { setRigState("Server unavailable or sign-in needed"); setDevices([]); } }
    };
    void poll(); const id = window.setInterval(poll, 15000);
    return () => { alive = false; window.clearInterval(id); };
  }, []);
  useEffect(() => {
    if (rehearsal !== "running" || hold) return;
    const id = window.setTimeout(() => {
      if (stage >= steps.length - 1) setRehearsal("done"); else setStage(s => s + 1);
    }, 3800);
    return () => window.clearTimeout(id);
  }, [rehearsal, hold, stage, steps.length]);
  useEffect(() => { if (dialog) modal.current?.showModal(); else modal.current?.close(); }, [dialog]);
  const reset = () => { setRehearsal("idle"); setStage(0); setHold(false); };
  const exportPlan = () => {
    const blob = new Blob([JSON.stringify({ format: "astrodeck-observatory-concept", version: 1, illustrative: true, target, frames, exposure_s: exposure, options, steps }, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob); const a = document.createElement("a"); a.href = url; a.download = `astrodeck-${target.id.replaceAll(" ", "-")}-concept.json`; a.click(); window.setTimeout(() => URL.revokeObjectURL(url), 1000);
    setMessage("Concept plan downloaded. It is a design draft, not an executable AstroFlow.");
  };
  const nav = [{ id: "sky", label: "Tonight", icon: "sky" }, { id: "flows", label: "AstroFlows", icon: "flow" }, { id: "rig", label: "Equipment", icon: "rig" }];
  return <div className={`observatory ${night ? "obs-night" : ""}`}>
    <a className="obs-skip" href="#obs-main" onClick={e => { e.preventDefault(); document.getElementById("obs-main")?.focus(); }}>Skip to workspace</a>
    <aside className="obs-rail"><a href="#/observatory" className="obs-brand" aria-label="AstroDeck observatory"><Logo size={39}/></a><nav aria-label="Workspace">{nav.map(n => <button key={n.id} aria-current={view === n.id ? "page" : undefined} onClick={() => goView(n.id)}><Icon name={n.icon} size={23}/><span>{n.label}</span></button>)}</nav><div className="obs-rail-bottom"><button aria-label="Toggle red night mode" aria-pressed={night} onClick={() => setNight(!night)}><Icon name="moon"/><span>Night</span></button><a href="#/" title="Open current AstroDeck UI"><Icon name="arrow"/><span>Current UI</span></a></div></aside>
    <div className="obs-shell">
      <header className="obs-header"><div className="obs-wordmark">ASTRO<span>DECK</span><span className="obs-version">OBSERVATORY</span></div><div className="obs-header-right"><span className="obs-demo">Interactive concept</span><button className={`obs-connection ${rigState.includes("connected") ? "connected" : ""}`} onClick={() => goView("rig")}><span className="obs-dot"/>{rigState}</button></div></header>
      <main id="obs-main" className="obs-main" tabIndex={-1}>
        <div className="obs-page-heading"><div><div className="obs-eyebrow">{view === "sky" ? "MONDAY, SEPTEMBER 14 · EXAMPLE NIGHT" : view === "flows" ? "YOUR NIGHT, CHOREOGRAPHED" : "YOUR OBSERVATORY"}</div><h1>{view === "sky" ? "Your night, in view." : view === "flows" ? "Build the night. Connect the possibilities." : "Many makers. One rig."}</h1></div>{view !== "flows" && <button className="obs-button obs-expert" aria-pressed={advanced} onClick={() => setAdvanced(!advanced)}><Icon name="tune"/>{advanced ? "Hide fine controls" : "Fine controls"}</button>}</div>
        {view === 'flows' ? <FlowStudio target={target.id} frames={frames} exposure={exposure}/> : <div className="obs-layout">
          <section className="obs-workspace">
            {view === "sky" && <>
              <section className="obs-sky-panel" aria-label="Explore the example night"><div className="obs-panel-heading"><div><span className="obs-eyebrow">SKYDOME</span><span className="obs-inline-note">The sky above your telescope</span></div><span className="obs-chip">Illustrative sky</span></div>
                <div className="obs-sky-tools"><button className={clouds ? "active" : ""} aria-pressed={clouds} onClick={() => setClouds(!clouds)}><Icon name="cloud" size={16}/>Clouds</button><button className={horizon ? "active" : ""} aria-pressed={horizon} onClick={() => setHorizon(!horizon)}><Icon name="sky" size={16}/>My horizon</button></div>
                <Dome target={target} select={choose} time={time} clouds={clouds} horizon={horizon}/>
                <div className="obs-sky-caption"><span><i className="obs-legend-line"/> Target paths</span><span><i className="obs-legend-cloud"/> Cloud layer</span><span>Tap a target. Move through the night.</span></div>
                <div className="obs-time"><button className={`obs-now ${time === 0 ? "active" : ""}`} onClick={() => setTime(0)}>Now</button><div className="obs-time-track"><label htmlFor="obs-time">Preview time <strong>{previewTime(time)}</strong></label><input id="obs-time" aria-label="Preview time" type="range" min="0" max="420" step="15" value={time} onChange={e => setTime(Number(e.target.value))}/><div className="obs-time-labels"><span>21:30</span><span>23:00</span><span>01:00</span><span>03:00</span><span>04:30</span></div></div><Icon name="sun"/></div>
              </section>
              <section className="obs-target-section"><div className="obs-section-heading"><h2>Worth staying up for</h2><label className="obs-search"><Icon name="search" size={16}/><input aria-label="Search example targets" placeholder="Find a target" value={query} onChange={e => setQuery(e.target.value)}/></label></div><div className="obs-target-cards">{TARGETS.filter(t => `${t.name} ${t.id}`.toLowerCase().includes(query.toLowerCase())).map(t => <button key={t.id} className={`obs-target-card ${target.id === t.id ? "selected" : ""}`} onClick={() => choose(t)} aria-pressed={target.id === t.id}><div className={`obs-object-art ${t.color}`} aria-hidden="true"><i/><b/><span/></div><div><span className="obs-eyebrow">{t.id} · {t.kind}</span><h3>{t.name}</h3><p>{t.constellation} <span>↗ {t.alt}°</span></p></div>{target.id === t.id && <span className="obs-selected-mark"><Icon name="check" size={14}/></span>}</button>)}</div>{!TARGETS.some(t => `${t.name} ${t.id}`.toLowerCase().includes(query.toLowerCase())) && <p className="obs-empty">No example targets match. Try “M31” or “nebula”.</p>}</section>
            </>}
            {view === "rig" && <section className="obs-equipment"><div className="obs-section-heading"><h2>Connected equipment</h2><span className="obs-chip">Live server status · refreshes every 15s</span></div><p className="obs-muted">{rigState}. Configure devices and issue commands in the existing equipment workspace.</p><div className="obs-device-grid">{devices.map(d => <a key={d.role} href="#/rig/devices" className="obs-device"><Icon name={d.role.includes("camera") ? "camera" : "rig"} size={27}/><div><h3>{d.role.replaceAll("_", " ")}</h3><span className={d.connected ? "obs-good" : "obs-muted"}>{d.connected ? "✓ Connected" : "Not connected"}</span></div><Icon name="arrow" size={16}/></a>)}</div>{devices.length === 0 && <p className="obs-empty">No device status is available. Open the current UI to check the server connection or sign in.</p>}<a href="#/rig/devices" className="obs-button">Open equipment controls <Icon name="arrow"/></a></section>}
            <section className="obs-flow-panel"><div className="obs-section-heading"><div><span className="obs-eyebrow">ASTROFLOW</span><h2>{target.id} · A quiet night of imaging</h2></div><div className="obs-segments" aria-label="Plan presentation">{["story", "timeline", "graph"].map(v => <button key={v} onClick={() => v === "graph" ? goView("flows") : setFlowView(v)} aria-pressed={flowView === v}>{v}</button>)}</div></div>
              {flowView === "story" && <div className="obs-story">{steps.map((s,i) => <div key={s.id} className={`obs-step ${locked && stage === i ? "current" : ""} ${rehearsal === "done" || locked && stage > i ? "complete" : ""}`}><span className="obs-step-icon"><Icon name={rehearsal === "done" || locked && stage > i ? "check" : s.icon}/></span><div><strong>{s.title}</strong><p>{s.detail}</p></div><span className="obs-step-number">{String(i+1).padStart(2,"0")}</span></div>)}</div>}
              {flowView === "timeline" && <div className="obs-timeline"><div className="obs-timeline-labels"><span>Prepare</span><span>{total} exposure time</span><span>Finish</span></div><div className="obs-timeline-bar">{steps.map((s,i) => <button key={s.id} style={{flex:s.id === "capture" ? 8 : 1}} onClick={() => setMessage(`${s.title}: ${s.detail}. Timing is illustrative; ${total} excludes setup, readout, and pauses.`)} aria-label={s.title} className={i === stage && locked ? "current" : ""}><Icon name={s.icon}/><span>{s.title}</span></button>)}</div><p className="obs-muted">Exposure time is exact for your settings. Setup, readout, dithering, and weather add time.</p></div>}
              <div className="obs-flow-foot"><Icon name="shield" size={16}/><span>{options.clouds ? "If clouds interrupt: pause, check the sky, then recover." : "Cloud recovery is off in this plan."}</span><button onClick={() => setDialog(true)}>Rehearse <Icon name="play" size={14}/></button></div>
            </section>
          </section>
          <aside className="obs-plan" aria-label="Tonight's plan"><div className="obs-plan-top"><span className="obs-eyebrow">YOUR PLAN</span><span className="obs-chip">Draft</span></div><div className={`obs-plan-art ${target.color}`}><div className={`obs-object-art ${target.color}`} aria-hidden="true"><i/><b/><span/></div><span>{target.id}</span></div><h2>{target.name}</h2><p className="obs-target-note">{target.note}</p><div className="obs-target-facts"><div><span>Example altitude</span><strong>{target.alt}° <small>↗</small></strong></div><div><span>Example window</span><strong>{target.window}</strong></div></div>
            <div className="obs-section-heading obs-settings-heading"><h3>Make it yours</h3><span>{total} of light</span></div><div className="obs-exposure"><label>Exposures<input type="number" min="1" max="9999" value={frames} disabled={locked} onChange={e => setFrames(Math.min(9999, Math.max(1, Number(e.target.value) || 1)))}/></label><span>×</span><label>Seconds each<input type="number" min="1" max="3600" value={exposure} disabled={locked} onChange={e => setExposure(Math.min(3600, Math.max(1, Number(e.target.value) || 1)))}/></label></div>
            <div className="obs-automation"><Toggle icon="focus" label="Keep it sharp" detail="Focus before imaging" checked={options.focus} change={() => changeOption("focus")}/><Toggle icon="activity" label="Keep stars steady" detail="Guide the mount" checked={options.guide} change={() => changeOption("guide")}/><Toggle icon="cloud" label="Recover from clouds" detail="Pause, check, then continue" checked={options.clouds} change={() => changeOption("clouds")}/><Toggle icon="moon" label="Tuck the rig in" detail="Park and warm up at the end" checked={options.park} change={() => changeOption("park")}/></div>
            {advanced && <div className="obs-fine"><span className="obs-eyebrow">FINE CONTROLS</span><Toggle icon="target" label="Dither between frames" detail="Every 3 exposures · example policy" checked={options.dither} change={() => changeOption("dither")}/><dl><div><dt>Right ascension</dt><dd>{target.ra}</dd></div><div><dt>Declination</dt><dd>{target.dec}</dd></div></dl><a className="obs-text-link" href="#/rig/capture">Camera, filters & gain <Icon name="arrow" size={15}/></a></div>}
            <button className="obs-primary" onClick={() => setDialog(true)}><Icon name="play" size={17}/> Rehearse this night <Icon name="arrow" size={17}/></button><p className="obs-rehearse-note">An accelerated preview. No equipment moves.</p><button className="obs-save" onClick={exportPlan}>Download concept plan</button>
          </aside>
        </div>}
        <footer className="obs-footer"><span><Icon name="book" size={15}/> Sky, conditions, and timing are examples. Equipment status is live when connected.</span><div><a href="#/">Current UI ↗</a><a href="#/classic">Classic UI ↗</a></div></footer>
      </main>
    </div>
    {view !== 'flows' && <button className="obs-mobile-plan" onClick={() => document.querySelector(".obs-plan")?.scrollIntoView({ block: "start", behavior: "instant" })}><span><strong>{target.id}</strong><small>{total} of light</small></span><span>Your plan <Icon name="arrow" size={17}/></span></button>}
    {message && <div className="obs-toast" role="status"><span>{message}</span><button onClick={() => setMessage("")} aria-label="Dismiss message"><Icon name="close"/></button></div>}
    <dialog ref={modal} aria-label="Night rehearsal" className="obs-dialog" onCancel={() => setDialog(false)} onClose={() => setDialog(false)}>
      <div className="obs-section-heading"><span className="obs-eyebrow">NIGHT REHEARSAL · NO EQUIPMENT COMMANDS</span><button className="obs-icon-button" aria-label="Close rehearsal" onClick={() => setDialog(false)}><Icon name="close"/></button></div><h2>{rehearsal === "done" ? "A night, beautifully finished." : hold ? "Clouds roll in. Your plan adapts." : rehearsal === "idle" ? "See your night before it starts." : steps[stage]?.title}</h2><p className="obs-muted">{rehearsal === "done" ? `${frames} planned exposures, ${total} of light. This was a rehearsal; no images were captured.` : hold ? "In this rehearsal, imaging waits. Clear the clouds to preview recovery." : `${target.name} · ${total} planned exposure time. Each step takes a few seconds here.`}</p><div className="obs-rehearsal-steps">{steps.map((s,i) => <div key={s.id} className={`${stage === i && locked ? "active" : ""} ${rehearsal === "done" || stage > i ? "complete" : ""}`}><Icon name={rehearsal === "done" || stage > i ? "check" : s.icon}/><span>{s.title}</span></div>)}</div><div className="obs-dialog-actions">{rehearsal === "idle" || rehearsal === "done" ? <button className="obs-primary" onClick={() => { reset(); setRehearsal("running"); }}> <Icon name="play"/>{rehearsal === "done" ? "Rehearse again" : "Start rehearsal"}</button> : <><button className="obs-primary" onClick={() => setRehearsal(rehearsal === "running" ? "paused" : "running")}>{rehearsal === "running" ? "Pause rehearsal" : "Continue rehearsal"}</button><button className="obs-button" onClick={() => { if(!options.clouds) {setMessage("Cloud recovery is off. Reset the rehearsal and enable it to try this scenario.");return;} setHold(!hold); }}><Icon name="cloud"/>{hold ? "Clear the clouds" : "Bring in clouds"}</button></>}<button className="obs-button" onClick={reset}>Reset</button></div><p className="obs-muted obs-dialog-caveat">Real runs still require the server's readiness checks. These switches are a design preview of the plan, not saved rig settings.</p>
    </dialog>
  </div>;
}
