# GitHub API Reference — Fetching PR Review Comments

Use read-only queries to retrieve the full comment set before filtering. Leave replies and thread resolution to the user.

## GraphQL — Paginated Review Threads

Use GraphQL because REST review comments do not expose thread resolution or outdated anchors. Save this query to a temporary `query.graphql` file:

```graphql
query GetReviewThreads($owner: String!, $repo: String!, $prNumber: Int!, $cursor: String) {
  repository(owner: $owner, name: $repo) {
    pullRequest(number: $prNumber) {
      headRefOid
      baseRefOid
      reviewThreads(first: 100, after: $cursor) {
        pageInfo { hasNextPage endCursor }
        nodes {
          id
          isResolved
          isOutdated
          path
          line
          startLine
          originalLine
          originalStartLine
          diffSide
          startDiffSide
          subjectType
          comments(first: 50) {
            pageInfo { hasNextPage endCursor }
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
              commit { oid }
              originalCommit { oid }
              url
              author { login }
              createdAt
              updatedAt
              pullRequestReview { state }
            }
          }
        }
      }
    }
  }
}
```

Execute with variables instead of interpolating repository names into GraphQL:

```bash
gh api graphql \
  -f query="$(cat query.graphql)" \
  -f owner="OWNER" \
  -f repo="REPO" \
  -F prNumber=PR_NUMBER
```

Replace placeholders with the detected owner, repository, and integer PR number. On subsequent thread pages, add `-f cursor="END_CURSOR_VALUE"`. Continue until `reviewThreads.pageInfo.hasNextPage` is false, combining all returned thread nodes. Do not interpret a null repository/PR, API errors, or a missing connection as an empty result.

### Paginate Comments Within Each Thread

Thread pagination does not paginate the nested comment connection. For each thread with `comments.pageInfo.hasNextPage: true`, save and run this separate query:

```graphql
query GetThreadComments($threadId: ID!, $cursor: String) {
  node(id: $threadId) {
    ... on PullRequestReviewThread {
      comments(first: 100, after: $cursor) {
        pageInfo { hasNextPage endCursor }
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
          commit { oid }
          originalCommit { oid }
          url
          author { login }
          createdAt
          updatedAt
          pullRequestReview { state }
        }
      }
    }
  }
}
```

```bash
gh api graphql \
  -f query="$(cat thread-comments.graphql)" \
  -f threadId="THREAD_NODE_ID" \
  -f cursor="COMMENT_END_CURSOR"
```

Append these nodes to that thread's first page. Continue using the comment connection's cursor until it is exhausted. Complete this step before checking review states or the last reply; a truncated thread can hide its submitted feedback or resolution reply.

## REST — General PR Comments

General comments use the issue-comments endpoint; retrieve all pages:

```bash
gh api user --jq .login
gh api repos/OWNER/REPO/issues/PR_NUMBER/comments --paginate
```

Preserve `id`, `body`, `user.login`, `created_at`, `updated_at`, and `html_url` when passing general feedback to the shared skill.

Exclude general comments whose `user.login` ends with `[bot]`. Exclude resolution acknowledgements only when authored by the authenticated user and starting with `Fixed:`, `Addressed:`, or `Resolved:`. Do not filter ordinary feedback because it contains a resolution word.

## Inline Thread Filtering

Include a thread when all these conditions hold:

1. `isResolved` is false.
2. At least one comment has `pullRequestReview.state` other than `PENDING`; skip a nonempty thread only when every comment is pending. Retain comments without a review object as non-draft context rather than failing on null metadata.
3. No resolution reply is detected using the rules below.

Count skipped resolved, pending, and acknowledged threads separately. Do not filter inline threads by author; include self-review feedback.

### Resolution Reply Detection

Treat a thread as already handled only when all these conditions hold:

1. It has more than one comment, so a reply exists.
2. The last comment is a different node posted after the first comment.
3. The last comment's body contains one of `fixed`, `done`, `resolved`, `addressed`, `lgtm`, or `updated`, case-insensitively, matching the existing workflow's resolution-phrase heuristic.

Check the complete, chronological comment list. A single comment containing "fixed" is still original feedback and remains active. Do not require different authors; self-review can have one author for both feedback and acknowledgement. Preserve this heuristic's scope: it can match resolution words inside a reply requesting more work, so mention this limitation when the user questions skipped feedback.

### Inclusion Rules

Keep `isOutdated: true` threads and pass that flag to the shared skill. A changed line makes the anchor stale, not the feedback. Also keep feedback on code outside the PR diff and high-level feedback. Preserve the thread's `path`, `line`/`startLine`, `originalLine`/`originalStartLine`, `diffSide`/`startDiffSide`, and `subjectType`, along with each comment's `diffHunk` and commit OIDs, to locate the current equivalent code. These fields are defined in [GitHub's review thread and comment schema](https://docs.github.com/en/graphql/reference/pulls#pullrequestreviewthread).

Use one shared feedback entry per active thread with the complete original body and chronological replies. Preserve comment IDs, author, URL, timestamps, and review state within that context. Keep pending comments labeled as draft context rather than treating them as additional submitted feedback.

## Diff Context

Use `diffHunk` when path and line are insufficient. Lines beginning with `-` were removed, `+` were added, and a space denotes unchanged context. Preserve the hunk as data for the shared skill, especially for moved or removed anchors.

Keep `headRefOid` and `baseRefOid` as PR context and `commit.oid`/`originalCommit.oid` as comment revision metadata. Gather the checkout's current HEAD with `git rev-parse HEAD` as `context.localHeadOid`. Preserve these distinct revisions rather than assuming the reviewed snapshot matches the working tree.

Follow the [shared location-context procedure](../../address-review-comments/references/comment-input.md) before passing arguments. Store the thread's raw coordinates in `sourceAnchor`; attach separately verified working-tree locations and code excerpts in `affectedLocations`. File-level threads can have null line bounds. Deleted-side anchors require matching the old code/hunk to its current equivalent, not copying old line numbers into current locations.
