import { mkdir, open } from "node:fs/promises";
import { resolve } from "node:path";
import { tryLock, Lock } from "@lickle/lock";

export class WorkspaceLockedError extends Error {
  constructor(path) {
    super(`Workspace is locked: ${path}`);
    this.name = "WorkspaceLockedError";
    this.exitCode = 4;
  }
}

export async function acquireRunLock(workspace) {
  const directory = resolve(workspace, "control");
  const path = resolve(directory, "run.lock");

  await mkdir(directory, { recursive: true });

  // Keep the same file across runs; never truncate or unlink it.
  const handle = await open(path, "a+");

  let guard;
  try {
    guard = await tryLock(handle, Lock.Exclusive);

    if (!guard) {
      throw new WorkspaceLockedError(path);
    }
  } catch (error) {
    await handle.close();
    throw error;
  }

  let releasePromise;

  return {
    path,

    release() {
      // Repeated calls share the same cleanup operation.
      releasePromise ??= (async () => {
        try {
          await guard.drop();
        } finally {
          await handle.close();
        }
      })();

      return releasePromise;
    },
  };
}

export async function withRunLock(workspace, operation) {
  const lock = await acquireRunLock(workspace);

  try {
    return await operation();
  } finally {
    await lock.release();
  }
}