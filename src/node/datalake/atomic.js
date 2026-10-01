import {
  closeSync,
  fsyncSync,
  mkdirSync,
  openSync,
  renameSync,
  unlinkSync,
  writeFileSync,
  writeSync,
} from "node:fs";
import { dirname } from "node:path";

function syncDirectory(directory) {
  let descriptor;

  try {
    descriptor = openSync(directory, "r");
    fsyncSync(descriptor);
    return true;
  } catch (error) {
    // Windows may reject opening or syncing a directory.
    const unsupportedOnWindows =
      process.platform === "win32" &&
      ["EPERM", "EACCES", "EINVAL", "ENOTSUP", "EISDIR"].includes(error.code);

    if (unsupportedOnWindows) {
      return false;
    }

    throw error;
  } finally {
    if (descriptor !== undefined) {
      closeSync(descriptor);
    }
  }
}

export function atomicWrite(targetPath, content) {
  if (typeof content !== "string" && !Buffer.isBuffer(content)) {
    throw new TypeError("content must be a string or Buffer");
  }

  const directory = dirname(targetPath);
  const temporaryPath = `${targetPath}.part`;

  mkdirSync(directory, { recursive: true });

  let descriptor;
  let ownsTemporaryFile = false;

  try {
    // Exclusive creation prevents overwriting another writer's partial file.
    descriptor = openSync(temporaryPath, "wx");
    ownsTemporaryFile = true;

    // Explicit UTF-8 preserves LF bytes on every platform.
    writeFileSync(descriptor, content, { encoding: "utf8" });
    fsyncSync(descriptor);

    closeSync(descriptor);
    descriptor = undefined;

    renameSync(temporaryPath, targetPath);
    ownsTemporaryFile = false;

    return { directorySynced: syncDirectory(directory) };
  } finally {
    try {
      if (descriptor !== undefined) {
        closeSync(descriptor);
      }
    } finally {
      if (ownsTemporaryFile) {
        unlinkSync(temporaryPath);
      }
    }
  }
}
/**
 * Atomic write of content produced in pieces (SPEC §2.4), for files too large
 * to build as one string -- V8 caps a string at ~512M characters.
 * `produce(write)` calls `write(string)` any number of times; text is encoded
 * as UTF-8 and flushed to the temporary file in ~4 MiB blocks.
 */
export function atomicWriteChunks(targetPath, produce) {
  const directory = dirname(targetPath);
  const temporaryPath = `${targetPath}.part`;

  mkdirSync(directory, { recursive: true });

  let descriptor;
  let ownsTemporaryFile = false;

  try {
    descriptor = openSync(temporaryPath, "wx");
    ownsTemporaryFile = true;

    let pending = [];
    let pendingLength = 0;
    const flush = () => {
      if (pendingLength === 0) return;
      const buffer = Buffer.from(pending.join(""), "utf8");
      let offset = 0;
      while (offset < buffer.length) {
        offset += writeSync(descriptor, buffer, offset, buffer.length - offset);
      }
      pending = [];
      pendingLength = 0;
    };

    produce((text) => {
      pending.push(text);
      pendingLength += text.length;
      if (pendingLength >= 4 * 1024 * 1024) flush();
    });
    flush();
    fsyncSync(descriptor);

    closeSync(descriptor);
    descriptor = undefined;

    renameSync(temporaryPath, targetPath);
    ownsTemporaryFile = false;

    return { directorySynced: syncDirectory(directory) };
  } finally {
    try {
      if (descriptor !== undefined) {
        closeSync(descriptor);
      }
    } finally {
      if (ownsTemporaryFile) {
        unlinkSync(temporaryPath);
      }
    }
  }
}
