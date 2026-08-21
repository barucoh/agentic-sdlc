#!/usr/bin/env node
/** Validate and render every committed Mermaid block with the pinned CLI. */
import { execFileSync } from "node:child_process";
import { existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const root = new URL("..", import.meta.url).pathname.replace(/^\/(.:\/)/, "$1");
const files = execFileSync("git", ["ls-files"], { cwd: root, encoding: "utf8" })
  .trim()
  .split(/\r?\n/)
  .filter(Boolean);
const blocks = [];
for (const file of files) {
  const path = join(root, file);
  if (!existsSync(path)) continue;
  const source = readFileSync(path, "utf8");
  for (const match of source.matchAll(/```mermaid\r?\n([\s\S]*?)```/g)) {
    blocks.push({ file, source: match[1] });
  }
}
if (!blocks.length) throw new Error("No committed Mermaid blocks found");

const temporary = mkdtempSync(join(tmpdir(), "pleiad-mermaid-"));
// Run the pinned package entry with this exact Node runtime.  Calling the
// platform shim requires shell spawning on Windows and emits deprecation
// warnings; the package's JavaScript entry is portable without a shell.
const cli = join(root, "node_modules", "@mermaid-js", "mermaid-cli", "src", "cli.js");
try {
  blocks.forEach(({ file, source }, index) => {
    const input = join(temporary, `${index}.mmd`);
    const output = join(temporary, `${index}.svg`);
    writeFileSync(input, source, "utf8");
    try {
      execFileSync(process.execPath, [cli, "--quiet", "--puppeteerConfigFile", join(root, "scripts", "mermaid-puppeteer.json"), "--input", input, "--output", output], {
        cwd: root,
        stdio: "pipe",
        shell: false,
      });
    } catch (error) {
      const detail = error.stderr?.toString() || error.message;
      throw new Error(`Mermaid block ${index + 1} in ${file} failed: ${detail}`);
    }
  });
} finally {
  rmSync(temporary, { recursive: true, force: true });
}
console.log(`Validated ${blocks.length} committed Mermaid block(s) with pinned Mermaid CLI.`);
