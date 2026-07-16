const fs = require("node:fs");
const vm = require("node:vm");

class FakeElement {
  constructor({
    text = "", href = "", attrs = {}, style = {}, hidden = false, disabled = false, parent = null,
    tagName = "DIV",
  } = {}) {
    this.textContent = text;
    this.href = href;
    this.attrs = attrs;
    this.style = { display: "block", visibility: "visible", ...style };
    this.hidden = hidden;
    this.disabled = disabled;
    this.parentElement = parent;
    this.tagName = tagName;
    this.children = [];
    this.matchesBySelector = new Map();
    this.onClick = null;
    if (parent) parent.children.push(this);
  }

  setMatches(selector, nodes) {
    this.matchesBySelector.set(selector, nodes);
    return this;
  }

  querySelectorAll(selector) {
    return this.matchesBySelector.get(selector) || [];
  }

  querySelector(selector) {
    return this.querySelectorAll(selector)[0] || null;
  }

  contains(node) {
    for (let current = node; current; current = current.parentElement) {
      if (current === this) return true;
    }
    return false;
  }

  getAttribute(name) {
    return Object.prototype.hasOwnProperty.call(this.attrs, name) ? this.attrs[name] : null;
  }

  hasAttribute(name) {
    return Object.prototype.hasOwnProperty.call(this.attrs, name);
  }

  getBoundingClientRect() {
    return { top: 100000, bottom: 100100, left: 0, right: 100 };
  }

  click() {
    if (this.onClick) this.onClick();
  }
}

const CARD_SELECTOR = (
  "li.jobs-search-results__list-item, [data-occludable-job-id], .job-card-container, .base-card"
);
const PAGE_CARD_SELECTOR = "[data-testid='lazy-column'] [role='button'][tabindex='0']";
const DETAIL_ROOT_SELECTORS = [
  ".jobs-search__job-details--container",
  ".job-view-layout",
  ".jobs-details",
  "main",
];
const EXTERNAL_APPLY_SELECTOR = "a[data-control-name='jobdetails_topcard_external_apply']";
const NATIVE_APPLY_SELECTOR = [
  "button.jobs-apply-button",
  "button[data-control-name='jobdetails_topcard_inapply']",
  "button[data-live-test-job-apply-button]",
  "button[aria-label*='Easy Apply']",
  "button[aria-label^='Apply']",
].join(", ");
const CLOSED_BANNER_SELECTOR = [
  ".jobs-details-top-card__apply-error",
  ".jobs-unified-top-card__closed-job",
  "[data-job-closed='true']",
].join(", ");

function leaf(properties, parent) {
  return new FakeElement({ ...properties, parent });
}

function setDetailRoots(document, rootsBySelector) {
  for (const selector of DETAIL_ROOT_SELECTORS) {
    document.setMatches(selector, rootsBySelector[selector] || []);
  }
}

function card(id, company, title, properties = {}) {
  const root = new FakeElement(properties);
  const jobLink = leaf({
    text: title,
    href: `https://www.linkedin.com/jobs/view/example-${id}?tracking=ignored`,
  }, root);
  const companyLink = leaf({
    text: company,
    href: `https://www.linkedin.com/company/company-${id}/about/`,
  }, root);
  root.setMatches("a.job-card-list__title--link", [jobLink]);
  root.setMatches(".job-card-list__title--link", [jobLink]);
  root.setMatches("a[href*='/company/']", [companyLink]);
  root.setMatches(".job-card-container__primary-description", [companyLink]);
  return root;
}

