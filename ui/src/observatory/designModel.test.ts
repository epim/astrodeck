import assert from 'node:assert/strict';
// Use the repository runner's explicit tally (it imports each test module).
export const result = {passed:0,failed:0,total:0};
function test(name:string,fn:()=>void){result.total++;try{fn();result.passed++;}catch(error){result.failed++;console.error(name,error);}}
import { INITIAL_SESSION, sessionReducer } from './designModel';

test('the example cannot start until preflight is ready', () => {
  assert.deepEqual(sessionReducer(INITIAL_SESSION, { type: 'start', ready: false, total: 20 }), INITIAL_SESSION);
  assert.deepEqual(sessionReducer(INITIAL_SESSION, { type: 'start', ready: true, total: 20 }), { phase: 'running', connected: true, accepted: 0, total: 20 });
});
test('user pause cannot be cleared by weather recovery or by accepting a frame', () => {
  const running = sessionReducer(INITIAL_SESSION, { type: 'start', ready: true, total: 20 });
  const paused = sessionReducer(running, { type: 'pause' });
  assert.equal(paused.phase, 'paused');
  for (const type of ['clear', 'cloud', 'frame'] as const) assert.deepEqual(sessionReducer(paused, { type }), paused);
  assert.equal(sessionReducer(paused, { type: 'resume' }).phase, 'running');
});
test('cloud hold stops progress until explicit example recovery', () => {
  const running = sessionReducer(INITIAL_SESSION, { type: 'start', ready: true, total: 20 });
  const held = sessionReducer(running, { type: 'cloud' });
  assert.equal(held.phase, 'weather');
  assert.deepEqual(sessionReducer(held, { type: 'frame' }), held);
  assert.equal(sessionReducer(held, { type: 'clear' }).phase, 'running');
});
test('connection loss preserves last-known state and rejects commands', () => {
  const running = sessionReducer(INITIAL_SESSION, { type: 'start', ready: true, total: 20 });
  const offline = sessionReducer(running, { type: 'disconnect' });
  for (const type of ['frame', 'pause', 'stop', 'finish', 'cloud'] as const) assert.deepEqual(sessionReducer(offline, { type }), offline);
  assert.equal(offline.phase, 'running');
  assert.deepEqual(sessionReducer(offline, { type: 'reconnect' }), running);
});
test('completion is bounded, while a stopped partial session keeps accepted data', () => {
  const running = sessionReducer(INITIAL_SESSION, { type: 'start', ready: true, total: 2 });
  const first = sessionReducer(running, { type: 'frame' });
  const stopped = sessionReducer(first, { type: 'stop' });
  assert.equal(stopped.accepted, 1); assert.equal(stopped.phase, 'stopped');
  assert.deepEqual(sessionReducer(stopped, { type: 'frame' }), stopped);
  const complete = sessionReducer(first, { type: 'frame' });
  assert.equal(complete.phase, 'complete'); assert.equal(complete.accepted, 2);
  assert.deepEqual(sessionReducer(complete, { type: 'frame' }), complete);
});
