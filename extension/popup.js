const BRIDGE_TIMEOUT_MS = 8000;
const POLL_DELAY_MS = 2000;
const SCAN_RETRY_DELAY_MS = 350;
const MAX_SCAN_RETRIES = 2;
const MAX_POLL_RETRIES = 2;
const PAGE_SCAN_WATCHDOG_MS = 60000;
const DEFAULT_BRIDGE_URL = "http://127.0.0.1:8765";
const PAIR_CLIENT = "ai-job-source-agent-extension";
const PAIR_PROTOCOL_VERSION = "1";
const MIN_PAIR_TOKEN_LENGTH = 32;
const SCAN_SNAPSHOT_KEY = "scanSnapshot";
const VERIFIED_DETAILS_KEY = "verifiedDetails";
const EXTERNAL_APPLY_CAPTURES_KEY = "externalApplyCaptures";
const SCAN_SNAPSHOT_VERSION = "1";
const VERIFIED_DETAILS_VERSION = "1";
const EXTERNAL_APPLY_CAPTURE_VERSION = "1";
const SCAN_SNAPSHOT_TTL_MS = 6 * 60 * 60 * 1000;
const MAX_STORED_RECORDS = 30;
const CONTENT_SCRIPT_VERSION = "3";
const CONTENT_STATUS_MESSAGE = "job_source_agent_content_status";
const SELECTED_SCAN_MESSAGE = "collect_job_source_records_v1";
const PAGE_SCAN_MESSAGE = "collect_job_source_page_v1";
const CANCEL_PAGE_SCAN_MESSAGE = "cancel_job_source_page_v1";
const CSV_EXPORT_FILENAME = "ai-job-source-results.csv";
const CSV_EXPORT_FIELDS = [
  "company_name",
  "linkedin_job_title",
  "linkedin_job_url",
  "company_website_url",
  "career_page_url",
  "job_list_page_url",
  "external_apply_url",
  "open_position_url",
  "result_status",
  "error_code",
];
const VERIFIED_DETAIL_FIELDS = [
  "company_name",
  "company_website_url",
  "career_root_url",
  "linkedin_job_url",
  "external_apply_url",
  "linkedin_job_title",
  "linkedin_job_location",
  "career_page_url",
  "job_list_page_url",
  "open_position_url",
  "error_code",
  "pipeline_status",
];

