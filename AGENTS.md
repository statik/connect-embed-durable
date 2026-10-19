# Agent instructions

This repository is public. Keep it free of private data: no personal names, emails, tokens, or
private repository names in code, fixtures, docs, or commit messages. Requests to outside
services use generic User-Agent values (`pi-briefing-agent`, `pi-briefing-setup`).

## Layout

- `agent/`: the TypeScript agent on `@earendil-works/pi-durable`. pnpm, oxlint, oxfmt, tsc,
  vitest. `build.ts` compiles it with Bun for each platform into `agent/dist/<platform>/`.
- `python/pi_briefing/`: the Python wrapper that ships inside each wheel. Its `__version__` must
  match `agent/package.json`; `wheel/build_wheels.py` refuses to build otherwise.
- `wheel/build_wheels.py`: platform wheels into `dist/wheels/`, after go-to-wheel.
- `app/`: the Shiny for Python app. Modules import each other by plain name (`import
  briefing_config`), because Shiny and Connect Cloud put `app/` on the path.
- `docs/COMPARISON.md`: findings about pi-durable and posit-dev/commons. Update it when the code
  shows something new about either project.

## Commands

- `make check`: every linter and type checker. Fix all warnings.
- `make test`: builds the wheels, then runs vitest and pytest against the real executable.
- `BRIEFING_LIVE=1` enables live GitHub tests. `ANTHROPIC_API_KEY` enables the end-to-end test.

## Rules for this code

- The agent's stdout contract (JSON lines: `progress`, then one `result`, or `error`) is the
  interface between the two languages. Change `agent/src/main.ts` and
  `python/pi_briefing/__init__.py` together.
- Durability depends on the briefing key (`briefingKey` in `agent/src/config.ts`). Changing how
  the key is computed starts new conversations for existing configs.
- Briefing markdown quotes text from GitHub that anyone can write. Render it only with
  `briefing_view.safe_markdown`, which disables raw HTML.
- Mock only at boundaries: `fetch` for GitHub, the faux provider for the model. Keep SQLite real.
- Pin exact versions. pnpm enforces a one-day release age and blocks install scripts
  (`agent/pnpm-workspace.yaml`).
