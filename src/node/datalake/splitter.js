const START_MARKER = "*** START OF THE PROJECT GUTENBERG EBOOK";
const END_MARKER = "*** END OF THE PROJECT GUTENBERG EBOOK";

export class MarkersNotFound extends Error {
  constructor() {
    super("Missing or incorrectly ordered Gutenberg markers");
    this.name = "MarkersNotFound";
  }
}

function normalizeNewlines(text) {
  return text.replaceAll("\r\n", "\n").replaceAll("\r", "\n");
}

function stripBom(text) {
  return text.startsWith("\uFEFF") ? text.slice(1) : text;
}

function stripTrailingSpacesAndTabs(line) {
  let end = line.length;

  while (end > 0 && (line[end - 1] === " " || line[end - 1] === "\t")) {
    end -= 1;
  }

  return line.slice(0, end);
}

export function cleanHeader(text) {
  // Header cleaning: BOM, line endings, outer whitespace, final LF.
  return `${normalizeNewlines(stripBom(text)).trim()}\n`;
}

export function cleanBody(text) {
  const lines = normalizeNewlines(stripBom(text))
    .split("\n")
    .map(stripTrailingSpacesAndTabs);

  // Keep at most two consecutive LF characters.
  let cleaned = "";
  let newlineCount = 0;

  for (const ch of lines.join("\n")) {
    newlineCount = ch === "\n" ? newlineCount + 1 : 0;

    if (newlineCount <= 2) {
      cleaned += ch;
    }
  }

  return `${cleaned.trim()}\n`;
}

export function splitText(rawText) {
  // Normalize line boundaries before locating entire marker lines.
  const lines = normalizeNewlines(rawText).split("\n");
  const start = lines.findIndex((line) => line.includes(START_MARKER));
  const end = lines.findLastIndex((line) => line.includes(END_MARKER));

  if (start < 0 || end <= start) {
    throw new MarkersNotFound();
  }

  return {
    header: cleanHeader(lines.slice(0, start).join("\n")),
    body: cleanBody(lines.slice(start + 1, end).join("\n")),
  };
}