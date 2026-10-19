import type { Context } from "@earendil-works/chord";
import { withoutAbortSignal } from "@earendil-works/chord/context";
import type { Models } from "@earendil-works/pi-ai/models";
import {
	type AgentEvent,
	AssistantEntry,
	type Conversation,
	type ConversationId,
	configure,
	createRegistry,
	Harness,
	UsageDoc,
	watchEvents,
} from "@earendil-works/pi-durable";
import { openNodeSqliteStorage } from "@earendil-works/pi-durable/storage/sqlite/node";
import { repairRequest, type Verification, verifyCitations } from "./citations.ts";
import { type BriefingConfig, briefingKey } from "./config.ts";
import {
	BriefingDoc,
	BriefingIndex,
	type BriefingState,
	createBriefingExtension,
} from "./extension.ts";
import type { GitHubClient } from "./github.ts";

export interface ModelChoice {
	readonly provider: string;
	readonly modelId: string;
}

export interface RunOptions {
	readonly config: BriefingConfig;
	/** The briefing's day, `YYYY-MM-DD` in UTC. */
	readonly date: string;
	readonly storagePath: string;
	readonly model: ModelChoice;
	readonly models: Models;
	readonly github: GitHubClient;
	readonly context: Context;
	readonly now?: () => Date;
	readonly onProgress?: (message: string) => void;
}

export interface BriefingResult {
	readonly key: string;
	readonly date: string;
	readonly window: { readonly since: string; readonly until: string };
	readonly model: string;
	readonly markdown: string;
	readonly verification: Verification;
	readonly activity: BriefingState["activity"];
	readonly usage: { readonly input: number; readonly output: number; readonly costUsd: number };
	/** The conversation already existed: this run picked up earlier work instead of starting fresh. */
	readonly resumed: boolean;
}

/** Repair rounds after the first answer; each is a durable follow-up with its own request ID. */
const MAX_REPAIRS = 2;

/** The model cannot be used as configured; nothing ran. The message says what to set. */
export class ModelSetupError extends Error {
	override readonly name = "ModelSetupError";
}

/** Fail before any storage or model work when the provider, model, or key is missing. */
export async function checkModel(models: Models, choice: ModelChoice): Promise<void> {
	const provider = models.getProvider(choice.provider);
	if (provider === undefined) {
		const known = models
			.getProviders()
			.map((p) => p.id)
			.join(", ");
		throw new ModelSetupError(`Unknown model provider "${choice.provider}". Known: ${known}`);
	}
	if (models.getModel(choice.provider, choice.modelId) === undefined) {
		throw new ModelSetupError(`${provider.name} has no model "${choice.modelId}"`);
	}
	if ((await models.getAuth(choice.provider)) === undefined) {
		const key = provider.auth.apiKey?.name ?? `${provider.name} credentials`;
		const variable = `${provider.id.toUpperCase().replaceAll("-", "_")}_API_KEY`;
		throw new ModelSetupError(`No ${key} found. Set it, usually as ${variable}.`);
	}
}
const HOUR_MS = 3_600_000;

/** The window ends at the end of `date`, or now when `date` is today. */
export function briefingWindow(date: string, lookbackHours: number, now: Date) {
	const endOfDay = Date.parse(`${date}T00:00:00Z`) + 24 * HOUR_MS;
	if (Number.isNaN(endOfDay)) throw new Error(`Invalid date ${date}; use YYYY-MM-DD`);
	if (endOfDay - 24 * HOUR_MS > now.getTime()) throw new Error(`${date} is in the future`);
	const until = Math.min(endOfDay, now.getTime());
	return {
		since: new Date(until - lookbackHours * HOUR_MS).toISOString(),
		until: new Date(until).toISOString(),
	};
}

function prompt(config: BriefingConfig, state: BriefingState): string {
	const repos = config.repos.map((r) => `- ${r.name}${r.focus ? ` (focus: ${r.focus})` : ""}`);
	const lines = [
		`Write the briefing for ${state.date}.`,
		`Window: ${state.since} to ${state.until} (UTC).`,
		"Repositories:",
		...repos,
	];
	if (config.instructions !== undefined)
		lines.push("", `Reader's instructions: ${config.instructions}`);
	return lines.join("\n");
}

async function findOrCreate(harness: Harness, key: string, options: RunOptions) {
	const window = briefingWindow(
		options.date,
		options.config.lookbackHours,
		options.now?.() ?? new Date(),
	);
	return harness.commit(async (tx) => {
		const index = await tx.doc(BriefingIndex);
		const existing = index.conversations[key];
		if (existing !== undefined) return { id: existing as ConversationId, resumed: true };
		const record = await tx.createConversation({ ownership: { kind: "ownerless" } });
		await configure(tx, record.id, { model: options.model, thinkingLevel: "low" });
		const state = await tx.doc(BriefingDoc, record.id);
		Object.assign(state, { date: options.date, ...window, repos: [...options.config.repos] });
		index.conversations[key] = record.id;
		return { id: record.id, resumed: false };
	}, options.context);
}

