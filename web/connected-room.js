"use strict";

(() => {
  const endpoint = "/api/lab";
  const button = document.querySelector("#room-button");
  const health = document.querySelector("#room-health");
  const controls = document.querySelector("#room-lifecycle");
  const start = document.querySelector("#room-start");
  const stop = document.querySelector("#room-stop");
  const lifecycleError = document.querySelector("#room-lifecycle-error");
  const chips = [...document.querySelectorAll("[data-device]")];
  const forms = [...document.querySelectorAll("form[data-room-api]")];
  const readings = [...document.querySelectorAll("[data-reading]")];
  const outputs = [...document.querySelectorAll("[data-output]")];
  const stages = [...document.querySelectorAll("[data-stage]")];
  const error = document.querySelector("#room-button-error");
  const command = document.querySelector("#room-command");
  const observed = document.querySelector("#room-observed");
  const clock = new Intl.DateTimeFormat("ko-KR", {timeStyle: "medium", hourCycle: "h23"});
  let refreshing = false;
  let posting = false;
  let requestVersion = 0;
  let formRun;
  let requestError = null;
  const progress = ["실험 준비 중", "실험 소스 준비 중", "이미지 빌드 중",
    "컨테이너 시작 중", "장치 연결 확인 중", "센서값 수신 확인 중"];

  function text(element, value) {
    if (element.textContent !== value) element.textContent = value;
  }

  function draw(status) {
    const enabled = status.enabled && status.mode === "room";
    const connected = new Set(status.connected);
    const active = enabled && status.spring;
    const applying = status.phase === "applying";
    const events = status.events || [];
    const receipt = events.find(event => event.stage === "received");
    const application = events.find(event => event.stage === "applied");

    const managed = enabled && status.lifecycle;
    const {phase} = status;
    const phaseLabels = {
      idle: "실험 시작 대기", starting: progress[status.step] || progress[0],
      stopping: "실험 정리 중", stopped: "실험 정지됨", failed: "실험 실패",
    };
    health.dataset.active = String(Boolean(active));
    text(health, active ? "PiLab 실행 중" : enabled ? phaseLabels[phase] || "Spring 상태 확인 불가" :
      "통합 작업대를 열려면 pilab web --room 실행");
    controls.hidden = !managed;
    start.disabled = !["idle", "stopped", "failed"].includes(phase) || Boolean(status.cleanupFailed);
    stop.disabled = !["starting", "running", "applying"].includes(phase);
    text(lifecycleError, requestError || status.error || status.observationError || "");
    lifecycleError.hidden = !lifecycleError.textContent;
    button.disabled = !active || applying || !connected.has("main") || !connected.has("light");
    button.dataset.pressing = String(applying);
    text(document.querySelector("#room-count"), active ? connected.size + " / 4" : "대기");
    const checking = phase === "starting";
    const expectedOnline = ["running", "applying"].includes(phase);
    for (const chip of chips) {
      const name = chip.dataset.device;
      const online = active && (name === "server" || connected.has(name));
      chip.dataset.online = String(Boolean(online));
      chip.querySelector(".pilot").dataset.state = online ? "online" : checking ? "pending" :
        expectedOnline ? "offline" : "idle";
      text(chip.querySelector("[data-connection]"), online ? name === "server" ? "실행 중" : "연결됨" :
        checking ? "준비 중" : expectedOnline ? "연결 끊김" : "대기");
    }
    const run = managed && active ? status.runId : null;
    for (const form of forms) {
      form.inert = !active || !connected.has(form.closest("[data-device]").dataset.device);
      if (run !== formRun) form.dispatchEvent(new Event("pilab-room-reset"));
    }
    formRun = run;
    for (const element of readings) {
      const name = element.closest("[data-device]").dataset.device;
      const value = connected.has(name) ? status.readings?.[element.dataset.reading] : null;
      text(element, value == null ? "미관측" : String(value) + (element.dataset.unit || ""));
    }
    for (const element of outputs) {
      const name = element.closest("[data-device]").dataset.device;
      const key = element.dataset.output;
      const value = connected.has(name) ? status.outputs?.[key] : null;
      if (key === "led" || key === "alarm") {
        element.dataset.on = String(Boolean(value));
        text(element, value == null ? "미관측" : value ? "켜짐" : "꺼짐");
      } else if (key === "servo") {
        text(element, value == null ? "미관측" : value + "°");
      } else {
        text(element, value == null ? "미관측" : value === 1 ? "올리기" : value === -1 ? "내리기" : "대기");
      }
    }

    text(document.querySelector("#room-sequence"), receipt ? String(receipt.seq) : "미관측");
    command.hidden = !receipt;
    text(command, receipt?.commandId || "");
    const labels = {input: "눌림 주입", received: "순번 " + receipt?.seq,
      applied: application?.on ? "켜짐 확인" : "꺼짐 확인"};
    for (const step of stages) {
      const stage = step.dataset.stage;
      const seen = events.some(event => event.stage === stage);
      step.dataset.done = String(seen);
      text(step.querySelector("strong"), seen ? labels[stage] : "대기");
    }

    text(error, status.buttonError || "");
    error.hidden = !error.textContent;
    observed.dateTime = status.observedAt || "";
    text(observed, status.observedAt ? clock.format(new Date(status.observedAt)) : "미관측");
  }

  async function refresh() {
    if (refreshing || posting || document.hidden) return;
    refreshing = true;
    const version = requestVersion;
    try {
      const response = await fetch(endpoint, {cache: "no-store"});
      if (!response.ok) throw new Error("상태 조회 실패");
      const status = await response.json();
      if (version === requestVersion) draw(status);
    } catch {
      if (version === requestVersion) draw({enabled: true, mode: "room", observationError: "PiLab 상태를 읽을 수 없습니다."});
    } finally {
      refreshing = false;
    }
  }

  async function send(action, source) {
    if (posting || source.disabled) return;
    posting = true;
    requestError = null;
    requestVersion += 1;
    source.disabled = true;
    if (action === "button") button.dataset.pressing = "true";
    try {
      const response = await fetch(endpoint + "/" + action, {
        method: "POST", headers: {"Content-Type": "application/json"}, body: "{}",
      });
      const status = await response.json();
      if (!response.ok) throw new Error(status.error);
      draw(status);
    } catch (ex) {
      requestError = ex.message || "실험 요청을 보내지 못했습니다.";
      text(lifecycleError, requestError);
      lifecycleError.hidden = false;
    } finally {
      posting = false;
    }
  }

  start.addEventListener("click", () => send("start", start));
  stop.addEventListener("click", () => send("stop", stop));
  button.addEventListener("click", () => send("button", button));

  refresh();
  setInterval(refresh, 600);
})();
