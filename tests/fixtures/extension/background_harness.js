const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

function eventHook() {
  const hook = {
    listeners: [],
    addListener(listener) { hook.listeners.push(listener); },
    emit(...args) { hook.listeners.forEach((listener) => listener(...args)); },
  };
  return hook;
}

const plain = (value) => JSON.parse(JSON.stringify(value));

function createHarness({ now = 1_000, tabs = {}, triggerResponse = { ok: true } } = {}) {
  const local = {};
  const session = {};
  const runtimeMessages = eventHook();
  const onCreated = eventHook();
  const onUpdated = eventHook();
  const sentMessages = [];
  let clock = now;

  function storageArea(values) {
    return {
      async get(key) {
        if (typeof key === "string") return { [key]: values[key] };
        return { ...values };
      },
      async set(update) { Object.assign(values, update); },
      async remove(key) { delete values[key]; },
    };
  }

  const sandbox = {
    URL,
    Date: class extends Date { static now() { return clock; } },
    chrome: {
      runtime: { onMessage: runtimeMessages },
      storage: { local: storageArea(local), session: storageArea(session) },
      tabs: {
        onCreated,
        onUpdated,
        async get(tabId) {
          if (!tabs[tabId]) throw new Error("missing tab");
          return tabs[tabId];
        },
        async sendMessage(tabId, message) {
          sentMessages.push({ tabId, message });
          if (triggerResponse instanceof Error) throw triggerResponse;
          return triggerResponse;
        },
      },
    },
  };
  sandbox.globalThis = sandbox;
  vm.runInNewContext(fs.readFileSync(process.argv[2], "utf8"), sandbox, { filename: process.argv[2] });

  return {
    local,
    session,
    sentMessages,
    advance(milliseconds) { clock += milliseconds; },
    async message(message) {
      return new Promise((resolve, reject) => {
        const listener = runtimeMessages.listeners?.[0];
        if (!listener) return reject(new Error("missing runtime listener"));
        const keepAlive = listener(message, {}, resolve);
        if (keepAlive !== true) reject(new Error("listener did not stay alive"));
      });
    },
    async created(tab) {
      onCreated.emit(tab);
      await this.settle();
    },
    async updated(tabId, changeInfo, tab) {
      onUpdated.emit(tabId, changeInfo, tab);
      await this.settle();
    },
    async settle(turns = 30) {
      for (let index = 0; index < turns; index += 1) await Promise.resolve();
    },
  };
}

const sourceTab = (jobId = "4439508231") => ({
  id: 7,
  url: `https://www.linkedin.com/jobs/search/?currentJobId=${jobId}`,
});

async function begin(h, jobId = "4439508231") {
  return h.message({
    type: "begin_external_apply_capture_v1",
    source_tab_id: 7,
    linkedin_job_id: jobId,
  });
}

async function workdayCapture() {
  const h = createHarness({ tabs: { 7: sourceTab() } });
  assert.deepEqual(plain(await begin(h)), { ok: true, status: "pending" });
  assert.equal(h.session.externalApplyCapturePending.linkedin_job_id, "4439508231");
  assert.deepEqual(plain(h.sentMessages), [{
    tabId: 7,
    message: { type: "trigger_external_apply_v1", linkedin_job_id: "4439508231" },
  }]);

  await h.created({ id: 9, openerTabId: 7, status: "loading", url: "about:blank" });
  const workday = "https://medtronic.wd1.myworkdayjobs.com/zh-CN/MedtronicCareers/job/example_R72412-1?source=LinkedIn";
  await h.updated(9, { status: "complete", url: workday }, { id: 9, openerTabId: 7, status: "complete", url: workday });
  const record = h.local.externalApplyCaptures.records["4439508231"];
  assert.equal(record.external_apply_url, workday);
  assert.equal(record.target_tab_id, 9);
  assert.equal(h.session.externalApplyCapturePending, undefined);
  assert.equal((await h.message({ type: "get_external_apply_capture_v1", linkedin_job_id: "4439508231" })).status, "captured");
}

async function safetyUnwrap() {
  const h = createHarness({ tabs: { 7: sourceTab("123") } });
  await begin(h, "123");
  await h.created({ id: 10, openerTabId: 7, status: "loading", url: "about:blank" });
  const target = "https://jobs.example.com/opening/123?source=LinkedIn";
  const safety = `https://www.linkedin.com/safety/go/?url=${encodeURIComponent(target)}`;
  await h.updated(10, { status: "complete", url: safety }, { id: 10, status: "complete", url: safety });
  assert.equal(h.local.externalApplyCaptures.records["123"].external_apply_url, target);
}

