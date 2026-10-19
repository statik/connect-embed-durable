/**
 * Compile the agent into one self-contained executable per platform with `bun build --compile`.
 *
 * This is the JavaScript stand-in for go-to-wheel's cross-compile step: Bun embeds its runtime and
 * the bundled agent in each binary, so the Python wheel needs no Node.js on the host.
 *
 *   bun run build.ts                  # every target, into dist/
 *   bun run build.ts linux-x64        # some targets
 */
import { fileURLToPath } from "node:url";
import { parseArgs } from "node:util";
import type { BunPlugin } from "bun";
import packageJson from "./package.json" with { type: "json" };

/** Our platform names, which `wheel/build_wheels.py` maps to wheel tags, and Bun's targets. */
const TARGETS: Record<string, string> = {
	// Baseline: no AVX2, so the binary runs on whatever CPU a cloud host has.
	"linux-x64": "bun-linux-x64-baseline",
	"linux-arm64": "bun-linux-arm64",
	"darwin-arm64": "bun-darwin-arm64",
	"darwin-x64": "bun-darwin-x64",
};

const shim = fileURLToPath(new URL("./src/bun-sqlite-shim.ts", import.meta.url));

const nodeSqliteShim: BunPlugin = {
	name: "node-sqlite-shim",
	setup(build) {
		build.onResolve({ filter: /^node:sqlite$/ }, () => ({ path: shim }));
	},
};

const { positionals } = parseArgs({ allowPositionals: true });
const wanted = positionals.length > 0 ? positionals : Object.keys(TARGETS);
for (const name of wanted) {
	const target = TARGETS[name];
	if (target === undefined) {
		throw new Error(`Unknown target ${name}; use one of ${Object.keys(TARGETS).join(", ")}`);
	}
	const result = await Bun.build({
		entrypoints: ["./src/main.ts"],
		compile: { target: target as Bun.Build.CompileTarget, outfile: `./dist/${name}/pi-briefing` },
		plugins: [nodeSqliteShim],
		define: { BRIEFING_VERSION: JSON.stringify(packageJson.version) },
		minify: true,
		sourcemap: "none",
	});
	if (!result.success) {
		for (const log of result.logs) console.error(log);
		throw new Error(`Build failed for ${name}`);
	}
	console.log(`built dist/${name}/pi-briefing`);
}
