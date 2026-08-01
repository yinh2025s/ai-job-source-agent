const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

class FakeElement {
  constructor(id = "") {
    this.id = id;
    this.value = "";
    this.textContent = "";
    this.disabled = false;
    this.hidden = false;
    this.dataset = {};
    this.children = [];
    this.listeners = new Map();
    this.attributes = new Map();
  }

  addEventListener(type, callback) {
    this.listeners.set(type, callback);
  }

  click() {
    const callback = this.listeners.get("click");
    if (callback && !this.disabled) callback();
  }

  setAttribute(name, value) {
    this.attributes.set(name, String(value));
  }

  getAttribute(name) {
    return this.attributes.get(name) || null;
  }

  append(...nodes) {
    this.children.push(...nodes);
  }

  replaceChildren(...nodes) {
    this.children = nodes;
  }
}

function response(status, payload, rawBody) {
  return {
    ok: status >= 200 && status < 300,
    status,
    text: async () => rawBody === undefined ? JSON.stringify(payload) : rawBody,
  };
}

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

function createHarness({
  fetchQueue = [], scanQueue = [], pageQueue = [], cancelQueue = [], statusQueue = [],
  injectionQueue = [], runId = null, tab = null,
  bridgeUrl = "http://127.0.0.1:8765", bridgeToken = "bridge-token", scanSnapshot,
} = {}) {
  const ids = [
    "bridgeState", "popupRoot", "scanSelectedButton", "scanPageButton", "runButton", "refreshButton", "saveButton",
    "bridgeUrl", "bridgeToken", "message", "recordCount", "applyCount", "runPanel",
    "runStatus", "jobListRate", "openingRate", "results", "scanPanel", "scanResults",
  ];
  const elements = Object.fromEntries(ids.map((id) => [id, new FakeElement(id)]));
  elements.scanButton = elements.scanSelectedButton;
  elements.bridgeUrl.value = "http://127.0.0.1:8765";
  elements.bridgeToken.value = "";
  elements.bridgeState.textContent = "Connecting";
  elements.bridgeState.dataset.state = "busy";
  elements.recordCount.textContent = "0";
  elements.applyCount.textContent = "0 direct Apply URLs";
  elements.runPanel.hidden = true;
  elements.scanPanel.hidden = true;
  const fetchCalls = [];
  const timers = new Map();
  let nextTimer = 1;
  const storage = { bridgeUrl, bridgeToken, runId };
  if (scanSnapshot !== undefined) storage.scanSnapshot = scanSnapshot;
  const storageCalls = { set: [], remove: [] };
  const executed = [];
  const sentMessages = [];
  const runtimeListeners = [];
  const defaultTab = { id: 4, url: "https://www.linkedin.com/jobs/search/" };

  const sandbox = {
    URL,
    AbortController,
    document: {
      getElementById: (id) => elements[id],
      createElement: () => new FakeElement(),
    },
    fetch: (url, options) => {
      fetchCalls.push({ url, options });
      const next = fetchQueue.shift();
      if (!next) throw new Error(`Unexpected fetch: ${url}`);
      if (next instanceof Error) return Promise.reject(next);
      return next.promise || Promise.resolve(next);
    },
    setTimeout: (callback, delay) => {
      const id = nextTimer++;
      timers.set(id, { callback, delay });
      return id;
    },
    clearTimeout: (id) => timers.delete(id),
    chrome: {
      storage: {
        local: {
          get: async () => ({ ...storage }),
          set: async (values) => {
            Object.assign(storage, values);
            storageCalls.set.push(values);
          },
          remove: async (key) => {
            delete storage[key];
            storageCalls.remove.push(key);
          },
        },
      },
      tabs: {
        query: async () => [tab || defaultTab],
        sendMessage: async (tabId, message) => {
          sentMessages.push({ tabId, message });
          let queue;
          if (message.type === "job_source_agent_content_status") queue = statusQueue;
          else if (["collect_job_source_records", "collect_job_source_records_v1"].includes(message.type)) {
            queue = scanQueue;
          } else if (["collect_job_source_page", "collect_job_source_page_v1"].includes(message.type)) {
            queue = pageQueue;
          } else queue = cancelQueue;
          const next = queue.length
            ? queue.shift()
            : (message.type === "job_source_agent_content_status"
              ? { ok: true, content_script_version: "2", scan_versions: ["2", "3"] }
              : undefined);
          if (next instanceof Error) throw next;
          return next?.promise || next;
        },
      },
      scripting: {
        executeScript: async (options) => {
          executed.push(options);
          const next = injectionQueue.shift();
          if (next instanceof Error) throw next;
        },
      },
      runtime: {
        onMessage: {
          addListener: (listener) => runtimeListeners.push(listener),
        },
      },
    },
  };
  sandbox.globalThis = sandbox;
  vm.runInNewContext(fs.readFileSync(process.argv[2], "utf8"), sandbox, { filename: process.argv[2] });

  return {
    elements, fetchCalls, timers, storage, storageCalls, executed, sentMessages,
    emitProgress(message, sender = { tab: { id: 4 } }) {
      runtimeListeners.forEach((listener) => listener(message, sender));
    },
    async settle(turns = 48) {
      for (let index = 0; index < turns; index += 1) await Promise.resolve();
    },
    async runNextTimer() {
      const next = timers.entries().next().value;
      assert(next, "Expected a scheduled timer");
      const [id, timer] = next;
      timers.delete(id);
      timer.callback();
      await this.settle();
    },
  };
}

