(() => {
  "use strict";

  const SCHEMA = "job_source_capture_session";
  const VERSION = 1;
  const PROVENANCE = "authenticated_user_apply_navigation";
  const STATES = Object.freeze([
    "armed",
    "awaiting_user_navigation",
    "target_presented",
    "validating",
    "bound",
  ]);
  const TERMINAL_REASONS = Object.freeze([
    "cancelled",
    "capture_expired",
    "permission_unavailable",
    "source_identity_changed",
    "external_control_not_observed",
    "target_not_observed",
    "target_is_linkedin",
    "unsafe_target_url",
    "sensitive_target_url",
    "ambiguous_capture",
    "bridge_validation_failed",
  ]);

  const isObject = (value) => value !== null && typeof value === "object" && !Array.isArray(value);
  const clone = (value) => JSON.parse(JSON.stringify(value));
  const fail = (reason) => Object.freeze({ ok: false, state: reason, reason, session: null });
  const succeed = (session) => Object.freeze({ ok: true, state: session.state, session: clone(session) });
  const normalizedText = (value) => (
    typeof value === "string" ? value.replace(/\s+/g, " ").trim() : ""
  );
  const captureId = (value) => (
    typeof value === "string" && /^[A-Za-z0-9._~-]{16,128}$/.test(value) ? value : ""
  );
  const jobId = (value) => (
    typeof value === "string" && /^\d+$/.test(value) ? value : ""
  );
  const timestamp = (value) => {
    if (typeof value !== "string") return "";
    const parsed = Date.parse(value);
    return Number.isFinite(parsed) ? new Date(parsed).toISOString() : "";
  };
  const nowValue = (value) => {
    const normalized = timestamp(value);
    return normalized ? Date.parse(normalized) : NaN;
  };

  const linkedinHost = (hostname) => (
    hostname === "linkedin.com" || hostname.endsWith(".linkedin.com")
  );
  const linkedinOwnedHost = (hostname) => (
    linkedinHost(hostname) || hostname === "licdn.com" || hostname.endsWith(".licdn.com")
  );
  const linkedinLookalikeHost = (hostname) => /(?:linkedin|licdn)/i.test(hostname);

  const isPublicIpv4 = (host) => {
    const parts = host.split(".");
    if (parts.length !== 4 || parts.some((part) => !/^\d+$/.test(part))) return null;
    const octets = parts.map(Number);
    if (octets.some((part) => part < 0 || part > 255)) return false;
    const [a, b, c] = octets;
    return !(
      a === 0 || a === 10 || a === 127 || a >= 224
      || (a === 100 && b >= 64 && b <= 127)
      || (a === 169 && b === 254)
      || (a === 172 && b >= 16 && b <= 31)
      || (a === 192 && b === 0 && (c === 0 || c === 2))
      || (a === 192 && b === 88 && c === 99)
      || (a === 192 && b === 168)
      || (a === 198 && (b === 18 || b === 19))
      || (a === 198 && b === 51 && c === 100)
      || (a === 203 && b === 0 && c === 113)
    );
  };

  const isPublicHost = (hostname) => {
    const host = hostname.toLowerCase().replace(/^\[|\]$/g, "");
    if (!host || host === "localhost" || host.endsWith(".localhost")
      || host.endsWith(".local") || host.endsWith(".internal")) return false;
    const ipv4 = isPublicIpv4(host);
    if (ipv4 !== null) return ipv4;
    if (host.includes(":")) {
      if (host === "::" || host === "::1" || host.startsWith("fc") || host.startsWith("fd")
        || host.startsWith("::ffff:")) return false;
      if (/^fe[89ab]/i.test(host) || /^ff/i.test(host) || /^2001:db8(?::|$)/i.test(host)) return false;
    }
    return host.includes(":") || (host.includes(".") && /^[a-z0-9.-]+$/i.test(host));
  };

  const hasSensitiveQuery = (url) => {
    const sensitive = new Set([
      "accesstoken", "apikey", "auth", "authorization", "code", "csrf", "idtoken",
      "lsd", "key", "password", "protectedsessionjwt", "refreshtoken", "secret",
      "session", "sessioncsrftoken", "sessionjwt", "sig", "signature", "state", "token",
      "xfblsd",
    ]);
    const markers = ["token", "secret", "password", "credential", "session", "csrf"];
    return Array.from(url.searchParams.keys()).some((key) => {
      const canonical = key.toLowerCase().replace(/[^a-z0-9]+/g, "");
      return sensitive.has(canonical) || markers.some((marker) => canonical.includes(marker));
    });
  };

  const inspectTargetUrl = (value) => {
    if (typeof value !== "string" || !value) return { ok: false, reason: "target_not_observed" };
    try {
      const url = new URL(value);
      const host = url.hostname.toLowerCase();
      if (linkedinOwnedHost(host) || linkedinLookalikeHost(host)) {
        return { ok: false, reason: "target_is_linkedin" };
      }
      if (url.username || url.password || url.hash || hasSensitiveQuery(url)) {
        return { ok: false, reason: "sensitive_target_url" };
      }
      if (url.protocol !== "https:" || !isPublicHost(host)) {
        return { ok: false, reason: "unsafe_target_url" };
      }
      return { ok: true, url: url.href };
    } catch {
      return { ok: false, reason: "unsafe_target_url" };
    }
  };

  const canonicalJobUrl = (value) => {
    if (typeof value !== "string") return "";
    try {
      const url = new URL(value);
      if (url.protocol !== "https:" || !linkedinHost(url.hostname.toLowerCase())) return "";
      const match = url.pathname.match(/^\/jobs\/view\/(?:[^/?#]*-)?(\d+)(?:\/|$)/);
      return match ? `https://www.linkedin.com/jobs/view/${match[1]}` : "";
    } catch {
      return "";
    }
  };

  const identityMatches = (session, binding) => (
    isObject(binding)
    && captureId(binding.capture_id) === session.capture_id
    && jobId(binding.linkedin_job_id) === session.linkedin_job_id
    && canonicalJobUrl(binding.linkedin_job_url) === session.linkedin_job_url
  );

  const baseKeys = [
    "schema", "version", "state", "capture_id", "source_tab_id", "linkedin_job_id",
    "linkedin_job_url", "company", "title", "location", "external_control_evidence",
    "started_at", "expires_at",
  ];
  const exactKeys = (value, expected) => {
    const actual = Object.keys(value).sort();
    const wanted = [...expected].sort();
    return actual.length === wanted.length && actual.every((key, index) => key === wanted[index]);
  };

  const validBase = (session) => {
    if (!isObject(session) || session.schema !== SCHEMA || session.version !== VERSION
      || !STATES.includes(session.state) || !captureId(session.capture_id)
      || !Number.isInteger(session.source_tab_id) || session.source_tab_id < 0
      || !jobId(session.linkedin_job_id)
      || canonicalJobUrl(session.linkedin_job_url) !== session.linkedin_job_url
      || !session.linkedin_job_url.endsWith(`/${session.linkedin_job_id}`)
      || !normalizedText(session.company) || normalizedText(session.company) !== session.company
      || !normalizedText(session.title) || normalizedText(session.title) !== session.title
      || normalizedText(session.location) !== session.location
      || !isObject(session.external_control_evidence)
      || !exactKeys(session.external_control_evidence, ["observed", "visible", "enabled", "off_site"])
      || Object.values(session.external_control_evidence).some((value) => value !== true)) return false;
    const started = timestamp(session.started_at);
    const expires = timestamp(session.expires_at);
    return Boolean(started && expires && started === session.started_at && expires === session.expires_at
      && Date.parse(expires) > Date.parse(started));
  };

  const validSession = (session) => {
    if (!validBase(session)) return false;
    const targetStates = ["target_presented", "validating", "bound"];
    const expected = targetStates.includes(session.state)
      ? [...baseKeys, "target_url", "provenance"]
      : baseKeys;
    if (!exactKeys(session, expected)) return false;
    if (!targetStates.includes(session.state)) return true;
    const inspected = inspectTargetUrl(session.target_url);
    return inspected.ok && inspected.url === session.target_url && session.provenance === PROVENANCE;
  };

  const active = (session, now) => {
    if (!validSession(session)) return fail("ambiguous_capture");
    const current = nowValue(now);
    if (!Number.isFinite(current)) return fail("ambiguous_capture");
    if (current >= Date.parse(session.expires_at)) return fail("capture_expired");
    return succeed(session);
  };

  const arm = (current, input, now) => {
    if (current !== null && current !== undefined) return fail("ambiguous_capture");
    if (!isObject(input)) return fail("ambiguous_capture");
    const currentTime = nowValue(now);
    const started = timestamp(input.started_at);
    const expires = timestamp(input.expires_at);
    const id = jobId(input.linkedin_job_id);
    const url = canonicalJobUrl(input.linkedin_job_url);
    if (!Number.isFinite(currentTime) || !started || !expires || Date.parse(started) !== currentTime
      || Date.parse(expires) <= currentTime) return fail("ambiguous_capture");
    if (!id || !url || !url.endsWith(`/${id}`)) return fail("source_identity_changed");
    if (!isObject(input.external_control_evidence)
      || input.external_control_evidence.observed !== true
      || input.external_control_evidence.visible !== true
      || input.external_control_evidence.enabled !== true
      || input.external_control_evidence.off_site !== true) return fail("external_control_not_observed");
    const company = normalizedText(input.company);
    const title = normalizedText(input.title);
    const location = normalizedText(input.location);
    if (!captureId(input.capture_id) || !Number.isInteger(input.source_tab_id) || input.source_tab_id < 0
      || !company || !title) return fail("ambiguous_capture");
    return succeed({
      schema: SCHEMA,
      version: VERSION,
      state: "armed",
      capture_id: input.capture_id,
      source_tab_id: input.source_tab_id,
      linkedin_job_id: id,
      linkedin_job_url: url,
      company,
      title,
      location,
      external_control_evidence: { observed: true, visible: true, enabled: true, off_site: true },
      started_at: started,
      expires_at: expires,
    });
  };

  const transition = (session, binding, now, expectedState, nextState) => {
    const recovered = active(session, now);
    if (!recovered.ok) return recovered;
    if (session.state !== expectedState) return fail("ambiguous_capture");
    if (!identityMatches(session, binding)) return fail("source_identity_changed");
    return succeed({ ...session, state: nextState });
  };

  const awaitNavigation = (session, binding, now) => (
    transition(session, binding, now, "armed", "awaiting_user_navigation")
  );

  const presentTarget = (session, input, now) => {
    const recovered = active(session, now);
    if (!recovered.ok) return recovered;
    if (session.state !== "awaiting_user_navigation") return fail("ambiguous_capture");
    if (!identityMatches(session, input)) return fail("source_identity_changed");
    if (input.permission_available !== true) return fail("permission_unavailable");
    const inspected = inspectTargetUrl(input.target_url);
    if (!inspected.ok) return fail(inspected.reason);
    return succeed({
      ...session,
      state: "target_presented",
      target_url: inspected.url,
      provenance: PROVENANCE,
    });
  };

  const validate = (session, binding, now) => (
    transition(session, binding, now, "target_presented", "validating")
  );

  const bind = (session, input, now) => {
    const recovered = active(session, now);
    if (!recovered.ok) return recovered;
    if (session.state !== "validating") return fail("ambiguous_capture");
    if (!identityMatches(session, input)) return fail("source_identity_changed");
    if (input.bridge_validated !== true) return fail("bridge_validation_failed");
    const inspected = inspectTargetUrl(input.target_url);
    if (!inspected.ok) return fail(inspected.reason);
    if (inspected.url !== session.target_url) return fail("ambiguous_capture");
    return succeed({ ...session, state: "bound" });
  };

  const commit = (session, binding, now) => {
    const recovered = active(session, now);
    if (!recovered.ok) return recovered;
    if (session.state !== "bound") return fail("ambiguous_capture");
    if (!identityMatches(session, binding)) return fail("source_identity_changed");
    return Object.freeze({
      ok: true,
      state: "committed",
      session: null,
      capture: Object.freeze({
        capture_id: session.capture_id,
        source_tab_id: session.source_tab_id,
        linkedin_job_id: session.linkedin_job_id,
        linkedin_job_url: session.linkedin_job_url,
        company: session.company,
        title: session.title,
        location: session.location,
        external_apply_url: session.target_url,
        provenance: PROVENANCE,
      }),
    });
  };

  const cancel = (session, capture_id, now) => {
    const recovered = active(session, now);
    if (!recovered.ok) return recovered;
    if (captureId(capture_id) !== session.capture_id) return fail("ambiguous_capture");
    return fail("cancelled");
  };

  const expire = (session, now) => active(session, now);

  const recover = (session, now) => active(session, now);

  globalThis.JobSourceCaptureSession = Object.freeze({
    SCHEMA,
    VERSION,
    PROVENANCE,
    STATES,
    TERMINAL_REASONS,
    arm,
    awaitNavigation,
    presentTarget,
    validate,
    bind,
    commit,
    cancel,
    expire,
    recover,
    sanitizeTargetUrl(value) {
      const inspected = inspectTargetUrl(value);
      return inspected.ok
        ? Object.freeze({ ok: true, url: inspected.url })
        : fail(inspected.reason);
    },
  });
})();
