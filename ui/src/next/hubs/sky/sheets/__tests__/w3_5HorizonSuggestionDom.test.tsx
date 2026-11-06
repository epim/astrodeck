// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// w3_5HorizonSuggestionDom.test.tsx - WP 3.5 (#599, #1093): the Site page shows
// the horizon the rig learned from obstructed frames, and stores it only when
// the operator presses accept.
//
// WHAT IS GRADED
//   * the offer shows its evidence and ONE accept control, and the page has
//     asked for nothing but a read until that control is pressed (D-07);
//   * pressing it stores the offer the page showed (its id rides the request),
//     reloads, and tells the editor beside it;
//   * an offer that moved under the operator is not stored: the page says so
//     and shows the new one;
//   * a viewer link neither asks nor sees (the regions are computed from the
//     site);
//   * with no horizon drawn and no offer the page says what to do about it
//     (#1093); with a horizon and no offer it says nothing.
//
//   Run directly:  node --import ./test-css-stub.mjs --import tsx <this file>
//
// Named mutants (edits to HorizonSuggestionOffer.tsx, from a byte backup,
// restored by hash):
//   applied without the press   the load effect also calls acceptHorizonSuggestion
//   viewer asks anyway          the `cleared` guard on the load effect removed

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
const MOVED = { ...OFFER, id: 'ffffffffffff', frames: 9 };

type Reply = { status: number; body: unknown };
const calls: { method: string; url: string }[] = [];
let readReplies: Reply[] = [];
let acceptReply: Reply = { status: 200, body: { accepted: OFFER.id, version: 2 } };
g.fetch = async (url: string, init?: { method?: string }) => {
  const u = String(url), method = (init?.method ?? 'GET').toUpperCase();
  calls.push({ method, url: u });
  const reply = u.includes('/accept') ? acceptReply : (readReplies.length > 1 ? readReplies.shift()! : readReplies[0]);
  return { ok: reply.status < 400, status: reply.status, statusText: String(reply.status), json: async () => reply.body };
};

const { createElement, act } = await import('react');
const { createRoot } = await import('react-dom/client');
const { useStore } = await import('../../../../../store');
const { HorizonSuggestionOffer } = await import('../HorizonSuggestionOffer');

