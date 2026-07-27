# v239 Local SearXNG Runtime - Phase C

## Frozen Runs

The 12-record development slice was frozen at:

```text
sha256 4a50f29c3d4ea2b6d6fd7a3602c3121dab7e36f9ad4663e4b142ac056e613490
12 records / 11 companies
```

Both valid runs used `.239`, one worker, no resume, equal pipeline budgets and
separate checkpoint, completion, snapshot, replay and output roots.

The first SearXNG attempt is invalid as recall evidence. Python's default
`urllib` opener inherited the macOS system proxy and sent loopback requests
through the proxy, producing HTTP 502 for every application search request even
though direct `curl` queries succeeded. The generic transport repair now gives
literal `localhost` and loopback IP URLs a separate cookie session with an
explicit no-proxy opener. Public and non-loopback URLs retain the existing
system-proxy behavior.

## Paired Result

| Metric | Legacy | SearXNG |
| --- | ---: | ---: |
| Raw `job_list_page_url` fields | 1/12 | 7/12 |
| S7 Exact | 1/12 | 0/12 |
| Full replay | 12/12 | 12/12 |
| Elapsed | 402.9 s | 360.4 s |

SearXNG moved Caesars, both Hawaiian Electric records, CHAMP, Fabric and
Prophetic from no board field to a candidate board field. These are candidate
production gains, not six verified recoveries. Every SearXNG board remained
outside a complete verified identity chain:

- iClassPro, Caesars, CHAMP, Fabric and Prophetic retained
  `COMPANY_IDENTITY_AMBIGUOUS`;
- Hawaiian Electric reached a generic SuccessFactors host but stopped at
  `PROVIDER_VARIANT_UNSUPPORTED`;
- CHAMP exposed an unrelated Greenhouse board as a partial candidate, proving
  that a populated partial field cannot be counted as a correct result.

The final identity gate published no incorrect Exact URL, cross-company Exact
or cross-tenant Exact. However, the unrelated CHAMP partial URL is a product
safety debt and must not be rendered as a verified Job List.

Legacy retained the existing iClassPro Paylocity Exact while the SearXNG run
initially lost it. The successful homepage response caused the LinkedIn display
descriptor `- Class Management Software` to be treated as part of the legal
brand, which falsely classified the official iClassPro homepage as a parent
site. That regression is addressed separately in `.240`.

## Decision

The local runtime and SearchBackend transport contract are accepted. SearXNG is
still optional and is not the default.

The 12-record causal cluster is **not closed**:

- raw candidate production improved in a batch;
- independently verified S7 recovery was zero;
- one legacy Exact regressed before the `.240` guard;
- one unrelated partial board was retained.

The next backend work must preserve strong first-party candidates, suppress
unverified partial board publication and convert the newly produced provider
candidates into verified provider/tenant/hiring relationships. A larger live
cohort is not justified until those contracts are addressed.
