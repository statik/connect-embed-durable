# Agent instructions

This repository is public. Keep it free of private data: no personal names, emails, tokens, or
private repository names in code, fixtures, docs, or commit messages. Configs that name private
repositories belong in the deployment's `BRIEFING_CONFIG_YAML` secret, never in
`app/briefing.yaml`. Requests to outside
services use generic User-Agent values (`pi-briefing-agent`, `pi-briefing-setup`).

## Layout

- `agent/`: the TypeScript agent on `@earendil-works/pi-durable`. pnpm, oxlint, oxfmt, tsc,
  vitest. `build.ts` compiles it with Bun for each platform into `agent/dist/<platform>/`.
- `python/pi_briefing/`: the Python wrapper that ships inside each wheel. Its `__version__` must
  match `agent/package.json`; `wheel/build_wheels.py` refuses to build otherwise.
- `wheel/build_wheels.py`: platform wheels into `dist/wheels/`, after go-to-wheel.
- `app/`: the Shiny for Python app. Modules import each other by plain name (`import
  briefing_config`), because Shiny and Connect Cloud put `app/` on the path. `app.py` only
  composes the pages (`*_page.py`); `app_state.py` reads the environment; `ask_agent.py` is the
  commons agent; `traces.py` holds the live traces that the Trace and Usage pages read.
- `docs/COMPARISON.md`: findings about pi-durable and posit-dev/commons. Update it when the code
  shows something new about either project.

## Commands

- `make check`: every linter and type checker. Fix all warnings.
- `make test`: builds the wheels, then runs vitest and pytest against the real executable.
- `BRIEFING_LIVE=1` enables live GitHub tests. `OPENROUTER_API_KEY` enables the end-to-end
  briefing and the live commons answer.

## Rules for this code

- The agent's stdout contract (JSON lines: `trace` events, then one `result`, or `error`; exit
  code 2 when something must change first) is the interface between the two languages. Change
  `agent/src/main.ts` and `python/pi_briefing/__init__.py` together.
- The trace event vocabulary is shared: `agent/src/trace.ts` emits it for pi-durable,
  `app/ask_agent.py` emits it for commons, and `app/traces.py` folds it for display. A new kind
  needs all three.
- `ask` takes a briefing's key, not its config, so a question always goes to the briefing on
  screen. Usage for a briefing and for each question is frozen in the briefing document when it
  first completes, so replays report the same spend.
- Durability depends on the briefing key (`briefingKey` in `agent/src/config.ts`). Changing how
  the key is computed starts new conversations for existing configs.
- Briefing markdown, model text, and tool output quote text from GitHub that anyone can write.
  Render them only with `briefing_view.safe_markdown` (raw HTML off) or htmltools escaping;
  never wrap them in `ui.HTML`.
- Reading a reactive value while a server function is being built raises "No current reactive
  context". Wrap such reads in `reactive.isolate()`.
- Mock only at boundaries: `fetch` for GitHub, the faux provider for pi-durable's model, a fake
  stream for commons' model. Keep SQLite and DuckDB real.
- Pin exact versions. pnpm enforces a one-day release age and blocks install scripts
  (`agent/pnpm-workspace.yaml`).

## commons work

Load the `commons` skill (`.agents/skills/commons/`, or `/skill:commons` in pi) before changing the
commons agent, its data, measures, dictionary, or evaluation. It is written for R; in this
Python project:

- `commons()` is `commons.Commons(...)`; see `app/ask_agent.py`.
- A measure is a function decorated with `@commons.measure(description=..., provenance=[...])`.
  Provenance goes in that `provenance` argument, not in roxygen `@provenance` tags.
- `data_source(con, dictionary = "...")` is `commons.data_source(..., dictionary=path)`.
- `vitals` and `trajectory_read()` are R only. Python commons blocks chatlas' `to_solver()`, so an
  evaluation here needs its own harness. Estimate its cost and get approval before any run.
