#!/usr/bin/env python3
"""run_benchmarks.py - P4-3: measures every query in db/queries/benchmark_queries.sql BEFORE and AFTER its index.

    python tools/run_benchmarks.py --check      # only EXPLAIN every query (finds wrong column names in seconds)
    python tools/run_benchmarks.py              # full run  -> docs/performance/benchmarks.md, results.json, img/*.png
    python tools/run_benchmarks.py --runs 9 --only Q1,Q4

Needs clinic_bench filled by tools/gen_bulk_data.py. Never touches clinic_db.
"""
from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from p4_lib import ROOT, connect  # noqa: E402

DB = "clinic_bench"
SQL_FILE = ROOT / "db" / "queries" / "benchmark_queries.sql"
OUT_MD = ROOT / "docs" / "performance" / "benchmarks.md"
OUT_JSON = ROOT / "docs" / "performance" / "results.json"
IMG = ROOT / "docs" / "performance" / "img"
ANALYZE = ["patient", "patient_phone", "visit", "visit_status_history", "stock_batch", "stock_movement", "medicine",
           "payment", "audit_log", "family_link"]


def parse(text: str):
    setup, blocks, cur = [], [], None
    buf, mode = [], "before"
    for line in text.splitlines():
        s = line.strip()
        m = re.match(r"-- @query\s+(\S+)\s*\|\s*(.*?)\s*\|\s*(.*)$", s)
        if m:
            cur = dict(id=m[1], title=m[2], structure=m[3], drops=[], indexes=[], special=None, before=[], after=[])
            mode = "before"
            continue
        if cur is None:
            if s and not s.startswith("--") and not s.upper().startswith("USE "):
                setup.append(line)
            continue
        if s == "-- @end":
            cur["before"] = "\n".join(cur["before"]).strip().rstrip(";")
            cur["after"] = "\n".join(cur["after"]).strip().rstrip(";") or cur["before"]
            blocks.append(cur)
            cur = None
            continue
        if s.startswith("-- @drop "):
            cur["drops"].append(s[9:].strip())
        elif s.startswith("-- @index "):
            cur["indexes"].append(s[10:].strip())
        elif s.startswith("-- @special "):
            cur["special"] = s[12:].strip()
        elif s == "-- @after":
            mode = "after"
        elif s and not s.startswith("--"):
            cur[mode].append(line)
    stmts = [x.strip() for x in "\n".join(setup).split(";") if x.strip()]
    return stmts, blocks


def build_partition_copy(conn):
    cur = conn.cursor()
    cur.execute("DROP TABLE IF EXISTS bx_audit_part")
    cur.execute("CREATE TABLE bx_audit_part LIKE audit_log")
    cur.execute("SELECT DISTINCT INDEX_NAME FROM information_schema.STATISTICS WHERE TABLE_SCHEMA=%s AND TABLE_NAME='bx_audit_part' "
                "AND NON_UNIQUE=0 AND INDEX_NAME<>'PRIMARY'", (DB,))
    for (n,) in cur.fetchall():
        cur.execute(f"ALTER TABLE bx_audit_part DROP INDEX `{n}`")
    cur.execute("SELECT COLUMN_NAME FROM information_schema.STATISTICS WHERE TABLE_SCHEMA=%s AND TABLE_NAME='bx_audit_part' "
                "AND INDEX_NAME='PRIMARY' ORDER BY SEQ_IN_INDEX", (DB,))
    pk = [r[0] for r in cur.fetchall()]
    cur.execute("SELECT DATA_TYPE FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=%s AND TABLE_NAME='audit_log' AND COLUMN_NAME='recorded_at'", (DB,))
    dtype = cur.fetchone()[0].lower()
    cur.execute("SELECT DATE_FORMAT(MIN(recorded_at),'%Y-%m-01'), DATE_FORMAT(MAX(recorded_at),'%Y-%m-01') FROM audit_log")
    lo, hi = cur.fetchone()
    lo, hi = datetime.strptime(str(lo)[:10], "%Y-%m-%d"), datetime.strptime(str(hi)[:10], "%Y-%m-%d")
    months, d = [], lo
    while d <= hi:
        nxt = datetime(d.year + (d.month == 12), d.month % 12 + 1, 1)
        months.append((d, nxt))
        d = nxt
    if dtype == "timestamp":
        parts = ",".join(f"PARTITION p{a:%Y%m} VALUES LESS THAN (UNIX_TIMESTAMP('{b:%Y-%m-%d} 00:00:00'))" for a, b in months)
        spec = f"PARTITION BY RANGE (UNIX_TIMESTAMP(recorded_at)) ({parts}, PARTITION pmax VALUES LESS THAN MAXVALUE)"
    else:
        parts = ",".join(f"PARTITION p{a:%Y%m} VALUES LESS THAN ('{b:%Y-%m-%d}')" for a, b in months)
        spec = f"PARTITION BY RANGE COLUMNS (recorded_at) ({parts}, PARTITION pmax VALUES LESS THAN (MAXVALUE))"
    newpk = pk + (["recorded_at"] if "recorded_at" not in pk else [])
    cur.execute(f"ALTER TABLE bx_audit_part DROP PRIMARY KEY, ADD PRIMARY KEY ({','.join(newpk)}) {spec}")
    cur.execute("SELECT COLUMN_NAME FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=%s AND TABLE_NAME='audit_log' "
                "AND EXTRA NOT LIKE '%%GENERATED%%' ORDER BY ORDINAL_POSITION", (DB,))
    cols = ",".join(f"`{r[0]}`" for r in cur.fetchall())
    cur.execute(f"INSERT INTO bx_audit_part ({cols}) SELECT {cols} FROM audit_log")
    conn.commit()