function detailRoot(id, company, title, properties = {}) {
  const root = new FakeElement({ attrs: { "data-job-id": String(id), ...properties.attrs } });
  const companyNode = leaf({ text: company }, root);
  const companyLink = leaf({
    text: company,
    href: `https://www.linkedin.com/company/${company.toLowerCase().replace(/\s+/g, "-")}/`,
  }, root);
  const titleNode = leaf({ text: title }, root);
  const location = leaf({ text: properties.location || "Remote" }, root);
  root.setMatches(".job-details-jobs-unified-top-card__company-name", [companyNode]);
  root.setMatches(
    ".job-details-jobs-unified-top-card__company-name a[href*='/company/']",
    [companyLink],
  );
  root.setMatches(".job-details-jobs-unified-top-card__job-title h1", [titleNode]);
  root.setMatches(
    ".job-details-jobs-unified-top-card__primary-description-container .tvm__text",
    [location],
  );
  return root;
}

function hiddenCardsScenario() {
  const document = new FakeElement();
  const collapsedParent = new FakeElement({ style: { visibility: "collapse" } });
  const cards = [
    card(101, "Hidden Attribute", "Hidden Role", { hidden: true }),
    card(102, "Aria Hidden", "Hidden Role", { attrs: { "aria-hidden": "true" } }),
    card(103, "Display None", "Hidden Role", { style: { display: "none" } }),
    card(104, "Visibility Hidden", "Hidden Role", { style: { visibility: "hidden" } }),
    card(105, "Collapsed Ancestor", "Hidden Role", { parent: collapsedParent }),
    card(106, "Visible Offscreen", "Remote AI Engineer"),
  ];
  document.setMatches(CARD_SELECTOR, cards);
  setDetailRoots(document, {});
  return { document, href: "https://www.linkedin.com/jobs/search/" };
}

function selectorFallbackScenario() {
  const document = new FakeElement();
  const root = new FakeElement();
  const hiddenTitle = leaf({
    text: "Stale Hidden Title",
    href: "https://www.linkedin.com/jobs/view/201",
    style: { visibility: "hidden" },
  }, root);
  const title = leaf({
    text: "Visible Platform Engineer",
    href: "https://www.linkedin.com/jobs/view/platform-engineer-202",
  }, root);
  const hiddenCompany = leaf({ text: "Stale Company", hidden: true }, root);
  const company = leaf({ text: "Visible Systems" }, root);
  const hiddenCompanyLink = leaf({
    text: "Stale Company",
    href: "https://www.linkedin.com/company/stale-company",
    attrs: { "aria-hidden": "true" },
  }, root);
  const companyLink = leaf({
    text: "Visible Systems",
    href: "https://www.linkedin.com/company/visible-systems/jobs/",
  }, root);
  const hiddenLocation = leaf({ text: "Hidden Location", style: { display: "none" } }, root);
  const location = leaf({ text: "Worldwide" }, root);

  root.setMatches("a.job-card-list__title--link", [hiddenTitle, title]);
  root.setMatches(".job-card-list__title--link", [hiddenTitle, title]);
  root.setMatches(".job-card-container__primary-description", [hiddenCompany]);
  root.setMatches(".base-search-card__subtitle", [company]);
  root.setMatches("a[href*='/company/']", [hiddenCompanyLink, companyLink]);
  root.setMatches(".job-card-container__metadata-item", [hiddenLocation]);
  root.setMatches(".job-search-card__location", [location]);
  document.setMatches(CARD_SELECTOR, [root]);
  setDetailRoots(document, {});
  return { document, href: "https://www.linkedin.com/jobs/search/" };
}

