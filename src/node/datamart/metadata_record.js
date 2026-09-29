import { createHash } from "node:crypto";
import { readFileSync, realpathSync } from "node:fs";
import { isAbsolute, relative, resolve, sep } from "node:path";

import { parseHeader } from "./metadata_parser.js";

function workspaceFile(root, suppliedPath) {
  const absolute = realpathSync(resolve(root, suppliedPath));
  const local = relative(root, absolute);

  if (
    local === "" ||
    local === ".." ||
    local.startsWith(`..${sep}`) ||
    isAbsolute(local)
  ) {
    throw new Error("Metadata files must be inside the workspace");
  }

  return {
    absolute,
    relative: local.split(sep).join("/"),
  };
}

export function buildMetadataRecord({
  bookId,
  workspace,
  headerPath,
  bodyPath,
  ingestedAt,
  parserOptions = {},
}) {
  if (!Number.isSafeInteger(bookId) || bookId <= 0) {
    throw new TypeError("bookId must be a positive safe integer");
  }

  if (
    !(ingestedAt instanceof Date) ||
    !Number.isFinite(ingestedAt.getTime())
  ) {
    throw new TypeError("ingestedAt must be an explicit valid Date");
  }

  const root = realpathSync(resolve(workspace));
  const header = workspaceFile(root, headerPath);
  const body = workspaceFile(root, bodyPath);

  const descriptive = parseHeader(
    readFileSync(header.absolute, "utf8"),
    parserOptions,
  );

  // Hash the stored bytes without newline conversion or text normalization.
  const bodyBytes = readFileSync(body.absolute);

  return {
    book_id: bookId,
    ...descriptive,
    header_path: header.relative,
    body_path: body.relative,
    body_bytes: bodyBytes.length,
    sha256: createHash("sha256").update(bodyBytes).digest("hex"),
    ingested_at: ingestedAt.toISOString().replace(".000Z", "Z"),
  };
}