# Benchmark results (Andrea's Laptop)

All folders below were produced on the code that is on main (SPEC 1.1.8).
Use ONLY these rows; folders not listed here are obsolete and not committed.

| Experiment | Folder | Use |
|---|---|---|
| E1, E4, E5 | t1000-datalake | all rows |
| E3 | t1000-py-node | E3 rows only (ignore E6 there) |
| E6 json (Python + Node), E6 sqlite (Node) | t1000-py-node-e6e8 | ignore Python sqlite rows |
| E8 json (Python + Node), E8 sqlite (Node) | t1000-py-node-e8 | ignore Python sqlite rows |
| E6, E8 sqlite (Python) | t1000-py-sqlite | all rows |
| E6, E8 at 100 books (json, sqlite) | t100 | all rows |
| E2, E3-micro, E7 | micro.jsonl, micro-pytest.json | Python only |

Each folder has raw.jsonl (one SPEC §8 record per repetition) and summary.csv
(median, IQR, min, max per configuration). Report medians + IQR.

Still to come (second PR): folder backend at 100 books, sqlite at 10 000 books,
metadata benchmarks.