# Task order v2 (Track A / Track B split)

Original TASKS.md order is unchanged; only ownership and sequencing changed so both of us work at the same time.

| Task | Owner | State |
|---|---|---|
| P0-1..P0-4, P1-6 | Track A | done |
| P1-1 ER model | Track A | done (rev 2) |
| P1-2 ER review, P1-3 table design, P1-4 normalization, P1-5 review, Gate 1 | Track A | in progress |
| P2-1..P2-5 schema + late FKs | Track B | generated (MySQL run pending) |
| P2-6 schema review | Track B + you | verify_kit passes; run on MySQL pending |
| P3-1 foundation | Track B | draft ready |
| P3-2 patients/visits | Track B | draft ready |
| P3-3 .. P3-7 | Track B | batches 3-4 |
| P3-8 rule tests | Track B | 47 tests for P3-1/P3-2 ready; more per batch |
| P4, P5, P6, P7, B1 | Track B | batches 5-7 |

Gates: Gate 1 needs your Phase-1 closure; Gate 2 needs `bash tools/run_sql_checked.sh` with zero errors on your machine.
