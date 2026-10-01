import { readFileSync } from "node:fs";
import { parseArgs } from "node:util";
import { createMetricsRecord, measureCommand } from "./metrics.js";

const SUPPORTED_SPEC_VERSION = "1.1.8";
const versionFile = new URL("../../spec/SPEC_VERSION", import.meta.url);

class ArgumentError extends Error {}

function positiveInteger(value, name) {
  if (!/^[1-9][0-9]*$/u.test(value ?? "")) {
    throw new ArgumentError(`${name} must be a positive integer`);
  }

  const result = Number(value);

  if (!Number.isSafeInteger(result)) {
    throw new ArgumentError(`${name} exceeds the safe integer range`);
  }

  return result;
}

function parseInstant(value) {
  if (value === undefined) return new Date();

  const match = /^(\d{4})-(\d{2})-(\d{2})T\d{2}:\d{2}:\d{2}(?:\.\d{1,3})?(?:Z|[+-]\d{2}:\d{2})$/u.exec(value);
  const date = new Date(value);

  if (!match || !Number.isFinite(date.getTime())) {
    throw new ArgumentError("--now requires an ISO8601 timestamp with timezone");
  }

  const year = Number(match[1]);
  const month = Number(match[2]);
  const day = Number(match[3]);
  const leap = year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0);
  const days = [31, leap ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];

  if (month < 1 || month > 12 || day < 1 || day > days[month - 1]) {
    throw new ArgumentError("--now contains an invalid calendar date");
  }

  return date;
}

function parseCommand() {
  let parsed;

  try {
    parsed = parseArgs({
      options: {
        workspace: { type: "string" },
        "datalake-layout": { type: "string" },
        now: { type: "string" },
        "book-id": { type: "string" },
        manifest: { type: "string" },
        workers: { type: "string" },
        "source-base": { type: "string" },
        since: { type: "string" },
        all: { type: "boolean" },
        "batch-size": { type: "string" },
        "metrics-out": { type: "string" },
        "index-backend": { type: "string" },
        positions: { type: "boolean" },
        out: { type: "string" },
        terms: { type: "string" },
        mode: { type: "string" },
        limit: { type: "string" },
        iterations: { type: "string" },
        "total-books": { type: "string" },
      },
      allowPositionals: true,
      strict: true,
    });
  } catch (error) {
    throw new ArgumentError(error.message);
  }

  const { values, positionals } = parsed;
  const command = positionals[0];

  if (
    !values.workspace ||
    positionals.length !== 1 ||
    !["version", "download", "split", "lookup", "scan-new", "metadata", "reconcile", "index", "export-canonical", "query", "control-step"].includes(command)
  ) {
    throw new ArgumentError("A workspace and a supported command are required");
  }

const allowed = new Set([
  "workspace",
  "datalake-layout",
  "now",
  "metrics-out",
  "index-backend",
]);
  if (command === "split" || command === "lookup") {
  allowed.add("book-id");
  }

  if (command === "scan-new") {
  allowed.add("since");
  }

  if (command === "download") {
    for (const name of ["book-id", "manifest", "workers", "source-base"]) {
      allowed.add(name);
    }
  }

  if (command === "metadata" || command === "index") {
  allowed.add("book-id");
  allowed.add("all");
  allowed.add("batch-size");
  }

    if (command === "index") {
    allowed.add("positions");
  }

  if (command === "export-canonical") {
    allowed.add("out");
  }

    if (command === "query") {
    allowed.add("terms");
    allowed.add("mode");
    allowed.add("limit");
  }

      if (command === "control-step") {
    for (const name of [
      "iterations", "total-books", "manifest", "source-base",
    ]) {
      allowed.add(name);
    }
  }

  for (const name of Object.keys(values)) {
    if (!allowed.has(name)) {
      throw new ArgumentError(`--${name} is not valid for ${command}`);
    }
  }

  const layout = values["datalake-layout"] ?? "time";

  if (!["time", "book", "hash"].includes(layout)) {
    throw new ArgumentError("--datalake-layout must be time, book or hash");
  }

  const now = parseInstant(values.now);
  let bookId;
  let workers = 1;

  if (command === "split" || command === "lookup") {
  bookId = positiveInteger(values["book-id"], "--book-id");
  }

  if (command === "scan-new" && values.since !== undefined) {
    try {
      parseInstant(values.since);
    } catch (error) {
      throw new ArgumentError(error.message.replaceAll("--now", "--since"));
    }
  }

  if (command === "download") {
    const hasId = values["book-id"] !== undefined;
    const hasManifest = values.manifest !== undefined;

    if (hasId === hasManifest || (hasManifest && !values.manifest)) {
      throw new ArgumentError("Choose exactly one of --book-id or --manifest");
    }

    if (hasId) bookId = positiveInteger(values["book-id"], "--book-id");
    workers = positiveInteger(values.workers ?? "1", "--workers");

    if (values["source-base"] !== undefined) {
      let url;

      try {
        url = new URL(values["source-base"]);
      } catch {
        throw new ArgumentError("--source-base must be an HTTP(S) URL");
      }

      if (!["http:", "https:"].includes(url.protocol) || url.search || url.hash) {
        throw new ArgumentError("--source-base must be HTTP(S), without query or fragment");
      }
    }
  }

  if (command === "metadata" || command === "index") {
  const hasId = values["book-id"] !== undefined;
  const hasAll = values.all === true;

  if (hasId === hasAll) {
    throw new ArgumentError("Choose exactly one of --book-id or --all");
  }

  if (hasId) {
    bookId = positiveInteger(values["book-id"], "--book-id");
  }

  positiveInteger(values["batch-size"] ?? "500", "--batch-size");
}

  if (
  values["metrics-out"] !== undefined &&
  values["metrics-out"].trim() === ""
) {
  throw new ArgumentError("--metrics-out requires a non-empty path");
}

if (
  !["json", "folder", "sqlite", "mongo"].includes(
    values["index-backend"] ?? "json",
  )
) {
  throw new ArgumentError("--index-backend must be json, folder, sqlite or mongo");
}

  if (command === "export-canonical" && !values.out?.trim()) {
    throw new ArgumentError("export-canonical requires --out <path>");
  }

  if (
    ["index", "export-canonical"].includes(command) &&
    (values["index-backend"] ?? "json") === "mongo"
  ) {
    throw new ArgumentError("MongoDB indexing is not implemented");
  }

    if (command === "query") {
    if (values.terms === undefined) {
      throw new ArgumentError("query requires --terms");
    }

    if (!["and", "or"].includes(values.mode)) {
      throw new ArgumentError("--mode must be and or or");
    }

    if (
      values.limit !== undefined &&
      (
        !/^(0|[1-9][0-9]*)$/u.test(values.limit) ||
        !Number.isSafeInteger(Number(values.limit))
      )
    ) {
      throw new ArgumentError("--limit must be a non-negative safe integer");
    }

    if ((values["index-backend"] ?? "json") === "mongo") {
      throw new ArgumentError("MongoDB queries are not implemented");
    }
  }

      if (command === "control-step") {
    positiveInteger(values.iterations, "--iterations");
    positiveInteger(values["total-books"] ?? "70000", "--total-books");

    if (values.manifest !== undefined && !values.manifest.trim()) {
      throw new ArgumentError("--manifest requires a non-empty path");
    }

    if ((values["index-backend"] ?? "json") === "mongo") {
      throw new ArgumentError("MongoDB control-step is not implemented");
    }

    if (values["source-base"] !== undefined) {
      let url;

      try {
        url = new URL(values["source-base"]);
      } catch {
        throw new ArgumentError("--source-base must be an HTTP(S) URL");
      }

      if (!["http:", "https:"].includes(url.protocol) || url.search || url.hash) {
        throw new ArgumentError(
          "--source-base must be HTTP(S), without query or fragment",
        );
      }
    }
  }

  return { command, values, layout, now, bookId, workers };
}


