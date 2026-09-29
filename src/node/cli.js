import { readFileSync } from "node:fs";
import { parseArgs } from "node:util";
import { createMetricsRecord, measureCommand } from "./metrics.js";

const SUPPORTED_SPEC_VERSION = "1.1.5";
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
    !["version", "download", "split", "lookup", "scan-new", "metadata", "reconcile"].includes(command)
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

  if (command === "metadata") {
  allowed.add("book-id");
  allowed.add("all");
  allowed.add("batch-size");
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

  if (command === "metadata") {
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

    return await measureCommand({
      path: values["metrics-out"],
      record,
      operation,
    });
  } catch (error) {
    console.error(error.message);

    if (error instanceof ArgumentError) {
      console.error(
        "Usage: node src/node/cli.js --workspace <path> " +
        "[--datalake-layout time|book|hash] [--now <ISO8601>] " +
        "[--metrics-out <path>] " +
        "<version|download|split|metadata|lookup|scan-new|reconcile> [command options]",
      );
      return 2;
    }

    if (error.name === "WorkspaceLockedError") return 4;
    return 1;
  }
}

process.exitCode = await main();