import {
  closeSync,
  fsyncSync,
  mkdirSync,
  openSync,
  renameSync,
  unlinkSync,
  writeFileSync,
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