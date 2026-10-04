#!/usr/bin/env python3
"""
Build results/summary.csv from ONLY the benchmark rows approved in
results/results.md.

The repository contains some superseded runs. This script deliberately uses
an explicit whitelist so stale rows cannot leak into the report.

Run from the repository root:
    python src/benchmark/aggregate.py --results results
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd


BASE_COLUMNS = [
    "experiment", "metric", "unit", "language", "layout", "backend",
    "corpus_size", "workers", "n", "median", "q1", "q3", "iqr",
    "min", "max", "median_peak_rss_mib", "cache",
    "batch_size", "p95", "workload", "layer", "source",
]


def read_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        print(f"WARN missing {path}")
        return pd.DataFrame()
    df = pd.read_csv(path)
    if "layout" not in df.columns:
        df["layout"] = ""
    if "backend" not in df.columns:
        df["backend"] = ""
    if "batch_size" not in df.columns:
        df["batch_size"] = pd.NA
    df["source"] = path.as_posix()
    df["layer"] = "macro"
    if "p95" not in df.columns:
        df["p95"] = pd.NA
    if "workload" not in df.columns:
        df["workload"] = ""
    return df


def select(df: pd.DataFrame, mask) -> pd.DataFrame:
    if df.empty:
        return df
    return df.loc[mask].copy()


def approved_macro(results: Path) -> list[pd.DataFrame]:
    parts: list[pd.DataFrame] = []

    # E1/E4/E5 — Python + Node
    d = read_csv(results / "t1000-datalake" / "summary.csv")
    if not d.empty:
        parts.append(select(d, d["experiment"].isin(["E1", "E4", "E5"])))

    # E1/E3/E4 — Go. E5 is byte-identical and already represented above.
    d = read_csv(results / "t1000-go" / "summary.csv")
    if not d.empty:
        parts.append(select(d, d["experiment"].isin(["E1", "E3", "E4"])))

    # E3 — Python + Node only from this folder.
    d = read_csv(results / "t1000-py-node" / "summary.csv")
    if not d.empty:
        parts.append(select(d, d["experiment"] == "E3"))

    # E6, 1000 books:
    # Python JSON + Node JSON/SQLite. Python SQLite here is obsolete.
    d = read_csv(results / "t1000-py-node-e6e8" / "summary.csv")
    if not d.empty:
        m = (
            (d["experiment"] == "E6")
            & (
                ((d["language"] == "python") & (d["backend"] == "json"))
                | ((d["language"] == "node") & d["backend"].isin(["json", "sqlite"]))
            )
        )
        parts.append(select(d, m))

    # E8, 1000 books:
    # Python JSON + Node JSON/SQLite. Python SQLite here is obsolete.
    d = read_csv(results / "t1000-py-node-e8" / "summary.csv")
    if not d.empty:
        m = (
            (d["experiment"] == "E8")
            & (
                ((d["language"] == "python") & (d["backend"] == "json"))
                | ((d["language"] == "node") & d["backend"].isin(["json", "sqlite"]))
            )
        )
        parts.append(select(d, m))

    # Authoritative Python SQLite E6/E8.
    d = read_csv(results / "t1000-py-sqlite" / "summary.csv")
    if not d.empty:
        m = (
            (d["language"] == "python")
            & (d["backend"] == "sqlite")
            & d["experiment"].isin(["E6", "E8"])
        )
        parts.append(select(d, m))

    # Go E6/E8, JSON + SQLite, 1000 books.
    d = read_csv(results / "t1000-go-e6e8" / "summary.csv")
    if not d.empty:
        m = (
            (d["language"] == "go")
            & d["backend"].isin(["json", "sqlite"])
            & d["experiment"].isin(["E6", "E8"])
        )
        parts.append(select(d, m))

    # 100-book E6/E8, JSON + SQLite, Python + Node.
    d = read_csv(results / "t100" / "summary.csv")
    if not d.empty:
        parts.append(select(d, d["experiment"].isin(["E6", "E8"])))

    # Folder backend, 100 books, Python + Node.
    d = read_csv(results / "t100-folder" / "summary.csv")
    if not d.empty:
        parts.append(select(d, d["experiment"].isin(["E6", "E8"])))

    # Folder backend, 100 books, Go.
    d = read_csv(results / "t100-folder-go" / "summary.csv")
    if not d.empty:
        parts.append(select(d, d["experiment"].isin(["E6", "E8"])))

    # Supplementary metadata datamart benchmark.
    d = read_csv(results / "t-meta" / "summary.csv")
    if not d.empty:
        parts.append(select(d, d["experiment"] == "E12"))

    return [p for p in parts if not p.empty]


def micro_rows(path: Path) -> pd.DataFrame:
    if not path.exists():
        print(f"WARN missing {path}")
        return pd.DataFrame()

    rows = []
    with path.open("r", encoding="utf-8") as fh:
        for line_no, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError as exc:
                print(f"WARN bad JSON {path}:{line_no}: {exc}")
                continue

            aux = rec.get("aux") or {}
            rows.append({
                "experiment": rec.get("experiment", ""),
                "metric": rec.get("metric", ""),
                "unit": rec.get("unit", ""),
                "language": rec.get("language", ""),
                "layout": rec.get("datalake_layout") or "",
                "backend": rec.get("index_backend") or "",
                "corpus_size": rec.get("corpus_size"),
                "workers": rec.get("workers"),
                "n": aux.get("rounds"),
                "median": rec.get("value"),
                "q1": aux.get("q1"),
                "q3": aux.get("q3"),
                "iqr": aux.get("iqr"),
                "min": aux.get("min"),
                "max": aux.get("max"),
                "median_peak_rss_mib": pd.NA,
                "cache": aux.get("cache", ""),
                "batch_size": rec.get("batch_size"),
                "p95": aux.get("p95"),
                "workload": aux.get("workload") or "",
                "layer": "micro",
                "source": path.as_posix(),
            })

    return pd.DataFrame(rows)


def normalize(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(columns=BASE_COLUMNS)

    for c in BASE_COLUMNS:
        if c not in df.columns:
            df[c] = pd.NA

    text_cols = [
        "experiment", "metric", "unit", "language", "layout", "backend",
        "cache", "workload", "layer", "source",
    ]
    for c in text_cols:
        df[c] = df[c].fillna("").astype(str)

    numeric_cols = [
        "corpus_size", "workers", "n", "median", "q1", "q3", "iqr",
        "min", "max", "median_peak_rss_mib", "batch_size", "p95",
    ]
    for c in numeric_cols:
        df[c] = pd.to_numeric(df[c], errors="coerce")

    return df[BASE_COLUMNS].copy()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", default="results")
    args = ap.parse_args()

    results = Path(args.results)
    parts = approved_macro(results)

    # Approved Python micro benchmarks: E2 lookup, E3 scan, E7 query.
    m = micro_rows(results / "micro.jsonl")
    if not m.empty:
        m = m[m["experiment"].isin(["E2_lookup", "E3_scan", "E7_query"])]
        parts.append(m)

    # Supplementary metadata query benchmark.
    m = micro_rows(results / "t-meta" / "micro-metadata.jsonl")
    if not m.empty:
        m = m[m["experiment"] == "E13_metadata_query"]
        parts.append(m)

    if not parts:
        raise SystemExit("No approved benchmark rows found.")

    out = normalize(pd.concat(parts, ignore_index=True, sort=False))

    # Defensive duplicate removal. source/layer are intentionally excluded.
    key = [
        "experiment", "metric", "unit", "language", "layout", "backend",
        "corpus_size", "workers", "batch_size", "workload", "median",
    ]
    out = out.drop_duplicates(key, keep="last")

    # Stable ordering makes diffs reviewable.
    out = out.sort_values(
        ["experiment", "corpus_size", "language", "layout", "backend",
         "batch_size", "workload", "metric"],
        na_position="last",
    ).reset_index(drop=True)

    output = results / "summary.csv"
    out.to_csv(output, index=False)

    macro_n = int((out["layer"] == "macro").sum())
    micro_n = int((out["layer"] == "micro").sum())
    print(f"WROTE {output}")
    print(f"approved rows: {len(out)} (macro={macro_n}, micro={micro_n})")


if __name__ == "__main__":
    main()
