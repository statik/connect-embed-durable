import { existsSync } from "node:fs";
import { mkdtemp } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { BACKGROUND_CONTEXT, withCancel } from "@earendil-works/chord/context";
import { createModels } from "@earendil-works/pi-ai/models";
import {
	fauxAssistantMessage,
	fauxProvider,
	type FauxResponseStep,
	fauxToolCall,
} from "@earendil-works/pi-ai/providers/faux";
import { beforeEach, describe, expect, it } from "vitest";
import { briefingKey, parseConfig } from "./config.ts";
import { AT, fakeGitHub, issue, pull } from "./fake-github.ts";
import { builtinModels } from "@earendil-works/pi-ai/providers/all";
import {
	askBriefing,
	briefingWindow,
	checkModel,
	ModelSetupError,
	NoBriefingError,
	type RunOptions,
	runBriefing,
} from "./run.ts";
import type { TraceEvent } from "./trace.ts";

const PULL = "https://github.com/acme/widgets/pull/1";
const INVENTED = "https://github.com/acme/widgets/pull/404";
const config = parseConfig({ repos: [{ name: "acme/widgets", focus: "releases" }] });

const toolTurn = () =>
	fauxAssistantMessage([fauxToolCall("repo_activity", { repo: "acme/widgets" })], {
		stopReason: "toolUse",
	});
const answer = (text: string) => fauxAssistantMessage(text);
const failure = () =>
	fauxAssistantMessage("", { stopReason: "error", errorMessage: "invalid request: bad model" });

let faux: ReturnType<typeof fauxProvider>;
let github: ReturnType<typeof fakeGitHub>;
let options: RunOptions;

beforeEach(async () => {
	faux = fauxProvider();
	const models = createModels();
	models.setProvider(faux.provider);
	github = fakeGitHub({
		"acme/widgets": {
			pulls: [pull(1, { merged: true })],
			issues: [issue(2, { created: AT.inside })],
		},
	});
	const directory = await mkdtemp(join(tmpdir(), "briefing-test-"));
	options = {
		config,
		date: "2026-10-18",
		storagePath: join(directory, "briefings.sqlite"),
		model: { provider: "faux", modelId: "faux-1" },
		models,
		github: { fetch: github.fetch },
		context: BACKGROUND_CONTEXT,
		now: () => new Date("2026-10-18T20:00:00Z"),
	};
});

function respond(...steps: FauxResponseStep[]) {
	faux.setResponses(steps);
}

