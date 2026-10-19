import type { AgentEvent } from "@earendil-works/pi-durable";
import { describe, expect, it } from "vitest";
import { OUTPUT_PREVIEW_CHARS, toTrace } from "./trace.ts";

const event = (value: object) => value as AgentEvent;

describe("toTrace", () => {
	it("streams text and thinking deltas, and ignores other changes", () => {
		const update = event({
			type: "message_update",
			usage: {},
			changes: [
				{ type: "text_start", contentIndex: 1, block: { type: "text", text: "He" } },
				{ type: "thinking_delta", contentIndex: 0, delta: "hmm" },
				{ type: "text_delta", contentIndex: 1, delta: "llo" },
				{ type: "toolcall_delta", contentIndex: 2, path: [], delta: "{" },
			],
		});
		expect(toTrace(update)).toEqual([
			{ kind: "text", delta: "He" },
			{ kind: "thinking", delta: "hmm" },
			{ kind: "text", delta: "llo" },
		]);
	});

	it("reports a finished model message's tokens by kind and its cost", () => {
		const usage = { input: 10, cacheRead: 100, cacheWrite: 5, output: 20, cost: { total: 0.25 } };
		const end = event({
			type: "message_end",
			entry: {
				model: [
					{
						role: "assistant",
						stopReason: "toolUse",
						usage,
						content: [
							{ type: "thinking", thinking: "hidden" },
							{ type: "text", text: "Let me look." },
						],
					},
				],
			},
		});
		expect(toTrace(end)).toEqual([
			{
				kind: "message_end",
				text: "Let me look.",
				stopReason: "toolUse",
				usage: { input: 10, cacheRead: 100, cacheWrite: 5, output: 20, costUsd: 0.25 },
			},
		]);
	});

	it("previews long tool output and keeps the error flag", () => {
		const text = "x".repeat(OUTPUT_PREVIEW_CHARS + 50);
		const end = event({
			type: "tool_execution_end",
			toolCallId: "c1",
			toolName: "repo_activity",
			entry: { model: [{ role: "toolResult", isError: true, content: [{ type: "text", text }] }] },
		});
		const [trace] = toTrace(end);
		expect(trace).toMatchObject({ kind: "tool_end", id: "c1", isError: true });
		expect(trace?.kind === "tool_end" && trace.output.endsWith("… 50 more characters")).toBe(true);
	});

	it("treats a tool that ended without a result entry as an error", () => {
		const end = event({ type: "tool_execution_end", toolCallId: "c1", toolName: "t" });
		expect(toTrace(end)).toEqual([
			{ kind: "tool_end", id: "c1", name: "t", isError: true, output: "" },
		]);
	});

	it("starts a model message with the text it already holds", () => {
		const start = event({
			type: "message_start",
			message: {
				role: "assistant",
				content: [
					{ type: "thinking", thinking: "plan" },
					{ type: "text", text: "Merged" },
				],
			},
		});
		expect(toTrace(start)).toEqual([
			{ kind: "thinking", delta: "plan" },
			{ kind: "text", delta: "Merged" },
		]);
		expect(
			toTrace(event({ type: "message_start", message: { role: "user", content: "hi" } })),
		).toEqual([]);
	});

	it("drops events the timeline does not show", () => {
		expect(toTrace(event({ type: "inbox_update", items: [] }))).toEqual([]);
	});
});
