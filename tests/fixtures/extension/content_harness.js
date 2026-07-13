const fs = require("node:fs");
const vm = require("node:vm");

class FakeElement {
  constructor({
    text = "", href = "", attrs = {}, style = {}, hidden = false, disabled = false, parent = null,
  } = {}) {
    this.textContent = text;
    this.href = href;
    this.attrs = attrs;
    this.style = { display: "block", visibility: "visible", ...style };
    this.hidden = hidden;
    this.disabled = disabled;
    this.parentElement = parent;
    this.matchesBySelector = new Map();
  }

  setMatches(selector, nodes) {
    this.matchesBySelector.set(selector, nodes);
    return this;
  }

  querySelectorAll(selector) {
    return this.matchesBySelector.get(selector) || [];
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
}

const CARD_SELECTOR = (
  "li.jobs-search-results__list-item, [data-occludable-job-id], .job-card-container, .base-card"
);
const DETAIL_SELECTOR = (
  ".jobs-search__job-details--container, .job-view-layout, .jobs-details, main"
);
const NATIVE_APPLY_SELECTOR = [
  "button.jobs-apply-button",
  "button[data-control-name='jobdetails_topcard_inapply']",
  "button[data-live-test-job-apply-button]",
  "button[aria-label*='Easy Apply']",
].join(", ");
const CLOSED_BANNER_SELECTOR = [
  ".jobs-details-top-card__apply-error",
  ".jobs-unified-top-card__closed-job",
  "[data-job-closed='true']",
].join(", ");

function leaf(properties, parent) {
  return new FakeElement({ ...properties, parent });
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
  document.setMatches(DETAIL_SELECTOR, []);
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
  document.setMatches(DETAIL_SELECTOR, []);
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
  root.setMatches("a[href]", [hiddenApply, linkedInApply, visibleApply]);
  document.setMatches(CARD_SELECTOR, []);
  document.setMatches(DETAIL_SELECTOR, [hiddenRoot, root]);
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
    root.setMatches("a[href]", [leaf({
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
  document.setMatches(DETAIL_SELECTOR, [root]);
  return { document, href: "https://www.linkedin.com/jobs/view/evidence-engineer-808/?trk=test" };
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
};

const contentPath = process.argv[2];
const scenarioName = process.argv[3];
const scenario = scenarios[scenarioName]?.();
if (!scenario) throw new Error(`Unknown scenario: ${scenarioName}`);

let listener;
const sandbox = {
  URL,
  document: scenario.document,
  location: { href: scenario.href },
  getComputedStyle: (node) => node.style,
  chrome: {
    runtime: {
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

let response;
listener({ type: "collect_job_source_records" }, {}, (value) => {
  response = value;
});
process.stdout.write(JSON.stringify(response));
