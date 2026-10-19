import { describe, expect, it } from "vitest";
import { briefingKey, ConfigError, MAX_REPOS, parseConfig } from "./config.ts";

describe("parseConfig", () => {
	it("accepts a minimal config and defaults the lookback to a day", () => {
		expect(parseConfig({ repos: [{ name: "posit-dev/commons" }] })).toEqual({
			repos: [{ name: "posit-dev/commons" }],
			lookbackHours: 24,
		});
	});

	it("keeps focus notes and instructions, trimmed, and drops blank ones", () => {
		const config = parseConfig({
			repos: [
				{ name: "a/b", focus: "  releases  " },
				{ name: "c/d", focus: " " },
			],
			instructions: "",
		});
		expect(config.repos).toEqual([{ name: "a/b", focus: "releases" }, { name: "c/d" }]);
		expect(config).not.toHaveProperty("instructions");
	});

	it.each([
		[null, "must be a JSON object"],
		[{ repos: [] }, "at least one repository"],
		[{ repos: [{ name: "not a repo" }] }, 'must look like "owner/repo"'],
		[{ repos: [{ name: "https://github.com/a/b" }] }, 'must look like "owner/repo"'],
		[{ repos: ["a/b"] }, 'must be an object with a "name"'],
		[{ repos: [{ name: "a/b" }, { name: "A/B" }] }, "lists A/B twice"],
		[{ repos: [{ name: "a/b", focus: 3 }] }, "focus must be text"],
		[{ repos: [{ name: "a/b" }], lookbackHours: 0 }, "between 1 and 168"],
		[{ repos: [{ name: "a/b" }], lookbackHours: 169 }, "between 1 and 168"],
		[{ repos: [{ name: "a/b" }], lookbackHours: 1.5 }, "whole number"],
	])("rejects %j", (value, message) => {
		expect(() => parseConfig(value)).toThrow(ConfigError);
		expect(() => parseConfig(value)).toThrow(message);
	});

	it("rejects more repositories than the limit", () => {
		const repos = Array.from({ length: MAX_REPOS + 1 }, (_, i) => ({ name: `o/r${i}` }));
		expect(() => parseConfig({ repos })).toThrow(`the limit is ${MAX_REPOS}`);
	});
});

describe("briefingKey", () => {
	const config = parseConfig({ repos: [{ name: "a/b" }] });

	it("is the same for configs that mean the same thing", () => {
		const recased = parseConfig({ repos: [{ name: "A/B", focus: "" }], lookbackHours: 24 });
		expect(briefingKey("2026-10-19", recased)).toBe(briefingKey("2026-10-19", config));
	});

	it("changes with the date and with the config", () => {
		const key = briefingKey("2026-10-19", config);
		expect(briefingKey("2026-10-20", config)).not.toBe(key);
		expect(briefingKey("2026-10-19", { ...config, lookbackHours: 48 })).not.toBe(key);
		expect(key).toMatch(/^briefing:2026-10-19:[0-9a-f]{12}$/);
	});
});