def explain_rows(cur, sql):
    cur.execute("EXPLAIN " + sql)
    names = [c[0].lower() for c in cur.description]
    rows = [dict(zip(names, r)) for r in cur.fetchall()]
    return rows


def summarize(rows):
    parts, total = [], 0
    for r in rows:
        key = r.get("key") or "-"
        parts.append(f"{r.get('table')}: {r.get('type')}/{key}/~{r.get('rows')} rows" + (f" [parts {r['partitions']}]" if r.get("partitions") else ""))
        total += int(r.get("rows") or 0)
    return "<br>".join(parts), total


def timed(cur, sql, runs):
    cur.execute(sql)
    cur.fetchall()                                    # warm-up
    ts = []
    for _ in range(runs):
        t = time.perf_counter()
        cur.execute(sql)
        cur.fetchall()
        ts.append((time.perf_counter() - t) * 1000)
    return statistics.median(ts), min(ts)


def tree(cur, sql):
    try:
        cur.execute("EXPLAIN ANALYZE " + sql)
        return "\n".join(r[0] for r in cur.fetchall())
    except Exception as e:
        return f"(EXPLAIN ANALYZE failed: {e})"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--runs", type=int, default=7)
    ap.add_argument("--only", default="")
    a = ap.parse_args()
    setup, blocks = parse(SQL_FILE.read_text(encoding="utf-8"))
    if a.only:
        want = set(a.only.split(","))
        blocks = [b for b in blocks if b["id"] in want]
    conn = connect(DB)
    cur = conn.cursor()
    cur.execute("SET time_zone='+00:00'")
    for s in setup:
        cur.execute(s)
    cur.execute("SELECT VERSION()")
    version = cur.fetchone()[0]
    if a.check:
        bad = 0
        for b in blocks:
            for label, sql in (("before", b["before"]), ("after", b["after"])):
                if label == "after" and b["special"] == "partition":
                    continue                                    # partition table does not exist yet
                if label == "after" and b["after"] != b["before"] and b["id"] in ("Q9", "Q10"):
                    continue                                    # needs the index/table first
                try:
                    explain_rows(cur, sql)
                    print(f"OK    {b['id']} {label}")
                except Exception as e:
                    bad += 1
                    print(f"ERROR {b['id']} {label}: {e}")
        print("\nAll queries explain fine." if not bad else f"\n{bad} problem(s): fix the column names in db/queries/benchmark_queries.sql")
        return
    results = []
    for b in blocks:
        print(f"== {b['id']} {b['title']}")
        if b["special"] == "partition":
            print("   building partitioned copy (takes a minute) ...")
            build_partition_copy(conn)
        for d in b["drops"]:
            try:
                cur.execute(d)
            except Exception:
                pass
        for t in ANALYZE:
            cur.execute(f"ANALYZE TABLE `{t}`")
            cur.fetchall()
        r = dict(id=b["id"], title=b["title"], structure=b["structure"], sql_before=b["before"], sql_after=b["after"], indexes=b["indexes"])
        r["before_ms"], r["before_min"] = timed(cur, b["before"], a.runs)
        rows = explain_rows(cur, b["before"])
        r["before_access"], r["before_rows"] = summarize(rows)
        r["before_tree"] = tree(cur, b["before"])
        t = time.perf_counter()
        for ddl in b["indexes"]:
            cur.execute(ddl)
        r["index_build_s"] = round(time.perf_counter() - t, 2)
        for t_ in ANALYZE + (["bx_audit_part"] if b["special"] == "partition" else []):
            cur.execute(f"ANALYZE TABLE `{t_}`")
            cur.fetchall()
        r["after_ms"], r["after_min"] = timed(cur, b["after"], a.runs)
        rows = explain_rows(cur, b["after"])
        r["after_access"], r["after_rows"] = summarize(rows)
        r["after_tree"] = tree(cur, b["after"])
        r["speedup"] = round(r["before_ms"] / max(r["after_ms"], 0.001), 1)
        print(f"   before {r['before_ms']:.2f} ms  ->  after {r['after_ms']:.2f} ms   (x{r['speedup']})")
        results.append(r)
    cur.execute("SELECT table_name, table_rows FROM information_schema.tables WHERE table_schema=%s AND table_rows > 1000 ORDER BY table_rows DESC", (DB,))
    sizes = cur.fetchall()
    OUT_JSON.write_text(json.dumps(dict(version=version, when=str(datetime.utcnow()), runs=a.runs, results=results, sizes=sizes), indent=1, default=str))
    charts(results)
    write_md(results, version, sizes, a.runs)
    print(f"\nWrote {OUT_MD.relative_to(ROOT)}, {OUT_JSON.relative_to(ROOT)}, {IMG.relative_to(ROOT)}/*.png")