const health = () => response(200, { status: "ok" });
const pairToken = "paired-token-0123456789-ABCDEFGHIJKLMNOPQRSTUVWXYZ";
const paired = () => response(200, {
  status: "paired",
  protocol_version: "1",
  token: pairToken,
});
const readyScan = () => ({ ok: true, scan_version: "2", state: "ready", page_url: "https://www.linkedin.com/jobs/search/", records: [{ company_name: "Acme", job_title: "Engineer" }] });
const pageScan = (overrides = {}) => ({
  ok: true,
  scan_version: "3",
  state: "ready",
  page_url: "https://www.linkedin.com/jobs/search/",
  scanned_count: 2,
  candidate_count: 2,
  failure_count: 0,
  records: [{ company_name: "Acme", job_title: "Engineer", external_apply_url: "https://apply.example/acme" }],
  ...overrides,
});
const complete = (runId, results = []) => response(200, {
  run_id: runId,
  status: "complete",
  summary: { rates: { job_list: 0.5, opening: 0.5 } },
  results,
});

async function freshAutoPair() {
  const h = createHarness({ fetchQueue: [paired(), health()], bridgeToken: "" });
  await h.settle();
  assert.equal(h.fetchCalls.length, 2);
  assert.equal(h.fetchCalls[0].url, "http://127.0.0.1:8765/v1/pair");
  assert.equal(h.fetchCalls[0].options.method, "POST");
  assert.equal(h.fetchCalls[0].options.headers.Authorization, undefined);
  assert.deepEqual(JSON.parse(h.fetchCalls[0].options.body), {
    client: "ai-job-source-agent-extension",
    protocol_version: "1",
  });
  assert.equal(h.fetchCalls[1].url, "http://127.0.0.1:8765/v1/health");
  assert.equal(h.fetchCalls[1].options.headers.Authorization, `Bearer ${pairToken}`);
  assert.equal(h.storage.bridgeToken, pairToken);
  assert.equal(h.elements.bridgeToken.value, pairToken);
  assert.equal(h.elements.bridgeState.textContent, "Online");
  assert.equal(h.elements.message.textContent, "");
}

async function validSavedTokenDoesNotPair() {
  const h = createHarness({ fetchQueue: [health()], bridgeToken: "saved-token" });
  assert.equal(h.elements.bridgeState.textContent, "Connecting");
  await h.settle();
  assert.equal(h.fetchCalls.length, 1);
  assert.equal(h.fetchCalls[0].url, "http://127.0.0.1:8765/v1/health");
  assert.equal(h.fetchCalls[0].options.headers.Authorization, "Bearer saved-token");
  assert.equal(h.elements.bridgeState.textContent, "Online");
}

async function staleTokenRepairsByPairing() {
  const h = createHarness({
    fetchQueue: [response(401, { error: "unauthorized" }), paired(), health()],
    bridgeToken: "stale-token",
  });
  await h.settle();
  assert.deepEqual(h.fetchCalls.map((call) => call.url), [
    "http://127.0.0.1:8765/v1/health",
    "http://127.0.0.1:8765/v1/pair",
    "http://127.0.0.1:8765/v1/health",
  ]);
  assert.equal(h.fetchCalls[1].options.headers.Authorization, undefined);
  assert.equal(h.fetchCalls[2].options.headers.Authorization, `Bearer ${pairToken}`);
  assert.equal(h.storage.bridgeToken, pairToken);
  assert.equal(h.elements.bridgeState.textContent, "Online");
}

