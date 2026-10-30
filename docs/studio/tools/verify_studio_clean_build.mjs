/** Native clean locked install and full isolated build; no reused node_modules or virtualization. */
import { spawn, execFileSync } from "node:child_process";
import { createRequire } from "node:module";
import { mkdir, readFile, readdir, writeFile, copyFile, lstat } from "node:fs/promises";
import { createHash } from "node:crypto";
import { fileURLToPath } from "node:url";
import path from "node:path";

const root = fileURLToPath(new URL("../../", import.meta.url));
const source = path.join(root, "infinite-canvas"), canonicalWeb = path.join(source, "web");
const label = new Date().toISOString().replaceAll(/[:.]/g, "-");
const privateRoot = path.join(root, ".local/studio-isolated-builds", label);
let cleanReceipt, cleanReceiptFile;
if (process.argv[2]) {
    if (process.argv[2] !== "--reuse-clean" || !process.argv[3]) throw new Error("Use --reuse-clean with an owned passing clean-install report");
    cleanReceiptFile = path.resolve(root, process.argv[3]);
    if (!cleanReceiptFile.startsWith(path.join(root, "docs/research/studio-implementation") + path.sep)) throw new Error("Not an owned clean receipt");
    cleanReceipt = JSON.parse(await readFile(cleanReceiptFile, "utf8"));
    if (!cleanReceipt.passed || cleanReceipt.install_scripts_disabled || cleanReceipt.force_or_peer_bypass || !cleanReceipt.stages.some((stage) => stage.name === "clean-install" && stage.exit_code === 0)) throw new Error("Receipt does not prove a full clean install");
}
const checkout = cleanReceipt ? path.resolve(root, cleanReceipt.private_logs, "repository") : path.join(privateRoot, "repository"), web = path.join(checkout, "web");
if (!checkout.startsWith(path.join(root, ".local/studio-isolated-builds") + path.sep)) throw new Error("Not an owned isolated checkout");
const evidence = path.join(root, "docs/research/studio-implementation");
await mkdir(privateRoot, { recursive: true }); await mkdir(evidence, { recursive: true });
const digest = (data) => createHash("sha256").update(data).digest("hex");
const report = { passed: false, scope: "Native Windows empty-directory npm ci followed by units, typecheck and managed production build of the copied current source; not another OS, public deployment or GPU acceptance", stages: [], virtualization_used: false, force_or_peer_bypass: false, install_scripts_disabled: false };
report.build_environment = { base: "/studio/", studio_mode: "managed", aggregator_origin: process.env.VITE_AGGREGATOR_ORIGIN || "" };
async function snapshot(base) {
    const files = ["package.json", "package-lock.json", "tsconfig.json", "vite.config.ts", "index.html", "maintenance.html", "../VERSION", "../CHANGELOG.md"];
    async function visit(folder) { for (const entry of await readdir(path.join(base, folder), { withFileTypes: true })) { if (entry.isDirectory()) await visit(`${folder}/${entry.name}`); else if (entry.isFile()) files.push(`${folder}/${entry.name}`); } }
    for (const directory of ["src", "tests", "public"]) await visit(directory);
    const hash = createHash("sha256"); for (const file of files.sort()) { hash.update(file); hash.update("\0"); hash.update(await readFile(path.join(base, file))); }
    return hash.digest("hex");
}
async function run(name, args) {
    const started = Date.now(), environment = { ...process.env, VITE_BASE: "/studio/", VITE_STUDIO_MODE: "managed", npm_config_cache: path.join(root, ".local/npm-cache") };
    const pathKey = Object.keys(environment).find((key) => key.toLowerCase() === "path") || "PATH";
    environment[pathKey] = path.dirname(process.execPath) + path.delimiter + (environment[pathKey] || "");
    const child = spawn(process.execPath, args, { cwd: web, env: environment, windowsHide: true, stdio: ["ignore", "pipe", "pipe"] });
    let output = ""; child.stdout.on("data", (data) => { output += data; }); child.stderr.on("data", (data) => { output += data; });
    const code = await new Promise((resolve, reject) => { child.on("error", reject); child.on("close", resolve); });
    await writeFile(path.join(privateRoot, name + ".log"), output);
    report.stages.push({ name, exit_code: code, seconds: (Date.now() - started) / 1000, log_sha256: digest(output) });
    if (code !== 0) throw new Error(`${name} failed; inspect the retained private log`);
    return output;
}
try {
    report.source_before = await snapshot(canonicalWeb);
    if (cleanReceipt) {
        if (!(await lstat(checkout)).isDirectory() || (await lstat(checkout)).isSymbolicLink() || digest(await readFile(path.join(web, "package-lock.json"))) !== cleanReceipt.lock_sha256 || digest(await readFile(path.join(canonicalWeb, "package-lock.json"))) !== cleanReceipt.lock_sha256 || digest(await readFile(path.join(canonicalWeb, "package.json"))) !== cleanReceipt.package_sha256) throw new Error("Previously clean dependency lock/package changed");
        report.clean_install_reused = { file: path.relative(root, cleanReceiptFile).replaceAll("\\", "/"), sha256: digest(await readFile(cleanReceiptFile)) };
        report.scope = "Final source units/type/build in an isolated tree using the exact unchanged dependency lock from the bound native empty-directory npm ci receipt; install not rerun, no canonical node_modules reuse or virtualization";
    }
    // Tailwind automatic source detection respects the repository ignore file.
    // Omitting it makes a reused isolated dist become an unintended CSS input.
    const files = execFileSync("git", ["ls-files", "-z", "--cached", "--others", "--exclude-standard", "--", "web", "VERSION", "CHANGELOG.md", "LICENSE", ".gitignore"], { cwd: source, encoding: "utf8" }).split("\0").filter(Boolean);
    for (const file of new Set(files)) {
        const from = path.resolve(source, file), target = path.resolve(checkout, file);
        if (!from.startsWith(source + path.sep) || !target.startsWith(checkout + path.sep) || file.split(/[\\/]/).some((part) => ["node_modules", "dist", ".local", ".git"].includes(part))) throw new Error("Unexpected source path");
        const info = await lstat(from); if (!info.isFile() || info.isSymbolicLink()) throw new Error("Only owned regular source files may be copied");
        await mkdir(path.dirname(target), { recursive: true }); await copyFile(from, target);
    }
    report.copied_source_files = new Set(files).size;
    if (await snapshot(web) !== report.source_before) throw new Error("Copied source differs from the original snapshot");
    const npm = "D:/Program Files/nodejs/node_modules/npm/bin/npm-cli.js";
    report.node = process.versions.node;
    report.npm = execFileSync(process.execPath, [npm, "--version"], { encoding: "utf8", windowsHide: true }).trim();
    report.package_sha256 = digest(await readFile(path.join(web, "package.json")));
    report.lock_sha256 = digest(await readFile(path.join(web, "package-lock.json")));
    if (!cleanReceipt) await run("clean-install", [npm, "ci", "--no-audit", "--no-fund", "--loglevel=error"]);
    const require = createRequire(path.join(web, "package.json"));
    const bundle = path.join(privateRoot, "unit.mjs");
    await require("esbuild").build({ entryPoints: [path.join(web, "tests/aggregator.test.ts")], bundle: true, platform: "node", format: "esm", banner: { js: 'import { createRequire as createNodeRequire } from "node:module"; const require = createNodeRequire(import.meta.url);' }, outfile: bundle, logLevel: "warning" });
    const units = await run("unit", ["--test", bundle]);
    report.unit_tests = Number(units.match(/^# tests (\d+)$/m)?.[1]);
    report.unit_failures = Number(units.match(/^# fail (\d+)$/m)?.[1]);
    if (!report.unit_tests || report.unit_failures !== 0) throw new Error("Unit summary is incomplete");
    await run("typecheck", [require.resolve("typescript/bin/tsc"), "--noEmit", "--tsBuildInfoFile", path.join(privateRoot, "isolated.tsbuildinfo")]);
    await run("build", [path.join(path.dirname(require.resolve("vite/package.json")), "bin/vite.js"), "build"]);
    const dist = path.join(web, "dist"), output = {};
    async function outputFiles(folder) { for (const entry of await readdir(folder, { withFileTypes: true })) { const file = path.join(folder, entry.name); if (entry.isDirectory()) await outputFiles(file); else output[path.relative(dist, file).replaceAll("\\", "/")] = digest(await readFile(file)); } }
    await outputFiles(dist); report.static_artifact_sha256 = output;
    report.source_after = await snapshot(canonicalWeb);
    report.copied_source_after = await snapshot(web);
    report.canonical_lock_unchanged = digest(await readFile(path.join(canonicalWeb, "package-lock.json"))) === report.lock_sha256;
    report.passed = report.source_before === report.source_after && report.source_before === report.copied_source_after && report.canonical_lock_unchanged;
} catch (error) { report.failure = error.message; }
report.private_logs = path.relative(root, privateRoot).replaceAll("\\", "/");
const target = path.join(evidence, `clean-build-${label}.json`);
await writeFile(target, JSON.stringify(report, null, 2) + "\n", { flag: "wx" });
console.log(JSON.stringify({ passed: report.passed, report: path.relative(root, target), stages: report.stages, unit_tests: report.unit_tests, failure: report.failure }));
if (!report.passed) process.exitCode = 1;
