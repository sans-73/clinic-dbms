# PROGRESS: your cockpit

**Current phase:** Phase 0 (setup)
**Next task(s):** P0-1 to P0-4, and P1-6 in parallel
**Last tag:** none yet

How to use: after each task passes its "You check", tick it, commit, and paste the AI's HANDOFF NOTE into the log at the bottom.
Status words: `[ ]` not started, `[~]` in progress, `[x]` done and committed.

## Phase 0: Setup
- [x] P0-1 Install tools
- [x] P0-2 GitHub repo
- [x] P0-3 First commit
- [x] P0-4 Start MySQL

## Phase 1: Design
- [x] P1-6 Module 1 theory notes (DOCS)
- [ ] P1-1 ER and Extended ER (ARCHITECT)
- [ ] P1-2 ER review (REVIEWER)
- [ ] P1-3 Table design (ARCHITECT)
- [ ] P1-4 Normalization (ARCHITECT)
- [ ] P1-5 Table design review (REVIEWER)
- [ ] **Gate 1** tag `design-v1`

## Phase 2: Tables
- [ ] P2-1 Cluster 01 core
- [ ] P2-2 Cluster 02 patients and visits
- [ ] P2-3 Cluster 03 pharmacy
- [ ] P2-4 Cluster 04 orders, billing, discharge
- [ ] P2-5 Integration and late FKs
- [ ] P2-6 Schema review
- [ ] **Gate 2** tag `schema-v1`

## Phase 3: Database rules
- [ ] P3-1 Foundation (alone first)
- [ ] P3-2 Patients and visits
- [ ] P3-3 Prescriptions and labs
- [ ] P3-4 Pharmacy
- [ ] P3-5 Billing and discharge
- [ ] P3-6 Owner alerts and family links
- [ ] P3-7 Roles and grants (SECURITY)
- [ ] P3-8 Rule tests (QA-DEMO)
- [ ] **Gate 3** tag `logic-v1`

## Phase 4: Data and performance
- [ ] P4-1 Demo seed
- [ ] P4-2 Bulk generator
- [ ] P4-3 Index benchmarks
- [ ] P4-4 Query showcase
- [ ] B1 (optional) C++ simulators

## Phase 5: Web app
- [ ] P5-1 Skeleton, login, role menu
- [ ] P5-2 Reception
- [ ] P5-3 Doctor
- [ ] P5-4 Pharmacy and nurse
- [ ] P5-5 Billing and discharge
- [ ] P5-6 Owner
- [ ] P5-7 Family link pages

## Phase 6: Proof demos
- [ ] P6-1 Concurrency lab
- [ ] P6-2 Recovery lab
- [ ] P6-3 Security demo
- [ ] P6-4 "A Day at the Clinic" script
- [ ] **Gate 4** tag `demo-ready`

## Phase 7: Docs, deploy, release
- [ ] P7-1 Report, viva, coverage
- [ ] P7-2 Free-hosted demo
- [ ] P7-3 Version 2 preview and future notes
- [ ] P7-4 Traceability audit
- [ ] P7-5 Release, tag `v1.0`

---

## Change requests (CR) log
| # | Date | CR | Decision (applied / rejected) | Files affected |
|---|---|---|---|---|

## Known issues / blockers
- (none yet)

## Handoff log (newest on top; paste each AI's HANDOFF NOTE here)
```
(empty)
```

## Quick prompt for any AI when you are lost
> I am the leader of a clinic DBMS project. Here are PROJECT_CONTEXT.md and PROGRESS.md. Tell me the next unblocked task, which cards I can run in parallel, and what I should check before committing.
