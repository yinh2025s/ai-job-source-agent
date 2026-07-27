const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

const sandbox = { URL };
sandbox.globalThis = sandbox;
vm.runInNewContext(fs.readFileSync(process.argv[2], "utf8"), sandbox, { filename: process.argv[2] });

const api = sandbox.JobSourceCaptureSession;
const scenario = process.argv[3];
const START = "2026-07-21T01:00:00.000Z";
const LATER = "2026-07-21T01:01:00.000Z";
const EXPIRES = "2026-07-21T01:05:00.000Z";
const EXPIRED = "2026-07-21T01:05:00.000Z";
const CAPTURE_ID = "capture_0123456789abcdef";

const input = (overrides = {}) => ({
  capture_id: CAPTURE_ID,
  source_tab_id: 17,
  linkedin_job_id: "1234567890",
  linkedin_job_url: "https://www.linkedin.com/jobs/view/example-role-1234567890/?tracking=ignored",
  company: "  Example   Corp ",
  title: " Staff   Engineer ",
  location: " Remote  ",
  external_control_evidence: { observed: true, visible: true, enabled: true, off_site: true },
  started_at: START,
  expires_at: EXPIRES,
  ...overrides,
});

const binding = (overrides = {}) => ({
  capture_id: CAPTURE_ID,
  linkedin_job_id: "1234567890",
  linkedin_job_url: "https://www.linkedin.com/jobs/view/1234567890",
  ...overrides,
});

const armed = () => {
  const result = api.arm(null, input(), START);
  assert.equal(result.ok, true);
  return result.session;
};

const awaiting = () => {
  const result = api.awaitNavigation(armed(), binding(), LATER);
  assert.equal(result.ok, true);
  return result.session;
};

const presented = () => {
  const result = api.presentTarget(awaiting(), {
    ...binding(),
    permission_available: true,
    target_url: "https://jobs.example.com/roles/42?utm_source=linkedin",
  }, LATER);
  assert.equal(result.ok, true);
  return result.session;
};

