"use strict";

const CAPTURE_SCHEMA_VERSION = "1";
const PENDING_STORAGE_KEY = "externalApplyCapturePending";
const CAPTURES_STORAGE_KEY = "externalApplyCaptures";
const CAPTURE_TTL_MS = 20_000;
const MAX_CAPTURE_RECORDS = 30;

function nowMs() {
  return Date.now();
}

function isPositiveInteger(value) {
  return Number.isInteger(value) && value > 0;
}

function isLinkedInJobId(value) {
  return typeof value === "string" && /^\d+$/.test(value);
}

function linkedInJobIdFromUrl(rawUrl) {
  try {
    const url = new URL(rawUrl);
    if (url.protocol !== "https:" || url.hostname.toLowerCase() !== "www.linkedin.com") return null;
    if (!url.pathname.startsWith("/jobs/")) return null;

    const queryJobId = url.searchParams.get("currentJobId");
    if (isLinkedInJobId(queryJobId)) return queryJobId;

    const pathMatch = url.pathname.match(/^\/jobs\/view\/(?:[^/?#]*-)?(\d+)(?:\/|$)/);
    return pathMatch ? pathMatch[1] : null;
  } catch (_error) {
    return null;
  }
}

function isExpired(pending, timestamp = nowMs()) {
  return !pending
    || pending.schema_version !== CAPTURE_SCHEMA_VERSION
    || !Number.isFinite(pending.expires_at)
    || pending.expires_at <= timestamp;
}

async function getPendingCapture() {
  const stored = await chrome.storage.session.get(PENDING_STORAGE_KEY);
  return stored[PENDING_STORAGE_KEY] || null;
}

async function clearPendingCapture() {
  await chrome.storage.session.remove(PENDING_STORAGE_KEY);
}

async function getLivePendingCapture() {
  const pending = await getPendingCapture();
  if (!pending) return null;
  if (isExpired(pending)) {
    await clearPendingCapture();
    return null;
  }
  return pending;
}

function isBlockedExternalHost(hostname) {
  const host = hostname.toLowerCase().replace(/^\[|\]$/g, "").replace(/\.$/, "");
  if (!host) return true;
  if (!host.includes(".") && !host.includes(":")) return true;
  if (host === "localhost" || [".localhost", ".local", ".internal", ".lan", ".home"]
    .some((suffix) => host.endsWith(suffix))) return true;
  if (host.includes("linkedin") || host.includes("licdn")) return true;
  return isPrivateIpAddress(host);
}

function isPrivateIpAddress(host) {
  if (/^\d+(?:\.\d+){3}$/.test(host)) {
    const octets = host.split(".").map(Number);
    if (octets.some((part) => !Number.isInteger(part) || part < 0 || part > 255)) return true;
    const [first, second] = octets;
    return first === 0
      || first === 10
      || first === 127
      || (first === 169 && second === 254)
      || (first === 172 && second >= 16 && second <= 31)
      || (first === 192 && second === 168)
      || first >= 224;
  }

  if (!host.includes(":")) return false;
  const normalized = host.toLowerCase();
  if (normalized === "::" || normalized === "::1") return true;
  if (normalized.startsWith("fc") || normalized.startsWith("fd")) return true;
  if (/^fe[89ab]/.test(normalized)) return true;
  if (normalized.startsWith("::ffff:")) {
    const mapped = normalized.slice("::ffff:".length);
    if (mapped.includes(".")) return isPrivateIpAddress(mapped);
    const groups = mapped.split(":");
    if (groups.length === 2 && groups.every((group) => /^[0-9a-f]{1,4}$/.test(group))) {
      const high = Number.parseInt(groups[0], 16);
      const low = Number.parseInt(groups[1], 16);
      return isPrivateIpAddress([
        high >> 8, high & 255, low >> 8, low & 255,
      ].join("."));
    }
    return true;
  }
  return false;
}

function hasSensitiveQueryParameter(url) {
  const sensitiveWords = [
    "token", "session", "auth", "apikey", "csrf", "secret", "password",
    "credential", "signature",
  ];
  for (const [name] of url.searchParams) {
    const parts = name.toLowerCase().split(/[^a-z0-9]+/).filter(Boolean);
    const compact = parts.join("");
    if (sensitiveWords.some((word) => compact.includes(word))) return true;
    if (compact === "sig" || parts.includes("sig")) return true;
  }
  return false;
}

function unwrapLinkedInSafetyUrl(rawUrl) {
  let current = rawUrl;
  for (let index = 0; index < 2; index += 1) {
    let parsed;
    try {
      parsed = new URL(current);
    } catch (_error) {
      return current;
    }
    const host = parsed.hostname.toLowerCase();
    const isSafetyUrl = (host === "www.linkedin.com" || host === "linkedin.com")
      && parsed.pathname.replace(/\/+$/, "") === "/safety/go";
    if (!isSafetyUrl) return current;
    const target = parsed.searchParams.get("url");
    if (!target) return current;
    current = target;
  }
  return current;
}

function sanitizeExternalApplyUrl(rawUrl) {
  if (typeof rawUrl !== "string" || !rawUrl.trim()) return null;
  const unwrapped = unwrapLinkedInSafetyUrl(rawUrl.trim());
  let url;
  try {
    url = new URL(unwrapped);
  } catch (_error) {
    return null;
  }
  if (url.protocol !== "https:") return null;
  if (url.username || url.password || url.hash) return null;
  if (isBlockedExternalHost(url.hostname)) return null;
  if (hasSensitiveQueryParameter(url)) return null;
  return url.href;
}

async function persistCompletedCapture(pending, targetTabId, externalApplyUrl) {
  const stored = await chrome.storage.local.get(CAPTURES_STORAGE_KEY);
  const container = stored[CAPTURES_STORAGE_KEY];
  const existing = container?.schema_version === CAPTURE_SCHEMA_VERSION && container.records
    ? container.records
    : {};
  const record = {
    linkedin_job_id: pending.linkedin_job_id,
    external_apply_url: externalApplyUrl,
    captured_at: nowMs(),
    source_tab_id: pending.source_tab_id,
    target_tab_id: targetTabId,
  };
  const records = { ...existing, [pending.linkedin_job_id]: record };
  const newest = Object.values(records)
    .filter((item) => item && isLinkedInJobId(item.linkedin_job_id))
    .sort((left, right) => Number(right.captured_at || 0) - Number(left.captured_at || 0))
    .slice(0, MAX_CAPTURE_RECORDS);
  const trimmedRecords = Object.fromEntries(newest.map((item) => [item.linkedin_job_id, item]));
  await chrome.storage.local.set({
    [CAPTURES_STORAGE_KEY]: { schema_version: CAPTURE_SCHEMA_VERSION, records: trimmedRecords },
  });
  await clearPendingCapture();
  return record;
}

async function maybeFinalizeCapture(tabId, tab) {
  const pending = await getLivePendingCapture();
  if (!pending) return false;
  const isSourceTab = tabId === pending.source_tab_id;
  const isTargetTab = tabId === pending.target_tab_id;
  if (!isSourceTab && !isTargetTab) return false;
  if (tab?.status !== "complete") return false;
  const safeUrl = sanitizeExternalApplyUrl(tab.url);
  if (!safeUrl) return false;
  await persistCompletedCapture(pending, tabId, safeUrl);
  return true;
}

async function beginCapture(message) {
  if (!isPositiveInteger(message.source_tab_id)) {
    return { ok: false, status: "rejected", error: "invalid_source_tab_id" };
  }
  if (!isLinkedInJobId(message.linkedin_job_id)) {
    return { ok: false, status: "rejected", error: "invalid_linkedin_job_id" };
  }

  let sourceTab;
  try {
    sourceTab = await chrome.tabs.get(message.source_tab_id);
  } catch (_error) {
    return { ok: false, status: "rejected", error: "source_tab_not_found" };
  }
  const sourceJobId = linkedInJobIdFromUrl(sourceTab?.url);
  if (!sourceJobId) {
    return { ok: false, status: "rejected", error: "source_not_linkedin_job" };
  }
  if (sourceJobId !== message.linkedin_job_id) {
    return { ok: false, status: "rejected", error: "linkedin_job_id_mismatch" };
  }

  const activePending = await getLivePendingCapture();
  if (activePending) {
    if (activePending.source_tab_id === message.source_tab_id
        && activePending.linkedin_job_id === message.linkedin_job_id) {
      return { ok: true, status: "pending" };
    }
    return { ok: false, status: "rejected", error: "capture_already_pending" };
  }

  const startedAt = nowMs();
  const pending = {
    schema_version: CAPTURE_SCHEMA_VERSION,
    source_tab_id: message.source_tab_id,
    linkedin_job_id: message.linkedin_job_id,
    started_at: startedAt,
    expires_at: startedAt + CAPTURE_TTL_MS,
  };
  await chrome.storage.session.set({ [PENDING_STORAGE_KEY]: pending });

  try {
    const response = await chrome.tabs.sendMessage(message.source_tab_id, {
      type: "trigger_external_apply_v1",
      linkedin_job_id: message.linkedin_job_id,
    });
    if (!response?.ok) {
      await clearPendingCapture();
      return {
        ok: false,
        status: "failed",
        error: response?.error || "external_apply_trigger_failed",
      };
    }
  } catch (_error) {
    await clearPendingCapture();
    return { ok: false, status: "failed", error: "content_script_unavailable" };
  }

  return { ok: true, status: "pending" };
}

async function getCaptureStatus(message) {
  if (!isLinkedInJobId(message.linkedin_job_id)) {
    return { ok: false, status: "rejected", error: "invalid_linkedin_job_id" };
  }
  const pending = await getLivePendingCapture();
  if (pending?.linkedin_job_id === message.linkedin_job_id) {
    return { ok: true, status: "pending" };
  }
  const stored = await chrome.storage.local.get(CAPTURES_STORAGE_KEY);
  const captures = stored[CAPTURES_STORAGE_KEY];
  const record = captures?.schema_version === CAPTURE_SCHEMA_VERSION
    ? captures.records?.[message.linkedin_job_id]
    : null;
  if (record) return { ok: true, status: "captured", record };
  return { ok: true, status: "missing" };
}

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  let operation;
  if (message?.type === "begin_external_apply_capture_v1") operation = beginCapture(message);
  else if (message?.type === "get_external_apply_capture_v1") operation = getCaptureStatus(message);
  else return false;

  operation
    .then(sendResponse)
    .catch(() => sendResponse({ ok: false, status: "failed", error: "capture_service_error" }));
  return true;
});

chrome.tabs.onCreated.addListener((tab) => {
  void (async () => {
    const pending = await getLivePendingCapture();
    if (!pending || tab?.openerTabId !== pending.source_tab_id) return;
    if (pending.target_tab_id && pending.target_tab_id !== tab.id) return;
    const updated = { ...pending, target_tab_id: tab.id };
    await chrome.storage.session.set({ [PENDING_STORAGE_KEY]: updated });
    let latestTab = tab;
    try {
      latestTab = await chrome.tabs.get(tab.id);
    } catch (_error) {
      // The onCreated payload is still usable if the tab disappears before lookup.
    }
    await maybeFinalizeCapture(tab.id, latestTab);
  })();
});

chrome.tabs.onUpdated.addListener((tabId, changeInfo, tab) => {
  void (async () => {
    const pending = await getLivePendingCapture();
    if (!pending) return;
    if (tabId !== pending.source_tab_id && tabId !== pending.target_tab_id) return;
    const completedTab = {
      ...tab,
      status: changeInfo?.status || tab?.status,
      url: changeInfo?.url || tab?.url,
    };
    await maybeFinalizeCapture(tabId, completedTab);
  })();
});
