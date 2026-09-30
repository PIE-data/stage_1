import test from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import Ajv from "ajv";
import addFormats from "ajv-formats";

import { createMetricsRecord, measureCommand } from "./metrics.js";

const schema = JSON.parse(readFileSync(
  new URL("../../spec/schemas/metrics.schema.json", import.meta.url),
  "utf8",
));

const specVersion = readFileSync(
  new URL("../../spec/SPEC_VERSION", import.meta.url),
  "utf8",
).trim();

const ajv = new Ajv({ allErrors: true });
addFormats(ajv);
const validate = ajv.compile(schema);

test("written metrics satisfy the shared schema with and without benchmark fields", async (t) => {
  const root = mkdtempSync(join(tmpdir(), "metrics-schema-"));
  t.after(() => rmSync(root, { recursive: true, force: true }));

  const path = join(root, "metrics.jsonl");
  const environments = [
    {},
    {
      BENCH_RUN_ID: "schema-test",
      BENCH_IMPL_VERSION: "git:abc123",
      BENCH_EXPERIMENT: "E7_query",
      BENCH_MACHINE_ID: "test-machine",
      BENCH_REPETITION: "3",
      BENCH_CORPUS_SIZE: "20",
    },
  ];

  for (const env of environments) {
    const record = createMetricsRecord({
      command: "query",
      specVersion,
      layout: "hash",
      indexBackend: "sqlite",
      env,
    });
    record.positions = true;

    await measureCommand({
      path,
      record,
      operation: async () => 0,
    });
  }

  const rows = readFileSync(path, "utf8")
    .trimEnd()
    .split("\n")
    .map(JSON.parse);

  assert.equal(rows.length, environments.length);

  for (const row of rows) {
    assert.equal(validate(row), true, ajv.errorsText(validate.errors));
    assert.equal(row.positions, true);
  }

  // Verify that validation detects malformed records rather than accepting all input.
  const valid = rows[0];

  for (const [field, value] of [
    ["value", -1],
    ["value", null],
    ["workers", 0],
    ["batch_size", 0],
    ["positions", "true"],
    ["corpus_size", "20"],
    ["started_at", "not-a-date"],
    ["unit", "seconds"],
  ]) {
    const invalid = structuredClone(valid);
    invalid[field] = value;
    assert.equal(validate(invalid), false, `Must reject invalid ${field}`);
  }

  const missing = structuredClone(valid);
  delete missing.spec_version;
  assert.equal(validate(missing), false);

  const invalidCounter = structuredClone(valid);
  invalidCounter.aux.bytes_written = -1;
  assert.equal(validate(invalidCounter), false);
});