async function unavailableBridgeIsConcise() {
  const h = createHarness({ fetchQueue: [new TypeError("network details")], bridgeToken: "" });
  await h.settle();
  assert.equal(h.fetchCalls.length, 1);
  assert.equal(h.fetchCalls[0].url, "http://127.0.0.1:8765/v1/pair");
  assert.equal(h.elements.bridgeState.textContent, "Offline");
  assert.equal(h.elements.message.textContent, "Bridge unavailable.");
}

async function malformedPairResponseIsRejected() {
  const invalidPayloads = [
    { status: "paired", protocol_version: "1", token: "short" },
    { status: "paired", protocol_version: "1", token: `${pairToken} invalid` },
    { status: "paired", protocol_version: "1", token: pairToken, extra: true },
  ];
  for (const payload of invalidPayloads) {
    const h = createHarness({
      fetchQueue: [response(200, payload)],
      bridgeToken: "",
    });
    await h.settle();
    assert.equal(h.fetchCalls.length, 1);
    assert.equal(h.storage.bridgeToken, "");
    assert.equal(h.elements.bridgeToken.value, "");
    assert.equal(h.elements.bridgeState.textContent, "Offline");
    assert.equal(h.elements.message.textContent, "Bridge pairing response was invalid.");
  }
}

async function serverErrorDoesNotPair() {
  const h = createHarness({
    fetchQueue: [response(503, { error: "temporarily_unavailable" })],
    bridgeToken: "saved-token",
  });
  await h.settle();
  assert.equal(h.fetchCalls.length, 1);
  assert.equal(h.fetchCalls[0].url, "http://127.0.0.1:8765/v1/health");
  assert.equal(h.storage.bridgeToken, "saved-token");
  assert.equal(h.elements.bridgeState.textContent, "Offline");
}

async function pairingTerminalMessagesAreActionable() {
  for (const [status, error, expected] of [
    [410, "pairing_expired", "Restart the local agent to pair again."],
    [409, "pairing_conflict", "Local agent is paired with another extension."],
    [403, "pairing_disabled", "Enter the explicit token under Advanced connection."],
  ]) {
    const h = createHarness({
      fetchQueue: [response(status, { error })],
      bridgeToken: "",
    });
    await h.settle();
    assert.equal(h.elements.bridgeState.textContent, "Offline");
    assert.equal(h.elements.message.textContent, expected);
  }
}

async function manualFallbackCompatibility() {
  const h = createHarness({ fetchQueue: [health(), health()], bridgeToken: "saved-token" });
  await h.settle();
  h.elements.bridgeUrl.value = "http://127.0.0.1:8877";
  h.elements.bridgeToken.value = "manual-token";
  h.elements.saveButton.click();
  await h.settle();
  assert.equal(h.fetchCalls.length, 2);
  assert.equal(h.fetchCalls[1].url, "http://127.0.0.1:8877/v1/health");
  assert.equal(h.fetchCalls[1].options.headers.Authorization, "Bearer manual-token");
  assert.equal(h.storage.bridgeUrl, "http://127.0.0.1:8877");
  assert.equal(h.storage.bridgeToken, "manual-token");
  assert.equal(h.elements.bridgeState.textContent, "Online");
}

async function invalidEndpointNoFetch() {
  const h = createHarness({ fetchQueue: [health()] });
  await h.settle();
  h.elements.bridgeUrl.value = "http://localhost:8765/private";
  h.elements.saveButton.click();
  await h.settle();
  assert.equal(h.fetchCalls.length, 1);
  assert.match(h.elements.message.textContent, /Bridge URL/);
  assert.equal(h.elements.saveButton.disabled, false);
}