async function unrelatedIgnored() {
  const h = createHarness({ tabs: { 7: sourceTab("124") } });
  await begin(h, "124");
  await h.created({ id: 11, openerTabId: 99, status: "complete", url: "https://jobs.evil.example/opening" });
  await h.updated(11, { status: "complete" }, { id: 11, status: "complete", url: "https://jobs.evil.example/opening" });
  assert.equal(h.local.externalApplyCaptures, undefined);
  assert.equal(h.session.externalApplyCapturePending.target_tab_id, undefined);
}

async function wrongSourceRejected() {
  const wrongHost = createHarness({ tabs: { 7: { id: 7, url: "https://example.com/jobs/443" } } });
  assert.deepEqual(plain(await begin(wrongHost, "443")), {
    ok: false, status: "rejected", error: "source_not_linkedin_job",
  });
  const wrongJob = createHarness({ tabs: { 7: sourceTab("443") } });
  assert.deepEqual(plain(await begin(wrongJob, "444")), {
    ok: false, status: "rejected", error: "linkedin_job_id_mismatch",
  });
}

async function unsafeUrlsRejected() {
  const urls = [
    "http://jobs.example.com/opening",
    "https://user:pass@jobs.example.com/opening",
    "https://jobs.example.com/opening#secret",
    "https://127.0.0.1/opening",
    "https://10.0.0.4/opening",
    "https://[::1]/opening",
    "https://[::ffff:127.0.0.1]/opening",
    "https://intranet/opening",
    "https://jobs.internal/opening",
    "https://linkedin.example.com/opening",
    "https://www.linkedin.com/jobs/view/123",
    "https://jobs.example.com/opening?access_token=secret",
    "https://jobs.example.com/opening?x-api-key=secret",
    "https://jobs.example.com/opening?authorization=secret",
    "https://jobs.example.com/opening?signature=secret",
  ];
  for (let index = 0; index < urls.length; index += 1) {
    const jobId = String(600 + index);
    const h = createHarness({ tabs: { 7: sourceTab(jobId) } });
    await begin(h, jobId);
    await h.created({ id: 20, openerTabId: 7, status: "complete", url: urls[index] });
    assert.equal(h.local.externalApplyCaptures, undefined, urls[index]);
    assert.ok(h.session.externalApplyCapturePending, urls[index]);
  }
}

async function sameTabCapture() {
  const h = createHarness({ tabs: { 7: sourceTab("779") } });
  await begin(h, "779");
  const target = "https://boards.example.com/jobs/779?source=LinkedIn";
  await h.updated(7, { status: "complete", url: target }, { id: 7, status: "complete", url: target });
  const record = h.local.externalApplyCaptures.records["779"];
  assert.equal(record.external_apply_url, target);
  assert.equal(record.source_tab_id, 7);
  assert.equal(record.target_tab_id, 7);
}

async function triggerFailureClearsPending() {
  const h = createHarness({
    tabs: { 7: sourceTab("778") },
    triggerResponse: { ok: false, error: "external_apply_button_missing" },
  });
  assert.deepEqual(plain(await begin(h, "778")), {
    ok: false,
    status: "failed",
    error: "external_apply_button_missing",
  });
  assert.equal(h.session.externalApplyCapturePending, undefined);
}

async function expiry() {
  const h = createHarness({ tabs: { 7: sourceTab("777") } });
  await begin(h, "777");
  h.advance(20_001);
  assert.deepEqual(plain(await h.message({ type: "get_external_apply_capture_v1", linkedin_job_id: "777" })), {
    ok: true, status: "missing",
  });
  assert.equal(h.session.externalApplyCapturePending, undefined);
  await h.created({ id: 12, openerTabId: 7, status: "complete", url: "https://jobs.example.com/late" });
  assert.equal(h.local.externalApplyCaptures, undefined);
}

const scenarios = {
  workday_capture: workdayCapture,
  safety_unwrap: safetyUnwrap,
  unrelated_ignored: unrelatedIgnored,
  wrong_source_rejected: wrongSourceRejected,
  unsafe_urls_rejected: unsafeUrlsRejected,
  expiry,
  trigger_failure_clears_pending: triggerFailureClearsPending,
  same_tab_capture: sameTabCapture,
};

(async () => {
  const scenario = scenarios[process.argv[3]];
  if (!scenario) throw new Error(`Unknown scenario: ${process.argv[3]}`);
  await scenario();
  process.stdout.write(JSON.stringify({ ok: true }));
})().catch((error) => {
  process.stderr.write(`${error.stack || error}\n`);
  process.exitCode = 1;
});
