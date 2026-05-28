# pr-review-toolkit

A Claude Code plugin for working through GitHub pull request reviews. Fetch active comments, apply code fixes, and resolve threads — all from your terminal.

## Skills

### `/pr-review-toolkit:address-review-comments`

Systematically addresses all active review comments on a pull request:

1. **Detects the current PR** (or lets you select one from a list)
2. **Fetches all active comments** — inline review threads and general PR comments, filtering out already-resolved and already-replied-to threads; outdated threads (comments on lines that no longer exist in the current diff) are **included** and labeled so the fix can be applied at the nearest equivalent location
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

The skill skips:
- Threads marked as **resolved** in GitHub's UI (`isResolved: true`)
- Threads where the **last reply** already indicates resolution (contains "fixed", "done", "resolved", "LGTM", etc.)
- Comments by **bots** (e.g. CI tools, Dependabot)

**Outdated threads are included.** When a comment was left on a line that no longer exists in the current diff (`isOutdated: true`), the skill labels it in the summary and finds the best matching location in the file to apply the fix. If no equivalent location exists (code was deleted), it posts a reply explaining the outcome.

Everything else is treated as actionable feedback.

## Permissions

Resolving threads via API requires **write access** to the repository. Without it, the skill falls back to posting a reply with a resolution description instead.

## License

MIT
