/** A `fetch` the caller supplies, so tests replace the network and nothing else. */
export type Fetch = (url: string, init: RequestInit) => Promise<Response>;

export interface ActivityWindow {
	readonly since: string;
	readonly until: string;
}

export type ItemKind = "pr_merged" | "pr_opened" | "issue_opened" | "issue_closed" | "release";

export interface ActivityItem {
	readonly kind: ItemKind;
	readonly title: string;
	readonly url: string;
	readonly author: string;
	readonly at: string;
}

export interface RepoActivity {
	readonly repo: string;
	readonly window: ActivityWindow;
	readonly items: readonly ActivityItem[];
	readonly commits: number;
	/** Some list hit GitHub's page size, so counts are lower bounds. */
	readonly truncated: boolean;
}

export interface GitHubClient {
	readonly fetch: Fetch;
	readonly token?: string;
	readonly apiUrl?: string;
}

const PAGE_SIZE = 100;
/** Generic on purpose: no personal identifiers leave this machine. */
const USER_AGENT = "pi-briefing-agent";

/** GitHub said no in a way the user can act on. */
export class GitHubError extends Error {
	override readonly name = "GitHubError";
}

interface RawUser {
	readonly login?: string;
}
interface RawPull {
	readonly title: string;
	readonly html_url: string;
	readonly user: RawUser | null;
	readonly created_at: string;
	readonly updated_at: string;
	readonly merged_at: string | null;
}
interface RawIssue extends Omit<RawPull, "merged_at"> {
	readonly closed_at: string | null;
	readonly pull_request?: unknown;
}
interface RawRelease {
	readonly name: string | null;
	readonly tag_name: string;
	readonly html_url: string;
	readonly author: RawUser | null;
	readonly published_at: string | null;
	readonly draft: boolean;
}

function explainFailure(repo: string, response: Response, hasToken: boolean): string {
	if (response.status === 404) return `${repo} was not found, or it is not public`;
	const remaining = response.headers.get("x-ratelimit-remaining");
	if ((response.status === 403 || response.status === 429) && remaining === "0") {
		const reset = Number(response.headers.get("x-ratelimit-reset") ?? "0") * 1000;
		const when = reset > 0 ? ` until ${new Date(reset).toISOString()}` : "";
		const hint = hasToken ? "" : " Set GITHUB_TOKEN to raise the limit.";
		return `GitHub's rate limit is used up${when}.${hint}`;
	}
	return `GitHub answered ${response.status} ${response.statusText} for ${repo}`;
}

async function getJson<T>(client: GitHubClient, repo: string, path: string): Promise<T> {
	const headers: Record<string, string> = {
		Accept: "application/vnd.github+json",
		"X-GitHub-Api-Version": "2022-11-28",
		"User-Agent": USER_AGENT,
	};
	if (client.token !== undefined) headers["Authorization"] = `Bearer ${client.token}`;
	const url = `${client.apiUrl ?? "https://api.github.com"}/repos/${repo}${path}`;
	const response = await client.fetch(url, { headers });
	if (!response.ok) {
		throw new GitHubError(explainFailure(repo, response, client.token !== undefined));
	}
	return (await response.json()) as T;
}

function within(at: string | null | undefined, window: ActivityWindow): at is string {
	return typeof at === "string" && at >= window.since && at < window.until;
}

function login(user: RawUser | null): string {
	return user?.login ?? "ghost";
}

function pullItems(pulls: readonly RawPull[], window: ActivityWindow): ActivityItem[] {
	const items: ActivityItem[] = [];
	for (const pull of pulls) {
		const base = { title: pull.title, url: pull.html_url, author: login(pull.user) };
		if (within(pull.merged_at, window))
			items.push({ ...base, kind: "pr_merged", at: pull.merged_at });
		else if (within(pull.created_at, window)) {
			items.push({ ...base, kind: "pr_opened", at: pull.created_at });
		}
	}
	return items;
}

