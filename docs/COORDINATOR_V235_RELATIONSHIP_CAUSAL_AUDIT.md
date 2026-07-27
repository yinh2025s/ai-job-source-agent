# Coordinator `.235` relationship causal audit

## Conclusion

WalkMe, OneApp, STRIKE and Focus do not share a provider-relationship extractor
defect:

- WalkMe's verified first-party careers list contains same-site job details.
  Its remaining problem is opening traversal, not authorization of a guessed
  Lever tenant.
- OneApp's verified Career page links a Pinpoint register-interest page. Its
  guessed Ashby tenant remains unauthorized; the issue is candidate portfolio
  use, not missing relationship evidence.
- STRIKE has a first-party `strikeusa.com -> strike.applytojob.com` handoff.
  A later route selected the unrelated `strike.com` identity and Greenhouse
  probe, so the root cause is same-name Website identity conflict.
- Focus has no first-party Website/Career handoff or provider-published
  employer evidence. Its Ashby probe belongs to another employer and must
  remain rejected.

These are four different causal paths. No relationship gate is relaxed.
Future provider authorization still requires an observed ATS link from a
verified first-party snapshot or provider-owned employer evidence; tenant,
company, domain and title similarity remain insufficient.
