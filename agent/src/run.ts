import type { Context } from "@earendil-works/chord";
import { withoutAbortSignal } from "@earendil-works/chord/context";
import type { Models } from "@earendil-works/pi-ai/models";
import {
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
import type { ActivityItem, GitHubClient } from "./github.ts";
import { type TraceEvent, type TraceUsage, toTrace } from "./trace.ts";

export interface ModelChoice {
	readonly provider: string;
	readonly modelId: string;
}

/** What both commands share: where the storage is, and how to reach the world. */
export interface BriefingOptions {
	readonly storagePath: string;
	readonly models: Models;
	readonly github: GitHubClient;
	readonly context: Context;
	readonly onTrace?: (event: TraceEvent) => void;
}

export interface RunOptions extends BriefingOptions {
	readonly config: BriefingConfig;
	/** The briefing's day, `YYYY-MM-DD` in UTC. */
	readonly date: string;
	readonly model: ModelChoice;
	readonly now?: () => Date;
}

export interface AskOptions extends BriefingOptions {
	/** The briefing's key, as in its result. */
	readonly key: string;
	readonly question: string;
	/** The caller's ID for this question; asking again with it returns the same answer. */
	readonly requestId: string;
}

export interface BriefingResult {
	readonly key: string;
	readonly date: string;
	readonly window: { readonly since: string; readonly until: string };
	readonly model: string;
	readonly markdown: string;
	readonly verification: Verification;
	readonly activity: BriefingState["activity"];
	readonly items: readonly ActivityItem[];
	readonly usage: TraceUsage;
	/** The conversation already existed: this run picked up earlier work instead of starting fresh. */
	readonly resumed: boolean;
}

export interface AskResult {
	readonly key: string;
	readonly question: string;
	readonly model: string;
	readonly markdown: string;
	readonly verification: Verification;
	/** Spend for this question alone. */
	readonly usage: TraceUsage;
}

/** Repair rounds after the first answer; each is a durable follow-up with its own request ID. */
const MAX_REPAIRS = 2;
const HOUR_MS = 3_600_000;

/** The model cannot be used as configured; nothing ran. The message says what to set. */
export class ModelSetupError extends Error {
	override readonly name = "ModelSetupError";
}

/** A follow-up question names a briefing that was never written. */
export class NoBriefingError extends Error {
	override readonly name = "NoBriefingError";
}

/** Fail before any model work when the provider, model, or key is missing. */
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
	if (config.instructions !== undefined) {
		lines.push("", `Reader's instructions: ${config.instructions}`);
	}
	return lines.join("\n");
}

function followUp(question: string): string {
	return [
		"A follow-up question about this briefing. Answer it directly and briefly, not as a briefing.",
		"Use what repo_activity already returned above; call it again only if that is not enough.",
		"Link items only with URLs repo_activity returned.",
		"",
		`Question: ${question}`,
	].join("\n");
}

