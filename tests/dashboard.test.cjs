const assert = require('node:assert/strict');
const { test } = require('node:test');

require('../web/model.js');
const { nodes, scenarios, initialState, record } = globalThis.PiLabModel;

test('the four Pis connect to the Spring hub, using fields from the source branches', () => {
  assert.deepEqual(Object.keys(nodes), ['main', 'fire', 'server', 'air', 'light']);
  for (const scenario of Object.values(scenarios)) {
    for (const event of scenario.events) {
      assert.ok(nodes[event.node]);
    }
  }
  for (const id of ['main', 'fire', 'air', 'light']) {
    assert.ok(nodes[id].incoming.length);
    assert.ok(nodes[id].outgoing.length);
  }
});

test('the fire scenario preserves sensor, server decision and command order', () => {
  const events = scenarios.fire.events;
  assert.deepEqual(events.map(({ name }) => name), ['infra', 'CheckFire()', 'fireControl']);
  assert.deepEqual(events.map(({ kind }) => kind), ['receive', 'process', 'send']);
});

test('disconnect and reconnect change only the affected Pi connection', () => {
  const starting = initialState();
  const [disconnect, reconnect] = scenarios.disconnect.events;
  const offline = record(starting, disconnect);
  assert.equal(offline.connections.air, false);
  assert.equal(starting.connections.air, true);
  assert.equal(offline.connections.fire, true);
  const online = record(offline, reconnect);
  assert.equal(online.connections.air, true);
  assert.deepEqual(online.events, [reconnect, disconnect]);
});