let passed = 0, failed = 0;
const failures: string[] = [];
async function test(name: string, fn: () => void | Promise<void>): Promise<void> {
  try { await fn(); passed++; console.log(`PASS ${name}`); }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); console.log(`FAIL ${name}\n     ${(e as Error).message.split('\n')[0]}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }
const settle = async () => { for (let i = 0; i < 4; i++) await act(async () => { await new Promise(r => setTimeout(r, 0)); }); };
const seed = (role: string, caps: string[]) => useStore.setState({
  principal: { role, email: `${role}@example.test`, caps },
  wsPhase: 'up',
} as never);
const OPERATOR = ['view.status', 'view.site_derived', 'control.mount'];
const VIEWER = ['view.status', 'view.preview'];

async function mount(onAccepted?: () => void) {
  calls.length = 0;
  const container = win.document.createElement('div');
  win.document.body.appendChild(container);
  const root = createRoot(container);
  await act(async () => { root.render(createElement(HorizonSuggestionOffer, { onAccepted })); });
  await settle();
  return { container, unmount: () => act(async () => { root.unmount(); container.remove(); }) };
}
const byId = (c: any, id: string) => c.querySelector(`[data-testid="${id}"]`);
const press = async (container: any) => {
  const button = byId(container, 'horizon-offer-accept');
  assert(!!button, 'no accept control');
  await act(async () => { button.dispatchEvent(new win.MouseEvent('click', { bubbles: true, cancelable: true })); });
  await settle();
};

await test('an offer shows its evidence and one accept control, and the page has only read', async () => {
  seed('operator', OPERATOR);
  readReplies = [{ status: 200, body: { drawn: true, suggestion: OFFER } }];
  const sheet = await mount();
  assert(!!byId(sheet.container, 'horizon-offer'), 'no offer card');
  const summary = byId(sheet.container, 'horizon-offer-summary').textContent;
  assert(/6 frames over 2 nights/.test(summary) && /1 direction/.test(summary), `summary: ${summary}`);
  assert(byId(sheet.container, 'horizon-offer-regions').children.length === 1, 'one region line expected');
  assert(sheet.container.querySelectorAll('button').length === 1, 'exactly one control expected');
  assert(calls.length === 1 && calls[0].method === 'GET', `the page made ${JSON.stringify(calls)} before any press`);
  await sheet.unmount();
});

await test('pressing accept stores the offer that was shown, reloads, and tells the editor', async () => {
  seed('operator', OPERATOR);
  readReplies = [{ status: 200, body: { drawn: true, suggestion: OFFER } }, { status: 200, body: { drawn: true, suggestion: null } }];
  let told = 0;
  const sheet = await mount(() => { told++; });
  await press(sheet.container);
  const posts = calls.filter(c => c.method === 'POST');
  assert(posts.length === 1, `${posts.length} writes`);
  assert(posts[0].url.endsWith(`/accept?suggestion_id=${OFFER.id}`), `the write named ${posts[0].url}`);
  assert(told === 1, `the editor was told ${told} times`);
  assert(!byId(sheet.container, 'horizon-offer'), 'the offer is still showing after it was stored');
  await sheet.unmount();
});

await test('an offer that moved is not stored: the page says so and shows the new one', async () => {
  seed('operator', OPERATOR);
  readReplies = [{ status: 200, body: { drawn: true, suggestion: OFFER } }, { status: 200, body: { drawn: true, suggestion: MOVED } }];
  acceptReply = { status: 409, body: { detail: { code: 'suggestion_changed' } } };
  let told = 0;
  const sheet = await mount(() => { told++; });
  await press(sheet.container);
  const problem = byId(sheet.container, 'horizon-offer-problem');
  assert(!!problem && /changed while you were looking/.test(problem.textContent), `problem: ${problem && problem.textContent}`);
  assert(/9 frames/.test(byId(sheet.container, 'horizon-offer-summary').textContent), 'the new offer was not shown');
  assert(told === 0, 'the editor was told of a write that did not happen');
  acceptReply = { status: 200, body: { accepted: OFFER.id, version: 2 } };
  await sheet.unmount();
});

await test('a viewer link neither asks nor sees', async () => {
  seed('viewer', VIEWER);
  readReplies = [{ status: 200, body: { drawn: true, suggestion: OFFER } }];
  const sheet = await mount();
  assert(calls.length === 0, `a viewer made ${JSON.stringify(calls)}`);
  assert(sheet.container.textContent === '', `a viewer sees: ${sheet.container.textContent}`);
  await sheet.unmount();
});

await test('no horizon and no offer says what the operator can do about it', async () => {
  seed('operator', OPERATOR);
  readReplies = [{ status: 200, body: { drawn: false, suggestion: null } }];
  const sheet = await mount();
  const guide = byId(sheet.container, 'horizon-offer-guide');
  assert(!!guide, 'no guidance');
  assert(/Draw the line/.test(guide.textContent) && /scan your surroundings/.test(guide.textContent) && /offers a line here/.test(guide.textContent),
    `guidance: ${guide.textContent}`);
  assert(sheet.container.querySelectorAll('button').length === 0, 'a control with nothing to accept');
  await sheet.unmount();
});

await test('a drawn horizon and no offer says nothing', async () => {
  seed('operator', OPERATOR);
  readReplies = [{ status: 200, body: { drawn: true, suggestion: null } }];
  const sheet = await mount();
  assert(sheet.container.textContent === '', `the page said: ${sheet.container.textContent}`);
  await sheet.unmount();
});

console.log(`w3_5HorizonSuggestionDom: ${passed}/${passed + failed} passed`);
for (const f of failures) console.log('  ' + f);
export const result = { passed, failed, total: passed + failed };
