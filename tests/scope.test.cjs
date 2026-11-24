const assert = require('node:assert/strict');
const { test } = require('node:test');

require('../web/scope.js');
const { latestTrace, timebase } = globalThis.PiLabScope;

test('scope pairs only the latest input with its matching output', () => {
  const first = {event_id: 'a', stage: 'INPUT', timestamp_ns: 9000000000};
  const applied = {event_id: 'a', stage: 'APPLIED', timestamp_ns: 9050000000, observed_output: 0};
  const repeated = {event_id: 'b', stage: 'INPUT', timestamp_ns: 10000000000};

  assert.equal(latestTrace([]), null);
  assert.deepEqual(latestTrace([first, applied]), {input: first, output: applied, elapsedMs: 50});
  assert.deepEqual(latestTrace([first, applied, repeated]), {input: repeated, output: null, elapsedMs: null});
  assert.equal(latestTrace([repeated, {...applied, event_id: 'b'}]).elapsedMs, null);
});

test('scope puts a completed edge on a measured scale and keeps pending time empty', () => {
  assert.equal(timebase(null), null);
  assert.deepEqual(timebase(1500), {endMs: 2000, outputPercent: 76.25, ticks: [0, 500, 1000, 1500, 2000]});
  assert.ok(timebase(0).outputPercent === 5);
});
