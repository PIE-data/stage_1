import test from "node:test";
import assert from "node:assert/strict";
import {
  mkdtempSync,
  readFileSync,
  rmSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { FolderIndex, termLocation } from "./folder_index.js";

function workspace(t) {
  const root = mkdtempSync(join(tmpdir(), "node-folder-index-"));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  return root;
}

test("term paths encode UTF-8 bytes and select the required bucket", () => {
  assert.deepEqual(termLocation("apple"), ["A", "apple.txt"]);
  assert.deepEqual(termLocation("Apple"), ["A", "%41pple.txt"]);
  assert.deepEqual(termLocation("can't"), ["C", "can%27t.txt"]);
  assert.deepEqual(termLocation("café"), ["C", "caf%C3%A9.txt"]);
  assert.deepEqual(termLocation("中文"), ["_", "%E4%B8%AD%E6%96%87.txt"]);
  assert.deepEqual(termLocation("12a"), ["_", "12a.txt"]);
  assert.deepEqual(termLocation("../"), ["_", "%2E%2E%2F.txt"]);
});

test("posting files preserve positions, LF endings and numeric book order", (t) => {
  const index = new FolderIndex(workspace(t), { positions: true });

  index.writeBook(20, [
    { term: "river", position: 1 },
    { term: "river", position: 8 },
  ]);
  index.writeBook(3, [{ term: "river", position: 0 }]);

  assert.equal(
    readFileSync(index.pathFor("river"), "utf8"),
    "3\t1\t0\n20\t2\t1,8\n",
  );
  assert.deepEqual(index.postings("river"), [
    [3, 1, [0]],
    [20, 2, [1, 8]],
  ]);
  assert.deepEqual(index.postings("absent"), []);
});

test("repeating a book does not duplicate its postings", (t) => {
  const index = new FolderIndex(workspace(t), { positions: true });
  const tokens = [{ term: "river", position: 4 }];

  index.writeBook(42, tokens);
  const before = readFileSync(index.pathFor("river"));
  index.writeBook(42, tokens);

  assert.deepEqual(readFileSync(index.pathFor("river")), before);
});

test("updating a term does not read or rewrite unrelated posting files", (t) => {
  const index = new FolderIndex(workspace(t), { positions: true });

  index.writeBook(1, [{ term: "unrelated", position: 0 }]);

  // An unrelated malformed file must not be read by this update.
  writeFileSync(index.pathFor("unrelated"), "untouched\n");
  index.writeBook(2, [{ term: "river", position: 0 }]);

  assert.equal(
    readFileSync(index.pathFor("unrelated"), "utf8"),
    "untouched\n",
  );
  assert.deepEqual(index.postings("river"), [[2, 1, [0]]]);
});

test("positions-off files omit the third column", (t) => {
  const root = workspace(t);
  const index = new FolderIndex(root);

  index.writeBook(7, [
    { term: "river", position: 1 },
    { term: "river", position: 9 },
  ]);

  assert.equal(readFileSync(index.pathFor("river"), "utf8"), "7\t2\n");

  const output = join(root, "canonical.json");
  index.exportCanonical(output);

  assert.equal(
    readFileSync(output, "utf8"),
    '{"river":{"df":1,"postings":[[7,2]]}}',
  );
});

test("export decodes filenames and ignores partial files", (t) => {
  const root = workspace(t);
  const index = new FolderIndex(root, { positions: true });

  index.writeBook(5, [{ term: "café", position: 2 }]);
  writeFileSync(`${index.pathFor("café")}.part`, "unfinished");

  const output = join(root, "canonical.json");
  index.exportCanonical(output);

  assert.equal(
    readFileSync(output, "utf8"),
    '{"café":{"df":1,"postings":[[5,1,[2]]]}}',
  );
});

test("an absent index exports an empty object", (t) => {
  const root = workspace(t);
  const index = new FolderIndex(root);
  const output = join(root, "canonical.json");

  index.exportCanonical(output);
  assert.equal(readFileSync(output, "utf8"), "{}");
});