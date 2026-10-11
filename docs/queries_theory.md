# Query theory: relational algebra, relational calculus, optimization (P4-4)

Companion of `db/queries/showcase.sql`. Query ids `[Sxx]` match the SQL file. Relation names are the tables of the clinic schema
(abbreviated: P = patient, V = visit, A = visit_doctor_assignment, D = doctor_detail, B = bill, BL = bill_line, PAY = payment,
SB = stock_batch, SM = stock_movement, M = medicine, AL = patient_allergy, LO = lab_order, RP = lab_report, SS = short_stay_episode).

## 0. Notation

| Symbol | Operator | SQL |
|---|---|---|
| σ<sub>c</sub>(R) | selection | `WHERE c` |
| π<sub>A</sub>(R) | projection (duplicates removed) | `SELECT DISTINCT A` |
| R ⋈<sub>c</sub> S | theta/natural join | `JOIN ... ON c` |
| ⟕ ⟖ ⟗ | left / right / full outer join | `LEFT/RIGHT JOIN`, UNION emulation |
| R × S | cartesian product | `CROSS JOIN` |
| R ∪ S, R ∩ S, R − S | union, intersection, difference | `UNION`, `INTERSECT`, `EXCEPT` |
| R ÷ S | division | double `NOT EXISTS` / `GROUP BY ... HAVING COUNT` |
| ρ<sub>x</sub>(R) | rename (alias) | `AS x` |
| γ<sub>G; f(A)</sub>(R) | grouping and aggregation | `GROUP BY G` + aggregate |

SQL works on **bags** (duplicates allowed); the algebra below is the set version unless noted. Aggregation (γ) is an extension of the
basic algebra.

## 1. Eight (plus) queries in algebra and calculus

### [S03] Patients who never had a visit (difference / anti-join)

* Algebra: π<sub>patient_id</sub>(P) − π<sub>patient_id</sub>(V), then join back to P to show details:
  `P ⋈ (π_patient_id(P) − π_patient_id(V))`
* TRC: `{ p | P(p) ∧ ¬∃v ( V(v) ∧ v.patient_id = p.patient_id ) }`
* DRC: `{ ⟨id, code, fam⟩ | ∃g,d,… P(id, code, g, fam, d, …) ∧ ¬∃vid,t,… V(vid, id, t, …) }`
* SQL form: `LEFT JOIN ... WHERE v.visit_id IS NULL` (NULL = "no match", the dangling-tuple case of Module 4).

### [S02] Visit with patient and attending doctor (multi-way join)

* Algebra: π<sub>visit_id, occurred_at, name(P), name(S)</sub>( V ⋈ P ⋈ σ<sub>role='attending'</sub>(A) ⋈ ρ<sub>S</sub>(Staff) )
* TRC: `{ ⟨v.visit_id, p.family_name, s.family_name⟩ | V(v) ∧ P(p) ∧ A(a) ∧ Staff(s) ∧ v.patient_id = p.patient_id ∧ a.visit_id = v.visit_id ∧ a.assignment_role = 'attending' ∧ a.doctor_staff_id = s.staff_id }`
* Optimization note: the selection on `assignment_role` is pushed down before the join (see §3, rule 1).

### [S16] Patients with at least one allergy (EXISTS = semi-join)

* Algebra: `P ⋉ AL` (semi-join) = π<sub>attrs(P)</sub>( P ⋈ AL )
* TRC: `{ p | P(p) ∧ ∃a ( AL(a) ∧ a.patient_id = p.patient_id ) }`
* Remark: `EXISTS` is the direct SQL image of the existential quantifier ∃ of the calculus.

### [S25] Lab order AND short stay (intersection)

* Algebra: π<sub>patient_id</sub>(V ⋈ LO) ∩ π<sub>patient_id</sub>(V ⋈ SS)
* TRC: `{ x | ∃v1,l ( V(v1) ∧ LO(l) ∧ l.visit_id = v1.visit_id ∧ v1.patient_id = x ) ∧ ∃v2,s ( V(v2) ∧ SS(s) ∧ s.visit_id = v2.visit_id ∧ v2.patient_id = x ) }`
* SQL: `INTERSECT` (MySQL 8.0.31+). Equivalent to `WHERE x IN (...) AND x IN (...)`.

### [S26] Visited but never paid (difference, reconciliation R2.4)

* Algebra: π<sub>patient_id</sub>(V) − π<sub>patient_id</sub>(V ⋈ B ⋈ PAY)
* TRC: `{ x | ∃v ( V(v) ∧ v.patient_id = x ) ∧ ¬∃v,b,y ( V(v) ∧ B(b) ∧ PAY(y) ∧ v.patient_id = x ∧ b.visit_id = v.visit_id ∧ y.bill_id = b.bill_id ) }`
* SQL: `EXCEPT`.

### [S27] Patients seen by EVERY doctor (division)

Let `Seen = π_patient_id, doctor_staff_id( V ⋈ A )` and `Doc = π_staff_id( D )` renamed to `doctor_staff_id`.

* Algebra: `Seen ÷ Doc`
* Expanded without ÷: `π_patient_id(P) − π_patient_id( ( π_patient_id(P) × Doc ) − Seen )`
  (all candidate pairs minus the pairs that really happened = patients missing at least one doctor; subtract them from all patients)
