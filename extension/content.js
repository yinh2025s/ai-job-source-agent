(() => {
  if (globalThis.__jobSourceAgentInstalled) return;
  globalThis.__jobSourceAgentInstalled = true;

  const text = (node) => (node?.textContent || "").replace(/\s+/g, " ").trim();
  const isVisible = (node) => {
    for (let current = node; current; current = current.parentElement) {
      if (current.hidden || current.hasAttribute?.("hidden")) return false;
      if ((current.getAttribute?.("aria-hidden") || "").trim().toLowerCase() === "true") return false;
      const style = getComputedStyle(current);
      if (style.display === "none" || ["hidden", "collapse"].includes(style.visibility)) {
        return false;
      }
    }
    return true;
  };
  const visibleMatches = (root, selector) => (
    Array.from(root.querySelectorAll(selector)).filter(isVisible)
  );
  const isEnabled = (node) => (
    !node.disabled
    && !node.hasAttribute?.("disabled")
    && (node.getAttribute?.("aria-disabled") || "").trim().toLowerCase() !== "true"
  );
  const firstText = (root, selectors) => {
    for (const selector of selectors) {
      for (const node of visibleMatches(root, selector)) {
        const value = text(node);
        if (value) return value;
      }
    }
    return "";
  };
  const firstHref = (root, selectors) => {
    for (const selector of selectors) {
      const node = visibleMatches(root, selector).find((candidate) => candidate.href);
      if (node) return node.href;
    }
    return "";
  };
  const LINKEDIN_HOST = (hostname) => (
    hostname === "linkedin.com" || hostname.endsWith(".linkedin.com")
  );
  const LINKEDIN_OWNED_HOST = (hostname) => (
    LINKEDIN_HOST(hostname) || hostname === "licdn.com" || hostname.endsWith(".licdn.com")
  );
  const LOOKS_LIKE_LINKEDIN_HOST = (hostname) => /(?:linkedin|licdn)/i.test(hostname);
  const isPublicHost = (hostname) => {
    const host = hostname.toLowerCase().replace(/^\[|\]$/g, "");
    if (!host || host === "localhost" || host.endsWith(".localhost")
      || host.endsWith(".local") || host.endsWith(".internal")) {
      return false;
    }
    if (host === "::" || host === "::1" || /^(?:fc|fd|fe8|fe9|fea|feb)/i.test(host)) return false;
    const octets = host.split(".").map(Number);
    if (octets.length !== 4 || octets.some((octet) => !Number.isInteger(octet) || octet < 0 || octet > 255)) {
      return true;
    }
    return !(
      octets[0] === 0 || octets[0] === 10 || octets[0] === 127 || octets[0] >= 224
      || (octets[0] === 100 && octets[1] >= 64 && octets[1] <= 127)
      || (octets[0] === 169 && octets[1] === 254)
      || (octets[0] === 172 && octets[1] >= 16 && octets[1] <= 31)
      || (octets[0] === 192 && octets[1] === 168)
      || (octets[0] === 198 && (octets[1] === 18 || octets[1] === 19))
    );
  };
  const jobIdFromValue = (value) => {
    const match = String(value || "").match(/(?:^|jobPosting:)(\d+)$/i);
    return match ? match[1] : "";
  };
  const canonicalJobUrl = (value) => {
    try {
      const url = new URL(value, location.href);
      if (url.protocol !== "https:" || !LINKEDIN_HOST(url.hostname.toLowerCase())) return "";
      const match = url.pathname.match(/^\/jobs\/view\/(?:[^/?#]*-)?(\d+)(?:\/|$)/);
      return match ? `https://www.linkedin.com/jobs/view/${match[1]}` : "";
    } catch {
      return "";
    }
  };
  const canonicalCompanyUrl = (value) => {
    try {
      const url = new URL(value, location.href);
      if (url.protocol !== "https:" || !LINKEDIN_HOST(url.hostname.toLowerCase())) return "";
      const match = url.pathname.match(/^\/company\/([^/?#]+)/);
      return match ? `https://www.linkedin.com/company/${match[1]}` : "";
    } catch {
      return "";
    }
  };
  const hasSensitiveQuery = (url) => {
    const sensitiveKey = /(?:^|[_-])(?:token|session|auth|authorization|api[_-]?key|csrf|xsrf|secret|password|credential|signature|sig)(?:$|[_-])/i;
    return Array.from(url.searchParams.keys()).some((key) => sensitiveKey.test(key));
  };
  const isSafeExternalApplyUrl = (value) => {
    try {
      const url = new URL(value, location.href);
      if (!/^https?:$/.test(url.protocol) || url.username || url.password || url.hash) return "";
      if (!isPublicHost(url.hostname) || LINKEDIN_OWNED_HOST(url.hostname.toLowerCase())
        || LOOKS_LIKE_LINKEDIN_HOST(url.hostname) || hasSensitiveQuery(url)) return "";
      return url.href;
    } catch {
      return "";
    }
  };
  const EXTERNAL_APPLY_SELECTORS = [
    "a[data-control-name='jobdetails_topcard_external_apply']",
    "a[data-live-test-job-apply-button]",
    ".job-details-jobs-unified-top-card__apply-button a[href]",
    ".jobs-unified-top-card__apply-button a[href]",
    ".jobs-apply-button--top-card a[href]",
    "a.jobs-apply-button[href]"
  ];
  const externalApplyUrl = (root) => {
    for (const selector of EXTERNAL_APPLY_SELECTORS) {
      for (const anchor of visibleMatches(root, selector)) {
        if (!isEnabled(anchor)) continue;
        const label = `${text(anchor)} ${anchor.getAttribute("aria-label") || ""}`.toLowerCase();
        if (!label.includes("apply")) continue;
        const externalUrl = isSafeExternalApplyUrl(anchor.href);
        if (externalUrl) return externalUrl;
      }
    }
    return "";
  };
  const DETAIL_ROOT_SELECTORS = [
    ".jobs-search__job-details--container",
    ".job-view-layout",
    ".jobs-details",
    "main"
  ];
  const explicitRootJobId = (root) => {
    for (const attribute of [
      "data-current-job-id",
      "data-job-id",
      "data-occludable-job-id",
      "data-entity-urn"
    ]) {
      const jobId = jobIdFromValue(root.getAttribute?.(attribute));
      if (jobId) return jobId;
    }
    return "";
  };
  const selectedJobId = () => {
    try {
      const url = new URL(location.href);
      return jobIdFromValue(url.searchParams.get("currentJobId"))
        || jobIdFromValue(canonicalJobUrl(url.href).split("/").pop());
    } catch {
      return "";
    }
  };
  const rootJobUrl = (root, explicitJobId) => (
    explicitJobId
      ? `https://www.linkedin.com/jobs/view/${explicitJobId}`
      : canonicalJobUrl(firstHref(root, ["a[href*='/jobs/view/']"]))
  );
  const selectedDetailRoot = () => {
    const selectedId = selectedJobId();
    const roots = DETAIL_ROOT_SELECTORS.flatMap((selector) => (
      visibleMatches(document, selector).map((root) => ({
        root,
        selector,
        explicitJobId: explicitRootJobId(root)
      }))
    ));
    if (!roots.length) return { root: document, selector: "document", jobUrl: "", identity: "none" };
    if (selectedId) {
      const explicitMatch = roots.find(({ explicitJobId }) => explicitJobId === selectedId);
      if (explicitMatch) return {
        ...explicitMatch,
        jobUrl: `https://www.linkedin.com/jobs/view/${selectedId}`,
        identity: "selected_detail_root"
      };
      const descendantMatch = roots.find(({ root, explicitJobId }) => (
        !explicitJobId && rootJobUrl(root, "").endsWith(`/${selectedId}`)
      ));
      if (!descendantMatch) return { root: document, selector: "document", jobUrl: "", identity: "unmatched_selected_job" };
      return {
        ...descendantMatch,
        jobUrl: `https://www.linkedin.com/jobs/view/${selectedId}`,
        identity: "selected_job_link"
      };
    }
    const candidate = roots.find(({ root, explicitJobId }) => rootJobUrl(root, explicitJobId));
    if (!candidate) return { root: document, selector: "document", jobUrl: "", identity: "none" };
    return {
      ...candidate,
      jobUrl: rootJobUrl(candidate.root, candidate.explicitJobId),
      identity: candidate.explicitJobId ? "detail_root_job_id" : "detail_job_link"
    };
  };
  const hasNativeApply = (root) => visibleMatches(root, [
    "button.jobs-apply-button",
    "button[data-control-name='jobdetails_topcard_inapply']",
    "button[data-live-test-job-apply-button]",
    "button[aria-label*='Easy Apply']"
  ].join(", ")).some((button) => {
    if (!isEnabled(button)) return false;
    const label = `${text(button)} ${button.getAttribute("aria-label") || ""}`.toLowerCase();
    return label.includes("apply");
  });
  const hasClosedBanner = (root) => visibleMatches(root, [
    ".jobs-details-top-card__apply-error",
    ".jobs-unified-top-card__closed-job",
    "[data-job-closed='true']"
  ].join(", ")).some((banner) => {
    if ((banner.getAttribute("data-job-closed") || "").trim().toLowerCase() === "true") {
      return true;
    }
    return /(?:no longer accepting applications|job (?:is )?(?:no longer available|unavailable)|job has expired|applications? (?:are|is) closed|position has been filled)/i.test(text(banner));
  });

  const linkedinPostingEvidence = (root, jobUrl, externalUrl) => {
    let availability = "unknown";
    let applyMode = "unknown";
    if (hasClosedBanner(root)) {
      availability = "closed";
    } else if (externalUrl) {
      availability = "active";
      applyMode = "external";
    } else if (hasNativeApply(root)) {
      availability = "active";
      applyMode = "linkedin_native";
    }
    return {
      availability,
      apply_mode: applyMode,
      evidence_source: "authenticated_detail_dom",
      job_url: jobUrl
    };
  };

  const detailRecord = () => {
    const detailRoot = selectedDetailRoot();
    const root = detailRoot.root;
    const jobUrl = detailRoot.jobUrl;
    const externalUrl = externalApplyUrl(root);
    const linkedinCompanyUrl = canonicalCompanyUrl(firstHref(root, [
      ".job-details-jobs-unified-top-card__company-name a[href*='/company/']",
      ".jobs-unified-top-card__company-name a[href*='/company/']",
      "a[href*='/company/']"
    ]));
    return {
      linkedin_job_url: jobUrl,
      external_apply_url: externalUrl || null,
      linkedin_company_url: linkedinCompanyUrl || null,
      company_name: firstText(root, [
        ".job-details-jobs-unified-top-card__company-name",
        ".jobs-unified-top-card__company-name",
        "a[href*='/company/']"
      ]),
      job_title: firstText(root, [
        ".job-details-jobs-unified-top-card__job-title h1",
        ".job-details-jobs-unified-top-card__job-title",
        ".jobs-unified-top-card__job-title",
        "h1"
      ]),
      job_location: firstText(root, [
        ".job-details-jobs-unified-top-card__primary-description-container .tvm__text",
        ".jobs-unified-top-card__bullet",
        ".job-details-jobs-unified-top-card__tertiary-description-container"
      ]),
      source: "linkedin_browser_extension",
      source_trace: {
        linkedin_posting: linkedinPostingEvidence(root, jobUrl, externalUrl),
        dom: {
          scope: "authenticated_detail_dom",
          root_selector: detailRoot.selector,
          identity_source: detailRoot.identity
        }
      }
    };
  };

  const cardRecords = () => {
    const cards = visibleMatches(
      document,
      "li.jobs-search-results__list-item, [data-occludable-job-id], .job-card-container, .base-card"
    );
    const records = [];
    for (const card of cards) {
      const jobUrl = canonicalJobUrl(firstHref(card, [
        "a.job-card-list__title--link",
        "a.job-card-container__link",
        "a.base-card__full-link",
        "a[href*='/jobs/view/']"
      ]));
      const companyHref = firstHref(card, ["a[href*='/company/']"]);
      const record = {
        linkedin_job_url: jobUrl,
        external_apply_url: null,
        linkedin_company_url: canonicalCompanyUrl(companyHref) || null,
        company_name: firstText(card, [
          ".job-card-container__primary-description",
          ".base-search-card__subtitle",
          ".job-card-container__company-name",
          "a[href*='/company/']"
        ]),
        job_title: firstText(card, [
          ".job-card-list__title--link",
          ".job-card-container__link",
          ".base-search-card__title",
          "a[href*='/jobs/view/']"
        ]),
        job_location: firstText(card, [
          ".job-card-container__metadata-item",
          ".job-search-card__location",
          ".base-search-card__metadata"
        ]),
        source: "linkedin_browser_extension",
        source_trace: {
          linkedin_posting: {
            availability: "listed",
            apply_mode: "unknown",
            evidence_source: "public_search_card",
            job_url: jobUrl
          },
          dom: {
            scope: "public_search_card",
            root_selector: "job_search_card",
            identity_source: "card_job_link"
          }
        }
      };
      if (record.linkedin_job_url && record.company_name && record.job_title) records.push(record);
    }
    return records;
  };

  const collect = () => {
    const records = cardRecords();
    const detail = detailRecord();
    const index = records.findIndex((record) => record.linkedin_job_url === detail.linkedin_job_url);
    if (detail.linkedin_job_url && detail.company_name && detail.job_title) {
      if (index >= 0) {
        records[index] = Object.fromEntries(
          Object.entries({ ...records[index], ...detail }).map(([key, value]) => [
            key,
            value || records[index][key] || null
          ])
        );
      } else {
        records.unshift(detail);
      }
    }
    const seen = new Set();
    return records.filter((record) => {
      if (seen.has(record.linkedin_job_url)) return false;
      seen.add(record.linkedin_job_url);
      return true;
    }).slice(0, 30);
  };

  const isLinkedinJobsRoute = () => {
    try {
      const url = new URL(location.href);
      return url.protocol === "https:" && LINKEDIN_HOST(url.hostname.toLowerCase())
        && /^\/jobs(?:\/|$)/.test(url.pathname);
    } catch {
      return false;
    }
  };

  chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
    if (message?.type !== "collect_job_source_records") return false;
    try {
      const records = collect();
      const completeDetail = records.some((record) => (
        record.source_trace?.dom?.scope === "authenticated_detail_dom"
        && record.linkedin_job_url && record.company_name && record.job_title
      ));
      sendResponse({
        ok: true,
        records,
        page_url: location.href,
        scan_version: "2",
        state: isLinkedinJobsRoute() && !records.length && !completeDetail ? "not_ready" : "ready"
      });
    } catch (error) {
      sendResponse({ ok: false, error: String(error) });
    }
    return false;
  });
})();
