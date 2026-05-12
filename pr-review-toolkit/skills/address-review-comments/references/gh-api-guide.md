# GitHub API Reference — PR Review Comments

Complete reference for fetching PR review data, detecting active comments, and resolving threads.

## GraphQL — Full Paginated Review Threads Query

Use this when a PR may have more than 100 review threads:

```graphql
query GetReviewThreads($owner: String!, $repo: String!, $prNumber: Int!, $cursor: String) {
  repository(owner: $owner, name: $repo) {
    pullRequest(number: $prNumber) {
      reviewThreads(first: 100, after: $cursor) {
        pageInfo {
          hasNextPage
          endCursor
        }
        nodes {
          id
          isResolved
          isOutdated
          resolvedBy { login }
          comments(first: 50) {
            nodes {
              id
              databaseId
              body
              path
              line
              originalLine
              startLine
              originalStartLine
              diffHunk
              author { login }
              createdAt
              updatedAt
              reactions(first: 5) {
                nodes { content }
              }
            }
          }
        }
      }
    }
  }
}
```

Execute with variables:

```bash
gh api graphql \
  -f query="$(cat query.graphql)" \
  -F owner="OWNER" \
  -F repo="REPO" \
  -F prNumber=PR_NUMBER
```

For subsequent pages, add `-F cursor="END_CURSOR_VALUE"` from the previous response's `pageInfo.endCursor`.

## GraphQL — Resolve a Review Thread

Requires write access (contributor or maintainer on the repo):

```bash
gh api graphql -f query='
mutation ResolveThread($threadId: ID!) {
  resolveReviewThread(input: {threadId: $threadId}) {
    thread {
      id
      isResolved
      resolvedBy { login }
    }
  }
}' -F threadId="THREAD_NODE_ID"
```

The `THREAD_NODE_ID` is the `id` field from the `reviewThreads` query (a base64 node ID, e.g. `PRRT_kwDO...`).

**Error responses:**

```json
{
  "errors": [{
    "type": "FORBIDDEN",
    "message": "Resource not accessible by integration"
  }]
}
```

When this error appears, fall back to posting a reply comment instead.

## REST — General PR Comments (Issue Comments)

These are comments on the PR itself (not on a diff line):

```bash
# List all comments
gh api repos/OWNER/REPO/issues/PR_NUMBER/comments \
  --paginate \
  --jq '.[] | {id: .id, user: .user.login, body: .body, created_at: .created_at}'

# Post a reply (top-level, since issue comments have no threading)
gh api repos/OWNER/REPO/issues/PR_NUMBER/comments \
  --method POST \
  -f body="Addressed: RESOLUTION"
```

Note: Issue comments do not support threading or resolution. When replying to acknowledge a specific comment, quote the original briefly:

```
> Original comment text (truncated)

Addressed: <description of what was done>
```

## REST — Inline Review Comments

When you need the database ID for replying without GraphQL:

```bash
gh api repos/OWNER/REPO/pulls/PR_NUMBER/comments \
  --paginate \
  --jq '.[] | {id: .id, path: .path, line: .line, body: .body, in_reply_to_id: .in_reply_to_id}'
```

Reply to a thread using the original comment's `id`:

```bash
gh api repos/OWNER/REPO/pulls/PR_NUMBER/comments \
  --method POST \
  -f body="Fixed: RESOLUTION" \
  -F in_reply_to=ORIGINAL_COMMENT_ID
```

## Resolution Detection Algorithm

A thread is considered already handled when **all three** conditions are true:

1. **The thread has more than one comment** — `comments.nodes.length > 1`. Single-comment threads are never treated as resolved, even if the comment body contains words like "fixed" or "resolved" (those are describing the required fix, not acknowledging one).
2. **The last comment was posted after the first** — compare `createdAt` timestamps to confirm a reply actually exists (in practice this is guaranteed when `length > 1`, but worth confirming).
3. **The last comment body contains resolution signals** — check case-insensitively:

