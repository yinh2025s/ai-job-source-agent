# v253 Workable Numeric Embed - Phase A

## Hypothesis

Some first-party Career pages expose the official Workable widget and a numeric
`whr_embed(<account_id>)` declaration but no canonical Workable board anchor.
The current backend verifies the Career page but cannot derive and validate the
tenant, so S5 never publishes a verified Job List.

This is a provider contract hypothesis, not a company or account-ID mapping.

## Recovery Cohort

Three independent current employers satisfy the same page trigger:

1. American Battery Technology Company: `whr_embed(708590)`;
2. ClassWallet: `whr_embed(564001)`;
3. Mention Me: `whr_embed(149632)`.

American Battery and ClassWallet are existing development recovery cases.
Mention Me is the third candidate. Its frozen public LinkedIn input is:

- path: `/private/tmp/mention-me-workable-input.json`;
- SHA-256:
  `2ee589c37db3b7c8951e2bcce1646f2bb609afd7802893e26df5b1a05053ac51`;
- job ID: `4361082047`;
- title: Product Growth Marketing Manager;
- location: London, England, United Kingdom;
- no Website, Career or provider URL is prefilled.

The first-party Mention Me Career HTML declares `whr_embed(149632)` and no
direct `apply.workable.com` anchor. A current official Workable opening exists,
but its tenant and URL are acceptance evidence only and must not become a
production lookup table.

## Baseline Gate

Frozen `.251` must reproduce for Mention Me:

- correct Website and first-party Career page;
- numeric Workable widget declaration captured from that page;
- no stronger direct Workable board handoff;
- no verified Job List or Exact through the numeric-only path.

If Mention Me succeeds through an existing stronger route or the widget is not
current, reject the cluster before implementation.

## Proposed Contract

An implementation is authorized only if the baseline reproduces. It must:

1. recognize a numeric widget only when the fetched first-party Career page
   also loads a Workable-owned embed asset;
2. derive tenant evidence through a bounded public Workable-owned contract,
   never from a company/account-ID table or fuzzy slug guess;
3. require one unambiguous canonical Workable tenant and board;
4. feed the candidate through the existing Provider Registry, hiring
   relationship validation, complete inventory matching and S7;
5. reject conflicting tenants, malformed numeric IDs, redirects outside
   Workable, login/private surfaces and incomplete evidence;
6. preserve stronger direct provider handoffs as higher-authority evidence.

## Focused Acceptance

- 3/3 recovery cases reach verified Workable Job Lists and S7 Exact openings;
- ESR Group, Symmetrio and iClassPro positive controls do not regress or change
  provider identity;
- wrong URL, wrong company, wrong location and cross-tenant publication: 0;
- focused same-version replay: 3/3 with no fixture gap, tape divergence or
  missing boundary;
- no company name, domain, account ID, tenant or job ID in production code.
