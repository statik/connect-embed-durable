# connect-embed-durable

A daily briefing about GitHub activity in repositories you choose, with questions answered by two
agents side by side. It is an experiment with
[pi-durable](https://github.com/earendil-works/pi/tree/main/packages/durable), the durable agent
runtime from Pi, and [commons](https://github.com/posit-dev/commons), Posit's framework for
trustworthy data agents. It runs inside a Python app on
[Posit Connect Cloud](https://connect.posit.cloud).

- A **TypeScript agent** on pi-durable reads GitHub activity and writes the briefing. It also
  answers follow-up questions in the briefing's own conversation.
- A **commons agent** in Python answers questions from all saved briefings, with trusted
  calculations and SQL.
- **Bun** compiles the TypeScript agent into one executable per platform, and **platform wheels**
  carry it into Python, as [go-to-wheel](https://github.com/simonw/go-to-wheel) does for Go.
- A **Shiny for Python app** has five pages:
  - **Briefing:** the briefing.
  - **Ask:** one question, answered by both agents.
  - **Trace:** each agent's work, live.
  - **Usage:** tokens and estimated cost.
  - **Set up:** the configuration wizard.

This code repository holds no private data. Private repository names and the data read from them
exist only at run time, in your deployment.

See [docs/COMPARISON.md](docs/COMPARISON.md) for what this experiment shows about pi-durable and
commons, and ideas for each.

## How it works

```text
Shiny app (app/)                               pi-durable agent (agent/, TypeScript)
  Briefing: "Write today's briefing" --JSON--> pi-briefing run --config --state
  Ask: question ---------------------+         pi-briefing ask --key --question
     |                               '------->   JSON lines: trace events, then one result
     v                                           |- Harness on SQLite (state/briefings.sqlite)
  commons agent (app/ask_agent.py)               |- tool repo_activity -> api.github.com
     DuckDB tables from saved results            '- link check against what the tool returned
     trusted measures, SQL, provenance
  Trace and Usage: both agents' events, live, in state/traces/*.jsonl
```

**Durable.** Each briefing has a key, made from the date and the config. The key maps to one
conversation and one submission request ID. If the process stops in the middle of a run, the next
run with the same key continues the same work. It does not fetch from GitHub again for tool calls
that finished. When a briefing is complete, a new request for it comes from storage, with no
model calls. The time window is fixed when the briefing starts, so a resumed run covers the same
hours.

**Verified links.** This idea comes from commons. The `repo_activity` tool records every pull
request, issue, and release link that it returns. After the model answers, the agent compares
each link with that record. If a link is not in the record, the agent asks the model to repair
the answer, at most two times. The app shows a **Verified** or **Unverified** marker. Follow-up
answers get the same check.

**Two agents, one question.** On the Ask page, a question goes to both agents:

- **commons** answers from every saved briefing. It uses three trusted measures for common
  questions (activity in the latest briefing, top contributors, and merged pull requests). For
  other questions it writes SQL. Each answer shows commons' provenance outcome.
- **pi-durable** answers in the conversation of the briefing on the Briefing page. That
  conversation already holds the GitHub data the agent fetched.

**Live view.** Both agents report their work in one small event vocabulary: model calls,
streamed text and thinking, tool calls with arguments and output, retries, notes, and tokens. The
Trace page shows the two side by side while they work.

**Usage.** Every model call reports its tokens: input, cache read, cache write, and output. The
cost is an estimate from the model catalog's prices in pi-ai. Both agents are priced from the
same catalog, so the numbers compare. The provider's bill can differ. The Usage page shows totals
by agent, a typical briefing, and every run and question.

## Run it locally

You need [Bun](https://bun.sh), [pnpm](https://pnpm.io), [uv](https://docs.astral.sh/uv/), and an
[OpenRouter](https://openrouter.ai) API key.

```bash
export OPENROUTER_API_KEY=...       # or set BRIEFING_MODEL and that provider's key
export GITHUB_TOKEN=...             # optional; reads private repositories, raises rate limits
make dev                            # builds the agent and the wheels, then runs the app
```

When you open the app, it writes today's briefing. To change the repositories, use **Set up**.

You can also run the agent without the app:

```bash
echo '{"repos":[{"name":"posit-dev/commons"}],"lookbackHours":168}' > /tmp/config.json
uv run pi-briefing run --config /tmp/config.json --state /tmp/briefing-state
uv run pi-briefing ask --state /tmp/briefing-state --key KEY_FROM_THE_RESULT \
  --question "What merged?" --request-id q1
```

## Choose a model

The default model is `openrouter/anthropic/claude-sonnet-5.5`, for both agents, so the only key
you need is `OPENROUTER_API_KEY`. To use another model, set `BRIEFING_MODEL` to `provider/model`,
with any provider and model in
[pi-ai](https://github.com/earendil-works/pi/tree/main/packages/ai)'s catalog. Then set that
provider's key. Some examples:

| `BRIEFING_MODEL` | Key |
|---|---|
| `openrouter/~anthropic/claude-sonnet-latest` | `OPENROUTER_API_KEY` |
| `anthropic/claude-sonnet-5-5` | `ANTHROPIC_API_KEY` |

The agent checks the provider, the model, and the key before it starts. If one is wrong, it stops
and says what to set.

A briefing keeps the model it started with. A new model takes effect from the next day's briefing,
or from the next config change.

## Private repositories

Set `GITHUB_TOKEN` to a
[fine-grained token](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/managing-your-personal-access-tokens)
with read-only access to the repositories you want: **Contents**, **Issues**, **Pull requests**,
and **Metadata**. The setup wizard then accepts those repositories and marks them **private**.

Everyone who can open the app sees briefings for everything the token can read, so:

1. **Share the app privately** on Connect Cloud, for example within your organization.
2. **Set `BRIEFING_REQUIRE_VIEWER=1`.** The app then shows nothing to a request that Connect
   does not identify (no `Posit-Connect-User-Session-Token` header). It is a guard against
   sharing the app publicly by mistake.
3. **Keep the config in the `BRIEFING_CONFIG_YAML` secret,** not in `app/briefing.yaml`. This
   code repository is public, and the config names your repositories. The wizard's **Download**
   button gives you the YAML to paste.

The app uses the first config it finds: one saved from the wizard (until the app restarts), then
`BRIEFING_CONFIG_YAML`, then `app/briefing.yaml`.

## Deploy to Posit Connect Cloud

Connect Cloud installs Python packages from `app/requirements.txt`. The pi-briefing wheel is not
on PyPI, so `requirements.txt` gets it from a direct URL to a GitHub release asset. The
[Release workflow](.github/workflows/release.yml) builds, tests, and attaches the wheels when you
push a version tag.

To release a new version:

1. Set the same version in `agent/package.json` and `python/pi_briefing/__init__.py`.
2. Point `app/requirements.txt` at the new release, then commit:

   ```bash
   make requirements WHEEL_BASE_URL=https://github.com/statik/connect-embed-durable/releases/download/v0.3.0
   ```

3. Tag and push: `git tag v0.3.0 && git push origin v0.3.0`. The workflow stops if the tag and
   the version differ, or if `app/requirements.txt` is stale.

To deploy:

1. In Connect Cloud, deploy a Shiny app from this repository. Set the primary file to
   `app/app.py` and the Python version to 3.13.
2. Add the variables:

   | Variable | Secret | Needed |
   |---|---|---|
   | `OPENROUTER_API_KEY` | yes | yes |
   | `GITHUB_TOKEN` | yes | for private repositories; otherwise optional |
   | `BRIEFING_CONFIG_YAML` | yes | for private repositories |
   | `BRIEFING_REQUIRE_VIEWER` | no | set to `1` for private repositories |
   | `BRIEFING_MODEL` | no | only to change the model |

The app writes a briefing on the first visit each day. Later visits that day get the stored
briefing. Connect Cloud resets the app's files when it restarts, which clears saved briefings,
traces, and usage.

The commons agent runs Python code that its model writes in a sandbox, which needs Linux user
namespaces. If the host does not allow them, the Ask page shows commons' error and the pi-durable
agent still answers.

## Develop

```bash
make check    # oxlint, oxfmt, and tsc for the agent; ruff and ty for Python
make test     # vitest for the agent; pytest for the wheel, the wrapper, and the app
```

The tests replace the network and the model only at their boundaries: a fake `fetch` for GitHub,
pi-ai's faux provider for the pi-durable agent, and a fake stream for commons. Storage is real
SQLite and the measures run on real DuckDB. Tests that call real services are skipped unless you
set their variables: `BRIEFING_LIVE=1` for GitHub, and `OPENROUTER_API_KEY` for the end-to-end
briefing and the live commons answer.

| Path | What it holds |
|---|---|
| `agent/src/` | The pi-durable agent: config, GitHub client, extension, runner, link check, trace, CLI |
| `agent/build.ts` | `bun build --compile` for each platform, with a `node:sqlite` shim over `bun:sqlite` |
| `python/pi_briefing/` | The Python wrapper that goes into each wheel |
| `wheel/build_wheels.py` | Builds the platform wheels (the go-to-wheel method) |
| `scripts/write_requirements.py` | Writes `app/requirements.txt` for Connect Cloud |
| `app/` | The Shiny app: one module per page, the commons agent, traces, and the config |
| `docs/COMPARISON.md` | pi-durable compared with commons |

## License

MIT
