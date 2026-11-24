"use strict";

globalThis.PiLabModel = (() => {
  // Message names come from the original Pi clients and Spring WebSocket handler.
  const nodes = {
    main: {
      title: "MainPi", role: "버튼 · 센서값 수신", branch: "MainPi", icon: "button",
      outgoing: ["buttonSignal"], incoming: ["temperature", "humidity", "gas", "light", "dust"],
    },
    fire: {
      title: "화재 감지 Pi", role: "온도 · 가스 · 적외선", branch: "FireDetectionPi", icon: "fire",
      outgoing: ["temperature", "gas", "infra"], incoming: ["fireControl"],
    },
    server: {
      title: "중앙 서버", role: "Spring · WebSocket", branch: "spring", icon: "server",
      outgoing: [
        "fireControl", "dustControl", "lightControl", "blindControl", "temperature", "humidity", "gas", "light", "dust",
      ],
      incoming: [
        "buttonSignal", "temperature", "gas", "infra", "dust", "humidity", "light",
      ],
    },
    air: {
      title: "공기질 Pi", role: "미세먼지 · 온습도", branch: "AirQualityPi", icon: "air",
      outgoing: ["dust", "humidity", "temperature"], incoming: ["dustControl"],
    },
    light: {
      title: "조명 Pi", role: "조도 · 조명 제어", branch: "lightPi", icon: "light",
      outgoing: ["light"], incoming: ["lightControl", "blindControl"],
    },
  };

  const scenarios = {
    normal: {
      title: "정상 통신",
      events: [
        { at: 0, node: "main", kind: "receive", name: "buttonSignal", value: "1" },
        { at: 240, node: "air", kind: "receive", name: "dust", value: 18 },
        { at: 520, node: "light", kind: "receive", name: "light", value: 320 },
        { at: 880, node: "fire", kind: "send", name: "fireControl", value: false },
      ],
    },
    fire: {
      title: "화재 경보",
      events: [
        { at: 0, node: "fire", kind: "receive", name: "infra", value: 790 },
        { at: 480, node: "server", kind: "process", name: "CheckFire()", value: true },
        { at: 1120, node: "fire", kind: "send", name: "fireControl", value: true },
      ],
    },
    disconnect: {
      title: "공기질 Pi 재연결",
      events: [
        { at: 0, node: "air", kind: "disconnect", name: "WebSocket 종료" },
        { at: 1600, node: "air", kind: "connect", name: "WebSocket 재연결" },
        { at: 2200, node: "air", kind: "receive", name: "dust", value: 18 },
      ],
    },
  };

  function initialState() {
    return {
      connections: { main: true, fire: true, air: true, light: true },
      events: [],
    };
  }

  function record(state, event) {
    let connections = state.connections;
    if (event.kind === "connect" || event.kind === "disconnect") {
      connections = { ...connections, [event.node]: event.kind === "connect" };
    }
    return { connections, events: [event, ...state.events] };
  }

  return { nodes, scenarios, initialState, record };
})();
