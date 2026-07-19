# Observed 40-Company Exact-13 Failure Checklist

This checklist is bound to the serialized observed regression executed on
2026-07-16. It is not a new blind measurement and must not be used to rewrite
the original blind-holdout result.

## Frozen Run Summary

- Cohort: 40 companies
- Website: 35/40
- Career page: 26/40
- Job list: 20/40
- Exact opening: 13/40
- Records requiring manual review below: 27
- Captured results: `/private/tmp/observed40-career91-results.json`
- Captured trace: `/private/tmp/observed40-career91-trace.json`
- Captured summary: `/private/tmp/observed40-career91-summary.json`

The category on each record is the stage `reason_code` from that run. Later
targeted recoveries are labeled separately and do not change the frozen 13/40
aggregate.

## Fetch Budget Exhausted (6)

- [x] **Resonate AI** - Software Engineer ($125k-$200k, 0.2% to 1%); San Francisco, CA. [LinkedIn](https://www.linkedin.com/jobs/view/software-engineer-%24125k-%24200k-0-2%25-to-1%25-at-resonate-ai-4436043581) | [Website](https://www.resonateapp.com/)
  - Automated evidence: S2 verified the website; S4 exhausted all 32 transport calls without a verified Career or ATS destination.
  - Later investigation: the homepage labels `/resources` as Careers, but that route is also used for Events and News and does not verify a public hiring destination.
  - Manual finding: 官网没有 Career page，不属于系统遗漏。
  - Manual disposition: `no_public_opening`
- [x] **Incredible Health** - Registered Nurse - Pediatric Ambulatory Care; Atlanta, GA. [LinkedIn](https://www.linkedin.com/jobs/view/registered-nurse-pediatric-ambulatory-care-at-incredible-health-4440558383) | [Website](http://www.incrediblehealth.com)
  - Automated evidence: website verified; S4 transport budget exhausted before a Career page was verified.
  - Manual finding: LinkedIn 显示 `No longer accepting applications`。
  - Manual disposition: `verified_closed`
- [x] **Atrium Health** - Registered Nurse (RN) Weekender - LDRP; Monroe, NC. [LinkedIn](https://www.linkedin.com/jobs/view/registered-nurse-rn-weekender-ldrp-at-atrium-health-4438801210) | [Website](https://atriumhealth.org)
  - Automated evidence: website verified; S4 transport budget exhausted before a Career page was verified.
  - Manual finding: 必须先创建账户；产品应向用户明确说明这种外部限制。
  - Manual disposition: `external_blocked`
- [x] **Vetted Solutions** - Registered Nurse - Full Time; Irving, TX. [LinkedIn](https://www.linkedin.com/jobs/view/registered-nurse-full-time-at-vetted-solutions-4439784701) | [Website](https://www.vettedsolutions.com)
  - Automated evidence: website verified; S4 transport budget exhausted before a Career page was verified.
  - Manual finding: LinkedIn 显示 `No longer accepting applications`。
  - Manual disposition: `verified_closed`
- [x] **Cassidy** - Account Executive; New York, NY. [LinkedIn](https://www.linkedin.com/jobs/view/account-executive-at-cassidy-4431720375) | [Website](https://www.cassidyai.com/)
  - Automated evidence: website verified; S4 transport budget exhausted before a Career page was verified.
  - Manual finding: 官网没有独立申请渠道，只能在 LinkedIn 申请。
  - Manual disposition: `no_public_opening`
- [x] **SpaceX** - Financial Analyst; Hawthorne, CA. [LinkedIn](https://www.linkedin.com/jobs/view/financial-analyst-at-spacex-4408086850) | [Website](https://www.spacex.com/)
  - Automated evidence: website verified; S4 transport budget exhausted before a Career page was verified.
  - Manual finding: 官网正确；进入 Career page 后搜索职位名称即可找到，属于系统没有继续执行的缺口。
  - Manual disposition: `system_gap`

## Job Board Not Found (6)

- [x] **Solace** - Registered Nurse Healthcare Advocate (Remote 1099) - RN; United States. [LinkedIn](https://www.linkedin.com/jobs/view/registered-nurse-healthcare-advocate-remote-1099-rn-at-solace-4430828700) | [Website](https://solace.com/) | [Career](https://solace.com/careers/)
  - Automated evidence: the selected same-name website/Career surface did not yield a verified Job Board.
  - Review warning: `solace.com` appears to be a technology company; restoring the old result could create a wrong-company exact.
  - Manual finding: 系统选择了错误的同名公司网站。
  - Manual disposition: `identity_rejected`
- [x] **Aarris Healthcare** - (9A) RN (Registered Nurse) Per Diem; California, United States. [LinkedIn](https://www.linkedin.com/jobs/view/9a-rn-registered-nurse-per-diem-at-aarris-healthcare-4437952329) | [Career](https://aarrishealthcare.com/careers/)
  - Automated evidence: the Career page exposed ApplicantStack detail links, but this frozen run did not yet support ApplicantStack.
  - Later targeted recovery: verified [ApplicantStack board](https://aarris.applicantstack.com/x/openings) and [exact opening](https://aarris.applicantstack.com/x/detail/a27xztr5mziq).
  - Manual finding: 目标职位已经在页面上，历史运行没有进入 detail；属于系统缺口，后续 ApplicantStack adapter 已恢复 exact。
  - Manual disposition: `system_gap` (fixed in targeted live)
- [x] **Focus Health Network** - RN Registered Nurse - Offering $4000 sign on bonus; Royersford, PA. [LinkedIn](https://www.linkedin.com/jobs/view/rn-registered-nurse-offering-%244000-sign-on-bonus-at-focus-health-network-4437923724) | [Career](https://focushealthnet.com/careers/)
  - Automated evidence: Career page verified; no public, provider-verifiable Job Board was found. The visible flow points toward Teams/contact rather than public inventory.
  - Manual finding: 需要进入 Teams 才能查看职位，不是公开匿名 inventory。
  - Manual disposition: `external_blocked`
- [x] **L'OCCITANE Group (B Corp)** - Key Account Manager - Sephora; New York, NY. [LinkedIn](https://www.linkedin.com/jobs/view/key-account-manager-%E2%80%93-sephora-at-l-occitane-group-b-corp-4439870928) | [Career](https://careers.loccitane.com/)
  - Automated evidence: Career surface verified; a Sol de Janeiro Greenhouse lead lacked sufficient parent/brand hiring-relationship evidence and was rejected.
  - Manual finding: 没有公开 Job List，只能在 LinkedIn 申请。
  - Manual disposition: `no_public_opening`
- [x] **Lacoste** - Account Executive; New York, NY. [LinkedIn](https://www.linkedin.com/jobs/view/account-executive-at-lacoste-4411244008) | [Career](https://careers.lacoste.com/en)
  - Automated evidence: Career page verified; no supported and identity-verified Job Board was established.
  - Manual finding: 点击 `Job Offers` 后可搜索目标岗位，属于系统没有继续执行的缺口。
  - Manual disposition: `system_gap`
- [x] **Hoxton Circle** - FP&A - Financial Business Analyst; Melville, NY. [LinkedIn](https://www.linkedin.com/jobs/view/fp-a-financial-business-analyst-at-hoxton-circle-4429467989) | [Career](https://www.hoxtoncircle.com/find-your-career)
  - Automated evidence: Career page verified; the public flow appears to collect resumes for recruiter follow-up rather than expose a public Job Board.
  - Manual finding: 必须先上传简历，无法匿名浏览岗位。
  - Manual disposition: `no_public_opening`

## Website Not Resolved (5)

- [x] **Texas Children's Hospital** - Registered Nurse (RN) - LDRP; Austin, TX. [LinkedIn](https://www.linkedin.com/jobs/view/registered-nurse-rn-ldrp-at-texas-children-s-hospital-4439315467)
  - Automated evidence: S2 did not verify a company website strongly enough to start the hiring identity chain.
  - Manual website / finding: 官方 Oracle opening 存在：[job 425798](https://eohh.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX/job/425798?utm_medium=jobshare)。系统未恢复已知官网/ATS 身份链。
  - Manual disposition: `system_gap`
- [ ] **PUMA Group** - Account Executive (Remote - Midwest Chicago); Aurora, CO. [LinkedIn](https://www.linkedin.com/jobs/view/account-executive-remote-midwest-chicago-at-puma-group-4400268406)
  - Automated evidence: S2 did not verify a region-compatible official website.
  - Manual website / finding:
- [ ] **Parfums Christian Dior** - Account Executive, Miami North; Fort Lauderdale, FL. [LinkedIn](https://www.linkedin.com/jobs/view/account-executive-miami-north-at-parfums-christian-dior-4428680533)
  - Automated evidence: S2 did not verify a company website; live attempts included external network/rate-limit instability.
  - Manual website / finding:
- [ ] **Electrolux Group** - Manufacturing Engineer; Kinston, NC. [LinkedIn](https://www.linkedin.com/jobs/view/manufacturing-engineer-at-electrolux-group-4422447041)
  - Automated evidence: S2 did not verify a company website strongly enough to continue.
  - Manual website / finding:
- [ ] **Gucci** - GUCCI Financial Analyst; New York, NY. [LinkedIn](https://www.linkedin.com/jobs/view/gucci-financial-analyst-at-gucci-4432834505)
  - Automated evidence: S2 did not verify a company website strongly enough to continue.
  - Manual website / finding:

## Opening Discovery Incomplete (5)

- [x] **Northwell Health** - Registered Nurse - Ambulatory (OB/GYN); Lake Success, NY. [LinkedIn](https://www.linkedin.com/jobs/view/registered-nurse-ambulatory-ob-gyn-at-northwell-health-4439580063) | [Career](https://jobs.northwell.edu/) | [Job List](https://jobs.northwell.edu/job-search-results/)
  - Automated evidence: official Job List verified; inventory traversal did not establish a complete target-opening result.
  - Later targeted result: official Job List recovered again, but no exact opening was confirmed.
  - Manual finding: Job List 的搜索功能可以找到目标岗位；系统没有完成最后一步。
  - Manual disposition: `system_gap`
- [x] **Gary and Mary West PACE** - Registered Nurse - $5,000.00 sign on bonus; San Marcos, CA. [LinkedIn](https://www.linkedin.com/jobs/view/registered-nurse-%245-000-00-sign-on-bonus-at-gary-and-mary-west-pace-4439515404) | [Job List](https://westpace.isolvedhire.com/jobs/)
  - Automated evidence: iSolved Job List verified; public inventory could not be read completely enough to publish an exact or verified no-match.
  - Manual finding: 目标岗位就在 Job List 首项，系统没有进入 detail。
  - Manual disposition: `system_gap`
- [x] **System One** - Mechanical Design Engineer; York, PA. [LinkedIn](https://www.linkedin.com/jobs/view/mechanical-design-engineer-at-system-one-4438887636) | [Career](https://www.systemone.com/careers/job-search-tips/)
  - Automated evidence: the frozen run stopped on a first-party article whose path looked like a listing and did not follow its explicit Search Jobs portal.
  - Later targeted recovery: verified [Job List](https://jobs.systemone.com/) and [exact opening](https://jobs.systemone.com/job/mechanical-design-engineer-industrial-manufacturing-york-pa-376670/07c3bf4e-7d3a-11f1-9454-02420a6c7775).
  - Manual finding: 人工最初未找到，但后续 targeted live 已验证目标 exact opening，证明冻结运行存在系统缺口且现已修复。
  - Manual disposition: `system_gap` (fixed in targeted live)
- [x] **Avery Dennison** - Mechanical Design Engineer; Azusa, CA. [LinkedIn](https://www.linkedin.com/jobs/view/mechanical-design-engineer-at-avery-dennison-4439889950) | [Job List](https://www.averydennison.com/en/home/careers/search-jobs.html)
  - Automated evidence: Job List surface verified; the scoped SmartRecruiters/public inventory path remained incomplete for this target.
  - Manual finding: Job List 正确，但系统没有进入目标 detail，属于尚未彻底解决的最后一步。
  - Manual disposition: `system_gap`
- [x] **Ellaway Blues Consulting** - Senior Manufacturing Engineer; Dallas-Fort Worth Metroplex. [LinkedIn](https://www.linkedin.com/jobs/view/senior-manufacturing-engineer-at-ellaway-blues-consulting-4431243756) | [Job List](https://www.ellawayblues.com/jobs)
  - Automated evidence: first-party jobs surface verified; its Wix Cloud Data inventory transport is unsupported/incomplete.
  - Manual finding: 人工也无法搜到目标岗位。
  - Manual disposition: `eligibility_unknown`

## Career Page Not Found (3)

- [x] **Hugh Chatham Health** - Registered Nurse (RN) Weekender - LDRP; Monroe, NC. [LinkedIn](https://www.linkedin.com/jobs/view/registered-nurse-rn-weekender-ldrp-at-hugh-chatham-health-4439383470) | [Website](http://www.hughchatham.org)
  - Automated evidence: website resolved; no Career page passed verification.
  - Manual Career URL / finding: 官网无法打开，但 Workday 上存在目标岗位；系统仍应通过 ATS 候选路径恢复。
  - Later identity audit: the public Workday target belongs to Atrium Health; no first-party handoff, parent relationship, or provider-tenant evidence connects Hugh Chatham Health to Atrium.
  - Manual disposition: `eligibility_unknown` (reclassified; do not force exact)
- [x] **Roper St. Francis Healthcare** - Registered Nurse (RN) - Full Time; Hayward, CA. [LinkedIn](https://www.linkedin.com/jobs/view/registered-nurse-rn-full-time-at-roper-st-francis-healthcare-4436643921) | [Website](https://www.rsfh.com)
  - Automated evidence: website resolved; no Career page passed verification.
  - Manual Career URL / finding: 官网同样无法打开，当前证据不足以判断是否存在公开目标 opening。
  - Manual disposition: `external_blocked`
- [x] **Community Health Center of Snohomish County (CHC)** - Registered Nurse - Clinical; Lynnwood, WA. [LinkedIn](https://www.linkedin.com/jobs/view/registered-nurse-clinical-at-community-health-center-of-snohomish-county-chc-4439063616) | [Website](https://www.chcsno.org/) | [Career](https://www.chcsno.org/careers/)
  - Automated evidence: the frozen run lost valid homepage navigation evidence across apex/`www` normalization.
  - Later targeted recovery: verified UltiPro board and [exact opening](https://recruiting2.ultipro.com/COM1101CMHS/JobBoard/8de41890-2fe6-4347-b1ad-f3043de88a1a/OpportunityDetail?opportunityId=7905b0d4-e183-4125-b3f6-fc6044809d7d).
  - Manual finding: `Open Positions` 后即可进入 Job List；冻结运行存在系统缺口，后续已恢复 exact。
  - Manual disposition: `system_gap` (fixed in targeted live)

## External Network / Bot Protection (2)

- [x] **Lilly Pulitzer** - Account Executive; Greater Philadelphia. [LinkedIn](https://www.linkedin.com/jobs/view/account-executive-at-lilly-pulitzer-4429546241) | [Career](https://www.lillypulitzer.com/content-pages/dream-jobs-october-update.html) | [Workday](https://oxford.wd5.myworkdayjobs.com/LillyPulitzer)
  - Automated evidence: official Workday board reached; opening inventory ended with `NETWORK_TIMEOUT`.
  - Manual finding: LinkedIn 显示 `No longer accepting applications`。
  - Manual disposition: `verified_closed`
- [x] **Tata Technologies** - Mechanical Engineer (Rotating component design); Indianapolis, IN. [LinkedIn](https://www.linkedin.com/jobs/view/mechanical-engineer-rotating-component-design-at-tata-technologies-4432858143) | [Career](https://www.tata.com/careers/jobs) | [Job List](https://www.tata.com/careers/jobs/joblisting)
  - Automated evidence: official Job List reached; opening inventory ended with `BOT_PROTECTION`.
  - Manual finding: 人工检查也未找到目标岗位。
  - Manual disposition: `no_public_opening`

## Review Totals

- Fetch Budget Exhausted: 6
- Job Board Not Found: 6
- Website Not Resolved: 5
- Opening Discovery Incomplete: 5
- Career Page Not Found: 3
- External Network / Bot Protection: 2
- Total: 27

## Manual Review Progress

- Reviewed: 23/27
- Pending manual review: 4/27 (`PUMA Group`, `Parfums Christian Dior`,
  `Electrolux Group`, and `Gucci`)
- `system_gap`: 9
- `verified_closed`: 3
- `no_public_opening`: 5
- `external_blocked`: 3
- `identity_rejected`: 1
- `eligibility_unknown`: 2

All nine confirmed system gaps now recover exact openings in targeted or final
live runs: Aarris Healthcare, System One, Community Health Center of Snohomish
County, SpaceX, Lacoste, Texas Children's Hospital, Northwell Health, Gary and
Mary West PACE, and Avery Dennison. Hugh Chatham Health is no longer in the
eligible queue after the identity audit above. These later dispositions and
recoveries do not rewrite the frozen 13/40 aggregate.

## Manual Disposition Options

Use one of these labels in each `Manual finding` field:

- `system_gap`: a public official route/opening exists and the system missed it.
- `external_blocked`: login, bot protection, rate limit, or network state prevents verification.
- `verified_closed`: the LinkedIn posting or official opening is no longer open.
- `no_public_opening`: the company has no public inventory or uses a private contact flow.
- `recruiter_client_undisclosed`: the publisher is a recruiter and the hiring client is not disclosed.
- `identity_rejected`: the apparent website/board belongs to a different company or tenant.
- `eligibility_unknown`: available evidence is insufficient to decide.
