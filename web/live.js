"use strict";

(() => {
  const {latestTrace, timebase} = globalThis.PiLabScope;
  const byId = (id) => document.getElementById(id);
  const controls = byId("live-controls");
  const bay = byId("live-lab");
  const stateLabel = byId("live-status");
  const hint = byId("button-hint");
  const error = byId("live-error");
  const press = document.querySelector('[data-action="button"]');
  const start = controls.querySelector('[data-action="start"]');
  const stop = controls.querySelector('[data-action="stop"]');
  const roles = [...document.querySelectorAll(".monitor-list [data-role]")];
  const statusLabels = {
    idle: "실험 대기", starting: "보드 초기화 중", running: "실험 연결됨",
    applying: "입력 처리 중", stopping: "정리 중", stopped: "실험 정지", failed: "실험 실패",
  };
  const clock = new Intl.DateTimeFormat("ko-KR", {timeStyle: "medium", hourCycle: "h23"});
  let requesting = null;
  let current = null;
  let timer = null;
  let generation = 0;
  let renderedTrace = null;

  function showError(message) {
    error.textContent = message || "";
    error.hidden = !message;
  }

  function pilot(id, state) {
    byId(id).dataset.state = state;
  }

  function renderNodes(status, online) {
    const connected = new Set(status.connected || []);
    const receiver = status.spring_receiver;
    for (const row of roles) {
      const role = row.dataset.role;
      row.hidden = Boolean(receiver && role !== "main" && role !== "server");
      const state = online ? (role === "server" || connected.has(role) ? "online" : receiver ? "idle" : "offline") :
        status.phase === "starting" ? "pending" : "idle";
      const label = row.querySelector(".monitor-status");
      label.dataset.state = state;
      label.querySelector("i").dataset.state = state;
      label.querySelector("span").textContent = receiver && role === "main" && online && !connected.has(role)
        ? "입력 준비" : {
        online: role === "server" ? "실행 중" : "연결됨", offline: "끊김", pending: "초기화 중", idle: "대기",
      }[state];
    }
    for (const role of ["main", "light"]) {
      const state = connected.has(role) && online ? "online" : online && !receiver ? "offline" : "idle";
      pilot(role + "-pilot", state);
      pilot(role + "-wire", state);
    }
    byId("live-nodes").textContent = (online ? connected.size : "—") + (receiver ? " / 1" : " / 4");
    const observed = byId("last-observation");
    observed.textContent = online && status.observed_at ? clock.format(new Date(status.observed_at)) : "—";
    observed.dateTime = online ? status.observed_at || "" : "";
  }

  function renderScope(trace, phase) {
    const plot = byId("scope-plot");
    const inputWave = byId("input-wave");
    const outputWave = byId("output-wave");
    const empty = byId("plot-empty");
    const completed = timebase(trace?.elapsedMs ?? null);
    const waiting = Boolean(trace && !completed && phase === "applying");

    inputWave.hidden = !trace;
    outputWave.hidden = !completed;
    empty.hidden = Boolean(completed);
    empty.textContent = !trace ? "버튼 입력 대기" : waiting ? "출력 전이 대기" : "출력 전이 미관측";
    plot.dataset.waiting = String(Boolean(trace && !completed));
    byId("live-latency").textContent = completed ? trace.elapsedMs.toFixed(1) + " ms" : "—";
    if (completed) {
      outputWave.dataset.rising = String(Boolean(trace.output.observed_output));
      outputWave.style.setProperty("--edge", completed.outputPercent + "%");
    }
    const axis = byId("scope-axis");
    if (axis.dataset.scale !== String(completed?.endMs)) {
      axis.dataset.scale = String(completed?.endMs);
      axis.replaceChildren(...(completed?.ticks ?? []).map((tick, index) => {
        const mark = document.createElement("span");
        mark.style.left = 5 + index * 23.75 + "%";
        mark.textContent = index === 4 ? tick + " ms" : String(tick);
        return mark;
      }));
    }
    plot.setAttribute("aria-label", completed
      ? "GPIO 20 입력 후 " + trace.elapsedMs.toFixed(1) + " 밀리초에 GPIO 18 출력이 " +
        (trace.output.observed_output ? "켜졌습니다." : "꺼졌습니다.")
      : empty.textContent);
  }

  function renderTrace(status, trace) {
    const events = status.events ?? [];
    const latest = events.at(-1);
    const signature = [status.run_id, latest?.event_id, latest?.stage, events.length].join(" ");
    if (renderedTrace === signature) return;
    renderedTrace = signature;

    const rows = byId("live-rows");
    const evidence = byId("live-evidence");
    const shown = events.slice(-4);
    const byEvent = new Map(events.filter(event => event.stage === "INPUT")
      .map(event => [event.event_id, event]));
    rows.hidden = evidence.hidden = shown.length === 0;
    byId("trace-empty").hidden = shown.length > 0;
    byId("trace-count").textContent = events.length + (events.length === 64 ? "+" : "") + "건";
    byId("live-run-id").textContent = status.run_id || "";
    rows.replaceChildren(...[...shown].reverse().map((event) => {
      const input = event.stage === "INPUT";
      const begin = byEvent.get(event.event_id);
      const row = document.createElement("li");
      const label = document.createElement("span");
      const mark = document.createElement("i");
      const title = document.createElement("strong");
      const time = document.createElement("code");
      row.className = "trace-item";
      row.dataset.stage = event.stage.toLowerCase();
      mark.setAttribute("aria-hidden", "true");
      const received = event.stage === "SERVER_RECEIVED";
      title.textContent = input ? "버튼 눌림" : received ? "Spring 수신" :
        "LED " + (event.observed_output ? "켜짐" : "꺼짐");
      time.textContent = received ? "seq " + event.sequence : input ? "0 ms" :
        begin ? "+" + ((event.timestamp_ns - begin.timestamp_ns) / 1e6).toFixed(1) + " ms" : "—";
      label.append(mark, title);
      row.append(label, time);
      return row;
    }));
    const raw = status.spring_receiver
      ? events.filter(event => event.event_id === latest?.event_id)
      : trace ? [trace.input, trace.output].filter(Boolean) : [];
    byId("trace-raw").replaceChildren(...raw.map((event) => {
      const item = document.createElement("li");
      const value = document.createElement("code");
      value.textContent = event.stage === "SERVER_RECEIVED"
        ? "SERVER_RECEIVED seq " + event.sequence + " · " + event.observed_at
        : event.stage + " " + event.timestamp_ns + " ns";
      item.append(value);
      return item;
    }));
  }

  function draw(status) {
    current = status;
    const {enabled, phase} = status;
    const receiver = Boolean(status.spring_receiver);
    const online = enabled && (phase === "running" || phase === "applying");
    const stable = ["idle", "stopped", "failed"].includes(phase);
    const label = statusLabels[phase] || "실험 상태 확인 중";
    const trace = latestTrace(status.events ?? []);
    controls.hidden = !enabled;
    start.disabled = !stable || Boolean(status.cleanup_failed) || Boolean(requesting);
    stop.disabled = phase !== "running" || Boolean(requesting);
    press.disabled = !online;
    press.setAttribute("aria-disabled", String(!online || phase === "applying" || Boolean(requesting)));
    const led = online ? status.led : null;
    pilot("session-pilot", online ? "online" : phase === "starting" ? "pending" : phase === "failed" ? "offline" : "idle");
    pilot("led-lamp", led == null ? "unknown" : led ? "online" : "off");
    const stateText = !enabled ? "관측 전용" : phase === "starting" ? label + " " + status.step + " / 4" : label;
    stateLabel.textContent = stateText;
    byId("live-led").textContent = receiver ? "이 실험의 범위 밖" :
      led == null ? "미관측" : led ? "켜짐" : "꺼짐";
    hint.textContent = phase === "applying" || requesting === "button"
      ? "입력 처리 중" : online ? "클릭 또는 Enter" : "실험 시작 후 사용";
    bay.dataset.benchState = phase || "idle";
    bay.dataset.online = String(online);
    byId("bench-description").textContent = receiver
      ? "MainPi 버튼 메시지가 Spring에 도착하는지 확인합니다."
      : "GPIO를 조작하고 출력이 바뀌는 순간을 관측합니다.";
    byId("bay-target").textContent = receiver ? "Spring 수신" : "조명";
    byId("bay-endpoint").textContent = receiver ? "ButtonPressed" : "GPIO 18";
    byId("bridge-endpoint").textContent = receiver ? "WS /socket" : "WSS /socket";
    byId("main-detail").textContent = receiver ? "버튼 입력" : "버튼 · LCD";
    byId("gpio-scope").hidden = receiver;
    byId("trace-empty").textContent = receiver ? "버튼을 누르면 Spring 수신 순번이 기록됩니다."
      : "버튼을 누르면 입력과 출력이 여기에 기록됩니다.";
    byId("bench-note").textContent = !enabled ? "로컬에서 pilab web --control로 실험을 시작할 수 있습니다."
      : online ? (phase === "applying"
        ? (receiver ? "Spring 수신 확인 중" : "GPIO 입력에서 LED 출력까지 관측 중") : "MainPi의 스위치를 눌러보세요.")
      : phase === "starting" ? "Spring과 가상 보드의 연결을 확인하는 중입니다."
      : "실험을 시작해 가상 보드를 연결하세요.";
    showError(status.error);
    renderNodes(status, online);
    renderScope(trace, phase);
    renderTrace(status, trace);
  }

  async function request(action) {
    const response = await fetch(action ? "/api/lab/" + action : "/api/lab", action ? {
      method: "POST", headers: {"Content-Type": "application/json"}, body: "{}",
    } : {cache: "no-store"});
    if (response.status === 404 && !action) return {enabled: false};
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "제어 API 요청에 실패했습니다.");
    return data;
  }

  function nextPoll(status) {
    const interval = ["applying", "starting", "stopping"].includes(status.phase) ? 300 : 1000;
    if (status.enabled) timer = window.setTimeout(poll, interval);
  }

  function routeRoom(status) {
    if (status.mode !== "room") return false;

    for (const link of document.querySelectorAll('a[href="#bench"]')) {
      link.href = "room.html";
    }
    function openRoomFromBench() {
      if (!["#replay", "#replay-events"].includes(location.hash)) {
        location.replace("room.html");
      }
    }
    window.addEventListener("hashchange", openRoomFromBench);
    openRoomFromBench();
    return true;
  }

  async function poll() {
    if (requesting) return;
    const pending = generation;
    try {
      const status = await request();
      if (pending !== generation || routeRoom(status)) return;
      draw(status);
      nextPoll(status);
    } catch (problem) {
      if (pending !== generation) return;
      stateLabel.textContent = "상태 연결 끊김";
      pilot("session-pilot", "offline");
      start.disabled = stop.disabled = press.disabled = true;
      showError(problem.message);
      timer = window.setTimeout(poll, 2000);
    }
  }

  for (const button of [start, stop, press]) {
    button.addEventListener("click", async () => {
      if (requesting || !current || button.getAttribute("aria-disabled") === "true") return;
      const action = button.dataset.action;
      if (action === "button" && current.phase !== "running") return;
      window.clearTimeout(timer);
      generation += 1;
      requesting = action;
      if (action === "button") {
        press.dataset.pulsing = "true";
        window.setTimeout(() => { delete press.dataset.pulsing; }, 175);
      }
      draw(current);
      try {
        const status = await request(action);
        requesting = null;
        draw(status);
        nextPoll(status);
      } catch (problem) {
        requesting = null;
        draw(current);
        showError(problem.message);
        timer = window.setTimeout(poll, 1000);
      }
    });
  }

  poll();
})();
