// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// w3_5HorizonSuggestionMountDom.test.tsx - WP 3.5 fix round: the learned-horizon
// offer is ON the two surfaces that edit a site's horizon, not only a component
// that exists.
//
//   * the horizon editor sheet (#/next), for the ACTIVE site's line: the offer
//     accepts into the active site, so a sheet editing a saved location that is
//     not the active one does not show it, and accepting reloads the line the
//     sheet draws;
//   * Settings > Observing Site (classic), for every principal that may read it
//     (an operator holds view.site_derived but not config.site_optics, so the
//     card sits outside the site editor's own gate);
//   * a viewer link neither shows nor asks, on both.
//
//   Run directly:  node --import ./test-css-stub.mjs --import tsx <this file>
//
// Named mutants (byte backups, restored by hash):
//   sheet mount deleted      the HorizonSuggestionOffer line in horizon.tsx
//   panel mount deleted      the HorizonSuggestionOffer line in SitePanel.tsx
//   panel mount under canEdit  the same line moved inside `canEdit &&`

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

const OFFER = {
  id: 'abc123def456', frames: 6, bins: 3, nights: 2,
  points: [[0, 0], [120.01, 40], [149.99, 40], [151, 0]],
  regions: [{ az_from: 120, az_to: 150, alt_min: 40, alt_max: 40, frames: 6 }],
};
const calls: { method: string; url: string }[] = [];
let offerNow: unknown = OFFER;
const ok = (data: unknown) => ({ ok: true, status: 200, statusText: 'OK', json: async () => data });
g.fetch = async (url: string, init?: { method?: string }) => {
  const u = String(url), method = (init?.method ?? 'GET').toUpperCase();
  calls.push({ method, url: u });
  if (u.includes('/horizon-suggestion/accept')) { offerNow = null; return ok({ accepted: OFFER.id, version: 2 }); }
  if (u.includes('/horizon-suggestion')) return ok({ drawn: true, suggestion: offerNow });
  if (u.includes('/api/locations') && method === 'GET') {
    return ok([{ id: 'other', name: 'Elsewhere', latitude: 1.5, longitude: 2.5, elevation_m: 0, horizon_points: [[0, 5]] }]);
  }
  if (u.includes('/api/site') && method === 'GET') return ok({ site: { name: 'Active', is_default: false, horizon_min_deg: 15, horizon_points: [[0, 5], [180, 5]] }, version: 1 });
  return ok({});
};

const { createElement, act } = await import('react');
const { createRoot } = await import('react-dom/client');
const { useStore } = await import('../../../../../store');
const { HorizonSheet } = await import('../horizon');
const SitePanel = (await import('../../../../../components/settings/SitePanel')).default;

let passed = 0, failed = 0;
const failures: string[] = [];
async function test(name: string, fn: () => void | Promise<void>): Promise<void> {
  try { await fn(); passed++; console.log(`PASS ${name}`); }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); console.log(`FAIL ${name}\n     ${(e as Error).message.split('\n')[0]}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }
const settle = async () => { for (let i = 0; i < 4; i++) await act(async () => { await new Promise(r => setTimeout(r, 0)); }); };

const ADMIN = ['view.status', 'view.site_derived', 'view.site_precise', 'config.site_optics', 'config.safety'];
const OPERATOR = ['view.status', 'view.site_derived', 'control.mount'];
const VIEWER = ['view.status', 'view.preview'];
const seed = (role: string, caps: string[]) => useStore.setState({
  principal: { role, email: `${role}@example.test`, caps },
  wsPhase: 'up', equipConnected: true,
  status: { connected: {}, looping: false, mode: 'sim', busy: null, busy_lanes: [] },
  config: { version: 1, site: { name: 'Active', latitude: 12.5, longitude: 34.5, elevation_m: 10, is_default: false, horizon_min_deg: 15 }, safety: { horizon: [[0, 5], [180, 5]] } },
} as never);

async function mount(el: any) {
  calls.length = 0;
  offerNow = OFFER;
  const container = win.document.createElement('div');
  win.document.body.appendChild(container);
  const root = createRoot(container);
  await act(async () => { root.render(el); });
  await settle();
  return { container, unmount: () => act(async () => { root.unmount(); container.remove(); }) };
}
const card = (c: any) => c.querySelector('[data-testid="horizon-offer"]');
const asked = () => calls.filter(c => c.url.includes('/horizon-suggestion')).length;

await test('the horizon sheet shows the offer for the active site', async () => {
  seed('admin', ADMIN);
  const sheet = await mount(createElement(HorizonSheet, { params: { site: 'current' }, depth: 0 } as any));
  assert(!!card(sheet.container), 'no offer on the active site\'s horizon sheet');
  await sheet.unmount();
});

await test('accepting on the sheet reloads the line it draws', async () => {
  seed('admin', ADMIN);
  const sheet = await mount(createElement(HorizonSheet, { params: { site: 'current' }, depth: 0 } as any));
  const before = calls.filter(c => c.url.includes('/api/site') && c.method === 'GET').length;
  const button = sheet.container.querySelector('[data-testid="horizon-offer-accept"]');
  await act(async () => { button.dispatchEvent(new win.MouseEvent('click', { bubbles: true, cancelable: true })); });
  await settle();
  const after = calls.filter(c => c.url.includes('/api/site') && c.method === 'GET').length;
  assert(after === before + 1, `the sheet read the site ${before} then ${after} times; it must read the stored line again`);
  assert(!card(sheet.container), 'the offer is still showing after it was stored');
  await sheet.unmount();
});

await test('a sheet editing a saved location that is not the active one shows no offer', async () => {
  seed('admin', ADMIN);
  const sheet = await mount(createElement(HorizonSheet, { params: { site: 'other' }, depth: 0 } as any));
  assert(!card(sheet.container), 'the offer (which stores into the active site) is on another location\'s sheet');
  await sheet.unmount();
});

await test('a viewer\'s horizon sheet neither shows nor asks', async () => {
  seed('viewer', VIEWER);
  const sheet = await mount(createElement(HorizonSheet, { params: { site: 'current' }, depth: 0 } as any));
  assert(!card(sheet.container) && asked() === 0, `a viewer's sheet asked ${asked()} times`);
  await sheet.unmount();
});

await test('Settings > Observing Site shows the offer to an admin', async () => {
  seed('admin', ADMIN);
  const panel = await mount(createElement(SitePanel));
  assert(!!card(panel.container), 'no offer on the Observing Site panel');
  await panel.unmount();
});

await test('Settings > Observing Site shows the offer to an operator, who cannot edit the site', async () => {
  seed('operator', OPERATOR);
  const panel = await mount(createElement(SitePanel));
  assert(!!card(panel.container), 'no offer for an operator (it must sit outside the site editor\'s gate)');
  await panel.unmount();
});

await test('Settings > Observing Site neither shows nor asks for a viewer', async () => {
  seed('viewer', VIEWER);
  const panel = await mount(createElement(SitePanel));
  assert(!card(panel.container) && asked() === 0, `a viewer's panel asked ${asked()} times`);
  await panel.unmount();
});

console.log(`w3_5HorizonSuggestionMountDom: ${passed}/${passed + failed} passed`);
for (const f of failures) console.log('  ' + f);
export const result = { passed, failed, total: passed + failed };
