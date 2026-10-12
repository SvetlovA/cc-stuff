---
name: gh-address-review-comments
description: This skill should be used when the user asks to "fetch GitHub review comments", "fix PR comments", "address GitHub PR feedback", "go through PR reviews", "address my own PR comments", or "self-review PR" and needs active GitHub review feedback fetched before applying fixes.
argument-hint: "[pr-number]"
allowed-tools: Bash, Read, Edit, Write, Grep, Glob, Skill
metadata:
  version: "0.7.0"
---

# GitHub Address Review Comments

Fetch active GitHub PR review feedback, then pass it as arguments to `address-review-comments` to apply fixes. Include self-review comments. Leave thread resolution, replies, commits, and pushing to the user.

Run shell commands through the host's available execution tool (Bash in Claude Code or shell/exec in Codex). Use the working checkout associated with the target PR; if its head branch differs from the current checkout, establish the intended checkout before applying fixes rather than switching branches automatically.

## Prerequisites

Verify before starting:
- `gh` CLI is installed and authenticated: `gh auth status`
- The working directory is a git repository with a GitHub remote

If `gh` is not authenticated, tell the user to run `gh auth login` and stop.

## Step 1 — Determine the Target PR

If a PR number was passed as an argument, use it directly and skip detection.

Otherwise, attempt to detect the PR for the current branch:

```bash
gh pr view --json number,title,headRefName,url 2>/dev/null
```

If that returns a PR, use its `number`. If it fails or returns nothing, list open PRs and ask the user to choose:

```bash
gh pr list --json number,title,author,headRefName,createdAt \
  --template '{{range .}}#{{.number}}\t{{.title}}\t({{.author.login}}) — {{.headRefName}}\n{{end}}'
```

Present the list and wait for the user to type a PR number before continuing.

## Step 2 — Get Repository Coordinates

Extract the owner and repo name for API calls:

```bash
gh repo view --json owner,name -q '"owner=\(.owner.login)\nrepo=\(.name)"'
```

Use these as `OWNER` and `REPO` throughout the rest of the workflow.

## Step 3 — Fetch Active Inline Review Threads

Use the GraphQL API to retrieve review threads. The REST API does not expose `isResolved` or `isOutdated` — GraphQL is required here. Each comment node must include `pullRequestReview { state }` to detect pending (unsubmitted) reviews. Read [the API guide](references/gh-api-guide.md) before fetching; retrieve every page of both review threads and comments within each thread before filtering.

**Core inclusion principle:** every unresolved, submitted thread whose feedback is still relevant is actionable — no matter where it is anchored or how it is phrased. The only questions are (a) has it already been resolved, and (b) *where* does the fix go. Whether a comment is on-diff or off-diff, specific or general, never decides *whether* it is addressed.

**Keep a thread when ALL of these are true:**
- `isResolved` is `false`
- At least one comment has `pullRequestReview.state` other than `PENDING` (the thread comes from a *submitted* review)
- The thread does NOT already have a resolution reply (see detection rules below)

**Never drop a thread for any of these reasons — each one is still actionable:**
- **`isOutdated` is `true`.** The comment is anchored to a line/hunk that changed under it, so the *anchor* is stale — but the *feedback* is not. A stale anchor only changes where the fix lands (the shared skill’s fix step), never whether the comment is handled.
- **The comment targets code the PR did not change.** A review left on existing, unchanged code (or on a file untouched by this diff) is still real feedback about the current state of the codebase. Address it.
- **The comment is general or high-level** rather than a specific line edit — e.g. "this skill should support any input and fall back when a parameter is missing." General direction is actionable: treat it as the umbrella that more specific comments fall under, and apply it. If specific comments are concrete instances of a general one, handling the specifics satisfies the general comment too — note that in the summary.

**Skip a thread only when every comment's `pullRequestReview.state` is `PENDING`** — those are unsubmitted draft comments not yet visible to other reviewers, so they are not yet final feedback. (Submitted states: `COMMENTED`, `APPROVED`, `CHANGES_REQUESTED`, `DISMISSED`.)

