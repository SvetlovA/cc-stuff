---
name: explain
description: This skill should be used when the user runs "/explain" or "$explain", or asks to "explain this", "explain the changes", "explain this PR", "explain this branch", "explain this file", "explain this plan", "explain the project", "walk me through", "present the logic", "how does X work" or "where is X", with or without a file path, directory, PR number or URL, branch, commit, plan document or question. It produces a plain-language explanation with ASCII diagrams, a Note under every diagram, tables, and clickable file:line links to the code.
argument-hint: "[file | dir | PR# or URL | branch | commit | changes | project | question] [focus]"
allowed-tools: Bash, Read, Grep, Glob, AskUserQuestion
metadata:
  version: "0.1.0"
---

# Explain

Explain code, changes, plans or a whole project so the reader understands it on the first read. Use short plain sentences, ASCII diagrams with a **Note:** under each one, tables for "what goes where", and links to the exact lines. Only read. Never edit, commit, push or publish anything.

Use the host's own tools: Bash/Read/Grep/Glob in Claude Code, or the equivalent shell and file-reading tools in Codex. Treat the arguments as plain text. Do not depend on a template variable.

## Step 1 — Resolve the Target

Split the argument into `<target> [focus]`. The focus is the rest of the text, such as "retry logic" or "only the reducer". It narrows the explanation, but does not change the mode.

| Argument | Mode | Example |
|---|---|---|
| none | from the conversation | `/explain` |
| existing file | file (a plan if it is a plan or spec document) | `/explain docs/plans/2026-plan.md` |
| directory | module | `/explain src/Services/CoreAIService` |
| `#123`, `123`, PR URL | pull request | `/explain #42` |
| `pr`, `branch`, a branch name | branch against the default branch | `/explain branch` |
| `changes`, `diff`, `uncommitted`, `staged` | working tree | `/explain changes` |
| commit SHA or range `a..b` | commits | `/explain a2fca26` |
| `project`, `repo`, `.` | whole project | `/explain project` |
| anything else | question or topic | `/explain where is the map step` |

**Paths** can be partial, or start with `/` and mean the repository root (`/plans/2026-plan`). Try these in order:

1. The path as given.
2. The path relative to the repository root, without the leading `/`.
3. A search of tracked files for the last fragment: `git ls-files | grep -i '<fragment>'`, also trying `.md` and common source extensions.

If one file matches, use it. If several match, ask which one. If none match, treat the argument as a question. When a word is both a keyword and an existing path, use the path.

**No argument:** look at the conversation. Explain its subject only when it is obvious, such as a PR, file, plan or feature that was just discussed, or code that was just changed. Then say in one line what was picked: "Explaining: the digest map-reduce branch."

If nothing is obvious, ask one question about what to explain. Build the options from the repository state:

- uncommitted changes, if any
- the current branch against the default branch, if it is ahead
- a file mentioned recently
- the whole project

Use the host's question tool when there is one (AskUserQuestion in Claude Code). Otherwise ask in plain text and stop until the user answers. Never guess.

## Step 2 — Gather the Material

Read the real material before writing. Never explain from file names, commit messages or memory. The commands for each mode, and how to handle a PR that is not checked out, are in **`references/targets.md`**.

- **Learn the vocabulary.** Read `README`, `AGENTS.md`/`CLAUDE.md` and any architecture doc for the area. Use the project's own names for things.
- **Diffs:** get the stat first. Sort the files into core logic, wiring, configuration, tests and docs. Read core logic files in full, not only their hunks: the hunk shows what changed, and the file shows why it matters.
- **Pick what matters:** the entry point, the main flow, the rules that keep it correct (validation, invariants, limits), the settings that change behaviour, and anything that fails in a surprising way. Skip renames, formatting, boilerplate and plain wiring. Read tests only when they show the intended behaviour more clearly than the code.
- **Line numbers:** get the exact line for every link, with `grep -n` or a numbered read. Never guess a line number.
- **Open items:** note markers such as `UNVERIFIED`, `TODO` or `HACK`, unchecked plan items, and anything the code admits it does not handle.

Read directly. Spawn subagents only when the user asks for them.

## Step 3 — Write the Explanation

Read **`references/style.md`** before writing every time. It holds the full style guide and the diagram templates. **`references/example.md`** shows a finished answer at the expected quality. **`references/targets.md`** gives the section outline for each mode.

The rules that matter most:

1. **Core idea first.** A `#` title, then one or two plain sentences: what this is and what changed or why it exists.
2. **Big picture before detail.** One flow diagram of the whole thing, then one `##` section per important part.
3. **A Note under every diagram.** Directly below each code-block diagram, write `**Note:**` and one or two plain sentences about what the diagram shows.
4. **Tables instead of bullet walls.** Use tables for comparisons, before/after, "what goes where", settings and rules. Keep a bullet list to about five items.
5. **Link every claim about code** to its line: `[File.cs:42](path/from/cwd/File.cs:42)`. A table cell with a link is a good way to say where something lives.
6. **Short sentences, plain words.** Explain each term the first time it appears, and give the code name after the plain description.
7. **One concrete example** with real values (numbers, a sample input, a worked calculation) wherever a rule is abstract.
8. **Be honest.** Say what is open, untested or only best effort. Say where a name misleads, for example "there is no class called Map".
9. **Only the important parts.** Match the length to the scope: a question gets one or two sections, and a PR or a project gets more. No preamble and no closing recap.
10. **Write in the language of the user's request.**

## Step 4 — Follow-up Questions

Answer follow-ups such as "where is X?" or "explain every setting" in the same style: core idea, diagram with Note, table, links. Re-read the code a follow-up depends on, and do not rely on the earlier answer. Keep each follow-up as focused as the question.

## Host Notes

| Topic | Claude Code | Codex |
|---|---|---|
| Invoke | `/code-explainer:explain [args]`, or `/explain` when installed as a personal skill | `$explain [args]` |
| Ask the user | AskUserQuestion | a plain-text question, then stop |
| PR data | `gh` CLI | `gh` CLI |

Link paths are relative to the current working directory, which is normally the repository root. Both terminals make the `path:line` text clickable even where markdown links are not rendered. Draw diagrams with box-drawing characters and arrows, in a code block with no language tag, at most about 100 columns wide so they do not wrap.

If `gh` is missing or not logged in, say so. Offer to explain the local branch instead, or ask for the branch name.

## Additional Resources

- **`references/style.md`** — the full style guide: structure, diagram toolkit with templates, tables, links, wording, and what to avoid.
- **`references/targets.md`** — the gather commands and the section outline for each mode.
- **`references/example.md`** — a complete answer in the expected style.
