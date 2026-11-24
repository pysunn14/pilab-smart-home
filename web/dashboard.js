"use strict";

const { nodes, scenarios, initialState, record } = globalThis.PiLabModel;
const scenarioForm = document.querySelector("#scenario-form");
const scenarioSelect = document.querySelector("#scenario");
const runButton = document.querySelector("#run");
const runLabel = document.querySelector("#run-label");
const runStatus = document.querySelector("#run-status");
const nodeGrid = document.querySelector("#node-buttons");
const topology = document.querySelector("#topology");
const nodeDetail = document.querySelector("#node-detail");
const traceCaption = document.querySelector("#trace-caption");
const eventRows = document.querySelector("#event-rows");
const filter = document.querySelector("#filter");
const connectedCount = document.querySelector("#connected");
const wires = document.querySelector("#wires");
const wireContext = wires.getContext("2d");
const buttons = {};
const palette = getComputedStyle(document.documentElement);
const wireColors = {
  idle: palette.getPropertyValue("--color-wire"),
  active: palette.getPropertyValue("--color-accent"),
  down: palette.getPropertyValue("--color-negative"),
};
const kinds = {
  receive: "수신", send: "송신", process: "서버 처리", disconnect: "연결 끊김", connect: "재연결",
};

let state = scenarios.normal.events.reduce(record, initialState());
let selectedNode = "server";
let trafficNode = null;
let traceId = "normal";
let traceState = "예시 트레이스";

function element(tag, className, content) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (content !== undefined) node.textContent = content;
  return node;
}

function icon(name) {
  const well = element("span", "icon-well");
  well.setAttribute("aria-hidden", "true");
  well.append(element("span", "icon icon-" + name));
  return well;
}

function createNodeButton(id, node) {
  const button = element("button", "node");
  button.type = "button";
  button.dataset.node = id;
  const label = element("span");
  label.append(element("strong", "node-name", node.title), element("span", "node-role", node.role));
  const status = element("span", "dot");
  status.setAttribute("aria-hidden", "true");
  button.append(icon(node.icon), label, status);
  button.addEventListener("click", () => {
    selectedNode = id;
    renderNodes();
    renderDetail();
  });
  return button;
}

function renderWires() {
  if (!wireContext) return;
  const canvas = wires.getBoundingClientRect();
  const ratio = window.devicePixelRatio || 1;
  wires.width = Math.round(canvas.width * ratio);
  wires.height = Math.round(canvas.height * ratio);
  wireContext.scale(ratio, ratio);
  wireContext.lineCap = "round";
  wireContext.lineJoin = "round";

  const hub = buttons.server.getBoundingClientRect();
  const piIds = Object.keys(state.connections);
  const order = piIds.filter((id) => id !== selectedNode && id !== trafficNode);
  for (const id of piIds) {
    if (!order.includes(id)) order.push(id);
  }

  for (const id of order) {
    const node = buttons[id].getBoundingClientRect();
    const above = node.top < hub.top;
    const origin = node.left + node.width / 2 - canvas.left;
    const hubCenter = hub.left + hub.width / 2 - canvas.left;
    const target = hubCenter + hub.width * (origin < hubCenter ? -.24 : .24);
    const start = (above ? node.bottom : node.top) - canvas.top;
    const end = (above ? hub.top : hub.bottom) - canvas.top;
    const bend = (start + end) / 2;
    const offline = !state.connections[id];
    const active = id === selectedNode || id === trafficNode;
    wireContext.strokeStyle = offline ? wireColors.down : active ? wireColors.active : wireColors.idle;
    wireContext.lineWidth = active ? 2.5 : 1.5;
    wireContext.setLineDash(offline ? [5, 5] : []);
    wireContext.beginPath();
    wireContext.moveTo(origin, start);
    wireContext.lineTo(origin, bend);
    wireContext.lineTo(target, bend);
    wireContext.lineTo(target, end);
    wireContext.stroke();
  }
}

function renderNodes() {
  for (const [id, button] of Object.entries(buttons)) {
    const offline = state.connections[id] === false;
    button.setAttribute("aria-pressed", String(id === selectedNode));
    button.setAttribute("aria-label", nodes[id].title + (offline ? ", 연결 끊김" : ", 연결됨"));
    button.dataset.offline = String(offline);
    button.dataset.traffic = String(id === trafficNode);
  }
  connectedCount.textContent = Object.values(state.connections).filter(Boolean).length + " / 4";
  renderWires();
}