describe("runBriefing", () => {
	it("fetches each repository through the tool and verifies the answer's links", async () => {
		respond(toolTurn(), answer(`## acme/widgets\n- Merged [PR 1](${PULL})`));
		const trace: TraceEvent[] = [];
		const result = await runBriefing({ ...options, onTrace: (e) => trace.push(e) });
		expect(result).toMatchObject({
			date: "2026-10-18",
			window: { since: "2026-10-17T20:00:00.000Z", until: "2026-10-18T20:00:00.000Z" },
			model: "faux/faux-1",
			verification: { outcome: "verified", cited: 1, unverified: [] },
			activity: { "acme/widgets": { counts: { pr_merged: 1, issue_opened: 1 }, commits: 0 } },
			resumed: false,
		});
		expect(result.markdown).toContain(PULL);
		expect(result.items).toEqual([
			expect.objectContaining({
				repo: "acme/widgets",
				kind: "pr_merged",
				number: 1,
				labels: ["enhancement"],
			}),
			expect.objectContaining({
				repo: "acme/widgets",
				kind: "issue_opened",
				number: 2,
				labels: [],
			}),
		]);
		expect(trace).toContainEqual(
			expect.objectContaining({
				kind: "tool_start",
				name: "repo_activity",
				args: { repo: "acme/widgets" },
			}),
		);
		expect(trace).toContainEqual(
			expect.objectContaining({ kind: "tool_end", name: "repo_activity", isError: false }),
		);
		expect(trace).toContainEqual({ kind: "note", message: "Links: verified (1 cited)" });
		expect(trace.filter((e) => e.kind === "message_end")).toHaveLength(2);
		expect(github.requests).toHaveLength(4);
	});

	it("answers a second run for the same day and config from storage", async () => {
		respond(toolTurn(), answer(`Merged ${PULL}`));
		const first = await runBriefing(options);
		const calls = faux.state.callCount;
		const second = await runBriefing(options);
		expect(second).toEqual({ ...first, resumed: true });
		expect(faux.state.callCount).toBe(calls);
		expect(github.requests).toHaveLength(4);
	});

	it("asks the model to repair links that did not come from the tool", async () => {
		respond(toolTurn(), answer(`Merged ${INVENTED}`), answer(`Merged ${PULL}`));
		const result = await runBriefing(options);
		expect(result.verification.outcome).toBe("verified");
		expect(result.markdown).toBe(`Merged ${PULL}`);
	});

	it("stops repairing after two rounds and reports the briefing as unverified", async () => {
		const bad = answer(`Merged ${INVENTED}`);
		respond(toolTurn(), bad, bad, bad);
		const result = await runBriefing(options);
		expect(result.verification).toEqual({
			outcome: "unverified",
			cited: 1,
			unverified: [INVENTED],
		});
		expect(faux.getPendingResponseCount()).toBe(0);
	});

	it("retries with a fresh request after a run the model failed", async () => {
		respond(failure(), failure(), failure(), failure());
		await expect(runBriefing(options)).rejects.toThrow("No answer (model_error)");
		respond(toolTurn(), answer(`Merged ${PULL}`));
		const result = await runBriefing(options);
		expect(result).toMatchObject({ resumed: true, verification: { outcome: "verified" } });
	});

	it("resumes a run interrupted mid-generation without fetching again", async () => {
		const { context, cancel } = withCancel(BACKGROUND_CONTEXT);
		const hang: FauxResponseStep = (_context, streamOptions) =>
			new Promise((_resolve, reject) => {
				cancel();
				streamOptions?.signal?.addEventListener("abort", () => reject(new Error("closed")));
			});
		respond(toolTurn(), hang);
		await expect(runBriefing({ ...options, context })).rejects.toThrow();
		expect(github.requests).toHaveLength(4);

		respond(answer(`Merged ${PULL}`));
		const result = await runBriefing(options);
		expect(result).toMatchObject({ resumed: true, verification: { outcome: "verified" } });
		expect(github.requests).toHaveLength(4);
	});
});

describe("briefingWindow", () => {
	const now = new Date("2026-10-19T09:30:00Z");

	it("ends now for today, and at midnight UTC for a past day", () => {
		expect(briefingWindow("2026-10-19", 24, now)).toEqual({
			since: "2026-10-18T09:30:00.000Z",
			until: "2026-10-19T09:30:00.000Z",
		});
		expect(briefingWindow("2026-10-10", 48, now)).toEqual({
			since: "2026-10-09T00:00:00.000Z",
			until: "2026-10-11T00:00:00.000Z",
		});
	});

	it("rejects a future day and a malformed date", () => {
		expect(() => briefingWindow("2026-10-20", 24, now)).toThrow("is in the future");
		expect(() => briefingWindow("yesterday", 24, now)).toThrow("Invalid date yesterday");
	});
});

/** Run with OPENROUTER_API_KEY unset, restoring it after. */
const withoutKey = async (run: () => Promise<void>) => {
	const saved = process.env["OPENROUTER_API_KEY"];
	delete process.env["OPENROUTER_API_KEY"];
	try {
		await run();
	} finally {
		if (saved !== undefined) process.env["OPENROUTER_API_KEY"] = saved;
	}
};

