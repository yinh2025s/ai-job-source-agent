# 100-Posting Three-Route Manual Review: 72 Non-Exact Records

Run date: 2026-07-17

只包含三路 OR 没有得到 S7 exact 的 72 条。编号与配套 CSV 的数据行一致。

## 标注规则

每条建议填写：

- `Posting status`: `OPEN` / `CLOSED` / `NOT_PUBLIC` / `UNKNOWN`.
- `Disposition`: `SYSTEM_GAP` / `EXTERNAL_BLOCKED` / `NO_PUBLIC_OPENING` / `VERIFIED_CLOSED` / `RECRUITER_CLIENT_UNDISCLOSED` / `WRONG_LINKEDIN_IDENTITY` / `UNKNOWN`.
- `Failure route`: `EXTERNAL_APPLY` / `PROVIDER_SEARCH` / `WEBSITE_CAREER` / `MULTIPLE`.
- 只有浏览器中确认仍开放的官方具体岗位 URL 才填 Correct opening。

配套可编辑表格：`samples/evaluation/live100_three_route_manual_review_72_20260717.csv`

## 汇总

- 待审核：72
- `JOB_BOARD_NOT_FOUND`: 16
- `PROVIDER_FETCH_FAILED`: 16
- `CAREER_PAGE_NOT_FOUND`: 13
- `OPENING_DISCOVERY_INCOMPLETE`: 8
- `OPENING_NOT_FOUND`: 8
- `RESULT_IDENTITY_MISMATCH`: 3
- `BOT_PROTECTION`: 2
- `WEBSITE_NOT_RESOLVED`: 2
- `COMPANY_TIME_BUDGET_EXHAUSTED`: 1
- `HTTP_FORBIDDEN`: 1
- `NETWORK_TIMEOUT`: 1
- `PROVIDER_VARIANT_UNSUPPORTED`: 1

## Checklist

问题A：进入了career page，有明确的position按钮，只要点进去就可以看到岗位，但是没有点进去

问题B：

问题C：只要再搜索岗位名称就能看到，就差一点，但是没有搜

### JOB_BOARD_NOT_FOUND (16)

