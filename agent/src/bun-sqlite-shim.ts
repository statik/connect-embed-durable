/**
 * The part of `node:sqlite` that `@earendil-works/chord/sqlite/node` uses, over `bun:sqlite`.
 *
 * Bun does not implement `node:sqlite`, so `build.ts` resolves that import here when it compiles
 * the agent into one Bun executable. Node runs (tests, `pnpm dev`) use the real module.
 */
import { Database, type Statement } from "bun:sqlite";

type Value = null | number | bigint | string | Uint8Array;

class StatementSync {
	readonly #statement: Statement;

	constructor(statement: Statement) {
		this.#statement = statement;
	}

	run(...params: Value[]): void {
		this.#statement.run(...params);
	}

	/** `node:sqlite` answers a missing row with `undefined`; Bun answers `null`. */
	get(...params: Value[]): unknown {
		return this.#statement.get(...params) ?? undefined;
	}

	all(...params: Value[]): unknown[] {
		return this.#statement.all(...params);
	}
}

export class DatabaseSync {
	readonly #database: Database;

	constructor(path: string, options?: { readonly timeout?: number }) {
		this.#database = new Database(path, { create: true, strict: true });
		this.#database.exec(`PRAGMA busy_timeout = ${options?.timeout ?? 0}`);
	}

	exec(sql: string): void {
		this.#database.exec(sql);
	}

	prepare(sql: string): StatementSync {
		return new StatementSync(this.#database.prepare(sql));
	}

	close(): void {
		this.#database.close();
	}
}

export type { StatementSync };
