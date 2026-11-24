"use strict";

(() => {
  function latestTrace(events) {
    const inputIndex = events.findLastIndex(event => event.stage === "INPUT");
    if (inputIndex < 0) return null;
    const input = events[inputIndex];
    const output = events.slice(inputIndex + 1).find(event =>
      event.event_id === input.event_id && event.stage === "APPLIED" &&
      event.timestamp_ns >= input.timestamp_ns) || null;
    return {input, output, elapsedMs: output ? (output.timestamp_ns - input.timestamp_ns) / 1e6 : null};
  }

  function timebase(elapsedMs) {
    if (elapsedMs === null) return null;
    // Keep pre-trigger space on the graph and choose a legible time per division.
    const target = Math.max(1, (elapsedMs * 1.15) / 4);
    const power = 10 ** Math.floor(Math.log10(target));
    const division = [1, 2, 5, 10].find(step => step * power >= target) * power;
    const endMs = 4 * division;
    return {
      endMs,
      outputPercent: 5 + 95 * elapsedMs / endMs,
      ticks: [0, division, 2 * division, 3 * division, endMs],
    };
  }

  globalThis.PiLabScope = {latestTrace, timebase};
})();