async function duplicateSubmission() {
  const submission = deferred();
  const h = createHarness({ fetchQueue: [health(), submission, complete("run-1")] , scanQueue: [readyScan()] });
  await h.settle();
  h.elements.scanButton.click();
  await h.settle();
  h.elements.runButton.click();
  h.elements.runButton.click();
  await h.settle();
  assert.equal(h.fetchCalls.filter((call) => call.options.method === "POST").length, 1);
  assert.equal(h.elements.runPanel.hidden, false);
  assert.equal(h.elements.runStatus.textContent, "Submitting");
  assert.equal(h.elements.runButton.textContent, "Verifying...");
  assert.equal(h.elements.runButton.disabled, true);
  assert.equal(h.elements.bridgeState.textContent, "Submitting");
  submission.resolve(response(202, { run_id: "run-1", status: "queued" }));
  await h.settle();
  assert.equal(h.elements.runButton.disabled, false);
}

async function submissionFailureIsVisible() {
  const h = createHarness({
    fetchQueue: [health(), response(503, { error: "bridge_busy" })],
    scanQueue: [readyScan()],
  });
  await h.settle();
  h.elements.scanButton.click();
  await h.settle();
  h.elements.runButton.click();
  await h.settle();
  assert.equal(h.elements.runPanel.hidden, false);
  assert.equal(h.elements.runStatus.textContent, "Submission failed");
  assert.equal(h.elements.message.textContent, "bridge_busy");
  assert.equal(h.elements.bridgeState.textContent, "Error");
  assert.equal(h.elements.runButton.textContent, "Verify source");
  assert.equal(h.elements.runButton.disabled, false);
}

async function executorFailureIsQueryable() {
  const failed = { run_id: "failed-run", status: "failed", error: "bridge_executor_unavailable" };
  const h = createHarness({
    fetchQueue: [health(), response(202, failed), response(200, failed)],
    scanQueue: [readyScan()],
  });
  await h.settle();
  h.elements.scanButton.click();
  await h.settle();
  h.elements.runButton.click();
  await h.settle();
  assert.equal(h.elements.runPanel.hidden, false);
  assert.equal(h.elements.runStatus.textContent, "Failed");
  assert.equal(h.elements.message.textContent, "bridge_executor_unavailable");
  assert.equal(h.storage.runId, "failed-run");
  assert.equal(h.elements.runButton.disabled, false);
}

async function duplicateWhilePolling() {
  const h = createHarness({
    fetchQueue: [
      health(),
      response(202, { run_id: "run-polling", status: "running" }),
      response(200, { run_id: "run-polling", status: "running" }),
    ],
    scanQueue: [readyScan()],
  });
  await h.settle();
  h.elements.scanButton.click();
  await h.settle();
  h.elements.runButton.click();
  await h.settle();
  assert.equal(h.elements.runButton.disabled, true);
  assert.equal(h.elements.runButton.textContent, "Verifying...");
  assert.equal(h.elements.scanSelectedButton.disabled, true);
  assert.equal(h.elements.scanPageButton.disabled, true);
  const sentBeforeBlockedScan = h.sentMessages.length;
  h.elements.scanPageButton.click();
  await h.settle();
  assert.equal(h.sentMessages.length, sentBeforeBlockedScan);
  h.elements.runButton.click();
  await h.settle();
  assert.equal(h.fetchCalls.filter((call) => call.options.method === "POST").length, 1);
}

async function duplicateScan() {
  const firstScan = deferred();
  const h = createHarness({ fetchQueue: [health()], scanQueue: [firstScan] });
  await h.settle();
  h.elements.scanButton.click();
  h.elements.scanButton.click();
  await h.settle();
  assert.equal(h.sentMessages.filter(
    (call) => call.message.type === "collect_job_source_records_v1"
  ).length, 1);
  firstScan.resolve(readyScan());
  await h.settle();
  assert.equal(h.elements.scanButton.disabled, false);
}

async function pageSuccessAndProgress() {
  const pendingPage = deferred();
  const h = createHarness({ fetchQueue: [health()], pageQueue: [pendingPage] });
  await h.settle();
  h.elements.scanPageButton.click();
  await h.settle();
  assert.equal(h.elements.scanPageButton.textContent, "Cancel scan");
  assert.equal(h.elements.scanPageButton.disabled, false);
  assert.equal(h.elements.scanSelectedButton.disabled, true);
  assert.equal(h.elements.runButton.disabled, true);
  assert.equal(h.elements.saveButton.disabled, true);
  h.emitProgress({ type: "job_source_page_progress", scanned_count: 7, candidate_count: 25 });
  assert.equal(h.elements.message.textContent, "Scanning 7/25");
  pendingPage.resolve(pageScan());
  await h.settle();
  assert.equal(h.elements.recordCount.textContent, "1");
  assert.equal(h.elements.scanResults.children[0].children[1].textContent, "External Apply");
  assert.equal(h.elements.scanPageButton.textContent, "Scan page");
  assert.equal(h.elements.scanSelectedButton.disabled, false);
  assert.equal(h.elements.message.textContent, "");
}

