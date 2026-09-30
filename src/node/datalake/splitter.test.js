import test from "node:test";
import assert from "node:assert/strict";

import {
  cleanBody,
  cleanHeader,
  MarkersNotFound,
  splitText,
} from "./splitter.js";

const START = "*** START OF THE PROJECT GUTENBERG EBOOK";
const END = "*** END OF THE PROJECT GUTENBERG EBOOK";

test("split excludes complete marker lines and the footer", () => {
  const raw = [
    "Title: Example",
    `prefix ${START} EXAMPLE ***`,
    "Hello world.",
    `${END} EXAMPLE *** suffix`,
    "Discard this footer.",
  ].join("\n");

  assert.deepEqual(splitText(raw), {
    header: "Title: Example\n",
    body: "Hello world.\n",
  });
});

test("split uses the first START and the last END", () => {
  const raw = [
    "Title: Example",
    START,
    "First",
    START,
    "Middle",
    END,
    "Last",
    END,
    "Footer",
  ].join("\n");

  assert.equal(
    splitText(raw).body,
    `First\n${START}\nMiddle\n${END}\nLast\n`,
  );
});

test("split handles CRLF and lone CR line endings", () => {
  const raw = `Title: Example\r\n${START}\rHello\r\n${END}\rFooter`;

  assert.deepEqual(splitText(raw), {
    header: "Title: Example\n",
    body: "Hello\n",
  });
});

test("body cleaning preserves indentation and internal spaces", () => {
  const raw = "\uFEFF \r\nFirst  line \t\r\n  Indented\t\r\n\r\n\r\nLast \t\r\n";

  assert.equal(
    cleanBody(raw),
    "First  line\n  Indented\n\nLast\n",
  );
});

test("header cleaning preserves internal blank lines and trailing spaces", () => {
  assert.equal(
    cleanHeader("\uFEFFTitle: Example  \r\n\r\n\r\nAuthor: Someone\r\n"),
    "Title: Example  \n\n\nAuthor: Someone\n",
  );
});

test("empty cleaned content consists of one LF", () => {
  assert.equal(cleanBody(" \t\r\n"), "\n");
  assert.equal(cleanHeader(""), "\n");
  assert.deepEqual(splitText(`${START}\n${END}`), {
    header: "\n",
    body: "\n",
  });
});

test("missing, reversed and differently cased markers are rejected", () => {
  const invalidInputs = [
    "No markers",
    `${START}\nBody`,
    `Body\n${END}`,
    `${END}\nBody\n${START}`,
    `${START.toLowerCase()}\nBody\n${END}`,
    `${START} ${END}`,
  ];

  for (const raw of invalidInputs) {
    assert.throws(() => splitText(raw), MarkersNotFound);
  }
});