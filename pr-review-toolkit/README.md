# pr-review-toolkit

Three skills for addressing code review feedback in **Claude Code and Codex**. Supply comments directly, fetch GitHub PR feedback, or fetch notes left in Orca's editor. Both fetching skills pass their comments to the same fixing workflow.

## Skills

| Skill | Input | Behavior |
|---|---|---|
| `address-review-comments` | Review text, structured comments, or `--comments-file <path>` | Applies fixes for supplied feedback; performs no comment fetching |
| `gh-address-review-comments` | Optional PR number | Fetches active GitHub inline threads and general PR comments, then calls the shared skill |
| `orca-address-review-comments` | Optional Orca worktree selector | Fetches editor/diff and Markdown review notes from Orca, then calls the shared skill |

The shared workflow summarizes the feedback, recommends addressing on the fly or creating a plan, applies requested fixes, runs relevant checks, and reports outcomes by comment. A mode already chosen by the user is retained. Outdated anchors, unchanged code, general feedback, and self-review comments remain actionable.

Both wrappers pass complete feedback and replies together with original review anchors, affected file paths and line ranges, and exact surrounding code excerpts. Moved lines and related locations are identified separately from the original anchor; unavailable or ambiguous context is explained. The fixer rereads the current files before editing. See the [payload contract and example](skills/address-review-comments/references/comment-input.md).

Thread resolution, replies, Orca note cleanup, commits, and pushing remain manual.

## Installation

### Claude Code

```bash
cc plugin install https://github.com/SvetlovA/cc-stuff/tree/master/pr-review-toolkit
```

For local development:

```bash
git clone https://github.com/SvetlovA/cc-stuff.git
cc --plugin-dir ./cc-stuff/pr-review-toolkit
```

### Codex

The plugin includes a `.codex-plugin/plugin.json` manifest pointing to the same `skills/` directory. For standalone local skill installation, clone the repository and copy all three skill folders together:

```bash
git clone https://github.com/SvetlovA/cc-stuff.git
mkdir -p ~/.agents/skills
cp -R ./cc-stuff/pr-review-toolkit/skills/address-review-comments ~/.agents/skills/
cp -R ./cc-stuff/pr-review-toolkit/skills/gh-address-review-comments ~/.agents/skills/
cp -R ./cc-stuff/pr-review-toolkit/skills/orca-address-review-comments ~/.agents/skills/
```

For repository-scoped installation, use the target repository's `.agents/skills/` instead. Codex discovers these folders and supports `$skill-name` invocation; see the [official skill documentation](https://learn.chatgpt.com/docs/build-skills). The manifests share the same instructions; no separate Codex implementation is needed.

## Usage

### Supply comments directly

Claude Code:

```text
/pr-review-toolkit:address-review-comments src/auth.ts:47 — Extract the duplicated validation into a helper. Address on the fly.
/pr-review-toolkit:address-review-comments --comments-file ./review-comments.json
```

Codex:

```text
$address-review-comments src/auth.ts:47 — Extract the duplicated validation into a helper. Address on the fly.
$address-review-comments --comments-file ./review-comments.json
```

Accept plain text, numbered lists, JSON payloads, and Orca's copied `File:`, `Line:`/`Lines:`, and `User comment:` blocks. Only the comment body is required; location and source metadata are optional. See the [shared input contract](skills/address-review-comments/references/comment-input.md).

### Fetch GitHub PR feedback

Claude Code:

```text
/pr-review-toolkit:gh-address-review-comments
/pr-review-toolkit:gh-address-review-comments 123
```

Codex:

```text
$gh-address-review-comments
$gh-address-review-comments 123
```

Without a number, detect the PR for the current branch. If detection fails, list open PRs and ask for a selection. Require `gh` installed and authenticated, and a checkout with a GitHub remote.

Include submitted, unresolved inline threads and general PR comments. Skip resolved threads, threads whose final reply signals resolution, pending-only draft threads, bot general comments, and the authenticated user's general resolution acknowledgements. Fetch all thread and comment pages. Include outdated anchors and comments from the PR author or repository owner. The last-reply filter retains the previous skill's resolution-word heuristic.

**Migration:** use `gh-address-review-comments` for the old PR-number/no-argument fetching behavior. `address-review-comments` now requires supplied feedback.

### Fetch Orca editor notes

Claude Code:

```text
/pr-review-toolkit:orca-address-review-comments
/pr-review-toolkit:orca-address-review-comments branch:feature/login
```

Codex:

```text
$orca-address-review-comments
$orca-address-review-comments branch:feature/login
```

Require a reachable Orca runtime and its CLI. Without a selector, use the worktree enclosing the current directory. Read `diffComments` from the worktree response, preserving file/line ranges, selected text, diff scope, and note IDs. The workspace card's status comment is separate from review notes.

Keep sent notes because `sentAt` records delivery, not resolution. Leave their state unchanged. No GitHub PR or `gh` authentication is needed. If the installed Orca version cannot return the expected note data, pass copied Orca notes to `address-review-comments` directly.

## Optional Planning Support

Use `planning:make` from the [planning plugin](https://github.com/umputun/cc-thingz) when it appears in the active session. Otherwise create the requested structured plan directly in the current host. All three skills work without this optional dependency or a host-specific skill invocation tool.

## License

MIT
