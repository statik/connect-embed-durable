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
import { parseConfig } from "./config.ts";
import { AT, fakeGitHub, issue, pull } from "./fake-github.ts";
import { builtinModels } from "@earendil-works/pi-ai/providers/all";
import {
	briefingWindow,
	checkModel,
	ModelSetupError,
	type RunOptions,
	runBriefing,
} from "./run.ts";

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
		const progress: string[] = [];
		const result = await runBriefing({ ...options, onProgress: (m) => progress.push(m) });
		expect(result).toMatchObject({
			date: "2026-10-18",
			window: { since: "2026-10-17T20:00:00.000Z", until: "2026-10-18T20:00:00.000Z" },
			model: "faux/faux-1",
			verification: { outcome: "verified", cited: 1, unverified: [] },
			activity: { "acme/widgets": { counts: { pr_merged: 1, issue_opened: 1 }, commits: 0 } },
			resumed: false,
		});
		expect(result.markdown).toContain(PULL);
		expect(progress).toContain("Fetching acme/widgets");
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
		await expect(runBriefing(options)).rejects.toThrow("The briefing was not answered");
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
