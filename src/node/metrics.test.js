import test from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir, hostname } from "node:os";
import { join } from "node:path";
import { createMetricsRecord, measureCommand } from "./metrics.js";

function record(env = {}) {
  return createMetricsRecord({
    command: "metadata",
    specVersion: "1.1.5",
    layout: "hash",
    env,
  });
}

test("metrics use specified defaults and do not invent counters", () => {
  const result = record();

  assert.equal(result.run_id, null);
  assert.equal(result.impl_version, null);
  assert.equal(result.experiment, "cli_metadata");
  assert.equal(result.machine_id, hostname());
  assert.equal(result.repetition, null);
  assert.equal(result.corpus_size, null);
  assert.equal(result.language, "node");
  assert.equal(result.batch_size, 500);

  for (const value of Object.values(result.aux)) {
    assert.equal(value, null);
  }
});

test("benchmark environment fields are copied with numeric types", () => {
  const result = record({
    BENCH_RUN_ID: "run-42",
    BENCH_IMPL_VERSION: "git:abc123",
    BENCH_EXPERIMENT: "E7_metadata",
    BENCH_MACHINE_ID: "bench-01",
    BENCH_REPETITION: "3",
    BENCH_CORPUS_SIZE: "20",
  });

  assert.equal(result.run_id, "run-42");
  assert.equal(result.impl_version, "git:abc123");
  assert.equal(result.experiment, "E7_metadata");
  assert.equal(result.machine_id, "bench-01");
  assert.equal(result.repetition, 3);
  assert.equal(result.corpus_size, 20);

  assert.throws(() => record({ BENCH_REPETITION: "invalid" }));
});

test("measurement appends JSONL using monotonic elapsed milliseconds", async (t) => {
  const root = mkdtempSync(join(tmpdir(), "metrics-"));
  t.after(() => rmSync(root, { recursive: true, force: true }));

  const path = join(root, "nested", "metrics.jsonl");

  for (let i = 0; i < 2; i += 1) {
    const ticks = [10_000_000n, 12_500_000n];

    const result = await measureCommand({
      path,
      record: record(),
      clock: () => ticks.shift(),
      operation: async () => 3,
    });

    assert.equal(result, 3);
  }

  const text = readFileSync(path, "utf8");
  assert.ok(text.endsWith("\n"));

  const rows = text.trimEnd().split("\n").map(JSON.parse);
  assert.equal(rows.length, 2);

  for (const row of rows) {
    assert.equal(row.value, 2.5);
    assert.equal(row.unit, "ms");
    assert.equal(row.metric, "wall_time");
    assert.ok(Number.isFinite(Date.parse(row.started_at)));
  }
});

test("an operation failure is propagated after recording its duration", async (t) => {
  const root = mkdtempSync(join(tmpdir(), "metrics-error-"));
  t.after(() => rmSync(root, { recursive: true, force: true }));

  const path = join(root, "metrics.jsonl");
  const failure = new Error("Operation failed");

  await assert.rejects(
    measureCommand({
      path,
      record: record(),
      operation: async () => {
        throw failure;
      },
    }),
    (error) => error === failure,
  );

  const row = JSON.parse(readFileSync(path, "utf8"));
  assert.ok(row.value >= 0);
});