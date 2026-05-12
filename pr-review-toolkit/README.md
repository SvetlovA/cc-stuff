# pr-review-toolkit

A Claude Code plugin for working through GitHub pull request reviews. Fetch active comments, apply code fixes, and resolve threads — all from your terminal.

## Skills

### `/pr-review-toolkit:address-review-comments`

Systematically addresses all active review comments on a pull request:

1. **Detects the current PR** (or lets you select one from a list)
2. **Fetches all active comments** — inline review threads and general PR comments, filtering out already-resolved, outdated, and already-replied-to threads
3. **Applies code fixes** for each comment using Claude's full coding capabilities
4. **Resolves or replies** — marks threads resolved via GitHub's GraphQL API, or posts a reply with a resolution description when resolution isn't possible
5. **Prompts for action** — commit the changes immediately or leave them for manual review

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
- Comments on **outdated** diff positions (`isOutdated: true`) — the code has moved
- Threads where the **last reply** already indicates resolution (contains "fixed", "done", "resolved", "LGTM", etc.)
- Comments by **bots** (e.g. CI tools, Dependabot)

Everything else is treated as actionable feedback.

## Permissions

Resolving threads via API requires **write access** to the repository. Without it, the skill falls back to posting a reply with a resolution description instead.

## License

MIT
