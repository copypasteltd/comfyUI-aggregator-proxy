import { spawn } from "node:child_process";
import { createRequire } from "node:module";
import { mkdir, writeFile, readdir, readFile } from "node:fs/promises";
import { createHash } from "node:crypto";
import { fileURLToPath } from "node:url";
import path from "node:path";

const root = fileURLToPath(new URL("../../", import.meta.url));
const web = path.join(root, "infinite-canvas/web");
const require = createRequire(path.join(web, "package.json"));
const mode = process.argv[2] || "unit";
if (!["unit", "typecheck", "build"].includes(mode)) throw new Error("Choose unit, typecheck or build");
const label = new Date().toISOString().replaceAll(/[:.]/g, "-");
const temporary = path.join(root, ".local/studio-checks");
const evidence = path.join(root, "docs/research/studio-implementation");
await Promise.all([mkdir(temporary, { recursive: true }), mkdir(evidence, { recursive: true })]);
async function sourceSnapshot() {
    const files = ["package.json", "package-lock.json", "tsconfig.json", "vite.config.ts", "index.html", "../VERSION", "../CHANGELOG.md"];
    for (const directory of ["src", "tests", "public"]) {
        async function visit(folder) {
            for (const entry of await readdir(path.join(web, folder), { withFileTypes: true })) {
                if (entry.isDirectory()) await visit(`${folder}/${entry.name}`);
                else if (entry.isFile()) files.push(`${folder}/${entry.name}`);
            }
        }
        await visit(directory);
    }
    const hash = createHash("sha256");
    for (const file of files.sort()) { hash.update(file); hash.update("\0"); hash.update(await readFile(path.join(web, file))); }
    return hash.digest("hex");
}
const sourceBefore = await sourceSnapshot();
let args;
if (mode === "unit") {
    const esbuild = require("esbuild");
    const bundle = path.join(temporary, `unit-${label}.mjs`);
    await esbuild.build({ entryPoints: [path.join(web, "tests/aggregator.test.ts")], bundle: true, platform: "node", format: "esm", banner: { js: 'import { createRequire as createNodeRequire } from "node:module"; const require = createNodeRequire(import.meta.url);' }, outfile: bundle, logLevel: "warning" });
    args = ["--test", bundle];
} else if (mode === "typecheck") {
    args = [require.resolve("typescript/bin/tsc"), "--noEmit", "--tsBuildInfoFile", path.join(temporary, "studio.tsbuildinfo")];
} else {
    args = [path.join(path.dirname(require.resolve("vite/package.json")), "bin/vite.js"), "build"];
}
const started = Date.now();
const child = spawn(process.execPath, args, { cwd: web, env: { ...process.env, VITE_BASE: process.env.VITE_BASE || "/studio/", VITE_STUDIO_MODE: "managed" }, windowsHide: true, stdio: ["ignore", "pipe", "pipe"] });
let output = "";
child.stdout.on("data", (data) => { output += data; });
child.stderr.on("data", (data) => { output += data; });
const exitCode = await new Promise((resolve, reject) => { child.on("error", reject); child.on("close", resolve); });
const sourceAfter = await sourceSnapshot();
const log = `${mode}-${label}.log`;
await writeFile(path.join(evidence, log), output);
const result = { scope: "active fusion branch; local native verification, not live media acceptance", mode, passed: exitCode === 0 && sourceBefore === sourceAfter, exit_code: exitCode, source_before: sourceBefore, source_after: sourceAfter, source_unchanged: sourceBefore === sourceAfter, seconds: (Date.now() - started) / 1000, log, virtualization_used: false };
await writeFile(path.join(evidence, `${mode}-${label}.json`), JSON.stringify(result, null, 2) + "\n");
console.log(JSON.stringify(result));
if (!result.passed) { console.log(output.slice(-12000)); process.exitCode = 1; }