function detailScenario() {
  const document = new FakeElement();
  const hiddenRoot = new FakeElement({ style: { display: "none" } });
  const root = new FakeElement();
  const ariaHiddenParent = new FakeElement({ attrs: { "aria-hidden": "true" }, parent: root });
  const hiddenCompany = leaf({ text: "Hidden Detail Company" }, ariaHiddenParent);
  const company = leaf({ text: "Detail Systems" }, root);
  const companyLink = leaf({
    text: "Detail Systems",
    href: "https://www.linkedin.com/company/detail-systems/",
  }, root);
  const hiddenTitle = leaf({ text: "Hidden Detail Role", style: { visibility: "collapse" } }, root);
  const title = leaf({ text: "Staff AI Engineer" }, root);
  const location = leaf({ text: "Shanghai, China" }, root);
  const hiddenApply = leaf({
    text: "Apply now",
    href: "https://hidden.example/jobs/777",
  }, ariaHiddenParent);
  const linkedInApply = leaf({
    text: "Apply",
    href: "https://www.linkedin.com/jobs/view/777/apply/",
  }, root);
  const visibleApply = leaf({
    text: "Apply on company site",
    href: "https://careers.detail.example/jobs/777",
  }, root);

  root.setMatches(".job-details-jobs-unified-top-card__company-name", [hiddenCompany, company]);
  root.setMatches(
    ".job-details-jobs-unified-top-card__company-name a[href*='/company/']",
    [companyLink],
  );
  root.setMatches(".job-details-jobs-unified-top-card__job-title h1", [hiddenTitle]);
  root.setMatches("h1", [title]);
  root.setMatches(
    ".job-details-jobs-unified-top-card__primary-description-container .tvm__text",
    [location],
  );
  root.setMatches(EXTERNAL_APPLY_SELECTOR, [hiddenApply, linkedInApply, visibleApply]);
  document.setMatches(CARD_SELECTOR, []);
  setDetailRoots(document, { ".jobs-search__job-details--container": [hiddenRoot, root] });
  return { document, href: "https://www.linkedin.com/jobs/view/staff-ai-engineer-777/" };
}

function evidenceScenario(kind) {
  const document = new FakeElement();
  const root = new FakeElement();
  const company = leaf({ text: "Evidence Systems" }, root);
  const companyLink = leaf({
    text: "Evidence Systems",
    href: "https://www.linkedin.com/company/evidence-systems/",
  }, root);
  const title = leaf({ text: "Principal Evidence Engineer" }, root);
  const location = leaf({ text: "Remote" }, root);

  root.setMatches(".job-details-jobs-unified-top-card__company-name", [company]);
  root.setMatches(
    ".job-details-jobs-unified-top-card__company-name a[href*='/company/']",
    [companyLink],
  );
  root.setMatches(".job-details-jobs-unified-top-card__job-title h1", [title]);
  root.setMatches(
    ".job-details-jobs-unified-top-card__primary-description-container .tvm__text",
    [location],
  );

  if (kind === "native") {
    root.setMatches(NATIVE_APPLY_SELECTOR, [leaf({
      text: "Easy Apply",
      attrs: { "aria-label": "Easy Apply to Evidence Systems" },
    }, root)]);
  } else if (kind === "external") {
    root.setMatches(EXTERNAL_APPLY_SELECTOR, [leaf({
      text: "Apply on company website",
      href: "https://careers.evidence.example/jobs/808",
    }, root)]);
  } else if (kind === "closed") {
    root.setMatches(CLOSED_BANNER_SELECTOR, [leaf({
      text: "This job is no longer accepting applications",
    }, root)]);
  } else if (kind === "hidden_disabled") {
    const hiddenParent = new FakeElement({ style: { display: "none" }, parent: root });
    root.setMatches(NATIVE_APPLY_SELECTOR, [
      leaf({ text: "Easy Apply" }, hiddenParent),
      leaf({ text: "Easy Apply", attrs: { "aria-disabled": "true" } }, root),
      leaf({ text: "Easy Apply", disabled: true }, root),
    ]);
    root.setMatches("a[href]", [leaf({
      text: "Apply on company website",
      href: "https://careers.evidence.example/jobs/808",
      attrs: { disabled: "" },
    }, root)]);
  }

  document.setMatches(CARD_SELECTOR, []);
  setDetailRoots(document, { ".jobs-search__job-details--container": [root] });
  return { document, href: "https://www.linkedin.com/jobs/view/evidence-engineer-808/?trk=test" };
}

function selectedDetailScenario() {
  const document = new FakeElement();
  const competingRoot = detailRoot(301, "Competing Systems", "Wrong Detail");
  const selectedRoot = detailRoot(300, "Selected Systems", "Selected Detail");
  document.setMatches(CARD_SELECTOR, [card(301, "Competing Systems", "Search Card")]);
  setDetailRoots(document, {
    ".jobs-search__job-details--container": [competingRoot, selectedRoot],
  });
  return { document, href: "https://www.linkedin.com/jobs/search/?currentJobId=300" };
}

