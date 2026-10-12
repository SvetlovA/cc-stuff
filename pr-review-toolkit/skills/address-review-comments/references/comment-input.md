# Review Comment Input and Wrapper Contract

Use this contract to pass fetched feedback into `address-review-comments` in Claude Code or Codex. Accept ordinary text for direct use; reserve structured fields for retaining source context. Do not require callers to fill in unavailable metadata.

## Invocation

- Pass comment text or a serialized JSON payload as the shared skill's arguments.
- For large collections, write a temporary UTF-8 JSON file and pass `--comments-file <path>`.
- Keep relative paths relative to the selected working checkout. Keep temporary files available until processing finishes.
- If the host has no skill invocation tool, read the shared `SKILL.md` and follow it in the same session using the prepared payload. Do not launch another agent or rely on a Claude-only slash command from Codex.
- Resolve shared files relative to the loaded skill's location. When skills are installed separately rather than as a plugin, find the shared skill's path in the active skill catalog if the sibling path is unavailable.

## Example Payload

```json
{
  "source": "github",
  "context": {
    "repository": "owner/repo",
    "prNumber": 123,
    "url": "https://github.com/owner/repo/pull/123",
    "workingDirectory": "<selected checkout>"
  },
  "mode": "on-the-fly",
  "comments": [
    {
      "id": "thread-or-comment-id",
      "kind": "inline",
      "body": "Extract this into a helper; it is used in three places.",
      "path": "src/auth/middleware.ts",
      "line": 44,
      "startLine": 42,
      "originalLine": 42,
      "isOutdated": true,
      "author": "alice",
      "sourceAnchor": {
        "path": "src/auth/middleware.ts",
        "startLine": 42,
        "line": 44,
        "originalStartLine": 40,
        "originalLine": 42,
        "side": "RIGHT",
        "startSide": "RIGHT",
        "subjectType": "LINE"
      },
      "diffHunk": "@@ -40,3 +42,3 @@\n const token = request.headers.authorization;\n-if (!token) throw new Error('Missing token');\n+if (!token) throw new Error('Authorization header is required');\n return validateToken(token);",
      "locationStatus": "located",
      "affectedLocations": [
        {
          "path": "src/auth/middleware.ts",
          "startLine": 47,
          "endLine": 49,
          "symbol": "authenticateRequest",
          "relationship": "review-target",
          "basis": "Matched the validation block from the review hunk in the current file.",
          "codeContext": {
            "contentSource": "working-tree",
            "startLine": 46,
            "endLine": 50,
            "code": "function authenticateRequest(request) {\n  const token = request.headers.authorization;\n  if (!token) throw new Error('Authorization header is required');\n  return validateToken(token);\n}"
          }
        }
      ],
      "replies": [
        {"body": "Include the CLI caller too.", "author": "bob"}
      ]
    },
    {
      "id": "general-comment-id",
      "kind": "general",
      "body": "Error messages are too generic. Include the failed operation.",
      "locationStatus": "unlocated",
      "affectedLocations": [],
      "locationReason": "Repository-wide feedback without a named operation; specific handlers remain to be identified."
    }
  ],
  "skipped": {"resolved": 2, "resolutionReply": 1, "pending": 1}
}
```

## Field Semantics

Require a nonempty `body` for each feedback item. Allow an array of comment objects without the enclosing payload for direct input. Preserve unknown source fields when they provide useful context.

Use `source` such as `provided`, `github`, or `orca`. Use `context` for the repository/PR or Orca worktree ID, execution host, and working directory. A PR is optional; Orca feedback does not require GitHub.

Use `kind: "inline"` for anchored feedback and `kind: "general"` for unanchored feedback. Preserve `id`, `url`, `author`, timestamps, `path`, `line`, `startLine`, `originalLine`, `originalStartLine`, `diffHunk`, `selectedText`, and `isOutdated` when available. Treat a null or missing line as an absent anchor. Keep thread replies in chronological order with complete bodies and their own available location metadata, including additional files mentioned in replies.

Allow `mode: "on-the-fly"` or `mode: "plan"` only when the user has already chosen it. Omit the field otherwise so the shared skill owns the recommendation and choice.

Use `skipped` for counts by source-specific reason. Apply source filtering in the wrapper, not again in the shared skill. Never filter outdated anchors, self-review, unchanged code, or general feedback merely because of those properties.

For Orca, preserve `scope`, `side`, `oldPath`, `diffIdentity`, `sentAt`, and the original note `source` (as `noteSource`) when supplied. `sentAt` records delivery to an agent, not resolution; retain delivered notes unless the user explicitly limits the request to unsent notes.

## Prepare Detailed Location Context Before Passing Arguments

Require both fetching wrappers to perform this preparation before invoking the fixer. Keep direct text input supported; let the shared skill enrich it when no wrapper supplied context.

1. **Retain the original anchor.** Put the source's path, start/end line, original start/end line, diff side/start side, file-level vs line-level target, scope, and revision in `sourceAnchor` when available. Preserve the raw `diffHunk` and selected text separately. Keep existing top-level `path`/line fields as source metadata; do not overwrite them with a guessed current location. Source `line` is the end of an inclusive range; default its start to the same line for a single-line anchor.
2. **Locate the current code.** Read the selected checkout, matching the anchor against the hunk, selected text, and surrounding function. For moved code, renamed files, deleted-side comments, or staged/branch snapshots, search for the equivalent current block instead of assuming the source line numbers match the working tree. Keep the original anchor even when no current equivalent exists.
3. **Attach affected locations.** Populate `affectedLocations` with one entry per verified target or clearly related location. Include the repository-relative `path`, inclusive `startLine`/`endLine` when known, enclosing `symbol` when identifiable, `relationship` (`review-target` or `related`), and a short `basis` explaining the evidence. Include locations explicitly mentioned in the body or replies, even across files. Mark inferred related locations in the basis; do not present them as reviewer-supplied anchors. For file-level feedback, retain the path and omit target line bounds rather than inventing a range.
4. **Include actual code.** Attach a `codeContext` block to each readable location, with exact unmodified text and its own inclusive start/end line numbers. Include the affected range and normally about five lines on either side; expand to the enclosing function when necessary to understand the change. Keep the snippet's range separate from the narrower affected range. Label `contentSource` as `working-tree`, `index`, or `review-commit`; attach a revision OID when applicable. Never label historical or diff-relative lines as current working-tree lines. Carry the working checkout's `localHeadOid` in the payload context when available; working-tree snippets can contain uncommitted changes and must still be rechecked before editing.
5. **Handle general or unavailable locations explicitly.** For general feedback, search named files, symbols, or behaviors and include relevant locations that can be established without implementing fixes. Use `locationStatus: "located"`, `"partially-located"`, or `"unlocated"`, with a `locationReason` for missing/ambiguous information. Keep all unresolved candidates and explain ambiguity rather than picking an arbitrary match. If no location is established, pass an empty array and the reason (for example, missing file, deleted code, or repository-wide feedback requiring further investigation). Keep the comment actionable; missing context is not a filtering reason.

Pass the entire enriched collection as arguments or via the comments file. Keep full bodies, replies, anchors, hunks, and code excerpts available to the fixer; a display summary or IDs alone are insufficient. Limit excerpts to relevant code without truncating the underlying feedback. Use the comments-file route when preserving all context makes inline arguments too large.
