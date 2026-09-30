#!/usr/bin/env python3
"""
Benchmark runner for Stage 1.  Issues #22, #71, #72.

Started by Marcela (repetitions, warm-up, cold cache, external peak RSS);
extended with a setup phase outside the timer, the experiment matrix, external
wall-clock timing and aggregation.  docs/TASKS.md, "Protocol" and "Two
measurement layers", is the contract this file implements.

What it measures -- the MACRO layer: whole CLI commands, timed from outside,
identical for every language.  Lookup (E2) and query (E7) are too short for
that and are micro-benchmarked inside each language instead.

    python3 src/benchmark/runner.py --smoke
        20 golden books, 1 repetition: checks the machinery in a minute.

    python3 src/benchmark/runner.py --experiments E1,E3,E5,E6,E8 \\
        --languages python --tiers 100,1000 --reps 5
        the real thing.  Run `sudo -v` first so the cold-cache drop works.

Output
    results/raw.jsonl     one SPEC.md §8 record per measured repetition
    results/summary.csv   median, IQR, min, max per configuration

Protocol, applied to every language alike
  * 1 warm-up run discarded (--warmup), then --reps measured runs;
  * every run starts from a workspace prepared OUTSIDE the timer
    (clean, or a copy of a prepared snapshot) -- teardown is never timed;
  * page cache dropped before each measured run (needs sudo; if it cannot,
    the records say cache="warm" instead of pretending);
  * wall time from time.perf_counter_ns() around the child process, peak RSS
    from /usr/bin/time on the child -- never self-reported (SPEC.md §8);
  * all downloads hit tools/mirror_server.py, never the live network.

E3 is the brief's "incremental processing": the cost of DETECTING which books
are new and ready to be indexed (`scan-new --since`), not of downloading them.

Two times per run.  `wall_time` is taken from outside and includes starting
the interpreter; `wall_time_internal` is what the CLI itself reports through
--metrics-out (SPEC.md §8), from its own monotonic clock around the command.
For short commands such as E3 the second is the one that shows the layout:
the scan takes milliseconds, starting Python 70-150 ms.

The time layout's snapshot is ingested at --books-per-hour (default 100), one
date/hour folder per slice, as a crawler running for hours would leave it.
With a single fixed --now every book lands in ONE folder and the layout
degenerates into a flat directory -- which is not the layout being compared.

E5 reports both the bytes of the files (`storage_bytes`, identical for every
layout by construction: same files) and the space allocated on disk
(`storage_allocated_bytes`: st_blocks of every file AND directory), which is
where the layouts actually differ.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import shutil
import socket
import statistics
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
NOW = "2026-01-01T00:00:00Z"  # fixed --now; time-layout snapshots start here (--books-per-hour)
SPEC_VERSION = (REPO / "spec" / "SPEC_VERSION").read_text(encoding="utf-8").strip()

LANGUAGES = ("python", "node", "go")
LAYOUTS = ("time", "book", "hash")
BACKENDS = ("json", "folder", "sqlite")


class RunFailed(RuntimeError):
    pass


# ---------------------------------------------------------------- Marcela


def drop_system_caches(state: dict) -> None:
    """
    Clears the OS pagecache, dentries, and inodes to ensure a 'cold cache' run.
    Requires root/sudo privileges on Linux/WSL2 (run `sudo -v` first).
    """
    if state.get("cache") == "warm":
        return  # already known not to work: don't retry every run
    try:
        subprocess.run(["sync"], check=True)
        # `tee` rather than `sh -c`, so one narrow sudoers rule is enough for
        # unattended runs:  <user> ALL=(root) NOPASSWD: /usr/bin/tee /proc/sys/vm/drop_caches
        subprocess.run(["sudo", "-n", "tee", "/proc/sys/vm/drop_caches"], input=b"3\n",
                       check=True, capture_output=True)
        state["cache"] = "cold"
    except (subprocess.CalledProcessError, FileNotFoundError):
        print("[RUNNER] WARNING: cannot drop caches (run `sudo -v` first). "
              "Continuing with a WARM cache; records say so.", file=sys.stderr)
        state["cache"] = "warm"


def clean_workspace(workspace: Path) -> None:
    """Deletes the workspace.  Always called outside the timer."""
    if workspace.exists():
        shutil.rmtree(workspace)
    workspace.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------- engines


class Engines:
    """The command line of each implementation (SPEC.md §9)."""

    def __init__(self, build_dir: Path) -> None:
        self.build_dir = build_dir
        self._cache: dict[str, list[str]] = {}

    def __call__(self, lang: str) -> list[str]:
        if lang not in self._cache:
            if lang == "python":
                self._cache[lang] = [sys.executable, str(REPO / "src/python/cli.py")]
            elif lang == "node":
                self._cache[lang] = ["node", str(REPO / "src/node/cli.js")]
            elif lang == "go":
                self.build_dir.mkdir(parents=True, exist_ok=True)
                out = self.build_dir / "engine-go"
                subprocess.run(["go", "build", "-o", str(out), "./cmd/engine"],
                               cwd=REPO / "src/go", check=True)
                self._cache[lang] = [str(out)]
            else:
                raise ValueError(lang)
        return self._cache[lang]


def cli(engine: list[str], ws: Path, layout: str, backend: str, *args: str,
        now: str = NOW) -> list[str]:
    return [*engine, "--workspace", str(ws), "--datalake-layout", layout,
            "--index-backend", backend, "--now", now, *args]


def with_metrics_out(argv: list[str], path: Path) -> list[str]:
    """Insert the global --metrics-out flag before the command (SPEC.md §1)."""
    i = argv.index("--now") + 2
    return [*argv[:i], "--metrics-out", str(path), *argv[i:]]


def internal_wall_ms(path: Path) -> float | None:
    """wall_time the CLI reported about itself, or None if it wrote nothing
    (a port without --metrics-out yet): the external time is still recorded."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
        rec = json.loads(lines[-1])
    except (OSError, ValueError, IndexError):
        return None
    return rec.get("value") if rec.get("metric") == "wall_time" else None


