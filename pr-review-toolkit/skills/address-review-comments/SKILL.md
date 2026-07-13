---
name: Address Review Comments
description: This skill should be used when the user asks to "address review comments", "fix PR comments", "resolve review feedback", "apply code review suggestions", "address PR feedback", "work through review comments", "go through PR reviews", "address my own PR comments", "self-review PR", or wants to systematically process GitHub pull request review comments (including comments left by themselves) and apply the suggested code fixes.
argument-hint: "[pr-number]"
allowed-tools: Bash, Read, Edit, Write, Grep, Glob
version: 0.5.0
---

# Address Review Comments

Systematically fetch all active GitHub PR review comments and apply the suggested code fixes. Thread resolution and committing/pushing changes are left for the user to perform manually.

**Designed for iterative self-review:** leave comments on your own PR, run this skill to address them, resolve the threads, then repeat — each iteration only picks up comments that were added since the last run or that were not yet resolved.

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

Use the GraphQL API to retrieve review threads with their resolution and outdated status. The REST API does not expose `isResolved` or `isOutdated` — GraphQL is required here.

```bash
gh api graphql -f query='
{
  repository(owner: "OWNER", name: "REPO") {
    pullRequest(number: PR_NUMBER) {
      reviewThreads(first: 100) {
        nodes {
          id
          isResolved
          isOutdated
          comments(first: 50) {
            nodes {
              id
              databaseId
              body
              path
              line
              originalLine
              author { login }
              createdAt
            }
          }
        }
      }
    }
  }
}'
```

**Core inclusion principle:** every unresolved thread whose feedback is still relevant is actionable — no matter where it is anchored or how it is phrased. The only questions are (a) has it already been resolved, and (b) *where* does the fix go. Whether a comment is on-diff or off-diff, specific or general, never decides *whether* it is addressed.

**Keep a thread when BOTH are true:**
- `isResolved` is `false`
- The thread does NOT already have a resolution reply (see detection rules below)

**Never drop a thread for any of these reasons — each one is still actionable:**
- **`isOutdated` is `true`.** The comment is anchored to a line/hunk that changed under it, so the *anchor* is stale — but the *feedback* is not. A stale anchor only changes where the fix lands (Step 6), never whether the comment is handled.
- **The comment targets code the PR did not change.** A review left on existing, unchanged code (or on a file untouched by this diff) is still real feedback about the current state of the codebase. Address it.
- **The comment is general or high-level** rather than a specific line edit — e.g. "this skill should support any input and fall back when a parameter is missing." General direction is actionable: treat it as the umbrella that more specific comments fall under, and apply it. If specific comments are concrete instances of a general one, handling the specifics satisfies the general comment too — note that in the summary.

Track each thread's `isOutdated` value so Step 5 can label it and Step 6 can locate the best matching code position. `isOutdated: true` affects *where* the fix goes, never *whether* the comment is addressed.

**Resolution reply detection — requires ALL of:**
1. The thread has **more than one comment** (at least one reply exists). Single-comment threads are always active — a review comment like "This should be fixed by extracting a helper" contains the word "fixed" but is clearly the original feedback, not a resolution.
2. The last comment was posted **after** the first comment (not the same comment node)
3. The last comment body contains resolution phrases: `fixed`, `done`, `resolved`, `addressed`, `lgtm`, `updated` (case-insensitive, whole-word or partial match)

Comments authored by the PR author or the repo owner are fully included — this skill is designed for self-review workflows where all comments come from the same person.

For PRs with more than 100 review threads, paginate using a cursor. See `references/gh-api-guide.md` for the paginated query.

## Step 4 — Fetch Active General PR Comments

General comments (not attached to a specific line) use the issues REST endpoint:

```bash
gh api repos/OWNER/REPO/issues/PR_NUMBER/comments --paginate
```

General comments arrive as a flat chronological list — there is no threading. Filter out:
- Comments where `user.login` ends with `[bot]` (CI tools, automation)
- Comments that were posted by this skill in a previous iteration — they start with `Fixed:`, `Addressed:`, or `Resolved:` and are authored by the authenticated user running `gh`