function readManifest(path) {
  const ids = [];

  for (const line of readFileSync(path, "utf8").split(/\r\n|\n|\r/u)) {
    const value = line.trim();
    if (value === "" || value.startsWith("#")) continue;
    ids.push(positiveInteger(value, "Manifest book ID"));
  }

  return ids;
}

async function prepareOperation(context) {
  const { command, values, layout, now, bookId, workers } = context;

    if (command === "control-step") {
    const { controlStep } = await import("./control/control_step.js");
    const bookIds = values.manifest === undefined
      ? undefined
      : readManifest(values.manifest);

    return async () => {
      const result = await controlStep({
        workspace: values.workspace,
        layout,
        backend: values["index-backend"] ?? "json",
        iterations: Number(values.iterations),
        totalBooks: Number(values["total-books"] ?? "70000"),
        bookIds,
        sourceBase: values["source-base"],
        now,
      });

      console.error(
        `control-step: iterations ${result.iterations}, ` +
        `downloaded ${result.downloaded}, indexed ${result.indexed}, ` +
        `failed ${result.failed}`,
      );

      return result.exitCode;
    };
  }

    if (command === "query") {
    const { queryIndex } = await import("./query.js");

    return async () => {
      const ids = await queryIndex({
        workspace: values.workspace,
        backend: values["index-backend"] ?? "json",
        terms: values.terms,
        mode: values.mode,
        limit: values.limit === undefined ? undefined : Number(values.limit),
      });

      if (ids.length > 0) {
        process.stdout.write(`${ids.join("\n")}\n`);
      }

      return 0;
    };
  }

    if (command === "index") {
    const { indexBooks } = await import("./index_pipeline.js");
    const batchSize = positiveInteger(
      values["batch-size"] ?? "500",
      "--batch-size",
    );

    return async () => {
      const result = await indexBooks({
        workspace: values.workspace,
        layout,
        backend: values["index-backend"] ?? "json",
        bookId,
        all: values.all === true,
        positions: values.positions === true,
        batchSize,
      });

      console.error(`index: processed ${result.processed}`);
      return result.exitCode;
    };
  }

  if (command === "export-canonical") {
    const { exportIndex } = await import("./index_pipeline.js");

    return () => exportIndex({
      workspace: values.workspace,
      backend: values["index-backend"] ?? "json",
      out: values.out,
    });
  }

    if (command === "reconcile") {
    const { reconcileWorkspace } = await import("./control/reconcile.js");

    return async () => {
      const result = await reconcileWorkspace({
        workspace: values.workspace,
        layout,
      });

      console.error(
        `reconcile: downloaded ${result.downloaded}, ` +
        `indexed ${result.indexed}, ` +
        `partials removed ${result.partialsRemoved}`,
      );

      return 0;
    };
  }

  if (command === "metadata") {
    const { generateMetadata } = await import("./metadata_pipeline.js");
    const batchSize = positiveInteger(
      values["batch-size"] ?? "500",
      "--batch-size",
    );

    return async () => {
      const result = await generateMetadata({
        workspace: values.workspace,
        layout,
        bookId,
        all: values.all === true,
        batchSize,
      });

      console.error(
        `metadata: processed ${result.processed}, written ${result.written}, ` +
        `meta files written ${result.metaFilesWritten}`,
      );

      return result.exitCode;
    };
  }

  if (command === "lookup" || command === "scan-new") {
    const { lookupBook, scanNewBooks } = await import("./datalake_queries.js");
    const since = values.since === undefined
      ? undefined
      : parseInstant(values.since);

    return async () => {
      if (command === "lookup") {
        const paths = lookupBook({
          workspace: values.workspace,
          layout,
          bookId,
        });

        if (!paths) return 3;

        process.stdout.write(`${paths[0]}\t${paths[1]}\n`);
        return 0;
      }

      const ids = scanNewBooks({
        workspace: values.workspace,
        layout,
        since,
      });

      if (ids.length > 0) {
        process.stdout.write(`${ids.join("\n")}\n`);
      }

      return 0;
    };
  }

  const { downloadBooks, splitCachedBook } = await import("./ingestion.js");

  const common = {
    workspace: values.workspace,
    layout,
    now,
  };

  if (command === "split") {
    return () => splitCachedBook({ ...common, bookId });
  }

  // Validate manifest IDs before starting the command timer.
  const bookIds = bookId === undefined
    ? readManifest(values.manifest)
    : [bookId];

  return () => downloadBooks({
    ...common,
    bookIds,
    workers,
    sourceBase: values["source-base"],
  });
}

