/**
 * A stand-in for api.github.com in tests: answers the four list endpoints `fetchRepoActivity`
 * calls from in-memory fixtures, and records every request it gets.
 */
import type { Fetch } from "./github.ts";

export interface FakeRepo {
	readonly pulls?: readonly object[];
	readonly issues?: readonly object[];
	readonly releases?: readonly object[];
	readonly commits?: number;
}

export interface FakeGitHub {
	readonly fetch: Fetch;
	readonly requests: { url: string; headers: Record<string, string> }[];
}

const ENDPOINT =
	/^https:\/\/api\.github\.com\/repos\/([^/]+\/[^/]+)\/(pulls|issues|releases|commits)\?/;

export function fakeGitHub(repos: Record<string, FakeRepo>, failure?: Response): FakeGitHub {
	const requests: FakeGitHub["requests"] = [];
	const fetch: Fetch = async (url, init) => {
		requests.push({ url, headers: { ...(init.headers as Record<string, string>) } });
		if (failure !== undefined) return failure.clone();
		const match = ENDPOINT.exec(url);
		const repo = match?.[1] === undefined ? undefined : repos[match[1]];
		if (match === null || repo === undefined) return new Response("{}", { status: 404 });
		const body =
			match[2] === "commits"
				? Array.from({ length: repo.commits ?? 0 }, (_, i) => ({ sha: String(i) }))
				: (repo[match[2] as "pulls" | "issues" | "releases"] ?? []);
		return Response.json(body);
	};
	return { fetch, requests };
}

export const WINDOW = { since: "2026-10-18T00:00:00.000Z", until: "2026-10-19T00:00:00.000Z" };
const INSIDE = "2026-10-18T12:00:00Z";
const BEFORE = "2026-10-17T12:00:00Z";

export function pull(number: number, fields: { merged?: boolean; created?: string } = {}) {
	return {
		number,
		labels: [{ name: "enhancement" }],
		title: `PR ${number}`,
		html_url: `https://github.com/acme/widgets/pull/${number}`,
		user: { login: "ada" },
		created_at: fields.created ?? BEFORE,
		updated_at: INSIDE,
		merged_at: fields.merged ? INSIDE : null,
	};
}

export function issue(number: number, fields: { created?: string; closed?: string } = {}) {
	return {
		number,
		labels: [],
		title: `Issue ${number}`,
		html_url: `https://github.com/acme/widgets/issues/${number}`,
		user: { login: "grace" },
		created_at: fields.created ?? BEFORE,
		updated_at: INSIDE,
		closed_at: fields.closed ?? null,
	};
}

export const AT = { inside: INSIDE, before: BEFORE };
