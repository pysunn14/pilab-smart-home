document.querySelectorAll("form[data-room-api]").forEach((form) => {
  const controls = form.querySelector("fieldset");
  const fields = [...controls.querySelectorAll("input")];
  const apply = controls.querySelector("button");
  const status = form.querySelector(".room-status");
  const dirty = new Set();
  let current;
  let reading = false;
  let saving = false;
  let generation = 0;

  async function request(patch) {
    const options = patch && {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(patch),
    };
    const response = await fetch(form.dataset.roomApi, options);
    const snapshot = await response.json();
    if (!response.ok) throw new Error(snapshot.error);
    return snapshot;
  }

  function showStatus(message, error = false) {
    if (status.textContent !== message) status.textContent = message;
    status.toggleAttribute("data-error", error);
  }

  function render() {
    controls.disabled = !current || saving;
    apply.disabled = !dirty.size;
    if (!current) return;
    for (const input of fields) {
      if (!dirty.has(input.name) && input.valueAsNumber !== current.values[input.name]) {
        input.value = current.values[input.name];
      }
    }
  }

  async function refresh() {
    if (reading || saving || form.inert || document.hidden) return;
    reading = true;
    const before = current;
    const version = generation;
    try {
      const snapshot = await request();
      if (saving || version !== generation || before !== current) return;
      current = snapshot;
      if (!before || before.revision !== snapshot.revision || status.hasAttribute("data-error") && !dirty.size) {
        showStatus("현재 가상 센서 입력");
      }
      render();
    } catch (error) {
      if (!saving && version === generation && before === current && (!current || !dirty.size)) {
        showStatus("방 상태를 불러오지 못했습니다.", true);
      }
    } finally {
      reading = false;
    }
  }

  form.addEventListener("input", ({ target }) => {
    if (target.valueAsNumber === current.values[target.name]) dirty.delete(target.name);
    else dirty.add(target.name);
    showStatus(dirty.size ? "적용할 변경 사항이 있습니다." : "현재 가상 센서 입력");
    apply.disabled = !dirty.size;
  });

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (saving || !dirty.size) return;
    const version = generation;
    const patch = Object.fromEntries(fields.filter(({ name }) => dirty.has(name)).map(
      ({ name, valueAsNumber }) => [name, valueAsNumber],
    ));
    saving = true;
    render();
    showStatus("적용 중");
    try {
      const snapshot = await request(patch);
      if (version !== generation) return;
      current = snapshot;
      dirty.clear();
      showStatus("방 상태를 바꿨습니다.");
    } catch (error) {
      if (version === generation) showStatus(error.message || "방 상태를 바꾸지 못했습니다.", true);
    } finally {
      if (version === generation) {
        saving = false;
        render();
      }
    }
  });

  form.addEventListener("pilab-room-reset", () => {
    generation += 1;
    dirty.clear();
    current = undefined;
    saving = false;
    for (const input of fields) input.value = "";
    showStatus("센서 입력 확인 중");
    render();
    refresh();
  });

  refresh();
  setInterval(refresh, 2000);
});