async function main() {
  try {
    const context = parseCommand();
    const { command, values, layout, workers } = context;
    const version = readFileSync(versionFile, "utf8").trim();

    if (version !== SUPPORTED_SPEC_VERSION) {
      throw new Error(
        `Spec mismatch: supported ${SUPPORTED_SPEC_VERSION}, found ${version}`,
      );
    }

    if (command === "version") {
      console.log(version);
      console.error(`stage-1-node 0.1.0 | Node ${process.version}`);
      return 0;
    }

    const operation = await prepareOperation(context);

    if (values["metrics-out"] === undefined) {
      return await operation();
    }

    const record = createMetricsRecord({
      command,
      specVersion: version,
      layout,
      indexBackend: values["index-backend"] ?? "json",
      workers,
      batchSize: positiveInteger(
        values["batch-size"] ?? "500",
        "--batch-size",
      ),
    });

        if (command === "index") {
      record.positions = values.positions === true;
        } else if (command === "control-step") {
      const { readIndexConfig } = await import("./index_pipeline.js");
      const config = readIndexConfig(
        values.workspace,
        values["index-backend"] ?? "json",
      );
      record.positions = config?.positions ?? true;
      record.batch_size = 1;
    } else if (command === "export-canonical" || command === "query") {
      const { readIndexConfig } = await import("./index_pipeline.js");
      const config = readIndexConfig(
        values.workspace,
        values["index-backend"] ?? "json",
      );
      record.positions = config?.positions ?? false;
    }

    return await measureCommand({
      path: values["metrics-out"],
      record,
      operation,
    });
  } catch (error) {
    console.error(error.message);

    if (
    error instanceof ArgumentError ||
    error.name === "IndexArgumentError"
    ) {
      console.error(
        "Usage: node src/node/cli.js --workspace <path> " +
        "[--datalake-layout time|book|hash] [--now <ISO8601>] " +
        "[--metrics-out <path>] " +
        "<version|download|split|metadata|lookup|scan-new|reconcile|index|export-canonical|Usage> [command options]",
      );
      return 2;
    }

    if (error.name === "WorkspaceLockedError") return 4;
    return 1;
  }
}

process.exitCode = await main();