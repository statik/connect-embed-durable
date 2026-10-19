import { readFile } from "node:fs/promises";
import { join } from "node:path";
import { parseArgs } from "node:util";
import { BACKGROUND_CONTEXT } from "@earendil-works/chord/context";
import { builtinModels } from "@earendil-works/pi-ai/providers/all";
import { ConfigError, parseConfig } from "./config.ts";
import {
	askBriefing,
	type BriefingOptions,
	ModelSetupError,
	type ModelChoice,
	NoBriefingError,
	runBriefing,
} from "./run.ts";

declare const BRIEFING_VERSION: string | undefined;
const VERSION = typeof BRIEFING_VERSION === "string" ? BRIEFING_VERSION : "dev";
const DEFAULT_MODEL = "openrouter/anthropic/claude-sonnet-5.5";

const USAGE = `Usage: pi-briefing run --config FILE --state DIR [--date YYYY-MM-DD] [--model PROVIDER/MODEL]
       pi-briefing ask --state DIR --key KEY --question TEXT --request-id ID
       pi-briefing price [--model PROVIDER/MODEL]
       pi-briefing --version

run writes a daily briefing; ask asks a follow-up question in the conversation of the briefing
with that key (from its result). Both print JSON lines on stdout:
  {"type":"trace","event":{...}}      while they work: turns, text, tool calls, usage, notes
  {"type":"result", ...}              once, at the end
  {"type":"error","message":...}      on failure (exit code 1; 2 when something must change first)

price prints the model's catalog price in USD per million tokens.

Environment:
  OPENROUTER_API_KEY  the key for the default provider; other providers read their own,
                      such as ANTHROPIC_API_KEY for anthropic/...
  BRIEFING_MODEL      default for --model (${DEFAULT_MODEL})
  GITHUB_TOKEN        optional; reads private repositories and raises GitHub's rate limit`;

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
		if (error instanceof SyntaxError) {
			throw new UsageError(`${path} is not valid JSON: ${error.message}`);
		}
		throw error;
	}
}

type Values = Record<string, string | boolean | undefined>;

function required(values: Values, ...names: string[]): Record<string, string> {
	const found: Record<string, string> = {};
	for (const name of names) {
		const value = values[name];
		if (typeof value !== "string" || value === "") {
			throw new UsageError(`This command needs --${name}`);
		}
		found[name] = value;
	}
	return found;
}

function shared(values: Values): BriefingOptions {
	const { state } = required(values, "state");
	const token = process.env["GITHUB_TOKEN"];
	return {
		storagePath: join(state ?? "", "briefings.sqlite"),
		models: builtinModels(),
		github: token ? { fetch, token } : { fetch },
		context: BACKGROUND_CONTEXT,
		onTrace: (event) => emit({ type: "trace", event }),
	};
}

function today(): string {
	return new Date().toISOString().slice(0, 10);
}

function modelOption(values: Values): ModelChoice {
	const value = values["model"];
	return parseModel(
		typeof value === "string" ? value : (process.env["BRIEFING_MODEL"] ?? DEFAULT_MODEL),
	);
}

function price(values: Values): void {
	const choice = modelOption(values);
	const model = builtinModels().getModel(choice.provider, choice.modelId);
	if (model === undefined) {
		throw new ModelSetupError(`No model ${choice.provider}/${choice.modelId} in the catalog`);
	}
	emit({ type: "price", model: `${choice.provider}/${choice.modelId}`, perMillionUsd: model.cost });
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
			question: { type: "string" },
			"request-id": { type: "string" },
			key: { type: "string" },
			version: { type: "boolean" },
			help: { type: "boolean" },
		},
	});
	if (values.version) return void process.stdout.write(`${VERSION}\n`);
	if (values.help) return void process.stdout.write(`${USAGE}\n`);
	const command = positionals[0];
	if (command === "price") return price(values);
	if (command === "run") {
		const { config } = required(values, "config");
		const date = typeof values["date"] === "string" ? values["date"] : today();
		const briefing = { config: await readConfig(config ?? ""), date, model: modelOption(values) };
		const result = await runBriefing({ ...shared(values), ...briefing });
		return emit({ type: "result", version: VERSION, ...result });
	}
	if (command === "ask") {
		const asked = required(values, "key", "question", "request-id");
		const options = {
			...shared(values),
			key: asked["key"] ?? "",
			question: asked["question"] ?? "",
			requestId: asked["request-id"] ?? "",
		};
		return emit({ type: "result", version: VERSION, ...(await askBriefing(options)) });
	}
	throw new UsageError(`Unknown command ${JSON.stringify(command ?? "")}.\n${USAGE}`);
}

const SETUP_ERRORS = [UsageError, ConfigError, ModelSetupError, NoBriefingError];

try {
	await run(process.argv.slice(2));
} catch (error) {
	emit({ type: "error", message: error instanceof Error ? error.message : String(error) });
	process.exitCode = SETUP_ERRORS.some((kind) => error instanceof kind) ? 2 : 1;
}
