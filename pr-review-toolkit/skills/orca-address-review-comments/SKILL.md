---
name: orca-address-review-comments
description: This skill should be used when the user asks to "address Orca review comments", "fix Orca editor comments", "fetch Orca diff comments", or "apply notes left in Orca" and needs local editor/diff or Markdown review notes fetched before applying fixes.
argument-hint: "[worktree-selector]"
allowed-tools: Bash, Read, Edit, Write, Grep, Glob, Skill
metadata:
  version: "0.7.1"
---

# Orca Address Review Comments

Fetch review notes left directly in Orca's editor, diff viewer, or Markdown preview, then pass them as arguments to `address-review-comments`. Keep notes and their delivery state unchanged. A GitHub PR and `gh` authentication are unnecessary.

Use the host's available execution and file tools in Claude Code or Codex. Keep fetching here and code fixing in the shared skill.

## Step 1 — Resolve Orca and Load Its Guide

If an `orca-cli` skill is available in the active session, read and follow it. Otherwise resolve the executable using these rules:

- Use the value of `ORCA_CLI_COMMAND` when set.
- Otherwise use `orca-dev` when the session exposes `ORCA_DEV_REPO_ROOT`.
- Otherwise use `orca-ide` on Linux outside an Orca-managed terminal. Avoid bare `orca` there because it can resolve to the GNOME screen reader.
- Otherwise use `orca`.

Choose once and reuse that executable. In the examples below, substitute it for `ORCA`; do not execute the placeholder literally. Load the version-matched guide:

```text
ORCA skills get orca-cli --json
```

If the executable is missing or fails, report the exact error without silently switching builds. If the guide command is unsupported, use the executable's `--help` for read-only discovery. If Orca is not running, follow the guide's startup recovery and retry. For connection or access failures, report the actual failure; do not treat it as zero comments.

## Step 2 — Select the Target Worktree

If a worktree selector was supplied as an argument, use it:

```text
ORCA worktree show --worktree <selector> --json
```

Otherwise use the checkout enclosing the current directory:

```text
ORCA worktree current --json
```

Do not select a different workspace solely because it has UI focus. If current-worktree detection fails, list registered worktrees and ask for a selector rather than guessing:

```text
ORCA worktree list --json
```

Copy the complete `result.worktree.id`, which includes both the repo ID and checkout path. Retain `path`, `hostId`, and identity metadata for the shared payload. Establish the selected working directory on the correct execution host before applying code changes. If the selected checkout is inaccessible, report that limitation instead of editing another checkout.

## Step 3 — Extract Editor Review Notes

Read `result.worktree.diffComments` from the successful JSON response. Orca exposes the editor review notes in that array; the singular `result.worktree.comment` is the workspace card's status text and is not review feedback. The installed CLI may have no separate comment-fetch command; use the worktree response rather than inventing one.

An absent or empty `diffComments` array in an otherwise valid worktree response means no stored editor notes were returned. Report that outcome and stop. If the response shape is unsupported or incomplete, report the incompatibility and suggest passing Orca's copied review notes to `address-review-comments`.

For each note:

- Preserve the full `body` and `id`.
- Map `filePath` to `path`, `lineNumber` to `line`, and retain `startLine` for ranges. Treat a file-level zero line as an absent line anchor.
- Preserve `selectedText`, `oldPath`, `scope`, `side`, `diffIdentity`, timestamps, and `sentAt` when present. Map the note's `source` (`diff` or `markdown`) to `noteSource` so the payload's source can remain `orca`.
- Keep notes belonging to the selected worktree; report and exclude any whose explicit `worktreeId` identifies a different workspace.
- Keep both delivered and undelivered notes. `sentAt` means a note was sent to an agent, not that its requested fix was completed. Filter to unsent notes only if the user specifically requests that scope.
- Include stale anchors, notes on unchanged code, and general feedback. Preserve diff context so the shared skill can locate the current equivalent code.

Do not add GitHub's pending-review or resolution-phrase filters to local Orca notes. Do not delete notes, mark them sent, or update workspace status during fetching.

## Step 4 — Pass Comments to the Shared Skill

Read [the shared input contract](../address-review-comments/references/comment-input.md). Build one payload with `source: "orca"`, the selected worktree identity and working directory in `context`, normalized comment entries, and any skipped counts. Carry a mode only if already chosen by the user. If filtering leaves no comments, report that result and stop.

Before invoking the fixer, follow **Prepare Detailed Location Context Before Passing Arguments** in that contract. Read the selected worktree's files and attach `sourceAnchor`, `affectedLocations` with exact file paths and current line ranges, and verbatim surrounding `codeContext` to each note. Include other locations named in the note. Preserve the original selected range/text, `scope`, `side`, and `diffIdentity`; staged or branch-diff line numbers can differ from the current working file. Match the selected text or corresponding code block before claiming a current range. Pass explicit location status and reasons for missing or ambiguous matches. Gather context only; leave fixes and final scope decisions to the shared skill.

Invoke `address-review-comments` with that payload as arguments in the same session. In Claude Code, use the Skill tool with `skill: "pr-review-toolkit:address-review-comments"` and serialized payload `args` when available. In Codex, or when no invocation tool exists, read [the shared skill](../address-review-comments/SKILL.md) and follow its workflow using the payload as input. For large payloads, write a temporary JSON file and pass `--comments-file <path>`.

If skills were installed separately and a sibling link is unavailable, locate `address-review-comments` in the active skill catalog and resolve its files from that path.

Use the shared skill's summary, mode selection, code fixes, validation, and outcome report. Do not duplicate that workflow or spawn another agent. Leave Orca note cleanup and committing/pushing to the user.
