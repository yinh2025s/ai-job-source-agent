# Fresh 100 `.196` Legal Form Phase C

## Scope And Reclassification

The former eight-record `G-core/legal shortening` cluster shared only an S2
symptom. A frozen trace/code audit split it into four executable causes:

| Cause | Records |
| --- | --- |
| Missing legal-form vocabulary | 021, 032 |
| Structured display/tagline and terminal slug delimiter | 001 |
| Source-backed descriptive brand-tail shortening | 003, 017, 025, 046 |
| Unconfirmed ground truth | 019 |

`.196` tests only records 021/032. It does not change descriptive-tail handling,
guess extra domains, increase verification slots, or claim a target for Cretex.

## Contract

The LinkedIn company slug is the source. Only a terminal whole-token legal form
may be removed. `Limited` and `LLP` are added consistently to:

- display-name tokenization;
- exact company identity tokens;
- LinkedIn slug suffix normalization.

Terminal delimiter noise is removed before suffix matching. Embedded words,
legal terms at the beginning, and legal terms followed by another token are not
stripped. Candidate verification remains bounded at three slots, and the
homepage must still pass the ordinary content/canonical identity gate.

## Frozen Source And Offline Gates

Live source: base commit `a17cdcc91c828eed43f7d78ddfedd6fe3aedcf1a`
plus dirty source diff SHA-256
`4160273867c63d659e5a0bb19464ec5c01d65e33d3b54ba117f2a0c8648991e0`.
No implementation file changed during live or replay.

| Gate | Result |
| --- | --- |
| Website resolver suite | 136/136 |
| Full test suite | 2509 passed, 4 skipped |
| Provider benchmark | 25/25 |
| Resolver benchmark | 6/6 |
| Architecture gate | 46 adapters, 0 issues |
| Compile / diff check | Passed |

## Isolated Live And Replay

Root: `/private/tmp/fresh100-v196-legal-form-20260720-run1`

The run used new checkpoint, completion, evidence, snapshot, replay, and output
stores. It was serialized with `verify_limit=3` and the same bounded `.195`
network/discovery policy.

| Record | Candidate generation | Website/Career/Job List | Terminal result |
| --- | --- | --- | --- |
| 021 FOTOMILL STUDIOS LIMITED | `fotomillstudios.com` produced and requested from LinkedIn slug | 0 / 0 / 0 | `FETCH_FAILED` |
| 032 Dechert LLP | `dechert.com` produced and requested from LinkedIn slug | 1 / 1 / 1 | `OPENING_NOT_FOUND` |

Candidate generation passed 2/2. End-to-end system-gap removal passed only 1/2,
so the two-record endpoint cluster is not declared closed.

Dechert selected `https://www.dechert.com`, followed the verified Career route
to `https://www.dechert.com/careers.html`, read a complete current first-party
inventory, and found no `UX Designer`. Its S7 hiring/provider chain is verified;
the no-match is authoritative rather than a discovery failure.

FotoMill allocated the correct compact `.com` candidate in the fast wave and
requested it. Python received `SSL: UNEXPECTED_EOF_WHILE_READING`; curl HTTPS
also failed with SSL exit 35 for apex and `www`. Plain HTTP reached only a small
2015 meta-refresh shell. The project does not globally downgrade verified HTTPS
identity because of one stale or misconfigured endpoint.

Same-version replay reproduced 2/2 outcomes with 0 mismatch, 0 fixture gap, and
passed record integrity.

## Decision

The legal-form candidate-generation defect is fixed 2/2, but only Dechert exits
the system-gap ledger. FotoMill moves from `G-legal-form normalization` to a
separate `T-TLS` cause. After `.195` and `.196`, 42 records remain open:
`T=11`, `B=0`, `S=1`, `G=26`, and `I=4`.
