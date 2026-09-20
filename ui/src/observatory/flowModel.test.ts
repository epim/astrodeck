import assert from 'node:assert/strict';
// Use the repository runner's explicit tally (it imports each test module).
export const result = {passed:0,failed:0,total:0};
function test(name:string,fn:()=>void){result.total++;try{fn();result.passed++;}catch(error){result.failed++;console.error(name,error);}}
import { connect, makeFlow, readDraft, trace, validate } from './flowModel';

test('clear sky, recovery, and timeout follow their own connected paths', () => {
  const f = makeFlow('M33', 24, 120);
  assert.deepEqual(validate(f), []);
  assert.deepEqual(trace(f, 'clear', 'clear'), ['start', 'frame', 'weather', 'capture', 'finish']);
  assert.deepEqual(trace(f, 'cloudy', 'clear'), ['start', 'frame', 'weather', 'wait', 'capture', 'finish']);
  assert.deepEqual(trace(f, 'cloudy', 'timeout'), ['start', 'frame', 'weather', 'wait', 'finish']);
});
test('rewiring replaces one exit, preserves other exits, and changes the trace', () => {
  const f = makeFlow('M31', 40, 180);
  const changed = connect(f, 'weather', 'clear', 'finish').flow!;
  assert.equal(changed.edges.length, f.edges.length);
  assert.deepEqual(trace(changed, 'clear', 'clear'), ['start', 'frame', 'weather', 'finish']);
  assert.deepEqual(trace(changed, 'cloudy', 'clear'), trace(f, 'cloudy', 'clear'));
});
test('cycles, self connections, invalid ports and incoming trigger wires are rejected', () => {
  const f = makeFlow('M31', 40, 180);
  for (const [from, port, to] of [['capture', 'next', 'frame'], ['frame', 'next', 'frame'], ['finish', 'next', 'capture'], ['weather', 'oops', 'capture'], ['frame', 'next', 'start']]) assert.ok(connect(f, from, port, to).error);
});
test('an incomplete branch and an orphan block prevent a trace-ready result', () => {
  const f = makeFlow('M31', 40, 180);
  f.edges = f.edges.filter(e => e.port !== 'cloudy');
  assert.ok(validate(f).some(i => i.includes('connect cloudy')));
  assert.ok(validate(f).some(i => i.includes('not connected to the start')));
});
test('invalid exposure settings are reported', () => {
  const f = makeFlow('M31', 1.5, 0);
  assert.ok(validate(f).some(i => i.includes('whole count')));
  assert.ok(validate(f).some(i => i.includes('1–3600')));
});
test('versioned drafts restore; malformed and circular drafts are ignored', () => {
  const graph = makeFlow('M31', 40, 180);
  const serialize = () => JSON.stringify({ format: 'astrodeck-visual-concept', version: 1, graph });
  assert.deepEqual(readDraft(serialize())?.nodes, graph.nodes);
  assert.equal(readDraft('{'), undefined);
  assert.equal(readDraft(JSON.stringify({ graph })), undefined);
  graph.edges.push({ id: 'cycle', from: 'capture', port: 'next', to: 'frame' });
  assert.equal(readDraft(serialize()), undefined);
  graph.nodes[0].x = Infinity;
  assert.equal(readDraft(serialize()), undefined);
});
