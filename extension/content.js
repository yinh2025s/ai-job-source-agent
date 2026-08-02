(() => {
  const CONTENT_SCRIPT_VERSION = "3";
  const INSTALLATION_KEY = "__jobSourceAgentContentScript";
  const previousInstallation = globalThis[INSTALLATION_KEY];
  try {
    previousInstallation?.dispose?.();
  } catch {
    // A stale extension context must not prevent the replacement listener from installing.
  }
  globalThis.__jobSourceAgentInstalled = CONTENT_SCRIPT_VERSION;

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
      let url = new URL(value, location.href);
      if (LINKEDIN_HOST(url.hostname.toLowerCase())) {
        if (url.protocol !== "https:" || url.pathname !== "/safety/go/") return "";
        const target = url.searchParams.get("url");
        if (!target) return "";
        url = new URL(target);
      }
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
    "a.jobs-apply-button[href]",
    "a[aria-label*='Apply on company website'][href]",
    "a[aria-label*='on company website'][href]",
    "a[aria-label*='company site'][href]"
  ];
  const EXTERNAL_MODE_CONTROL_SELECTOR = [
    "button[aria-label*='on company website']",
    "button[aria-label*='company site']",
    "button[role='link'][data-live-test-job-apply-button]"
  ].join(", ");
  const EXTERNAL_APPLY_TARGET_ATTRIBUTES = [
    "data-apply-url",
    "data-redirect-url",
    "data-url",
    "data-href"
  ];
  const externalApplyTargetValues = (control) => {
    const values = [
      control.href,
      control.getAttribute("href"),
      control.hasAttribute("formaction") ? control.formAction : "",
      control.getAttribute("formaction")
    ];
    for (const attribute of EXTERNAL_APPLY_TARGET_ATTRIBUTES) {
      values.push(control.getAttribute(attribute));
    }
    return values;
  };
  const safeExternalApplyTarget = (control) => {
    for (const value of externalApplyTargetValues(control)) {
      const target = isSafeExternalApplyUrl(value);
      if (target) return target;
    }
    return "";
  };
  const isExternalApplyControl = (control) => {
    if (!isEnabled(control)) return false;
    const label = `${text(control)} ${control.getAttribute("aria-label") || ""}`.toLowerCase();
    return label.includes("apply") && /(?:company website|company site)/.test(label);
  };
  const externalApplyUrl = (root) => {
    for (const selector of EXTERNAL_APPLY_SELECTORS) {
      for (const anchor of visibleMatches(root, selector)) {
        if (!isEnabled(anchor)) continue;
        const label = `${text(anchor)} ${anchor.getAttribute("aria-label") || ""}`.toLowerCase();
        if (!label.includes("apply")) continue;
        const externalUrl = safeExternalApplyTarget(anchor);
        if (externalUrl) return externalUrl;
      }
    }
    for (const control of visibleMatches(root, EXTERNAL_MODE_CONTROL_SELECTOR)) {
      if (!isExternalApplyControl(control)) continue;
      const externalUrl = safeExternalApplyTarget(control);
      if (externalUrl) return externalUrl;
    }
    return "";
  };
  const externalApplyClickControl = (root) => {
    for (const selector of EXTERNAL_APPLY_SELECTORS) {
      for (const control of visibleMatches(root, selector)) {
        if (!isEnabled(control)) continue;
        const label = `${text(control)} ${control.getAttribute("aria-label") || ""}`.toLowerCase();
        const declaredExternal = control.getAttribute("data-control-name")
          === "jobdetails_topcard_external_apply";
        if (label.includes("apply") && (
          safeExternalApplyTarget(control)
          || declaredExternal
          || /(?:company website|company site)/.test(label)
        )) return control;
      }
    }
    return visibleMatches(root, EXTERNAL_MODE_CONTROL_SELECTOR).find((control) => (
      isExternalApplyControl(control)
    )) || null;
  };
  const DETAIL_ROOT_SELECTORS = [
    ".jobs-search__job-details--container",
    ".job-view-layout",
    ".jobs-details",
    "main"
  ];
  const selectedSemanticDetail = () => {
    const selectedId = selectedJobId();
    if (!selectedId) return null;
    const titleLink = visibleMatches(document, "a[href*='/jobs/view/']").find((link) => (
      canonicalJobUrl(link.href).endsWith(`/${selectedId}`)
    ));
    if (!titleLink) return null;

    let header = null;
    for (let current = titleLink; current && current !== document.body; current = current.parentElement) {
      const hasCompany = visibleMatches(current, "a[href*='/company/']").length > 0;
      const locationNode = Array.from(current.children || []).find((child) => (
        child.tagName === "P" && !child.contains?.(titleLink) && text(child)
      ));
      if (hasCompany && locationNode) {
        header = current;
        break;
      }
    }
    if (!header) return null;

    let root = header;
    for (let current = header; current && current !== document.body; current = current.parentElement) {
      const hasApply = visibleMatches(current, "a, button").some((control) => (
        /apply/i.test(`${text(control)} ${control.getAttribute?.("aria-label") || ""}`)
      ));
      if (hasApply) {
        root = current;
        break;
      }
    }
    const locationNode = Array.from(header.children || []).find((child) => (
      child.tagName === "P" && !child.contains?.(titleLink) && text(child)
    ));
    return { root, header, titleLink, locationNode, selectedId };
  };
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
    const semanticDetail = selectedSemanticDetail();
    if (semanticDetail) return {
      ...semanticDetail,
      selector: "selected_job_semantic_detail",
      jobUrl: `https://www.linkedin.com/jobs/view/${semanticDetail.selectedId}`,
      identity: "selected_detail_semantic_link"
    };
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
  const hasExternalApplyControl = (root) => visibleMatches(
    root,
    EXTERNAL_MODE_CONTROL_SELECTOR
  ).some(isExternalApplyControl);
  const hasNativeApply = (root) => visibleMatches(root, [
    "button.jobs-apply-button",
    "button[data-control-name='jobdetails_topcard_inapply']",
    "button[data-live-test-job-apply-button]",
    "button[aria-label*='Easy Apply']",
    "button[aria-label^='Apply']"
  ].join(", ")).some((button) => {
    if (!isEnabled(button)) return false;
    const label = `${text(button)} ${button.getAttribute("aria-label") || ""}`.toLowerCase();
    if (/(?:company website|company site)/.test(label)) return false;
    return label.includes("easy apply")
      || button.getAttribute("data-control-name") === "jobdetails_topcard_inapply";
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
    } else if (externalUrl || hasExternalApplyControl(root)) {
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
    const header = detailRoot.header || root;
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
      company_name: firstText(header, [
        "[aria-label^='Company, ']",
        ".job-details-jobs-unified-top-card__company-name",
        ".jobs-unified-top-card__company-name",
        "a[href*='/company/']"
      ]),
      job_title: detailRoot.titleLink ? text(detailRoot.titleLink) : firstText(root, [
        ".job-details-jobs-unified-top-card__job-title h1",
        ".job-details-jobs-unified-top-card__job-title",
        ".jobs-unified-top-card__job-title",
        "h1"
      ]),
      job_location: detailRoot.locationNode
        ? text(detailRoot.locationNode).split("·", 1)[0].trim()
        : firstText(root, [
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
            job_url: jobUrl,
            observation_state: "detail_not_observed"
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

  const PAGE_CARD_SELECTOR = [
    "[data-occludable-job-id]",
    "[data-testid='lazy-column'] [role='button'][tabindex='0']"
  ].join(", ");
  const PAGE_SCAN_LIMIT = 30;
  const PAGE_NAVIGATION_POLLS = 12;
  const PAGE_CARD_POLLS = 24;
  const PAGE_DETAIL_POLLS = 24;
  const SELECTED_DETAIL_POLLS = 24;
  const PAGE_POLL_INTERVAL_MS = 50;
  let activePageScan = null;

  const pause = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds));
  const pageCards = () => {
    const seen = new Set();
    return visibleMatches(document, PAGE_CARD_SELECTOR).filter((card) => {
      if (!isEnabled(card)) return false;
      const jobId = explicitRootJobId(card)
        || jobIdFromValue(canonicalJobUrl(firstHref(card, ["a[href*='/jobs/view/']"])).split("/").pop());
      if (!jobId || seen.has(jobId)) return false;
      seen.add(jobId);
      return true;
    });
  };
  const firstDescendantText = (root, selectors) => {
    if (!root) return "";
    for (const selector of selectors) {
      for (const node of Array.from(root.querySelectorAll(selector))) {
        const value = text(node);
        if (value) return value;
      }
    }
    return "";
  };
  const frozenCardMetadata = (card) => {
    const paragraphs = visibleMatches(card, "p").filter((paragraph) => text(paragraph));
    const titleParagraph = paragraphs[0];
    const jobId = explicitRootJobId(card)
      || jobIdFromValue(canonicalJobUrl(firstHref(card, ["a[href*='/jobs/view/']"])).split("/").pop());
    const title = firstText(card, [
      ".job-card-list__title--link",
      ".artdeco-entity-lockup__title",
      "a[href*='/jobs/view/']"
    ]) || firstDescendantText(titleParagraph, ["[aria-hidden='true']", "span"])
      || text(titleParagraph);
    const company = firstText(card, [
      ".artdeco-entity-lockup__subtitle",
      ".job-card-container__primary-description",
      ".job-card-container__company-name"
    ]) || text(paragraphs[1]);
    const locationValue = firstText(card, [
      ".artdeco-entity-lockup__caption",
      ".job-card-container__metadata-item"
    ]) || text(paragraphs[2]);
    return {
      root: card,
      card: card.querySelector(".job-card-container--clickable, a[href*='/jobs/view/']") || card,
      job_id: jobId,
      selected: jobId === selectedJobId()
        || /^selected\s*,/i.test(text(titleParagraph))
        || (card.getAttribute?.("aria-selected") || "").toLowerCase() === "true",
      job_title: title,
      company_name: company,
      job_location: locationValue
    };
  };
  const listedPageRecord = (metadata, jobUrl) => ({
    linkedin_job_url: jobUrl,
    external_apply_url: null,
    linkedin_company_url: null,
    company_name: metadata.company_name,
    job_title: metadata.job_title,
    job_location: metadata.job_location,
    source: "linkedin_browser_extension",
    source_trace: {
      linkedin_posting: {
        availability: "listed",
        apply_mode: "unknown",
        evidence_source: "authenticated_search_card",
        job_url: jobUrl,
        observation_state: "detail_not_observed"
      },
      dom: {
        scope: "authenticated_search_card",
        root_selector: "linkedin_job_card",
        identity_source: "selected_current_job_id"
      }
    }
  });
  const completeDetailFor = (jobUrl) => {
    const record = detailRecord();
    return record.linkedin_job_url === jobUrl && record.company_name && record.job_title ? record : null;
  };
  const detailApplyResolved = (record) => (
    Boolean(record)
    && record.source_trace?.linkedin_posting?.availability !== "unknown"
  );
  const observationState = (record, detailObserved = true) => {
    if (!detailObserved) return "detail_not_observed";
    const posting = record?.source_trace?.linkedin_posting || {};
    if (posting.availability === "closed") return "closed_observed";
    if (posting.apply_mode === "external") return "external_apply_observed";
    if (posting.apply_mode === "linkedin_native") return "linkedin_native_observed";
    return "detail_observed_but_apply_absent";
  };
  const withObservationState = (record, detailObserved = true) => ({
    ...record,
    source_trace: {
      ...record.source_trace,
      linkedin_posting: {
        ...record.source_trace?.linkedin_posting,
        observation_state: observationState(record, detailObserved)
      }
    }
  });
  const mergeRecord = (record, detail) => Object.fromEntries(
    Object.entries({ ...record, ...detail }).map(([key, value]) => [
      key,
      value || record[key] || null
    ])
  );
  const waitForSelectedJob = async (expectedId, _previousId, polls = PAGE_NAVIGATION_POLLS) => {
    for (let attempt = 0; attempt < polls; attempt += 1) {
      const currentId = selectedJobId();
      if (currentId === expectedId) return true;
      await pause(PAGE_POLL_INTERVAL_MS);
    }
    return false;
  };
  const waitForChangedJob = async (previousId, polls = PAGE_NAVIGATION_POLLS) => {
    for (let attempt = 0; attempt < polls; attempt += 1) {
      const currentId = selectedJobId();
      if (currentId && currentId !== previousId) return currentId;
      await pause(PAGE_POLL_INTERVAL_MS);
    }
    return "";
  };
  const completeCardMetadata = (metadata) => (
    Boolean(metadata?.job_id && metadata.job_title && metadata.company_name)
  );
  const settleCardMetadata = async (card, scan, polls = PAGE_CARD_POLLS) => {
    let metadata = frozenCardMetadata(card);
    if (completeCardMetadata(metadata)) return metadata;
    try {
      card.scrollIntoView?.({ block: "center", inline: "nearest" });
    } catch {
      // A detached virtualized card becomes one partial failure, not a batch failure.
    }
    for (let attempt = 0; attempt < polls; attempt += 1) {
      if (scan.cancelled) return metadata;
      await pause(PAGE_POLL_INTERVAL_MS);
      metadata = frozenCardMetadata(card);
      if (completeCardMetadata(metadata)) return metadata;
    }
    return metadata;
  };
  const settleDetailFor = async (jobUrl, scan, polls = PAGE_DETAIL_POLLS) => {
    let latestDetail = null;
    for (let attempt = 0; attempt < polls; attempt += 1) {
      const detail = completeDetailFor(jobUrl);
      if (detail) latestDetail = detail;
      if (detailApplyResolved(detail) || scan.cancelled) {
        return detail ? withObservationState(detail) : detail;
      }
      await pause(PAGE_POLL_INTERVAL_MS);
    }
    return latestDetail ? withObservationState(latestDetail) : null;
  };
  const settleSelectedDetail = async (polls = SELECTED_DETAIL_POLLS) => {
    const expectedId = selectedJobId();
    if (!expectedId) return null;
    return settleDetailFor(`https://www.linkedin.com/jobs/view/${expectedId}`, { cancelled: false }, polls);
  };
  const pageScanResponse = (scan, state) => ({
    ok: true,
    records: scan.records,
    page_url: location.href,
    scan_version: "3",
    state,
    scanned_count: scan.scannedCount,
    candidate_count: scan.candidateCount,
    failure_count: scan.failureCount,
    detail_observed_count: scan.detailObservedCount,
    detail_not_observed_count: scan.detailNotObservedCount,
    apply_observed_count: scan.applyObservedCount
  });
  const collectPage = async () => {
    const originalJobId = selectedJobId();
    const candidates = pageCards()
      .map(frozenCardMetadata)
      .filter((candidate) => candidate.job_id)
      .slice(0, PAGE_SCAN_LIMIT);
    const scan = {
      cancelled: false,
      candidateCount: candidates.length,
      failureCount: 0,
      detailNotObservedCount: 0,
      detailObservedCount: 0,
      applyObservedCount: 0,
      records: [],
      scannedCount: 0
    };
    activePageScan = scan;
    const cardsByJobId = new Map();
    for (const candidate of candidates) cardsByJobId.set(candidate.job_id, candidate.root);
    try {
      for (const initialCandidate of candidates) {
        if (scan.cancelled) break;
        const candidate = await settleCardMetadata(initialCandidate.root, scan);
        const previousId = selectedJobId();
        scan.scannedCount += 1;
        let currentId = "";
        if (!completeCardMetadata(candidate)) {
          scan.failureCount += 1;
        } else {
          try {
            if (candidate.selected && previousId
              && (!candidate.job_id || candidate.job_id === previousId)) {
              currentId = previousId;
            } else {
              candidate.card.click();
              if (candidate.job_id) {
                const selected = await waitForSelectedJob(candidate.job_id, previousId);
                currentId = selected ? candidate.job_id : "";
              } else {
                currentId = await waitForChangedJob(previousId);
              }
            }
          } catch {
            currentId = "";
          }
          if (!currentId) {
            scan.failureCount += 1;
          } else {
            cardsByJobId.set(currentId, candidate.root);
            const jobUrl = `https://www.linkedin.com/jobs/view/${currentId}`;
            let record = listedPageRecord(candidate, jobUrl);
            const detail = await settleDetailFor(jobUrl, scan);
            if (detail) {
              record = mergeRecord(record, detail);
              scan.detailObservedCount += 1;
              if (["external_apply_observed", "linkedin_native_observed"].includes(
                detail.source_trace?.linkedin_posting?.observation_state
              )) scan.applyObservedCount += 1;
            } else {
              scan.detailNotObservedCount += 1;
            }
            if (!scan.records.some((existing) => existing.linkedin_job_url === jobUrl)) {
              scan.records.push(record);
            }
          }
        }
        try {
          const progress = chrome.runtime.sendMessage?.({
            type: "job_source_page_progress",
            scanned_count: scan.scannedCount,
            candidate_count: scan.candidateCount
          });
          progress?.catch?.(() => {});
        } catch {
          // The popup may close while the content scan continues.
        }
      }
      if (originalJobId && selectedJobId() !== originalJobId) {
        const originalRoot = cardsByJobId.get(originalJobId);
        if (originalRoot) {
          const originalCard = await settleCardMetadata(originalRoot, { cancelled: false });
          const previousId = selectedJobId();
          originalCard.card.click();
          await waitForSelectedJob(originalJobId, previousId);
        }
      }
      if (scan.cancelled) return pageScanResponse(scan, "cancelled");
      if (!scan.candidateCount) return pageScanResponse(scan, "not_ready");
      return pageScanResponse(
        scan,
        scan.failureCount || scan.detailNotObservedCount ? "partial" : "ready"
      );
    } finally {
      if (activePageScan === scan) activePageScan = null;
    }
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

  const prepareExternalApply = async (expectedId) => {
    if (!/^\d+$/.test(String(expectedId || ""))) {
      return { ok: false, status: "rejected", error: "invalid_linkedin_job_id" };
    }
    if (!isLinkedinJobsRoute()) {
      return { ok: false, status: "rejected", error: "not_linkedin_jobs_page" };
    }
    if (activePageScan) {
      return { ok: false, status: "busy", error: "page_scan_in_progress" };
    }

    let currentId = selectedJobId();
    if (currentId !== expectedId) {
      const candidate = pageCards()
        .map(frozenCardMetadata)
        .find((metadata) => metadata.job_id === expectedId);
      if (!candidate?.card) {
        return { ok: false, status: "rejected", error: "linkedin_job_card_not_available" };
      }
      try {
        candidate.card.click();
      } catch {
        return { ok: false, status: "rejected", error: "linkedin_job_selection_failed" };
      }
      const selected = await waitForSelectedJob(expectedId, currentId);
      if (!selected) {
        return { ok: false, status: "rejected", error: "linkedin_job_selection_timed_out" };
      }
      currentId = selectedJobId();
    }
    if (currentId !== expectedId) {
      return { ok: false, status: "rejected", error: "linkedin_job_identity_mismatch" };
    }

    const jobUrl = `https://www.linkedin.com/jobs/view/${expectedId}`;
    const detail = await settleDetailFor(jobUrl, { cancelled: false }, SELECTED_DETAIL_POLLS);
    const detailRoot = selectedDetailRoot();
    if (!detail || detailRoot.jobUrl !== jobUrl) {
      return { ok: false, status: "rejected", error: "linkedin_job_detail_not_observed" };
    }
    const control = externalApplyClickControl(detailRoot.root);
    if (!control) {
      return { ok: false, status: "rejected", error: "external_apply_control_not_available" };
    }
    return { ok: true, status: "ready", linkedin_job_id: expectedId };
  };

  const triggerExternalApply = async (expectedId) => {
    const prepared = await prepareExternalApply(expectedId);
    if (!prepared.ok) return prepared;
    if (selectedJobId() !== expectedId) {
      return { ok: false, status: "rejected", error: "linkedin_job_identity_mismatch" };
    }
    const detailRoot = selectedDetailRoot();
    const control = detailRoot.jobUrl === `https://www.linkedin.com/jobs/view/${expectedId}`
      ? externalApplyClickControl(detailRoot.root)
      : null;
    if (!control) {
      return { ok: false, status: "rejected", error: "external_apply_control_not_available" };
    }
    try {
      control.click();
    } catch {
      return { ok: false, status: "rejected", error: "external_apply_click_failed" };
    }
    return { ok: true, status: "clicked", linkedin_job_id: expectedId };
  };

  const messageListener = (message, _sender, sendResponse) => {
    if (message?.type === "job_source_agent_content_status") {
      sendResponse({
        ok: true,
        content_script_version: CONTENT_SCRIPT_VERSION,
        scan_versions: ["2", "3"]
      });
      return false;
    }
    if (message?.type === "prepare_external_apply_v1") {
      prepareExternalApply(message.linkedin_job_id).then(sendResponse).catch((error) => {
        sendResponse({
          ok: false,
          status: "error",
          error: String(error?.message || error || "external_apply_prepare_failed")
        });
      });
      return true;
    }
    if (message?.type === "trigger_external_apply_v1") {
      triggerExternalApply(message.linkedin_job_id).then(sendResponse).catch((error) => {
        sendResponse({
          ok: false,
          status: "error",
          error: String(error?.message || error || "external_apply_click_failed")
        });
      });
      return true;
    }
    if (["cancel_job_source_page", "cancel_job_source_page_v1"].includes(message?.type)) {
      const cancelled = Boolean(activePageScan && !activePageScan.cancelled);
      if (activePageScan) activePageScan.cancelled = true;
      sendResponse({ ok: true, cancelled });
      return false;
    }
    if (["collect_job_source_page", "collect_job_source_page_v1"].includes(message?.type)) {
      if (activePageScan) {
        sendResponse({
          ok: false,
          records: [],
          page_url: location.href,
          scan_version: "3",
          state: "partial",
          scanned_count: 0,
          candidate_count: 0,
          failure_count: 0
        });
        return false;
      }
      collectPage().then(sendResponse).catch((error) => {
        sendResponse({
          ok: false,
          records: [],
          page_url: location.href,
          scan_version: "3",
          state: "partial",
          scanned_count: 0,
          candidate_count: 0,
          failure_count: 1,
          error: String(error)
        });
      });
      return true;
    }
    if (!["collect_job_source_records", "collect_job_source_records_v1"].includes(message?.type)) {
      return false;
    }
    const respondWithRecords = async () => {
      let records = collect();
      const selectedId = isLinkedinJobsRoute() ? selectedJobId() : "";
      let selectedDetail = null;
      if (selectedId) {
        const detail = await settleSelectedDetail();
        if (detail) {
          selectedDetail = detail;
          records = [detail];
        } else {
          records = records.filter((record) => (
            record.linkedin_job_url === `https://www.linkedin.com/jobs/view/${selectedId}`
          )).slice(0, 1);
        }
      }
      const completeDetail = records.some((record) => (
        record.source_trace?.dom?.scope === "authenticated_detail_dom"
        && record.linkedin_job_url && record.company_name && record.job_title
      ));
      let state = "ready";
      if (selectedId && !selectedDetail) state = records.length ? "partial" : "not_ready";
      else if (isLinkedinJobsRoute() && !records.length && !completeDetail) state = "not_ready";
      sendResponse({
        ok: true,
        records,
        page_url: location.href,
        scan_version: "2",
        state
      });
    };
    respondWithRecords().catch((error) => {
      sendResponse({ ok: false, error: String(error) });
    });
    return true;
  };
  chrome.runtime.onMessage.addListener(messageListener);
  globalThis[INSTALLATION_KEY] = {
    version: CONTENT_SCRIPT_VERSION,
    listener: messageListener,
    dispose: () => {
      if (activePageScan) activePageScan.cancelled = true;
      chrome.runtime.onMessage.removeListener?.(messageListener);
    }
  };
})();