def run_measured(cmd: list[str], env: dict) -> tuple[int, float, int | None, str, str]:
    """Run once: (exit code, wall ms, peak RSS bytes or None, stderr, stdout)."""
    timer = ["/usr/bin/time", "-f", "PEAK_RSS_KB:%M"] if Path("/usr/bin/time").exists() else []
    t0 = time.perf_counter_ns()
    proc = subprocess.run(timer + cmd, env=env, capture_output=True, text=True)
    wall_ms = (time.perf_counter_ns() - t0) / 1e6
    rss = None
    kept = []
    for line in proc.stderr.splitlines():
        if line.startswith("PEAK_RSS_KB:"):
            rss = int(line.split(":", 1)[1]) * 1024
        else:
            kept.append(line)
    return proc.returncode, wall_ms, rss, "\n".join(kept), proc.stdout


def run_setup(cmd: list[str], env: dict) -> None:
    proc = subprocess.run(cmd, env=env, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RunFailed(f"setup failed ({proc.returncode}): {' '.join(cmd)}\n"
                        f"{proc.stderr[-2000:]}")


# ------------------------------------------------------------------ disk


def tree_stats(root: Path) -> dict:
    """files, dirs, bytes = sum of file sizes, allocated_bytes = what the
    filesystem really reserves (st_blocks * 512) for files and directories,
    root included.  On ext4 a directory costs at least one 4 KiB block and a
    small file a whole block, which `bytes` cannot see."""
    files = dirs = size = allocated = 0
    if root.exists():
        allocated += os.stat(root).st_blocks * 512
        for dirpath, dirnames, filenames in os.walk(root):
            dirs += len(dirnames)
            for d in dirnames:
                allocated += os.stat(os.path.join(dirpath, d)).st_blocks * 512
            for f in filenames:
                st = os.stat(os.path.join(dirpath, f))
                files += 1
                size += st.st_size
                allocated += st.st_blocks * 512
    return {"files": files, "dirs": dirs, "bytes": size, "allocated_bytes": allocated}


def copy_snapshot(src: Path, dst: Path) -> None:
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst, symlinks=True)
    lock = dst / "control" / "run.lock"
    lock.unlink(missing_ok=True)


# ---------------------------------------------------------------- runner