- [ ] **8. Redlands Community Hospital** - New Graduate Registered Nurse (RN); Redlands, CA

  - 自动结果：[LinkedIn](https://www.linkedin.com/jobs/view/new-graduate-registered-nurse-rn-at-redlands-community-hospital-4437655656) | [Website](https://www.redlandshospital.org) | [Career](https://www.redlandshospital.org/careers/) | Job list: 未找到
  - 当前分类：pipeline=`partial`; stages=`job_board_discovery:JOB_BOARD_NOT_FOUND`
  - 三路诊断：External Apply=`no_visible_external_apply_link`; ATS Search=`candidate_not_produced`; Website/Career=`candidate_not_produced`
  - Posting status: `___`
  - Correct website: `___`
  - Correct Career: `___`
  - Correct Job list: `___`
  - Correct opening: `___`
  - Disposition: `___`
  - Failure route: `___`
  - Notes: `___`careerpage点[VIEW ALL AVAILABLE JOBS AND APPLY](https://redlandshospital.hcshiring.com/)就可以进去，但是没有点进去，还是老问题，问题A
- [ ] **9. Redlands Community Hospital** - New Graduate Registered Nurse (RN); Redlands, CA

  - 自动结果：[LinkedIn](https://www.linkedin.com/jobs/view/new-graduate-registered-nurse-rn-at-redlands-community-hospital-4437656695) | [Website](https://www.redlandshospital.org) | [Career](https://www.redlandshospital.org/careers/) | Job list: 未找到
  - 当前分类：pipeline=`partial`; stages=`job_board_discovery:JOB_BOARD_NOT_FOUND`
  - 三路诊断：External Apply=`no_visible_external_apply_link`; ATS Search=`candidate_not_produced`; Website/Career=`candidate_not_produced`
  - Posting status: `___`
  - Correct website: `___`
  - Correct Career: `___`
  - Correct Job list: `___`
  - Correct opening: `___`
  - Disposition: `___`
  - Failure route: `___`
  - Notes: `___`问题A
- [ ] **10. Redlands Community Hospital** - New Graduate Registered Nurse (RN); Redlands, CA

  - 自动结果：[LinkedIn](https://www.linkedin.com/jobs/view/new-graduate-registered-nurse-rn-at-redlands-community-hospital-4437663361) | [Website](https://www.redlandshospital.org) | [Career](https://www.redlandshospital.org/careers/) | Job list: 未找到
  - 当前分类：pipeline=`partial`; stages=`job_board_discovery:JOB_BOARD_NOT_FOUND`
  - 三路诊断：External Apply=`no_visible_external_apply_link`; ATS Search=`candidate_not_produced`; Website/Career=`candidate_not_produced`
  - Posting status: `___`
  - Correct website: `___`
  - Correct Career: `___`
  - Correct Job list: `___`
  - Correct opening: `___`
  - Disposition: `___`
  - Failure route: `___`
  - Notes: `___`问题A
- [ ] **13. Tenet Healthcare** - Registered Nurse (RN) - Ambulatory; San Ramon, CA

  - 自动结果：[LinkedIn](https://www.linkedin.com/jobs/view/registered-nurse-rn-ambulatory-at-tenet-healthcare-4440577391) | [Website](https://www.tenethealth.com) | [Career](https://jobs.tenethealth.com/) | Job list: 未找到
  - 当前分类：pipeline=`partial`; stages=`job_board_discovery:JOB_BOARD_NOT_FOUND`
  - 三路诊断：External Apply=`no_visible_external_apply_link`; ATS Search=`candidate_not_produced`; Website/Career=`candidate_not_produced`
  - Posting status: `___`
  - Correct website: `___`
  - Correct Career: `___`
  - Correct Job list: `___`
  - Correct opening: `___`
  - Disposition: `___`
  - Failure route: `___`
  - Notes: `___`问题C
- [ ] **15. Horizon Health** - Registered Nurse (RN)- Behavioral Health; Murray, KY

  - 自动结果：[LinkedIn](https://www.linkedin.com/jobs/view/registered-nurse-rn-behavioral-health-at-horizon-health-4440822674) | [Website](https://www.myhorizonhealth.org) | [Career](https://www.myhorizonhealth.org/about/careers/) | Job list: 未找到
  - 当前分类：pipeline=`partial`; stages=`job_board_discovery:JOB_BOARD_NOT_FOUND`
  - 三路诊断：External Apply=`no_visible_external_apply_link`; ATS Search=`candidate_not_produced`; Website/Career=`candidate_not_produced`
  - Posting status: `___`
  - Correct website: `___`
  - Correct Career: `___`
  - Correct Job list: `___`
  - Correct opening: `___`
  - Disposition: `___`
  - Failure route: `___`
  - Notes: `___`问题B
- [ ] **21. NexCare WellBridge Senior Living** - Registered Nurse RN - $40.55 per hour; Saginaw, MI

  - 自动结果：[LinkedIn](https://www.linkedin.com/jobs/view/registered-nurse-rn-%2440-55-per-hour-at-nexcare-wellbridge-senior-living-4440360762) | [Website](https://www.nexcarehealth.com) | [Career](https://www.nexcarehealth.com/careers-overview/) | Job list: 未找到
  - 当前分类：pipeline=`partial`; stages=`job_board_discovery:JOB_BOARD_NOT_FOUND`
  - 三路诊断：External Apply=`no_visible_external_apply_link`; ATS Search=`candidate_not_produced`; Website/Career=`candidate_not_produced`
  - Posting status: `___`
  - Correct website: `___`
  - Correct Career: `___`
  - Correct Job list: `___`
  - Correct opening: `___`
  - Disposition: `___`
  - Failure route: `___`
  - Notes: `___`问题A
- [ ] **32. Square** - SMB Account Executive; New York, NY

  - 自动结果：[LinkedIn](https://www.linkedin.com/jobs/view/smb-account-executive-at-square-4430270488) | [Website](https://squareup.com/us/en) | [Career](https://careers.squareup.com/us/en) | Job list: 未找到
  - 当前分类：pipeline=`partial`; stages=`job_board_discovery:JOB_BOARD_NOT_FOUND`
  - 三路诊断：External Apply=`no_visible_external_apply_link`; ATS Search=`candidate_not_produced`; Website/Career=`candidate_not_produced`
  - Posting status: `___`
  - Correct website: `___`
  - Correct Career: `___`
  - Correct Job list: `___`
  - Correct opening: `___`
  - Disposition: `___`
  - Failure route: `___`
  - Notes: `___`问题A
- [ ] **34. Future Beauty Brands (formerly PPI Beauty)** - National Account Manager; New York City Metropolitan Area

  - 自动结果：[LinkedIn](https://www.linkedin.com/jobs/view/national-account-manager-at-future-beauty-brands-formerly-ppi-beauty-4441236528) | [Website](https://futurebeautybrands.com/) | [Career](https://futurebeautybrands.com/pages/careers) | Job list: 未找到
  - 当前分类：pipeline=`partial`; stages=`job_board_discovery:JOB_BOARD_NOT_FOUND`
  - 三路诊断：External Apply=`no_visible_external_apply_link`; ATS Search=`candidate_not_produced`; Website/Career=`candidate_not_produced`
  - Posting status: `___`
  - Correct website: `___`
  - Correct Career: `___`
  - Correct Job list: `___`
  - Correct opening: `___`
  - Disposition: `___`
  - Failure route: `___`
  - Notes: `___`官网不放岗位，不是你的问题
- [ ] **36. Yamaha Motor Corporation, USA** - Mechanical Engineer I; Kennesaw, GA

  - 自动结果：[LinkedIn](https://www.linkedin.com/jobs/view/mechanical-engineer-i-at-yamaha-motor-corporation-usa-4388242757) | [Website](https://www.yamaha-motor.com) | [Career](https://careers.yamaha-motor.com) | Job list: 未找到
  - 当前分类：pipeline=`partial`; stages=`job_board_discovery:JOB_BOARD_NOT_FOUND`
  - 三路诊断：External Apply=`no_visible_external_apply_link`; ATS Search=`candidate_not_produced`; Website/Career=`candidate_not_produced`
  - Posting status: `___`
  - Correct website: `___`
  - Correct Career: `___`
  - Correct Job list: `___`
  - Correct opening: `___`
  - Disposition: `___`
  - Failure route: `___`
  - Notes: `___`问题A
- [ ] **46. Gucci** - GUCCI Financial Analyst; New York, NY

  - 自动结果：[LinkedIn](https://www.linkedin.com/jobs/view/gucci-financial-analyst-at-gucci-4432834505) | [Website](https://careers.gucci.com/) | [Career](https://careers.gucci.com) | Job list: 未找到
  - 当前分类：pipeline=`partial`; stages=`job_board_discovery:JOB_BOARD_NOT_FOUND`
  - 三路诊断：External Apply=`fetch_failed`; ATS Search=`candidate_not_produced`; Website/Career=`candidate_not_produced`
  - Posting status: `___`
  - Correct website: `___`
  - Correct Career: `___`
  - Correct Job list: `___`
  - Correct opening: `___`
  - Disposition: `___`
  - Failure route: `___`
  - Notes: `___`
- [ ] **48. Century Communities, Inc. (NYSE:CCS)** - Corporate Financial Analyst; Greenwood Village, CO

  - 自动结果：[LinkedIn](https://www.linkedin.com/jobs/view/corporate-financial-analyst-at-century-communities-inc-nyse-ccs-4440349440) | [Website](https://www.centurycommunities.com) | [Career](https://www.centurycommunities.com/Careers/) | Job list: 未找到
  - 当前分类：pipeline=`partial`; stages=`job_board_discovery:JOB_BOARD_NOT_FOUND`
  - 三路诊断：External Apply=`no_visible_external_apply_link`; ATS Search=`candidate_not_produced`; Website/Career=`candidate_not_produced`
  - Posting status: `___`
  - Correct website: `___`
  - Correct Career: `___`
  - Correct Job list: `___`
  - Correct opening: `___`
  - Disposition: `___`
  - Failure route: `___`
  - Notes: `___`
- [ ] **52. L'OCCITANE Group (B Corp)** - Senior Financial Analyst (CONTRACT); New York, NY

  - 自动结果：[LinkedIn](https://www.linkedin.com/jobs/view/senior-financial-analyst-contract-at-l-occitane-group-b-corp-4343041480) | [Website](https://group.loccitane.com) | [Career](https://careers.loccitane.com/) | Job list: 未找到
  - 当前分类：pipeline=`partial`; stages=`job_board_discovery:JOB_BOARD_NOT_FOUND`
  - 三路诊断：External Apply=`no_visible_external_apply_link`; ATS Search=`candidate_not_produced`; Website/Career=`candidate_not_produced`
  - Posting status: `___`
  - Correct website: `___`
  - Correct Career: `___`
  - Correct Job list: `___`
  - Correct opening: `___`
  - Disposition: `___`
  - Failure route: `___`
  - Notes: `___`
- [ ] **53. adidas** - Financial Analyst; Portland, OR

  - 自动结果：[LinkedIn](https://www.linkedin.com/jobs/view/financial-analyst-at-adidas-4433034488) | [Website](https://www.adidas-group.com/en/) | [Career](https://www.adidas-group.com/en/magazine/careers/hybrid-hotel-launching-the-adizero-dropset-pro-on-the-global-stage) | Job list: 未找到
  - 当前分类：pipeline=`partial`; stages=`job_board_discovery:JOB_BOARD_NOT_FOUND`
  - 三路诊断：External Apply=`no_visible_external_apply_link`; ATS Search=`candidate_not_produced`; Website/Career=`candidate_not_produced`
  - Posting status: `___`
  - Correct website: `___`
  - Correct Career: `___`
  - Correct Job list: `___`
  - Correct opening: `___`
  - Disposition: `___`
  - Failure route: `___`
  - Notes: `___`
- [ ] **68. Steve Madden** - Junior Business Intelligence Analyst- Digital; New York, NY

  - 自动结果：[LinkedIn](https://www.linkedin.com/jobs/view/junior-business-intelligence-analyst-digital-at-steve-madden-4432716413) | [Website](https://www.stevemadden.com/) | [Career](https://www.stevemadden.com/pages/steve-madden-careers) | Job list: 未找到
  - 当前分类：pipeline=`partial`; stages=`job_board_discovery:JOB_BOARD_NOT_FOUND`
  - 三路诊断：External Apply=`no_visible_external_apply_link`; ATS Search=`candidate_not_produced`; Website/Career=`candidate_not_produced`
  - Posting status: `___`
  - Correct website: `___`
  - Correct Career: `___`
  - Correct Job list: `___`
  - Correct opening: `___`
  - Disposition: `___`
  - Failure route: `___`
  - Notes: `___`
- [ ] **69. Solomon Page** - Data Analyst; Austin, TX

  - 自动结果：[LinkedIn](https://www.linkedin.com/jobs/view/data-analyst-at-solomon-page-4438018820) | [Website](https://www.solomonpage.com) | [Career](https://opportunities.solomonpage.com) | Job list: 未找到
  - 当前分类：pipeline=`partial`; stages=`job_board_discovery:JOB_BOARD_NOT_FOUND`
  - 三路诊断：External Apply=`no_visible_external_apply_link`; ATS Search=`candidate_not_produced`; Website/Career=`candidate_not_produced`
  - Posting status: `___`
  - Correct website: `___`
  - Correct Career: `___`
  - Correct Job list: `___`
  - Correct opening: `___`
  - Disposition: `___`
  - Failure route: `___`
  - Notes: `___`
- [ ] **71. LTIMindtree** - Data Analyst/BA; Jersey City, NJ

  - 自动结果：[LinkedIn](https://www.linkedin.com/jobs/view/data-analyst-ba-at-ltimindtree-4439886741) | [Website](https://www.ltm.com/) | [Career](https://www.ltm.com/careers) | Job list: 未找到
  - 当前分类：pipeline=`partial`; stages=`job_board_discovery:JOB_BOARD_NOT_FOUND`
  - 三路诊断：External Apply=`no_visible_external_apply_link`; ATS Search=`candidate_not_produced`; Website/Career=`candidate_not_produced`
  - Posting status: `___`
  - Correct website: `___`
  - Correct Career: `___`
  - Correct Job list: `___`
  - Correct opening: `___`
  - Disposition: `___`
  - Failure route: `___`
  - Notes: `___`

### PROVIDER_FETCH_FAILED (16)

- [ ] **6. Paramount** - Software Engineer; New York, NY

  - 自动结果：[LinkedIn](https://www.linkedin.com/jobs/view/software-engineer-at-paramount-4408291912) | [Website](https://www.paramount.com) | [Career](https://careers.paramount.com) | [Job list](https://career41.sapsf.com)
  - 当前分类：pipeline=`partial`; stages=`opening_match:PROVIDER_FETCH_FAILED`
  - 三路诊断：External Apply=`no_visible_external_apply_link`; ATS Search=`candidate_not_produced`; Website/Career=`final_exact_not_available`
  - Posting status: `___`
  - Correct website: `___`
  - Correct Career: `___`
  - Correct Job list: `___`
  - Correct opening: `___`
  - Disposition: `___`
  - Failure route: `___`
  - Notes: `___`careerpage找到了，顺着点开就能看到joblist里面可以搜索到，你做的有问题，跟问题A差不多
- [ ] **35. Meta** - Product Design Engineer; New York, NY

  - 自动结果：[LinkedIn](https://www.linkedin.com/jobs/view/product-design-engineer-at-meta-4434865612) | [Website](https://www.meta.com/) | [Career](https://www.metacareers.com/jobs/) | [Job list](https://www.metacareers.com/jobsearch/)
  - 当前分类：pipeline=`partial`; stages=`opening_match:PROVIDER_FETCH_FAILED`
  - 三路诊断：External Apply=`no_visible_external_apply_link`; ATS Search=`candidate_not_produced`; Website/Career=`final_exact_not_available`
  - Posting status: `___`
  - Correct website: `___`
  - Correct Career: `___`
  - Correct Job list: `___`
  - Correct opening: `___`
  - Disposition: `___`
  - Failure route: `___`
  - Notes: `___`已经找到joblist了，但是没有点进岗位，还有我不知道这个你为什么放到这个失败分类里
- [ ] **38. Meta** - Product Design Engineer; Sunnyvale, CA

  - 自动结果：[LinkedIn](https://www.linkedin.com/jobs/view/product-design-engineer-at-meta-4433807917) | [Website](https://www.meta.com/) | [Career](https://www.metacareers.com/jobs/) | [Job list](https://www.metacareers.com/jobsearch/)
  - 当前分类：pipeline=`partial`; stages=`opening_match:PROVIDER_FETCH_FAILED`
  - 三路诊断：External Apply=`no_visible_external_apply_link`; ATS Search=`candidate_not_produced`; Website/Career=`final_exact_not_available`
  - Posting status: `___`
  - Correct website: `___`
  - Correct Career: `___`
  - Correct Job list: `___`
  - Correct opening: `___`
  - Disposition: `___`
  - Failure route: `___`
  - Notes: `___`已经找到joblist了，但是没有点进岗位，还有我不知道这个你为什么放到这个失败分类里
- [ ] **41. Meta** - Product Design Engineer; Redmond, WA

  - 自动结果：[LinkedIn](https://www.linkedin.com/jobs/view/product-design-engineer-at-meta-4431557512) | [Website](https://www.meta.com/) | [Career](https://www.metacareers.com/jobs/) | [Job list](https://www.metacareers.com/jobsearch/)
  - 当前分类：pipeline=`partial`; stages=`opening_match:PROVIDER_FETCH_FAILED`
  - 三路诊断：External Apply=`no_visible_external_apply_link`; ATS Search=`candidate_not_produced`; Website/Career=`final_exact_not_available`
  - Posting status: `___`
  - Correct website: `___`
  - Correct Career: `___`
  - Correct Job list: `___`
  - Correct opening: `___`
  - Disposition: `___`
  - Failure route: `___`
  - Notes: `___`已经找到joblist了，但是没有点进岗位，还有我不知道这个你为什么放到这个失败分类里
- [ ] **55. Instagram** - Product Manager; Menlo Park, CA

  - 自动结果：[LinkedIn](https://www.linkedin.com/jobs/view/product-manager-at-instagram-4198546605) | [Website](https://www.instagram.com/) | [Career](https://www.metacareers.com/jobs/) | [Job list](https://www.metacareers.com/jobsearch/)
  - 当前分类：pipeline=`partial`; stages=`opening_match:PROVIDER_FETCH_FAILED`
  - 三路诊断：External Apply=`no_visible_external_apply_link`; ATS Search=`candidate_not_produced`; Website/Career=`final_exact_not_available`
  - Posting status: `___`
  - Correct website: `___`
  - Correct Career: `___`
  - Correct Job list: `___`
  - Correct opening: `___`
  - Disposition: `___`
  - Failure route: `___`
  - Notes: `___`已经找到joblist了，但是没有点进岗位，还有我不知道这个你为什么放到这个失败分类里
- [ ] **56. Meta** - Product Manager; New York, NY

  - 自动结果：[LinkedIn](https://www.linkedin.com/jobs/view/product-manager-at-meta-4432210853) | [Website](https://www.meta.com/) | [Career](https://www.metacareers.com/jobs/) | [Job list](https://www.metacareers.com/jobsearch/)
  - 当前分类：pipeline=`partial`; stages=`opening_match:PROVIDER_FETCH_FAILED`
  - 三路诊断：External Apply=`no_visible_external_apply_link`; ATS Search=`candidate_not_produced`; Website/Career=`final_exact_not_available`
  - Posting status: `___`
  - Correct website: `___`
  - Correct Career: `___`
  - Correct Job list: `___`
  - Correct opening: `___`
  - Disposition: `___`
  - Failure route: `___`
  - Notes: `___`已经找到joblist了，但是没有点进岗位，还有我不知道这个你为什么放到这个失败分类里
- [ ] **57. Meta** - Product Manager; Menlo Park, CA

  - 自动结果：[LinkedIn](https://www.linkedin.com/jobs/view/product-manager-at-meta-4198807083) | [Website](https://www.meta.com/) | [Career](https://www.metacareers.com/jobs/) | [Job list](https://www.metacareers.com/jobsearch/)
  - 当前分类：pipeline=`partial`; stages=`opening_match:PROVIDER_FETCH_FAILED`
  - 三路诊断：External Apply=`no_visible_external_apply_link`; ATS Search=`candidate_not_produced`; Website/Career=`final_exact_not_available`
  - Posting status: `___`
  - Correct website: `___`
  - Correct Career: `___`
  - Correct Job list: `___`
  - Correct opening: `___`
  - Disposition: `___`
  - Failure route: `___`
  - Notes: `___`已经找到joblist了，但是没有点进岗位，还有我不知道这个你为什么放到这个失败分类里
- [ ] **58. Meta** - Product Manager; Burlingame, CA

  - 自动结果：[LinkedIn](https://www.linkedin.com/jobs/view/product-manager-at-meta-4199409425) | [Website](https://www.meta.com/) | [Career](https://www.metacareers.com/jobs/) | [Job list](https://www.metacareers.com/jobsearch/)
  - 当前分类：pipeline=`partial`; stages=`opening_match:PROVIDER_FETCH_FAILED`
  - 三路诊断：External Apply=`no_visible_external_apply_link`; ATS Search=`candidate_not_produced`; Website/Career=`final_exact_not_available`
  - Posting status: `___`
  - Correct website: `___`
  - Correct Career: `___`
  - Correct Job list: `___`
  - Correct opening: `___`
  - Disposition: `___`
  - Failure route: `___`
  - Notes: `___`已经找到joblist了，但是没有点进岗位，还有我不知道这个你为什么放到这个失败分类里
- [ ] **59. Meta** - Product Manager; Sunnyvale, CA

  - 自动结果：[LinkedIn](https://www.linkedin.com/jobs/view/product-manager-at-meta-4254370688) | [Website](https://www.meta.com/) | [Career](https://www.metacareers.com/jobs/) | [Job list](https://www.metacareers.com/jobsearch/)
  - 当前分类：pipeline=`partial`; stages=`opening_match:PROVIDER_FETCH_FAILED`
  - 三路诊断：External Apply=`no_visible_external_apply_link`; ATS Search=`candidate_not_produced`; Website/Career=`final_exact_not_available`
  - Posting status: `___`
  - Correct website: `___`
  - Correct Career: `___`
  - Correct Job list: `___`
  - Correct opening: `___`
  - Disposition: `___`
  - Failure route: `___`
  - Notes: `___`已经找到joblist了，但是没有点进岗位，还有我不知道这个你为什么放到这个失败分类里
- [ ] **60. Instagram** - Product Manager; Bellevue, WA

  - 自动结果：[LinkedIn](https://www.linkedin.com/jobs/view/product-manager-at-instagram-4197550638) | [Website](https://www.instagram.com/) | [Career](https://www.metacareers.com/jobs/) | [Job list](https://www.metacareers.com/jobsearch/)
  - 当前分类：pipeline=`partial`; stages=`opening_match:PROVIDER_FETCH_FAILED`
  - 三路诊断：External Apply=`no_visible_external_apply_link`; ATS Search=`candidate_not_produced`; Website/Career=`final_exact_not_available`
  - Posting status: `___`
  - Correct website: `___`
  - Correct Career: `___`
  - Correct Job list: `___`
  - Correct opening: `___`
  - Disposition: `___`
  - Failure route: `___`
  - Notes: `___`已经找到joblist了，但是没有点进岗位，还有我不知道这个你为什么放到这个失败分类里
- [ ] **61. Meta** - Product Manager; Menlo Park, CA

  - 自动结果：[LinkedIn](https://www.linkedin.com/jobs/view/product-manager-at-meta-4432205983) | [Website](https://www.meta.com/) | [Career](https://www.metacareers.com/jobs/) | [Job list](https://www.metacareers.com/jobsearch/)
  - 当前分类：pipeline=`partial`; stages=`opening_match:PROVIDER_FETCH_FAILED`
  - 三路诊断：External Apply=`no_visible_external_apply_link`; ATS Search=`candidate_not_produced`; Website/Career=`final_exact_not_available`
  - Posting status: `___`
  - Correct website: `___`
  - Correct Career: `___`
  - Correct Job list: `___`
  - Correct opening: `___`
  - Disposition: `___`
  - Failure route: `___`
  - Notes: `___`已经找到joblist了，但是没有点进岗位，还有我不知道这个你为什么放到这个失败分类里
- [ ] **62. Meta** - Product Manager; New York, NY

  - 自动结果：[LinkedIn](https://www.linkedin.com/jobs/view/product-manager-at-meta-4314986128) | [Website](https://www.meta.com/) | [Career](https://www.metacareers.com/jobs/) | [Job list](https://www.metacareers.com/jobsearch/)
  - 当前分类：pipeline=`partial`; stages=`opening_match:PROVIDER_FETCH_FAILED`
  - 三路诊断：External Apply=`no_visible_external_apply_link`; ATS Search=`candidate_not_produced`; Website/Career=`final_exact_not_available`
  - Posting status: `___`
  - Correct website: `___`
  - Correct Career: `___`
  - Correct Job list: `___`
  - Correct opening: `___`
  - Disposition: `___`
  - Failure route: `___`
  - Notes: `___`已经找到joblist了，但是没有点进岗位，还有我不知道这个你为什么放到这个失败分类里
- [ ] **63. Meta** - Product Manager; United States

  - 自动结果：[LinkedIn](https://www.linkedin.com/jobs/view/product-manager-at-meta-4254366801) | [Website](https://www.meta.com/) | [Career](https://www.metacareers.com/jobs/) | [Job list](https://www.metacareers.com/jobsearch/)
  - 当前分类：pipeline=`partial`; stages=`opening_match:PROVIDER_FETCH_FAILED`
  - 三路诊断：External Apply=`no_visible_external_apply_link`; ATS Search=`candidate_not_produced`; Website/Career=`final_exact_not_available`
  - Posting status: `___`
  - Correct website: `___`
  - Correct Career: `___`
  - Correct Job list: `___`
  - Correct opening: `___`
  - Disposition: `___`
  - Failure route: `___`
  - Notes: `___`已经找到joblist了，但是没有点进岗位，还有我不知道这个你为什么放到这个失败分类里
- [ ] **65. Meta** - Product Manager; Redmond, WA

  - 自动结果：[LinkedIn](https://www.linkedin.com/jobs/view/product-manager-at-meta-4254371627) | [Website](https://www.meta.com/) | [Career](https://www.metacareers.com/jobs/) | [Job list](https://www.metacareers.com/jobsearch/)
  - 当前分类：pipeline=`partial`; stages=`opening_match:PROVIDER_FETCH_FAILED`
  - 三路诊断：External Apply=`no_visible_external_apply_link`; ATS Search=`candidate_not_produced`; Website/Career=`final_exact_not_available`
  - Posting status: `___`
  - Correct website: `___`
  - Correct Career: `___`
  - Correct Job list: `___`
  - Correct opening: `___`
  - Disposition: `___`
  - Failure route: `___`
  - Notes: `___`已经找到joblist了，但是没有点进岗位，还有我不知道这个你为什么放到这个失败分类里
- [ ] **66. Meta** - Product Manager (Leadership); New York, NY

  - 自动结果：[LinkedIn](https://www.linkedin.com/jobs/view/product-manager-leadership-at-meta-4418315263) | [Website](https://www.meta.com/) | [Career](https://www.metacareers.com/jobs/) | [Job list](https://www.metacareers.com/jobsearch/)
  - 当前分类：pipeline=`partial`; stages=`opening_match:PROVIDER_FETCH_FAILED`
  - 三路诊断：External Apply=`no_visible_external_apply_link`; ATS Search=`candidate_not_produced`; Website/Career=`final_exact_not_available`
  - Posting status: `___`
  - Correct website: `___`
  - Correct Career: `___`
  - Correct Job list: `___`
  - Correct opening: `___`
  - Disposition: `___`
  - Failure route: `___`
  - Notes: `___`已经找到joblist了，但是没有点进岗位，还有我不知道这个你为什么放到这个失败分类里
- [ ] **67. Meta** - Product Manager; Bellevue, WA

  - 自动结果：[LinkedIn](https://www.linkedin.com/jobs/view/product-manager-at-meta-4297751666) | [Website](https://www.meta.com/) | [Career](https://www.metacareers.com/jobs/) | [Job list](https://www.metacareers.com/jobsearch/)
  - 当前分类：pipeline=`partial`; stages=`opening_match:PROVIDER_FETCH_FAILED`
  - 三路诊断：External Apply=`no_visible_external_apply_link`; ATS Search=`candidate_not_produced`; Website/Career=`final_exact_not_available`
  - Posting status: `___`
  - Correct website: `___`
  - Correct Career: `___`
  - Correct Job list: `___`
  - Correct opening: `___`
  - Disposition: `___`
  - Failure route: `___`
  - Notes: `___`已经找到joblist了，但是没有点进岗位，还有我不知道这个你为什么放到这个失败分类里

### CAREER_PAGE_NOT_FOUND (13)

- [ ] **2. Hadrian** - All Levels: Frontend Software Engineer; Los Angeles, CA

  - 自动结果：[LinkedIn](https://www.linkedin.com/jobs/view/all-levels-frontend-software-engineer-at-hadrian-4285833475) | [Website](https://www.hadrian.com/) | Career: 未找到 | Job list: 未找到
  - 当前分类：pipeline=`failed`; stages=`career_discovery:CAREER_PAGE_NOT_FOUND`
  - 三路诊断：External Apply=`no_visible_external_apply_link`; ATS Search=`candidate_not_produced`; Website/Career=`candidate_not_produced`
  - Posting status: `___`
  - Correct website: `___`
  - Correct Career: `___`
  - Correct Job list: `___`
  - Correct opening: `___`
  - Disposition: `___`
  - Failure route: `___`
  - Notes: `___`这个不怪你，就没有careerpage更没有岗位，不是你的问题
- [ ] **3. Hadrian** - Fullstack Software Engineer, New Grad; Los Angeles, CA

  - 自动结果：[LinkedIn](https://www.linkedin.com/jobs/view/fullstack-software-engineer-new-grad-at-hadrian-4427890981) | [Website](https://www.hadrian.com/) | Career: 未找到 | Job list: 未找到
  - 当前分类：pipeline=`failed`; stages=`career_discovery:CAREER_PAGE_NOT_FOUND`
  - 三路诊断：External Apply=`no_visible_external_apply_link`; ATS Search=`candidate_not_produced`; Website/Career=`candidate_not_produced`
  - Posting status: `___`
  - Correct website: `___`
  - Correct Career: `___`
  - Correct Job list: `___`
  - Correct opening: `___`
  - Disposition: `___`
  - Failure route: `___`
  - Notes: `___`这个不怪你，就没有careerpage更没有岗位，不是你的问题
- [ ] **12. West Oaks Hospital** - Nurse Manager -Registered Nurse (Full-Time) - Behavioral Health - $7,500 Sign-On Bonus; Houston, TX

  - 自动结果：[LinkedIn](https://www.linkedin.com/jobs/view/nurse-manager-registered-nurse-full-time-behavioral-health-%247-500-sign-on-bonus-at-west-oaks-hospital-4440824545) | [Website](https://www.westoakshospital.com) | Career: 未找到 | Job list: 未找到
  - 当前分类：pipeline=`failed`; stages=`career_discovery:CAREER_PAGE_NOT_FOUND`
  - 三路诊断：External Apply=`no_visible_external_apply_link`; ATS Search=`candidate_not_produced`; Website/Career=`candidate_not_produced`
  - Posting status: `___`
  - Correct website: `___`
  - Correct Career: `___`
  - Correct Job list: `___`
  - Correct opening: `___`
  - Disposition: `___`
  - Failure route: `___`
  - Notes: `___`这个不怪你，就没有careerpage更没有岗位，不是你的问题
- [ ] **14. Panacea Health Corp** - Registered Nurse (RN) Unit Manager; Chambersburg, PA

  - 自动结果：[LinkedIn](https://www.linkedin.com/jobs/view/registered-nurse-rn-unit-manager-at-panacea-health-corp-4439893528) | [Website](https://panaceahealth.io/) | Career: 未找到 | Job list: 未找到
  - 当前分类：pipeline=`failed`; stages=`career_discovery:CAREER_PAGE_NOT_FOUND`
  - 三路诊断：External Apply=`no_visible_external_apply_link`; ATS Search=`candidate_not_produced`; Website/Career=`candidate_not_produced`
  - Posting status: `___`
  - Correct website: `___`
  - Correct Career: `___`
  - Correct Job list: `___`
  - Correct opening: `___`
  - Disposition: `___`
  - Failure route: `___`
  - Notes: `___`这个不怪你，就没有careerpage更没有岗位，不是你的问题
- [ ] **16. Tidelands Health** - Registered Nurse (RN) - Staff/Med Surg; Georgetown, SC

  - 自动结果：[LinkedIn](https://www.linkedin.com/jobs/view/registered-nurse-rn-staff-med-surg-at-tidelands-health-4440340861) | [Website](https://www.tidelandshealth.org) | Career: 未找到 | Job list: 未找到
  - 当前分类：pipeline=`failed`; stages=`career_discovery:CAREER_PAGE_NOT_FOUND`
  - 三路诊断：External Apply=`no_visible_external_apply_link`; ATS Search=`candidate_not_produced`; Website/Career=`candidate_not_produced`
  - Posting status: `___`
  - Correct website: `___`
  - Correct Career: `___`
  - Correct Job list: `___`
  - Correct opening: `___`
  - Disposition: `___`
  - Failure route: `___`
  - Notes: `___`这个不怪你，access denied，不是你的问题
- [ ] **20. Southeastern Renal Dialysis** - Registered Nurse (RN) ($10,000 SIGN ON BONUS); West Burlington, IA

  - 自动结果：[LinkedIn](https://www.linkedin.com/jobs/view/registered-nurse-rn-%2410-000-sign-on-bonus-at-southeastern-renal-dialysis-4440377324) | [Website](https://srdlc.org) | Career: 未找到 | Job list: 未找到
  - 当前分类：pipeline=`failed`; stages=`career_discovery:CAREER_PAGE_NOT_FOUND`
  - 三路诊断：External Apply=`no_visible_external_apply_link`; ATS Search=`candidate_not_produced`; Website/Career=`candidate_not_produced`
  - Posting status: `___`
  - Correct website: `___`
  - Correct Career: `___`
  - Correct Job list: `___`
  - Correct opening: `___`
  - Disposition: `___`
  - Failure route: `___`
  - Notes: `___`这个不怪你，就没有careerpage更没有岗位，不是你的问题
- [ ] **26. SKIMS** - Account Executive, Franchise Partnerships; Los Angeles, CA

  - 自动结果：[LinkedIn](https://www.linkedin.com/jobs/view/account-executive-franchise-partnerships-at-skims-4429787695) | [Website](https://skims.com/en-sg) | Career: 未找到 | Job list: 未找到
  - 当前分类：pipeline=`failed`; stages=`career_discovery:CAREER_PAGE_NOT_FOUND`
  - 三路诊断：External Apply=`no_visible_external_apply_link`; ATS Search=`candidate_not_produced`; Website/Career=`candidate_not_produced`
  - Posting status: `___`
  - Correct website: `___`
  - Correct Career: `___`
  - Correct Job list: `___`
  - Correct opening: `___`
  - Disposition: `___`
  - Failure route: `___`
  - Notes: `___`这个你官网找对了，但是careerpage没找到不应该，就在官网主页最下面就有career，再往后找到joblist也是顺其自然。可能一开始要求选你所处的地区卡住了？
- [ ] **28. Parfums Christian Dior** - Account Executive, Chicago; Chicago, IL

  - 自动结果：[LinkedIn](https://www.linkedin.com/jobs/view/account-executive-chicago-at-parfums-christian-dior-4428662758) | [Website](https://www.dior.com) | Career: 未找到 | Job list: 未找到
  - 当前分类：pipeline=`failed`; stages=`career_discovery:CAREER_PAGE_NOT_FOUND`
  - 三路诊断：External Apply=`no_visible_external_apply_link`; ATS Search=`candidate_not_produced`; Website/Career=`candidate_not_produced`
  - Posting status: `___`
  - Correct website: `___`
  - Correct Career: `___`
  - Correct Job list: `___`
  - Correct opening: `___`
  - Disposition: `___`
  - Failure route: `___`
  - Notes: `___`这个你官网找对了，但是careerpage没找到不应该，就在官网主页最下面就有career，可能一开始要求选你所处的地区卡住了？
- [ ] **29. Caudalie** - Account Executive; New York, NY

  - 自动结果：[LinkedIn](https://www.linkedin.com/jobs/view/account-executive-at-caudalie-4427354704) | [Website](https://www.caudalie.com/) | Career: 未找到 | Job list: 未找到
  - 当前分类：pipeline=`failed`; stages=`career_discovery:CAREER_PAGE_NOT_FOUND`
  - 三路诊断：External Apply=`no_visible_external_apply_link`; ATS Search=`candidate_not_produced`; Website/Career=`candidate_not_produced`
  - Posting status: `___`
  - Correct website: `___`
  - Correct Career: `___`
  - Correct Job list: `___`
  - Correct opening: `___`
  - Disposition: `___`
  - Failure route: `___`
  - Notes: `___`这个你官网找对了，但是careerpage没找到不应该，就在官网主页最下面就有career，可能一开始要求选你所处的地区卡住了？
- [ ] **47. Michael Kors** - Financial Analyst; New York, NY

  - 自动结果：[LinkedIn](https://www.linkedin.com/jobs/view/financial-analyst-at-michael-kors-4432425467) | [Website](https://michaelkors.com) | Career: 未找到 | Job list: 未找到
  - 当前分类：pipeline=`failed`; stages=`career_discovery:CAREER_PAGE_NOT_FOUND`
  - 三路诊断：External Apply=`no_visible_external_apply_link`; ATS Search=`candidate_not_produced`; Website/Career=`candidate_not_produced`
  - Posting status: `___`
  - Correct website: `___`
  - Correct Career: `___`
  - Correct Job list: `___`
  - Correct opening: `___`
  - Disposition: `___`
  - Failure route: `___`
  - Notes: `___`这个你官网找对了，但是careerpage没找到不应该，就在官网主页最下面就有career
- [ ] **49. Saint Laurent** - SAINT LAURENT Financial Analyst; Wayne, NJ

  - 自动结果：[LinkedIn](https://www.linkedin.com/jobs/view/saint-laurent-financial-analyst-at-saint-laurent-4434898301) | [Website](https://www.ysl.com) | Career: 未找到 | Job list: 未找到
  - 当前分类：pipeline=`failed`; stages=`career_discovery:CAREER_PAGE_NOT_FOUND`
  - 三路诊断：External Apply=`no_visible_external_apply_link`; ATS Search=`candidate_not_produced`; Website/Career=`candidate_not_produced`
  - Posting status: `___`
  - Correct website: `___`
  - Correct Career: `___`
  - Correct Job list: `___`
  - Correct opening: `___`
  - Disposition: `___`
  - Failure route: `___`
  - Notes: `___`这个你官网找对了，但是careerpage没找到不应该，就在官网主页最下面就有career
- [ ] **54. Garan, Incorporated** - Junior Financial Operations Analyst; New York, NY

  - 自动结果：[LinkedIn](https://www.linkedin.com/jobs/view/junior-financial-operations-analyst-at-garan-incorporated-4440596578) | [Website](https://www.garanimals.com) | Career: 未找到 | Job list: 未找到
  - 当前分类：pipeline=`failed`; stages=`career_discovery:CAREER_PAGE_NOT_FOUND`
  - 三路诊断：External Apply=`no_visible_external_apply_link`; ATS Search=`candidate_not_produced`; Website/Career=`candidate_not_produced`
  - Posting status: `___`
  - Correct website: `___`
  - Correct Career: `___`
  - Correct Job list: `___`
  - Correct opening: `___`
  - Disposition: `___`
  - Failure route: `___`
  - Notes: `___`这个好像确实没有careerpage不怪你
- [ ] **64. Great Value Hiring** - Product Manager; United States

  - 自动结果：[LinkedIn](https://www.linkedin.com/jobs/view/product-manager-at-great-value-hiring-4439862724) | [Website](https://greatvaluehiring.com) | Career: 未找到 | Job list: 未找到
  - 当前分类：pipeline=`failed`; stages=`career_discovery:CAREER_PAGE_NOT_FOUND`
  - 三路诊断：External Apply=`no_visible_external_apply_link`; ATS Search=`candidate_not_produced`; Website/Career=`candidate_not_produced`
  - Posting status: `___`
  - Correct website: `___`
  - Correct Career: `___`
  - Correct Job list: `___`
  - Correct opening: `___`
  - Disposition: `___`
  - Failure route: `___`
  - Notes: `___`这个好像确实没有careerpage不怪你

### OPENING_DISCOVERY_INCOMPLETE (8)

- [ ] **7. hackajob** - Software Engineer - Apollo Platform; New York, NY

  - 自动结果：[LinkedIn](https://www.linkedin.com/jobs/view/software-engineer-apollo-platform-at-hackajob-4440203478) | [Website](https://hackajob.com/) | [Career](https://hackajob.com/jobs) | [Job list](https://hackajob.com/en-us/jobs)
  - 当前分类：pipeline=`partial`; stages=`opening_match:OPENING_DISCOVERY_INCOMPLETE`
  - 三路诊断：External Apply=`no_visible_external_apply_link`; ATS Search=`candidate_not_produced`; Website/Career=`final_exact_not_available`
  - Posting status: `___`
  - Correct website: `___`
  - Correct Career: `___`
  - Correct Job list: `___`
  - Correct opening: `___`
  - Disposition: `___`
  - Failure route: `___`
  - Notes: `___`我先搜Software Engineer - Apollo Platform有三十几页，翻起来很难找到这个岗位，但是我把前面的职称名删掉，只留公司名字，一下子就搜出来那个岗位了
- [ ] **11. hackajob** - Registered Nurse Healthcare Advocate (Remote 1099) - RN; United States

  - 自动结果：[LinkedIn](https://www.linkedin.com/jobs/view/registered-nurse-healthcare-advocate-remote-1099-rn-at-hackajob-4431491227) | [Website](https://hackajob.com/) | [Career](https://hackajob.com/jobs) | [Job list](https://hackajob.com/en-us/jobs)
  - 当前分类：pipeline=`partial`; stages=`opening_match:OPENING_DISCOVERY_INCOMPLETE`
  - 三路诊断：External Apply=`no_visible_external_apply_link`; ATS Search=`candidate_not_produced`; Website/Career=`final_exact_not_available`
  - Posting status: `___`
  - Correct website: `___`
  - Correct Career: `___`
  - Correct Job list: `___`
  - Correct opening: `___`
  - Disposition: `___`
  - Failure route: `___`
  - Notes: `___`我直接搜Registered Nurse Healthcare Advocate (Remote 1099) - RN搜不到，但是搜Registered Nurse Healthcare Advocate就搜到了
- [ ] **22. PUMA Group** - Account Executive (Remote - Midwest Chicago); Aurora, CO

  - 自动结果：[LinkedIn](https://www.linkedin.com/jobs/view/account-executive-remote-midwest-chicago-at-puma-group-4400268406) | [Website](https://about.puma.com/en) | [Career](https://about.puma.com/en/careers/job-openings) | [Job list](https://about.puma.com/en/careers/job-openings)
  - 当前分类：pipeline=`partial`; stages=`opening_match:OPENING_DISCOVERY_INCOMPLETE`
  - 三路诊断：External Apply=`no_visible_external_apply_link`; ATS Search=`candidate_not_produced`; Website/Career=`final_exact_not_available`
  - Posting status: `___`
  - Correct website: `___`
  - Correct Career: `___`
  - Correct Job list: `___`
  - Correct opening: `___`
  - Disposition: `___`
  - Failure route: `___`
  - Notes: `___`问题C
- [ ] **31. Bacardi** - National Account Manager - Hotels; New York, NY

  - 自动结果：[LinkedIn](https://www.linkedin.com/jobs/view/national-account-manager-hotels-at-bacardi-4441657690) | [Website](https://www.bacardi.com/) | [Career](https://jobs.bacardilimited.com:443/) | [Job list](https://jobs.bacardilimited.com:443/job-search)
  - 当前分类：pipeline=`partial`; stages=`opening_match:OPENING_DISCOVERY_INCOMPLETE`
  - 三路诊断：External Apply=`no_visible_external_apply_link`; ATS Search=`candidate_not_produced`; Website/Career=`final_exact_not_available`
  - Posting status: `___`
  - Correct website: `___`
  - Correct Career: `___`
  - Correct Job list: `___`
  - Correct opening: `___`
  - Disposition: `___`
  - Failure route: `___`
  - Notes: `___`问题C
- [ ] **42. Snap Inc.** - Product Design Engineer; Los Angeles, CA

  - 自动结果：[LinkedIn](https://www.linkedin.com/jobs/view/product-design-engineer-at-snap-inc-4436142141) | [Website](https://www.snap.com/) | [Career](https://careers.snap.com/) | [Job list](https://careers.snap.com/jobs)
  - 当前分类：pipeline=`partial`; stages=`opening_match:OPENING_DISCOVERY_INCOMPLETE`
  - 三路诊断：External Apply=`no_visible_external_apply_link`; ATS Search=`candidate_not_produced`; Website/Career=`final_exact_not_available`
  - Posting status: `___`
  - Correct website: `___`
  - Correct Career: `___`
  - Correct Job list: `___`
  - Correct opening: `___`
  - Disposition: `___`
  - Failure route: `___`
  - Notes: `___`问题C
- [ ] **43. Bastion Technologies, Inc.** - Mechanical Project Engineer; Houston, TX

  - 自动结果：[LinkedIn](https://www.linkedin.com/jobs/view/mechanical-project-engineer-at-bastion-technologies-inc-4439478675) | [Website](https://bastiontechnologies.com/) | [Career](https://bastiontechnologies.applicantpro.com/jobs/) | [Job list](https://bastiontechnologies.applicantpro.com//jobs/)
  - 当前分类：pipeline=`partial`; stages=`opening_match:OPENING_DISCOVERY_INCOMPLETE`
  - 三路诊断：External Apply=`no_visible_external_apply_link`; ATS Search=`candidate_not_produced`; Website/Career=`final_exact_not_available`
  - Posting status: `___`
  - Correct website: `___`
  - Correct Career: `___`
  - Correct Job list: `___`
  - Correct opening: `___`
  - Disposition: `___`
  - Failure route: `___`
  - Notes: `___`问题C
- [ ] **44. EVONA** - Propulsion Roles (Multiple); Colorado, United States

  - 自动结果：[LinkedIn](https://www.linkedin.com/jobs/view/propulsion-roles-multiple-at-evona-4432708542) | [Website](https://evona.com/) | [Career](https://evona.com/spacejobs/) | [Job list](https://evona.com/spacejobs/)
  - 当前分类：pipeline=`partial`; stages=`opening_match:OPENING_DISCOVERY_INCOMPLETE`
  - 三路诊断：External Apply=`no_visible_external_apply_link`; ATS Search=`candidate_not_produced`; Website/Career=`final_exact_not_available`
  - Posting status: `___`
  - Correct website: `___`
  - Correct Career: `___`
  - Correct Job list: `___`
  - Correct opening: `___`
  - Disposition: `___`
  - Failure route: `___`
  - Notes: `___`官网好像确实没有这个岗位不怪你
- [ ] **72. Randstad USA** - Data Analyst; Malvern, PA

  - 自动结果：[LinkedIn](https://www.linkedin.com/jobs/view/data-analyst-at-randstad-usa-4437690386) | [Website](https://www.randstadusa.com/) | [Career](https://www.randstadusa.com/jobs/) | [Job list](https://www.randstadusa.com/jobs/)
  - 当前分类：pipeline=`partial`; stages=`opening_match:OPENING_DISCOVERY_INCOMPLETE`
  - 三路诊断：External Apply=`no_visible_external_apply_link`; ATS Search=`candidate_not_produced`; Website/Career=`final_exact_not_available`
  - Posting status: `___`
  - Correct website: `___`
  - Correct Career: `___`
  - Correct Job list: `___`
  - Correct opening: `___`
  - Disposition: `___`
  - Failure route: `___`
  - Notes: `___`问题C

### OPENING_NOT_FOUND (8)

- [ ] **1. Sony Interactive Entertainment** - Software Engineer I; Los Angeles Metropolitan Area

  - 自动结果：[LinkedIn](https://www.linkedin.com/jobs/view/software-engineer-i-at-sony-interactive-entertainment-4412034983) | [Website](https://sonyinteractive.com/en/) | [Career](https://careers.playstation.com/?smcid=web%3Asie%3Aus-en%3Ahome%3Aprimary-nav%3Acareers) | [Job list](https://job-boards.greenhouse.io/haven)
  - 当前分类：pipeline=`partial`; stages=`opening_match:OPENING_NOT_FOUND`
  - 三路诊断：External Apply=`no_visible_external_apply_link`; ATS Search=`candidate_not_produced`; Website/Career=`final_exact_not_available`
  - Posting status: `___`
  - Correct website: `___`
  - Correct Career: `___`
  - Correct Job list: `___`
  - Correct opening: `___`
  - Disposition: `___`
  - Failure route: `___`
  - Notes: `___`careerpage对的，但是joblist不知道你怎么找的另外一家公司的，不应该是一级一级点进去，怎么能点到别的公司呢
- [ ] **23. LinkedIn** - SMB Account Executive - Search & Staffing, Talent Solutions; New York, NY

  - 自动结果：[LinkedIn](https://www.linkedin.com/jobs/view/smb-account-executive-search-staffing-talent-solutions-at-linkedin-4441201517) | [Website](https://www.linkedin.com/) | [Career](https://www.linkedin.com/top-content/career/) | [Job list](https://job-boards.greenhouse.io/linkedin)
  - 当前分类：pipeline=`partial`; stages=`opening_match:OPENING_NOT_FOUND`
  - 三路诊断：External Apply=`no_visible_external_apply_link`; ATS Search=`final_exact_not_available`; Website/Career=`candidate_not_produced`
  - Posting status: `___`
  - Correct website: `___`
  - Correct Career: `___`
  - Correct Job list: `___`
  - Correct opening: `___`
  - Disposition: `___`
  - Failure route: `___`
  - Notes: `___`好像greenhouse里确实没有这个岗位，毕竟这是linkedin，大部分人应该就easyapply了
- [ ] **24. LinkedIn** - Account Executive 3, Performance & Expansion, Marketing Solutions; New York, NY

  - 自动结果：[LinkedIn](https://www.linkedin.com/jobs/view/account-executive-3-performance-expansion-marketing-solutions-at-linkedin-4441269292) | [Website](https://www.linkedin.com/) | [Career](https://www.linkedin.com/top-content/career/) | [Job list](https://job-boards.greenhouse.io/linkedin)
  - 当前分类：pipeline=`partial`; stages=`opening_match:OPENING_NOT_FOUND`
  - 三路诊断：External Apply=`no_visible_external_apply_link`; ATS Search=`final_exact_not_available`; Website/Career=`candidate_not_produced`
  - Posting status: `___`
  - Correct website: `___`
  - Correct Career: `___`
  - Correct Job list: `___`
  - Correct opening: `___`
  - Disposition: `___`
  - Failure route: `___`
  - Notes: `___`好像greenhouse里确实没有这个岗位，毕竟这是linkedin，大部分人应该就easyapply了
- [ ] **25. LinkedIn** - Mid-Market Account Executive - Talent & Learning; San Francisco, CA

  - 自动结果：[LinkedIn](https://www.linkedin.com/jobs/view/mid-market-account-executive-talent-learning-at-linkedin-4422966708) | [Website](https://www.linkedin.com/) | [Career](https://www.linkedin.com/top-content/career/) | [Job list](https://job-boards.greenhouse.io/linkedin)
  - 当前分类：pipeline=`partial`; stages=`opening_match:OPENING_NOT_FOUND`
  - 三路诊断：External Apply=`no_visible_external_apply_link`; ATS Search=`final_exact_not_available`; Website/Career=`candidate_not_produced`
  - Posting status: `___`
  - Correct website: `___`
  - Correct Career: `___`
  - Correct Job list: `___`
  - Correct opening: `___`
  - Disposition: `___`
  - Failure route: `___`
  - Notes: `___`好像greenhouse里确实没有这个岗位，毕竟这是linkedin，大部分人应该就easyapply了
- [ ] **27. LinkedIn** - SMB Account Executive - Talent & Learning; San Francisco, CA

  - 自动结果：[LinkedIn](https://www.linkedin.com/jobs/view/smb-account-executive-talent-learning-at-linkedin-4422952933) | [Website](https://www.linkedin.com/) | [Career](https://www.linkedin.com/top-content/career/) | [Job list](https://job-boards.greenhouse.io/linkedin)
  - 当前分类：pipeline=`partial`; stages=`opening_match:OPENING_NOT_FOUND`
  - 三路诊断：External Apply=`no_visible_external_apply_link`; ATS Search=`final_exact_not_available`; Website/Career=`candidate_not_produced`
  - Posting status: `___`
  - Correct website: `___`
  - Correct Career: `___`
  - Correct Job list: `___`
  - Correct opening: `___`
  - Disposition: `___`
  - Failure route: `___`
  - Notes: `___`好像greenhouse里确实没有这个岗位，毕竟这是linkedin，大部分人应该就easyapply了
- [ ] **30. LinkedIn** - Manager, Talent Account Directors; Chicago, IL

  - 自动结果：[LinkedIn](https://www.linkedin.com/jobs/view/manager-talent-account-directors-at-linkedin-4440030712) | [Website](https://www.linkedin.com/) | [Career](https://www.linkedin.com/top-content/career/) | [Job list](https://job-boards.greenhouse.io/linkedin)
  - 当前分类：pipeline=`partial`; stages=`opening_match:OPENING_NOT_FOUND`
  - 三路诊断：External Apply=`no_visible_external_apply_link`; ATS Search=`final_exact_not_available`; Website/Career=`candidate_not_produced`
  - Posting status: `___`
  - Correct website: `___`
  - Correct Career: `___`
  - Correct Job list: `___`
  - Correct opening: `___`
  - Disposition: `___`
  - Failure route: `___`
  - Notes: `___`好像greenhouse里确实没有这个岗位，毕竟这是linkedin，大部分人应该就easyapply了
- [ ] **33. LinkedIn** - Account Executive, Growth Mid-Market - LinkedIn Marketing Solutions; New York, NY

  - 自动结果：[LinkedIn](https://www.linkedin.com/jobs/view/account-executive-growth-mid-market-linkedin-marketing-solutions-at-linkedin-4440026968) | [Website](https://www.linkedin.com/) | [Career](https://www.linkedin.com/top-content/career/) | [Job list](https://job-boards.greenhouse.io/linkedin)
  - 当前分类：pipeline=`partial`; stages=`opening_match:OPENING_NOT_FOUND`
  - 三路诊断：External Apply=`no_visible_external_apply_link`; ATS Search=`final_exact_not_available`; Website/Career=`candidate_not_produced`
  - Posting status: `___`
  - Correct website: `___`
  - Correct Career: `___`
  - Correct Job list: `___`
  - Correct opening: `___`
  - Disposition: `___`
  - Failure route: `___`
  - Notes: `___`好像greenhouse里确实没有这个岗位，毕竟这是linkedin，大部分人应该就easyapply了
- [ ] **45. United Pharma** - Mechanical Engineer; New York, United States

  - 自动结果：[LinkedIn](https://www.linkedin.com/jobs/view/mechanical-engineer-at-united-pharma-4435120007) | [Website](https://unitedpharma.us) | [Career](https://jobs.smartrecruiters.com/unitedpharma) | [Job list](https://jobs.smartrecruiters.com/unitedpharma)
  - 当前分类：pipeline=`partial`; stages=`opening_match:OPENING_NOT_FOUND`
  - 三路诊断：External Apply=`no_visible_external_apply_link`; ATS Search=`final_exact_not_available`; Website/Career=`final_exact_not_available`
  - Posting status: `___`
  - Correct website: `___`
  - Correct Career: `___`
  - Correct Job list: `___`
  - Correct opening: `___`
  - Disposition: `___`
  - Failure route: `___`
  - Notes: `___`好像确实没有这个岗位

### RESULT_IDENTITY_MISMATCH (3)

- [ ] **4. Middesk** - Software Engineer; New York, United States

  - 自动结果：[LinkedIn](https://www.linkedin.com/jobs/view/software-engineer-at-middesk-4205003719) | [Website](https://www.middesk.com:443/) | [Career](https://www.middesk.com:443/careers) | [Job list](https://jobs.ashbyhq.com/middesk)
  - 当前分类：pipeline=`failed`; stages=`result_validation:RESULT_IDENTITY_MISMATCH`
  - 三路诊断：External Apply=`no_visible_external_apply_link`; ATS Search=`final_exact_not_available`; Website/Career=`final_exact_not_available`
  - Posting status: `___`
  - Correct website: `___`
  - Correct Career: `___`
  - Correct Job list: `___`
  - Correct opening: `___`
  - Disposition: `___`
  - Failure route: `___`
  - Notes: `___`问题A
- [ ] **19. Elderwood** - RN - Registered Nurse; Ticonderoga, NY

  - 自动结果：[LinkedIn](https://www.linkedin.com/jobs/view/rn-registered-nurse-at-elderwood-4437294077) | [Website](https://www.elderwood.com/) | [Career](https://www.elderwoodcareers.com/) | Job list: 未找到
  - 当前分类：pipeline=`failed`; stages=`result_validation:RESULT_IDENTITY_MISMATCH`
  - 三路诊断：External Apply=`fetch_failed`; ATS Search=`candidate_not_produced`; Website/Career=`relationship_not_verified`
  - Posting status: `___`
  - Correct website: `___`
  - Correct Career: `___`
  - Correct Job list: `___`
  - Correct opening: `___`
  - Disposition: `___`
  - Failure route: `___`
  - Notes: `___`问题A
- [ ] **50. Crayola** - Sr. Financial Analyst; Easton, PA

  - 自动结果：[LinkedIn](https://www.linkedin.com/jobs/view/sr-financial-analyst-at-crayola-4439457708) | [Website](https://www.crayola.com) | [Career](https://www.crayola.com/company/colorful-careers) | [Job list](https://recruiting2.ultipro.com/HAL1009HLLI/JobBoard/2e074503-bbdc-4597-b2d2-775bb304b40d/)
  - 当前分类：pipeline=`failed`; stages=`result_validation:RESULT_IDENTITY_MISMATCH`
  - 三路诊断：External Apply=`no_visible_external_apply_link`; ATS Search=`candidate_not_produced`; Website/Career=`final_exact_not_available`
  - Posting status: `___`
  - Correct website: `___`
  - Correct Career: `___`
  - Correct Job list: `___`
  - Correct opening: `___`
  - Disposition: `___`
  - Failure route: `___`
  - Notes: `___`问题C

### BOT_PROTECTION (2)

- [ ] **39. Tata Technologies** - Mechanical Design Engineer; Hayward, CA

  - 自动结果：[LinkedIn](https://www.linkedin.com/jobs/view/mechanical-design-engineer-at-tata-technologies-4438687607) | [Website](https://www.tata.com/home-page) | [Career](https://www.tata.com/careers/jobs) | [Job list](https://www.tata.com/careers/jobs/joblisting)
  - 当前分类：pipeline=`partial`; stages=`opening_match:BOT_PROTECTION`
  - 三路诊断：External Apply=`no_visible_external_apply_link`; ATS Search=`candidate_not_produced`; Website/Career=`final_exact_not_available`
  - Posting status: `___`
  - Correct website: `___`
  - Correct Career: `___`
  - Correct Job list: `___`
  - Correct opening: `___`
  - Disposition: `___`
  - Failure route: `___`
  - Notes: `___`好像确实没有这个岗位
- [ ] **40. Tata Technologies** - Mechanical Engineer; Livonia, MI

  - 自动结果：[LinkedIn](https://www.linkedin.com/jobs/view/mechanical-engineer-at-tata-technologies-4410912130) | [Website](https://www.tata.com/home-page) | [Career](https://www.tata.com/careers/jobs) | [Job list](https://www.tata.com/careers/jobs/joblisting)
  - 当前分类：pipeline=`partial`; stages=`opening_match:BOT_PROTECTION`
  - 三路诊断：External Apply=`no_visible_external_apply_link`; ATS Search=`candidate_not_produced`; Website/Career=`final_exact_not_available`
  - Posting status: `___`
  - Correct website: `___`
  - Correct Career: `___`
  - Correct Job list: `___`
  - Correct opening: `___`
  - Disposition: `___`
  - Failure route: `___`
  - Notes: `___`好像确实没有这个岗位

### WEBSITE_NOT_RESOLVED (2)

- [ ] **17. Riverview School** - Registered Nurse - RN- School Nurse; East Sandwich, MA

  - 自动结果：[LinkedIn](https://www.linkedin.com/jobs/view/registered-nurse-rn-school-nurse-at-riverview-school-4441636937) | Website: 未找到 | Career: 未找到 | Job list: 未找到
  - 当前分类：pipeline=`failed`; stages=`website_resolution:WEBSITE_NOT_RESOLVED`
  - 三路诊断：External Apply=`no_visible_external_apply_link`; ATS Search=`candidate_not_produced`; Website/Career=`input_not_covered`
  - Posting status: `___`
  - Correct website: `___`
  - Correct Career: `___`
  - Correct Job list: `___`
  - Correct opening: `___`
  - Disposition: `___`
  - Failure route: `___`
  - Notes: `___`[www.adzuna.com/details/5731111005?v=5C56A43367606B3EF5DDBF18B0DA541624E921F0&amp;ccd=ad43cbab1553ec33dd0faa13273043a0&amp;frd=87f240d07c259a83d16ff26bb295978a&amp;r=22213945&amp;utm_source=linkedin7&amp;utm_medium=organic&amp;chnlid=1931&amp;title=Registered%20Nurse%20-%20RN-%20School%20Nurse&amp;a=e](<https://www.adzuna.com/details/5731111005?v=5C56A43367606B3EF5DDBF18B0DA541624E921F0&ccd=ad43cbab1553ec33dd0faa13273043a0&frd=87f240d07c259a83d16ff26bb295978a&r=22213945&utm_source=linkedin7&utm_medium=organic&chnlid=1931&title=Registered%20Nurse%20-%20RN-%20School%20Nurse&a=e>)这个是他的外链，是没见过的ats吗
- [ ] **70. Lyft** - Data Analyst, Operations Planning; New York, NY

  - 自动结果：[LinkedIn](https://www.linkedin.com/jobs/view/data-analyst-operations-planning-at-lyft-4420633964) | Website: 未找到 | Career: 未找到 | Job list: 未找到
  - 当前分类：pipeline=`failed`; stages=`website_resolution:WEBSITE_NOT_RESOLVED`
  - 三路诊断：External Apply=`no_visible_external_apply_link`; ATS Search=`candidate_not_produced`; Website/Career=`input_not_covered`
  - Posting status: `___`
  - Correct website: `___`
  - Correct Career: `___`
  - Correct Job list: `___`
  - Correct opening: `___`
  - Disposition: `___`
  - Failure route: `___`
  - Notes: `___`403了，可能是我挂了梯子的原因

### COMPANY_TIME_BUDGET_EXHAUSTED (1)

- [ ] **51. Actabl** - Financial Analyst; United States
  - 自动结果：[LinkedIn](https://www.linkedin.com/jobs/view/financial-analyst-at-actabl-4433251930) | [Website](https://actabl.com/) | [Career](https://actabl.com/careers/) | Job list: 未找到
  - 当前分类：pipeline=`failed`; stages=`job_board_discovery:COMPANY_TIME_BUDGET_EXHAUSTED`
  - 三路诊断：External Apply=`no_visible_external_apply_link`; ATS Search=`legacy_route_not_evaluated`; Website/Career=`candidate_not_produced`
  - Posting status: `___`
  - Correct website: `___`
  - Correct Career: `___`
  - Correct Job list: `___`
  - Correct opening: `___`
  - Disposition: `___`
  - Failure route: `___`
  - Notes: `___`问题A

### HTTP_FORBIDDEN (1)

- [ ] **18. Aveanna Healthcare** - Registered Nurse (RN); Southampton, PA
  - 自动结果：[LinkedIn](https://www.linkedin.com/jobs/view/registered-nurse-rn-at-aveanna-healthcare-4439431438) | [Website](https://www.aveanna.com/) | [Career](https://www.aveanna.com/accreditedhomecare-careers.html) | [Job list](https://jobs.aveanna.com/jobs/)
  - 当前分类：pipeline=`partial`; stages=`opening_match:HTTP_FORBIDDEN`
  - 三路诊断：External Apply=`no_visible_external_apply_link`; ATS Search=`candidate_not_produced`; Website/Career=`final_exact_not_available`
  - Posting status: `___`
  - Correct website: `___`
  - Correct Career: `___`
  - Correct Job list: `___`
  - Correct opening: `___`
  - Disposition: `___`
  - Failure route: `___`
  - Notes: `___`You don't have permission to access this resource.应该不是你的问题

### NETWORK_TIMEOUT (1)

- [ ] **5. Leadenhall Search & Selection** - Software Engineer; New York City Metropolitan Area
  - 自动结果：[LinkedIn](https://www.linkedin.com/jobs/view/software-engineer-at-leadenhall-search-selection-4437695387) | [Website](https://leadenhallsearch.com) | [Career](https://leadenhallsearch.com/jobs/) | [Job list](https://leadenhallsearch.com/jobs/)
  - 当前分类：pipeline=`partial`; stages=`opening_match:NETWORK_TIMEOUT`
  - 三路诊断：External Apply=`no_visible_external_apply_link`; ATS Search=`candidate_not_produced`; Website/Career=`final_exact_not_available`
  - Posting status: `___`
  - Correct website: `___`
  - Correct Career: `___`
  - Correct Job list: `___`
  - Correct opening: `___`
  - Disposition: `___`
  - Failure route: `___`
  - Notes: `___`好像确实没有这个岗位

### PROVIDER_VARIANT_UNSUPPORTED (1)

- [ ] **37. OrganOx** - Mechanical Design Engineer; Madison, NJ
  - 自动结果：[LinkedIn](https://www.linkedin.com/jobs/view/mechanical-design-engineer-at-organox-4440989993) | [Website](https://www.organox.com/) | [Career](https://www.organox.com/careers) | [Job list](https://apply.workable.com/organox/)
  - 当前分类：pipeline=`partial`; stages=`opening_match:PROVIDER_VARIANT_UNSUPPORTED`
  - 三路诊断：External Apply=`no_visible_external_apply_link`; ATS Search=`candidate_not_produced`; Website/Career=`final_exact_not_available`
  - Posting status: `___`
  - Correct website: `___`
  - Correct Career: `___`
  - Correct Job list: `___`
  - Correct opening: `___`
  - Disposition: `___`
  - Failure route: `___`
  - Notes: `___`问题C