async function pagePartialKeepsRecords() {
  const h = createHarness({ fetchQueue: [health()], pageQueue: [pageScan({
    ok: false,
    state: "partial",
    scanned_count: 3,
    candidate_count: 4,
    failure_count: 1,
  })] });
  await h.settle();
  h.elements.scanPageButton.click();
  await h.settle();
  assert.equal(h.elements.recordCount.textContent, "1");
  assert.equal(h.elements.scanPanel.hidden, false);
  assert.equal(h.elements.message.textContent, "Partial results: 1 failures.");
}

async function pageMissingDetailsIsExplicit() {
  const h = createHarness({ fetchQueue: [health()], pageQueue: [pageScan({
    state: "partial",
    failure_count: 0,
    detail_not_observed_count: 2,
  })] });
  await h.settle();
  h.elements.scanPageButton.click();
  await h.settle();
  assert.equal(h.elements.recordCount.textContent, "1");
  assert.equal(h.elements.message.textContent, "Partial scan: 2 job detail panels were not observed.");
}

async function pageNotReadyDoesNotRetry() {
  const h = createHarness({ fetchQueue: [health()], pageQueue: [pageScan({
    state: "not_ready",
    scanned_count: 0,
    candidate_count: 0,
    records: [],
  })] });
  await h.settle();
  h.elements.scanPageButton.click();
  await h.settle();
  assert.equal(h.sentMessages.filter(
    (call) => call.message.type === "collect_job_source_page_v1"
  ).length, 1);
  assert.equal(h.timers.size, 0);
  assert.equal(h.elements.message.textContent, "Page is not ready.");
  assert.equal(h.elements.scanPageButton.disabled, false);
}

async function pageCancellationRecoversButtons() {
  const pendingPage = deferred();
  const h = createHarness({
    fetchQueue: [health()],
    pageQueue: [pendingPage],
    cancelQueue: [{ ok: true, cancelled: true }],
  });
  await h.settle();
  h.elements.scanPageButton.click();
  await h.settle();
  h.elements.scanPageButton.click();
  await h.settle();
  assert.equal(h.sentMessages.filter(
    (call) => call.message.type === "collect_job_source_page_v1"
  ).length, 1);
  assert.equal(h.sentMessages.filter(
    (call) => call.message.type === "cancel_job_source_page_v1"
  ).length, 1);
  assert.equal(h.elements.message.textContent, "Scan cancelled.");
  assert.equal(h.elements.scanPageButton.textContent, "Scan page");
  assert.equal(h.elements.scanSelectedButton.disabled, false);
  pendingPage.resolve(pageScan());
  await h.settle();
  assert.equal(h.elements.recordCount.textContent, "0");
}

async function pageWatchdogRecoversButtons() {
  const pendingPage = deferred();
  const h = createHarness({
    fetchQueue: [health()],
    pageQueue: [pendingPage],
    cancelQueue: [{ ok: true, cancelled: true }],
  });
  await h.settle();
  h.elements.scanPageButton.click();
  await h.settle();
  assert.equal(h.timers.size, 1);
  await h.runNextTimer();
  assert.equal(h.sentMessages.filter(
    (call) => call.message.type === "cancel_job_source_page_v1"
  ).length, 1);
  assert.equal(h.elements.message.textContent, "Page scan timed out and was cancelled.");
  assert.equal(h.elements.scanSelectedButton.disabled, false);
  assert.equal(h.elements.saveButton.disabled, false);
}

async function staleOutputReset() {
  const pendingScan = deferred();
  const h = createHarness({ fetchQueue: [health()], scanQueue: [pendingScan] });
  await h.settle();
  h.elements.runPanel.hidden = false;
  h.elements.jobListRate.textContent = "88%";
  h.elements.results.append(new FakeElement());
  h.elements.recordCount.textContent = "7";
  h.elements.scanButton.click();
  await h.settle(2);
  assert.equal(h.elements.runPanel.hidden, true);
  assert.equal(h.elements.jobListRate.textContent, "--");
  assert.equal(h.elements.results.children.length, 0);
  assert.equal(h.elements.recordCount.textContent, "0");
  pendingScan.resolve(readyScan());
  await h.settle();
}