function selectorPriorityScenario() {
  const document = new FakeElement();
  const first = detailRoot(402, "Priority First", "First Detail");
  const second = detailRoot(402, "Priority Second", "Second Detail");
  document.setMatches(CARD_SELECTOR, []);
  setDetailRoots(document, {
    ".jobs-search__job-details--container": [first],
    ".job-view-layout": [second],
  });
  return { document, href: "https://www.linkedin.com/jobs/view/402/" };
}

function semanticSearchDetailScenario() {
  const document = new FakeElement();
  const root = new FakeElement();
  const header = new FakeElement({ parent: root });
  const companyBlock = new FakeElement({ parent: header });
  const companyLabel = leaf({
    text: "Microsoft",
    attrs: { "aria-label": "Company, Microsoft." },
  }, companyBlock);
  const companyLink = leaf({
    text: "Microsoft",
    href: "https://www.linkedin.com/company/microsoft/life/",
    tagName: "A",
  }, companyBlock);
  const titleBlock = new FakeElement({ parent: header });
  const titleParagraph = new FakeElement({ parent: titleBlock, tagName: "P" });
  const titleLink = leaf({
    text: "Software Engineer - CTJ - Poly",
    href: "https://www.linkedin.com/jobs/view/4420695497/?trackingId=ignored",
    tagName: "A",
  }, titleParagraph);
  const location = leaf({
    text: "Reston, VA · Reposted 4 days ago · 87 people clicked apply",
    tagName: "P",
  }, header);
  const apply = leaf({
    text: "Apply",
    href: "https://www.linkedin.com/safety/go/?url=https%3A%2F%2Fapply.careers.microsoft.com%2Fcareers%2Fjob%2F1970393556824773%3Futm_source%3Dlinkedin&urlhash=ignored",
    attrs: { "aria-label": "Apply on company website" },
    tagName: "A",
  }, root);

  document.setMatches(CARD_SELECTOR, []);
  document.setMatches("a[href*='/jobs/view/']", [titleLink]);
  header.setMatches("a[href*='/company/']", [companyLink]);
  header.setMatches("[aria-label^='Company, ']", [companyLabel]);
  root.setMatches("a[href*='/company/']", [companyLink]);
  root.setMatches("a, button", [apply]);
  root.setMatches("a[aria-label*='Apply on company website'][href]", [apply]);
  setDetailRoots(document, { main: [root] });
  return {
    document,
    href: "https://www.linkedin.com/jobs/search-results/?currentJobId=4420695497",
  };
}

function unsafeExternalScenario() {
  const document = new FakeElement();
  const root = detailRoot(809, "Unsafe Apply", "Security Engineer");
  root.setMatches(EXTERNAL_APPLY_SELECTOR, [
    leaf({ text: "Apply", href: "javascript:alert(1)" }, root),
    leaf({ text: "Apply", href: "https://user:pass@careers.example/jobs/809" }, root),
    leaf({ text: "Apply", href: "https://careers.example/jobs/809#apply" }, root),
    leaf({ text: "Apply", href: "http://127.0.0.1/jobs/809" }, root),
    leaf({ text: "Apply", href: "https://careers.internal/jobs/809" }, root),
    leaf({ text: "Apply", href: "https://www.linkedin.com.evil/jobs/809" }, root),
    leaf({ text: "Apply", href: "https://careers.example/jobs/809?access_token=secret" }, root),
  ]);
  root.setMatches("a[href]", [leaf({
    text: "Apply in description",
    href: "https://description.example/jobs/809",
  }, root)]);
  document.setMatches(CARD_SELECTOR, []);
  setDetailRoots(document, { ".jobs-search__job-details--container": [root] });
  return { document, href: "https://www.linkedin.com/jobs/view/809/" };
}

