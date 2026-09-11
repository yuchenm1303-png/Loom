const DEFAULT_BRIDGE_URL = "http://127.0.0.1:39222";
const DEFAULT_TOKEN = "loom-dev-browser-extension";

const bridgeUrl = document.getElementById("bridgeUrl");
const token = document.getElementById("token");
const status = document.getElementById("status");

async function load() {
  const values = await chrome.storage.local.get(["bridgeUrl", "token"]);
  bridgeUrl.value = values.bridgeUrl || DEFAULT_BRIDGE_URL;
  token.value = values.token || DEFAULT_TOKEN;
}

document.getElementById("save").addEventListener("click", async () => {
  await chrome.storage.local.set({
    bridgeUrl: bridgeUrl.value.trim() || DEFAULT_BRIDGE_URL,
    token: token.value.trim() || DEFAULT_TOKEN,
  });
  status.textContent = "Saved. The background bridge will use these values on the next poll.";
  window.setTimeout(() => { status.textContent = ""; }, 2600);
});

load().catch((cause) => {
  status.textContent = cause instanceof Error ? cause.message : String(cause);
});
