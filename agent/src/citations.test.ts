import { describe, expect, it } from "vitest";
import { repairRequest, verifyCitations } from "./citations.ts";

const PULL = "https://github.com/acme/widgets/pull/1";
const ISSUE = "https://github.com/acme/widgets/issues/2";

describe("verifyCitations", () => {
	it("verifies a briefing whose every item link came from the tool", () => {
		const markdown = `Merged [the fix](${PULL}). Opened ${ISSUE}.`;
		expect(verifyCitations(markdown, [PULL, ISSUE])).toEqual({
			outcome: "verified",
			cited: 2,
			unverified: [],
		});
	});

	it("flags links the tool never returned", () => {
		const invented = "https://github.com/acme/widgets/pull/999";
		const result = verifyCitations(`See [this](${PULL}) and [that](${invented}).`, [PULL]);
		expect(result).toEqual({ outcome: "unverified", cited: 2, unverified: [invented] });
	});

	it("ignores trailing punctuation, anchors, and letter case", () => {
		const recased = PULL.replace("acme/widgets", "ACME/Widgets");
		const markdown = `${recased}#issuecomment-1, and ${ISSUE}.`;
		expect(verifyCitations(markdown, [PULL, ISSUE])).toEqual({
			outcome: "verified",
			cited: 2,
			unverified: [],
		});
	});

	it("does not count links to repositories, people, or commits", () => {
		const markdown = "See https://github.com/acme/widgets and https://github.com/ada.";
		expect(verifyCitations(markdown, [])).toEqual({
			outcome: "verified",
			cited: 0,
			unverified: [],
		});
	});
});

describe("repairRequest", () => {
	it("lists every unverified link", () => {
		const text = repairRequest({ outcome: "unverified", cited: 2, unverified: [PULL, ISSUE] });
		expect(text).toContain(`- ${PULL}\n- ${ISSUE}`);
	});
});
