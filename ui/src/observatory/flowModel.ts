export type BlockKind = 'start' | 'frame' | 'focus' | 'guide' | 'capture' | 'weather' | 'wait' | 'finish';
export type Block = { id: string; kind: BlockKind; x: number; y: number; label: string; value: string; count: number };
export type Wire = { id: string; from: string; port: string; to: string };
export type Flow = { nodes: Block[]; edges: Wire[] };
export const BLOCKS: Record<BlockKind, { title: string; group: string; glyph: string; detail: string; value: string; count: number }> = {
  start: { title: 'When night begins', group: 'Triggers', glyph: '☾', detail: 'Begin at your chosen time', value: 'Astronomical dusk', count: 1 },
  frame: { title: 'Find & frame', group: 'Actions', glyph: '⌖', detail: 'Center a target in the camera', value: 'M31', count: 1 },
  focus: { title: 'Find sharp focus', group: 'Actions', glyph: '◎', detail: 'Measure stars and adjust focus', value: 'Before imaging', count: 1 },
  guide: { title: 'Start guiding', group: 'Actions', glyph: '≋', detail: 'Settle the mount before imaging', value: 'Settle below 1.0″', count: 1 },
  capture: { title: 'Repeat exposures', group: 'Actions', glyph: '▣', detail: 'An exposure loop in one block', value: '180', count: 40 },
  weather: { title: 'Is the sky clear?', group: 'Logic', glyph: '◇', detail: 'Choose a path from a condition', value: 'Cloud cover below 25%', count: 25 },
  wait: { title: 'Wait for clear skies', group: 'Logic', glyph: '◷', detail: 'Continue if clear; finish if timed out', value: 'Cloud cover below 25%', count: 20 },
  finish: { title: 'Finish gently', group: 'Actions', glyph: '⌂', detail: 'Park the mount and warm the camera', value: 'Park & warm camera', count: 1 },
};
export const outputs = (kind: BlockKind) => kind === 'finish' ? [] : kind === 'weather' ? ['clear', 'cloudy'] : kind === 'wait' ? ['clear', 'timeout'] : ['next'];
export const blockDetail = (n: Block) => n.kind === 'capture' ? `${n.count} × ${n.value}s · repeat exposures` : n.kind === 'wait' ? `${n.count} min maximum · ${n.value}` : n.value;
export function makeFlow(target: string, count: number, seconds: number): Flow {
  const node = (id: string, kind: BlockKind, x: number, y: number, extra: Partial<Block> = {}): Block => ({ id, kind, x, y, label: BLOCKS[kind].title, value: BLOCKS[kind].value, count: BLOCKS[kind].count, ...extra });
  return { nodes: [node('start', 'start', 55, 190), node('frame', 'frame', 340, 190, { value: target }), node('weather', 'weather', 625, 190), node('capture', 'capture', 920, 95, { value: String(seconds), count }), node('wait', 'wait', 920, 415), node('finish', 'finish', 1230, 190)], edges: [
    { id: 'a', from: 'start', port: 'next', to: 'frame' }, { id: 'b', from: 'frame', port: 'next', to: 'weather' },
    { id: 'c', from: 'weather', port: 'clear', to: 'capture' }, { id: 'd', from: 'weather', port: 'cloudy', to: 'wait' },
    { id: 'e', from: 'capture', port: 'next', to: 'finish' }, { id: 'f', from: 'wait', port: 'clear', to: 'capture' }, { id: 'g', from: 'wait', port: 'timeout', to: 'finish' },
  ] };
}
export function connect(flow: Flow, from: string, port: string, to: string): { flow?: Flow; error?: string } {
  const source = flow.nodes.find(n => n.id === from), dest = flow.nodes.find(n => n.id === to);
  if (!source || !dest || dest.kind === 'start' || !outputs(source.kind).includes(port)) return { error: 'Connect an output to an input.' };
  if (from === to) return { error: 'A block cannot connect to itself. Use Repeat exposures for a loop.' };
  const edges = flow.edges.filter(e => !(e.from === from && e.port === port));
  const seen = new Set<string>();
  const reaches = (id: string): boolean => { if (id === from) return true; if (seen.has(id)) return false; seen.add(id); return edges.filter(e => e.from === id).some(e => reaches(e.to)); };
  if (reaches(to)) return { error: 'That wire creates an endless loop. Use a bounded repeat block.' };
  return { flow: { ...flow, edges: [...edges, { id: `${from}:${port}`, from, port, to }] } };
}
export function validate(flow: Flow): string[] {
  const issues: string[] = [], starts = flow.nodes.filter(n => n.kind === 'start');
  if (starts.length !== 1) issues.push('Use exactly one When night begins block.');
  if (!flow.nodes.some(n => n.kind === 'finish')) issues.push('Add a Finish gently block.');
  for (const n of flow.nodes) {
    for (const p of outputs(n.kind)) if (!flow.edges.some(e => e.from === n.id && e.port === p)) issues.push(`${n.label}: connect ${p}.`);
    if (n.kind === 'capture' && (!Number.isFinite(Number(n.value)) || Number(n.value) < 1 || Number(n.value) > 3600)) issues.push(`${n.label}: exposure must be 1–3600 seconds.`);
    if (!Number.isInteger(n.count) || n.count < 1 || n.count > 9999) issues.push(`${n.label}: enter a whole count from 1 to 9999.`);
    if (!n.label.trim() || !n.value.trim()) issues.push(`${BLOCKS[n.kind].title}: fill in its name and setting.`);
  }
  const seen = new Set<string>(), active = new Set<string>();
  let cycle = false;
  const visit = (id: string) => { if (active.has(id)) { cycle = true; return; } if (seen.has(id)) return; seen.add(id); active.add(id); flow.edges.filter(e => e.from === id).forEach(e => visit(e.to)); active.delete(id); };
  starts.forEach(n => visit(n.id));
  flow.nodes.filter(n => !seen.has(n.id)).forEach(n => issues.push(`${n.label} is not connected to the start.`));
  if (cycle) issues.push('Remove the circular connection. Use a bounded repeat block.');
  return issues;
}
export function trace(flow: Flow, sky: 'clear' | 'cloudy', recovery: 'clear' | 'timeout'): string[] {
  const path: string[] = []; let n = flow.nodes.find(n => n.kind === 'start');
  while (n && !path.includes(n.id)) { path.push(n.id); const port = n.kind === 'weather' ? sky : n.kind === 'wait' ? recovery : 'next'; const edge = flow.edges.find(e => e.from === n!.id && e.port === port); n = edge ? flow.nodes.find(b => b.id === edge.to) : undefined; }
  return path;
}
// Local drafts are versioned separately from executable server flows.
export function readDraft(raw: string | null): Flow | undefined {
  try {
    const data = JSON.parse(raw || 'null'); const f = data?.graph;
    if (data?.format !== 'astrodeck-visual-concept' || data.version !== 1 || !Array.isArray(f?.nodes) || !Array.isArray(f?.edges) || f.nodes.length > 200 || f.edges.length > 400) return;
    if (!f.nodes.every((n: Block) => n && typeof n.id === 'string' && Object.hasOwn(BLOCKS, n.kind) && Number.isFinite(n.x) && n.x >= 0 && n.x <= 5000 && Number.isFinite(n.y) && n.y >= 0 && n.y <= 5000 && typeof n.label === 'string' && typeof n.value === 'string' && Number.isFinite(n.count))) return;
    const ids = new Set(f.nodes.map((n: Block) => n.id)); if (ids.size !== f.nodes.length) return;
    let built: Flow = { nodes: f.nodes, edges: [] };
    for (const e of f.edges) { if (!e || typeof e.id !== 'string' || built.edges.some(w => w.from === e.from && w.port === e.port)) return; const result = connect(built, e.from, e.port, e.to); if (!result.flow) return; built = result.flow; }
    return built;
  } catch { return; }
}
