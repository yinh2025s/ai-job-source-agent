(() => {
  if (globalThis.__jobSourceAgentInstalled) return;
  globalThis.__jobSourceAgentInstalled = true;

  const text = (node) => (node?.textContent || "").replace(/\s+/g, " ").trim();
  const firstText = (root, selectors) => {
    for (const selector of selectors) {
      const value = text(root.querySelector(selector));
      if (value) return value;
    }
    return "";
  };
  const firstHref = (root, selectors) => {
    for (const selector of selectors) {
      const node = root.querySelector(selector);
      if (node?.href) return node.href;
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
    for (const anchor of root.querySelectorAll("a[href]")) {
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

  const detailRecord = () => {
    const root = document.querySelector(
      ".jobs-search__job-details--container, .job-view-layout, .jobs-details, main"
    ) || document;
    const jobUrl = canonicalJobUrl(location.href)
      || canonicalJobUrl(firstHref(root, ["a[href*='/jobs/view/']"]));
    const linkedinCompanyUrl = canonicalCompanyUrl(firstHref(root, [
      ".job-details-jobs-unified-top-card__company-name a[href*='/company/']",
      ".jobs-unified-top-card__company-name a[href*='/company/']",
      "a[href*='/company/']"
    ]));
    return {
      linkedin_job_url: jobUrl,
      external_apply_url: externalApplyUrl(root) || null,
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
      source: "linkedin_browser_extension"
    };
  };

  const cardRecords = () => {
    const cards = document.querySelectorAll(
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
