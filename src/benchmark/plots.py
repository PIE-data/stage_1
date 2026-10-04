#!/usr/bin/env python3
"""
Generate Stage 1 report figures F1-F10 from results/summary.csv.

Run from the repository root, after aggregate.py:
    python src/benchmark/plots.py --results results --out report/figures
"""

from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


def load_summary(results: Path) -> pd.DataFrame:
    path = results / "summary.csv"
    if not path.exists():
        raise SystemExit(
            f"{path} does not exist. Run src/benchmark/aggregate.py first."
        )

    df = pd.read_csv(path)
    for c in [
        "corpus_size", "workers", "n", "median", "q1", "q3", "iqr",
        "min", "max", "median_peak_rss_mib", "batch_size", "p95",
    ]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")

    for c in ["layout", "backend", "workload", "layer"]:
        if c not in df.columns:
            df[c] = ""
        df[c] = df[c].fillna("").astype(str)

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
    if pd.notna(w):
        parts.append(f"workers={int(float(w))}")

    return " / ".join(x for x in parts if x)


def save_barh(
    df: pd.DataFrame,
    value: str,
    label: str,
    title: str,
    xlabel: str,
    path: Path,
    xerr: str | None = None,
) -> None:
    if df.empty:
        print(f"SKIP {path.name}: no approved data")
        return

    p = df.copy().sort_values(value)
    y = np.arange(len(p))

    fig, ax = plt.subplots(figsize=(10, max(4.7, 0.43 * len(p) + 1.5)))
    err = p[xerr].to_numpy() if xerr and xerr in p.columns else None
    ax.barh(y, p[value].to_numpy(), xerr=err)
    ax.set_yticks(y, p[label].tolist())
    ax.set_xlabel(xlabel)
    ax.set_title(title)
    ax.grid(axis="x", alpha=0.25)
    ax.margins(x=0.05)
    fig.tight_layout()
    fig.savefig(path, dpi=180, bbox_inches="tight")
    plt.close(fig)
    print(f"WROTE {path}")


def f1(df: pd.DataFrame, out: Path) -> None:
    p = df[
        (df["experiment"] == "E1")
        & (df["metric"] == "wall_time")
        & (df["corpus_size"] == 1000)
        & (df["layer"] == "macro")
    ].copy()

    if not p.empty:
        p["throughput"] = 1000 / (p["median"] / 1000.0)
        p["label"] = p.apply(label_for, axis=1)

    save_barh(
        p, "throughput", "label",
        "F1 — Download + write throughput, 1,000 books",
        "books/s (higher is better)",
        out / "F1_download_throughput.png",
    )


def f2(df: pd.DataFrame, out: Path) -> None:
    p = df[
        (df["experiment"] == "E2_lookup")
        & (df["metric"] == "latency")
        & (df["corpus_size"] == 1000)
        & (df["layer"] == "micro")
    ].copy()

    if p.empty:
        print("SKIP F2: no approved E2 data")
        return

    p["label"] = p["language"] + " / " + p["layout"]
    p = p.sort_values("median")
    y = np.arange(len(p))

    fig, ax = plt.subplots(figsize=(9, 4.8))
    ax.barh(y, p["median"])
    ax.scatter(p["p95"], y, marker="x", s=60, label="p95")
    ax.set_yticks(y, p["label"])
    ax.set_xlabel("latency (µs)")
    ax.set_title("F2 — Datalake lookup latency: median and p95 (Python micro)")
    ax.legend()
    ax.grid(axis="x", alpha=0.25)
    ax.margins(x=0.05)
    fig.tight_layout()

    path = out / "F2_lookup_latency.png"
    fig.savefig(path, dpi=180, bbox_inches="tight")
    plt.close(fig)
    print(f"WROTE {path}")


def f3(df: pd.DataFrame, out: Path) -> None:
    p = df[
        (df["experiment"] == "E3")
        & (df["metric"] == "wall_time_internal")
        & (df["corpus_size"] == 1000)
        & (df["layer"] == "macro")
    ].copy()

    if not p.empty:
        p["label"] = p.apply(label_for, axis=1)

    save_barh(
        p, "median", "label",
        "F3 — Incremental detection cost (+50 books)",
        "median internal time (ms)",
        out / "F3_incremental.png",
        xerr="iqr",
    )


def f4(df: pd.DataFrame, out: Path) -> None:
    p = df[
        (df["experiment"] == "E4")
        & (df["metric"] == "recovery_time")
        & (df["corpus_size"] == 1000)
        & (df["layer"] == "macro")
    ].copy()

    if not p.empty:
        p["label"] = p.apply(label_for, axis=1)

    save_barh(
        p, "median", "label",
        "F4 — Recovery after SIGKILL",
        "median recovery time (ms)",
        out / "F4_recovery.png",
        xerr="iqr",
    )


def f5(df: pd.DataFrame, out: Path) -> None:
    p = df[
        (df["experiment"] == "E5")
        & (df["metric"] == "storage_allocated_bytes")
        & (df["corpus_size"] == 1000)
        & (df["layer"] == "macro")
    ].copy()

    if not p.empty:
        # Layout-only result: remove any accidental repeated language copy.
        p = p.sort_values("language").drop_duplicates("layout", keep="first")
        p["allocated_mib"] = p["median"] / (2**20)
        p["label"] = p["layout"]

    save_barh(
        p, "allocated_mib", "label",
        "F5 — Datalake allocated disk space, 1,000 books",
        "allocated MiB",
        out / "F5_storage_overhead.png",
    )


