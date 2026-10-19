import { readFile } from "node:fs/promises";
import { join } from "node:path";
import { parseArgs } from "node:util";
import { BACKGROUND_CONTEXT } from "@earendil-works/chord/context";
import { builtinModels } from "@earendil-works/pi-ai/providers/all";
import { ConfigError, parseConfig } from "./config.ts";
import { type ModelChoice, runBriefing } from "./run.ts";

declare const BRIEFING_VERSION: string | undefined;
const VERSION = typeof BRIEFING_VERSION === "string" ? BRIEFING_VERSION : "dev";
const DEFAULT_MODEL = "anthropic/claude-sonnet-5-5";

const USAGE = `Usage: pi-briefing run --config FILE --state DIR [--date YYYY-MM-DD] [--model PROVIDER/MODEL]
       pi-briefing --version

Writes a daily briefing about GitHub activity as JSON lines on stdout:
  {"type":"progress","message":...}   while it works
  {"type":"result", ...}              once, at the end
  {"type":"error","message":...}      on failure (exit code 1; 2 for bad arguments or config)

Environment:
  ANTHROPIC_API_KEY (or another provider's key)  the model provider's credentials
  GITHUB_TOKEN                                    optional; raises GitHub's rate limit
  BRIEFING_MODEL                                  default for --model (${DEFAULT_MODEL})`;

/** Wrong arguments or config: the caller must change something before retrying. */
class UsageError extends Error {
	override readonly name = "UsageError";
}

function emit(value: object): void {
	process.stdout.write(`${JSON.stringify(value)}\n`);
}

function parseModel(value: string): ModelChoice {
	const slash = value.indexOf("/");
	if (slash <= 0 || slash === value.length - 1) {
		throw new UsageError(`--model must look like "provider/model", got ${JSON.stringify(value)}`);
	}
	return { provider: value.slice(0, slash), modelId: value.slice(slash + 1) };
}

async function readConfig(path: string) {
	let text: string;
	try {
		text = await readFile(path, "utf8");
	} catch (error) {
		throw new UsageError(`Cannot read --config ${path}: ${(error as Error).message}`);
	}
	try {
		return parseConfig(JSON.parse(text));
	} catch (error) {
		if (error instanceof SyntaxError)
			throw new UsageError(`${path} is not valid JSON: ${error.message}`);
		throw error;
	}
}

async function run(argv: string[]): Promise<void> {
	const { values, positionals } = parseArgs({
		args: argv,
		allowPositionals: true,
		options: {
			config: { type: "string" },
			state: { type: "string" },
			date: { type: "string" },
			model: { type: "string" },
			version: { type: "boolean" },
			help: { type: "boolean" },
		},
	});
	if (values.version) return void process.stdout.write(`${VERSION}\n`);
	if (values.help) return void process.stdout.write(`${USAGE}\n`);
	if (positionals[0] !== "run") {
		throw new UsageError(`Unknown command ${JSON.stringify(positionals[0] ?? "")}.\n${USAGE}`);
	}
	if (values.config === undefined || values.state === undefined) {
		throw new UsageError("run needs --config FILE and --state DIR");
	}
	const config = await readConfig(values.config);
	const model = parseModel(values.model ?? process.env["BRIEFING_MODEL"] ?? DEFAULT_MODEL);
	const token = process.env["GITHUB_TOKEN"];
	const result = await runBriefing({
		config,
		date: values.date ?? new Date().toISOString().slice(0, 10),
		storagePath: join(values.state, "briefings.sqlite"),
		model,
		models: builtinModels(),
		github: token ? { fetch, token } : { fetch },
		context: BACKGROUND_CONTEXT,
		onProgress: (message) => emit({ type: "progress", message }),
	});
	emit({ type: "result", version: VERSION, ...result });
}

try {
	await run(process.argv.slice(2));
} catch (error) {
	const usage = error instanceof UsageError || error instanceof ConfigError;
	emit({ type: "error", message: error instanceof Error ? error.message : String(error) });
	process.exitCode = usage ? 2 : 1;
}
