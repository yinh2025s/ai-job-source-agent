const state = { records: [], runId: null, pollTimer: null };
const $ = (id) => document.getElementById(id);

const setMessage = (message = "") => { $("message").textContent = message; };
const setBridgeState = (label, value) => {
  $("bridgeState").textContent = label;
  $("bridgeState").dataset.state = value;
};
const connection = () => ({
  url: $("bridgeUrl").value.replace(/\/$/, ""),
  token: $("bridgeToken").value
});
const authorizedFetch = (path, options = {}) => {
  const { url, token } = connection();
  return fetch(`${url}${path}`, {
    ...options,
    headers: {
      "Authorization": `Bearer ${token}`,
      "Content-Type": "application/json",
      ...(options.headers || {})
    }
  });
};

async function loadSettings() {
  const saved = await chrome.storage.local.get(["bridgeUrl", "bridgeToken", "runId"]);
  if (saved.bridgeUrl) $("bridgeUrl").value = saved.bridgeUrl;
  if (saved.bridgeToken) $("bridgeToken").value = saved.bridgeToken;
  state.runId = saved.runId || null;
  await checkHealth();
  if (state.runId) await pollRun();
}

async function checkHealth() {
  try {
    const response = await authorizedFetch("/v1/health");
    if (!response.ok) throw new Error(response.status === 401 ? "Token rejected" : "Bridge unavailable");
    setBridgeState("Online", "online");
  } catch (error) {
    setBridgeState("Offline", "error");
    setMessage(error.message);
  }
}

async function scanPage() {
  setMessage();
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab?.id || !tab.url?.startsWith("https://www.linkedin.com/jobs/")) {
    setMessage("Open a LinkedIn Jobs page first.");
    return;
  }
  let response;
  try {
    response = await chrome.tabs.sendMessage(tab.id, { type: "collect_job_source_records" });
  } catch {
    await chrome.scripting.executeScript({ target: { tabId: tab.id }, files: ["content.js"] });
    response = await chrome.tabs.sendMessage(tab.id, { type: "collect_job_source_records" });
  }
  if (!response?.ok) {
    setMessage(response?.error || "Page scan failed.");
    return;
  }
  state.records = response.records || [];
  $("recordCount").textContent = String(state.records.length);
  $("applyCount").textContent = `${state.records.filter((item) => item.external_apply_url).length} Apply URLs`;
  $("runButton").disabled = state.records.length === 0;
}

async function runDiscovery() {
  setMessage();
  setBridgeState("Submitting", "busy");
  try {
    const response = await authorizedFetch("/v1/runs", {
      method: "POST",
      body: JSON.stringify({ records: state.records })
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.detail || payload.error || "Run submission failed");
    state.runId = payload.run_id;
    await chrome.storage.local.set({ runId: state.runId });
    $("runPanel").hidden = false;
    $("runStatus").textContent = "Queued";
    await pollRun();
  } catch (error) {
    setBridgeState("Error", "error");
    setMessage(error.message);
  }
}

async function pollRun() {
  if (!state.runId) return;
  clearTimeout(state.pollTimer);
  try {
    const response = await authorizedFetch(`/v1/runs/${state.runId}`);
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || "Run lookup failed");
    $("runPanel").hidden = false;
    $("runStatus").textContent = payload.status[0].toUpperCase() + payload.status.slice(1);
    if (payload.status === "complete") {
      renderCompletedRun(payload);
      setBridgeState("Online", "online");
      return;
    }
    if (payload.status === "failed") {
      setBridgeState("Error", "error");
      setMessage(payload.error || "Discovery failed.");
      return;
    }
    setBridgeState("Running", "busy");
    state.pollTimer = setTimeout(pollRun, 2000);
  } catch (error) {
    setBridgeState("Offline", "error");
    setMessage(error.message);
  }
}

function renderCompletedRun(payload) {
  const summary = payload.summary;
  $("jobListRate").textContent = `${Math.round(summary.rates.job_list * 100)}%`;
  $("openingRate").textContent = `${Math.round(summary.rates.opening * 100)}%`;
  $("results").replaceChildren(...payload.results.map((result) => {
    const item = document.createElement("li");
    const title = document.createElement("strong");
    const outcome = document.createElement("span");
    title.textContent = `${result.company_name} · ${result.linkedin_job_title || "Untitled role"}`;
    outcome.textContent = result.open_position_url ? "Exact opening" : result.job_list_page_url ? "Job list" : result.error_code || "Not found";
    item.append(title, outcome);
    return item;
  }));
}

async function saveConnection() {
  const { url, token } = connection();
  await chrome.storage.local.set({ bridgeUrl: url, bridgeToken: token });
  await checkHealth();
}

$("scanButton").addEventListener("click", scanPage);
$("runButton").addEventListener("click", runDiscovery);
$("refreshButton").addEventListener("click", pollRun);
$("saveButton").addEventListener("click", saveConnection);
loadSettings();
