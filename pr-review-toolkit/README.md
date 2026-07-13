# pr-review-toolkit

A Claude Code plugin for working through GitHub pull request reviews. Fetch active comments, apply code fixes, and resolve threads — all from your terminal.

## Skills

### `/pr-review-toolkit:address-review-comments`

Systematically addresses all active review comments on a pull request:

1. **Detects the current PR** (or lets you select one from a list)
2. **Fetches all active comments** — inline review threads and general PR comments, filtering out only already-resolved threads, already-replied-to threads, and pending (unsubmitted) draft reviews. Every other unresolved comment is treated as actionable: outdated threads, comments on code this PR did not change, and general/high-level comments are all **included** (outdated threads are labeled so the fix can be applied at the nearest equivalent location)
3. **Presents a summary** of all active threads and evaluates scope
4. **Recommends a mode** and asks how to proceed:
   - **Address on the fly** — apply each fix immediately, resolve/reply to threads, then prompt to commit (recommended for ≤ 5 comments in 1–2 files)
   - **Create a plan first** — delegates to `/planning:make` to produce a structured implementation plan; the plan ends with two dedicated tasks: resolve/reply to all threads, then `git push` to update the PR (recommended for complex or cross-file changes)
5. **Resolves or replies** — marks threads resolved via GitHub's GraphQL API, or posts a reply with a resolution description when resolution isn't possible

## Installation

### Install from GitHub

```bash
cc plugin install https://github.com/SvetlovA/cc-stuff/tree/master/pr-review-toolkit
```

### Install locally (development)

```bash
# Clone the repo
git clone https://github.com/SvetlovA/cc-stuff.git

# Run Claude Code with the plugin loaded
cc --plugin-dir ./cc-stuff/pr-review-toolkit
```

## Prerequisites

- [GitHub CLI (`gh`)](https://cli.github.com/) — installed and authenticated (`gh auth login`)
- A git repository with a GitHub remote
- Pull request open on GitHub

## Optional dependency

Plan mode uses the **planning plugin** from [umputun/cc-thingz](https://github.com/umputun/cc-thingz) to produce a structured implementation plan via `/planning:make`. Without it, the skill prompts you to install it or fall back to on-the-fly mode. The plugin works fully without this dependency.

## Usage

### Auto-detect current PR

Run from inside a git repo with an open PR on the current branch:

```
/pr-review-toolkit:address-review-comments
```

### Specify a PR number

```
/pr-review-toolkit:address-review-comments 123
```

### No PR on current branch

If no PR is detected, the skill lists open PRs and prompts you to select one.

## What counts as an "active" comment?

The skill includes every **submitted, unresolved** thread and general PR comment whose feedback is still relevant.

The skill skips only:
- Threads marked as **resolved** in GitHub's UI (`isResolved: true`)
- Threads where the **last reply** already indicates resolution (contains "fixed", "done", "resolved", "LGTM", etc.)
- Threads where every comment belongs to a **pending (unsubmitted) review** — draft comments not yet visible to other reviewers
- Comments by **bots** (e.g. CI tools, Dependabot)

Everything else is treated as actionable feedback — including:

- **Outdated threads** (`isOutdated: true`). The comment is anchored to a line that changed under it, but the feedback is still valid. The skill labels it in the summary and finds the best matching location in the file to apply the fix. If no equivalent location exists (code was deleted), it posts a reply explaining the outcome.
- **Comments on code this PR did not change** — feedback about existing or unchanged code is still real and gets addressed.
- **General or high-level comments** (e.g. "support any input and fall back when a parameter is missing"). These are applied across every place they imply a change, and act as the umbrella that more specific comments fall under.

A comment is never skipped just because its anchor is stale, its target wasn't part of the diff, or it's phrased generally.

## Permissions

Resolving threads via API requires **write access** to the repository. Without it, the skill falls back to posting a reply with a resolution description instead.

## License

MIT
