# connect-embed-durable

A daily briefing about GitHub activity in repositories you choose. It is an experiment with
[pi-durable](https://github.com/earendil-works/pi/tree/main/packages/durable), the durable agent
runtime from Pi, packaged so that it runs inside a Python app on
[Posit Connect Cloud](https://connect.posit.cloud).

- A **TypeScript agent** on pi-durable reads public GitHub activity and writes the briefing.
- **Bun** compiles the agent into one executable per platform. The executable includes the
  JavaScript runtime, so the host does not need Node.js.
- **Platform wheels** carry the executable into Python, as
  [go-to-wheel](https://github.com/simonw/go-to-wheel) does for Go programs.
- A **Shiny for Python app** guides you through the setup and shows each briefing.

The project uses only public data. It reads public repositories and sends no personal
identifiers to GitHub.

See [docs/COMPARISON.md](docs/COMPARISON.md) for what this experiment shows about pi-durable and
[posit-dev/commons](https://github.com/posit-dev/commons), and ideas for each.

## How it works

```text
Shiny app (app/)                     agent (agent/, TypeScript on pi-durable)
  setup wizard -> briefing.yaml
  "Write today's briefing" --JSON--> pi-briefing run --config --state
                           <-JSONL--   progress events, then one result
                                       |- Harness on SQLite (state/briefings.sqlite)
                                       |- tool repo_activity -> api.github.com (read-only)
                                       '- link check: every cited item must come from the tool
```

**Durable.** Each briefing has a key, made from the date and the config. The key maps to one
conversation and one submission request ID. If the process stops in the middle of a run, the next
run with the same key continues the same work. It does not fetch from GitHub again for tool calls
that finished. When a briefing is complete, a new request for it comes from storage, with no
model calls. The time window is fixed when the briefing starts, so a resumed run covers the same
hours.

**Verified links.** This idea comes from commons. The `repo_activity` tool records every pull
request, issue, and release link that it returns. After the model answers, the agent compares
each link in the briefing with that record. If a link is not in the record, the agent asks the
model to repair the briefing, at most two times. The app then shows a **Verified** or
**Unverified** marker.

## Run it locally

You need [Bun](https://bun.sh), [pnpm](https://pnpm.io), [uv](https://docs.astral.sh/uv/), and an
Anthropic API key.

```bash
export ANTHROPIC_API_KEY=...        # or set BRIEFING_MODEL to another provider/model
export GITHUB_TOKEN=...             # optional; raises GitHub's rate limit from 60 to 5,000 an hour
make dev                            # builds the agent and the wheels, then runs the app
```

When you open the app, it writes today's briefing for `app/briefing.yaml`. To change the
repositories, use **Set up**.

You can also run the agent without the app:

```bash
echo '{"repos":[{"name":"posit-dev/commons"}],"lookbackHours":168}' > /tmp/config.json
uv run pi-briefing run --config /tmp/config.json --state /tmp/briefing-state
```

## Deploy to Posit Connect Cloud

Connect Cloud installs Python packages from `app/requirements.txt`. The pi-briefing wheel is not
on PyPI, so `requirements.txt` gets it from a direct URL. A GitHub release of this repository is a
good place for it.

1. Push this repository to a public GitHub repository.
2. Build the wheels and attach them to a release:

   ```bash
   make wheels
   gh release create v0.1.0 dist/wheels/*.whl --title v0.1.0 --notes "Agent wheels"
   ```

3. Write `app/requirements.txt` with the release URL, then commit it:

   ```bash
   make requirements WHEEL_BASE_URL=https://github.com/OWNER/REPO/releases/download/v0.1.0
   ```

4. In Connect Cloud, deploy a Shiny app from the repository. Set the primary file to
   `app/app.py` and the Python version to 3.13.
5. Add the secret variable `ANTHROPIC_API_KEY`. You can also add `GITHUB_TOKEN`. A fine-grained
   token with no extra permissions can read public repositories.

The app writes a briefing on the first visit each day. Later visits that day get the stored
briefing.

Connect Cloud resets the app's files when the app restarts. To keep a config that you made in
the wizard, download `briefing.yaml` and commit it to `app/`.

## Develop

```bash
make check    # oxlint, oxfmt, and tsc for the agent; ruff and ty for Python
make test     # vitest for the agent; pytest for the wheel, the wrapper, and the app
```

The tests replace the network and the model at their boundaries: a fake `fetch` for GitHub and
pi-ai's faux provider for the model. Storage is real SQLite. One test stops a run in the middle
of a model call and then checks that the next run continues without fetching again. Tests that
call real services are skipped unless you set their variables: `BRIEFING_LIVE=1` for GitHub, and
`ANTHROPIC_API_KEY` for the end-to-end briefing.

| Path | What it holds |
|---|---|
| `agent/src/` | The agent: config, GitHub client, pi-durable extension, runner, link check, CLI |
| `agent/build.ts` | `bun build --compile` for each platform, with a `node:sqlite` shim over `bun:sqlite` |
| `python/pi_briefing/` | The Python wrapper that goes into each wheel |
| `wheel/build_wheels.py` | Builds the platform wheels (the go-to-wheel method) |
| `scripts/write_requirements.py` | Writes `app/requirements.txt` for Connect Cloud |
| `app/` | The Shiny app, its default config, and its helpers |
| `docs/COMPARISON.md` | pi-durable compared with commons |

## License

MIT
