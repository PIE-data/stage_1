import { appendFileSync, mkdirSync } from "node:fs";
import { dirname } from "node:path";
import { hostname } from "node:os";

function environmentInteger(env, name) {
  const value = env[name];
  if (value === undefined) return null;

  if (!/^[0-9]+$/u.test(value) || !Number.isSafeInteger(Number(value))) {
    throw new TypeError(`${name} must be a non-negative safe integer`);
  }

  return Number(value);
}

export function createMetricsRecord({
  command,
  specVersion,
  layout,
  indexBackend = "json",
  workers = 1,
  batchSize = 500,
  env = process.env,
}) {
  return {
    run_id: env.BENCH_RUN_ID ?? null,
    spec_version: specVersion,
    language: "node",
    impl_version: env.BENCH_IMPL_VERSION ?? null,
    experiment: env.BENCH_EXPERIMENT ?? `cli_${command}`,
    datalake_layout: layout,
    index_backend: indexBackend,
    positions: false,
    corpus_size: environmentInteger(env, "BENCH_CORPUS_SIZE"),
    workers,
    batch_size: batchSize,
    repetition: environmentInteger(env, "BENCH_REPETITION"),
    metric: "wall_time",
    value: null,
    unit: "ms",
    aux: {
      peak_rss_bytes: null,
      bytes_written: null,
      files_created: null,
      dirs_created: null,
      docs_processed: null,
      terms_total: null,
    },
    machine_id: env.BENCH_MACHINE_ID ?? hostname(),
    started_at: null,
  };
}

export async function measureCommand({
  path,
  record,
  operation,
  clock = () => process.hrtime.bigint(),
}) {
  record.started_at = new Date().toISOString();
  const start = clock();

  try {
    return await operation();
  } finally {
    const end = clock();
    record.value = Number(end - start) / 1_000_000;

    mkdirSync(dirname(path), { recursive: true });
    appendFileSync(path, `${JSON.stringify(record)}\n`, "utf8");
  }
}