function renderDetail() {
  const node = nodes[selectedNode];
  const identity = element("div", "detail-identity");
  const title = element("div");
  title.append(element("h3", "", node.title), element("p", "", node.role));
  identity.append(icon(node.icon), title);

  const metadata = element("dl", "detail-meta");
  const offline = state.connections[selectedNode] === false;
  for (const [label, value] of [
    ["연결 상태", element("span", offline ? "state offline" : "state", offline ? "연결 끊김" : "연결됨")],
    ["원본 브랜치", element("code", "", node.branch)],
    ["통신", element("code", "", "WebSocket")],
  ]) {
    const row = element("div");
    const definition = element("dd");
    definition.append(value);
    row.append(element("dt", "", label), definition);
    metadata.append(row);
  }

  const messages = element("div", "detail-messages");
  for (const [label, names] of [["수신 메시지", node.incoming], ["송신 메시지", node.outgoing]]) {
    const block = element("div");
    const tags = element("div", "message-tags");
    tags.append(...names.map((name) => element("code", "message-tag", name)));
    block.append(element("h3", "", label), tags);
    messages.append(block);
  }
  nodeDetail.replaceChildren(identity, metadata, messages);
}

function renderEvents() {
  traceCaption.textContent = scenarios[traceId].title + " · " + traceState;
  const displayed = state.events.filter((event) => !filter.value || event.node === filter.value);
  if (displayed.length === 0) {
    const row = element("tr");
    const cell = element("td", "empty", "이 필터에 해당하는 이벤트가 없습니다.");
    cell.colSpan = 5;
    row.append(cell);
    eventRows.replaceChildren(row);
    return;
  }

  eventRows.replaceChildren(...displayed.map((event) => {
    const row = element("tr");
    const elapsed = element("time", "", "+" + (event.at / 1000).toFixed(2) + " s");
    elapsed.dateTime = "PT" + event.at / 1000 + "S";
    const timeCell = element("td");
    const message = element("td");
    const value = element("td");
    timeCell.append(elapsed);
    message.append(event.value === undefined ? element("span", "", event.name) : element("code", "", event.name));
    value.append(element("code", "", event.value === undefined ? "—" : JSON.stringify(event.value)));
    row.append(timeCell, element("td", "", nodes[event.node].title),
      element("td", "event-kind " + event.kind, kinds[event.kind]), message, value);
    return row;
  }));
}

function render() {
  renderNodes();
  renderDetail();
  renderEvents();
}

scenarioForm.addEventListener("submit", async (submit) => {
  submit.preventDefault();
  if (runButton.disabled) return;

  traceId = scenarioSelect.value;
  const scenario = scenarios[traceId];
  state = initialState();
  filter.value = "";
  traceState = "재생 중";
  runButton.disabled = true;
  scenarioSelect.disabled = true;
  runLabel.textContent = "재생 중";
  runStatus.textContent = scenario.title + " 재생을 시작했습니다.";

  const startedAt = performance.now();
  for (const event of scenario.events) {
    const delay = event.at - (performance.now() - startedAt);
    if (delay > 0) await new Promise((resolve) => window.setTimeout(resolve, delay));
    state = record(state, event);
    trafficNode = event.node;
    render();
  }

  traceState = "재생 완료";
  trafficNode = null;
  renderNodes();
  renderEvents();
  runStatus.textContent = scenario.title + " 재생이 완료되었습니다.";
  runLabel.textContent = "실행";
  runButton.disabled = false;
  scenarioSelect.disabled = false;
});

for (const [id, scenario] of Object.entries(scenarios)) {
  scenarioSelect.add(new Option(scenario.title, id));
}
filter.add(new Option("전체 노드", ""));
for (const [id, node] of Object.entries(nodes)) {
  buttons[id] = createNodeButton(id, node);
  nodeGrid.append(buttons[id]);
  filter.add(new Option(node.title, id));
}
filter.addEventListener("change", renderEvents);

function renderNavigation() {
  const replay = location.hash === "#replay" || location.hash === "#replay-events";
  document.querySelector("#bench-view").hidden = replay;
  document.querySelector("#replay-view").hidden = !replay;
  for (const link of document.querySelectorAll(".view-nav a")) {
    if (link.hash === (replay ? "#replay" : "#bench")) {
      link.setAttribute("aria-current", "page");
    } else {
      link.removeAttribute("aria-current");
    }
  }
  if (replay) renderWires();
}
window.addEventListener("hashchange", renderNavigation);

render();
renderNavigation();
const measureTopology = new ResizeObserver(renderWires);
measureTopology.observe(topology);
for (const button of Object.values(buttons)) measureTopology.observe(button);
