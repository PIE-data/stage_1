#!/usr/bin/env python31
"""
Generate Stage 1 report figures using ONLY the benchmark rows approved in
results/results.md.

This version intentionally does not glob every summary.csv indiscriminately,
because results/results.md marks some rows/folders as obsolete or to be ignored.

Expected repository layout:
  results/
    results.md
    micro.jsonl
    t1000-datalake/summary.csv
    t1000-py-node/summary.csv
    t1000-py-node-e6e8/summary.csv
    t1000-py-node-e8/summary.csv
    t1000-py-sqlite/summary.csv
    t100/summary.csv

Outputs:
  report/figures/F1_*.png ... F10_*.png

Current first-batch limitations:
- E2 and E7 micro results are Python-only.
- Go results and some folder/scalability results are still pending.
- No final scalability figure is produced until E10/E11 are available.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


def read_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    df = pd.read_csv(path)
    for c in [
        "corpus_size", "workers", "median", "q1", "q3", "iqr",
        "min", "max", "median_peak_rss_mib"
    ]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    for c in ["layout", "backend", "unit", "cache"]:
        if c not in df.columns:
            df[c] = ""
        df[c] = df[c].fillna("").astype(str)
    return df


def approved_macro(results: Path) -> pd.DataFrame:
    """
    Reproduce the whitelist written in results/results.md.

    E1/E4/E5: t1000-datalake, all rows
    E3:       t1000-py-node, E3 rows only
    E6:       t1000-py-node-e6e8:
                - Python + Node JSON
                - Node SQLite
                - ignore Python SQLite
              plus t1000-py-sqlite:
                - Python SQLite
    E8:       t1000-py-node-e8:
                - Node JSON + Node SQLite
                - ignore Python SQLite
              t1000-py-node-e6e8:
                - Python JSON
              t1000-py-sqlite:
                - Python SQLite
    100-book E6/E8:
              t100, all rows
    """
    parts = []

    # E1, E4, E5
    d = read_csv(results / "t1000-datalake" / "summary.csv")
    if not d.empty:
        parts.append(d[d["experiment"].isin(["E1", "E4", "E5"])].copy())

    # E3 only from t1000-py-node
    d = read_csv(results / "t1000-py-node" / "summary.csv")
    if not d.empty:
        parts.append(d[d["experiment"] == "E3"].copy())

    # E6 canonical 1000-book source
    d = read_csv(results / "t1000-py-node-e6e8" / "summary.csv")
    if not d.empty:
        e6 = d[
            (d["experiment"] == "E6")
            & (
                (d["backend"] == "json")
                | ((d["language"] == "node") & (d["backend"] == "sqlite"))
            )
        ].copy()
        parts.append(e6)

        # Python JSON E8 lives here and is an approved row.
        e8_py_json = d[
            (d["experiment"] == "E8")
            & (d["language"] == "python")
            & (d["backend"] == "json")
        ].copy()
        parts.append(e8_py_json)

    # E8 Node rows from the dedicated rerun folder
    d = read_csv(results / "t1000-py-node-e8" / "summary.csv")
    if not d.empty:
        e8_node = d[
            (d["experiment"] == "E8")
            & (d["language"] == "node")
            & (d["backend"].isin(["json", "sqlite"]))
        ].copy()
        parts.append(e8_node)

    # Python SQLite E6/E8 from its authoritative rerun folder
    d = read_csv(results / "t1000-py-sqlite" / "summary.csv")
    if not d.empty:
        parts.append(
            d[
                (d["language"] == "python")
                & (d["backend"] == "sqlite")
                & (d["experiment"].isin(["E6", "E8"]))
            ].copy()
        )

    # 100-book scaling seed
    d = read_csv(results / "t100" / "summary.csv")
    if not d.empty:
        parts.append(d[d["experiment"].isin(["E6", "E8"])].copy())

    if not parts:
        return pd.DataFrame()

    out = pd.concat(parts, ignore_index=True, sort=False)

    # Defensive dedupe: one authoritative row per configuration/metric.
    keys = [
        "experiment", "metric", "unit", "language",
        "layout", "backend", "corpus_size", "workers"
    ]
    existing = [c for c in keys if c in out.columns]
    out = out.drop_duplicates(existing, keep="last").reset_index(drop=True)
    return out


def read_micro(results: Path) -> pd.DataFrame:
    path = results / "micro.jsonl"
    if not path.exists():
        return pd.DataFrame()

    rows = []
    with path.open("r", encoding="utf-8") as fh:
        for n, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError as exc:
                print(f"WARN bad JSON {path}:{n}: {exc}")
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
                "value": rec.get("value"),
                "q1": aux.get("q1"),
                "q3": aux.get("q3"),
                "iqr": aux.get("iqr"),
                "p95": aux.get("p95"),
                "rounds": aux.get("rounds"),
                "workload": aux.get("workload") or "",
            })
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    for c in ["corpus_size", "value", "q1", "q3", "iqr", "p95", "rounds"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


def label_for(r) -> str:
    parts = [str(r.get("language", ""))]
    layout = str(r.get("layout", ""))
    backend = str(r.get("backend", ""))
    if layout:
        parts.append(layout)
    if backend:
        parts.append(backend)
    w = r.get("workers")
    if pd.notna(w) and str(w) not in ("", "nan"):
        parts.append(f"workers={int(float(w))}")
    return " / ".join(x for x in parts if x)


def save_barh(df, value, label, title, xlabel, path, xerr=None):
    if df.empty:
        print(f"SKIP {path.name}: no approved data")
        return
    p = df.copy().sort_values(value)
    y = np.arange(len(p))
    fig, ax = plt.subplots(figsize=(10, max(4.5, 0.42 * len(p) + 1.5)))
    err = p[xerr].to_numpy() if xerr and xerr in p.columns else None
    ax.barh(y, p[value].to_numpy(), xerr=err)
    ax.set_yticks(y, p[label].tolist())
    ax.set_xlabel(xlabel)
    ax.set_title(title)
    ax.grid(axis="x", alpha=0.25)
    fig.tight_layout()
    fig.savefig(path, dpi=180, bbox_inches="tight")
    plt.close(fig)
    print(f"WROTE {path}")


def f1(macro, out):
    p = macro[
        (macro["experiment"] == "E1")
        & (macro["metric"] == "wall_time")
        & (macro["corpus_size"] == 1000)
    ].copy()
    if not p.empty:
        p["throughput"] = 1000 / (p["median"] / 1000.0)
        p["label"] = p.apply(label_for, axis=1)
    save_barh(
        p, "throughput", "label",
        "F1 — Download + write throughput, 1,000 books",
        "books/s (higher is better)",
        out / "F1_download_throughput.png"
    )


def f2(micro, out):
    p = micro[
        (micro["experiment"] == "E2_lookup")
        & (micro["metric"] == "latency")
        & (micro["corpus_size"] == 1000)
    ].copy()
    if p.empty:
        print("SKIP F2: no approved micro data")
        return
    p["label"] = p["language"] + " / " + p["layout"]
    p = p.sort_values("value")
    y = np.arange(len(p))

    fig, ax = plt.subplots(figsize=(9, 4.8))
    ax.barh(y, p["value"])
    ax.scatter(p["p95"], y, marker="x", s=60, label="p95")
    ax.set_yticks(y, p["label"])
    ax.set_xlabel("latency (µs)")
    ax.set_title("F2 — Datalake lookup latency: p50 and p95")
    ax.legend()
    ax.grid(axis="x", alpha=0.25)
    fig.tight_layout()
    path = out / "F2_lookup_latency.png"
    fig.savefig(path, dpi=180, bbox_inches="tight")
    plt.close(fig)
    print(f"WROTE {path}")


def f3(macro, out):
    p = macro[
        (macro["experiment"] == "E3")
        & (macro["metric"] == "wall_time_internal")
        & (macro["corpus_size"] == 1000)
    ].copy()
    if not p.empty:
        p["label"] = p.apply(label_for, axis=1)
    save_barh(
        p, "median", "label",
        "F3 — Incremental detection cost (+50 books)",
        "median internal time (ms)",
        out / "F3_incremental.png",
        xerr="iqr"
    )


def f4(macro, out):
    p = macro[
        (macro["experiment"] == "E4")
        & (macro["metric"] == "recovery_time")
        & (macro["corpus_size"] == 1000)
    ].copy()
    if not p.empty:
        p["label"] = p.apply(label_for, axis=1)
    save_barh(
        p, "median", "label",
        "F4 — Recovery after SIGKILL",
        "median recovery time (ms)",
        out / "F4_recovery.png",
        xerr="iqr"
    )


def f5(macro, out):
    p = macro[
        (macro["experiment"] == "E5")
        & (macro["metric"] == "storage_allocated_bytes")
        & (macro["corpus_size"] == 1000)
    ].copy()
    if not p.empty:
        p["allocated_mib"] = p["median"] / (2**20)
        p["label"] = p["layout"]
    save_barh(
        p, "allocated_mib", "label",
        "F5 — Datalake allocated disk space, 1,000 books",
        "allocated MiB",
        out / "F5_storage_overhead.png"
    )


def e6_1000(macro) -> pd.DataFrame:
    return macro[
        (macro["experiment"] == "E6")
        & (macro["metric"] == "wall_time")
        & (macro["corpus_size"] == 1000)
    ].copy()


def f6(macro, out):
    p = e6_1000(macro)
    if not p.empty:
        p["label"] = p.apply(label_for, axis=1)
    save_barh(
        p, "median", "label",
        "F6 — Index build time, 1,000 books",
        "median wall time (ms)",
        out / "F6_index_build.png",
        xerr="iqr"
    )


def f7(micro, out):
    p = micro[
        (micro["experiment"] == "E7_query")
        & (micro["metric"] == "latency")
        & (micro["corpus_size"] == 1000)
    ].copy()
    if p.empty:
        print("SKIP F7: no approved micro data")
        return
    p["label"] = (
        p["language"] + " / " + p["backend"] + " / " + p["workload"]
    )
    save_barh(
        p, "value", "label",
        "F7 — Query latency by workload",
        "median latency (µs)",
        out / "F7_query_latency.png"
    )


def f8(macro, out):
    p = macro[
        (macro["experiment"] == "E8")
        & (macro["metric"] == "wall_time")
        & (macro["corpus_size"] == 1000)
    ].copy()
    if not p.empty:
        p["label"] = p.apply(label_for, axis=1)
    save_barh(
        p, "median", "label",
        "F8 — Index update cost (+50 books)",
        "median wall time (ms)",
        out / "F8_update.png",
        xerr="iqr"
    )


def f9(macro, out):
    p = e6_1000(macro)
    p = p[p["median_peak_rss_mib"].notna()].copy()
    if not p.empty:
        p["label"] = p.apply(label_for, axis=1)
    save_barh(
        p, "median_peak_rss_mib", "label",
        "F9 — Peak memory during index build, 1,000 books",
        "median peak RSS (MiB)",
        out / "F9_peak_memory.png"
    )


def pareto_front(p, x, y):
    keep = []
    for idx, row in p.iterrows():
        dominated = (
            (p[x] <= row[x])
            & (p[y] <= row[y])
            & ((p[x] < row[x]) | (p[y] < row[y]))
        ).any()
        if not dominated:
            keep.append(idx)
    return p.loc[keep].sort_values(x)


def f10(macro, out):
    p = e6_1000(macro)
    p = p[p["median_peak_rss_mib"].notna()].copy()
    if p.empty:
        print("SKIP F10: no approved E6 time+memory data")
        return

    p["label"] = p.apply(label_for, axis=1)
    front = pareto_front(p, "median", "median_peak_rss_mib")

    fig, ax = plt.subplots(figsize=(9.2, 6.0))
    ax.scatter(p["median"], p["median_peak_rss_mib"], s=70)

    # Offsets chosen by point order after sorting to prevent text collisions.
    q = p.sort_values(["median", "median_peak_rss_mib"]).reset_index(drop=True)
    offsets = [(7, 9), (7, -17), (7, 9), (7, -17), (7, 9), (7, -17)]
    for i, (_, r) in enumerate(q.iterrows()):
        dx, dy = offsets[i % len(offsets)]
        ax.annotate(
            r["label"],
            (r["median"], r["median_peak_rss_mib"]),
            xytext=(dx, dy),
            textcoords="offset points",
            fontsize=9
        )

    if len(front) >= 2:
        ax.plot(
            front["median"],
            front["median_peak_rss_mib"],
            linewidth=1.5,
            label="Pareto front"
        )

    ax.set_xlabel("index-build time (ms) — lower is better")
    ax.set_ylabel("peak RSS (MiB) — lower is better")
    ax.set_title("F10 — Time vs memory Pareto front")
    ax.grid(alpha=0.25)
    ax.margins(x=0.08, y=0.08)
    ax.legend(loc="best")
    fig.tight_layout()

    path = out / "F10_time_memory_pareto.png"
    fig.savefig(path, dpi=180, bbox_inches="tight")
    plt.close(fig)
    print(f"WROTE {path}")

    print("F10 approved points:")
    for _, r in p.sort_values("median").iterrows():
        print(
            f"  {r['label']}: "
            f"{r['median']:.1f} ms, {r['median_peak_rss_mib']:.1f} MiB"
        )
    print("F10 Pareto front:")
    for _, r in front.iterrows():
        print(f"  {r['label']}")


def scalability(macro, out):
    p = macro[
        macro["experiment"].isin(["E10", "E11"])
        & (macro["metric"] == "wall_time")
    ].copy()
    if p.empty:
        print("SKIP scalability appendix figure: E10/E11 not yet approved")
        return

    fig, ax = plt.subplots(figsize=(9, 5.5))
    p["series"] = p.apply(label_for, axis=1)
    for (exp, series), g in p.groupby(["experiment", "series"]):
        g = g.sort_values("corpus_size")
        ax.plot(
            g["corpus_size"], g["median"],
            marker="o", label=f"{exp} / {series}"
        )

    ax.set_xscale("log")
    ax.set_yscale("log")
    ax.set_xlabel("corpus size (books)")
    ax.set_ylabel("median wall time (ms)")
    ax.set_title("Scalability — E10 datalake and E11 index")
    ax.grid(alpha=0.25)
    ax.legend(fontsize=7)
    fig.tight_layout()
    path = out / "appendix_scalability.png"
    fig.savefig(path, dpi=180, bbox_inches="tight")
    plt.close(fig)
    print(f"WROTE {path}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", default="results")
    ap.add_argument("--out", default="report/figures")
    args = ap.parse_args()

    results = Path(args.results)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    macro = approved_macro(results)
    micro = read_micro(results)

    print(f"approved macro rows: {len(macro)}")
    print(f"micro rows: {len(micro)}")

    f1(macro, out)
    f2(micro, out)
    f3(macro, out)
    f4(macro, out)
    f5(macro, out)
    f6(macro, out)
    f7(micro, out)
    f8(macro, out)
    f9(macro, out)
    f10(macro, out)
    scalability(macro, out)


if __name__ == "__main__":
    main()
