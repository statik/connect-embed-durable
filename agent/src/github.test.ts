import { describe, expect, it } from "vitest";
import { AT, fakeGitHub, issue, pull, WINDOW } from "./fake-github.ts";
import { countByKind, fetchRepoActivity, renderActivity } from "./github.ts";

const REPO = "acme/widgets";

describe("fetchRepoActivity", () => {
	it("classifies pull requests, issues, and releases by what happened in the window", async () => {
		const github = fakeGitHub({
			[REPO]: {
				pulls: [pull(1, { merged: true }), pull(2, { created: AT.inside }), pull(3)],
				issues: [
					issue(10, { created: AT.inside, closed: AT.inside }),
					issue(11, { closed: AT.before }),
					{ ...issue(12, { created: AT.inside }), pull_request: {} },
				],
				releases: [
					{
						name: null,
						tag_name: "v1.0",
						html_url: "https://github.com/acme/widgets/releases/tag/v1.0",
						author: null,
						published_at: AT.inside,
						draft: false,
					},
					{
						name: "Draft",
						tag_name: "v2",
						html_url: "x",
						author: null,
						published_at: AT.inside,
						draft: true,
					},
				],
				commits: 7,
			},
		});
		const activity = await fetchRepoActivity({ fetch: github.fetch }, REPO, WINDOW);
		expect(countByKind(activity.items)).toEqual({
			release: 1,
			pr_merged: 1,
			pr_opened: 1,
			issue_opened: 1,
			issue_closed: 1,
		});
		expect(activity.items.find((i) => i.kind === "release")).toMatchObject({
			title: "v1.0",
			author: "ghost",
		});
		expect(activity.commits).toBe(7);
		expect(activity.truncated).toBe(false);
	});

	it("excludes an item stamped exactly at the end of the window", async () => {
		const github = fakeGitHub({ [REPO]: { issues: [issue(1, { created: WINDOW.until })] } });
		const activity = await fetchRepoActivity({ fetch: github.fetch }, REPO, WINDOW);
		expect(activity.items).toEqual([]);
	});

	it("marks counts as lower bounds when a list fills a whole page", async () => {
		const github = fakeGitHub({ [REPO]: { commits: 100 } });
		const activity = await fetchRepoActivity({ fetch: github.fetch }, REPO, WINDOW);
		expect(activity.truncated).toBe(true);
		expect(renderActivity(activity)).toContain("100+");
	});

	it("sends a generic User-Agent and the token only when one is given", async () => {
		const github = fakeGitHub({ [REPO]: {} });
		await fetchRepoActivity({ fetch: github.fetch }, REPO, WINDOW);
		await fetchRepoActivity({ fetch: github.fetch, token: "t0ken" }, REPO, WINDOW);
		const [anonymous, authed] = [github.requests[0]?.headers, github.requests[4]?.headers];
		expect(anonymous).toMatchObject({ "User-Agent": "pi-briefing-agent" });
		expect(anonymous).not.toHaveProperty("Authorization");
		expect(authed).toMatchObject({ Authorization: "Bearer t0ken" });
	});

	it("says a missing repository is missing or private", async () => {
		const github = fakeGitHub({});
		await expect(fetchRepoActivity({ fetch: github.fetch }, "acme/nope", WINDOW)).rejects.toThrow(
			"acme/nope was not found, or it is not public",
		);
	});

	it("explains an exhausted rate limit and suggests a token", async () => {
		const limited = new Response("{}", {
			status: 403,
			headers: { "x-ratelimit-remaining": "0", "x-ratelimit-reset": "1792500000" },
		});
		const github = fakeGitHub({ [REPO]: {} }, limited);
		await expect(fetchRepoActivity({ fetch: github.fetch }, REPO, WINDOW)).rejects.toThrow(
			/rate limit is used up until 2026-.*Set GITHUB_TOKEN/,
		);
	});
});

describe("renderActivity", () => {
	it("says so when nothing happened", async () => {
		const github = fakeGitHub({ [REPO]: {} });
		const text = renderActivity(await fetchRepoActivity({ fetch: github.fetch }, REPO, WINDOW));
		expect(text).toContain("No pull requests, issues, or releases in the window.");
	});
});

// The real API, as commons tests do: skipped offline or without a token, so CI stays fast.
const token = process.env["GITHUB_TOKEN"];
describe.skipIf(token === undefined || process.env["BRIEFING_LIVE"] !== "1")("live GitHub", () => {
	it("reads a week of public activity from posit-dev/commons", async () => {
		const until = new Date();
		const since = new Date(until.getTime() - 7 * 24 * 3_600_000);
		const window = { since: since.toISOString(), until: until.toISOString() };
		const activity = await fetchRepoActivity(
			{ fetch, ...(token ? { token } : {}) },
			"posit-dev/commons",
			window,
		);
		expect(activity.repo).toBe("posit-dev/commons");
		for (const item of activity.items) {
			expect(item.url).toMatch(/^https:\/\/github\.com\/posit-dev\/commons\//);
			expect(item.at >= window.since && item.at < window.until).toBe(true);
		}
	});
});
