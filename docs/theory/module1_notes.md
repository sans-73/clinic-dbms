# Module 1 – Introduction to DBMS: Study Notes

Project context: a web-based management system for a single small clinic in India (1–3 doctors, 4–6 staff), covering OPD consultations and short-stay/day-care treatment (max ~24 h). Every topic below has one clinic example.

---

## 1. Historical perspective of DBMS

| Era | Approach | Key idea | Clinic example |
|---|---|---|---|
| 1950s–60s | Flat files, tapes, punch cards | Each program owns its data files | Clinic keeps patient cards in paper registers |
| Late 1960s | Hierarchical (IBM IMS), Network (CODASYL/IDS) | Data linked by physical pointers/trees | Patient → Visits → Prescriptions as a fixed tree |
| 1970 | Relational model (E. F. Codd) | Data as tables; set-based, declarative queries | `patient`, `visit`, `prescription` tables joined by keys |
| 1970s–80s | System R, Ingres, SQL, Oracle, DB2 | SQL becomes standard (ANSI 1986, ISO 1987) | Query "today's queue" with one SQL statement |
| 1980s–90s | Object-oriented / object-relational DBs | Complex types, objects | Storing a scanned report as a typed object |
| 2000s | Web-scale, NoSQL (key-value, document, column, graph) | Scale, flexible schema | Session cache, JSON documents |
| Today | NewSQL, cloud DBs, polyglot persistence | ACID + scale; right tool per job | Clinic uses one relational DB (see §8) |

**Takeaway:** each generation fixed a pain of the previous one (redundancy → pointers → data independence → scale).

---

## 2. File system vs DBMS

| Aspect | File system | DBMS |
|---|---|---|
| Redundancy | High (same patient address in many files) | Controlled via normalization |
| Consistency | Manual, easily broken | Constraints enforce it |
| Data independence | None; programs depend on file layout | Yes (see §5) |
| Concurrent access | Overwrites, lost updates | Transactions + locking |
| Security | OS file permissions only | Per-user, per-object privileges (GRANT/REVOKE), views |
| Recovery | Manual backups | Logs, rollback, crash recovery |
| Querying | New program for each question | Ad-hoc SQL |
| Integrity | In application code, scattered | Declared once in schema |

**Clinic example:** In a spreadsheet, a pharmacist and a receptionist editing the same stock file can silently overwrite each other. In the DBMS, a dispense transaction locks the stock row, deducts quantity, and writes an audit record atomically.

---

## 3. Advantages of using a DBMS