def charts(results):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    IMG.mkdir(parents=True, exist_ok=True)
    ids = [r["id"] for r in results]
    for key, fname, title, ylabel in (("ms", "bench_time.png", "Query time before vs after index (median ms, log scale)", "milliseconds"),
                                      ("rows", "bench_rows.png", "Rows the optimizer expects to read (EXPLAIN, log scale)", "rows")):
        b = [max(r[f"before_{key}"], 0.01) for r in results]
        a_ = [max(r[f"after_{key}"], 0.01) for r in results]
        x = range(len(ids))
        fig, ax = plt.subplots(figsize=(11, 4.5))
        ax.bar([i - 0.2 for i in x], b, 0.4, label="before", color="#c0504d")
        ax.bar([i + 0.2 for i in x], a_, 0.4, label="after", color="#4f81bd")
        ax.set_yscale("log")
        ax.set_xticks(list(x))
        ax.set_xticklabels(ids)
        ax.set_title(title)
        ax.set_ylabel(ylabel)
        ax.legend()
        fig.tight_layout()
        fig.savefig(IMG / fname, dpi=120)
        plt.close(fig)


def write_md(results, version, sizes, runs):
    L = ["# Index benchmarks (P4-3)", "",
         f"Generated by `tools/run_benchmarks.py` on {datetime.utcnow():%Y-%m-%d %H:%M} UTC | MySQL {version} | median of {runs} runs after 1 warm-up.",
         "Data: `clinic_bench` (synthetic, built by `tools/gen_bulk_data.py`). Structures are explained in [index_structures.md](index_structures.md).", "",
         "Largest tables: " + ", ".join(f"`{t}` ~{int(n):,}" for t, n in sizes[:8]), "",
         "![time](img/bench_time.png)", "", "![rows](img/bench_rows.png)", "",
         "| Query | What | Before ms | After ms | Speed-up | Access before | Access after |", "|---|---|---:|---:|---:|---|---|"]
    for r in results:
        L.append(f"| {r['id']} | {r['title']} | {r['before_ms']:.2f} | {r['after_ms']:.2f} | x{r['speedup']} | {r['before_access']} | {r['after_access']} |")
    L += ["", "Reading guide: `type` ALL = full table scan, `ref`/`range` = B+ tree lookup/range, `fulltext` = inverted index. "
          "A speed-up near x1 means the real schema already had a suitable index (FK indexes often do) - that is a finding, not a failure.", ""]
    for r in results:
        L += [f"## {r['id']} - {r['title']}", "", f"**Structure shown:** {r['structure']}", "",
              "Index / DDL applied between the runs:", "```sql", *(r["indexes"] or ["-- (none: the partitioned copy is built by the runner)"]), "```",
              f"Index build time: {r.get('index_build_s', 0)} s.", "", "Query (before):", "```sql", r["sql_before"], "```"]
        if r["sql_after"] != r["sql_before"]:
            L += ["Query (after - rewritten to use the new structure):", "```sql", r["sql_after"], "```"]
        L += ["EXPLAIN ANALYZE before:", "```", "\n".join(r["before_tree"].splitlines()[:14]), "```",
              "EXPLAIN ANALYZE after:", "```", "\n".join(r["after_tree"].splitlines()[:14]), "```", ""]
    OUT_MD.write_text("\n".join(L), encoding="utf-8")


if __name__ == "__main__":
    main()