const scenarios = {
  lifecycle() {
    assert(api);
    assert.equal(api.SCHEMA, "job_source_capture_session");
    assert.equal(api.VERSION, 1);
    const session = armed();
    assert.equal(session.state, "armed");
    assert.equal(session.linkedin_job_url, "https://www.linkedin.com/jobs/view/1234567890");
    assert.equal(session.company, "Example Corp");
    assert.deepEqual(JSON.parse(JSON.stringify(session.external_control_evidence)), {
      observed: true, visible: true, enabled: true, off_site: true,
    });
    const target = presented();
    assert.equal(target.target_url, "https://jobs.example.com/roles/42?utm_source=linkedin");
    assert.equal(target.provenance, api.PROVENANCE);
    const validating = api.validate(target, binding(), LATER);
    assert.equal(validating.state, "validating");
    const bound = api.bind(validating.session, {
      ...binding(),
      bridge_validated: true,
      target_url: target.target_url,
    }, LATER);
    assert.equal(bound.state, "bound");
    const committed = api.commit(bound.session, binding(), LATER);
    assert.equal(committed.state, "committed");
    assert.equal(committed.session, null);
    assert.equal(committed.capture.linkedin_job_id, "1234567890");
    assert.equal(committed.capture.external_apply_url, target.target_url);
  },

  cancellation_expiry_and_recovery() {
    const session = armed();
    assert.deepEqual(JSON.parse(JSON.stringify(api.cancel(session, CAPTURE_ID, LATER))), {
      ok: false, state: "cancelled", reason: "cancelled", session: null,
    });
    const expired = api.recover(session, EXPIRED);
    assert.equal(expired.reason, "capture_expired");
    assert.equal(expired.session, null);
    assert.equal(api.expire(session, EXPIRED).reason, "capture_expired");
    assert.equal(api.expire(session, LATER).state, "armed");
    const recovered = api.recover(JSON.parse(JSON.stringify(session)), LATER);
    assert.equal(recovered.ok, true);
    assert.equal(recovered.session.state, "armed");
    const malformed = { ...session, state: "future_state" };
    assert.equal(api.recover(malformed, LATER).reason, "ambiguous_capture");
    assert.equal(api.recover(malformed, LATER).session, null);
  },

  duplicate_and_identity_fail_closed() {
    const original = armed();
    const duplicate = api.arm(original, input({ capture_id: "capture_ffffffffffffffff" }), START);
    assert.equal(duplicate.reason, "ambiguous_capture");
    assert.equal(duplicate.session, null);
    const changedJob = api.awaitNavigation(original, binding({ linkedin_job_id: "9999999999" }), LATER);
    assert.equal(changedJob.reason, "source_identity_changed");
    assert.equal(changedJob.session, null);
    const sameCompanyOtherJob = api.presentTarget(awaiting(), {
      ...binding({
        linkedin_job_id: "2222222222",
        linkedin_job_url: "https://www.linkedin.com/jobs/view/2222222222",
      }),
      permission_available: true,
      target_url: "https://jobs.example.com/roles/other",
    }, LATER);
    assert.equal(sameCompanyOtherJob.reason, "source_identity_changed");
  },

  arm_evidence_and_shape() {
    assert.equal(api.arm(null, input({ linkedin_job_id: "99" }), START).reason, "source_identity_changed");
    assert.equal(api.arm(null, input({ external_control_evidence: { observed: true } }), START).reason,
      "external_control_not_observed");
    assert.equal(api.arm(null, input({ capture_id: "too-short" }), START).reason, "ambiguous_capture");
    const session = armed();
    session.company = " Changed  ";
    assert.equal(api.recover(session, LATER).reason, "ambiguous_capture");
  },

  rejected_urls_are_not_returned() {
    const values = [
      ["", "target_not_observed"],
      ["https://www.linkedin.com/jobs/view/1", "target_is_linkedin"],
      ["https://linkedin.example/jobs/1", "target_is_linkedin"],
      ["https://user:pass@jobs.example/1", "sensitive_target_url"],
      ["https://jobs.example/1#token", "sensitive_target_url"],
      ["https://jobs.example/1?access_token=secret", "sensitive_target_url"],
      ["https://jobs.example/1?state=secret", "sensitive_target_url"],
      ["https://jobs.example/1?state=opaque", "sensitive_target_url"],
      ["https://jobs.example/1?myTokenValue=secret", "sensitive_target_url"],
      ["http://jobs.example/1", "unsafe_target_url"],
      ["https://127.0.0.1/1", "unsafe_target_url"],
      ["https://192.168.1.2/1", "unsafe_target_url"],
      ["https://[::ffff:127.0.0.1]/1", "unsafe_target_url"],
      ["https://jobs.example:99999/1", "unsafe_target_url"],
    ];
    for (const [raw, reason] of values) {
      const result = api.presentTarget(awaiting(), {
        ...binding(), permission_available: true, target_url: raw,
      }, LATER);
      assert.equal(result.reason, reason);
      assert.equal(result.session, null);
      assert.deepEqual(Object.keys(result).sort(), ["ok", "reason", "session", "state"]);
      if (raw) assert.equal(JSON.stringify(result).includes(raw), false);
    }
    assert.equal(api.presentTarget(awaiting(), {
      ...binding(), permission_available: false, target_url: "https://jobs.example/roles/42",
    }, LATER).reason, "permission_unavailable");
  },

  late_replay_and_validation() {
    const late = api.presentTarget(awaiting(), {
      ...binding(), permission_available: true, target_url: "https://jobs.example/roles/42",
    }, EXPIRED);
    assert.equal(late.reason, "capture_expired");
    const target = presented();
    assert.equal(api.presentTarget(target, {
      ...binding(), permission_available: true, target_url: "https://jobs.example/roles/42",
    }, LATER).reason, "ambiguous_capture");
    const validating = api.validate(target, binding(), LATER).session;
    assert.equal(api.bind(validating, {
      ...binding(), bridge_validated: false, target_url: target.target_url,
    }, LATER).reason, "bridge_validation_failed");
    assert.equal(api.bind(validating, {
      ...binding(), bridge_validated: true, target_url: "https://jobs.example/roles/other",
    }, LATER).reason, "ambiguous_capture");
  },
};

Promise.resolve()
  .then(() => {
    assert(scenarios[scenario], `Unknown scenario: ${scenario}`);
    scenarios[scenario]();
    process.stdout.write(JSON.stringify({ ok: true }));
  })
  .catch((error) => {
    process.stderr.write(`${error.stack}\n`);
    process.exitCode = 1;
  });