1. **Reduced redundancy and inconsistency** – one patient record, referenced everywhere.
2. **Data sharing** – doctors, nurses, pharmacy and billing use the same data.
3. **Integrity enforcement** – e.g. `CHECK (quantity >= 0)`; a bill cannot reference a missing patient.
4. **Security and separation of duties** – the pharmacist cannot edit prescriptions.
5. **Backup and recovery** – restore the clinic after a crash with no lost payments.
6. **Concurrency control** – two staff can work at once safely.
7. **Data independence** – schema can grow without rewriting every screen.
8. **Accountability** – audit trail of who changed what (core to this project's "trust" goal).
9. **Standard access** – SQL, reports, owner exception alerts.

**Disadvantages (be honest in exams):** cost, complexity, need for a DBA, overhead for very small/simple data, single point of failure if not backed up.

---

## 4. Three-level (ANSI/SPARC) architecture

```
 External level   View 1 (Reception)   View 2 (Pharmacy)   View 3 (Owner reports)
                          \                 |                 /
                    ---- external/conceptual mapping ----
 Conceptual level          Whole-clinic logical schema (tables, keys, constraints)
                                      |
                    ---- conceptual/internal mapping ----
 Internal level            Files, pages, indexes, B+ trees, storage on disk
```

| Level | Describes | Clinic example |
|---|---|---|
| External (view) | What each user group sees | Receptionist sees patient name and phone, but not clinical notes |
| Conceptual | Entire logical design | Tables: Patient, Staff, Visit, Prescription, StockItem, Bill… |
| Internal (physical) | How data is stored | Index on `patient.phone`; table stored in heap file |

**Mappings** translate between levels; they are what makes data independence possible.

---

## 5. Data independence

- **Logical data independence:** change the conceptual schema without changing external views/application programs.
  *Example:* add a column `blood_group` to `patient`; the reception view and screens that don't use it keep working.
- **Physical data independence:** change the internal storage without changing the conceptual schema.
  *Example:* add an index on `visit(visit_date)` or move the DB to another disk; no SQL query changes.
- Physical independence is easier to achieve than logical independence.

---

## 6. Database languages and interfaces

### 6.1 Languages

| Language | Purpose | Typical statements | Clinic example |
|---|---|---|---|
| **DDL** (Data Definition) | Define/alter schema | `CREATE`, `ALTER`, `DROP`, `TRUNCATE` | `CREATE TABLE patient (...)` |
| **DML** (Data Manipulation) | Query and modify data | `SELECT`, `INSERT`, `UPDATE`, `DELETE` | `INSERT INTO visit ...` |
| **DCL** (Data Control) | Permissions | `GRANT`, `REVOKE` | `GRANT SELECT ON stock_view TO pharmacist_role` |
| **TCL** (Transaction Control) | Control transactions | `COMMIT`, `ROLLBACK`, `SAVEPOINT` | Rollback a dispense if stock is insufficient |

Also: **procedural** (DML) vs **non-procedural/declarative** (SQL says *what*, not *how*). Embedded SQL / host-language access is used by the web application.

### 6.2 Interfaces

- Command-line/SQL console (DBA scripts, `db/` scripts in this project)
- Forms-based (reception registration screen)
- Menu/GUI-based (web app screens)
- Report/dashboard (owner audit and alert reports)
- Programmatic API (application server → DB driver)
- Natural-language / admin tools (optional)

---

## 7. DBA and database users

### 7.1 Database Administrator (DBA)
Responsibilities: schema definition, storage and index tuning, user/role management and grants, backup and recovery, monitoring performance, enforcing security and integrity policies.
*Clinic example:* the person who creates roles (`doctor`, `nurse`, `pharmacist`, `receptionist`, `owner`), runs nightly backups and rehearses recovery.

### 7.2 Types of users

| User type | Description | Clinic example |
|---|---|---|
| Naive / parametric end users | Use pre-built forms and screens | Receptionist registers patient; cashier takes payment |
| Casual users | Occasional ad-hoc queries | Owner running a custom report |
| Sophisticated users | Write queries directly (analysts, engineers) | Student/developer testing queries |
| Application programmers | Write programs that use the DB | Developers of the clinic web app |
| Specialized users | Build special applications | Future analytics tools |
| DBA | Administers the DBMS | Described above |

---

## 8. Comparison of data models

A **data model** = structures + operations + constraints for describing data.

| Model | Structure | Relationships | Query style | Strengths | Weaknesses | Fit for the clinic |
|---|---|---|---|---|---|---|
| **Hierarchical** | Tree of parent–child records | 1:N only, via pointers | Navigational | Fast fixed-path access | No M:N, rigid, redundancy | Patient→Visit tree works, but Doctor↔Patient (M:N) does not |
| **Network** | Graph of records and sets | M:N via set types/pointers | Navigational | Flexible links | Complex, pointer-heavy | Possible but hard to maintain |
| **Relational** | Tables (rows/columns) | Foreign keys | Declarative (SQL) | Simple, strong theory, constraints, ACID | Joins cost; object mismatch | **Chosen** – money, medicines and audit need integrity |
| **Document** | JSON-like documents | Embedded/references | API/query on documents | Flexible schema, nested data | Weak joins, weaker integrity | Could hold free-text notes; not for billing |
| **Key-value** | Key → opaque value | None | Get/put by key | Very fast, simple, scalable | No queries on value, no constraints | Session or cache storage only |
| **Graph** | Nodes + edges (+ properties) | First-class edges | Traversal (e.g. Cypher) | Relationship-heavy queries | Less suited to tabular/aggregate work | Family-link exploration (we use read-only family-link table instead) |

**Why relational for this project:** strict integrity, transactions for money and stock, SQL reporting, mature security, and it fits the syllabus.

---

## 9. Protection and security

### 9.1 Goals (CIA + accountability)
- **Confidentiality** – only authorized people see patient data.
- **Integrity** – data cannot be changed improperly or silently.
- **Availability** – clinic can work when needed; recoverable after failure.
- **Accountability** – every change traceable to a person.

### 9.2 Mechanisms

| Mechanism | What it does | Clinic example |
|---|---|---|
| Authentication | Verifies identity (login, password hashing) | Staff log in with individual accounts |
| Authorization (DAC) | `GRANT`/`REVOKE` privileges on objects | Pharmacist: `SELECT, UPDATE` on stock; no access to billing edits |
| Roles | Group privileges by job | `doctor`, `nurse`, `pharmacist`, `receptionist`, `owner` |
| Views | Hide columns/rows | Reception view excludes diagnosis |
| Integrity constraints | Block invalid data | FK from `dispense` to `prescription` |
| Audit trail | Log who did what and when | No silent edits; owner reviews exception reports |
| Encryption | Protects data at rest/in transit | HTTPS; encrypted backups |
| Backup and recovery | Survives failure/disaster | Scheduled backups, tested restore |
| SQL-injection defence | Parameterized queries, input validation, least privilege | Search-by-name box cannot run `'; DROP TABLE...` |

### 9.3 Principles
- **Least privilege:** each role gets only what its job needs.
- **Separation of duties:** the person who prescribes ≠ dispenses ≠ bills ≠ audits.
- **Defence in depth:** several layers (app checks + DB grants + audit).

### 9.4 Other models (awareness)
Mandatory access control (labels/clearances) and role-based access control (RBAC); this project uses RBAC with DAC grants.

---

## 10. Quick revision checklist

- [ ] History: file → hierarchical/network → relational → NoSQL/NewSQL
- [ ] File system vs DBMS table
- [ ] 3-level architecture and mappings
- [ ] Logical vs physical independence
- [ ] DDL / DML / DCL / TCL and interfaces
- [ ] DBA duties and user types
- [ ] Six data models comparison
- [ ] Security goals, mechanisms, least privilege
