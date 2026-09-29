import test from "node:test";
import assert from "node:assert/strict";
import {
  existsSync,
  mkdtempSync,
  readFileSync,
  rmSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import {
  canonicalJSON,
  JsonIndex,
} from "./json_index.js";

function workspace(t) {
  const root = mkdtempSync(join(tmpdir(), "node-json-index-"));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  return root;
}

test("postings preserve raw positions and sort books numerically", (t) => {
  const root = workspace(t);
  const index = new JsonIndex(root, { positions: true });

  index.writeBook(20, [
    { term: "river", position: 1 },
    { term: "blue", position: 3 },
    { term: "river", position: 8 },
  ]);

  index.writeBook(3, [{ term: "river", position: 0 }]);

  assert.deepEqual(index.postings("river"), [
    [3, 1, [0]],
    [20, 2, [1, 8]],
  ]);
  assert.deepEqual(index.postings("absent"), []);

  const reopened = new JsonIndex(root, { positions: true });
  assert.deepEqual(reopened.postings("blue"), [[20, 1, [3]]]);
});

test("re-indexing replaces postings and removes obsolete terms", (t) => {
  const index = new JsonIndex(workspace(t), { positions: true });

  index.writeBook(1, [
    { term: "old", position: 0 },
    { term: "shared", position: 2 },
  ]);
  index.writeBook(2, [{ term: "shared", position: 0 }]);
  index.writeBook(1, [{ term: "new", position: 4 }]);

  assert.deepEqual(index.postings("old"), []);
  assert.deepEqual(index.postings("shared"), [[2, 1, [0]]]);
  assert.deepEqual(index.postings("new"), [[1, 1, [4]]]);

  index.writeBook(1, []);
  assert.deepEqual(index.postings("new"), []);
  assert.deepEqual(index.postings("shared"), [[2, 1, [0]]]);
});

test("repeating an update preserves index bytes without duplicate postings", (t) => {
  const index = new JsonIndex(workspace(t), { positions: true });
  const tokens = [
    { term: "river", position: 0 },
    { term: "river", position: 5 },
  ];

  index.writeBook(42, tokens);
  const before = readFileSync(index.path);
  index.writeBook(42, tokens);

  assert.deepEqual(readFileSync(index.path), before);
  assert.equal(existsSync(`${index.path}.part`), false);
});

test("canonical export has exact compact bytes and no trailing newline", (t) => {
  const root = workspace(t);
  const index = new JsonIndex(root, { positions: true });

  index.writeBook(9, [
    { term: "beta", position: 0 },
    { term: "alpha", position: 2 },
    { term: "beta", position: 5 },
  ]);
  index.writeBook(2, [{ term: "beta", position: 1 }]);

  const output = join(root, "canonical.json");
  index.exportCanonical(output);

  assert.equal(
    readFileSync(output, "utf8"),
    '{"alpha":{"df":1,"postings":[[9,1,[2]]]},' +
      '"beta":{"df":2,"postings":[[2,1,[1]],[9,2,[0,5]]]}}',
  );
});

test("canonical term ordering uses UTF-8 bytes rather than UTF-16", () => {
  const bmp = "\uFF41";
  const astral = "\u{10428}";

  assert.equal(
    canonicalJSON(new Map([
      [astral, [[2, 1, [0]]]],
      [bmp, [[1, 1, [0]]]],
    ])),
    `{"${bmp}":{"df":1,"postings":[[1,1,[0]]]},` +
      `"${astral}":{"df":1,"postings":[[2,1,[0]]]}}`,
  );
});

test("positions-off postings omit the third element", (t) => {
  const root = workspace(t);
  const index = new JsonIndex(root);

  index.writeBook(7, [
    { term: "river", position: 1 },
    { term: "river", position: 9 },
  ]);

  const output = join(root, "canonical.json");
  index.exportCanonical(output);

  assert.equal(
    readFileSync(output, "utf8"),
    '{"river":{"df":1,"postings":[[7,2]]}}',
  );
});

test("empty export produces an empty object without creating an index", (t) => {
  const root = workspace(t);
  const index = new JsonIndex(root);
  const output = join(root, "canonical.json");

  index.exportCanonical(output);

  assert.equal(readFileSync(output, "utf8"), "{}");
  assert.equal(existsSync(index.path), false);
});

test("invalid input and incompatible positions leave existing bytes unchanged", (t) => {
  const root = workspace(t);
  const index = new JsonIndex(root, { positions: true });
  index.writeBook(1, [{ term: "valid", position: 0 }]);
  const before = readFileSync(index.path);

  assert.throws(() => index.writeBook(0, []), /bookId/);
  assert.throws(() => index.writeBook(2, [
    { term: "first", position: 3 },
    { term: "second", position: 3 },
  ]), /positions/);

  const incompatible = new JsonIndex(root, { positions: false });
  assert.throws(() => incompatible.writeBook(2, []), /positions/);
  assert.deepEqual(readFileSync(index.path), before);
});