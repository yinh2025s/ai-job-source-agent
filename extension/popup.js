const BRIDGE_TIMEOUT_MS = 8000;
const POLL_DELAY_MS = 2000;
const SCAN_RETRY_DELAY_MS = 350;
const MAX_SCAN_RETRIES = 2;
const MAX_POLL_RETRIES = 2;

const state = {
  records: [],
  runId: null,
  pollTimer: null,
  pollRetries: 0,
  connectionKey: null,
  runInFlight: false,
  busy: { scan: false, run: false, poll: false, save: false },
};
const $ = (id) => document.getElementById(id);

const setMessage = (message = "") => { $("message").textContent = message; };
const setBridgeState = (label, value) => {
  $("bridgeState").textContent = label;
  $("bridgeState").dataset.state = value;
};

function hasBusyOperation() {
  return Object.values(state.busy).some(Boolean);
}

function syncBusyUi() {
  const busy = hasBusyOperation();
  $("popupRoot").setAttribute("aria-busy", String(busy));
  $("scanButton").disabled = busy;
  $("runButton").disabled = busy || state.records.length === 0 || state.runInFlight;
  $("runButton").textContent = state.runInFlight ? "Verifying..." : "Verify source";
  $("refreshButton").disabled = busy || !state.runId;
  $("saveButton").disabled = busy;
}

function setBusy(operation, busy) {
  state.busy[operation] = busy;
  syncBusyUi();
}

function normalizeBridgeUrl(rawUrl) {
  let parsed;
  try {
    parsed = new URL(String(rawUrl || "").trim());
  } catch {
    throw new Error("Bridge URL must be a local HTTP address.");
  }
  if (
    parsed.protocol !== "http:" ||
    parsed.hostname !== "127.0.0.1" ||
    parsed.username ||
    parsed.password ||
    parsed.pathname !== "/" ||
    parsed.search ||
    parsed.hash
  ) {
    throw new Error("Bridge URL must be http://127.0.0.1 with a local port only.");
  }
  const port = parsed.port === "" ? 80 : Number(parsed.port);
  if (!Number.isInteger(port) || port < 1 || port > 65535) {
    throw new Error("Bridge URL must use a valid local port.");
  }
  return `http://127.0.0.1:${port}`;
}

function bridgeConnection() {
  const url = normalizeBridgeUrl($("bridgeUrl").value);
  const token = $("bridgeToken").value.trim();
  if (!token) throw new Error("Enter a bridge token before connecting.");
  return { url, token, key: `${url}\n${token}` };
}

function payloadMessage(payload, fallback) {
  if (payload && typeof payload === "object") {
    for (const key of ["detail", "error", "message"]) {
      if (typeof payload[key] === "string" && payload[key].trim()) return payload[key];
    }
  }
  return fallback;
}

class BridgeRequestError extends Error {
  constructor(message, status = 0) {
    super(message);
    this.status = status;
  }
}

async function bridgeFetch(path, options = {}) {
  const { url, token } = bridgeConnection();
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), BRIDGE_TIMEOUT_MS);
  let response;
  try {
    response = await fetch(`${url}${path}`, {
      ...options,
      signal: controller.signal,
      headers: {
        Authorization: `Bearer ${token}`,
        "Content-Type": "application/json",
        ...(options.headers || {}),
      },
    });
  } catch (error) {
    if (error?.name === "AbortError") throw new BridgeRequestError("Bridge request timed out.");
    throw new BridgeRequestError("Bridge request failed.");
  } finally {
    clearTimeout(timeout);
  }
  let payload = null;
  try {
    const body = await response.text();
    payload = body ? JSON.parse(body) : null;
  } catch {
    throw new BridgeRequestError("Bridge returned an invalid JSON response.", response.status);
  }
  if (!response.ok) throw new BridgeRequestError(payloadMessage(payload, "Bridge request failed."), response.status);
  return payload;
}

function clearRunOutput() {
  $("runPanel").hidden = true;
  $("runStatus").textContent = "Queued";
  $("jobListRate").textContent = "--";
  $("openingRate").textContent = "--";
  $("results").replaceChildren();
}