def e6_1000(df: pd.DataFrame) -> pd.DataFrame:
    return df[
        (df["experiment"] == "E6")
        & (df["metric"] == "wall_time")
        & (df["corpus_size"] == 1000)
        & (df["backend"].isin(["json", "sqlite"]))
        & (df["layer"] == "macro")
    ].copy()


def f6(df: pd.DataFrame, out: Path) -> None:
    p = e6_1000(df)
    if not p.empty:
        p["label"] = p.apply(label_for, axis=1)

    save_barh(
        p, "median", "label",
        "F6 — Index build time, 1,000 books (JSON and SQLite)",
        "median wall time (ms)",
        out / "F6_index_build.png",
        xerr="iqr",
    )


def f7(df: pd.DataFrame, out: Path) -> None:
    p = df[
        (df["experiment"] == "E7_query")
        & (df["metric"] == "latency")
        & (df["corpus_size"] == 1000)
        & (df["layer"] == "micro")
    ].copy()

    if p.empty:
        print("SKIP F7: no approved E7 data")
        return

    p["label"] = p["language"] + " / " + p["backend"] + " / " + p["workload"]

    save_barh(
        p, "median", "label",
        "F7 — Query latency by workload (Python micro)",
        "median latency (µs)",
        out / "F7_query_latency.png",
    )


def f8(df: pd.DataFrame, out: Path) -> None:
    p = df[
        (df["experiment"] == "E8")
        & (df["metric"] == "wall_time")
        & (df["corpus_size"] == 1000)
        & (df["backend"].isin(["json", "sqlite"]))
        & (df["layer"] == "macro")
    ].copy()

    if not p.empty:
        p["label"] = p.apply(label_for, axis=1)

    save_barh(
        p, "median", "label",
        "F8 — Index update cost (+50 books, JSON and SQLite)",
        "median wall time (ms)",
        out / "F8_update.png",
        xerr="iqr",
    )


def f9(df: pd.DataFrame, out: Path) -> None:
    p = e6_1000(df)
    p = p[p["median_peak_rss_mib"].notna()].copy()

    if not p.empty:
        p["label"] = p.apply(label_for, axis=1)

    save_barh(
        p, "median_peak_rss_mib", "label",
        "F9 — Peak memory during index build, 1,000 books",
        "median peak RSS (MiB)",
        out / "F9_peak_memory.png",
    )


def pareto_front(p: pd.DataFrame, x: str, y: str) -> pd.DataFrame:
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


def f10(df: pd.DataFrame, out: Path) -> None:
    p = e6_1000(df)
    p = p[p["median_peak_rss_mib"].notna()].copy()

    if p.empty:
        print("SKIP F10: no approved E6 time+memory data")
        return

    p["label"] = p.apply(label_for, axis=1)
    front = pareto_front(p, "median", "median_peak_rss_mib")

    fig, ax = plt.subplots(figsize=(9.5, 6.3))
    ax.scatter(p["median"], p["median_peak_rss_mib"], s=70)

    q = p.sort_values(["median", "median_peak_rss_mib"]).reset_index(drop=True)
    offsets = [
        (7, 10), (7, -18), (7, 10),
        (7, -18), (-115, 10), (-115, -18),
    ]

    for i, (_, r) in enumerate(q.iterrows()):
        dx, dy = offsets[i % len(offsets)]
        ax.annotate(
            r["label"],
            (r["median"], r["median_peak_rss_mib"]),
            xytext=(dx, dy),
            textcoords="offset points",
            fontsize=9,
        )

    if len(front) >= 2:
        ax.plot(
            front["median"],
            front["median_peak_rss_mib"],
            linewidth=1.5,
            label="Pareto front",
        )

    ax.set_xlabel("index-build time (ms) — lower is better")
    ax.set_ylabel("peak RSS (MiB) — lower is better")
    ax.set_title("F10 — Time vs memory Pareto front, 1,000 books")
    ax.grid(alpha=0.25)
    ax.margins(x=0.10, y=0.08)
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


def folder_limit_check(df: pd.DataFrame) -> None:
    p = df[
        (df["experiment"].isin(["E6", "E8"]))
        & (df["backend"] == "folder")
        & (df["corpus_size"] == 100)
        & (df["metric"] == "wall_time")
        & (df["layer"] == "macro")
    ].copy()

    if p.empty:
        print("WARN no approved 100-book folder-backend rows")
        return

    print("Folder backend, 100 books (report as scalability limitation):")
    for _, r in p.sort_values(["experiment", "language"]).iterrows():
        print(
            f"  {r['experiment']} {r['language']}: "
            f"{r['median']/1000.0:.1f} s"
        )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", default="results")
    ap.add_argument("--out", default="report/figures")
    args = ap.parse_args()

    results = Path(args.results)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    df = load_summary(results)
    print(f"approved summary rows: {len(df)}")

    f1(df, out)
    f2(df, out)
    f3(df, out)
    f4(df, out)
    f5(df, out)
    f6(df, out)
    f7(df, out)
    f8(df, out)
    f9(df, out)
    f10(df, out)

    folder_limit_check(df)

    print(
        "NOTE: 10,000-book E10/E11 scaling is not present in the approved "
        "results and must be declared as a limitation in the report."
    )


if __name__ == "__main__":
    main()