async function findOrCreate(harness: Harness, key: string, options: RunOptions) {
	const now = options.now?.() ?? new Date();
	const window = briefingWindow(options.date, options.config.lookbackHours, now);
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
		throw new Error(`No answer (${settled.reason ?? settled.status})${detail}${hint}`);
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
async function storedModel(conversation: Conversation, context: Context) {
	const model = (await conversation.agent(context)).model;
	if (model === undefined) throw new Error(`Conversation ${conversation.id} has no model`);
	return { provider: model.provider, modelId: model.modelId };
}

/** Everything the conversation has spent so far, from its durable `pi.usage` document. */
async function totalUsage(harness: Harness, id: ConversationId, context: Context) {
	const usage = await harness.snapshot(UsageDoc, id, context);
	const total: TraceUsage = { input: 0, cacheRead: 0, cacheWrite: 0, output: 0, costUsd: 0 };
	for (const bucket of Object.values(usage?.models ?? {})) {
		total.input += bucket.input;
		total.cacheRead += bucket.cacheRead;
		total.cacheWrite += bucket.cacheWrite;
		total.output += bucket.output;
		total.costUsd += bucket.cost.total;
	}
	return total;
}

function usageSince(before: TraceUsage, after: TraceUsage): TraceUsage {
	return {
		input: after.input - before.input,
		cacheRead: after.cacheRead - before.cacheRead,
		cacheWrite: after.cacheWrite - before.cacheWrite,
		output: after.output - before.output,
		costUsd: after.costUsd - before.costUsd,
	};
}

async function briefingState(harness: Harness, id: ConversationId, context: Context) {
	const state = await harness.snapshot(BriefingDoc, id, context);
	if (state === undefined) throw new Error(`Briefing conversation ${id} has no state`);
	return state;
}

/** Stream the conversation's agent events to `onTrace` while `work` runs. */
async function traced<T>(
	harness: Harness,
	id: ConversationId,
	options: BriefingOptions,
	work: () => Promise<T>,
): Promise<T> {
	const events = await watchEvents(harness, id, options.context);
	events.start(async (batch) => {
		for (const event of batch) for (const trace of toTrace(event)) options.onTrace?.(trace);
	});
	try {
		return await work();
	} finally {
		await events.stop();
	}
}

/** Check the answer's links, asking for repairs as durable follow-ups while any fail. */
async function verifiedAnswer(
	harness: Harness,
	conversation: Conversation,
	first: { markdown: string; requestId: string },
	options: BriefingOptions,
) {
	let { markdown } = first;
	let verification = verifyCitations(
		markdown,
		(await briefingState(harness, conversation.id, options.context)).citable,
	);
	for (let round = 1; round <= MAX_REPAIRS && verification.outcome === "unverified"; round++) {
		const count = verification.unverified.length;
		options.onTrace?.({ kind: "note", message: `Repairing ${count} unverified links` });
		const repairId = `${first.requestId}:repair:${round}`;
		markdown = await ask(conversation, repairRequest(verification), repairId, options.context);
		const { citable } = await briefingState(harness, conversation.id, options.context);
		verification = verifyCitations(markdown, citable);
	}
	const { outcome, cited } = verification;
	options.onTrace?.({ kind: "note", message: `Links: ${outcome} (${cited} cited)` });
	return { markdown, verification };
}

async function writeBriefing(harness: Harness, key: string, options: RunOptions) {
	const { id, resumed } = await findOrCreate(harness, key, options);
	const conversation = await harness.conversation(id, options.context);
	if (conversation === undefined) throw new Error(`Briefing conversation ${id} is missing`);
	options.onTrace?.({ kind: "note", message: resumed ? "Resuming from storage" : "New briefing" });
	return traced(harness, id, options, async () => {
		const initial = await briefingState(harness, id, options.context);
		const requestId = await attemptId(conversation, key, options.context);
		const content = prompt(options.config, initial);
		const markdown = await ask(conversation, content, requestId, options.context);
		const answer = await verifiedAnswer(harness, conversation, { markdown, requestId }, options);
		const spent = await totalUsage(harness, id, options.context);
		const usage = await conversation.commit(async (tx) => {
			const doc = await tx.doc(BriefingDoc, id);
			doc.usage ??= spent;
			return { ...doc.usage };
		}, options.context);
		const state = await briefingState(harness, id, options.context);
		const model = await storedModel(conversation, options.context);
		return {
			key,
			date: state.date,
			window: { since: state.since, until: state.until },
			model: `${model.provider}/${model.modelId}`,
			...answer,
			activity: state.activity,
			items: state.items,
			usage,
			resumed,
		};
	});
}

async function withHarness<T>(
	options: BriefingOptions,
	work: (harness: Harness) => Promise<T>,
): Promise<T> {
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
		return await work(harness);
	} finally {
		// Close even when the caller cancelled: unfinished work stays pending for the next run.
		await harness.close(withoutAbortSignal(options.context));
	}
}

/**
 * Write one briefing, durably. Storage is a SQLite file: if the process dies mid-run, running
 * again with the same date and config resumes the same conversation and request.
 */
export async function runBriefing(options: RunOptions): Promise<BriefingResult> {
	await checkModel(options.models, options.model);
	const key = briefingKey(options.date, options.config);
	return withHarness(options, (harness) => writeBriefing(harness, key, options));
}

/**
 * Ask a follow-up question in a briefing's own conversation, where the model already has the
 * GitHub data it fetched. The answer's links are checked like the briefing's.
 */
export async function askBriefing(options: AskOptions): Promise<AskResult> {
	const { key } = options;
	return withHarness(options, async (harness) => {
		const index = await harness.snapshot(BriefingIndex, options.context);
		const id = index?.conversations[key] as ConversationId | undefined;
		const conversation =
			id === undefined ? undefined : await harness.conversation(id, options.context);
		if (conversation === undefined) {
			throw new NoBriefingError(`There is no briefing ${key} in this storage`);
		}
		const model = await storedModel(conversation, options.context);
		await checkModel(options.models, model);
		const before = await totalUsage(harness, conversation.id, options.context);
		return traced(harness, conversation.id, options, async () => {
			const requestId = `${key}:ask:${options.requestId}`;
			const content = followUp(options.question);
			const markdown = await ask(conversation, content, requestId, options.context);
			const answer = await verifiedAnswer(harness, conversation, { markdown, requestId }, options);
			const spent = usageSince(before, await totalUsage(harness, conversation.id, options.context));
			const usage = await conversation.commit(async (tx) => {
				const doc = await tx.doc(BriefingDoc, conversation.id);
				doc.asks[requestId] ??= spent;
				return { ...doc.asks[requestId] };
			}, options.context);
			return {
				key,
				question: options.question,
				model: `${model.provider}/${model.modelId}`,
				...answer,
				usage,
			};
		});
	});
}