function clearScanOutput() {
  state.records = [];
  $("recordCount").textContent = "0";
  $("applyCount").textContent = "0 Apply URLs";
  $("scanPanel").hidden = true;
  $("scanResults").replaceChildren();
  clearRunOutput();
  syncBusyUi();
}

async function clearStaleRun() {
  state.runId = null;
  state.runInFlight = false;
  state.pollRetries = 0;
  if (state.pollTimer !== null) clearTimeout(state.pollTimer);
  state.pollTimer = null;
  await chrome.storage.local.remove("runId");
  syncBusyUi();
}

function isObject(value) {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function validScanResponse(payload) {
  return isObject(payload) && typeof payload.ok === "boolean" && Array.isArray(payload.records)
    && typeof payload.page_url === "string" && payload.page_url.length > 0
    && (payload.scan_version === undefined || payload.scan_version === "2")
    && (payload.state === undefined || payload.state === "ready" || payload.state === "not_ready")
    && payload.records.every(isObject);
}

async function loadSettings() {
  const saved = await chrome.storage.local.get(["bridgeUrl", "bridgeToken", "runId"]);
  if (saved.bridgeUrl) $("bridgeUrl").value = saved.bridgeUrl;
  if (saved.bridgeToken) $("bridgeToken").value = saved.bridgeToken;
  state.runId = typeof saved.runId === "string" && saved.runId ? saved.runId : null;
  state.runInFlight = Boolean(state.runId);
  try {
    state.connectionKey = bridgeConnection().key;
  } catch {
    state.connectionKey = null;
  }
  syncBusyUi();
  await checkHealth();
  if (state.runId) await pollRun();
}

async function checkHealth() {
  try {
    const payload = await bridgeFetch("/v1/health");
    if (!isObject(payload) || payload.status !== "ok") throw new Error("Bridge health response was invalid.");
    setBridgeState("Online", "online");
    return true;
  } catch (error) {
    setBridgeState("Offline", "error");
    setMessage(error.message);
    return false;
  }
}

async function requestScan(tabId, attempt = 0) {
  let response;
  try {
    response = await chrome.tabs.sendMessage(tabId, { type: "collect_job_source_records" });
  } catch {
    try {
      await chrome.scripting.executeScript({ target: { tabId }, files: ["content.js"] });
      response = await chrome.tabs.sendMessage(tabId, { type: "collect_job_source_records" });
    } catch (error) {
      throw new Error(`Page scan failed: ${error?.message || "content script injection failed."}`);
    }
  }
  if (!validScanResponse(response)) throw new Error("Page scan returned an invalid response.");
  if (response.state === "not_ready") {
    if (attempt < MAX_SCAN_RETRIES) {
      await new Promise((resolve) => setTimeout(resolve, SCAN_RETRY_DELAY_MS));
      return requestScan(tabId, attempt + 1);
    }
    throw new Error("LinkedIn Jobs is still loading. Wait a moment and scan again.");
  }
  if (!response.ok) throw new Error(payloadMessage(response, "Page scan failed."));
  return response.records;
}

async function scanPage() {
  if (hasBusyOperation()) return;
  setBusy("scan", true);
  clearScanOutput();
  setMessage();
  try {
    await clearStaleRun();
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (!tab?.id || !tab.url?.startsWith("https://www.linkedin.com/jobs/")) {
      throw new Error("Open a LinkedIn Jobs page first.");
    }
    state.records = await requestScan(tab.id);
    $("recordCount").textContent = String(state.records.length);
    $("applyCount").textContent = `${state.records.filter((item) => item.external_apply_url).length} Apply URLs`;
    renderScannedRecords();
    if (state.records.length === 0) setMessage("No eligible jobs were found on this page.");
  } catch (error) {
    setMessage(error.message || "Page scan failed.");
  } finally {
    setBusy("scan", false);
  }
}

function validSubmission(payload) {
  return isObject(payload) && typeof payload.run_id === "string" && payload.run_id.length > 0
    && payload.status === "queued";
}

async function runDiscovery() {
  if (hasBusyOperation() || state.records.length === 0) return;
  setMessage();
  setBusy("run", true);
  setBridgeState("Submitting", "busy");
  try {
    clearRunOutput();
    await clearStaleRun();
    const payload = await bridgeFetch("/v1/runs", {
      method: "POST",
      body: JSON.stringify({ records: state.records }),
    });
    if (!validSubmission(payload)) throw new Error("Bridge returned an invalid run submission.");
    state.runId = payload.run_id;
    state.runInFlight = true;
    state.pollRetries = 0;
    await chrome.storage.local.set({ runId: state.runId });
    $("runPanel").hidden = false;
    $("runStatus").textContent = "Queued";
  } catch (error) {
    setBridgeState("Error", "error");
    setMessage(error.message || "Run submission failed.");
  } finally {
    setBusy("run", false);
  }
  if (state.runId) await pollRun();
}

function validRunResponse(payload, runId) {
  const statuses = new Set(["queued", "running", "complete", "failed"]);
  if (!isObject(payload) || payload.run_id !== runId || !statuses.has(payload.status)) return false;
  if (payload.status === "complete") {
    return isObject(payload.summary) && isObject(payload.summary.rates)
      && Number.isFinite(payload.summary.rates.job_list)
      && payload.summary.rates.job_list >= 0 && payload.summary.rates.job_list <= 1
      && Number.isFinite(payload.summary.rates.opening)
      && payload.summary.rates.opening >= 0 && payload.summary.rates.opening <= 1
      && Array.isArray(payload.results) && payload.results.every(isObject);
  }
  return payload.status !== "failed" || payload.error === undefined || typeof payload.error === "string";
}

function schedulePoll(delay = POLL_DELAY_MS) {
  if (!state.runId || state.pollTimer !== null) return;
  state.pollTimer = setTimeout(() => {
    state.pollTimer = null;
    pollRun();
  }, delay);
}

function isTransient(error) {
  return error instanceof BridgeRequestError && (error.status === 0 || error.status === 408 || error.status === 429 || error.status >= 500);
}

async function pollRun() {
  if (!state.runId || hasBusyOperation()) return;
  const runId = state.runId;
  if (state.pollTimer !== null) clearTimeout(state.pollTimer);
  state.pollTimer = null;
  setBusy("poll", true);
  try {
    const payload = await bridgeFetch(`/v1/runs/${encodeURIComponent(runId)}`);
    if (state.runId !== runId) return;
    if (!validRunResponse(payload, runId)) throw new Error("Bridge returned an invalid run response.");
    state.pollRetries = 0;
    $("runPanel").hidden = false;
    $("runStatus").textContent = payload.status.charAt(0).toUpperCase() + payload.status.slice(1);
    if (payload.status === "complete") {
      state.runInFlight = false;
      renderCompletedRun(payload);
      setBridgeState("Online", "online");
    } else if (payload.status === "failed") {
      state.runInFlight = false;
      setBridgeState("Error", "error");
      setMessage(payload.error || "Discovery failed.");
    } else {
      state.runInFlight = true;
      setBridgeState("Running", "busy");
      schedulePoll();
    }
  } catch (error) {
    if (state.runId !== runId) return;
    if (error instanceof BridgeRequestError && (error.status === 401 || error.status === 404)) {
      await clearStaleRun();
      setBridgeState("Offline", "error");
      setMessage(error.status === 404 ? "Saved run is no longer available." : "Bridge token was rejected.");
    } else if (isTransient(error) && state.pollRetries < MAX_POLL_RETRIES) {
      state.pollRetries += 1;
      setBridgeState("Offline", "error");
      setMessage("Bridge connection interrupted. Retrying shortly.");
      schedulePoll(POLL_DELAY_MS);
    } else {
      state.runInFlight = false;
      setBridgeState("Error", "error");
      setMessage(error.message || "Run lookup failed.");
    }
  } finally {
    setBusy("poll", false);
  }
}

function safeHttpsUrl(value) {
  try {
    const parsed = new URL(value);
    if (parsed.protocol !== "https:" || parsed.username || parsed.password) return null;
    const host = parsed.hostname.toLowerCase().replace(/^\[|\]$/g, "");
    if (!host || host === "localhost" || host.endsWith(".localhost")
      || host.endsWith(".local") || host.endsWith(".internal")) return null;
    if (host === "::" || host === "::1" || /^(?:fc|fd|fe8|fe9|fea|feb)/i.test(host)) return null;
    const octets = host.split(".").map(Number);
    if (octets.length === 4 && octets.every((octet) => (
      Number.isInteger(octet) && octet >= 0 && octet <= 255
    ))) {
      if (octets[0] === 0 || octets[0] === 10 || octets[0] === 127 || octets[0] >= 224
        || (octets[0] === 100 && octets[1] >= 64 && octets[1] <= 127)
        || (octets[0] === 169 && octets[1] === 254)
        || (octets[0] === 172 && octets[1] >= 16 && octets[1] <= 31)
        || (octets[0] === 192 && octets[1] === 168)) return null;
    }
    return parsed.href;
  } catch {
    return null;
  }
}

function appendOutcome(item, label, url) {
  const safeUrl = safeHttpsUrl(url);
  if (!safeUrl) return false;
  const link = document.createElement("a");
  link.href = safeUrl;
  link.target = "_blank";
  link.rel = "noopener noreferrer";
  link.textContent = label;
  item.append(link);
  return true;
}

function resultTitle(record) {
  const company = typeof record.company_name === "string" && record.company_name
    ? record.company_name : "Unknown company";
  const title = typeof record.linkedin_job_title === "string" && record.linkedin_job_title
    ? record.linkedin_job_title
    : (typeof record.job_title === "string" && record.job_title ? record.job_title : "Untitled role");
  return `${company} · ${title}`;
}

function renderScannedRecords() {
  $("scanPanel").hidden = state.records.length === 0;
  $("scanResults").replaceChildren(...state.records.map((record) => {
    const item = document.createElement("li");
    const title = document.createElement("strong");
    title.textContent = resultTitle(record);
    item.append(title);
    if (!appendOutcome(item, "LinkedIn Apply", record.external_apply_url)) {
      const source = document.createElement("span");
      source.textContent = "LinkedIn job selected";
      item.append(source);
    }
    return item;
  }));
}

function sourceRecordFor(result) {
  return state.records.find((record) => (
    record.linkedin_job_url && record.linkedin_job_url === result.linkedin_job_url
  )) || state.records.find((record) => (
    record.company_name === result.company_name
    && record.job_title === (result.linkedin_job_title || result.job_title)
  ));
}

function renderCompletedRun(payload) {
  const { summary, results } = payload;
  $("jobListRate").textContent = `${Math.round(summary.rates.job_list * 100)}%`;
  $("openingRate").textContent = `${Math.round(summary.rates.opening * 100)}%`;
  $("results").replaceChildren(...results.map((result) => {
    const item = document.createElement("li");
    const title = document.createElement("strong");
    title.textContent = resultTitle(result);
    item.append(title);
    const hasExact = appendOutcome(item, "Exact opening", result.open_position_url);
    let hasOutcome = hasExact;
    if (!hasExact) {
      hasOutcome = appendOutcome(item, "Job list", result.job_list_page_url) || hasOutcome;
      const sourceRecord = sourceRecordFor(result);
      hasOutcome = appendOutcome(item, "LinkedIn Apply", sourceRecord?.external_apply_url) || hasOutcome;
    }
    if (!hasOutcome) {
      const reason = document.createElement("span");
      reason.textContent = typeof result.error_code === "string" && result.error_code
        ? result.error_code
        : (typeof result.reason === "string" && result.reason ? result.reason : "No verified public job URL.");
      item.append(reason);
    }
    return item;
  }));
}

async function saveConnection() {
  if (hasBusyOperation()) return;
  setMessage();
  setBusy("save", true);
  try {
    const connection = bridgeConnection();
    if (state.runId && state.connectionKey !== connection.key) await clearStaleRun();
    $("bridgeUrl").value = connection.url;
    await chrome.storage.local.set({ bridgeUrl: connection.url, bridgeToken: connection.token });
    state.connectionKey = connection.key;
    await checkHealth();
  } catch (error) {
    setBridgeState("Offline", "error");
    setMessage(error.message || "Connection could not be saved.");
  } finally {
    setBusy("save", false);
  }
}

$("scanButton").addEventListener("click", scanPage);
$("runButton").addEventListener("click", runDiscovery);
$("refreshButton").addEventListener("click", pollRun);
$("saveButton").addEventListener("click", saveConnection);
loadSettings();
