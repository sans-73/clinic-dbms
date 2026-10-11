# bonus/ - C++ index simulators (B1, Module 6)

```bash
g++ -std=c++17 bonus/bplus_tree.cpp -o bplus && ./bplus          # B+ tree: inserts, splits, levels, point search, range scan
g++ -std=c++17 bonus/extendible_hash.cpp -o ehash && ./ehash     # extendible hashing: directory doubling, bucket splits
```
(Windows Git Bash: use `./bplus.exe` if needed; needs a C++17 compiler, e.g. MinGW-w64 or MSYS2 g++.)

| Program | Shows | MySQL / syllabus link |
|---|---|---|
| `bplus_tree.cpp` | keys are patient codes `CLN1-000123`; every insert prints all levels; leaf chain used by a range scan; page reads per lookup = tree height | InnoDB clustered/secondary indexes, `WHERE display_code BETWEEN ...` |
| `extendible_hash.cpp` | keys are patient numbers; directory of 2^d entries, bucket local depth, directory doubling | dynamic hashing; MEMORY engine `USING HASH` (benchmark Q10) |

Change `ORDER` / `BUCKET_SIZE` at the top of each file and re-run to see how heights and splits change. Theory: `docs/performance/index_structures.md`.