Track each thread's `isOutdated` value so the shared skill’s summary step can label it and the shared skill’s fix step can locate the best matching code position. `isOutdated: true` affects *where* the fix goes, never *whether* the comment is addressed.

**Resolution reply detection — requires ALL of:**
1. The thread has **more than one comment** (at least one reply exists). Single-comment threads are always active — a review comment like "This should be fixed by extracting a helper" contains the word "fixed" but is clearly the original feedback, not a resolution.
2. The last comment was posted **after** the first comment (not the same comment node)
3. The last comment body contains resolution phrases: `fixed`, `done`, `resolved`, `addressed`, `lgtm`, `updated` (case-insensitive, whole-word or partial match)

Comments authored by the PR author or the repo owner are fully included — this skill is designed for self-review workflows where all comments come from the same person.

Paginate review threads and each thread’s comments using their independent cursors. Use the complete queries in [the API guide](references/gh-api-guide.md); inspect `pageInfo` and continue until both connections are exhausted.

## Step 4 — Fetch Active General PR Comments

Determine the authenticated login for acknowledgement filtering:

```bash
gh api user --jq .login
```

General comments (not attached to a specific line) use the issues REST endpoint:

```bash
gh api repos/OWNER/REPO/issues/PR_NUMBER/comments --paginate
```

General comments arrive as a flat chronological list — there is no threading. Filter out:
- Comments where `user.login` ends with `[bot]` (CI tools, automation)
- Comments that are themselves resolution acknowledgements — they start with `Fixed:`, `Addressed:`, or `Resolved:` and are authored by the authenticated user running `gh`

Do **not** filter out comments based on their content alone (e.g. a comment containing the word "resolved" is still actionable — it's describing the expected fix, not acknowledging one).

Comments from the repo owner or PR author are always actionable — include them. General observations about the codebase's overall behavior or about code this PR did not touch are actionable too — they describe the current state and are addressed the same as any other comment.

## Step 5 — Pass Comments to the Shared Skill

Read [the shared input contract](../address-review-comments/references/comment-input.md). Build one payload with `source: "github"`, repository/PR identity, the working checkout, active comments, and skipped counts by reason.

For each active inline thread, pass one entry containing the complete original feedback and chronological replies, plus thread/comment IDs, author, URL if available, full current/original line ranges, diff sides, review commit OIDs, raw diff hunk, and `isOutdated`. Preserve submitted feedback even when a thread also has pending comments; label draft context separately. For general comments, pass the complete body, ID, author, timestamp, and URL. Do not replace bodies with summaries.

Before invoking the fixer, follow **Prepare Detailed Location Context Before Passing Arguments** in the shared input contract. Read the relevant checkout files and attach `sourceAnchor`, `affectedLocations` with exact file paths and current line ranges, and verbatim surrounding `codeContext` for each comment. Include related locations identified in the body or replies. Preserve GitHub's `diffSide`/`startDiffSide` as anchor `side`/`startSide`; a `LEFT` anchor belongs to the old side and must be mapped to current code rather than treated as a working-tree line. Keep review/PR revisions distinct from the local checkout revision. Pass explicit location status and reasons when mapping is incomplete. Perform only context gathering here; leave fixes and final scope decisions to the shared skill.

If no active comments remain, report that outcome and the skipped counts, then stop without editing files. On authentication/API errors or incomplete pagination, report the failure rather than treating it as an empty result.

Invoke `address-review-comments` with the payload as arguments in the same session. In Claude Code, use the Skill tool with `skill: "pr-review-toolkit:address-review-comments"` and `args` containing the serialized payload when available. In Codex, or when no skill invocation tool exists, read [the shared skill](../address-review-comments/SKILL.md) and follow its workflow with the payload as input. For large payloads, write a temporary JSON file and pass `--comments-file <path>`; keep the file available until the shared workflow finishes. If skills were installed separately and a sibling link is unavailable, locate `address-review-comments` in the active skill catalog and resolve its files from that path. Do not spawn another agent or copy the fixing workflow into this wrapper.

## Additional Resources

- [GitHub API guide](references/gh-api-guide.md) — pagination, fetching endpoints, and filtering rules. Read before executing the review-thread query so both threads and their comments are fetched completely.