function forgedIdentityScenario() {
  const document = new FakeElement();
  const root = new FakeElement();
  const jobLink = leaf({
    text: "Forged Job",
    href: "https://www.linkedin.com.evil/jobs/view/901",
  }, root);
  const companyLink = leaf({
    text: "Forged Company",
    href: "http://www.linkedin.com/company/forged-company",
  }, root);
  root.setMatches("a.job-card-list__title--link", [jobLink]);
  root.setMatches(".job-card-list__title--link", [jobLink]);
  root.setMatches(".job-card-container__primary-description", [companyLink]);
  root.setMatches("a[href*='/company/']", [companyLink]);
  document.setMatches(CARD_SELECTOR, [root]);
  setDetailRoots(document, {});
  return { document, href: "https://www.linkedin.com/jobs/search/" };
}

function emptyScenario(href) {
  const document = new FakeElement();
  document.setMatches(CARD_SELECTOR, []);
  setDetailRoots(document, {});
  return { document, href };
}

function pageCard(id, company, title, location, properties = {}) {
  const root = new FakeElement({
    attrs: { role: "button", tabindex: "0", ...properties.attrs },
    hidden: properties.hidden,
    disabled: properties.disabled,
    style: properties.style,
  });
  const titleParagraph = leaf({
    text: properties.selected ? `Selected, ${title}` : title,
    tagName: "P",
  }, root);
  const titleText = leaf({ text: title, attrs: { "aria-hidden": "true" }, tagName: "SPAN" }, titleParagraph);
  const companyParagraph = leaf({ text: company, tagName: "P" }, root);
  const locationParagraph = leaf({ text: location, tagName: "P" }, root);
  root.setMatches("p", [titleParagraph, companyParagraph, locationParagraph]);
  titleParagraph.setMatches("[aria-hidden='true']", [titleText]);
  titleParagraph.setMatches("span", [titleText]);
  root.pageJobId = String(id);
  return root;
}

function pageFooterControl() {
  const root = new FakeElement({ attrs: { role: "button", tabindex: "0" } });
  const paragraph = leaf({ text: "Show more", tagName: "P" }, root);
  root.setMatches("p", [paragraph]);
  return root;
}

function pageScanScenario({
  ids = [101, 102, 103], startId = "", timeoutIds = [], cancelAfterProgress = 0,
  externalApplyIds = [], detailDelayTicks = 0,
} = {}) {
  const document = new FakeElement();
  const cards = ids.map((id, index) => pageCard(
    id,
    `Company ${id}`,
    `Role ${id}`,
    `Location ${id}`,
    { selected: Boolean(startId && String(id) === String(startId)) },
  ));
  const footerControls = [pageFooterControl(), pageFooterControl()];
  document.setMatches(PAGE_CARD_SELECTOR, [...cards, ...footerControls]);
  document.setMatches(CARD_SELECTOR, []);
  setDetailRoots(document, {});
  let pendingDetail = null;
  const showDetail = (id) => {
    const root = detailRoot(id, `Company ${id}`, `Role ${id}`, { location: `Location ${id}` });
    if (externalApplyIds.map(String).includes(String(id))) {
      root.setMatches(EXTERNAL_APPLY_SELECTOR, [leaf({
        text: "Apply on company website",
        href: `https://careers.example/jobs/${id}`,
      }, root)]);
    }
    setDetailRoots(document, { ".jobs-search__job-details--container": [root] });
  };
  return {
    document,
    href: `https://www.linkedin.com/jobs/search/${startId ? `?currentJobId=${startId}` : ""}`,
    messageType: "collect_job_source_page",
    cancelAfterProgress,
    bindLocation(location) {
      for (const cardNode of cards) {
        cardNode.onClick = () => {
          if (!timeoutIds.map(String).includes(cardNode.pageJobId)) {
            location.href = `https://www.linkedin.com/jobs/search/?currentJobId=${cardNode.pageJobId}`;
            if (detailDelayTicks > 0) {
              pendingDetail = { id: cardNode.pageJobId, remaining: detailDelayTicks };
              setDetailRoots(document, {});
            } else {
              showDetail(cardNode.pageJobId);
            }
          }
        };
      }
    },
    onTimer() {
      if (!pendingDetail) return;
      pendingDetail.remaining -= 1;
      if (pendingDetail.remaining <= 0) {
        showDetail(pendingDetail.id);
        pendingDetail = null;
      }
    },
  };
}

