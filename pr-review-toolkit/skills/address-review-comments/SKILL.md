---
name: Address Review Comments
description: This skill should be used when the user asks to "address review comments", "fix PR comments", "resolve review feedback", "apply code review suggestions", "address PR feedback", "work through review comments", "go through PR reviews", "address my own PR comments", "self-review PR", or wants to systematically process GitHub pull request review comments (including comments left by themselves), apply the suggested code fixes, and mark threads as resolved.
argument-hint: "[pr-number]"
allowed-tools: Bash, Read, Edit, Write, Grep, Glob
version: 0.1.0
---

# Address Review Comments

Systematically fetch all active GitHub PR review comments, apply the suggested code fixes, then resolve or reply to each comment. After completing all fixes, prompt the user to commit or leave changes for manual review.

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

**Keep only threads where:**
- `isResolved` is `false`
- `isOutdated` is `false`
- The thread does NOT already have a resolution reply

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

Comments from the repo owner or PR author are always actionable — include them.

## Step 5 — Present Summary Before Acting

Before changing any code, show the user what was found:

```
Found 3 active review thread(s) and 2 general comment(s):

Inline threads:
  1. [src/auth/middleware.ts:47] "Extract this into a helper — it's used in three places" (alice)
  2. [tests/order.test.ts:92] "This test doesn't cover the null input case" (bob)
  3. [README.md:12] "Broken link in the installation section" (carol)

General comments:
  1. "Error messages throughout are too generic, users can't tell what went wrong" (alice)
  2. "Missing migration script for the new column" (bob)

Applying fixes...
```

## Step 6 — Apply Code Fixes

Process each active comment in order. For each:

1. Read the comment body carefully to understand the requested change
2. For inline comments: read the file at the indicated `path` around the indicated `line` for context
3. Determine the minimal correct fix
4. Apply the fix using `Edit` (for targeted changes) or `Write` (for new files)
5. Record a one-sentence description of what was changed for use in Step 7

Group related comments that touch the same file or function — handle them together to avoid conflicting edits.

When a comment is ambiguous, apply the most reasonable interpretation. When a comment is a question rather than a requested change (e.g. "Is this intentional?"), prepare a text reply instead of a code change.

When a comment cannot be addressed in code at all (out-of-scope, architectural question, requires discussion), prepare a reply explaining why it was not addressed in code.

## Step 7 — Resolve or Reply to Each Comment

After all code changes are applied:

**For inline review threads — attempt GraphQL resolution first:**

```bash
gh api graphql -f query='
mutation {
  resolveReviewThread(input: {threadId: "THREAD_NODE_ID"}) {
    thread { isResolved }
  }
}'
```

If this succeeds, the thread is marked resolved in GitHub's UI.

**If GraphQL resolution fails** (insufficient permissions or the comment requires acknowledgment):

Reply to the thread using the `databaseId` of the first comment in the thread:

```bash
gh api repos/OWNER/REPO/pulls/PR_NUMBER/comments \
  --method POST \
  -f body="Fixed: RESOLUTION_DESCRIPTION" \
  -F in_reply_to=COMMENT_DATABASE_ID
```

**For general PR comments** (no resolve mechanism — always reply):

```bash
gh api repos/OWNER/REPO/issues/PR_NUMBER/comments \
  --method POST \
  -f body="Addressed: RESOLUTION_DESCRIPTION"
```

## Step 8 — Prompt for Commit

After all fixes and resolutions are complete, show a final summary:

```
Done. Applied fixes for 5 comment(s):

  ✓ [src/auth/middleware.ts:47]  Extracted shared logic into authHelper()  — resolved
  ✓ [tests/order.test.ts:92]     Added null-input test case               — resolved
  ✓ [README.md:12]               Fixed installation link                  — resolved
  ✓ General: Error messages      Improved messages in 4 handlers          — replied
  ✓ General: Migration script    Added migrations/add_user_column.sql     — replied

Files changed: src/auth/middleware.ts, src/auth/helpers.ts, tests/order.test.ts,
               README.md, migrations/add_user_column.sql

What would you like to do?
  [1] Commit changes
  [2] Finish without committing — leave for manual review
```

Wait for the user's response.

**If the user chooses to commit:**

Stage only the modified/created files (do not use `git add -A` blindly — list specific changed files):

```bash
git add <file1> <file2> ...
git commit -m "Address PR #NUMBER review comments

- <one-line summary of the most significant change>
- <other key changes if notable>

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
```

Remind the user to run `git push` to update the PR branch, but do not push automatically.

**If the user chooses manual review:**

Leave the working tree as-is and report completion.

## Additional Resources

- **`references/gh-api-guide.md`** — Full paginated GraphQL queries, REST endpoint details, resolution detection algorithm, and example API responses
