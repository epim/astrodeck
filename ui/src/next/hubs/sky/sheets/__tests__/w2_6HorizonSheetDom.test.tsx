// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// w2_6HorizonSheetDom.test.tsx - WP 2.6 (#1117): the horizon editor draws and
// saves what each bin of the line IS.
//
// A scan's bins that nothing measured are saved as points at 90 and used to be
// indistinguishable from a wall once saved. The sheet now loads the per-bin
// status a saved location (or the active site's safety config) carries, draws
// the bins nothing measured, writes the status back with an unedited line, and
// sends none with an edited one: the status describes one particular line.
//
//   Run directly:  npx tsx src/next/hubs/sky/sheets/__tests__/w2_6HorizonSheetDom.test.tsx
//
// Named mutants (edits to horizon.tsx, from byte backups, restored by hash):
//   status dropped on save   `horizon_bins: nextBins` removed from the PUT body
//   stale status drawn       `liveBins` taken as `bins` without `binsDescribe`
//   status not loaded        `setBins(parseSavedBins(...))` for a location removed

/* eslint-disable @typescript-eslint/no-explicit-any */
const { JSDOM } = await import('jsdom');
const dom = new JSDOM('<!doctype html><html><body><div id="root"></div></body></html>', { url: 'http://local/', pretendToBeVisual: true });
const win = dom.window as any;
Object.defineProperty(win, 'isSecureContext', { value: false, configurable: true });
win.matchMedia = () => ({ matches: false, addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {} });
win.WebSocket = class { close() {} addEventListener() {} send() {} };
const g = globalThis as any;
for (const k of ['window', 'document', 'navigator', 'HTMLElement', 'HTMLInputElement', 'Element', 'Node', 'Event', 'CustomEvent',
  'MouseEvent', 'KeyboardEvent', 'localStorage', 'sessionStorage', 'getComputedStyle', 'matchMedia', 'WebSocket',
  'requestAnimationFrame', 'cancelAnimationFrame']) {
  Object.defineProperty(g, k, { value: k === 'window' ? win : win[k], writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;

const { binsFromTrace, pointsFromBins } = await import('../horizonBins');
// Six bins of 60 degrees: two measured walls and one bin nothing measured.
const BINS = binsFromTrace([5, 5, 30, 5, 5, 5], [3]);
const POINTS = pointsFromBins(BINS).map(p => [p.az, p.alt] as [number, number]);
const BODY = { name: 'Test field', latitude: 12.5, longitude: 34.5, elevation_m: 10, horizon_min_deg: 15 };

const locations = new Map<string, any>([
  ['fresh', { id: 'fresh', ...BODY, horizon_points: POINTS, horizon_bins: BINS }],
  ['stale', { id: 'stale', ...BODY, horizon_points: POINTS.map(([az, alt]) => [az, alt + 4]), horizon_bins: BINS }],
  ['bare', { id: 'bare', ...BODY, horizon_points: POINTS }],
]);
const puts: any[] = [];
const posts: any[] = [];
let activeBins: unknown = null;
const ok = (data: unknown) => ({ ok: true, status: 200, statusText: 'OK', json: async () => data });
g.fetch = async (url: string, init?: { method?: string; body?: string }) => {
  const u = String(url), method = (init?.method ?? 'GET').toUpperCase();
  if (u.includes('/api/locations') && method === 'GET') return ok(Array.from(locations.values()));
  const put = /\/api\/locations\/([^/]+)$/.exec(u);
  if (put && method === 'PUT') {
    const body = JSON.parse(init!.body!);
    puts.push(body);
    const merged = { ...locations.get(put[1]), ...body, id: put[1] };
    locations.set(put[1], merged);
    return ok(merged);
  }
  if (u.includes('/api/site') && method === 'GET') return ok({ site: { name: 'Active', is_default: false, horizon_min_deg: 15 }, version: 1 });
  if (u.includes('/api/config') && method === 'POST') { posts.push(JSON.parse(init!.body!)); return ok({ version: 2 }); }
  if (u.includes('/api/config') && method === 'GET') return ok({ version: 2, site: { is_default: true, horizon_min_deg: 15 } });
  return ok({});
};

const { createElement, act } = await import('react');
const { createRoot } = await import('react-dom/client');
const { useStore } = await import('../../../../../store');
const { HorizonSheet } = await import('../horizon');

let passed = 0, failed = 0;
const failures: string[] = [];
async function test(name: string, fn: () => void | Promise<void>): Promise<void> {
  try { await fn(); passed++; console.log(`PASS ${name}`); }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); console.log(`FAIL ${name}\n     ${(e as Error).message.split('\n')[0]}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }
const settle = async () => { for (let i = 0; i < 3; i++) await act(async () => { await new Promise(r => setTimeout(r, 0)); }); };
const seed = () => useStore.setState({
  principal: { role: 'admin', email: 'admin@example.test', caps: ['view.status', 'config.site_optics', 'config.safety', 'view.site_precise'] },
  wsPhase: 'up', equipConnected: true,
  status: { connected: {}, looping: false, mode: 'sim', busy: null, busy_lanes: [] },
  config: { version: 1, site: { is_default: true, horizon_min_deg: 15 }, safety: { horizon: POINTS, horizon_bins: activeBins } },
} as never);

async function mount(params: Record<string, string>, extra: Record<string, unknown> = {}) {
  seed();
  const container = win.document.createElement('div');
  win.document.body.appendChild(container);
  const root = createRoot(container);
  await act(async () => { root.render(createElement(HorizonSheet, { params, depth: 0, ...extra } as any)); });
  await settle();
  return { container, unmount: () => act(async () => { root.unmount(); container.remove(); }) };
}
const statusRects = (container: any) => Array.from(container.querySelectorAll('[data-testid="horizon-bin-status"]')) as any[];
const press = async (container: any, re: RegExp) => {
  const button = Array.from(container.querySelectorAll('button')).find((b: any) => re.test(b.textContent || '')) as any;
  assert(!!button, `no button ${re}`);
  await act(async () => { button.dispatchEvent(new win.MouseEvent('click', { bubbles: true, cancelable: true })); });
  await settle();
};

await test('the bins nothing measured are drawn, with their status, and the measured ones are not', async () => {
  const sheet = await mount({ site: 'fresh' });
  const rects = statusRects(sheet.container);
  assert(rects.length === 1, `${rects.length} unmeasured bins drawn, there is one`);
  assert(rects[0].getAttribute('data-status') === 'unknown' && rects[0].getAttribute('data-bin') === '3', 'wrong bin or status');
  await sheet.unmount();
});

await test('a status that no longer describes the line is not drawn over it', async () => {
  const sheet = await mount({ site: 'stale' });
  assert(statusRects(sheet.container).length === 0, 'a stale status was drawn over an edited line');
  await sheet.unmount();
});

await test('a line saved without any status draws none', async () => {
  const sheet = await mount({ site: 'bare' });
  assert(statusRects(sheet.container).length === 0, 'a status appeared from nowhere');
  await sheet.unmount();
});

await test('saving an unedited line writes its status back beside its points', async () => {
  puts.length = 0;
  const sheet = await mount({ site: 'fresh' }, { guided: true });
  await press(sheet.container, /Save horizon/);
  assert(puts.length === 1, `${puts.length} writes`);
  assert(JSON.stringify(puts[0].horizon_bins) === JSON.stringify(BINS), `the write carried ${JSON.stringify(puts[0].horizon_bins)}`);
  assert(JSON.stringify(puts[0].horizon_points) === JSON.stringify(POINTS), 'the points changed');
  await sheet.unmount();
});

await test('an edit by hand is saved with no status: the status described a line that no longer exists', async () => {
  puts.length = 0;
  const sheet = await mount({ site: 'fresh' });
  const strip = sheet.container.querySelector('[data-testid="horizon-strip"]') as any;
  const tap = (type: string) => { const ev = new win.Event(type, { bubbles: true, cancelable: true });
    Object.assign(ev, { pointerId: 1, pointerType: 'mouse', button: 0, buttons: 1, isPrimary: true, clientX: 0.4 * 340, clientY: 120 }); strip.dispatchEvent(ev); };
  await act(async () => { tap('pointerdown'); });
  await act(async () => { tap('pointerup'); });
  await settle();
  assert(puts.length === 1, `${puts.length} writes`);
  assert(puts[0].horizon_bins === null, `an edited line was saved with ${JSON.stringify(puts[0].horizon_bins)}`);
  assert(statusRects(sheet.container).length === 0, 'the status was still drawn over the edited line');
  await sheet.unmount();
});

await test('the active site: its status comes from the safety config and goes back with the line', async () => {
  posts.length = 0;
  activeBins = BINS;
  const sheet = await mount({ site: 'current' }, { guided: true });
  assert(statusRects(sheet.container).length === 1, `${statusRects(sheet.container).length} unmeasured bins drawn on the active site`);
  await press(sheet.container, /Save horizon/);
  assert(posts.length === 1, `${posts.length} config writes`);
  assert(JSON.stringify(posts[0].safety.horizon_bins) === JSON.stringify(BINS), `the safety block carried ${JSON.stringify(posts[0].safety.horizon_bins)}`);
  activeBins = null;
  await sheet.unmount();
});

console.log(`w2_6HorizonSheetDom: ${passed}/${passed + failed} passed`);
for (const f of failures) console.log('  ' + f);
export const result = { passed, failed, total: passed + failed };
