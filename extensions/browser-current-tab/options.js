const DEFAULT_BRIDGE_URL = "http://127.0.0.1:39222";

const bridgeUrl = document.getElementById("bridgeUrl");
const status = document.getElementById("status");

async function load() {
  const values = await chrome.storage.local.get(["bridgeUrl"]);
  bridgeUrl.value = values.bridgeUrl || DEFAULT_BRIDGE_URL;
}

document.getElementById("save").addEventListener("click", async () => {
  await chrome.storage.local.set({
    bridgeUrl: bridgeUrl.value.trim() || DEFAULT_BRIDGE_URL,
  });
  status.textContent = "Saved. Pairing credentials are managed by Loom Desktop.";
  window.setTimeout(() => { status.textContent = ""; }, 2600);
});

load().catch((cause) => {
  status.textContent = cause instanceof Error ? cause.message : String(cause);
});
