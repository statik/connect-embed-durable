/**
 * Provenance for a briefing, after commons' trusted/untrusted outcomes: a briefing is "verified"
 * only when every GitHub item it links to came back from `repo_activity` for this briefing.
 */
export interface Verification {
	readonly outcome: "verified" | "unverified";
	readonly cited: number;
	readonly unverified: readonly string[];
}

const ITEM_LINK =
	/https:\/\/github\.com\/[\w.-]+\/[\w.-]+\/(?:pull|issues|releases\/tag)\/[^\s)\]>"']+/g;

function normalize(url: string): string {
	return url
		.replace(/[.,;:]+$/, "")
		.replace(/#.*$/, "")
		.toLowerCase();
}

/** Check every pull request, issue, and release link in `markdown` against `citable`. */
export function verifyCitations(markdown: string, citable: readonly string[]): Verification {
	const allowed = new Set(citable.map(normalize));
	const cited = new Set((markdown.match(ITEM_LINK) ?? []).map(normalize));
	const unverified = [...cited].filter((url) => !allowed.has(url)).toSorted();
	return {
		outcome: unverified.length === 0 ? "verified" : "unverified",
		cited: cited.size,
		unverified,
	};
}

/** The follow-up that asks the model to repair the links it could not have seen. */
export function repairRequest(verification: Verification): string {
	return [
		"These links in your briefing did not come from repo_activity, so they may be wrong:",
		...verification.unverified.map((url) => `- ${url}`),
		"Rewrite the whole briefing. Use only links that repo_activity returned, or remove the statement.",
	].join("\n");
}
