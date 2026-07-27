# v246 LinkedIn-Official Homepage Alias - Phase C

## Result

The three-company resolver cluster is closed.

The resolver now removes a parent/group rejection only when the requested
company name, LinkedIn company slug and a fetched homepage organization
identity or leading title reduce to the same bounded alias token set. The
candidate must be LinkedIn's declared official website.

The rule does not use edit distance, substring matching, company names or
domains. County remains an identity token. Institutional descriptors are
removed only when at least two brand tokens remain, except the bounded
`Brands` suffix.

## Focused Live

Final artifacts: `/private/tmp/v246-alias-focused-run2`

| Company | Website | Downstream result |
| --- | --- | --- |
| Yum! Brands | `www.yum.com` | official `jobs.yum.com` Job List; opening discovery incomplete |
| County of Maui | `mauicounty.gov` | official Website; later company-budget terminal |
| Duke University Health System | `dukehealth.org` | Phenom Job List and S7 Exact opening |

Duke selects `Physical Therapist - Sports Medicine` in Durham, North Carolina
on tenant `DUHDUHUS`. The canonical opening is
`https://careers.dukehealth.org/us/en/job/271221/physical-therapist-sports-medicine`.

No unsupported opening URL is published for Yum or Maui.

## Replay And Gates

- replay: 2 reproduced, 1 budget recovery;
- mismatch: 0;
- fixture gap: 0;
- relevant tests: 367 passed;
- provider benchmark: 25/25;
- resolver benchmark: 6/6;
- architecture validation: 46 adapters, 0 issues;
- `git diff --check`: clean.

Tata Technologies, Bosch Home, Google DeepMind and the single-token
University-of-Rochester control remain rejected.

## Scope

This result belongs to a development-only diagnostic cohort and does not change
Fresh100 aggregate metrics. The extension, coordinator-v2, LLM path and sealed
holdouts are unchanged.
