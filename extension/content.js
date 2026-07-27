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
  const linkedinDocument = () => {
    for (const frame of Array.from(document.querySelectorAll("iframe"))) {
      try {
        const nested = frame.contentDocument;
        if (nested && (
          nested.querySelectorAll("a[href*='/jobs/view/']").length
          || nested.querySelectorAll("[data-testid='lazy-column'] [role='button'][tabindex='0']").length
          || nested.querySelectorAll("li[data-occludable-job-id]").length
        )) return nested;
      } catch {
        // Cross-origin frames are never eligible evidence roots.
      }
    }
    return document;
  };
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
  const firstVisible = (root, selectors) => {
    for (const selector of selectors) {
      const node = visibleMatches(root, selector)[0];
      if (node) return node;
    }
    return null;
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
  const isSafeExternalApplyUrl = (value) => {
    const sanitizer = globalThis.JobSourceExternalApplySafety?.sanitize;
    return typeof sanitizer === "function" ? sanitizer(value, location.href) : "";
  };
  const EXTERNAL_APPLY_SELECTORS = [
    "a[data-control-name='jobdetails_topcard_external_apply']",
    "a[data-live-test-job-apply-button]",
    ".job-details-jobs-unified-top-card__apply-button a[href]",
    ".jobs-unified-top-card__apply-button a[href]",
    ".jobs-apply-button--top-card a[href]",
    "a.jobs-apply-button[href]",
    "a[aria-label*='Apply on company website'][href]"
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
  const selectedSemanticDetail = () => {
    const selectedId = selectedJobId();
    if (!selectedId) return null;
    const titleLink = visibleMatches(linkedinDocument(), "a[href*='/jobs/view/']").find((link) => (
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
    if (!root) return "";
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
      visibleMatches(linkedinDocument(), selector).map((root) => ({
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
    "button[aria-label*='Easy Apply']",
    "button[aria-label^='Apply']"
  ].join(", ")).some((button) => {
    if (!isEnabled(button)) return false;
    const label = `${text(button)} ${button.getAttribute("aria-label") || ""}`.toLowerCase();
    return label.includes("easy apply")
      || (button.getAttribute("data-control-name") || "") === "jobdetails_topcard_inapply";
  });
  const hasExternalApplyControl = (root) => visibleMatches(root, "button").some((button) => {
    if (!isEnabled(button)) return false;
    const label = `${text(button)} ${button.getAttribute("aria-label") || ""}`.toLowerCase();
    return label.includes("apply") && label.includes("company website");
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
    let observation = "detail_observed_but_apply_absent";
    if (hasClosedBanner(root)) {
      availability = "closed";
      observation = "closed_observed";
    } else if (externalUrl) {
      availability = "active";
      applyMode = "external";
      observation = "external_apply_observed";
    } else if (hasNativeApply(root)) {
      availability = "active";
      applyMode = "linkedin_native";
      observation = "linkedin_native_observed";
    }
    const evidence = {
      availability,
      apply_mode: applyMode,
      evidence_source: "authenticated_detail_dom",
      job_url: jobUrl,
      observation
    };
    if (!externalUrl && hasExternalApplyControl(root)) {
      evidence.external_apply_control = "target_url_unavailable_in_dom";
    }
    return evidence;
  };

  const detailRecord = () => {
    const detailRoot = selectedDetailRoot();
    const root = detailRoot.root;
    const header = detailRoot.header || root;
    const jobUrl = detailRoot.jobUrl;
    const externalUrl = externalApplyUrl(root);
    const postingEvidence = linkedinPostingEvidence(root, jobUrl, externalUrl);
    const linkedinCompanyUrl = canonicalCompanyUrl(firstHref(root, [
      ".job-details-jobs-unified-top-card__company-name a[href*='/company/']",
      ".jobs-unified-top-card__company-name a[href*='/company/']",
      "a[href*='/company/']"
    ]));
    return {
      linkedin_job_url: jobUrl,
      external_apply_url: postingEvidence.observation === "external_apply_observed"
        ? externalUrl
        : null,
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
        linkedin_posting: postingEvidence,
        dom: {
          scope: "authenticated_detail_dom",
          root_selector: detailRoot.selector,
          identity_source: detailRoot.identity
        }
      }
    };
  };

  const prepareExternalApplyCapture = () => {
    const record = detailRecord();
    const posting = record.source_trace?.linkedin_posting;
    const selectedId = selectedJobId();
    const recordId = jobIdFromValue(record.linkedin_job_url?.split("/").pop());
    if (!selectedId || recordId !== selectedId || !record.company_name || !record.job_title) {
      return { ok: false, error_code: "source_identity_changed" };
    }
    if (
      record.external_apply_url
      || posting?.observation !== "detail_observed_but_apply_absent"
      || posting?.external_apply_control !== "target_url_unavailable_in_dom"
    ) {
      return { ok: false, error_code: "external_control_not_observed" };
    }
    return {
      ok: true,
      capture_contract: "1",
      source: {
        linkedin_job_id: selectedId,
        linkedin_job_url: record.linkedin_job_url,
        company_name: record.company_name,
        job_title: record.job_title,
        job_location: record.job_location || "",
        external_apply_control: "target_url_unavailable_in_dom"
      }
    };
  };

  const cardRecords = () => {
    const cards = visibleMatches(
      linkedinDocument(),
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

  const PAGE_CARD_SELECTOR = "[data-testid='lazy-column'] [role='button'][tabindex='0']";
  const PAGE_CARD_SELECTORS = [
    PAGE_CARD_SELECTOR,
    "li[data-occludable-job-id]",
    ".job-card-container[data-job-id]"
  ];
  const PAGE_SCAN_LIMIT = 30;
  const PAGE_NAVIGATION_POLLS = 12;
  const PAGE_DETAIL_POLLS = 32;
  const PAGE_DETAIL_MIN_POLLS = 4;
  const PAGE_DETAIL_STABLE_POLLS = 3;
  const PAGE_POLL_INTERVAL_MS = 75;
  let activePageScan = null;

  const pause = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds));
  const pageCards = () => {
    const seen = new Set();
    const seenJobIds = new Set();
    return PAGE_CARD_SELECTORS.flatMap((selector) => (
      visibleMatches(linkedinDocument(), selector)
    )).filter((card) => {
      if (seen.has(card) || !isEnabled(card)) return false;
      seen.add(card);
      const cardJobId = explicitRootJobId(card)
        || explicitRootJobId(visibleMatches(card, ".job-card-container[data-job-id]")[0]);
      if (cardJobId && seenJobIds.has(cardJobId)) return false;
      const title = firstText(card, [
        ".job-card-list__title--link",
        ".job-card-container__link",
        "a[href*='/jobs/view/']"
      ]);
      const company = firstText(card, [
        ".artdeco-entity-lockup__subtitle",
        ".job-card-container__primary-description",
        ".job-card-container__company-name"
      ]);
      const paragraphs = visibleMatches(card, "p").filter((paragraph) => text(paragraph));
      const eligible = Boolean((title && company) || paragraphs.length >= 3);
      if (eligible && cardJobId) seenJobIds.add(cardJobId);
      return eligible;
    });
  };
  const firstDescendantText = (root, selectors) => {
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
    const titleLink = firstVisible(card, [
      ".job-card-list__title--link",
      ".job-card-container__link",
      "a[href*='/jobs/view/']"
    ]);
    const title = (
      titleParagraph
        ? firstDescendantText(titleParagraph, ["[aria-hidden='true']", "span"])
          || text(titleParagraph)
        : ""
    ) || text(titleLink);
    const jobId = explicitRootJobId(card)
      || explicitRootJobId(visibleMatches(card, ".job-card-container[data-job-id]")[0])
      || jobIdFromValue(canonicalJobUrl(titleLink?.href).split("/").pop());
    return {
      card,
      clickTarget: titleLink || card,
      jobId,
      selected: selectedJobId() === jobId
        || /^selected\s*,/i.test(text(titleParagraph))
        || (card.getAttribute("aria-selected") || "").toLowerCase() === "true",
      job_title: title,
      company_name: text(paragraphs[1]) || firstText(card, [
        ".artdeco-entity-lockup__subtitle",
        ".job-card-container__primary-description",
        ".job-card-container__company-name"
      ]),
      job_location: text(paragraphs[2]) || firstText(card, [
        ".artdeco-entity-lockup__caption",
        ".job-card-container__metadata-item",
        ".job-card-container__metadata-wrapper li"
      ])
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
        observation: "detail_not_observed"
      },
      dom: {
        scope: "authenticated_search_card",
        root_selector: "linkedin_lazy_column_card",
        identity_source: "selected_current_job_id"
      }
    }
  });
  const comparableText = (value) => String(value || "").replace(/\s+/g, " ").trim().toLowerCase();
  const comparableCompany = (left, right) => {
    const normalizedLeft = comparableText(left);
    const normalizedRight = comparableText(right);
    return normalizedLeft === normalizedRight
      || (normalizedLeft.length >= 4 && normalizedRight.includes(normalizedLeft))
      || (normalizedRight.length >= 4 && normalizedLeft.includes(normalizedRight));
  };
  const completeDetailFor = (jobUrl, metadata) => {
    const record = detailRecord();
    const matchingMetadata = !metadata || (
      comparableCompany(record.company_name, metadata.company_name)
      && comparableText(record.job_title) === comparableText(metadata.job_title)
    );
    return record.linkedin_job_url === jobUrl
      && record.company_name
      && record.job_title
      && matchingMetadata
      ? record
      : null;
  };
  const detailApplyResolved = (record) => new Set([
    "external_apply_observed",
    "linkedin_native_observed",
    "closed_observed"
  ]).has(record?.source_trace?.linkedin_posting?.observation);
  const detailFingerprint = (record) => JSON.stringify({
    apply_mode: record.source_trace.linkedin_posting.apply_mode,
    availability: record.source_trace.linkedin_posting.availability,
    company_name: record.company_name,
    external_apply_url: record.external_apply_url,
    identity_source: record.source_trace.dom.identity_source,
    job_title: record.job_title,
    linkedin_job_url: record.linkedin_job_url,
    observation: record.source_trace.linkedin_posting.observation,
    external_apply_control: record.source_trace.linkedin_posting.external_apply_control || null
  });
  const mergeRecord = (record, detail) => Object.fromEntries(
    Object.entries({ ...record, ...detail }).map(([key, value]) => [
      key,
      value || record[key] || null
    ])
  );
  const waitForSelectedJob = async (expectedId, previousId, polls = PAGE_NAVIGATION_POLLS) => {
    for (let attempt = 0; attempt < polls; attempt += 1) {
      const currentId = selectedJobId();
      if (currentId === expectedId && currentId !== previousId) return true;
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
  const settleDetailFor = async (jobUrl, metadata, scan) => {
    let latestDetail = null;
    let latestFingerprint = "";
    let stablePolls = 0;
    for (let attempt = 0; attempt < PAGE_DETAIL_POLLS; attempt += 1) {
      const detail = completeDetailFor(jobUrl, metadata);
      if (detail) {
        const fingerprint = detailFingerprint(detail);
        stablePolls = fingerprint === latestFingerprint ? stablePolls + 1 : 1;
        latestFingerprint = fingerprint;
        latestDetail = detail;
      } else {
        latestDetail = null;
        latestFingerprint = "";
        stablePolls = 0;
      }
      if (scan.cancelled) return detail;
      if (
        detailApplyResolved(detail)
        && attempt + 1 >= PAGE_DETAIL_MIN_POLLS
        && stablePolls >= PAGE_DETAIL_STABLE_POLLS
      ) return detail;
      await pause(PAGE_POLL_INTERVAL_MS);
    }
    return stablePolls >= PAGE_DETAIL_STABLE_POLLS ? latestDetail : null;
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
    navigation_failure_count: scan.navigationFailureCount,
    detail_not_observed_count: scan.detailNotObservedCount
  });
  const collectPage = async () => {
    const originalJobId = selectedJobId();
    const candidates = pageCards().slice(0, PAGE_SCAN_LIMIT).map(frozenCardMetadata);
    const scan = {
      cancelled: false,
      candidateCount: candidates.length,
      detailNotObservedCount: 0,
      failureCount: 0,
      navigationFailureCount: 0,
      records: [],
      scannedCount: 0
    };
    activePageScan = scan;
    const cardsByJobId = new Map();
    const originalCandidate = candidates.find((candidate) => candidate.selected);
    if (originalJobId && originalCandidate) {
      cardsByJobId.set(originalJobId, originalCandidate.clickTarget);
    }
    try {
      for (const candidate of candidates) {
        if (scan.cancelled) break;
        const previousId = selectedJobId();
        scan.scannedCount += 1;
        let currentId = "";
        try {
          if (candidate.selected && previousId && previousId === originalJobId) {
            currentId = previousId;
          } else {
            candidate.clickTarget.click();
            if (candidate.jobId) {
              const selected = await waitForSelectedJob(candidate.jobId, previousId);
              currentId = selected ? candidate.jobId : "";
            } else {
              currentId = await waitForChangedJob(previousId);
            }
          }
        } catch {
          currentId = "";
        }
        if (!currentId) {
          scan.failureCount += 1;
          scan.navigationFailureCount += 1;
        } else {
          cardsByJobId.set(currentId, candidate.clickTarget);
          const jobUrl = `https://www.linkedin.com/jobs/view/${currentId}`;
          let record = listedPageRecord(candidate, jobUrl);
          const detail = await settleDetailFor(jobUrl, candidate, scan);
          if (detail) {
            record = mergeRecord(record, detail);
          } else {
            scan.failureCount += 1;
            scan.detailNotObservedCount += 1;
          }
          if (!scan.records.some((existing) => existing.linkedin_job_url === jobUrl)) {
            scan.records.push(record);
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
        const originalCard = cardsByJobId.get(originalJobId);
        if (originalCard) {
          const previousId = selectedJobId();
          originalCard.click();
          await waitForSelectedJob(originalJobId, previousId);
        }
      }
      if (scan.cancelled) return pageScanResponse(scan, "cancelled");
      if (!scan.candidateCount) return pageScanResponse(scan, "not_ready");
      return pageScanResponse(scan, scan.failureCount ? "partial" : "ready");
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

  chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
    if (message?.type === "prepare_external_apply_capture") {
      try {
        sendResponse(prepareExternalApplyCapture());
      } catch {
        sendResponse({ ok: false, error_code: "source_identity_changed" });
      }
      return false;
    }
    if (message?.type === "cancel_job_source_page") {
      const cancelled = Boolean(activePageScan && !activePageScan.cancelled);
      if (activePageScan) activePageScan.cancelled = true;
      sendResponse({ ok: true, cancelled });
      return false;
    }
    if (message?.type === "collect_job_source_page") {
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