describe("checkModel", () => {
	const models = builtinModels();

	it("names the key to set when the provider has none", async () => {
		await withoutKey(async () => {
			const check = checkModel(models, {
				provider: "openrouter",
				modelId: "anthropic/claude-sonnet-5.5",
			});
			await expect(check).rejects.toThrow(ModelSetupError);
			await expect(check).rejects.toThrow(
				"No OpenRouter API key found. Set it, usually as OPENROUTER_API_KEY.",
			);
		});
	});

	it("rejects an unknown provider and lists the known ones", async () => {
		await expect(checkModel(models, { provider: "nope", modelId: "x" })).rejects.toThrow(
			/Unknown model provider "nope"\. Known: .*openrouter/,
		);
	});

	it("rejects a model the provider does not have", async () => {
		await expect(
			checkModel(models, { provider: "openrouter", modelId: "nope/nope" }),
		).rejects.toThrow('OpenRouter has no model "nope/nope"');
	});

	it("stops runBriefing before it opens storage", async () => {
		await withoutKey(async () => {
			const choice = { provider: "openrouter", modelId: "anthropic/claude-sonnet-5.5" };
			await expect(runBriefing({ ...options, models, model: choice })).rejects.toThrow(
				ModelSetupError,
			);
		});
		expect(existsSync(options.storagePath)).toBe(false);
	});
});

describe("askBriefing", () => {
	const key = briefingKey("2026-10-18", config);
	const question = { key, question: "What merged?", requestId: "q1" };

	it("answers in the briefing's conversation, with its own spend and checked links", async () => {
		respond(toolTurn(), answer(`Merged ${PULL}`), answer(`[PR 1](${PULL}) merged.`));
		const briefing = await runBriefing(options);
		const trace: TraceEvent[] = [];
		const reply = await askBriefing({ ...options, ...question, onTrace: (e) => trace.push(e) });
		expect(reply).toMatchObject({
			question: "What merged?",
			model: "faux/faux-1",
			markdown: `[PR 1](${PULL}) merged.`,
			verification: { outcome: "verified", cited: 1 },
		});
		expect(reply.usage.output).toBeGreaterThan(0);
		expect(reply.usage.output).toBeLessThan(briefing.usage.output);
		expect(trace).toContainEqual(
			expect.objectContaining({ kind: "message_end", text: `[PR 1](${PULL}) merged.` }),
		);
		expect(github.requests).toHaveLength(4);
	});

	it("returns the stored answer when asked again with the same request ID", async () => {
		respond(toolTurn(), answer(`Merged ${PULL}`), answer("Just PR 1."));
		await runBriefing(options);
		const first = await askBriefing({ ...options, ...question });
		const calls = faux.state.callCount;
		expect(await askBriefing({ ...options, ...question })).toEqual(first);
		expect(faux.state.callCount).toBe(calls);
	});

	it("keeps the briefing's own spend apart from follow-up questions", async () => {
		respond(toolTurn(), answer(`Merged ${PULL}`), answer("Just PR 1."));
		const first = await runBriefing(options);
		await askBriefing({ ...options, ...question });
		expect((await runBriefing(options)).usage).toEqual(first.usage);
	});

	it("says when there is no briefing to ask about", async () => {
		await expect(askBriefing({ ...options, ...question })).rejects.toThrow(NoBriefingError);
	});
});

describe("live trace", () => {
	it("streams the model's text while it writes", async () => {
		const slow = fauxProvider({ tokensPerSecond: 40 });
		const models = createModels();
		models.setProvider(slow.provider);
		const words = Array.from({ length: 30 }, (_, i) => `word${i}`).join(" ");
		slow.setResponses([toolTurn(), answer(`Merged ${PULL}. ${words}`)]);
		const trace: TraceEvent[] = [];
		await runBriefing({ ...options, models, onTrace: (e) => trace.push(e) });
		const deltas = trace.filter((e) => e.kind === "text");
		expect(deltas.length).toBeGreaterThan(1);
		const streamed = deltas.map((e) => (e.kind === "text" ? e.delta : "")).join("");
		const full = `Merged ${PULL}. ${words}`;
		// Partials are committed on a timer, so the stream is a prefix; the end has the whole text.
		expect(full.startsWith(streamed)).toBe(true);
		expect(streamed.length).toBeGreaterThan(full.length / 2);
		expect(trace).toContainEqual(expect.objectContaining({ kind: "message_end", text: full }));
	});
});