const state = {
  records: [],
  completedResults: [],
  runId: null,
  pollTimer: null,
  pollRetries: 0,
  connectionKey: null,
  runInFlight: false,
  pageScan: null,
  detailSelection: null,
  captureJobId: null,
  listScrollTop: 0,
  nextPageScanId: 1,
  busy: { selectedScan: false, pageScan: false, run: false, poll: false, save: false },
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
  const pageScanActive = Boolean(state.pageScan);
  $("popupRoot").setAttribute("aria-busy", String(busy));
  $("scanSelectedButton").disabled = busy || state.runInFlight;
  $("scanPageButton").disabled = state.runInFlight || (busy && !pageScanActive);
  $("scanPageButton").textContent = pageScanActive ? "Cancel scan" : "Scan page";
  $("runButton").disabled = busy || state.records.length === 0 || state.runInFlight;
  $("runButton").textContent = state.runInFlight ? "Verifying..." : "Verify source";
  $("exportCsvButton").disabled = state.records.length === 0;
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

function bridgeConnection({ requireToken = true } = {}) {
  const url = normalizeBridgeUrl($("bridgeUrl").value);
  const token = $("bridgeToken").value.trim();
  if (requireToken && !token) throw new Error("Enter a bridge token before connecting.");
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

async function bridgeRequest(path, options = {}, { authenticated = true } = {}) {
  const { url, token } = bridgeConnection({ requireToken: authenticated });
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), BRIDGE_TIMEOUT_MS);
  let response;
  try {
    response = await fetch(`${url}${path}`, {
      ...options,
      signal: controller.signal,
      headers: {
        ...(authenticated ? { Authorization: `Bearer ${token}` } : {}),
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

async function bridgeFetch(path, options = {}) {
  return bridgeRequest(path, options, { authenticated: true });
}

function validPairResponse(payload) {
  const expectedKeys = ["protocol_version", "status", "token"];
  if (!isObject(payload)
    || JSON.stringify(Object.keys(payload).sort()) !== JSON.stringify(expectedKeys)
    || payload.status !== "paired"
    || payload.protocol_version !== PAIR_PROTOCOL_VERSION
    || typeof payload.token !== "string") return false;
  return payload.token.length >= MIN_PAIR_TOKEN_LENGTH
    && payload.token.length <= 128
    && /^[A-Za-z0-9_-]+$/.test(payload.token);
}

async function pairBridge() {
  const payload = await bridgeRequest("/v1/pair", {
    method: "POST",
    body: JSON.stringify({ client: PAIR_CLIENT, protocol_version: PAIR_PROTOCOL_VERSION }),
  }, { authenticated: false });
  if (!validPairResponse(payload)) throw new Error("Bridge pairing response was invalid.");
  const connection = bridgeConnection({ requireToken: false });
  const token = payload.token.trim();
  $("bridgeUrl").value = connection.url;
  $("bridgeToken").value = token;
  await chrome.storage.local.set({ bridgeUrl: connection.url, bridgeToken: token });
  state.connectionKey = `${connection.url}\n${token}`;
}

function clearRunOutput({ preserveVerified = false } = {}) {
  if (!preserveVerified) state.completedResults = [];
  $("runPanel").hidden = true;
  $("runStatus").textContent = "Queued";
  $("jobListRate").textContent = "--";
  $("openingRate").textContent = "--";
  $("results").replaceChildren();
  if (state.records.length) renderScannedRecords();
}

function clearScanOutput() {
  closeJobDetail({ restoreFocus: false });
  state.records = [];
  $("recordCount").textContent = "0";
  $("applyCount").textContent = "0 direct Apply URLs";
  $("scanPanel").hidden = true;
  $("scanResults").replaceChildren();
  clearRunOutput();
  syncBusyUi();
}

async function forgetScanSnapshot() {
  await chrome.storage.local.remove(SCAN_SNAPSHOT_KEY);
}

async function clearStaleRun({ preserveInFlight = false } = {}) {
  state.runId = null;
  if (!preserveInFlight) state.runInFlight = false;
  state.pollRetries = 0;
  if (state.pollTimer !== null) clearTimeout(state.pollTimer);
  state.pollTimer = null;
  await chrome.storage.local.remove("runId");
  syncBusyUi();
}

function isObject(value) {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function linkedInJobsUrl(value) {
  try {
    const parsed = new URL(value);
    if (parsed.protocol !== "https:" || parsed.hostname !== "www.linkedin.com"
      || !parsed.pathname.startsWith("/jobs/")) return null;
    parsed.hash = "";
    parsed.searchParams.sort();
    return parsed;
  } catch {
    return null;
  }
}

function linkedInJobId(value) {
  const parsed = linkedInJobsUrl(value);
  if (!parsed) return null;
  const queryId = parsed.searchParams.get("currentJobId");
  if (/^\d+$/.test(queryId || "")) return queryId;
  const pathMatch = parsed.pathname.match(/^\/jobs\/view\/(\d+)(?:\/|$)/);
  return pathMatch ? pathMatch[1] : null;
}

function validExternalApplyCaptureStore(store) {
  if (!isObject(store) || store.schema_version !== EXTERNAL_APPLY_CAPTURE_VERSION
    || !isObject(store.records) || Object.keys(store.records).length > MAX_STORED_RECORDS) return false;
  return Object.entries(store.records).every(([jobId, capture]) => (
    /^\d+$/.test(jobId)
    && isObject(capture)
    && capture.linkedin_job_id === jobId
    && Boolean(safeExternalApplyUrl(capture.external_apply_url))
    && Number.isFinite(capture.captured_at)
  ));
}

function mergeCapturedExternalApply(store) {
  if (!validExternalApplyCaptureStore(store) || state.records.length === 0) return false;
  let changed = false;
  for (const record of state.records) {
    const jobId = linkedInJobId(record.linkedin_job_url);
    const capturedUrl = jobId ? safeExternalApplyUrl(store.records[jobId]?.external_apply_url) : null;
    if (capturedUrl && record.external_apply_url !== capturedUrl) {
      record.external_apply_url = capturedUrl;
      changed = true;
    }
  }
  if (!changed) return false;
  renderExternalApplyCount();
  renderScannedRecords();
  if (state.detailSelection) {
    const sourceRecord = state.detailSelection.sourceRecord;
    openJobDetail(sourceRecord, resultForSource(sourceRecord), { preserveScroll: true });
  }
  return true;
}

function renderExternalApplyCount() {
  const count = state.records.filter((item) => safeExternalApplyUrl(item.external_apply_url)).length;
  $("applyCount").textContent = `${count} direct Apply URLs`;
}

function pageScanContext(value) {
  const parsed = linkedInJobsUrl(value);
  if (!parsed) return null;
  parsed.searchParams.delete("currentJobId");
  parsed.searchParams.sort();
  return parsed.href;
}

function selectedSnapshotJobId(snapshot) {
  for (const record of snapshot.records) {
    const jobId = linkedInJobId(record.linkedin_job_url);
    if (jobId) return jobId;
  }
  return linkedInJobId(snapshot.page_url);
}

function validScanSnapshot(snapshot) {
  if (!isObject(snapshot) || snapshot.schema_version !== SCAN_SNAPSHOT_VERSION
    || !["selected", "page"].includes(snapshot.mode)
    || !Number.isInteger(snapshot.tab_id) || snapshot.tab_id <= 0
    || !linkedInJobsUrl(snapshot.page_url)
    || !Number.isFinite(snapshot.saved_at)
    || !Array.isArray(snapshot.records) || snapshot.records.length === 0
    || snapshot.records.length > MAX_STORED_RECORDS
    || !snapshot.records.every(isObject)
    || (snapshot.verified_results !== undefined
      && (!Array.isArray(snapshot.verified_results)
        || snapshot.verified_results.length > snapshot.records.length
        || !snapshot.verified_results.every(validVerifiedDetail)))) return false;
  const age = Date.now() - snapshot.saved_at;
  return age >= 0 && age <= SCAN_SNAPSHOT_TTL_MS;
}

function scanSnapshotMatchesTab(snapshot, tab) {
  if (!tab?.id || snapshot.tab_id !== tab.id) return false;
  if (snapshot.mode === "page") {
    return pageScanContext(snapshot.page_url) === pageScanContext(tab.url);
  }
  const snapshotJobId = selectedSnapshotJobId(snapshot);
  const activeJobId = linkedInJobId(tab.url);
  if (activeJobId) return Boolean(snapshotJobId && activeJobId === snapshotJobId);
  return linkedInJobsUrl(snapshot.page_url)?.href === linkedInJobsUrl(tab.url)?.href;
}

async function saveScanSnapshot(tab, pageUrl, mode) {
  if (state.records.length === 0) {
    await forgetScanSnapshot();
    return;
  }
  await chrome.storage.local.set({
    [SCAN_SNAPSHOT_KEY]: {
      schema_version: SCAN_SNAPSHOT_VERSION,
      mode,
      tab_id: tab.id,
      page_url: pageUrl,
      saved_at: Date.now(),
      records: state.records,
      verified_results: verifiedDetailsForRecords(state.records, state.completedResults),
    },
  });
}

function validVerifiedDetail(result) {
  if (!isObject(result) || !linkedInJobsUrl(result.linkedin_job_url)) return false;
  const allowed = new Set(VERIFIED_DETAIL_FIELDS);
  return Object.keys(result).every((key) => allowed.has(key))
    && VERIFIED_DETAIL_FIELDS.every((key) => (
      result[key] === undefined || result[key] === null
      || (typeof result[key] === "string" && result[key].length <= 4096)
    ));
}

function verifiedDetailProjection(result) {
  const projected = {};
  for (const field of VERIFIED_DETAIL_FIELDS) {
    if (typeof result[field] === "string" && result[field]) projected[field] = result[field];
  }
  return projected;
}

function verifiedDetailEntriesFromStore(store, now = Date.now()) {
  if (!isObject(store) || store.schema_version !== VERIFIED_DETAILS_VERSION
    || !isObject(store.records)) return [];
  return Object.entries(store.records)
    .filter(([jobId, entry]) => {
      if (!/^\d+$/.test(jobId) || !isObject(entry)
        || entry.linkedin_job_id !== jobId || !Number.isFinite(entry.saved_at)
        || !validVerifiedDetail(entry.result)) return false;
      const age = now - entry.saved_at;
      return age >= 0 && age <= SCAN_SNAPSHOT_TTL_MS
        && linkedInJobId(entry.result.linkedin_job_url) === jobId;
    })
    .sort((left, right) => right[1].saved_at - left[1].saved_at)
    .slice(0, MAX_STORED_RECORDS)
    .map(([jobId, entry]) => ({
      jobId,
      savedAt: entry.saved_at,
      result: verifiedDetailProjection(entry.result),
    }));
}

function verifiedDetailsFromStore(store, now = Date.now()) {
  return verifiedDetailEntriesFromStore(store, now).map((entry) => entry.result);
}

function verifiedDetailsForRecords(records, ...sources) {
  const allowedJobIds = new Set(records.map((record) => linkedInJobId(record.linkedin_job_url)).filter(Boolean));
  const selected = new Map();
  for (const source of sources) {
    for (const result of Array.isArray(source) ? source : []) {
      if (!isObject(result)) continue;
      const projected = verifiedDetailProjection(result);
      if (!validVerifiedDetail(projected)) continue;
      const jobId = linkedInJobId(projected.linkedin_job_url);
      if (jobId && allowedJobIds.has(jobId) && !selected.has(jobId)) {
        selected.set(jobId, projected);
      }
    }
  }
  return [...selected.values()];
}

async function hydrateVerifiedDetailsForCurrentRecords(store) {
  const indexed = store === undefined
    ? verifiedDetailsFromStore((await chrome.storage.local.get(VERIFIED_DETAILS_KEY))[VERIFIED_DETAILS_KEY])
    : verifiedDetailsFromStore(store);
  state.completedResults = verifiedDetailsForRecords(
    state.records,
    state.completedResults,
    indexed,
  );
}

async function persistVerifiedDetails(results) {
  const saved = await chrome.storage.local.get([SCAN_SNAPSHOT_KEY, VERIFIED_DETAILS_KEY]);
  const snapshot = saved[SCAN_SNAPSHOT_KEY];
  if (!validScanSnapshot(snapshot)) return;
  const verifiedResults = verifiedDetailsForRecords(snapshot.records, results);
  const now = Date.now();
  const merged = new Map(verifiedDetailEntriesFromStore(saved[VERIFIED_DETAILS_KEY], now)
    .map((entry) => [entry.jobId, entry]));
  for (const result of verifiedResults) {
    const jobId = linkedInJobId(result.linkedin_job_url);
    merged.set(jobId, { jobId, savedAt: now, result });
  }
  const indexRecords = {};
  for (const entry of [...merged.values()]
    .sort((left, right) => right.savedAt - left.savedAt)
    .slice(0, MAX_STORED_RECORDS)) {
    indexRecords[entry.jobId] = {
      linkedin_job_id: entry.jobId,
      saved_at: entry.savedAt,
      result: verifiedDetailProjection(entry.result),
    };
  }
  await chrome.storage.local.set({
    [SCAN_SNAPSHOT_KEY]: {
      ...snapshot,
      saved_at: now,
      verified_results: verifiedResults,
    },
    [VERIFIED_DETAILS_KEY]: {
      schema_version: VERIFIED_DETAILS_VERSION,
      records: indexRecords,
    },
  });
}

async function restoreScanSnapshot(snapshot, verifiedStore) {
  if (snapshot === undefined) return;
  if (!validScanSnapshot(snapshot)) {
    await forgetScanSnapshot();
    return;
  }
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!scanSnapshotMatchesTab(snapshot, tab)) {
    if (tab?.id === snapshot.tab_id) await forgetScanSnapshot();
    return;
  }
  state.records = snapshot.records;
  state.completedResults = verifiedDetailsForRecords(
    state.records,
    snapshot.verified_results || [],
    verifiedDetailsFromStore(verifiedStore),
  );
  $("recordCount").textContent = String(state.records.length);
  renderExternalApplyCount();
  renderScannedRecords();
  syncBusyUi();
}

function validScanResponse(payload) {
  const states = new Set(["ready", "partial", "not_ready"]);
  return isObject(payload) && typeof payload.ok === "boolean" && Array.isArray(payload.records)
    && typeof payload.page_url === "string" && payload.page_url.length > 0
    && (payload.scan_version === undefined || payload.scan_version === "2")
    && (payload.state === undefined || states.has(payload.state))
    && payload.records.every(isObject);
}

function validPageScanResponse(payload) {
  const states = new Set(["ready", "partial", "cancelled", "not_ready"]);
  return isObject(payload) && typeof payload.ok === "boolean" && Array.isArray(payload.records)
    && typeof payload.page_url === "string" && payload.page_url.length > 0
    && payload.scan_version === "3" && states.has(payload.state)
    && Number.isInteger(payload.scanned_count) && payload.scanned_count >= 0
    && Number.isInteger(payload.candidate_count) && payload.candidate_count >= 0
    && Number.isInteger(payload.failure_count) && payload.failure_count >= 0
    && payload.records.every(isObject);
}

async function loadSettings() {
  const saved = await chrome.storage.local.get([
    "bridgeUrl", "bridgeToken", "runId", SCAN_SNAPSHOT_KEY, VERIFIED_DETAILS_KEY,
    EXTERNAL_APPLY_CAPTURES_KEY,
  ]);
  $("bridgeUrl").value = saved.bridgeUrl || DEFAULT_BRIDGE_URL;
  if (saved.bridgeToken) $("bridgeToken").value = saved.bridgeToken;
  state.runId = typeof saved.runId === "string" && saved.runId ? saved.runId : null;
  state.runInFlight = Boolean(state.runId);
  await restoreScanSnapshot(saved[SCAN_SNAPSHOT_KEY], saved[VERIFIED_DETAILS_KEY]);
  mergeCapturedExternalApply(saved[EXTERNAL_APPLY_CAPTURES_KEY]);
  try {
    state.connectionKey = bridgeConnection().key;
  } catch {
    state.connectionKey = null;
  }
  syncBusyUi();
  const connected = await connectOnStartup();
  if (connected && state.runId) await pollRun();
}

async function requestHealth() {
  const payload = await bridgeFetch("/v1/health");
  if (!isObject(payload) || payload.status !== "ok") {
    throw new BridgeRequestError("Bridge health response was invalid.");
  }
  return true;
}

async function connectOnStartup() {
  setBridgeState("Connecting", "busy");
  setMessage();
  try {
    const hasSavedToken = Boolean($("bridgeToken").value.trim());
    if (hasSavedToken) {
      try {
        await requestHealth();
      } catch (error) {
        if (!(error instanceof BridgeRequestError) || ![401, 403].includes(error.status)) throw error;
        await pairBridge();
        await requestHealth();
      }
    } else {
      await pairBridge();
      await requestHealth();
    }
    setBridgeState("Online", "online");
    return true;
  } catch (error) {
    setBridgeState("Offline", "error");
    if (error instanceof BridgeRequestError && error.status === 0) {
      setMessage("Bridge unavailable.");
    } else if (error instanceof BridgeRequestError && error.status === 410) {
      setMessage("Restart the local agent to pair again.");
    } else if (error instanceof BridgeRequestError && error.status === 409) {
      setMessage("Local agent is paired with another extension.");
    } else if (error instanceof BridgeRequestError && error.status === 403
      && error.message === "pairing_disabled") {
      setMessage("Enter the explicit token under Advanced connection.");
    } else {
      setMessage(error.message || "Bridge unavailable.");
    }
    return false;
  }
}

async function checkHealth() {
  try {
    await requestHealth();
    setBridgeState("Online", "online");
    return true;
  } catch (error) {
    setBridgeState("Offline", "error");
    setMessage(error.message || "Bridge unavailable.");
    return false;
  }
}

function validContentStatus(payload, requiredScanVersion) {
  return isObject(payload) && payload.ok === true
    && payload.content_script_version === CONTENT_SCRIPT_VERSION
    && Array.isArray(payload.scan_versions)
    && payload.scan_versions.includes(requiredScanVersion);
}

async function readContentStatus(tabId) {
  try {
    return await chrome.tabs.sendMessage(tabId, { type: CONTENT_STATUS_MESSAGE });
  } catch {
    return null;
  }
}

async function ensureContentScript(tabId, requiredScanVersion) {
  const current = await readContentStatus(tabId);
  if (validContentStatus(current, requiredScanVersion)) return;
  try {
    await chrome.scripting.executeScript({ target: { tabId }, files: ["content.js"] });
  } catch (error) {
    throw new Error(`Page scan failed: ${error?.message || "content script injection failed."}`);
  }
  const upgraded = await readContentStatus(tabId);
  if (!validContentStatus(upgraded, requiredScanVersion)) {
    throw new Error("LinkedIn page scanner could not be updated. Reload the page and try again.");
  }
}

async function requestSelectedScan(tabId, attempt = 0) {
  const response = await chrome.tabs.sendMessage(tabId, { type: SELECTED_SCAN_MESSAGE });
  if (!validScanResponse(response)) throw new Error("Page scan returned an invalid response.");
  if (response.state === "not_ready") {
    if (attempt < MAX_SCAN_RETRIES) {
      await new Promise((resolve) => setTimeout(resolve, SCAN_RETRY_DELAY_MS));
      return requestSelectedScan(tabId, attempt + 1);
    }
    throw new Error("LinkedIn Jobs is still loading. Wait a moment and scan again.");
  }
  if (!response.ok) throw new Error(payloadMessage(response, "Page scan failed."));
  return response;
}

async function scanSelected() {
  if (hasBusyOperation() || state.runInFlight) return;
  setBusy("selectedScan", true);
  clearScanOutput();
  setMessage();
  try {
    await forgetScanSnapshot();
    await clearStaleRun();
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (!tab?.id || !tab.url?.startsWith("https://www.linkedin.com/jobs/")) {
      throw new Error("Open a LinkedIn Jobs page first.");
    }
    await ensureContentScript(tab.id, "2");
    const response = await requestSelectedScan(tab.id);
    state.records = response.records;
    await hydrateVerifiedDetailsForCurrentRecords();
    await saveScanSnapshot(tab, response.page_url, "selected");
    $("recordCount").textContent = String(state.records.length);
    renderExternalApplyCount();
    renderScannedRecords();
    if (response.state === "partial") {
      setMessage("Selected job found, but its detail panel was not fully observed.");
    } else if (state.records.length === 0) {
      setMessage("No eligible jobs were found on this page.");
    }
  } catch (error) {
    setMessage(error.message || "Selected scan failed.");
  } finally {
    setBusy("selectedScan", false);
  }
}

function pageScanIsActive(id) {
  return state.pageScan?.id === id;
}

function finishPageScan(id) {
  if (!pageScanIsActive(id)) return false;
  clearTimeout(state.pageScan.watchdogTimer);
  state.pageScan = null;
  setBusy("pageScan", false);
  return true;
}

async function cancelPageScan({ watchdog = false } = {}) {
  const pageScan = state.pageScan;
  if (!pageScan || pageScan.cancelling) return;
  pageScan.cancelling = true;
  try {
    if (!pageScan.tabId) {
      setMessage(watchdog ? "Page scan timed out and was cancelled." : "Scan cancelled.");
      return;
    }
    const response = await chrome.tabs.sendMessage(pageScan.tabId, { type: CANCEL_PAGE_SCAN_MESSAGE });
    if (!isObject(response) || response.ok !== true || typeof response.cancelled !== "boolean") {
      throw new Error("Page scan cancellation returned an invalid response.");
    }
    setMessage(watchdog ? "Page scan timed out and was cancelled." : "Scan cancelled.");
  } catch (error) {
    setMessage(error.message || "Page scan cancellation failed.");
  } finally {
    finishPageScan(pageScan.id);
  }
}

function renderPageScan(response) {
  state.records = response.records;
  $("recordCount").textContent = String(state.records.length);
  renderExternalApplyCount();
  renderScannedRecords();
  if (response.state === "partial") {
    const missingDetails = Number.isInteger(response.detail_not_observed_count)
      ? response.detail_not_observed_count : 0;
    setMessage(missingDetails
      ? `Partial scan: ${missingDetails} job detail panels were not observed.`
      : `Partial results: ${response.failure_count} failures.`);
  } else if (response.state === "cancelled") {
    setMessage("Scan cancelled.");
  } else if (response.state === "not_ready") {
    setMessage("Page is not ready.");
  } else if (state.records.length === 0) {
    setMessage("No eligible jobs were found on this page.");
  } else {
    setMessage();
  }
}

async function scanLoadedPage() {
  if (state.pageScan) {
    await cancelPageScan();
    return;
  }
  if (hasBusyOperation() || state.runInFlight) return;
  const pageScan = { id: state.nextPageScanId++, tabId: null, watchdogTimer: null, cancelling: false };
  state.pageScan = pageScan;
  setBusy("pageScan", true);
  clearScanOutput();
  setMessage();
  try {
    await forgetScanSnapshot();
    await clearStaleRun();
    if (!pageScanIsActive(pageScan.id)) return;
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (!pageScanIsActive(pageScan.id)) return;
    if (!tab?.id || !tab.url?.startsWith("https://www.linkedin.com/jobs/")) {
      throw new Error("Open a LinkedIn Jobs page first.");
    }
    pageScan.tabId = tab.id;
    pageScan.watchdogTimer = setTimeout(() => { cancelPageScan({ watchdog: true }); }, PAGE_SCAN_WATCHDOG_MS);
    await ensureContentScript(tab.id, "3");
    const response = await chrome.tabs.sendMessage(tab.id, { type: PAGE_SCAN_MESSAGE });
    if (!pageScanIsActive(pageScan.id)) return;
    if (!validPageScanResponse(response)) throw new Error("Page scan returned an invalid response.");
    if (!response.ok && response.state !== "partial" && response.state !== "cancelled") {
      throw new Error(payloadMessage(response, "Page scan failed."));
    }
    state.records = response.records;
    await hydrateVerifiedDetailsForCurrentRecords();
    await saveScanSnapshot(tab, response.page_url, "page");
    renderPageScan(response);
  } catch (error) {
    if (pageScanIsActive(pageScan.id)) setMessage(error.message || "Page scan failed.");
  } finally {
    finishPageScan(pageScan.id);
  }
}

function handlePageScanProgress(message, sender) {
  const pageScan = state.pageScan;
  if (!pageScan || pageScan.cancelling || sender?.tab?.id !== pageScan.tabId) return;
  if (!Number.isInteger(message.scanned_count) || message.scanned_count < 0
    || !Number.isInteger(message.candidate_count) || message.candidate_count < 0) return;
  setMessage(`Scanning ${message.scanned_count}/${message.candidate_count}`);
}

function validSubmission(payload) {
  const statuses = new Set(["queued", "running", "complete", "failed"]);
  return isObject(payload) && typeof payload.run_id === "string" && payload.run_id.length > 0
    && statuses.has(payload.status)
    && validRunProgress(payload)
    && (payload.status !== "failed" || payload.error === undefined || typeof payload.error === "string");
}

function validRunProgress(payload) {
  const hasSubmitted = payload.submitted !== undefined;
  const hasCompleted = payload.completed !== undefined;
  if (!hasSubmitted && !hasCompleted) return true;
  return hasSubmitted && hasCompleted
    && Number.isInteger(payload.submitted) && payload.submitted > 0 && payload.submitted <= 30
    && Number.isInteger(payload.completed) && payload.completed >= 0
    && payload.completed <= payload.submitted;
}

function runStatusLabel(payload) {
  const label = payload.status.charAt(0).toUpperCase() + payload.status.slice(1);
  return payload.submitted !== undefined
    ? `${label} ${payload.completed}/${payload.submitted}`
    : label;
}

async function runDiscovery() {
  if (hasBusyOperation() || state.records.length === 0) return;
  state.runInFlight = true;
  setMessage();
  setBusy("run", true);
  setBridgeState("Submitting", "busy");
  clearRunOutput();
  $("runPanel").hidden = false;
  $("runStatus").textContent = "Submitting";
  try {
    await clearStaleRun({ preserveInFlight: true });
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
    $("runStatus").textContent = runStatusLabel(payload);
  } catch (error) {
    state.runInFlight = false;
    $("runStatus").textContent = "Submission failed";
    setBridgeState("Error", "error");
    setMessage(error.message || "Run submission failed.");
  } finally {
    setBusy("run", false);
  }
  if (state.runId) await pollRun();
}

function validRunResponse(payload, runId) {
  const statuses = new Set(["queued", "running", "complete", "failed"]);
  if (!isObject(payload) || payload.run_id !== runId || !statuses.has(payload.status)
    || !validRunProgress(payload)) return false;
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
    $("runStatus").textContent = runStatusLabel(payload);
    if (payload.status === "complete") {
      state.runInFlight = false;
      await renderCompletedRun(payload);
      setBridgeState("Online", "online");
    } else if (payload.status === "failed") {
      state.runInFlight = false;
      setBridgeState("Error", "error");
      setMessage(payload.error || "Discovery failed.");
    } else {
      state.runInFlight = true;
      setBridgeState(payload.status === "queued" ? "Queued" : "Running", "busy");
      schedulePoll();
    }
  } catch (error) {
    if (state.runId !== runId) return;
    if (error instanceof BridgeRequestError && error.status === 404) {
      await clearStaleRun();
      clearRunOutput({ preserveVerified: true });
      setBridgeState("Online", "online");
      setMessage("Previous run is no longer available; scanned jobs are still ready.");
    } else if (error instanceof BridgeRequestError && error.status === 401) {
      await clearStaleRun();
      setBridgeState("Offline", "error");
      setMessage("Bridge token was rejected.");
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

function safeExternalApplyUrl(value) {
  const safeUrl = safeHttpsUrl(value);
  if (!safeUrl) return null;
  try {
    const parsed = new URL(safeUrl);
    const host = parsed.hostname.toLowerCase();
    if (parsed.hash || /(?:linkedin|licdn)/i.test(host)) return null;
    const sensitiveKey = /(?:^|[_-])(?:token|session|auth|authorization|api[_-]?key|csrf|xsrf|secret|password|credential|signature|sig)(?:$|[_-])/i;
    if (Array.from(parsed.searchParams.keys()).some((key) => sensitiveKey.test(key))) return null;
    return parsed.href;
  } catch {
    return null;
  }
}

function safeExportHttpsUrl(value) {
  const safeUrl = safeHttpsUrl(value);
  if (!safeUrl) return null;
  try {
    const parsed = new URL(safeUrl);
    if (parsed.hash) return null;
    const sensitiveKey = /(?:^|[_-])(?:token|session|auth|authorization|api[_-]?key|csrf|xsrf|secret|password|credential|signature|sig)(?:$|[_-])/i;
    if (Array.from(parsed.searchParams.keys()).some((key) => sensitiveKey.test(key))) return null;
    return parsed.href;
  } catch {
    return null;
  }
}

function safeLinkedInPostingUrl(value) {
  const jobId = linkedInJobId(value);
  return jobId ? `https://www.linkedin.com/jobs/view/${jobId}` : null;
}

function exportResultStatus(result, row) {
  if (["success", "partial", "failed", "unsupported"].includes(result?.pipeline_status)) {
    return result.pipeline_status;
  }
  if (row.external_apply_url) return "external_apply_captured";
  return result ? "verification_failed" : "not_verified";
}

function exportRow(sourceRecord) {
  const result = resultForSource(sourceRecord);
  const row = {
    company_name: sourceRecord.company_name || result?.company_name || "",
    linkedin_job_title: sourceRecord.linkedin_job_title || sourceRecord.job_title
      || result?.linkedin_job_title || result?.job_title || "",
    linkedin_job_url: safeLinkedInPostingUrl(sourceRecord.linkedin_job_url)
      || safeLinkedInPostingUrl(result?.linkedin_job_url) || "",
    company_website_url: safeExportHttpsUrl(result?.company_website_url)
      || safeExportHttpsUrl(sourceRecord.company_website_url) || "",
    career_page_url: safeExportHttpsUrl(result?.career_page_url || result?.career_root_url)
      || safeExportHttpsUrl(sourceRecord.career_page_url || sourceRecord.career_root_url) || "",
    job_list_page_url: safeExportHttpsUrl(result?.job_list_page_url)
      || safeExportHttpsUrl(sourceRecord.job_list_page_url) || "",
    external_apply_url: safeExternalApplyUrl(sourceRecord.external_apply_url)
      || safeExternalApplyUrl(result?.external_apply_url) || "",
    open_position_url: safeExportHttpsUrl(result?.open_position_url)
      || safeExportHttpsUrl(sourceRecord.open_position_url) || "",
    result_status: "",
    error_code: typeof result?.error_code === "string" ? result.error_code
      : (typeof sourceRecord.error_code === "string" ? sourceRecord.error_code : ""),
  };
  row.result_status = exportResultStatus(result, row);
  return row;
}

function csvCell(value) {
  const raw = String(value ?? "");
  const text = /^[\t\r\n ]*[=+\-@]/.test(raw) ? `'${raw}` : raw;
  return /[",\r\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
}

function exportCsv() {
  if (state.records.length === 0) return;
  const lines = [
    CSV_EXPORT_FIELDS.join(","),
    ...state.records.map((sourceRecord) => {
      const row = exportRow(sourceRecord);
      return CSV_EXPORT_FIELDS.map((field) => csvCell(row[field])).join(",");
    }),
  ];
  const objectUrl = URL.createObjectURL(new Blob([`\ufeff${lines.join("\r\n")}\r\n`], {
    type: "text/csv;charset=utf-8",
  }));
  const link = document.createElement("a");
  link.href = objectUrl;
  link.download = CSV_EXPORT_FILENAME;
  link.hidden = true;
  document.body.append(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(objectUrl);
  setMessage(`Exported ${state.records.length} job${state.records.length === 1 ? "" : "s"}.`);
}

function resultTitle(record) {
  const company = typeof record.company_name === "string" && record.company_name
    ? record.company_name : "Unknown company";
  const title = typeof record.linkedin_job_title === "string" && record.linkedin_job_title
    ? record.linkedin_job_title
    : (typeof record.job_title === "string" && record.job_title ? record.job_title : "Untitled role");
  return `${company} · ${title}`;
}

function sourceObservation(record) {
  const observation = record?.source_trace?.linkedin_posting?.observation_state;
  return {
    external_apply_observed: "External Apply button found; open details to capture URL",
    linkedin_native_observed: "LinkedIn native apply",
    closed_observed: "Posting closed",
    detail_observed_but_apply_absent: "Apply control not observed",
    detail_not_observed: "Detail panel not observed",
  }[observation] || "LinkedIn job selected";
}

function resultForSource(record) {
  return state.completedResults.find((result) => (
    record.linkedin_job_url && record.linkedin_job_url === result.linkedin_job_url
  )) || state.completedResults.find((result) => (
    record.company_name === result.company_name
    && record.job_title === (result.linkedin_job_title || result.job_title)
  )) || null;
}

function resultSummary(result, sourceRecord) {
  if (!result) return safeExternalApplyUrl(sourceRecord?.external_apply_url)
    ? "External Apply URL ready" : sourceObservation(sourceRecord);
  if (safeHttpsUrl(result.open_position_url)) return "Exact opening verified";
  if (safeHttpsUrl(result.job_list_page_url)) return "Official job list verified";
  if (safeHttpsUrl(result.career_page_url || result.career_root_url)) return "Career page verified";
  return typeof result.error_code === "string" && result.error_code
    ? result.error_code
    : "No verified public job URL";
}

function makeJobRow(record, result = resultForSource(record)) {
  const item = document.createElement("li");
  const button = document.createElement("button");
  button.type = "button";
  button.className = "job-row";
  button.setAttribute("aria-label", `View details for ${resultTitle(record)}`);
  const copy = document.createElement("span");
  copy.className = "job-row-copy";
  const title = document.createElement("strong");
  title.textContent = resultTitle(record);
  const summary = document.createElement("span");
  summary.textContent = resultSummary(result, record);
  copy.append(title, summary);
  const chevron = document.createElement("span");
  chevron.className = "job-row-chevron";
  chevron.setAttribute("aria-hidden", "true");
  chevron.textContent = "\u203a";
  button.append(copy, chevron);
  button.addEventListener("click", () => openJobDetail(record, resultForSource(record) || result));
  item.append(button);
  return item;
}

function renderScannedRecords() {
  $("scanPanel").hidden = state.records.length === 0;
  $("scanResults").replaceChildren(...state.records.map((record) => makeJobRow(record)));
}

function sourceRecordFor(result) {
  return state.records.find((record) => (
    record.linkedin_job_url && record.linkedin_job_url === result.linkedin_job_url
  )) || state.records.find((record) => (
    record.company_name === result.company_name
    && record.job_title === (result.linkedin_job_title || result.job_title)
  ));
}

async function renderCompletedRun(payload) {
  const { summary, results } = payload;
  state.completedResults = results;
  await persistVerifiedDetails(results);
  $("jobListRate").textContent = `${Math.round(summary.rates.job_list * 100)}%`;
  $("openingRate").textContent = `${Math.round(summary.rates.opening * 100)}%`;
  renderScannedRecords();
  $("results").replaceChildren(...results.map((result) => {
    const sourceRecord = sourceRecordFor(result) || result;
    return makeJobRow(sourceRecord, result);
  }));
  if (state.detailSelection) {
    const sourceRecord = state.detailSelection.sourceRecord;
    openJobDetail(sourceRecord, resultForSource(sourceRecord), { preserveScroll: true });
  }
}

function externalApplyCaptureError(error) {
  return {
    invalid_linkedin_job_id: "This LinkedIn job has no stable job ID.",
    not_linkedin_jobs_page: "Return to the LinkedIn Jobs tab and try again.",
    linkedin_job_card_not_available: "This job card is no longer loaded on the LinkedIn page.",
    linkedin_job_selection_failed: "LinkedIn could not select this job.",
    linkedin_job_selection_timed_out: "LinkedIn did not finish loading this job detail.",
    linkedin_job_identity_mismatch: "The visible LinkedIn job did not match this record.",
    linkedin_job_detail_not_observed: "The LinkedIn job detail is not ready yet.",
    external_apply_control_not_available: "No external Apply control is available for this job.",
    page_scan_in_progress: "Finish or cancel the page scan before opening Apply.",
  }[error] || "The external Apply destination could not be captured.";
}

async function beginExternalApplyCapture(sourceRecord) {
  const jobId = linkedInJobId(sourceRecord?.linkedin_job_url);
  if (!jobId || state.captureJobId) return;
  state.captureJobId = jobId;
  let captureStatusText = "Opening the company Apply page...";
  openJobDetail(sourceRecord, resultForSource(sourceRecord), { preserveScroll: true });
  $("detailStatus").textContent = captureStatusText;
  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (!tab?.id || !linkedInJobsUrl(tab.url)) throw new Error("not_linkedin_jobs_page");
    await ensureContentScript(tab.id, "2");
    const prepared = await chrome.tabs.sendMessage(tab.id, {
      type: "prepare_external_apply_v1",
      linkedin_job_id: jobId,
    });
    if (!isObject(prepared) || prepared.ok !== true || prepared.status !== "ready"
      || prepared.linkedin_job_id !== jobId) {
      throw new Error(prepared?.error || "external_apply_prepare_failed");
    }
    const response = await chrome.runtime.sendMessage({
      type: "begin_external_apply_capture_v1",
      source_tab_id: tab.id,
      linkedin_job_id: jobId,
    });
    if (!isObject(response) || response.ok !== true
      || !["pending", "captured"].includes(response.status)) {
      throw new Error(response?.error || "external_apply_capture_failed");
    }
    captureStatusText = response.status === "captured"
      ? "External Apply URL captured"
      : "Waiting for the company Apply page to finish loading...";
  } catch (error) {
    captureStatusText = externalApplyCaptureError(error?.message);
  } finally {
    state.captureJobId = null;
    if (state.detailSelection?.sourceRecord === sourceRecord) {
      openJobDetail(sourceRecord, resultForSource(sourceRecord), { preserveScroll: true });
      $("detailStatus").textContent = captureStatusText;
    }
  }
}

function detailLinkRows(sourceRecord, result) {
  const externalUrl = safeExternalApplyUrl(sourceRecord.external_apply_url)
    || safeExternalApplyUrl(result?.external_apply_url);
  const externalObserved = sourceRecord.source_trace?.linkedin_posting?.observation_state
    === "external_apply_observed";
  return [
    { label: "LinkedIn posting", url: sourceRecord.linkedin_job_url || result?.linkedin_job_url, unavailable: "Unavailable" },
    {
      label: "External Apply",
      url: externalUrl,
      unavailable: externalObserved ? "Button found; destination not captured" : "Not observed",
      action: !externalUrl && externalObserved ? {
        label: state.captureJobId === linkedInJobId(sourceRecord.linkedin_job_url)
          ? "Opening..." : "Open Apply",
        disabled: Boolean(state.captureJobId),
        run: () => beginExternalApplyCapture(sourceRecord),
      } : null,
    },
    { label: "Company website", url: result?.company_website_url || sourceRecord.company_website_url, unavailable: result ? "Not found" : "Not verified yet" },
    { label: "Career page", url: result?.career_page_url || result?.career_root_url, unavailable: result ? "Not found" : "Not verified yet" },
    { label: "Job list", url: result?.job_list_page_url, unavailable: result ? "Not found" : "Not verified yet" },
    { label: "Exact opening", url: result?.open_position_url, unavailable: result ? "Not found" : "Not verified yet" },
  ];
}

function detailStatus(result) {
  if (!result) return "Source verification not run";
  if (safeHttpsUrl(result.open_position_url)) return "Verified exact opening";
  if (safeHttpsUrl(result.job_list_page_url)) return "Verified job list; exact opening unavailable";
  if (typeof result.error_code === "string" && result.error_code) {
    return `Verification result: ${result.error_code}`;
  }
  return "Verification complete; no public source verified";
}

function renderDetailLink({ label, url, unavailable, action }) {
  const row = document.createElement("div");
  row.className = "detail-link-row";
  const heading = document.createElement("span");
  heading.className = "detail-link-label";
  heading.textContent = label;
  row.append(heading);
  const safeUrl = safeHttpsUrl(url);
  if (safeUrl) {
    const link = document.createElement("a");
    link.href = safeUrl;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    link.textContent = safeUrl;
    row.append(link);
  } else {
    const value = document.createElement("span");
    value.className = "detail-link-value";
    const unavailableLabel = document.createElement("span");
    unavailableLabel.className = "detail-link-unavailable";
    unavailableLabel.textContent = unavailable;
    value.append(unavailableLabel);
    if (action) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "quiet detail-link-action";
      button.textContent = action.label;
      button.disabled = action.disabled;
      button.addEventListener("click", action.run);
      value.append(button);
    }
    row.append(value);
  }
  return row;
}

function openJobDetail(sourceRecord, result = resultForSource(sourceRecord), { preserveScroll = false } = {}) {
  if (!sourceRecord) return;
  if (!preserveScroll) state.listScrollTop = $("scanResults").scrollTop || 0;
  state.detailSelection = { sourceRecord };
  $("detailTitle").textContent = sourceRecord.job_title
    || result?.linkedin_job_title || result?.job_title || "Untitled role";
  $("detailCompany").textContent = sourceRecord.company_name || result?.company_name || "Unknown company";
  $("detailLocation").textContent = sourceRecord.job_location
    || result?.linkedin_job_location || "Location unavailable";
  $("detailLinks").replaceChildren(...detailLinkRows(sourceRecord, result).map(renderDetailLink));
  $("detailStatus").textContent = detailStatus(result);
  $("listView").hidden = true;
  $("detailView").hidden = false;
  $("detailBackButton").focus?.();
}

function closeJobDetail({ restoreFocus = true } = {}) {
  if (!state.detailSelection && $("detailView").hidden) return;
  const previous = state.detailSelection;
  state.detailSelection = null;
  $("detailView").hidden = true;
  $("listView").hidden = false;
  $("scanResults").scrollTop = state.listScrollTop;
  if (restoreFocus && previous) {
    const index = state.records.indexOf(previous.sourceRecord);
    $("scanResults").children[index]?.children[0]?.focus?.();
  }
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

chrome.runtime.onMessage.addListener((message, sender) => {
  if (message?.type === "job_source_page_progress") handlePageScanProgress(message, sender);
});
chrome.storage.onChanged?.addListener?.((changes, areaName) => {
  if (areaName !== "local" || !changes[EXTERNAL_APPLY_CAPTURES_KEY]) return;
  mergeCapturedExternalApply(changes[EXTERNAL_APPLY_CAPTURES_KEY].newValue);
});
$("scanSelectedButton").addEventListener("click", scanSelected);
$("scanPageButton").addEventListener("click", scanLoadedPage);
$("runButton").addEventListener("click", runDiscovery);
$("exportCsvButton").addEventListener("click", exportCsv);
$("refreshButton").addEventListener("click", pollRun);
$("saveButton").addEventListener("click", saveConnection);
$("detailBackButton").addEventListener("click", () => closeJobDetail());
loadSettings();
