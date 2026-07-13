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
  const canonicalJobUrl = (value) => {
    try {
      const url = new URL(value, location.href);
      const match = url.pathname.match(/\/jobs\/view\/(?:[^/?#]*-)?(\d+)/);
      return match ? `https://www.linkedin.com/jobs/view/${match[1]}` : "";
    } catch {
      return "";
    }
  };
  const canonicalCompanyUrl = (value) => {
    try {
      const url = new URL(value, location.href);
      const match = url.pathname.match(/^\/company\/([^/?#]+)/);
      return match ? `https://www.linkedin.com/company/${match[1]}` : "";
    } catch {
      return "";
    }
  };
  const externalApplyUrl = (root) => {
    for (const anchor of visibleMatches(root, "a[href]")) {
      if (!isEnabled(anchor)) continue;
      const label = `${text(anchor)} ${anchor.getAttribute("aria-label") || ""}`.toLowerCase();
      if (!label.includes("apply")) continue;
      try {
        const url = new URL(anchor.href, location.href);
        const host = url.hostname.toLowerCase();
        if (host !== "linkedin.com" && !host.endsWith(".linkedin.com") && !host.endsWith(".licdn.com")) {
          return url.href;
        }
      } catch {
        continue;
      }
    }
    return "";
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
    const root = visibleMatches(
      document,
      ".jobs-search__job-details--container, .job-view-layout, .jobs-details, main"
    )[0] || document;
    const jobUrl = canonicalJobUrl(location.href)
      || canonicalJobUrl(firstHref(root, ["a[href*='/jobs/view/']"]));
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
        linkedin_posting: linkedinPostingEvidence(root, jobUrl, externalUrl)
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
        source: "linkedin_browser_extension"
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

  chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
    if (message?.type !== "collect_job_source_records") return false;
    try {
      sendResponse({ ok: true, records: collect(), page_url: location.href });
    } catch (error) {
      sendResponse({ ok: false, error: String(error) });
    }
    return false;
  });
})();
