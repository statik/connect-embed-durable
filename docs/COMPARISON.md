# pi-durable and commons: what this experiment shows

This note compares [pi-durable](https://github.com/earendil-works/pi/tree/main/packages/durable)
1.3.0 with [posit-dev/commons](https://github.com/posit-dev/commons) at its October 2026 main
branch. It is based on building the briefing agent in this repository, and on reading both code
bases. It lists ideas that each project can take from the other.

## They solve different problems

The two projects are at different layers, so they are not competitors.

- **pi-durable is a runtime.** It makes an agent's work survive crashes and restarts. It stores
  conversations, model turns, tool calls, tasks, and documents in a log of atomic commits. It has
  no opinion about what the agent does.
- **commons is a domain framework.** It helps data teams build data agents that people can trust.
  It gives the model trusted calculations (measures), context, and SQL tools. It labels each
  answer with a provenance outcome: Trusted, Cited, or Untrusted. It uses chatlas or ellmer for
  model access and keeps conversation state in memory.

| Topic | pi-durable | commons |
|---|---|---|
| Language | TypeScript (Node, Bun with a shim, Cloudflare Workers) | Python and R, as two first-class packages |
| State | Commit log in SQLite, JSONL, or a Durable Object | chatlas or ellmer turns in memory; Shiny bookmarks restore the chat history only |
| After a crash | Pending tasks resume. Tools that are replay-safe run again; other tools give the model an `interrupted` result | The session is lost. A restored chat gets a reminder that the session behind its history is gone (`queue_restore_reminder`) |
| Duplicate requests | `requestId` returns the existing submission | No request IDs on submissions |
| Trust | Not in scope | A/B/C provenance tags, verified citations, provenance markers in the UI |
| Tools | TypeBox schemas, a replay policy, nested calls, a code mode sandbox with deterministic replay | Measures with injected data sources, SQL with a guard, R and Python sandboxes |
| Observability | Watches and agent events derived from commits, live | OpenTelemetry GenAI spans that both languages can read; no live event API for hosts |
| Usage and cost | Tokens and cost per model call, totals per conversation in `pi.usage` | chatlas token counts per turn; `get_cost()` uses chatlas' own price table |
| Cross-language agreement | A prose specification and a storage conformance suite | Executable fixtures in `tests/shared/` that both test suites run |
| UI | None; a host renders the committed view | shinychat components and provenance markers |

## What this experiment took from commons

The briefing agent uses two commons ideas, rebuilt on pi-durable.

1. **A trusted calculation.** `repo_activity` is deterministic code that gets all the facts. The
   model only writes the text. This is the commons "happy path": run trusted code, then report
   its result.
2. **Verified citations and a provenance marker.** The tool records the links it returned in a
   conversation document (`briefing.state`). After the answer, the runner checks each linked
   item against that record. If the check fails, it sends a repair request, at most two times.
   The UI then shows **Verified** or **Unverified**. This is a simpler form of commons'
   `match_citation`: links are exact identifiers, so there is no need for fuzzy quote matching.

pi-durable made the bookkeeping easy. The record of citable links is a document. A tool writes it
in a commit, so it survives a restart together with the transcript. Each repair is a follow-up
submission with its own request ID, so a crash during a repair does not send it again.

## Ideas for commons, from pi-durable

1. **Durable sessions.** Now, a restored commons chat has its history but not the session behind
   it: result handles (`r1`, `r2`, ...), the citation corpus, and the citation request state. If
   the agent stored these with each turn as documents, a restored or restarted session could
   continue fully, and it would not need a reminder that state is gone. A small store, such as
   one SQLite file for each conversation, is enough.
2. **Idempotent submissions.** A request ID on each user turn stops a reconnect or a double click
   from running an expensive measure two times. pi-durable's `requestId` does this.
3. **A replay policy for measures.** pi-durable asks each tool to declare whether it is safe to run
   again. Most measures are read-only and safe. A measure that writes or takes a long time could
   say that it is not. That policy is the first step toward resuming measures after a restart.
4. **Cost for each answer.** pi-durable keeps token and cost totals in a document for each
   conversation (`pi.usage`). commons calculates cost in its evaluations. The UI could also show
   the cost of each answer next to its provenance marker.
5. **Store before you show.** pi-durable shows nothing until it is committed. If commons wrote the
   provenance outcome and citation decision to its trace before it streamed the marker, the
   trajectory reviewer and the user would always see the same thing.

## Ideas for pi-durable, from commons

1. **Provenance as a shared concept.** Each app that wants trusted answers builds this alone, as
   this experiment did. pi-durable could define a small standard: a tool result declares how
   trusted it is (in `details` or a new field), and a helper derives an answer-level outcome from
   the results that the answer used. The commons A/B/C truth table is a tested design to start
   from.
2. **A bounded `onYield` continue.** A natural way to check citations is an `onYield` hook that
   returns `continue` with a repair request. But hooks can read documents and task memos only,
   and a continued run can be a new generation task, so a hook has no durable count of its
   repairs. A model that never fixes its links would loop. This experiment moved the check out of
   the hook, into follow-up submissions. A run-scoped counter for hooks, or a `maxContinues`
   setting, would let the check stay in the hook.
3. **Executable cross-language fixtures.** commons keeps everything that both languages must agree
   on in `tests/shared/`, as fixtures that both test suites run. pi-durable has a prose
   specification and a storage conformance suite. Hosts in other languages, such as the Python
   wrapper here, depend on the shape of the event stream. Fixtures for agent events would let
   those hosts test against the same contract.
4. **Bun storage.** `openNodeSqliteStorage` imports `node:sqlite`, and Bun does not have it. This
   experiment adds a 50-line shim over `bun:sqlite` (`agent/src/bun-sqlite-shim.ts`), which
   `build.ts` uses in place of `node:sqlite`. A `storage/sqlite/bun` entry point would make
   single-file executables (`bun build --compile`) a supported way to ship an agent.
5. **An embedding CLI.** Pi's coding agent has an RPC mode, but pi-durable has no command-line
   entry point. Hosts such as Python, R, and Posit Connect need one process that reads a request
   and writes events as JSON lines. `agent/src/main.ts` is about 100 lines and could be a
   starting point.
6. **A retry pattern for failed submissions.** A submission that settles `unanswered` keeps its
   request ID. A retry with the same ID returns the same failure. This experiment looks up the
   earlier submission and moves to `key:retry:N` only when it was unanswered, so that a crashed
   run still resumes (`attemptId` in `agent/src/run.ts`). A documented pattern, or an option on
   `submit()`, would save each app from rebuilding this.

## What observing both agents showed

The app shows both agents' work live, in one event vocabulary
([`agent/src/trace.ts`](../agent/src/trace.ts) and [`app/traces.py`](../app/traces.py)), and
prices both from one catalog. Building that showed these differences.

1. **pi-durable is observable by design; commons is not yet.** pi-durable's `watchEvents` gives
   turns, streamed text and thinking, tool starts and ends with results, retries, and usage, all
   derived from commits. For commons, the app subclasses `Commons` and copies `stream_async`'s
   chunks into the trace (`ObservedCommons` in [`app/ask_agent.py`](../app/ask_agent.py)).
   chatlas' `on_tool_request` and `on_tool_result` hooks cover tool calls only.
2. **commons' provenance outcome is only HTML.** The outcome reaches a host as a
   `<shiny-aside label="Trusted">` chunk in the stream, so the trace reads it with a regular
   expression. Tool results carry the A/B/C tag in `extra["commons_tag"]`, but there is no
   structured outcome for the answer. **Idea for commons:** put the answer's provenance outcome
   and citation decision on the turn, or in an event, so hosts and traces need not parse HTML.
3. **pi-durable's text deltas are not the whole text.** Partial text is committed on a timer
   (every 100 ms by default). The first chunk of a message arrives in `message_start`, and the
   last chunk only in the final message of `message_end`. A consumer that only joins
   `text_delta` events loses both ends of every message, and short answers have no deltas at
   all. The live trace takes all three into account, and a test guards it. **Idea for
   pi-durable:** document this in the agent events section, or emit deltas that add up to the
   whole text.
4. **Usage for one request is work in pi-durable.** `pi.usage` holds totals for a conversation.
   A follow-up question in the briefing's conversation needs its own spend, so the runner takes
   the difference before and after it. It then freezes that number in the briefing document, so
   that asking again with the same request ID reports the same spend, not zero. **Idea for
   pi-durable:** record usage on each submission.
5. **One price catalog matters.** chatlas' `get_cost()` reports $0.00 for
   `anthropic/claude-sonnet-5.5` on OpenRouter, because its price table does not have it.
   pi-ai's catalog does. The app asks the agent for the catalog price (`pi-briefing price`) and
   prices commons' tokens with it, so the two agents' costs compare. **Idea for commons:** take
   a price table from the host, or use a maintained catalog.
6. **The two agents answer different questions well.** commons queries structured data from
   every saved briefing, so questions that count or rank across days go to trusted measures. The
   pi-durable follow-up has one briefing's raw tool output in its context, so it can talk about
   items the measures do not cover, but only for that briefing. The Ask page puts them side by
   side so the difference is visible. It is a design expectation, not yet a measured result.
7. **commons needs its sandbox when it starts.** `Commons()` builds its Python sandbox at
   construction and raises if the host cannot provide one. It works in a default Docker
   container. If a host forbids user namespaces, the Ask page shows commons' error and the
   pi-durable agent still answers.

## Small frictions found while building

- **Faux tool calls stop the run.** A faux assistant message with a tool call ends the run unless
  it sets `stopReason: "toolUse"`. The examples do this, but nothing warns when it is missing.
- **Interfaces are not `JsonObject`.** Document types must be type aliases. A TypeScript
  `interface` has no index signature, so `defineDoc` rejects it with a long error.
- **The time goes in the user message.** Sections are cached between requests, so a section that
  shows the date would change every day and defeat the prompt cache. The date and window go in
  the user message instead.
- **Faux models finish too fast to stream.** The faux provider answers at once, so no partial
  text is committed and no `text_delta` events arrive. To test streaming, pass
  `fauxProvider({ tokensPerSecond })`.
- **pip drops the execute bit unless the wheel marks a regular file.** pip keeps a file's execute
  permission only when its zip entry's mode includes the regular-file type bits (`S_IFREG`), not
  just `0755`. go-to-wheel, which this project copied, sets only the permission bits. uv keeps
  the bit either way, so local runs worked, but Connect Cloud installs with pip as one user and
  runs the app as another. The app could not restore the bit and failed with `EPERM`.
  `tests/test_wheel_install.py` now installs a built wheel with pip and checks the bit. This is
  worth reporting to go-to-wheel.
- **Size.** The Bun executable is about 96 MB on Linux and 66 MB on macOS. The compressed wheels
  are 24 to 36 MB. That is acceptable for Connect Cloud, but too large to commit to git, so the
  wheels go in GitHub releases.
