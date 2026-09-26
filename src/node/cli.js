import { readFileSync } from "node:fs";
import { parseArgs } from "node:util";

// Specification version supported by this implementation.
const SUPPORTED_SPEC_VERSION = "1.1.2";

// Resolve the path relative to this file, regardless of the terminal's working directory.
const versionFile = new URL("../../spec/SPEC_VERSION", import.meta.url);

function main() {
  let values;
  let positionals;

  try {
    ({ values, positionals } = parseArgs({
      options: {
        workspace: { type: "string" }
      },
      allowPositionals: true,
      strict: true
    }));
  } catch (error) {
    console.error(error.message);
    return 2;
  }

  // The specification requires --workspace as a global option.
  if (!values.workspace || positionals.length !== 1 || positionals[0] !== "version") {
    console.error("Usage: node src/node/cli.js --workspace <path> version");
    return 2;
  }

  try {
    const version = readFileSync(versionFile, "utf8").trim();

    // Prevent execution when the repository specification is incompatible.
    if (version !== SUPPORTED_SPEC_VERSION) {
      console.error(
        `Spec mismatch: supported ${SUPPORTED_SPEC_VERSION}, found ${version}`
      );
      return 1;
    }

    console.log(version);
    console.error(`stage-1-node 0.1.0 | Node ${process.version}`);
    return 0;
  } catch (error) {
    console.error(`Cannot read SPEC_VERSION: ${error.message}`);
    return 1;
  }
}

process.exitCode = main();