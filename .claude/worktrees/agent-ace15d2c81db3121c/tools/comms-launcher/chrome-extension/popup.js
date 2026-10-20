const HOST_NAME = "com.jonathanmckay.comms_launcher";

const launchButton = document.getElementById("launch");
const statusEl = document.getElementById("status");

function setStatus(text, kind = "") {
  statusEl.textContent = text;
  statusEl.className = kind;
}

launchButton.addEventListener("click", () => {
  launchButton.disabled = true;
  setStatus("Launching channels...");

  chrome.runtime.sendNativeMessage(HOST_NAME, { action: "launch" }, (response) => {
    const lastError = chrome.runtime.lastError;

    if (lastError) {
      setStatus(
        "Native host not available.\nRun install_native_host.sh with this extension ID.",
        "err"
      );
      launchButton.disabled = false;
      return;
    }

    if (!response || !response.ok) {
      const errorMessage = response && response.error ? response.error : "Unknown error";
      setStatus(`Launch failed: ${errorMessage}`, "err");
      launchButton.disabled = false;
      return;
    }

    setStatus("Channels launched.", "ok");
    launchButton.disabled = false;
  });
});