const scenarios = {
  hidden_cards: hiddenCardsScenario,
  selector_fallback: selectorFallbackScenario,
  visible_detail: detailScenario,
  evidence_native: () => evidenceScenario("native"),
  evidence_external: () => evidenceScenario("external"),
  evidence_closed: () => evidenceScenario("closed"),
  evidence_missing: () => evidenceScenario("missing"),
  evidence_hidden_disabled: () => evidenceScenario("hidden_disabled"),
  selected_detail: selectedDetailScenario,
  selector_priority: selectorPriorityScenario,
  semantic_search_detail: semanticSearchDetailScenario,
  unsafe_external: unsafeExternalScenario,
  forged_identity: forgedIdentityScenario,
  empty_jobs: () => emptyScenario("https://www.linkedin.com/jobs/search/"),
  empty_non_jobs: () => emptyScenario("https://www.linkedin.com/feed/"),
  page_success_dedupe: () => pageScanScenario({ ids: [101, 102, 101] }),
  page_timeout: () => pageScanScenario({ ids: [201, 202], timeoutIds: [202] }),
  page_cancel: () => pageScanScenario({ ids: [301, 302, 303], startId: "302", cancelAfterProgress: 1 }),
  page_selected_first: () => pageScanScenario({ ids: [601, 602], startId: "601" }),
  page_max_30: () => pageScanScenario({ ids: Array.from({ length: 35 }, (_, index) => 400 + index) }),
  page_restore: () => pageScanScenario({ ids: [501, 502, 503], startId: "502" }),
  page_delayed_external: () => pageScanScenario({
    ids: [701, 702], externalApplyIds: [701, 702], detailDelayTicks: 2,
  }),
};

const contentPath = process.argv[2];
const scenarioName = process.argv[3];
const scenario = scenarios[scenarioName]?.();
if (!scenario) throw new Error(`Unknown scenario: ${scenarioName}`);

let listener;
let progressCount = 0;
let response;
let cancelResponse;
const sandbox = {
  URL,
  document: scenario.document,
  location: { href: scenario.href },
  getComputedStyle: (node) => node.style,
  setTimeout: (callback) => {
    scenario.onTimer?.();
    Promise.resolve().then(callback);
    return 0;
  },
  chrome: {
    runtime: {
      sendMessage: (message) => {
        if (message?.type === "job_source_page_progress") {
          progressCount += 1;
          if (scenario.cancelAfterProgress && progressCount === scenario.cancelAfterProgress) {
            listener({ type: "cancel_job_source_page" }, {}, (value) => {
              cancelResponse = value;
            });
          }
        }
      },
      onMessage: {
        addListener: (callback) => {
          listener = callback;
        },
      },
    },
  },
};
sandbox.globalThis = sandbox;
vm.runInNewContext(fs.readFileSync(contentPath, "utf8"), sandbox, { filename: contentPath });
scenario.bindLocation?.(sandbox.location);

listener({ type: scenario.messageType || "collect_job_source_records" }, {}, (value) => {
  response = value;
});

async function finish() {
  for (let attempt = 0; attempt < 100; attempt += 1) {
    if (response) {
      process.stdout.write(JSON.stringify({
        ...response,
        progress_count: progressCount,
        cancel_response: cancelResponse,
      }));
      return;
    }
    await new Promise((resolve) => setTimeout(resolve, 0));
  }
  throw new Error("Content script did not respond");
}

finish().catch((error) => {
  process.stderr.write(String(error));
  process.exitCode = 1;
});