async function scanNotReadyRetry() {
  const h = createHarness({
    fetchQueue: [health()],
    scanQueue: [
      { ok: true, scan_version: "2", state: "not_ready", page_url: "https://www.linkedin.com/jobs/search/", records: [] },
      readyScan(),
    ],
  });
  await h.settle();
  h.elements.scanButton.click();
  await h.settle();
  assert.equal(h.sentMessages.filter(
    (call) => call.message.type === "collect_job_source_records_v1"
  ).length, 1);
  await h.runNextTimer();
  assert.equal(h.sentMessages.filter(
    (call) => call.message.type === "collect_job_source_records_v1"
  ).length, 2);
  assert.equal(h.elements.recordCount.textContent, "1");
  assert.equal(h.elements.scanButton.disabled, false);
}

async function selectedPartialKeepsBoundRecord() {
  const h = createHarness({
    fetchQueue: [health()],
    scanQueue: [{
      ok: true,
      scan_version: "2",
      state: "partial",
      page_url: "https://www.linkedin.com/jobs/search/?currentJobId=902",
      records: [{
        company_name: "Selected Card",
        job_title: "Selected Card Role",
        linkedin_job_url: "https://www.linkedin.com/jobs/view/902",
        source_trace: { linkedin_posting: { observation_state: "detail_not_observed" } },
      }],
    }],
  });
  await h.settle();
  h.elements.scanButton.click();
  await h.settle();
  assert.equal(h.elements.recordCount.textContent, "1");
  assert.equal(h.elements.runButton.disabled, false);
  assert.equal(h.elements.scanResults.children[0].children[1].textContent, "Detail panel not observed");
  assert.equal(h.elements.message.textContent, "Selected job found, but its detail panel was not fully observed.");
}

async function restoredSelectedScanRemainsVisible() {
  const scanned = createHarness({ fetchQueue: [health()], scanQueue: [readyScan()] });
  await scanned.settle();
  scanned.elements.scanButton.click();
  await scanned.settle();
  assert.equal(scanned.storage.scanSnapshot.schema_version, "1");
  assert.equal(scanned.storage.scanSnapshot.mode, "selected");
  assert.equal(scanned.storage.scanSnapshot.tab_id, 4);
  assert.equal(scanned.storage.scanSnapshot.records.length, 1);

  const reopened = createHarness({
    fetchQueue: [health()],
    scanSnapshot: scanned.storage.scanSnapshot,
  });
  await reopened.settle();
  assert.equal(reopened.elements.recordCount.textContent, "1");
  assert.equal(reopened.elements.scanPanel.hidden, false);
  assert.equal(reopened.elements.scanResults.children[0].children[0].textContent, "Acme · Engineer");
  assert.equal(reopened.elements.runButton.disabled, false);
}

async function staleSelectedScanIsNotRestored() {
  const snapshot = {
    schema_version: "1",
    mode: "selected",
    tab_id: 4,
    page_url: "https://www.linkedin.com/jobs/search/?currentJobId=101",
    saved_at: Date.now(),
    records: [{
      company_name: "Old Company",
      job_title: "Old Role",
      linkedin_job_url: "https://www.linkedin.com/jobs/view/101",
    }],
  };
  const h = createHarness({
    fetchQueue: [health()],
    scanSnapshot: snapshot,
    tab: { id: 4, url: "https://www.linkedin.com/jobs/search/?currentJobId=202" },
  });
  await h.settle();
  assert.equal(h.elements.recordCount.textContent, "0");
  assert.equal(h.elements.scanPanel.hidden, true);
  assert.equal(h.storage.scanSnapshot, undefined);
  assert.equal(h.elements.runButton.disabled, true);
}

