import { useEffect, useRef, useState, type PointerEvent as ReactPointerEvent } from 'react';
import { BLOCKS, blockDetail, connect, makeFlow, outputs, readDraft, trace, validate, type Block, type BlockKind, type Flow } from './flowModel';
import './flowStudio.css';

const DRAFT = 'astrodeck.observatory.visual-flow.v1';
const WIDTH = 222;
const inputPoint = (n: Block) => ({ x: n.x, y: n.y + 75 });
const outputPoint = (n: Block, port: string) => ({ x: n.x + WIDTH, y: n.y + 65 + outputs(n.kind).indexOf(port) * 35 });
const curve = (a: { x: number; y: number }, b: { x: number; y: number }) => { const d = Math.max(65, Math.abs(b.x - a.x) * .45); return `M ${a.x} ${a.y} C ${a.x + d} ${a.y}, ${b.x - d} ${b.y}, ${b.x} ${b.y}`; };
type Gesture = { mode: 'move'; id: string; dx: number; dy: number; before: Flow } | { mode: 'pan'; x: number; y: number; left: number; top: number } | { mode: 'wire'; from: string; port: string; x: number; y: number } | { mode: 'add'; kind: BlockKind; x: number; y: number; moved: boolean; startX: number; startY: number };

export default function FlowStudio({ target, frames, exposure }: { target: string; frames: number; exposure: number }) {
  const [flow, setFlow] = useState<Flow>(() => { try { return readDraft(localStorage.getItem(DRAFT)) || makeFlow(target, frames, exposure); } catch { return makeFlow(target, frames, exposure); } });
  const current = useRef(flow); current.current = flow;
  const [history, setHistory] = useState<Flow[]>([]), [future, setFuture] = useState<Flow[]>([]);
  const [selection, setSelection] = useState<{ type: 'node' | 'edge'; id: string } | null>(null);
  const [pending, setPending] = useState<{ from: string; port: string } | null>(null);
  const [gesture, setGesture] = useState<Gesture | null>(null);
  const drag = useRef<Gesture | null>(null);
  const [zoom, setZoom] = useState(.75);
  const [search, setSearch] = useState('');
  const [notice, setNotice] = useState('Drag a block onto the canvas. Pull a wire from an output to an input.');
  const [saved, setSaved] = useState('Saved on this browser');
  const [showChecks, setShowChecks] = useState(false);
  const [sky, setSky] = useState<'clear' | 'cloudy'>('clear'), [recovery, setRecovery] = useState<'clear' | 'timeout'>('clear');
  const [preview, setPreview] = useState(-1);
  const viewport = useRef<HTMLDivElement>(null);
  const issues = validate(flow), route = trace(flow, sky, recovery);
  const selected = selection?.type === 'node' ? flow.nodes.find(n => n.id === selection.id) : undefined;
  const selectedEdge = selection?.type === 'edge' ? flow.edges.find(e => e.id === selection.id) : undefined;
  const worldWidth = Math.max(1530, ...flow.nodes.map(n => n.x + WIDTH + 100));
  const worldHeight = Math.max(790, ...flow.nodes.map(n => n.y + 240));
  useEffect(() => {
    if (preview < 0) return;
    const n = current.current.nodes.find(b => b.id === route[preview]);
    const v = viewport.current;
    if (n && v) v.scrollTo({ left: Math.max(0, (n.x + WIDTH / 2) * zoom - v.clientWidth / 2), top: Math.max(0, (n.y + 75) * zoom - v.clientHeight / 2) });
    document.querySelector('.fs-preview')?.scrollIntoView({ block: 'nearest' });
  }, [preview]);
  const inspect = (id: string) => {
    setSelection({ type: 'node', id });
    if (window.matchMedia('(max-width:1200px)').matches) document.querySelector('.fs-inspector')?.scrollIntoView({ block: 'start' });
  };
  useEffect(() => { try { localStorage.setItem(DRAFT, JSON.stringify({ format: 'astrodeck-visual-concept', version: 1, graph: flow })); setSaved('Saved on this browser'); } catch { setSaved('Browser storage unavailable · download to keep'); } }, [flow]);
  const commit = (next: Flow, before = current.current) => { setHistory(h => [...h.slice(-49), before]); setFuture([]); setFlow(next); setPreview(-1); };
  const undo = () => { const prev = history.at(-1); if (!prev) return; setFuture(f => [...f, flow]); setHistory(h => h.slice(0, -1)); setFlow(prev); setPreview(-1); setPending(null); };
  const redo = () => { const next = future.at(-1); if (!next) return; setHistory(h => [...h, flow]); setFuture(f => f.slice(0, -1)); setFlow(next); setPreview(-1); setPending(null); };
  const patchNode = (patch: Partial<Block>) => { if (selected) commit({ ...flow, nodes: flow.nodes.map(n => n.id === selected.id ? { ...n, ...patch } : n) }); };
  const remove = () => { if (!selection) return; commit({ nodes: flow.nodes.filter(n => selection.type !== 'node' || n.id !== selection.id), edges: flow.edges.filter(e => selection.type === 'edge' ? e.id !== selection.id : e.from !== selection.id && e.to !== selection.id) }); setSelection(null); setPending(null); };
  const world = (x: number, y: number) => { const r = viewport.current!.getBoundingClientRect(); return { x: (x - r.left + viewport.current!.scrollLeft) / zoom, y: (y - r.top + viewport.current!.scrollTop) / zoom }; };
  const add = (kind: BlockKind, point?: { x: number; y: number }) => {
    if (flow.nodes.length >= 200) { setNotice('This draft supports up to 200 blocks.'); return; }
    const p = point || world(viewport.current!.getBoundingClientRect().left + 80, viewport.current!.getBoundingClientRect().top + 100);
    const def = BLOCKS[kind], id = crypto.randomUUID();
    const node: Block = { id, kind, x: Math.max(25, Math.min(5000, p.x)), y: Math.max(25, Math.min(5000, p.y)), label: def.title, value: kind === 'frame' ? target : def.value, count: def.count };
    commit({ ...flow, nodes: [...flow.nodes, node] }); setSelection({ type: 'node', id }); setNotice(`${def.title} added. Connect its ports to include it in the flow.`);
  };
  const wire = (from: string, port: string, to: string) => { const result = connect(current.current, from, port, to); if (result.flow) { commit(result.flow); setNotice('Connected. Drag from the same output to replace its wire.'); } else setNotice(result.error!); setPending(null); };
  const begin = (e: ReactPointerEvent, value: Gesture) => { if (e.button !== 0) return; e.preventDefault(); e.stopPropagation(); e.currentTarget.setPointerCapture(e.pointerId); drag.current = value; setGesture(value); };
  const move = (e: ReactPointerEvent) => {
    const g = drag.current; if (!g) return;
    if (g.mode === 'move') { const p = world(e.clientX, e.clientY); setFlow(f => ({ ...f, nodes: f.nodes.map(n => n.id === g.id ? { ...n, x: Math.round(Math.max(25, Math.min(5000, p.x - g.dx)) / 5) * 5, y: Math.round(Math.max(25, Math.min(5000, p.y - g.dy)) / 5) * 5 } : n) })); }
    if (g.mode === 'pan' && viewport.current) { viewport.current.scrollLeft = g.left - (e.clientX - g.x); viewport.current.scrollTop = g.top - (e.clientY - g.y); }
    if (g.mode === 'wire' || g.mode === 'add') { const next = { ...g, x: e.clientX, y: e.clientY, ...(g.mode === 'add' ? { moved: g.moved || Math.hypot(e.clientX - g.startX, e.clientY - g.startY) > 5 } : {}) }; drag.current = next; setGesture(next); }
  };
  const end = (e: ReactPointerEvent, cancel = false) => {
    const g = drag.current; if (!g) return;
    if (g.mode === 'move') { if (cancel) setFlow(g.before); else if (JSON.stringify(g.before) !== JSON.stringify(current.current)) commit(current.current, g.before); }
    if (!cancel && g.mode === 'wire') { const port = document.elementFromPoint(e.clientX, e.clientY)?.closest<HTMLElement>('[data-flow-input]'); if (port?.dataset.flowInput) wire(g.from, g.port, port.dataset.flowInput); else { setPending({ from: g.from, port: g.port }); setNotice('Choose an input port to finish the connection. Escape cancels.'); } }
    if (!cancel && g.mode === 'add') { const rect = viewport.current!.getBoundingClientRect(); if (e.clientX > rect.left && e.clientX < rect.right && e.clientY > rect.top && e.clientY < rect.bottom) add(g.kind, world(e.clientX, e.clientY)); else if (!g.moved) add(g.kind); else setNotice('Drop a block inside the canvas to add it.'); }
    drag.current = null; setGesture(null);
  };
  const fit = () => { const v = viewport.current; if (!v) return; const w = Math.max(...flow.nodes.map(n => n.x + WIDTH), 900) + 65, h = Math.max(...flow.nodes.map(n => n.y + 145), 500) + 65; setZoom(Math.max(.3, Math.min(1, (v.clientWidth - 25) / w, (v.clientHeight - 25) / h))); v.scrollLeft = 0; v.scrollTop = 0; };
  useEffect(() => { const v = viewport.current; if (!v) return; const w = Math.max(...current.current.nodes.map(n => n.x + WIDTH), 900) + 65; setZoom(Math.max(v.clientWidth < 600 ? .9 : .65, Math.min(1, (v.clientWidth - 25) / w))); }, []);
  const download = () => { const url = URL.createObjectURL(new Blob([JSON.stringify({ format: 'astrodeck-visual-concept', version: 1, executable: false, graph: flow }, null, 2)], { type: 'application/json' })); const a = document.createElement('a'); a.href = url; a.download = 'astrodeck-visual-flow.json'; a.click(); setTimeout(() => URL.revokeObjectURL(url), 1000); };
  return <section className="fs" aria-label="Visual AstroFlow editor" onPointerMove={move} onPointerUp={e => end(e)} onPointerCancel={e => end(e, true)} onKeyDown={e => {
    if (e.key === 'Escape') { setPending(null); setPreview(-1); }
    if ((e.target as HTMLElement).matches('input,textarea,select')) return;
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'z') { e.preventDefault(); if (e.shiftKey) redo(); else undo(); }
    if (e.key === 'Delete' || e.key === 'Backspace') { e.preventDefault(); remove(); }
  }}>
    <div className="fs-toolbar"><div><span className="obs-eyebrow">VISUAL WORKSPACE</span><h2>A night that thinks ahead <span>Draft</span></h2></div><div className="fs-actions"><button aria-label="Undo" onClick={undo} disabled={!history.length} title="Undo (Ctrl+Z)">↶ <span>Undo</span></button><button aria-label="Redo" onClick={redo} disabled={!future.length} title="Redo (Ctrl+Shift+Z)">↷ <span>Redo</span></button><button aria-label="Download flow" onClick={download}>↓ <span>Download</span></button><button className="fs-test" onClick={() => { setShowChecks(true); if (!issues.length) { setPreview(0); setNotice('Tracing your connected blocks. This preview sends no equipment commands.'); } }}>▷ Trace flow</button></div></div>
    <div className="fs-workbench">
      <aside className="fs-library" aria-label="Block library"><div className="fs-aside-heading"><span>Building blocks</span><small>DRAG TO ADD</small></div><input aria-label="Search blocks" placeholder="Search blocks…" value={search} onChange={e => setSearch(e.target.value)}/>
        {['Triggers', 'Actions', 'Logic'].map(group => <div key={group} className="fs-block-group"><h3>{group}</h3>{(Object.entries(BLOCKS) as [BlockKind, typeof BLOCKS[BlockKind]][]).filter(([, d]) => d.group === group && `${d.title} ${d.detail}`.toLowerCase().includes(search.toLowerCase())).map(([kind, d]) => <button className={`fs-library-block fs-${kind}`} key={kind} aria-label={`Add ${d.title}`} title={`${d.detail}. Drag onto canvas or click to add.`} onPointerDown={e => begin(e, { mode: 'add', kind, x: e.clientX, y: e.clientY, startX: e.clientX, startY: e.clientY, moved: false })} onClick={e => { if (e.detail === 0) add(kind); }}><span className="fs-glyph">{d.glyph}</span><span>{d.title}<small>{d.detail}</small></span><b>⠿</b></button>)}</div>)}
        {!Object.values(BLOCKS).some(d => `${d.title} ${d.detail}`.toLowerCase().includes(search.toLowerCase())) && <p className="obs-muted">No matching blocks.</p>}
        <div className="fs-library-tip"><span>◇</span><strong>Let the night decide.</strong><p>Logic blocks have named exits. Follow each wire to see what happens next.</p></div>
      </aside>
      <div className="fs-canvas-column"><div className="fs-canvas-heading"><span><i/> {flow.nodes.length} blocks · {flow.edges.length} connections</span><button aria-expanded={showChecks} onClick={() => setShowChecks(!showChecks)}>{issues.length ? `${issues.length} to connect / check` : '✓ Ready to trace'}</button></div>
        <div className={`fs-viewport ${pending ? 'fs-wiring' : ''}`} ref={viewport} tabIndex={0} aria-label="Flow canvas. Drag blocks to move. Drag empty space to pan." onPointerDown={e => { if (e.target === e.currentTarget || (e.target as HTMLElement).classList.contains('fs-world') || (e.target as HTMLElement).classList.contains('fs-wires')) { setSelection(null); begin(e, { mode: 'pan', x: e.clientX, y: e.clientY, left: viewport.current!.scrollLeft, top: viewport.current!.scrollTop }); } }}>
          <div style={{ width: worldWidth * zoom, height: worldHeight * zoom }}><div className="fs-world" style={{ width: worldWidth, height: worldHeight, transform: `scale(${zoom})` }}>
            <div className="fs-canvas-label" style={{ left: 55, top: 35 }}>THE PLAN <span>One block at a time. Every path has a purpose.</span></div>
            <svg className="fs-wires" width={worldWidth} height={worldHeight} aria-label="Flow connections"><defs><marker id="fs-arrow" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="5" markerHeight="5" orient="auto-start-reverse"><path d="M0 0 10 5 0 10z" fill="context-stroke"/></marker></defs>{flow.edges.map(edge => { const a = flow.nodes.find(n => n.id === edge.from), b = flow.nodes.find(n => n.id === edge.to); if (!a || !b) return null; const path = curve(outputPoint(a, edge.port), inputPoint(b)); const isTrace = preview >= 0 && route.indexOf(a.id) >= 0 && route.indexOf(a.id) < preview && route[route.indexOf(a.id) + 1] === b.id; return <g key={edge.id} className={`fs-wire ${selection?.id === edge.id ? 'selected' : ''} ${isTrace ? 'traced' : ''} ${edge.port === 'cloudy' || edge.port === 'timeout' ? 'alternate' : ''}`}><path d={path} markerEnd="url(#fs-arrow)"/><path d={path} className="fs-wire-hit" role="button" tabIndex={0} aria-label={`Connection ${a.label}, ${edge.port}, to ${b.label}`} onClick={() => setSelection({ type: 'edge', id: edge.id })} onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); setSelection({ type: 'edge', id: edge.id }); } }}/></g>; })}
              {gesture?.mode === 'wire' && (() => { const n = flow.nodes.find(b => b.id === gesture.from); return n && <path className="fs-pending-wire" d={curve(outputPoint(n, gesture.port), world(gesture.x, gesture.y))}/>; })()}
            </svg>
            {flow.nodes.map(n => <article key={n.id} className={`fs-node fs-${n.kind} ${selected?.id === n.id ? 'selected' : ''} ${route[preview] === n.id ? 'tracing' : ''}`} style={{ left: n.x, top: n.y, width: WIDTH }} aria-label={`${n.label} block`}>
              <button className="fs-node-handle" aria-label={`Move ${n.label}`} onPointerDown={e => { setSelection({ type: 'node', id: n.id }); const p = world(e.clientX, e.clientY); begin(e, { mode: 'move', id: n.id, dx: p.x - n.x, dy: p.y - n.y, before: flow }); }} onClick={() => setSelection({ type: 'node', id: n.id })} onKeyDown={e => { if (['ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight'].includes(e.key)) { e.preventDefault(); commit({ ...flow, nodes: flow.nodes.map(b => b.id === n.id ? { ...b, x: Math.max(25, Math.min(5000, b.x + (e.key === 'ArrowRight' ? 20 : e.key === 'ArrowLeft' ? -20 : 0))), y: Math.max(25, Math.min(5000, b.y + (e.key === 'ArrowDown' ? 20 : e.key === 'ArrowUp' ? -20 : 0))) } : b) }); } }}><span className="fs-glyph">{BLOCKS[n.kind].glyph}</span><span><small>{BLOCKS[n.kind].group === 'Logic' ? 'LOGIC / BRANCH' : n.kind === 'capture' ? 'LOOP / EXPOSURES' : BLOCKS[n.kind].group.toUpperCase()}</small><strong>{n.label}</strong></span><b>⠿</b></button>
              <button className="fs-node-body" aria-label={`Edit ${n.label}`} onClick={() => inspect(n.id)}>{blockDetail(n)}</button>
              <div className="fs-node-foot">{n.kind === 'finish' ? 'END OF PATH' : n.kind === 'weather' ? 'FOLLOW THE MATCHING EXIT' : n.kind === 'wait' ? 'CONTINUE OR TIME OUT' : 'THEN CONTINUE'}</div>
              {n.kind !== 'start' && <button className={`fs-port fs-input ${pending ? 'available' : ''}`} data-flow-input={n.id} style={{ top: 75 }} aria-label={`Input of ${n.label}`} title="Connect here" onClick={() => { if (pending) wire(pending.from, pending.port, n.id); else { setSelection({ type: 'node', id: n.id }); setNotice('Choose an output first, then this input.'); } }}><i/></button>}
              {outputs(n.kind).map((port, i) => <button key={port} className={`fs-port fs-output ${pending?.from === n.id && pending.port === port ? 'armed' : ''}`} style={{ top: 65 + i * 35 }} aria-label={`${port} output of ${n.label}`} title={`Drag to an input, or click then click an input`} onPointerDown={e => begin(e, { mode: 'wire', from: n.id, port, x: e.clientX, y: e.clientY })} onClick={e => { if (e.detail === 0) { setPending({ from: n.id, port }); setNotice('Output selected. Choose an input to connect.'); } }}><span>{port === 'next' ? 'out' : port}</span><i/></button>)}
            </article>)}
          </div></div>
        </div>
        <div className="fs-canvas-bottom"><span>{pending ? '● Choose an input to connect · Esc cancels' : 'Drag to arrange · Pull ports to connect · Drag space to pan'}</span><div><button aria-label="Zoom out" onClick={() => setZoom(z => Math.max(.3, z - .1))}>−</button><span>{Math.round(zoom * 100)}%</span><button aria-label="Zoom in" onClick={() => setZoom(z => Math.min(1.6, z + .1))}>+</button><button onClick={fit}>Fit</button></div></div>
        {showChecks && <div className="fs-checks"><strong>{issues.length ? 'Finish the wiring' : 'All branches are connected.'}</strong>{issues.length ? <ul>{issues.map((issue, i) => <li key={i}>{issue}</li>)}</ul> : <p>The diagram is ready for a local trace. Equipment readiness and server execution are not checked.</p>}</div>}
        {preview >= 0 && <div className="fs-preview" aria-live="polite"><span className="fs-glyph">▷</span><div><small>TRACE · {preview + 1} / {route.length}</small><strong>{flow.nodes.find(n => n.id === route[preview])?.label}</strong><p>{blockDetail(flow.nodes.find(n => n.id === route[preview])!)}{flow.nodes.find(n => n.id === route[preview])?.kind === 'capture' ? ' · All iterations represented by this step.' : ''}</p></div><button onClick={() => setPreview(p => p + 1 < route.length ? p + 1 : -1)}>{preview + 1 === route.length ? 'Finish trace' : 'Next step →'}</button><button aria-label="Close trace" onClick={() => setPreview(-1)}>×</button></div>}
      </div>
      <aside className="fs-inspector" aria-label="Block settings"><div className="fs-aside-heading"><span>{selected ? 'Block settings' : selectedEdge ? 'Connection' : 'Your flow'}</span><small>{selected ? BLOCKS[selected.kind].group : 'INSPECT'}</small></div>
        {selected ? <><div className={`fs-inspector-symbol fs-${selected.kind}`}><span className="fs-glyph">{BLOCKS[selected.kind].glyph}</span></div><h3>{BLOCKS[selected.kind].title}</h3><p>{BLOCKS[selected.kind].detail}.</p><label>Block name<input value={selected.label} onChange={e => patchNode({ label: e.target.value })}/></label><label>{selected.kind === 'capture' ? 'Seconds per exposure' : selected.kind === 'frame' ? 'Target' : selected.kind === 'weather' || selected.kind === 'wait' ? 'Condition' : 'Setting'}<input type={selected.kind === 'capture' ? 'number' : 'text'} min={selected.kind === 'capture' ? 1 : undefined} max={selected.kind === 'capture' ? 3600 : undefined} value={selected.value} onChange={e => patchNode({ value: e.target.value })}/></label>{(selected.kind === 'capture' || selected.kind === 'wait') && <label>{selected.kind === 'capture' ? 'Repeat count' : 'Timeout in minutes'}<input type="number" min="1" max="9999" value={selected.count} onChange={e => patchNode({ count: Number(e.target.value) })}/></label>}<div className="fs-exits"><small>EXITS</small>{outputs(selected.kind).map(p => <div key={p}><b>{p}</b><span>{flow.nodes.find(n => n.id === flow.edges.find(e => e.from === selected.id && e.port === p)?.to)?.label || 'Not connected'}</span></div>)}{!outputs(selected.kind).length && <p>This path ends here.</p>}</div><button className="fs-delete" onClick={remove}>Remove block</button></> : selectedEdge ? <><h3>Follow this connection</h3><p>{flow.nodes.find(n => n.id === selectedEdge.from)?.label}</p><div className="fs-connection-detail">{selectedEdge.port} ↓</div><p>{flow.nodes.find(n => n.id === selectedEdge.to)?.label}</p><button className="fs-delete" onClick={remove}>Remove connection</button></> : <><div className="fs-empty-symbol">⌁</div><h3>Make your own connections.</h3><p>Pick a block to tune it. Connect its output to the next block’s input.</p><p>A cloudy night can take a different path. A timeout can take the rig safely home.</p><button onClick={() => { commit(makeFlow(target, frames, exposure)); setSelection(null); setPending(null); setNotice('Example rebuilt with the current target and exposure settings. Undo restores your previous draft.'); }}>Use tonight’s target</button><small className="fs-subtle">Replaces this draft with the example. Undo brings it back.</small></>}
        <div className="fs-scenario"><h3>Try a different night</h3><p>Choose the conditions for your trace.</p><label>Sky at the decision<select value={sky} onChange={e => { setSky(e.target.value as typeof sky); setPreview(-1); }}><option value="clear">Clear skies</option><option value="cloudy">Clouds roll in</option></select></label><label>If the flow waits<select value={recovery} onChange={e => { setRecovery(e.target.value as typeof recovery); setPreview(-1); }}><option value="clear">Sky clears in time</option><option value="timeout">Timeout is reached</option></select></label></div>
      </aside>
    </div>
    <div className="fs-status"><span role="status">{notice}</span><span>{saved}</span></div>
    <p className="fs-prototype-note">Interactive design draft · Wiring and traces work locally; this graph does not run equipment. <a href="#/session/flows">Open the current execution editor ↗</a></p>
    {gesture?.mode === 'add' && gesture.moved && <div className="fs-drag-ghost" style={{ left: gesture.x + 12, top: gesture.y + 12 }}>{BLOCKS[gesture.kind].glyph} {BLOCKS[gesture.kind].title}</div>}
  </section>;
}