function describe(event: AgentEvent): string | undefined {
	if (event.type === "tool_execution_start") return `Fetching ${String(event.args["repo"] ?? "")}`;
	if (event.type === "tool_execution_end") return event.entry === undefined ? undefined : "Fetched";
	if (event.type === "auto_retry_start") return `Model error, retrying: ${event.errorMessage}`;
	if (event.type === "turn_start") return "Asking the model";
	return undefined;
}

/**
 * The request ID for this attempt. A pending submission (a crashed run) is reused, so its work
 * resumes; one that came back unanswered (the model failed) gets a fresh retry ID.
 */
async function attemptId(conversation: Conversation, key: string, context: Context) {
	return conversation.commit(async (tx) => {
		for (let attempt = 0; ; attempt++) {
			const requestId = attempt === 0 ? key : `${key}:retry:${attempt}`;
			const existing = await tx.submissionByRequest(conversation.id, requestId);
			if (existing?.status !== "unanswered") return requestId;
		}
	}, context);
}

async function ask(
	conversation: Conversation,
	content: string,
	requestId: string,
	context: Context,
) {
	const submission = await conversation.submit({ type: "input", content, requestId }, context);
	const settled = await submission.wait(context);
	if (settled.status !== "done" || settled.type !== "input") {
		const raw = settled.detail;
		const detail =
			raw === undefined ? "" : `: ${typeof raw === "string" ? raw : JSON.stringify(raw)}`;
		const hint =
			settled.reason === "model_error" ? ". Check the model name and its provider's API key." : "";
		throw new Error(
			`The briefing was not answered (${settled.reason ?? settled.status})${detail}${hint}`,
		);
	}
	const entry = await conversation.commit(
		(tx) => tx.entry(AssistantEntry, settled.answer),
		context,
	);
	const message = entry?.model?.[0];
	if (message?.role !== "assistant") throw new Error("The answer entry holds no assistant message");
	return message.content.flatMap((part) => (part.type === "text" ? [part.text] : [])).join("\n");
}

/** The model the conversation runs with: on a resumed run, the one chosen when it began. */
async function modelName(conversation: Conversation, context: Context) {
	const model = (await conversation.agent(context)).model;
	return model === undefined ? "unknown" : `${model.provider}/${model.modelId}`;
}

async function totalUsage(harness: Harness, id: ConversationId, context: Context) {
	const usage = await harness.snapshot(UsageDoc, id, context);
	let input = 0;
	let output = 0;
	let costUsd = 0;
	for (const bucket of Object.values(usage?.models ?? {})) {
		input += bucket.input + bucket.cacheRead + bucket.cacheWrite;
		output += bucket.output;
		costUsd += bucket.cost.total;
	}
	return { input, output, costUsd };
}

async function writeBriefing(harness: Harness, key: string, options: RunOptions) {
	const { id, resumed } = await findOrCreate(harness, key, options);
	const conversation = await harness.conversation(id, options.context);
	if (conversation === undefined) throw new Error(`Briefing conversation ${id} is missing`);
	const events = await watchEvents(harness, id, options.context);
	events.start(async (batch) => {
		for (const event of batch) {
			const message = describe(event);
			if (message !== undefined) options.onProgress?.(message);
		}
	});
	try {
		const initial = await harness.snapshot(BriefingDoc, id, options.context);
		if (initial === undefined) throw new Error(`Briefing conversation ${id} has no state`);
		const requestId = await attemptId(conversation, key, options.context);
		const content = prompt(options.config, initial);
		let markdown = await ask(conversation, content, requestId, options.context);
		let state = (await harness.snapshot(BriefingDoc, id, options.context)) ?? initial;
		let verification = verifyCitations(markdown, state.citable);
		for (let round = 1; round <= MAX_REPAIRS && verification.outcome === "unverified"; round++) {
			options.onProgress?.(`Repairing ${verification.unverified.length} unverified links`);
			const repair = repairRequest(verification);
			const repairId = `${requestId}:repair:${round}`;
			markdown = await ask(conversation, repair, repairId, options.context);
			state = (await harness.snapshot(BriefingDoc, id, options.context)) ?? state;
			verification = verifyCitations(markdown, state.citable);
		}
		return {
			key,
			date: state.date,
			window: { since: state.since, until: state.until },
			model: await modelName(conversation, options.context),
			markdown,
			verification,
			activity: state.activity,
			usage: await totalUsage(harness, id, options.context),
			resumed,
		};
	} finally {
		await events.stop();
	}
}

/**
 * Write one briefing, durably. Storage is a SQLite file: if the process dies mid-run, running
 * again with the same date and config resumes the same conversation and request.
 */
export async function runBriefing(options: RunOptions): Promise<BriefingResult> {
	await checkModel(options.models, options.model);
	const registry = createRegistry();
	registry.install(createBriefingExtension(options.github));
	const storage = await openNodeSqliteStorage(options.storagePath);
	const harness = await Harness.open(
		storage,
		{
			models: options.models,
			registry,
			settings: { toolExecution: "parallel", retry: { maxRetries: 3 } },
		},
		options.context,
	);
	try {
		harness.resume();
		return await writeBriefing(harness, briefingKey(options.date, options.config), options);
	} finally {
		// Close even when the caller cancelled: unfinished work stays pending for the next run.
		await harness.close(withoutAbortSignal(options.context));
	}
}