async function externalApplyWithoutTargetIsExplicit() {
  const h = createHarness({
    fetchQueue: [health()],
    scanQueue: [{
      ok: true,
      scan_version: "2",
      state: "ready",
      page_url: "https://www.linkedin.com/jobs/search/?currentJobId=904",
      records: [{
        company_name: "External Systems",
        job_title: "Platform Engineer",
        linkedin_job_url: "https://www.linkedin.com/jobs/view/904",
        external_apply_url: null,
        source_trace: { linkedin_posting: { observation_state: "external_apply_observed" } },
      }],
    }],
  });
  await h.settle();
  h.elements.scanButton.click();
  await h.settle();
  assert.equal(
    h.elements.scanResults.children[0].children[1].textContent,
    "External Apply button found; LinkedIn exposed no target URL",
  );
  assert.equal(h.elements.applyCount.textContent, "0 direct Apply URLs");
}

async function staleRunClear() {
  const h = createHarness({ fetchQueue: [health(), response(404, { error: "run_not_found" })], runId: "gone" });
  await h.settle();
  assert.equal(h.storage.runId, undefined);
  assert.deepEqual(h.storageCalls.remove, ["runId"]);
  assert.match(h.elements.message.textContent, /no longer available/);
  assert.equal(h.elements.bridgeState.textContent, "Online");
}

async function transientPollingRetry() {
  const h = createHarness({ fetchQueue: [health(), response(503, { error: "busy" }), complete("retry-run")], runId: "retry-run" });
  await h.settle();
  assert.match(h.elements.message.textContent, /Retrying shortly/);
  assert.equal(h.timers.size, 1);
  await h.runNextTimer();
  assert.equal(h.elements.runStatus.textContent, "Complete");
  assert.equal(h.elements.refreshButton.disabled, false);
}

async function restoredRunningRunIsVisible() {
  const h = createHarness({
    fetchQueue: [health(), response(200, {
      run_id: "active-run",
      status: "running",
      submitted: 25,
      completed: 7,
    })],
    runId: "active-run",
  });
  await h.settle();
  assert.equal(h.elements.runPanel.hidden, false);
  assert.equal(h.elements.runStatus.textContent, "Running 7/25");
  assert.equal(h.elements.bridgeState.textContent, "Running");
  assert.equal(h.elements.runButton.textContent, "Verifying...");
  assert.equal(h.elements.runButton.disabled, true);
}

async function malformedResponse() {
  const h = createHarness({ fetchQueue: [health(), response(200, null, "not-json")], runId: "bad-run" });
  await h.settle();
  assert.match(h.elements.message.textContent, /invalid JSON/);
  assert.equal(h.elements.refreshButton.disabled, false);
}

async function clickableSafeLinks() {
  const results = [
    { company_name: "Exact", job_title: "One", open_position_url: "https://jobs.example/opening" },
    { company_name: "List", job_title: "Two", job_list_page_url: "https://jobs.example/list" },
    { company_name: "Unsafe", job_title: "Three", open_position_url: "javascript:alert(1)", error_code: "unsafe_url" },
    { company_name: "Private", job_title: "Four", open_position_url: "https://127.0.0.1/private", error_code: "private_url" },
  ];
  const h = createHarness({ fetchQueue: [health(), complete("links", results)], runId: "links" });
  await h.settle();
  const items = h.elements.results.children;
  assert.equal(items.length, 4);
  assert.equal(items[0].children[1].textContent, "Exact opening");
  assert.equal(items[0].children[1].href, "https://jobs.example/opening");
  assert.equal(items[1].children[1].textContent, "Job list");
  assert.equal(items[2].children[1].textContent, "unsafe_url");
  assert.equal(items[3].children[1].textContent, "private_url");
}

async function scannedApplyRemainsAvailableWithoutVerifiedOpening() {
  const record = {
    company_name: "Microsoft",
    job_title: "Software Engineer",
    linkedin_job_url: "https://www.linkedin.com/jobs/view/4420695497",
    external_apply_url: "https://apply.careers.microsoft.com/careers/job/1970393556824773",
  };
  const result = {
    company_name: "Microsoft",
    linkedin_job_title: "Software Engineer",
    linkedin_job_url: record.linkedin_job_url,
    job_list_page_url: "https://microsoft.example/careers/",
  };
  const h = createHarness({
    fetchQueue: [
      health(),
      response(202, { run_id: "source-fallback", status: "queued" }),
      complete("source-fallback", [result]),
    ],
    scanQueue: [{
      ok: true,
      scan_version: "2",
      state: "ready",
      page_url: "https://www.linkedin.com/jobs/search/",
      records: [record],
    }],
  });
  await h.settle();
  h.elements.scanButton.click();
  await h.settle();
  assert.equal(h.elements.scanPanel.hidden, false);
  assert.equal(h.elements.scanResults.children[0].children[1].textContent, "External Apply");
  h.elements.runButton.click();
  await h.settle();
  assert.equal(h.elements.results.children[0].children[1].textContent, "Job list");
  assert.equal(h.elements.results.children[0].children[2].textContent, "LinkedIn Apply");
  assert.equal(
    h.elements.results.children[0].children[2].href,
    "https://apply.careers.microsoft.com/careers/job/1970393556824773",
  );
}