class Runner:
    def __init__(self, args) -> None:
        self.args = args
        self.work = Path(args.work).expanduser().resolve()
        self.results = Path(args.results).resolve()
        self.results.mkdir(parents=True, exist_ok=True)
        self.raw = self.results / "raw.jsonl"
        self.engines = Engines(self.work / ".build")
        self.state: dict = {}
        self.run_id = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")
        self.env = dict(os.environ, NO_PROXY="127.0.0.1,localhost", no_proxy="127.0.0.1,localhost")
        self.impl_version = self._git_rev()
        self.snapshots = self.work / ".snapshots"
        self.manifests = self.work / ".manifests"
        self.manifests.mkdir(parents=True, exist_ok=True)
        self.mirror_proc = None
        self.base = ""

    # -------------------------------------------------------- environment

    def _git_rev(self) -> str:
        try:
            rev = subprocess.run(["git", "-C", str(REPO), "rev-parse", "--short", "HEAD"],
                                 capture_output=True, text=True, check=True).stdout.strip()
            return f"git:{rev}"
        except (OSError, subprocess.CalledProcessError):
            return "unknown"

    def preflight(self) -> None:
        if not sys.platform.startswith("linux"):
            sys.exit("[RUNNER] benchmarks run on Linux only (WSL2 on ext4, see #62): this is "
                     f"{sys.platform}. Open Ubuntu and run from ~/bench/stage_1.")
        if str(self.work).startswith("/mnt/"):
            sys.exit(f"[RUNNER] {self.work} is on the Windows drive (NTFS): FolderIndex "
                     "merges terms that differ only in case there. Use a path under ~.")
        free_gb = shutil.disk_usage(self.work).free / 1e9
        print(f"[RUNNER] work dir {self.work}, {free_gb:.0f} GB free "
              "(inside WSL this is the virtual disk's limit; check the host drive too)",
              file=sys.stderr)
        if free_gb < self.args.min_free_gb:
            sys.exit(f"[RUNNER] less than {self.args.min_free_gb} GB free: aborting")

    def start_mirror(self, root: Path) -> None:
        port_file = self.work / ".mirror-port"
        port_file.unlink(missing_ok=True)
        self.mirror_proc = subprocess.Popen(
            [sys.executable, str(REPO / "tools/mirror_server.py"), "--root", str(root),
             "--port-file", str(port_file)], stdout=subprocess.DEVNULL)
        for _ in range(100):
            if port_file.exists() and port_file.read_text().strip():
                break
            time.sleep(0.1)
        else:
            raise RunFailed("mirror server did not start")
        self.base = f"http://127.0.0.1:{port_file.read_text().strip()}"

    def stop_mirror(self) -> None:
        if self.mirror_proc:
            self.mirror_proc.terminate()
            self.mirror_proc.wait()

    # ---------------------------------------------------------- corpora

    def tier_ids(self, tier: int) -> list[int]:
        if self.args.smoke:
            ids = [int(x) for x in (REPO / "spec/golden/manifest_20.txt").read_text().split()]
            return ids[:15]
        path = REPO / "spec" / "corpus" / f"manifest_{tier}.txt"
        return [int(x) for x in path.read_text().split() if x.strip()]

    def extra_ids(self, tier: int, n: int = 50) -> list[int]:
        """Books to add on top of a tier (E3, E8): the next ones not in it."""
        if self.args.smoke:
            ids = [int(x) for x in (REPO / "spec/golden/manifest_20.txt").read_text().split()]
            return ids[15:]
        have = set(self.tier_ids(tier))
        pool = (REPO / "spec/corpus/manifest_10000.txt").read_text().split()
        return [int(x) for x in pool if int(x) not in have][:n]

    def manifest(self, name: str, ids: list[int]) -> Path:
        path = self.manifests / f"{name}.txt"
        path.write_text("".join(f"{i}\n" for i in ids))
        return path

    def py(self) -> list[str]:
        return self.engines("python")

    def datalake_snapshot(self, layout: str, tier: int) -> Path:
        """A workspace holding the tier downloaded in `layout`, built once with
        the Python reference.  Conformance guarantees every language would
        produce the same datalake, so the setup is language-neutral."""
        snap = self.snapshots / f"datalake-{layout}-{tier}{self.time_tag(layout)}"
        if not (snap / "control" / "downloaded_books.txt").exists():
            clean_workspace(snap)
            ids = self.tier_ids(tier)
            # time layout: one --now per slice of --books-per-hour books, one
            # hour apart, so the tier spreads over date/hour folders as a
            # crawler running for hours would leave it (module docstring).
            # The other layouts ignore --now: one slice is the same thing.
            step = self.args.books_per_hour if layout == "time" else len(ids)
            start = datetime.fromisoformat(NOW.replace("Z", "+00:00"))
            for k, i in enumerate(range(0, len(ids), max(step, 1))):
                now = (start + timedelta(hours=k)).strftime("%Y-%m-%dT%H:%M:%SZ")
                m = self.manifest(f"tier-{tier}-slice", ids[i:i + step])
                run_setup(cli(self.py(), snap, layout, "json", "download", "--manifest", str(m),
                              "--workers", "8", "--source-base", self.base, now=now),
                          self.env)
        return snap

    def time_tag(self, layout: str) -> str:
        """Snapshot-name suffix, so a cache built with another spread (or with
        the old single --now) is never reused by mistake."""
        return f"-h{self.args.books_per_hour}" if layout == "time" else ""

    def indexed_snapshot(self, backend: str, tier: int) -> Path:
        """hash-layout tier, indexed with `backend`, plus extra books downloaded
        but not indexed: the starting point of E8."""
        snap = self.snapshots / f"indexed-{backend}-{tier}"
        if not (snap / "control" / "indexed_books.txt").exists():
            copy_snapshot(self.datalake_snapshot("hash", tier), snap)
            run_setup(cli(self.py(), snap, "hash", backend, "index", "--all", "--positions"),
                      self.env)
            m = self.manifest(f"extra-{tier}", self.extra_ids(tier))
            run_setup(cli(self.py(), snap, "hash", backend, "download", "--manifest", str(m),
                          "--source-base", self.base), self.env)
        return snap

    # ---------------------------------------------------------- records

    def record(self, *, experiment, metric, value, unit, lang, layout, backend, tier,
               workers=None, rep=None, positions=None, aux=None) -> dict:
        rec = {
            "run_id": self.run_id, "spec_version": SPEC_VERSION, "language": lang,
            "impl_version": self.impl_version, "experiment": experiment,
            "datalake_layout": layout, "index_backend": backend, "positions": positions,
            "corpus_size": tier, "workers": workers, "batch_size": 500 if backend else None,
            "repetition": rep, "metric": metric, "value": round(value, 3), "unit": unit,
            "aux": {"layer": "macro", "cache": self.state.get("cache", "n/a"), **(aux or {})},
            "machine_id": self.args.machine_id,
            "started_at": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
        }
        with open(self.raw, "a", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(rec, separators=(",", ":")) + "\n")
        return rec

    def measure(self, *, experiment, lang, layout, backend, tier, setup, argv,
                ws, workers=None, positions=None, post=None, ok=(0,),
                expect_lines=None) -> None:
        label = f"{experiment} {lang} {layout or '-'} {backend or '-'} n={tier}" + \
                (f" w={workers}" if workers else "")
        for r in range(self.args.warmup + self.args.reps):
            setup()                                         # outside the timer
            measured = r >= self.args.warmup
            metrics = self.work / ".metrics.jsonl"
            metrics.unlink(missing_ok=True)
            if measured:
                drop_system_caches(self.state)
            code, wall, rss, err, out = run_measured(with_metrics_out(argv, metrics), self.env)
            inner = internal_wall_ms(metrics)
            if code not in ok:
                raise RunFailed(f"{label}: exit {code}\n{err[-2000:]}")
            if expect_lines is not None and len(out.split()) != expect_lines:
                raise RunFailed(f"{label}: expected {expect_lines} ids, got {len(out.split())}")
            if not measured:
                print(f"[RUNNER] {label}  warm-up {wall:9.1f} ms", file=sys.stderr)
                continue
            aux = {"peak_rss_bytes": rss}
            if post:
                aux.update(post())
            self.record(experiment=experiment, metric="wall_time", value=wall, unit="ms",
                        lang=lang, layout=layout, backend=backend, tier=tier,
                        workers=workers, rep=r - self.args.warmup + 1,
                        positions=positions, aux=aux)
            if inner is not None:
                self.record(experiment=experiment, metric="wall_time_internal", value=inner,
                            unit="ms", lang=lang, layout=layout, backend=backend, tier=tier,
                            workers=workers, rep=r - self.args.warmup + 1,
                            positions=positions, aux=aux)
            print(f"[RUNNER] {label}  rep {r - self.args.warmup + 1} {wall:9.1f} ms  "
                  f"(inside {inner if inner is None else round(inner, 1)} ms)  "
                  f"rss {rss and rss // 2**20} MiB", file=sys.stderr)

    # ------------------------------------------------------ experiments

    def e1(self, tiers, experiment="E1", workers_list=None) -> None:
        """Download and write throughput: lang x layout x workers."""
        for tier in tiers:
            m = self.manifest(f"tier-{tier}", self.tier_ids(tier))
            for lang in self.args.languages:
                for layout in self.args.layouts:
                    for w in workers_list or self.args.workers:
                        ws = self.work / "ws"
                        self.measure(
                            experiment=experiment, lang=lang, layout=layout, backend=None,
                            tier=tier, workers=w, ws=ws, setup=lambda: clean_workspace(ws),
                            argv=cli(self.engines(lang), ws, layout, "json", "download",
                                     "--manifest", str(m), "--workers", str(w),
                                     "--source-base", self.base))

    def e3_snapshot(self, layout: str, tier: int) -> tuple[Path, str]:
        """The tier downloaded earlier, then 50 more "today": the state in which
        the pipeline has to find what is new.  Returns (workspace, since)."""
        snap = self.snapshots / f"incremental-{layout}-{tier}{self.time_tag(layout)}"
        stamp = snap / ".since"
        if not stamp.exists():
            copy_snapshot(self.datalake_snapshot(layout, tier), snap)  # keeps mtimes
            # the tier is marked indexed: only the 50 new books are "ready"
            done = (snap / "control" / "downloaded_books.txt").read_text()
            (snap / "control" / "indexed_books.txt").write_text(done)
            time.sleep(1.1)  # mtime resolution: the new books must be strictly newer
            since = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            m = self.manifest(f"extra-{tier}", self.extra_ids(tier))
            # no --now: the new books land in today's date/hour folder, the tier
            # stays in the fixed past one -- exactly the incremental situation
            run_setup([*self.py(), "--workspace", str(snap), "--datalake-layout", layout,
                       "download", "--manifest", str(m), "--source-base", self.base],
                      self.env)
            stamp.write_text(since)
        return snap, stamp.read_text().strip()

    def e3(self, tiers) -> None:
        """Incremental processing, as the brief defines it: the cost of
        DETECTING which books are new and ready to be indexed.  `scan-new
        --since` on a tier ingested earlier plus 50 books ingested now."""
        for tier in tiers:
            n_new = len(self.extra_ids(tier))
            for layout in self.args.layouts:
                snap, since = self.e3_snapshot(layout, tier)
                for lang in self.args.languages:
                    ws = self.work / "ws"

                    def check(ws=ws):  # the answer must be the 50 new books, no more
                        return {"books_new": n_new}

                    self.measure(
                        experiment="E3", lang=lang, layout=layout, backend=None, tier=tier,
                        ws=ws, setup=lambda s=snap: copy_snapshot(s, ws), post=check,
                        expect_lines=n_new,
                        argv=cli(self.engines(lang), ws, layout, "json", "scan-new",
                                 "--since", since))

    def e5(self, tiers) -> None:
        """Storage overhead of each layout (language-independent)."""
        for tier in tiers:
            for layout in self.args.layouts:
                stats = tree_stats(self.datalake_snapshot(layout, tier) / "datalake")
                n = len(self.tier_ids(tier))
                aux = {**stats, "bytes_per_book": stats["bytes"] / n,
                       "allocated_per_book": stats["allocated_bytes"] / n,
                       "overhead_vs_bytes": stats["allocated_bytes"] / stats["bytes"] - 1
                       if stats["bytes"] else None}
                for metric, key in (("storage_bytes", "bytes"),
                                    ("storage_allocated_bytes", "allocated_bytes")):
                    self.record(experiment="E5", metric=metric, value=stats[key],
                                unit="bytes", lang="python", layout=layout, backend=None,
                                tier=tier, aux=aux)
                print(f"[RUNNER] E5 {layout} n={tier}: {stats}", file=sys.stderr)

    def e6(self, tiers, experiment="E6") -> None:
        """Index build: lang x backend on the hash layout.  E9 rides along:
        peak RSS and the size of the index on disk."""
        for tier in tiers:
            snap = self.datalake_snapshot("hash", tier)
            for lang in self.args.languages:
                for backend in self.args.backends:
                    ws = self.work / "ws"
                    self.measure(
                        experiment=experiment, lang=lang, layout="hash", backend=backend,
                        tier=tier, ws=ws, positions=True,
                        setup=lambda: copy_snapshot(snap, ws),
                        post=lambda: {f"index_{k}": v for k, v in
                                      tree_stats(ws / "datamarts").items()},
                        argv=cli(self.engines(lang), ws, "hash", backend, "index", "--all",
                                 "--positions"))

    def e8(self, tiers) -> None:
        """Update: +50 books onto an indexed tier."""
        for tier in tiers:
            for backend in self.args.backends:
                snap = self.indexed_snapshot(backend, tier)
                for lang in self.args.languages:
                    ws = self.work / "ws"
                    self.measure(
                        experiment="E8", lang=lang, layout="hash", backend=backend, tier=tier,
                        ws=ws, positions=True, setup=lambda s=snap: copy_snapshot(s, ws),
                        argv=cli(self.engines(lang), ws, "hash", backend, "index", "--all",
                                 "--positions"))

    def e4(self, tiers) -> None:
        """Recovery: SIGKILL half-way through a download, then reconcile and resume."""
        for tier in tiers:
            ids = self.tier_ids(tier)
            m = self.manifest(f"tier-{tier}", ids)
            for lang in self.args.languages:
                for layout in self.args.layouts:
                    for rep in range(1, self.args.reps + 1):
                        ws = self.work / "ws"
                        clean_workspace(ws)
                        eng = self.engines(lang)
                        child = subprocess.Popen(
                            cli(eng, ws, layout, "json", "download", "--manifest", str(m),
                                "--source-base", self.base),
                            env=self.env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                        done = ws / "control" / "downloaded_books.txt"
                        while child.poll() is None:
                            if done.exists() and len(done.read_text().split()) >= len(ids) // 2:
                                child.kill()
                                break
                            time.sleep(0.01)
                        child.wait()
                        listed = done.read_text().split() if done.exists() else []
                        bodies = {p.name.split(".")[0] for p in (ws / "datalake").rglob("*body.txt")} \
                            if layout != "book" else \
                            {p.parent.name for p in (ws / "datalake").rglob("body.txt")}
                        before = {"listed": len(listed), "duplicates": len(listed) - len(set(listed)),
                                  "unrecorded_on_disk": len(bodies - set(listed)),
                                  "recorded_missing": len(set(listed) - bodies)}
                        t0 = time.perf_counter_ns()
                        run_setup(cli(eng, ws, layout, "json", "reconcile"), self.env)
                        run_setup(cli(eng, ws, layout, "json", "download", "--manifest", str(m),
                                      "--source-base", self.base), self.env)
                        wall = (time.perf_counter_ns() - t0) / 1e6
                        final = done.read_text().split()
                        after = {"final_listed": len(final),
                                 "final_duplicates": len(final) - len(set(final)),
                                 "final_lost": len(set(map(str, ids)) - set(final))}
                        self.record(experiment="E4", metric="recovery_time", value=wall,
                                    unit="ms", lang=lang, layout=layout, backend=None,
                                    tier=tier, rep=rep, aux={**before, **after})
                        print(f"[RUNNER] E4 {lang} {layout} rep {rep}: {before} -> {after}",
                              file=sys.stderr)

    # ---------------------------------------------------------- summary

    def summarize(self) -> Path:
        rows: dict[tuple, list[dict]] = {}
        for line in self.raw.read_text(encoding="utf-8").splitlines():
            r = json.loads(line)
            key = (r["experiment"], r["metric"], r["unit"], r["language"],
                   r["datalake_layout"] or "", r["index_backend"] or "",
                   r["corpus_size"], r["workers"] or "")
            rows.setdefault(key, []).append(r)
        out = self.results / "summary.csv"
        with open(out, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["experiment", "metric", "unit", "language", "layout", "backend",
                        "corpus_size", "workers", "n", "median", "q1", "q3", "iqr",
                        "min", "max", "median_peak_rss_mib", "cache"])
            for key, recs in sorted(rows.items(), key=lambda kv: tuple(map(str, kv[0]))):
                vals = sorted(r["value"] for r in recs)
                q1, q3 = (statistics.quantiles(vals, n=4, method="inclusive")[::2]
                          if len(vals) > 1 else (vals[0], vals[0]))
                rss = [r["aux"].get("peak_rss_bytes") for r in recs
                       if r["aux"].get("peak_rss_bytes")]
                w.writerow([*key, len(vals), round(statistics.median(vals), 3), round(q1, 3),
                            round(q3, 3), round(q3 - q1, 3), vals[0], vals[-1],
                            round(statistics.median(rss) / 2**20, 1) if rss else "",
                            "/".join(sorted({r["aux"].get("cache", "") for r in recs}))])
        return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Stage 1 benchmark runner")
    ap.add_argument("--experiments", default="E1,E3,E4,E5,E6,E8",
                    help="comma list of E1,E3,E4,E5,E6,E8,E10,E11")
    ap.add_argument("--languages", default="python")
    ap.add_argument("--layouts", default=",".join(LAYOUTS))
    ap.add_argument("--backends", default=",".join(BACKENDS))
    ap.add_argument("--tiers", default="100,1000")
    ap.add_argument("--scaling-tiers", default="100,1000,10000", help="for E10 and E11")
    ap.add_argument("--workers", default="1,8")
    ap.add_argument("--reps", type=int, default=5)
    ap.add_argument("--warmup", type=int, default=1)
    ap.add_argument("--work", default="~/bench/work")
    ap.add_argument("--results", default=str(REPO / "results"))
    ap.add_argument("--mirror", default=str(REPO / "infra/mirror"))
    ap.add_argument("--min-free-gb", type=float, default=20)
    ap.add_argument("--books-per-hour", type=int, default=100,
                    help="time-layout snapshots: books per date/hour folder (see docstring)")
    ap.add_argument("--machine-id", default=socket.gethostname())
    ap.add_argument("--smoke", action="store_true",
                    help="golden books, 1 rep, no warm-up: checks the machinery")
    args = ap.parse_args()

    split = lambda s: [x.strip() for x in s.split(",") if x.strip()]  # noqa: E731
    args.languages, args.layouts, args.backends = (split(args.languages),
                                                   split(args.layouts), split(args.backends))
    args.workers = [int(x) for x in split(args.workers)]
    tiers = [int(x) for x in split(args.tiers)]
    scaling = [int(x) for x in split(args.scaling_tiers)]
    if args.smoke:
        args.reps, args.warmup, args.mirror = 1, 0, str(REPO / "spec/golden")
        args.results = str(Path(args.results) / "smoke")
        tiers = scaling = [15]  # 15 golden books + 5 extra for E3 and E8

    runner = Runner(args)
    runner.preflight()
    runner.start_mirror(Path(args.mirror))
    try:
        for exp in split(args.experiments):
            print(f"=== {exp} ===", file=sys.stderr)
            {"E1": lambda: runner.e1(tiers),
             "E3": lambda: runner.e3(tiers),
             "E4": lambda: runner.e4(tiers),
             "E5": lambda: runner.e5(tiers),
             "E6": lambda: runner.e6(tiers),
             "E8": lambda: runner.e8(tiers),
             "E10": lambda: runner.e1(scaling, "E10", [1]),
             "E11": lambda: runner.e6(scaling, "E11")}[exp]()
    except RunFailed as exc:
        print(f"[RUNNER] FAILED: {exc}", file=sys.stderr)
        return 1
    finally:
        runner.stop_mirror()
        if runner.raw.exists():
            print(f"[RUNNER] summary -> {runner.summarize()}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