Do **not** filter out comments based on their content alone (e.g. a comment containing the word "resolved" is still actionable — it's describing the expected fix, not acknowledging one). Only skip a comment if a subsequent reply in the flat list looks like it was posted by this skill (prefix match on `Fixed:` / `Addressed:`).

Comments from the repo owner or PR author are always actionable — include them. General observations about the codebase's overall behavior or about code this PR did not touch are actionable too — they describe the current state and are addressed the same as any other comment.

## Step 5 — Present Summary Before Acting

Before changing any code, show the user what was found:

```
Found 3 active review thread(s) (1 outdated) and 2 general comment(s):

Inline threads:
  1. [src/auth/middleware.ts:47] "Extract this into a helper — it's used in three places" (alice)
  2. [tests/order.test.ts:92] "This test doesn't cover the null input case" (bob)  [OUTDATED — line moved/removed]
  3. [README.md:12] "Broken link in the installation section" (carol)

General comments:
  1. "Error messages throughout are too generic, users can't tell what went wrong" (alice)
  2. "Missing migration script for the new column" (bob)
```

Label any thread whose `isOutdated` is `true` with `[OUTDATED — line moved/removed]` so the user understands the context when reviewing fixes.

## Step 5b — Recommend an Addressing Mode and Ask the User

After presenting the summary, evaluate the scope of work and give a concrete recommendation before asking the user how to proceed.

### Evaluate Complexity

Score the work across these signals:

| Signal | Points |
|---|---|
| Total comment count > 5 | +2 |
| Changes span 3+ distinct files | +2 |
| Any comment implies refactoring / extraction / architectural change | +2 |
| Any comment is ambiguous or requires interpretation | +1 |
| All comments are in 1–2 files and self-contained | −2 |

**Score ≥ 3 → recommend Plan. Score < 3 → recommend Address on the fly.**

### Write the Recommendation

Present a short, opinionated recommendation (2–4 sentences) explaining what you observed and why one mode fits better. Examples:

- _"5 comments, all in one file, each fix is a single targeted change — addressing on the fly is the fastest path here."_
- _"8 comments across 4 files, two of which involve extracting shared helpers. A plan lets you see the full picture before touching any code and avoids conflicting edits across files."_

### Ask the User to Choose

```
How would you like to proceed?
  [1] Address on the fly — apply each fix now, then resolve/reply to the thread
  [2] Create a plan first — produce a structured implementation plan before touching any code
```

Wait for the user's choice before continuing.

### If the User Chooses "Create a plan first"

Check whether `planning:make` is available by scanning the skills list in the current session (it appears as `planning:make` when the [planning plugin](https://github.com/umputun/cc-thingz) is installed).

**If `planning:make` is available:**

Follow the `planning:make` skill instructions with this task description, which carries the full context gathered in Steps 1–4 so the skill can skip its discovery phase and start directly with the interactive questions:

> Address PR #NUMBER review comments — X inline thread(s) (N outdated) and Y general comment(s) across the following files: [list]. Key changes needed: [one-line summary per comment]. **After applying all fixes, list the changed files so the user can review, commit, and push manually.**

`planning:make` will ask the user what to do after the plan is created (review, implement, done). **Stop here — do not proceed to Step 6.**

**If `planning:make` is not available:**

Tell the user:

> The `planning` plugin is not installed. Install it to get full structured planning support:
>
> See: https://github.com/umputun/cc-thingz
>
> Once installed, re-run `/pr-review-toolkit:address-review-comments` and choose "Create a plan first" again.

Then ask the user whether to fall back to addressing on the fly now, or stop and install the plugin first. **Do not proceed to Step 6 automatically.**

### If the User Chooses "Address on the fly"

Continue immediately to Step 6.

## Step 6 — Apply Code Fixes

Process each active comment in order. For each:

1. Read the comment body carefully to understand the requested change
2. For inline comments: read the file at the indicated `path` around the indicated `line` for context
3. Determine the minimal correct fix
4. Apply the fix using `Edit` (for targeted changes) or `Write` (for new files)
5. Record a one-sentence description of what was changed for the final summary in Step 7

**For outdated inline threads** (`isOutdated: true`) **or comments on code the PR did not change**: the `line` anchor may not point at the relevant code in the current file. Read the full `path` file to find the best matching location (same function, same logic block, or the nearest equivalent) and apply the fix there. If no matching location exists (code was deleted), note in the summary that the code was removed and the comment no longer applies.

**For general or high-level comments** (no specific line, or feedback about overall behavior): identify every place in the codebase the comment implies a change and apply it across all of them. When more specific comments are concrete instances of the general one, fixing the specifics covers the general comment — record that linkage in the summary rather than skipping it.

Group related comments that touch the same file or function — handle them together to avoid conflicting edits.

When a comment is ambiguous, apply the most reasonable interpretation. When a comment is a question rather than a requested change (e.g. "Is this intentional?"), note the question in the summary and leave the code unchanged so the user can decide.

When a comment cannot be addressed in code at all (out-of-scope, architectural question, requires discussion), note in the summary why no code change was made.

## Step 7 — Report Changes

After all code changes are applied, show a final summary:

```
Done. Applied fixes for 5 comment(s):

  ✓ [src/auth/middleware.ts:47]  Extracted shared logic into authHelper()
  ✓ [tests/order.test.ts:92]     Added null-input test case
  ✓ [README.md:12]               Fixed installation link
  ✓ General: Error messages      Improved messages in 4 handlers
  ✓ General: Migration script    Added migrations/add_user_column.sql

Files changed: src/auth/middleware.ts, src/auth/helpers.ts, tests/order.test.ts,
               README.md, migrations/add_user_column.sql
```

Thread resolution (marking threads as resolved on GitHub) and committing/pushing the branch are left for the user to perform manually.

## Additional Resources

- **`references/gh-api-guide.md`** — Full paginated GraphQL queries, REST endpoint details, resolution detection algorithm, and example API responses