async function buttonRecovery() {
  const injectionError = new Error("Injection blocked");
  const h = createHarness({
    fetchQueue: [health()],
    statusQueue: [new Error("No receiver")],
    injectionQueue: [injectionError],
  });
  await h.settle();
  h.elements.scanButton.click();
  await h.settle();
  assert.match(h.elements.message.textContent, /Injection blocked/);
  assert.equal(h.elements.scanButton.disabled, false);
  assert.equal(h.elements.saveButton.disabled, false);
}

async function staleContentScriptIsUpgradedBeforeScanning() {
  const h = createHarness({
    fetchQueue: [health()],
    statusQueue: [
      { ok: true, content_script_version: "1", scan_versions: ["2", "3"] },
      { ok: true, content_script_version: "2", scan_versions: ["2", "3"] },
    ],
    pageQueue: [pageScan()],
  });
  await h.settle();
  h.elements.scanPageButton.click();
  await h.settle();
  assert.equal(h.executed.length, 1);
  assert.equal(JSON.stringify(h.executed[0].files), JSON.stringify(["content.js"]));
  assert.equal(
    h.sentMessages.at(-1).message.type,
    "collect_job_source_page_v1",
  );
  assert.equal(h.elements.message.textContent, "");
}

const scenarios = {
  fresh_auto_pair: freshAutoPair,
  saved_token_no_pair: validSavedTokenDoesNotPair,
  stale_token_repair: staleTokenRepairsByPairing,
  unavailable_bridge: unavailableBridgeIsConcise,
  malformed_pair_response: malformedPairResponseIsRejected,
  server_error_no_pair: serverErrorDoesNotPair,
  pairing_terminal_messages: pairingTerminalMessagesAreActionable,
  manual_fallback: manualFallbackCompatibility,
  invalid_endpoint_no_fetch: invalidEndpointNoFetch,
  duplicate_submission: duplicateSubmission,
  submission_failure_visible: submissionFailureIsVisible,
  executor_failure_queryable: executorFailureIsQueryable,
  duplicate_while_polling: duplicateWhilePolling,
  duplicate_scan: duplicateScan,
  page_success_progress: pageSuccessAndProgress,
  page_partial: pagePartialKeepsRecords,
  page_missing_details: pageMissingDetailsIsExplicit,
  page_not_ready_no_retry: pageNotReadyDoesNotRetry,
  page_cancellation: pageCancellationRecoversButtons,
  page_watchdog: pageWatchdogRecoversButtons,
  stale_output_reset: staleOutputReset,
  scan_not_ready_retry: scanNotReadyRetry,
  selected_partial: selectedPartialKeepsBoundRecord,
  restored_scan_visible: restoredSelectedScanRemainsVisible,
  stale_scan_not_restored: staleSelectedScanIsNotRestored,
  external_without_target: externalApplyWithoutTargetIsExplicit,
  stale_run_clear: staleRunClear,
  transient_polling_retry: transientPollingRetry,
  restored_running_visible: restoredRunningRunIsVisible,
  malformed_response: malformedResponse,
  clickable_safe_links: clickableSafeLinks,
  scanned_apply_fallback: scannedApplyRemainsAvailableWithoutVerifiedOpening,
  button_recovery: buttonRecovery,
  stale_content_upgrade: staleContentScriptIsUpgradedBeforeScanning,
};

const scenario = scenarios[process.argv[3]];
if (!scenario) throw new Error(`Unknown scenario: ${process.argv[3]}`);
scenario().then(() => process.stdout.write(JSON.stringify({ ok: true }))).catch((error) => {
  process.stderr.write(`${error.stack}\n`);
  process.exitCode = 1;
});
