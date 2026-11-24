const blindForm = document.querySelector("#blind-controls");

if (blindForm) {
  const actions = blindForm.querySelector("fieldset");
  const status = blindForm.querySelector("[role=status]");
  const endpoint = "/api/lab/blind";

  function showBlindStatus(message, error = false) {
    status.textContent = message;
    status.toggleAttribute("data-error", error);
  }

  async function checkBlindAvailability() {
    try {
      const response = await fetch(endpoint, { cache: "no-store" });
      if (!response.ok) return;
      const availability = await response.json();
      if (availability.mode === "automatic") {
        blindForm.hidden = false;
        showBlindStatus("조도에 따라 자동 제어 중");
      } else if (availability.enabled) {
        blindForm.hidden = false;
        actions.disabled = false;
      }
    } catch (error) {
      console.error("블라인드 조작 가능 여부를 확인하지 못했습니다.", error);
    }
  }

  blindForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (actions.disabled || !event.submitter) return;
    const { value, textContent } = event.submitter;
    actions.disabled = true;
    showBlindStatus("LightPi 출력 확인 중");
    try {
      const response = await fetch(endpoint + "/" + value, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: "{}",
      });
      const receipt = await response.json();
      if (!response.ok) throw new Error(receipt.error);
      if (receipt.status === "applied" && receipt.direction === value && receipt.commandId) {
        showBlindStatus(textContent + " 방향 적용 확인");
      } else {
        showBlindStatus("LightPi 출력 적용을 확인하지 못했습니다.", true);
      }
    } catch (error) {
      showBlindStatus(error.message || "블라인드 명령을 전달하지 못했습니다.", true);
    } finally {
      actions.disabled = false;
    }
  });

  checkBlindAvailability();
}
