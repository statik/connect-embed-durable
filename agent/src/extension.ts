import { Type } from "@earendil-works/pi-ai";
import { defineDoc, defineExtension, defineTool, section } from "@earendil-works/pi-durable";
import type { TraceUsage } from "./trace.ts";
import type { RepoConfig } from "./config.ts";
import {
	type ActivityItem,
	countByKind,
	fetchRepoActivity,
	type GitHubClient,
	type ItemKind,
	renderActivity,
} from "./github.ts";

/**
 * Per-briefing state, written in the commit that creates the conversation. The window is fixed
 * there, so a run resumed after a crash covers exactly the same hours as the first attempt.
 */
export type BriefingState = {
	date: string;
	since: string;
	until: string;
	repos: RepoConfig[];
	/** Every link `repo_activity` returned. Only these may appear in the briefing. */
	citable: string[];
	/** Every item `repo_activity` returned, for the app to query. */
	items: ActivityItem[];
	/** What writing the briefing cost, fixed when it first completes; follow-ups are not in it. */
	usage: TraceUsage | null;
	/** What each follow-up question cost, by request ID, fixed when it first completes. */
	asks: Record<string, TraceUsage>;
	activity: Record<
		string,
		{ counts: Record<ItemKind, number>; commits: number; truncated: boolean }
	>;
};

export const BriefingDoc = defineDoc<BriefingState>({
	kind: "briefing.state",
	version: 1,
	scope: "conversation",
	history: "latest",
	fork: "current",
	initial: () => ({
		date: "",
		since: "",
		until: "",
		repos: [],
		citable: [],
		items: [],
		usage: null,
		asks: {},
		activity: {},
	}),
});

/** Maps each briefing key to its conversation, so the same date and config reuse one transcript. */
export const BriefingIndex = defineDoc<{ conversations: Record<string, number> }>({
	kind: "briefing.index",
	version: 1,
	scope: "session",
	initial: () => ({ conversations: {} }),
});

const PREAMBLE = `You write a daily engineering briefing about GitHub activity in a few public repositories.

How to work:
- Call repo_activity once for every repository you are asked about. Call them all in one turn.
- Base every statement on what repo_activity returned. Do not guess at activity it did not report.
- Link pull requests, issues, and releases with the exact URLs repo_activity gave you. Never build a URL yourself.

How to write:
- Start with a "Highlights" section: at most five bullets, the most important things across all repositories.
- Then one section per repository with a heading "## owner/repo". Group by releases, merged work, and new issues.
- When a repository has a focus note, put what matches it first and say so.
- If a repository had no activity, say so in one line.
- Be brief and concrete. Use plain words. No filler, no closing summary.`;

const repoActivityParameters = Type.Object({
	repo: Type.String({
		description: 'The repository as "owner/repo", from the list you were given',
	}),
});

/** The extension a briefing conversation runs with: one read-only tool and the writing rules. */
export function createBriefingExtension(github: GitHubClient) {
	const repoActivity = defineTool({
		name: "repo_activity",
		description:
			"Releases, merged and new pull requests, new and closed issues, and the commit count for one " +
			"repository during this briefing's window.",
		parameters: repoActivityParameters,
		// Read-only GET requests: rerunning after a crash is harmless.
		replay: "safe",
		execute: async (args, api, context) => {
			const state = await api.snapshot(BriefingDoc, api.conversationId, context);
			if (state === undefined) throw new Error("This conversation has no briefing state");
			const wanted = state.repos.find((r) => r.name.toLowerCase() === args.repo.toLowerCase());
			if (wanted === undefined) {
				const names = state.repos.map((r) => r.name).join(", ");
				throw new Error(`${args.repo} is not in this briefing. Use one of: ${names}`);
			}
			const window = { since: state.since, until: state.until };
			const activity = await fetchRepoActivity(github, wanted.name, window);
			await api.commit(async (tx) => {
				const doc = await tx.doc(BriefingDoc, api.conversationId);
				const known = new Set(doc.citable);
				for (const item of activity.items) if (!known.has(item.url)) doc.citable.push(item.url);
				// A rerun of the tool (a crash, or a follow-up question) must not add items twice.
				const seen = new Set(doc.items.map((item) => `${item.kind} ${item.url}`));
				for (const item of activity.items) {
					if (!seen.has(`${item.kind} ${item.url}`)) doc.items.push(item);
				}
				doc.activity[wanted.name] = {
					counts: countByKind(activity.items),
					commits: activity.commits,
					truncated: activity.truncated,
				};
			}, context);
			const focus = wanted.focus === undefined ? "" : `\nFocus note: ${wanted.focus}`;
			return { output: [{ type: "text", text: renderActivity(activity) + focus }] };
		},
	});
	return defineExtension({
		name: "briefing",
		tools: [repoActivity],
		sections: [section("preamble", () => PREAMBLE, { tag: false })],
	});
}
