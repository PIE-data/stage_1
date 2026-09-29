import { Agent } from "undici";
import { setTimeout as sleep } from "node:timers/promises";

export class DownloadError extends Error {
  constructor(message, reason, status = null) {
    super(message);
    this.name = "DownloadError";
    this.reason = reason;
    this.status = status;
  }
}

export function createDownloader({
  sourceBase = "https://www.gutenberg.org",
  connectTimeoutMs = 30_000,
  readTimeoutMs = 60_000,
  wait = sleep,
} = {}) {
  const base = new URL(sourceBase);

  if (!["http:", "https:"].includes(base.protocol) || base.search || base.hash) {
    throw new TypeError(
      "sourceBase must be an HTTP(S) URL without query or fragment",
    );
  }

  for (const value of [connectTimeoutMs, readTimeoutMs]) {
    if (!Number.isSafeInteger(value) || value <= 0) {
      throw new TypeError("Timeouts must be positive integer milliseconds");
    }
  }

  const agent = new Agent({
    connect: { timeout: connectTimeoutMs },
  });

  // Override fetch's dispatch defaults for headers and body inactivity.
  const dispatcher = {
    dispatch(options, handler) {
      return agent.dispatch({
        ...options,
        headersTimeout: readTimeoutMs,
        bodyTimeout: readTimeoutMs,
      }, handler);
    },
  };

  async function download(bookId) {
    if (!Number.isSafeInteger(bookId) || bookId <= 0) {
      throw new TypeError("bookId must be a positive safe integer");
    }

    const prefix = base.href.replace(/\/+$/u, "");
    const url = `${prefix}/cache/epub/${bookId}/pg${bookId}.txt`;

    // One initial attempt followed by at most three retries.
    for (let attempt = 0; attempt < 4; attempt += 1) {
      try {
        const response = await fetch(url, {
          dispatcher,
          headers: {
            "User-Agent":
              "ULPGC-BigData-Stage1/PIE-data (+https://github.com/PIE-data/stage_1)",
          },
        });

        if (response.status !== 200) {
          // Release the response without downloading an error page.
          if (response.body) {
            await response.body.cancel();
          }

          throw new DownloadError(
            `HTTP ${response.status} for book ${bookId}`,
            response.status === 404 ? "NOT_FOUND" : "DOWNLOAD_ERROR",
            response.status,
          );
        }

        // Replace malformed UTF-8 while preserving a BOM for the splitter.
        return Buffer.from(await response.arrayBuffer()).toString("utf8");
      } catch (error) {
        const retryable = error instanceof DownloadError
          ? error.status >= 500 && error.status <= 599
          : error instanceof TypeError;

        if (!retryable || attempt === 3) {
          if (error instanceof DownloadError) {
            throw error;
          }

          const failure = new DownloadError(
            `Download failed for book ${bookId}: ${error.message}`,
            "DOWNLOAD_ERROR",
          );
          failure.cause = error;
          throw failure;
        }

        await wait(1000 * (2 ** attempt));
      }
    }
  }

  return {
    download,
    close: () => agent.close(),
  };
}