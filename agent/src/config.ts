import { createHash } from "node:crypto";

/** One repository the briefing covers, with an optional note on what to watch for. */
export type RepoConfig = {
	readonly name: string;
	readonly focus?: string;
};

/** What the briefing covers. The Shiny app writes this; the agent only reads it. */
export interface BriefingConfig {
	readonly repos: readonly RepoConfig[];
	readonly lookbackHours: number;
	readonly instructions?: string;
}

export const MAX_REPOS = 20;
export const MAX_LOOKBACK_HOURS = 168;
const REPO_NAME = /^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})\/[A-Za-z0-9._-]{1,100}$/;

/** A config the user can fix: the message says what is wrong and where. */
export class ConfigError extends Error {
	override readonly name = "ConfigError";
}

function isRecord(value: unknown): value is Record<string, unknown> {
	return typeof value === "object" && value !== null && !Array.isArray(value);
}

function optionalText(value: unknown, field: string): string | undefined {
	if (value === undefined || value === null || value === "") return undefined;
	if (typeof value !== "string") throw new ConfigError(`${field} must be text`);
	return value.trim() === "" ? undefined : value.trim();
}

function parseRepo(value: unknown, index: number): RepoConfig {
	const where = `repos[${index}]`;
	if (!isRecord(value)) throw new ConfigError(`${where} must be an object with a "name"`);
	const name = value["name"];
	if (typeof name !== "string" || !REPO_NAME.test(name)) {
		throw new ConfigError(`${where}.name must look like "owner/repo", got ${JSON.stringify(name)}`);
	}
	const focus = optionalText(value["focus"], `${where}.focus`);
	return focus === undefined ? { name } : { name, focus };
}

function parseRepos(value: unknown): RepoConfig[] {
	if (!Array.isArray(value) || value.length === 0) {
		throw new ConfigError("repos must list at least one repository");
	}
	if (value.length > MAX_REPOS) {
		throw new ConfigError(`repos lists ${value.length} repositories; the limit is ${MAX_REPOS}`);
	}
	const repos = value.map(parseRepo);
	const seen = new Set<string>();
	for (const repo of repos) {
		const key = repo.name.toLowerCase();
		if (seen.has(key)) throw new ConfigError(`repos lists ${repo.name} twice`);
		seen.add(key);
	}
	return repos;
}

/** Validate untrusted JSON into a config, failing on the first problem. */
export function parseConfig(value: unknown): BriefingConfig {
	if (!isRecord(value)) throw new ConfigError("The config must be a JSON object");
	const lookback = value["lookbackHours"] ?? 24;
	if (typeof lookback !== "number" || !Number.isInteger(lookback)) {
		throw new ConfigError("lookbackHours must be a whole number of hours");
	}
	if (lookback < 1 || lookback > MAX_LOOKBACK_HOURS) {
		throw new ConfigError(`lookbackHours must be between 1 and ${MAX_LOOKBACK_HOURS}`);
	}
	const instructions = optionalText(value["instructions"], "instructions");
	const config = { repos: parseRepos(value["repos"]), lookbackHours: lookback };
	return instructions === undefined ? config : { ...config, instructions };
}

/**
 * The durable identity of one briefing: the same date and config always map to the same
 * conversation and submission, so rerunning after a crash resumes instead of starting over.
 */
export function briefingKey(date: string, config: BriefingConfig): string {
	const canonical = JSON.stringify({
		repos: config.repos.map((r) => ({ name: r.name.toLowerCase(), focus: r.focus ?? "" })),
		lookbackHours: config.lookbackHours,
		instructions: config.instructions ?? "",
	});
	const digest = createHash("sha256").update(canonical).digest("hex").slice(0, 12);
	return `briefing:${date}:${digest}`;
}
