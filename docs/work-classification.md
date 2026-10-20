# Classifying work: feature, maintenance, security

This is the specification for the question "What percentage of the work we are doing is
maintenance or security versus feature work?" It records the decisions made so far and the ones
still open. Code that classifies work must follow it, and changes to the rules start here.

## Unit of work

The unit is a **merged pull request**. Each answer reports two shares side by side:

- **By count:** the share of merged pull requests.
- **By size:** the share of lines changed (lines added plus lines deleted).

The two can differ a lot. A small dependency bump and a large feature count the same by count.
Showing both lets the reader see that.

**Open:** which files to leave out of the size. Lock files and generated files can be very large
and say nothing about effort. The proposed default leaves out `*.lock`, `*-lock.yaml`,
`*-lock.json`, `uv.lock`, generated model catalogs, and `dist/`.

## Categories

Every merged pull request gets exactly one category: **feature**, **maintenance**, **security**,
or **unclassified**. Rules decide it, in order; the first rule that matches wins. Each pull
request records the ID of the rule that classified it, so an answer can say why.

### Security (checked first)

A pull request is **security** when any of these is true:

- Its author is a dependency bot (Dependabot, Renovate, Snyk) and it has a security label.
- Its title, body, or labels mention a CVE (`CVE-2026-12345`), a GitHub advisory (`GHSA-…`),
  Snyk, or the words "vulnerability", "vulnerable", or "security".

### By conventional-commit type

The type is the first word of the title, before `:` or `(scope):`. Matching ignores case, and
also accepts a branch-style title such as `Fix/copilot request size overflow`.

| Type | Category |
|---|---|
| `feat`, `perf` | feature |
| `ci`, `chore`, `build`, `deps`, `refactor`, `test`, `revert`, `docs` | maintenance |
| `fix` | see below |

**Open:** `docs`. It is under maintenance above as a default, but documentation for a new feature
may be feature work, in the same way as fixes below.

### Fixes

A fix is **maintenance** when it responds to an issue a customer reported. It is **feature** when
it polishes a feature that is still being prepared for release. A fix with neither signal is
**unclassified**. It is not guessed.

**Open:** the signals for each. Proposed:

- **Customer reported:** the pull request closes an issue whose author is not a member, owner,
  or collaborator of the repository (GitHub's `author_association`). Question: is "customer"
  anyone outside the team, or only some people or organizations?
- **Polishing an unreleased feature:** the fix is merged after the repository's latest release,
  and touches the same scope (`fix(py)` after `feat(py)`) or the same files as a `feat` pull
  request merged since that release. Question: which releases count? Some repositories release
  per package (`py-v*` and `r-v*` tags in commons).

### Labels

When the title gives no type, labels can: `bug` behaves like `fix`, `documentation` like `docs`,
and `tests` like `test`. Which labels a repository uses varies; the rules list them per
repository.

### Unclassified

Anything no rule matches is **unclassified**. Answers always show the unclassified share. They do
not hide it or spread it over the other categories.

## Model guesses (untrusted)

For unclassified pull requests, a model may also guess a category and give a one-line reason.
Guesses are stored apart from the rule-based category, and are never used by a trusted
calculation. An answer that uses them must go through commons' ad hoc analysis path, so it is
marked **Untrusted**. The trusted answer and the guess-assisted answer can appear together, each
with its own marker.

## Burning down the unclassified share

The rules grow over time through a review loop:

1. The app lists unclassified pull requests, with the model's guess and reason when there is
   one.
2. A person spots a pattern (for example, titles that start with `Fix/`) and adds a rule to the
   rules file. Each rule has an ID, a description, and where it came from, such as a review
   date.
3. Every rule has test cases: real titles it must classify, and titles it must not.
4. The unclassified share is tracked over time, so the burn-down is visible.

A model guess can suggest a rule. Only a person makes it one.

## Default scope

Without other instructions, an answer covers all configured repositories and the last 30 days of
merged pull requests.