* TRC: `{ p | P(p) ∧ ∀d ( D(d) → ∃v,a ( V(v) ∧ A(a) ∧ v.patient_id = p.patient_id ∧ a.visit_id = v.visit_id ∧ a.doctor_staff_id = d.staff_id ) ) }`
* Because SQL has no ∀, `∀d (X)` is rewritten as `¬∃d ( ¬X )`, giving the double `NOT EXISTS` of [S27].
* The same idea in counting form is [S28] (`HAVING COUNT(DISTINCT dosage_form) = (SELECT COUNT(DISTINCT dosage_form) ...)`),
  valid because every counted dosage form is guaranteed to exist in the catalog.

### [S11] Visits per doctor (grouping/aggregation)

* Algebra: γ<sub>doctor_staff_id; COUNT(*) → visits</sub>( σ<sub>role='attending'</sub>(A) )
* Calculus: pure TRC/DRC cannot count; aggregation is an extension (this is the standard "completeness" gap, see §2).

### [S12] Patients who came more than once (grouping + selection on aggregate)

* Algebra: σ<sub>visits &gt; 1</sub>( γ<sub>patient_id; COUNT(*) → visits</sub>( V ) ) , then join to P
* SQL: `GROUP BY ... HAVING COUNT(*) > 1`. `HAVING` is σ applied **after** γ, `WHERE` is σ applied **before** it.

### [S05] Full outer join (appointments and visits)

* Algebra: `Appt ⟗ V = (Appt ⟕ V) ∪ (Appt ⟖ V)`; MySQL has no `FULL JOIN`, so the SQL uses `UNION` of the left and right outer joins, which also removes the duplicate matched rows.

### [S21] Current status of a visit (derived value, G3)

* Algebra: `H = visit_status_history`. Latest row per visit = `H − π_{H.*}( H ⋈_{H.visit_id = H'.visit_id ∧ H.status_seq < H'.status_seq} ρ_{H'}(H) )`
  (rows for which no later row exists).
* TRC: `{ h | H(h) ∧ ¬∃h' ( H(h') ∧ h'.visit_id = h.visit_id ∧ h'.status_seq > h.status_seq ) }`
* Design consequence: because the trail is append-only and the *state* is a query, there is no editable "current status" column to corrupt.

## 2. Is SQL relationally complete?

* **Codd's theorem:** relational algebra and *safe* tuple relational calculus have the same expressive power. A language is **relationally
  complete** if it can express every algebra query.
* SQL's `SELECT-FROM-WHERE` expresses σ, π, ×, ⋈; `UNION`/`INTERSECT`/`EXCEPT` give ∪, ∩, −; aliases give ρ. So SQL ⊇ relational algebra:
  it is relationally complete. Division is expressible through `NOT EXISTS` ([S27]).
* SQL is **more** than the basic calculus: aggregation (`COUNT`, `SUM`, `GROUP BY`), arithmetic, sorting, window functions, and recursion
  (`WITH RECURSIVE`, [S36] [S37]) have no counterpart in the basic algebra/calculus. Recursion (transitive closure) is the classic query that
  basic relational algebra **cannot** express.
* **Safety:** an unsafe calculus expression such as `{ t | ¬P(t) }` would be infinite. SQL avoids this because every variable ranges over a
  stored table (`FROM`).
* **NULL and 3-valued logic** (Module 4): comparisons with NULL yield UNKNOWN. That is why [S03] uses `IS NULL` and why
  `NOT IN (subquery with NULL)` is dangerous, whereas `NOT EXISTS` ([S17], [S27]) is safe.

## 3. Query optimization (how MySQL runs these)

Pipeline: parse → logical plan (algebra tree) → algebraic rewrites → cost-based choice of join order and access paths → execution.

| Rule | Idea | Where you can see it in the project |
|---|---|---|
| 1. Push selections down | filter early so joins see fewer rows | [S02]: `role='attending'` is applied to A before joining |
| 2. Push projections down | carry only needed columns | covering indexes, e.g. `bx_sm_batch_qty` in benchmark Q5 |
| 3. Join order by cost | smallest intermediate result first | `EXPLAIN` of [S08] lists the tables in the chosen order |
| 4. Choose access path | full scan vs B+ tree lookup vs range scan | `type` column of `EXPLAIN`: `ALL`, `ref`, `range`, `fulltext` |
| 5. Sub-query to join | `IN`/`EXISTS` become semi-joins | [S16] shows `FirstMatch` / `semijoin` in `EXPLAIN FORMAT=TREE` |
| 6. Sargable predicates | an index is usable only if the column is not wrapped in a function | benchmark Q3: `SOUNDEX(family_name)` cannot use an index, so the plan narrows by `date_of_birth` first |

Cost model: estimated rows × per-row cost, from table statistics (`ANALYZE TABLE` refreshes them; the benchmark runner calls it).
Measured before/after results with real numbers are generated into `docs/performance/benchmarks.md` (run `python tools/run_benchmarks.py`);
the structures behind them are explained in `docs/performance/index_structures.md`.

How to look at a plan yourself:

```sql
EXPLAIN FORMAT=TREE SELECT ... ;       -- shape of the plan
EXPLAIN ANALYZE   SELECT ... ;         -- really runs it, shows actual rows and time per step
```
