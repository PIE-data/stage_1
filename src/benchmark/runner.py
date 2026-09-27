#!/usr/bin/env python3
"""
Benchmark runner for Stage 1.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

# Number of total runs (1 warmup + 2 actual measurements)
REPETITIONS = 3

def drop_system_caches() -> None:
    """
    Clears the OS pagecache, dentries, and inodes to ensure a 'cold cache' run.
    Requires root/sudo privileges on Linux/WSL2.
    """
    print("[RUNNER] Dropping OS caches (cold cache)...", file=sys.stderr)
    try:
        # Run sync first to flush pending writes
        subprocess.run(["sync"], check=True)
        # Drop caches
        subprocess.run(
            ["sudo", "sh", "-c", "echo 3 > /proc/sys/vm/drop_caches"], 
            check=True
        )
    except subprocess.CalledProcessError:
        print("[RUNNER] WARNING: Failed to drop caches. Are you running with sudo?", file=sys.stderr)

def clean_workspace(workspace: Path) -> None:
    """
    Deletes datalake, datamarts, and control directories from the workspace.
    This time is specifically EXCLUDED from the benchmark timer.
    """
    print(f"[RUNNER] Cleaning workspace: {workspace}", file=sys.stderr)
    for sub in ("datalake", "datamarts", "control", "raw"):
        target = workspace / sub
        if target.exists():
            shutil.rmtree(target)
    workspace.mkdir(parents=True, exist_ok=True)

def run_iteration(cmd: list[str], workspace: Path, metrics_file: Path, 
                  experiment: str, repetition: int, is_warmup: bool) -> None:
    """
    Executes a single run of the pipeline command.
    """
    clean_workspace(workspace)
    
    if not is_warmup:
        drop_system_caches()
    else:
        print("[RUNNER] Starting WARM-UP run (results will be ignored)...", file=sys.stderr)

    # Set environment variables expected by CLI (e.g. test_pipeline.py format)
    env = os.environ.copy()
    env["BENCH_EXPERIMENT"] = experiment
    env["BENCH_REPETITION"] = str(repetition)

    # If it's a measured run, wrap the command in `/usr/bin/time -f "%M"` 
    # to capture external Peak RSS in kilobytes
    rss_kb = None
    if not is_warmup:
        print(f"[RUNNER] Starting measured run {repetition}...", file=sys.stderr)
        time_cmd = ["/usr/bin/time", "-f", "PEAK_RSS_KB:%M"] + cmd
        
        process = subprocess.run(
            time_cmd, env=env, capture_output=True, text=True
        )
        
        # Parse RSS from stderr
        for line in process.stderr.splitlines():
            if line.startswith("PEAK_RSS_KB:"):
                rss_kb = int(line.split(":")[1].strip())
            else:
                print(line, file=sys.stderr)
                
        if process.returncode != 0:
            print(process.stdout, file=sys.stdout)
            print(f"[RUNNER] Command failed with code {process.returncode}", file=sys.stderr)
            sys.exit(process.returncode)
    else:
        # Just run it normally for warmup
        subprocess.run(cmd, env=env, check=True)

    # Inject peak RSS into the last JSONL record written by cli.py
    if not is_warmup and rss_kb is not None and metrics_file.exists():
        lines = metrics_file.read_text(encoding="utf-8").splitlines()
        if lines:
            last_record = json.loads(lines[-1])
            # Ensure aux dictionary exists
            if "aux" not in last_record:
                last_record["aux"] = {}
            # Convert KB to Bytes (1 KB = 1024 Bytes)
            last_record["aux"]["peak_rss_bytes"] = rss_kb * 1024
            
            lines[-1] = json.dumps(last_record)
            metrics_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"[RUNNER] Captured Peak RSS: {rss_kb * 1024} bytes", file=sys.stderr)

def main() -> int:
    parser = argparse.ArgumentParser(description="Stage 1 Benchmark Runner")
    parser.add_argument("--experiment", required=True, help="Experiment ID (e.g., E1, E6)")
    parser.add_argument("--workspace", required=True, help="Path to the clean workspace")
    parser.add_argument("--metrics-out", required=True, help="Path to output metrics JSONL")
    parser.add_argument("cmd", nargs=argparse.REMAINDER, help="CLI command to run (e.g. python3 src/python/cli.py ...)")
    
    args = parser.parse_args()
    workspace = Path(args.workspace)
    metrics_file = Path(args.metrics_out)
    cmd = args.cmd

    # argparse sometimes leaves '--' in the remainder
    if cmd and cmd[0] == "--":
        cmd = cmd[1:]

    if not cmd:
        print("[RUNNER] Error: No command provided to run.", file=sys.stderr)
        return 1

    # Ensure output directory exists
    metrics_file.parent.mkdir(parents=True, exist_ok=True)

    print(f"=== Starting Experiment: {args.experiment} ===", file=sys.stderr)
    
    for rep in range(1, REPETITIONS + 1):
        is_warmup = (rep == 1)
        run_iteration(
            cmd=cmd,
            workspace=workspace,
            metrics_file=metrics_file,
            experiment=args.experiment,
            repetition=rep,
            is_warmup=is_warmup
        )

    print(f"=== Experiment {args.experiment} completed successfully ===", file=sys.stderr)
    return 0

if __name__ == "__main__":
    sys.exit(main())