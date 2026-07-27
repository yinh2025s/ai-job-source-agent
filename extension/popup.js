const BRIDGE_TIMEOUT_MS = 8000;
const POLL_DELAY_MS = 2000;
const SCAN_RETRY_DELAY_MS = 350;
const MAX_SCAN_RETRIES = 2;
const MAX_POLL_RETRIES = 2;
const PAGE_SCAN_WATCHDOG_MS = 120000;
const CAPTURE_TTL_MS = 5 * 60 * 1000;
const CAPTURE_SESSION_KEY = "externalApplyCaptureSession";
const CAPTURE_RECORD_KEY = "externalApplyCapturedRecord";

const state = {
  records: [],
  runId: null,
  pollTimer: null,
  pollRetries: 0,
  connectionKey: null,
  runInFlight: false,
  pageScan: null,
  captureSession: null,
  captureTargetTab: null,
  nextPageScanId: 1,
  busy: { selectedScan: false, pageScan: false, run: false, poll: false, save: false, capture: false },
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
  $("scanSelectedButton").disabled = busy;
  $("scanPageButton").disabled = busy && !pageScanActive;
  $("scanPageButton").textContent = pageScanActive ? "Cancel scan" : "Scan page";
  $("runButton").disabled = busy || state.records.length === 0 || state.runInFlight;
  $("runButton").textContent = state.runInFlight ? "Verifying..." : "Verify source";
  $("refreshButton").disabled = busy || !state.runId;
  $("saveButton").disabled = busy;
  $("confirmCaptureButton").disabled = busy || state.captureSession?.state !== "target_presented";
  $("cancelCaptureButton").disabled = busy || !state.captureSession;
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

function captureApi() {
  const api = globalThis.JobSourceCaptureSession;
  if (!api || typeof api.arm !== "function") throw new Error("Capture contract is unavailable.");
  return api;
}

function captureBinding(session = state.captureSession) {
  return {
    capture_id: session.capture_id,
    linkedin_job_id: session.linkedin_job_id,
    linkedin_job_url: session.linkedin_job_url,
  };
}

function captureErrorMessage(reason) {
  const messages = {
    capture_expired: "External Apply capture expired.",
    permission_unavailable: "The active target page is unavailable.",
    source_identity_changed: "The selected LinkedIn job changed.",
    external_control_not_observed: "No eligible company-website Apply button is selected.",
    target_not_observed: "The company target page was not observed.",
    target_is_linkedin: "The captured page is still owned by LinkedIn.",
    unsafe_target_url: "The target page URL was rejected.",
    sensitive_target_url: "The target page contains sensitive URL data.",
    ambiguous_capture: "The target page could not be bound to one job.",
    bridge_validation_failed: "The local backend rejected the target URL.",
  };
  return messages[reason] || "External Apply capture failed.";
}

function renderCapturePanel() {
  const session = state.captureSession;
  $("capturePanel").hidden = !session;
  if (!session) {
    $("captureStatus").textContent = "";
    $("captureTitle").textContent = "";
    $("captureCompany").textContent = "";
    $("captureTarget").textContent = "";
    syncBusyUi();
    return;
  }
  $("captureTitle").textContent = session.title;
  $("captureCompany").textContent = session.company;
  if (session.state === "target_presented") {
    $("captureStatus").textContent = "Target ready";
    $("captureTarget").textContent = new URL(session.target_url).hostname;
  } else {
    $("captureStatus").textContent = "Waiting";
    $("captureTarget").textContent = "";
  }
  syncBusyUi();
}

function validCapturedRecord(record) {
  if (!isObject(record) || record.source !== "linkedin_browser_extension") return false;
  const safeUrl = globalThis.JobSourceExternalApplySafety?.sanitize(record.external_apply_url);
  const posting = record.source_trace?.linkedin_posting;
  return Boolean(
    safeUrl && safeUrl === record.external_apply_url
    && /^https:\/\/www\.linkedin\.com\/jobs\/view\/\d+$/.test(record.linkedin_job_url || "")
    && typeof record.company_name === "string" && record.company_name
    && typeof record.job_title === "string" && record.job_title
    && posting?.evidence_source === captureApi().PROVENANCE
    && posting?.job_url === record.linkedin_job_url
    && posting?.apply_mode === "external"
  );
}

function renderRecordSet(records) {
  state.records = records;
  $("recordCount").textContent = String(records.length);
  $("applyCount").textContent = `${records.filter((item) => item.external_apply_url).length} Apply URLs`;
  renderScannedRecords();
}

async function clearCaptureSession() {
  state.captureSession = null;
  state.captureTargetTab = null;
  await chrome.storage.session.remove(CAPTURE_SESSION_KEY);
  renderCapturePanel();
}

async function failCapture(reason) {
  await clearCaptureSession();
  setMessage(captureErrorMessage(reason));
}

async function restoreCaptureState() {
  if (!chrome.storage?.session) return;
  const saved = await chrome.storage.session.get([CAPTURE_SESSION_KEY, CAPTURE_RECORD_KEY]);
  if (validCapturedRecord(saved[CAPTURE_RECORD_KEY])) {
    renderRecordSet([saved[CAPTURE_RECORD_KEY]]);
  } else if (saved[CAPTURE_RECORD_KEY] !== undefined) {
    await chrome.storage.session.remove(CAPTURE_RECORD_KEY);
  }
  const stored = saved[CAPTURE_SESSION_KEY];
  if (stored === undefined) return;
  const recovered = captureApi().recover(stored, new Date().toISOString());
  if (!recovered.ok) {
    await failCapture(recovered.reason);
    return;
  }
  state.captureSession = recovered.session;
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab?.id || typeof tab.url !== "string") {
    await failCapture("permission_unavailable");
    return;
  }
  if (state.captureSession.state === "awaiting_user_navigation") {
    if (tab.id === state.captureSession.source_tab_id && tab.url.startsWith("https://www.linkedin.com/jobs/")) {
      renderCapturePanel();
      return;
    }
    if (tab.id !== state.captureSession.source_tab_id && tab.openerTabId !== state.captureSession.source_tab_id) {
      await failCapture("ambiguous_capture");
      return;
    }
    const targetUrl = globalThis.JobSourceExternalApplySafety?.sanitize(tab.url);
    const presented = captureApi().presentTarget(state.captureSession, {
      ...captureBinding(),
      permission_available: true,
      target_url: targetUrl || tab.url,
    }, new Date().toISOString());
    if (!presented.ok) {
      await failCapture(presented.reason);
      return;
    }
    state.captureSession = presented.session;
    state.captureTargetTab = tab;
    await chrome.storage.session.set({ [CAPTURE_SESSION_KEY]: state.captureSession });
  } else if (state.captureSession.state === "target_presented") {
    if (tab.id !== state.captureSession.source_tab_id && tab.openerTabId !== state.captureSession.source_tab_id) {
      await failCapture("ambiguous_capture");
      return;
    }
    const targetUrl = globalThis.JobSourceExternalApplySafety?.sanitize(tab.url);
    if (!targetUrl || targetUrl !== state.captureSession.target_url) {
      await failCapture("ambiguous_capture");
      return;
    }
    state.captureTargetTab = tab;
  }
  renderCapturePanel();
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
  await restoreCaptureState();
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

async function sendContentMessage(tabId, message) {
  let response;
  try {
    response = await chrome.tabs.sendMessage(tabId, message);
  } catch {
    try {
      await chrome.scripting.executeScript({
        target: { tabId }, files: ["external_apply_safety.js", "content.js"]
      });
      response = await chrome.tabs.sendMessage(tabId, message);
    } catch (error) {
      throw new Error(`Page scan failed: ${error?.message || "content script injection failed."}`);
    }
  }
  return response;
}

async function requestSelectedScan(tabId, attempt = 0) {
  const response = await sendContentMessage(tabId, { type: "collect_job_source_records" });
  if (!validScanResponse(response)) throw new Error("Page scan returned an invalid response.");
  if (response.state === "not_ready") {
    if (attempt < MAX_SCAN_RETRIES) {
      await new Promise((resolve) => setTimeout(resolve, SCAN_RETRY_DELAY_MS));
      return requestSelectedScan(tabId, attempt + 1);
    }
    throw new Error("LinkedIn Jobs is still loading. Wait a moment and scan again.");
  }
  if (!response.ok) throw new Error(payloadMessage(response, "Page scan failed."));
  return response.records;
}

function captureEligible(record) {
  const posting = record?.source_trace?.linkedin_posting;
  return Boolean(
    !record?.external_apply_url
    && posting?.observation === "detail_observed_but_apply_absent"
    && posting?.external_apply_control === "target_url_unavailable_in_dom"
    && /^https:\/\/www\.linkedin\.com\/jobs\/view\/\d+$/.test(record.linkedin_job_url || "")
  );
}

function validCapturePreparation(payload) {
  const source = payload?.source;
  return Boolean(
    payload?.ok === true && payload.capture_contract === "1" && isObject(source)
    && /^\d+$/.test(source.linkedin_job_id || "")
    && source.linkedin_job_url === `https://www.linkedin.com/jobs/view/${source.linkedin_job_id}`
    && typeof source.company_name === "string" && source.company_name
    && typeof source.job_title === "string" && source.job_title
    && source.external_apply_control === "target_url_unavailable_in_dom"
  );
}

async function armExternalApplyCapture(record) {
  if (hasBusyOperation()) return;
  setBusy("capture", true);
  setMessage();
  try {
    if (!chrome.storage?.session) throw new Error(captureErrorMessage("permission_unavailable"));
    const existing = await chrome.storage.session.get(CAPTURE_SESSION_KEY);
    if (existing[CAPTURE_SESSION_KEY] !== undefined) {
      throw new Error(captureErrorMessage("ambiguous_capture"));
    }
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (!tab?.id || !tab.url?.startsWith("https://www.linkedin.com/jobs/")) {
      throw new Error(captureErrorMessage("source_identity_changed"));
    }
    const prepared = await sendContentMessage(tab.id, { type: "prepare_external_apply_capture" });
    if (!validCapturePreparation(prepared)) {
      throw new Error(captureErrorMessage(prepared?.error_code || "source_identity_changed"));
    }
    if (prepared.source.linkedin_job_url !== record.linkedin_job_url) {
      throw new Error(captureErrorMessage("source_identity_changed"));
    }
    const started = new Date();
    const input = {
      capture_id: `capture_${crypto.randomUUID().replaceAll("-", "")}`,
      source_tab_id: tab.id,
      linkedin_job_id: prepared.source.linkedin_job_id,
      linkedin_job_url: prepared.source.linkedin_job_url,
      company: prepared.source.company_name,
      title: prepared.source.job_title,
      location: prepared.source.job_location || "",
      external_control_evidence: { observed: true, visible: true, enabled: true, off_site: true },
      started_at: started.toISOString(),
      expires_at: new Date(started.getTime() + CAPTURE_TTL_MS).toISOString(),
    };
    const armed = captureApi().arm(null, input, input.started_at);
    if (!armed.ok) throw new Error(captureErrorMessage(armed.reason));
    const awaiting = captureApi().awaitNavigation(
      armed.session,
      {
        capture_id: input.capture_id,
        linkedin_job_id: input.linkedin_job_id,
        linkedin_job_url: input.linkedin_job_url,
      },
      input.started_at,
    );
    if (!awaiting.ok) throw new Error(captureErrorMessage(awaiting.reason));
    state.captureSession = awaiting.session;
    state.captureTargetTab = null;
    await chrome.storage.session.remove(CAPTURE_RECORD_KEY);
    await chrome.storage.session.set({ [CAPTURE_SESSION_KEY]: state.captureSession });
    renderCapturePanel();
    setMessage("Capture armed.");
  } catch (error) {
    setMessage(error.message || "External Apply capture failed.");
  } finally {
    setBusy("capture", false);
  }
}

function capturedRecord(capture) {
  return {
    linkedin_job_url: capture.linkedin_job_url,
    external_apply_url: capture.external_apply_url,
    linkedin_company_url: null,
    company_name: capture.company,
    job_title: capture.title,
    job_location: capture.location,
    source: "linkedin_browser_extension",
    source_trace: {
      linkedin_posting: {
        availability: "active",
        apply_mode: "external",
        evidence_source: capture.provenance,
        job_url: capture.linkedin_job_url,
        observation: "external_apply_observed",
        external_apply_control: "user_confirmed_navigation",
      },
      navigation_capture: {
        capture_contract: "1",
        capture_id: capture.capture_id,
        method: "user_confirmed_active_tab",
      },
    },
  };
}

async function confirmCapturedTarget() {
  if (hasBusyOperation() || state.captureSession?.state !== "target_presented") return;
  setBusy("capture", true);
  setMessage();
  try {
    const now = new Date().toISOString();
    const validating = captureApi().validate(state.captureSession, captureBinding(), now);
    if (!validating.ok) {
      await failCapture(validating.reason);
      return;
    }
    let payload;
    try {
      payload = await bridgeFetch("/v1/external-apply/validate", {
        method: "POST",
        body: JSON.stringify({ external_apply_url: state.captureSession.target_url }),
      });
    } catch {
      await failCapture("bridge_validation_failed");
      return;
    }
    if (
      !isObject(payload) || payload.status !== "valid"
      || payload.external_apply_url !== state.captureSession.target_url
    ) {
      await failCapture("bridge_validation_failed");
      return;
    }
    const bound = captureApi().bind(validating.session, {
      ...captureBinding(validating.session),
      bridge_validated: true,
      target_url: payload.external_apply_url,
    }, now);
    if (!bound.ok) {
      await failCapture(bound.reason);
      return;
    }
    const committed = captureApi().commit(bound.session, captureBinding(bound.session), now);
    if (!committed.ok) {
      await failCapture(committed.reason);
      return;
    }
    const record = capturedRecord(committed.capture);
    await chrome.storage.session.set({ [CAPTURE_RECORD_KEY]: record });
    await clearCaptureSession();
    renderRecordSet([record]);
    setMessage("External Apply target captured.");
  } catch {
    await failCapture("ambiguous_capture");
  } finally {
    setBusy("capture", false);
  }
}

async function cancelCapture() {
  if (hasBusyOperation() || !state.captureSession) return;
  setBusy("capture", true);
  const result = captureApi().cancel(
    state.captureSession,
    state.captureSession.capture_id,
    new Date().toISOString(),
  );
  await clearCaptureSession();
  setMessage(result.reason === "cancelled" ? "Capture cancelled." : captureErrorMessage(result.reason));
  setBusy("capture", false);
}

async function scanSelected() {
  if (hasBusyOperation()) return;
  setBusy("selectedScan", true);
  clearScanOutput();
  setMessage();
  try {
    await clearStaleRun();
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (!tab?.id || !tab.url?.startsWith("https://www.linkedin.com/jobs/")) {
      throw new Error("Open a LinkedIn Jobs page first.");
    }
    state.records = await requestSelectedScan(tab.id);
    $("recordCount").textContent = String(state.records.length);
    $("applyCount").textContent = `${state.records.filter((item) => item.external_apply_url).length} Apply URLs`;
    renderScannedRecords();
    if (state.records.length === 0) setMessage("No eligible jobs were found on this page.");
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
    const response = await chrome.tabs.sendMessage(pageScan.tabId, { type: "cancel_job_source_page" });
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
  $("applyCount").textContent = `${state.records.filter((item) => item.external_apply_url).length} Apply URLs`;
  renderScannedRecords();
  if (response.state === "partial") {
    setMessage(`Partial results: ${response.failure_count} failures.`);
  } else if (response.state === "cancelled") {
    setMessage("Scan cancelled.");
  } else if (response.state === "not_ready") {
    setMessage("Page is not ready.");
  } else if (state.records.length === 0) {
    setMessage("No eligible jobs were found on this page.");
  }
}

async function scanLoadedPage() {
  if (state.pageScan) {
    await cancelPageScan();
    return;
  }
  if (hasBusyOperation()) return;
  const pageScan = { id: state.nextPageScanId++, tabId: null, watchdogTimer: null, cancelling: false };
  state.pageScan = pageScan;
  setBusy("pageScan", true);
  clearScanOutput();
  setMessage();
  try {
    await clearStaleRun();
    if (!pageScanIsActive(pageScan.id)) return;
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (!pageScanIsActive(pageScan.id)) return;
    if (!tab?.id || !tab.url?.startsWith("https://www.linkedin.com/jobs/")) {
      throw new Error("Open a LinkedIn Jobs page first.");
    }
    pageScan.tabId = tab.id;
    pageScan.watchdogTimer = setTimeout(() => { cancelPageScan({ watchdog: true }); }, PAGE_SCAN_WATCHDOG_MS);
    const response = await sendContentMessage(tab.id, { type: "collect_job_source_page" });
    if (!pageScanIsActive(pageScan.id)) return;
    if (!validPageScanResponse(response)) throw new Error("Page scan returned an invalid response.");
    if (!response.ok && response.state !== "partial" && response.state !== "cancelled") {
      throw new Error(payloadMessage(response, "Page scan failed."));
    }
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
    if (chrome.storage?.session) await chrome.storage.session.remove(CAPTURE_RECORD_KEY);
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
    if (captureEligible(record)) {
      const capture = document.createElement("button");
      capture.type = "button";
      capture.textContent = "Capture target";
      capture.addEventListener("click", () => armExternalApplyCapture(record));
      item.append(capture);
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

chrome.runtime.onMessage.addListener((message, sender) => {
  if (message?.type === "job_source_page_progress") handlePageScanProgress(message, sender);
});
$("scanSelectedButton").addEventListener("click", scanSelected);
$("scanPageButton").addEventListener("click", scanLoadedPage);
$("runButton").addEventListener("click", runDiscovery);
$("refreshButton").addEventListener("click", pollRun);
$("saveButton").addEventListener("click", saveConnection);
$("confirmCaptureButton").addEventListener("click", confirmCapturedTarget);
$("cancelCaptureButton").addEventListener("click", cancelCapture);
loadSettings();
