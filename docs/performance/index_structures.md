# Index structures used in the clinic database (Module 6 notes)

Companion of `benchmarks.md` (numbers) and `bonus/` (C++ simulators that you can run and watch).

## 1. How InnoDB stores a table

* A table **is** its primary-key B+ tree: the **clustered index**. Leaf pages hold the full rows, sorted by primary key.
  `patient` is stored in `patient_id` order; a lookup `WHERE patient_id = 500` walks root → internal → leaf (3-4 page reads for 100k rows).
* A **secondary index** is another B+ tree whose leaves hold `(indexed columns, primary key)`. A lookup by secondary key finds the primary key,
  then does one more clustered-index lookup to fetch the row ("bookmark lookup"). If the index already contains every column the query needs
  it is a **covering index** and the second step disappears (benchmark Q5, `bx_sm_batch_qty`).
* This is file organization of Module 6: heap/sequential files versus **index-organized** files. InnoDB is index-organized.

```
                 [ root page:   keys 40 | 80 ]
                 /            |             \
        [ 10 | 25 ]     [ 50 | 65 ]      [ 90 | 120 ]        internal pages (multi-level index)
        /    |    \      /   |   \        /    |    \
      leaf ↔ leaf ↔ leaf ↔ leaf ↔ leaf ↔ leaf ↔ leaf ↔ ...   leaves are linked: range scans walk sideways
```

## 2. Index kinds and where the project uses them

| Kind (syllabus) | Meaning | In our schema / benchmark |
|---|---|---|
| Primary (clustered) index | file sorted on the key | every `<table>_id` |
| Secondary (non-clustering) index | extra sorted structure on a non-key column | Q1 `(family_name, given_name)`, Q2 `phone_number`, Q3 `date_of_birth` |
| Composite index | key = several columns, leftmost-prefix rule | Q6 `(medicine_id, expiry_date)`: equality on the first, order/range on the second |
| Multi-level index | index on the index, so the root stays in memory | the internal pages of every B+ tree above |
| Covering index | answers the query from the index alone | Q5 `(stock_batch_id, quantity_delta)` |
| Unique index | B+ tree that also enforces uniqueness | `request_uuid`, `token_hash`, `display_code` |
| Hash index | `h(key)` picks a bucket: O(1) equality, no ranges, no ordering | Q10 `MEMORY` table `USING HASH` (InnoDB itself builds an *adaptive hash index* in memory automatically) |
| Inverted (FULLTEXT) index | word → list of rows | Q9 medicine type-ahead |
| Partitioning | table split into ranges by a column | Q11 audit table by month; the optimizer reads only the matching partitions ("pruning") |

## 3. When is a B+ tree used? (the leftmost-prefix rule)

For `INDEX (a, b, c)`:

* usable: `a = ?`; `a = ? AND b = ?`; `a = ? AND b > ?`; `a = ? ORDER BY b`
* not usable: `b = ?` alone; `a > ? AND b = ?` for the `b` part; any predicate that wraps the column in a function (`SOUNDEX(a) = ?`, `DATE(a) = ?`).

## 4. Hashing in the syllabus, and the C++ simulators

* **Static hashing:** fixed number of buckets, overflow chains when a bucket fills: lookups degrade as data grows.
* **Dynamic hashing, extendible:** a directory of 2<sup>d</sup> pointers indexed by the first/last *d* bits of the hash ("global depth").
  A full bucket splits; only if its *local depth* equals the global depth does the directory double. `bonus/extendible_hash.cpp` prints the
  directory after every insert so you can watch this happen with patient numbers.
* **B+ tree:** `bonus/bplus_tree.cpp` inserts patient codes (`CLN1-000123`), splits nodes, prints each level and does a range scan, the
  operation hashing cannot do.

Compare in one line: **hash = fastest equality only; B+ tree = equality + range + ordering, always balanced, so MySQL uses it by default.**

## 5. Cost of indexes (why not index everything)

Every insert/update must also update each secondary index; event tables of this project are append-only, so the write cost is only inserts, which
B+ trees handle well when keys grow (ids, timestamps). Index only the queries in `benchmark_queries.sql` and the access paths the screens use.
