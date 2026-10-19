/**
 * The live trace: a small event vocabulary that the app renders as a timeline. The agent turns
 * pi-durable's agent events into it here; the app turns commons' tool callbacks into the same
 * kinds, so one renderer shows both agents side by side.
 */
import type { AgentEvent } from "@earendil-works/pi-durable";

/** Tokens by kind, and the cost estimated from the model catalog's prices. */
export type TraceUsage = {
	input: number;
	cacheRead: number;
	cacheWrite: number;
	output: number;
	costUsd: number;
};

export type TraceEvent =
	| { kind: "turn" }
	| { kind: "text"; delta: string }
	| { kind: "thinking"; delta: string }
	/** `text` is the whole message: short answers can finish before any delta is committed. */
	| { kind: "message_end"; text: string; stopReason: string; usage: TraceUsage }
	| { kind: "tool_start"; id: string; name: string; args: Record<string, unknown> }
	| { kind: "tool_end"; id: string; name: string; isError: boolean; output: string }
	| { kind: "retry"; message: string }
	| { kind: "error"; message: string }
	| { kind: "note"; message: string };

/** Tool output in the trace is a preview; the model saw the whole of it. */
export const OUTPUT_PREVIEW_CHARS = 4000;

type Message = NonNullable<Extract<AgentEvent, { type: "message_end" }>["entry"]["model"]>[number];

function messageText(message: Message | undefined): string {
	if (message === undefined || !("content" in message)) return "";
	const { content } = message;
	if (typeof content === "string") return content;
	return content.flatMap((part) => (part.type === "text" ? [part.text] : [])).join("\n");
}

function preview(text: string): string {
	if (text.length <= OUTPUT_PREVIEW_CHARS) return text;
	const more = text.length - OUTPUT_PREVIEW_CHARS;
	return `${text.slice(0, OUTPUT_PREVIEW_CHARS)}\n… ${more.toLocaleString("en-US")} more characters`;
}

function messageEnd(event: Extract<AgentEvent, { type: "message_end" }>): TraceEvent[] {
	const message = event.entry.model?.[0];
	if (message?.role !== "assistant") return [];
	const { usage } = message;
	return [
		{
			kind: "message_end",
			text: messageText(message),
			stopReason: message.stopReason,
			usage: {
				input: usage.input,
				cacheRead: usage.cacheRead,
				cacheWrite: usage.cacheWrite,
				output: usage.output,
				costUsd: usage.cost.total,
			},
		},
	];
}

function messageUpdate(event: Extract<AgentEvent, { type: "message_update" }>): TraceEvent[] {
	const events: TraceEvent[] = [];
	for (const change of event.changes) {
		// A block can arrive with its first text already in it, before any delta.
		if (change.type === "text_start" && change.block.type === "text" && change.block.text) {
			events.push({ kind: "text", delta: change.block.text });
		}
		if (
			change.type === "thinking_start" &&
			change.block.type === "thinking" &&
			change.block.thinking
		) {
			events.push({ kind: "thinking", delta: change.block.thinking });
		}
		if (change.type === "text_delta") events.push({ kind: "text", delta: change.delta });
		if (change.type === "thinking_delta") events.push({ kind: "thinking", delta: change.delta });
	}
	return events;
}

/** A message can start with text already in it: whatever arrived before its first commit. */
function messageStart(event: Extract<AgentEvent, { type: "message_start" }>): TraceEvent[] {
	const { message } = event;
	if (message.role !== "assistant") return [];
	const events: TraceEvent[] = [];
	for (const part of message.content) {
		if (part.type === "thinking" && part.thinking) {
			events.push({ kind: "thinking", delta: part.thinking });
		}
		if (part.type === "text" && part.text) events.push({ kind: "text", delta: part.text });
	}
	return events;
}

/** The trace events one pi-durable agent event stands for; most map to none. */
export function toTrace(event: AgentEvent): TraceEvent[] {
	switch (event.type) {
		case "turn_start":
			return [{ kind: "turn" }];
		case "message_start":
			return messageStart(event);
		case "message_update":
			return messageUpdate(event);
		case "message_end":
			return messageEnd(event);
		case "tool_execution_start":
			return [{ kind: "tool_start", id: event.toolCallId, name: event.toolName, args: event.args }];
		case "tool_execution_end": {
			const result = event.entry?.model?.[0];
			const isError = result?.role === "toolResult" ? result.isError : true;
			const output = preview(messageText(result));
			return [{ kind: "tool_end", id: event.toolCallId, name: event.toolName, isError, output }];
		}
		case "auto_retry_start":
			return [{ kind: "retry", message: `Attempt ${event.attempt}: ${event.errorMessage}` }];
		case "task_failed":
			return [{ kind: "error", message: `${event.kind}: ${event.message}` }];
		default:
			return [];
	}
}