```python
RESOLUTION_SIGNALS = [
    "fixed", "done", "resolved", "addressed", "lgtm",
    "updated", "changed", "implemented", "completed",
    "wont fix", "won't fix", "by design", "not applicable",
    "no action needed"
]

def thread_is_already_handled(comments: list) -> bool:
    if len(comments) <= 1:
        return False  # Single comment = original review, always actionable
    last = comments[-1]["body"].lower().strip()
    return any(signal in last for signal in RESOLUTION_SIGNALS)
```

**Self-review note:** In the iterative self-review workflow, the same person authors both the original comment and any replies. Do not require `last_author != first_author` as a condition — the repo owner may write a comment and later reply to it themselves after fixing it. The presence of a reply with resolution signals is sufficient.

## Example GraphQL Response Structure

```json
{
  "data": {
    "repository": {
      "pullRequest": {
        "reviewThreads": {
          "pageInfo": {
            "hasNextPage": false,
            "endCursor": "Y3Vyc29yOnYyOpHOABCD=="
          },
          "nodes": [
            {
              "id": "PRRT_kwDOBp5T2M5BvXyz",
              "isResolved": false,
              "isOutdated": false,
              "resolvedBy": null,
              "comments": {
                "nodes": [
                  {
                    "id": "PRRC_kwDOBp5T2M5BvXyz",
                    "databaseId": 1234567,
                    "body": "This function is doing too much — consider splitting it.",
                    "path": "src/services/orderService.ts",
                    "line": 84,
                    "originalLine": 84,
                    "diffHunk": "@@ -81,7 +81,12 @@\n ...",
                    "author": { "login": "alice" },
                    "createdAt": "2024-01-15T10:30:00Z"
                  }
                ]
              }
            },
            {
              "id": "PRRT_kwDOBp5T2M5CwYab",
              "isResolved": true,
              "isOutdated": false,
              "resolvedBy": { "login": "bob" },
              "comments": { "nodes": [...] }
            }
          ]
        }
      }
    }
  }
}
```

The second thread (`isResolved: true`) is skipped in the filtering step.

## Handling the `diffHunk` Field

The `diffHunk` field provides the surrounding diff context for an inline comment. Use it when the `path` + `line` alone is not enough to understand the comment:

```
@@ -81,7 +81,12 @@
 function processOrder(order) {
   const items = order.items;
-  const total = items.reduce((acc, i) => acc + i.price, 0);
+  const total = items.reduce((acc, i) => acc + (i.price * i.quantity), 0);
+  const tax = calculateTax(total, order.region);
+  const shipping = calculateShipping(order.weight, order.destination);
+  const grandTotal = total + tax + shipping;
```

Lines starting with `-` were removed, `+` were added, and ` ` (space) are unchanged context.

## Checking Repository Permissions

To determine if GraphQL resolve mutation will succeed before attempting:

```bash
gh api repos/OWNER/REPO --jq '.permissions | {push, maintain, admin}'
```

If `push` or `maintain` or `admin` is `true`, the mutation should succeed. If all are `false`, skip the mutation and go straight to reply-based resolution.

## Paginating General Comments

General issue comments also need pagination for busy PRs:

```bash
gh api repos/OWNER/REPO/issues/PR_NUMBER/comments \
  --paginate \
  --jq '[.[] | select(.user.type != "Bot") | {id: .id, user: .user.login, body: .body}]'
```

The `--paginate` flag handles GitHub's link-header pagination automatically. The `jq` filter excludes bot accounts.

## Formatting Resolution Replies

Keep resolution replies short and informative:

**Good:**
```
Fixed: Extracted `calculateOrderTotals()` into `src/utils/orderHelpers.ts`.
```

**Good (for a non-code change):**
```
Addressed: Updated the installation link in README.md line 12 to point to the new docs URL.
```

**Good (for a cannot-fix scenario):**
```
Acknowledged: This is intentional — the function handles three lifecycle stages and splitting
it would require passing shared state through props. Left a TODO comment explaining this.
```

**Avoid:**
- Generic replies like "Done" or "Fixed" with no description
- Long explanations (keep under 3 sentences)
- Promising future work unless it's tracked in a ticket
