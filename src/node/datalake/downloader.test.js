import test from "node:test";
import assert from "node:assert/strict";
import { createServer } from "node:http";

import { createDownloader } from "./downloader.js";

async function fixture(t, handler, options = {}) {
  let requests = 0;

  const server = createServer((req, res) => {
    requests += 1;
    handler(req, res, requests);
  });

  await new Promise((resolve) => {
    server.listen(0, "127.0.0.1", resolve);
  });

  const delays = [];
  const client = createDownloader({
    sourceBase: `http://127.0.0.1:${server.address().port}`,
    wait: async (ms) => {
      delays.push(ms);
    },
    ...options,
  });

  t.after(async () => {
    await client.close();
    server.closeAllConnections();
    await new Promise((resolve) => server.close(resolve));
  });

  return {
    ...client,
    delays,
    count: () => requests,
  };
}

test("download uses the required URL, user agent and UTF-8 replacement", async (t) => {
  let path;
  let userAgent;

  const client = await fixture(t, (req, res) => {
    path = req.url;
    userAgent = req.headers["user-agent"];
    res.end(Buffer.from([0xef, 0xbb, 0xbf, 0x41, 0xff, 0x0a]));
  });

  assert.equal(await client.download(42), "\uFEFFA\uFFFD\n");
  assert.equal(path, "/cache/epub/42/pg42.txt");
  assert.equal(
    userAgent,
    "ULPGC-BigData-Stage1/PIE-data (+https://github.com/PIE-data/stage_1)",
  );
  assert.equal(client.count(), 1);
  assert.deepEqual(client.delays, []);
});

test("404 is not retried", async (t) => {
  const client = await fixture(t, (_, res) => {
    res.writeHead(404);
    res.end();
  });

  await assert.rejects(client.download(42), {
    reason: "NOT_FOUND",
    status: 404,
  });
  assert.equal(client.count(), 1);
  assert.deepEqual(client.delays, []);
});

test("503 retries and can recover", async (t) => {
  const client = await fixture(t, (_, res, count) => {
    res.writeHead(count < 3 ? 503 : 200);
    res.end("Recovered");
  });

  assert.equal(await client.download(42), "Recovered");
  assert.equal(client.count(), 3);
  assert.deepEqual(client.delays, [1000, 2000]);
});

test("persistent 503 stops after three retries", async (t) => {
  const client = await fixture(t, (_, res) => {
    res.writeHead(503);
    res.end();
  });

  await assert.rejects(client.download(42), {
    reason: "DOWNLOAD_ERROR",
    status: 503,
  });
  assert.equal(client.count(), 4);
  assert.deepEqual(client.delays, [1000, 2000, 4000]);
});

test("other client errors are not retried", async (t) => {
  const client = await fixture(t, (_, res) => {
    res.writeHead(403);
    res.end();
  });

  await assert.rejects(client.download(42), {
    reason: "DOWNLOAD_ERROR",
    status: 403,
  });
  assert.equal(client.count(), 1);
});

test("network failures are retried", async (t) => {
  const client = await fixture(t, (req) => {
    req.socket.destroy();
  });

  await assert.rejects(client.download(42), {
    reason: "DOWNLOAD_ERROR",
  });
  assert.equal(client.count(), 4);
  assert.deepEqual(client.delays, [1000, 2000, 4000]);
});

test("stalled response headers time out and are retried", async (t) => {
  const client = await fixture(t, () => {}, {
    readTimeoutMs: 100,
  });

  await assert.rejects(client.download(42), {
    reason: "DOWNLOAD_ERROR",
  });
  assert.equal(client.count(), 4);
  assert.deepEqual(client.delays, [1000, 2000, 4000]);
});

test("stalled response bodies time out and are retried", async (t) => {
  const client = await fixture(t, (_, res) => {
    res.writeHead(200);
    res.write("Incomplete");
  }, {
    readTimeoutMs: 100,
  });

  await assert.rejects(client.download(42), {
    reason: "DOWNLOAD_ERROR",
  });
  assert.equal(client.count(), 4);
  assert.deepEqual(client.delays, [1000, 2000, 4000]);
});