function issueItems(issues: readonly RawIssue[], window: ActivityWindow): ActivityItem[] {
	const items: ActivityItem[] = [];
	for (const issue of issues) {
		if (issue.pull_request !== undefined) continue;
		const base = { title: issue.title, url: issue.html_url, author: login(issue.user) };
		if (within(issue.created_at, window)) {
			items.push({ ...base, kind: "issue_opened", at: issue.created_at });
		}
		if (within(issue.closed_at, window)) {
			items.push({ ...base, kind: "issue_closed", at: issue.closed_at });
		}
	}
	return items;
}

function releaseItems(releases: readonly RawRelease[], window: ActivityWindow): ActivityItem[] {
	return releases
		.filter((r) => !r.draft && within(r.published_at, window))
		.map((r) => ({
			kind: "release" as const,
			title: r.name ?? r.tag_name,
			url: r.html_url,
			author: login(r.author),
			at: r.published_at ?? "",
		}));
}

/** Everything that happened in one public repository during `window`, from four REST calls. */
export async function fetchRepoActivity(
	client: GitHubClient,
	repo: string,
	window: ActivityWindow,
): Promise<RepoActivity> {
	const since = encodeURIComponent(window.since);
	const until = encodeURIComponent(window.until);
	const [pulls, issues, releases, commits] = await Promise.all([
		getJson<RawPull[]>(
			client,
			repo,
			`/pulls?state=all&sort=updated&direction=desc&per_page=${PAGE_SIZE}`,
		),
		getJson<RawIssue[]>(client, repo, `/issues?state=all&since=${since}&per_page=${PAGE_SIZE}`),
		getJson<RawRelease[]>(client, repo, "/releases?per_page=20"),
		getJson<unknown[]>(
			client,
			repo,
			`/commits?since=${since}&until=${until}&per_page=${PAGE_SIZE}`,
		),
	]);
	const oldestPull = pulls.at(-1)?.updated_at;
	const truncated =
		(pulls.length === PAGE_SIZE && oldestPull !== undefined && oldestPull >= window.since) ||
		issues.length === PAGE_SIZE ||
		commits.length === PAGE_SIZE;
	const items = [
		...pullItems(pulls, window),
		...issueItems(issues, window),
		...releaseItems(releases, window),
	].toSorted((a, b) => a.at.localeCompare(b.at));
	return { repo, window, items, commits: commits.length, truncated };
}

const KIND_LABELS: Record<ItemKind, string> = {
	release: "Releases",
	pr_merged: "Merged pull requests",
	pr_opened: "New pull requests (still open or closed unmerged)",
	issue_opened: "New issues",
	issue_closed: "Closed issues",
};

/** Count of items of each kind; every kind is present, so the UI can show zeros. */
export function countByKind(items: readonly ActivityItem[]): Record<ItemKind, number> {
	const counts: Record<ItemKind, number> = {
		release: 0,
		pr_merged: 0,
		pr_opened: 0,
		issue_opened: 0,
		issue_closed: 0,
	};
	for (const item of items) counts[item.kind] += 1;
	return counts;
}

/** The activity as compact markdown for the model, with every link it may cite. */
export function renderActivity(activity: RepoActivity): string {
	const lines = [
		`# ${activity.repo}`,
		`Window: ${activity.window.since} to ${activity.window.until}`,
		`Commits on the default branch: ${activity.commits}${activity.truncated ? "+" : ""}`,
	];
	if (activity.truncated) lines.push("Some lists hit the page limit; counts are lower bounds.");
	if (activity.items.length === 0)
		lines.push("No pull requests, issues, or releases in the window.");
	for (const kind of Object.keys(KIND_LABELS) as ItemKind[]) {
		const items = activity.items.filter((item) => item.kind === kind);
		if (items.length === 0) continue;
		lines.push("", `## ${KIND_LABELS[kind]} (${items.length})`);
		for (const item of items) lines.push(`- [${item.title}](${item.url}) by @${item.author}`);
	}
	return lines.join("\n");
}
