# code-explainer

One skill, `explain`, for **Claude Code and Codex**. It explains a file, a plan, your uncommitted changes, a branch, a pull request, a module, the whole project, or a question about the code. The answer is built to be understood on the first read:

- the core idea first, in one or two plain sentences
- ASCII diagrams of flows, loops and timelines, each followed by a short **Note:** in plain words
- tables for "what goes where", before/after, settings and rules, instead of long bullet lists
- clickable `file:line` links to the exact code behind every claim
- only the important parts, with open or unverified items called out honestly

The skill only reads. It never edits, commits or publishes anything.

## Usage

| You type | It explains |
|---|---|
| `/explain` | the subject of the current conversation, or asks you what to explain if that is not obvious |
| `/explain docs/plans/2026-plan.md` | a plan: goal, design, tasks with real status, decisions, risks |
| `/explain /plans/2026-plan` | the same; partial paths and a leading `/` resolve against the repository |
| `/explain src/Services/Foo.cs retry logic` | one file, narrowed to a focus |
| `/explain src/Services/CoreAIService` | a module or directory |
| `/explain changes` | uncommitted changes (`staged` for the index only) |
| `/explain branch` | the current branch against the default branch |
| `/explain #42` or a PR URL | a GitHub pull request (needs an authenticated `gh`) |
| `/explain a2fca26` or `/explain a..b` | a commit or a range |
| `/explain project` | the whole repository |
| `/explain where is the retry policy?` | a question, answered directly first |

Text after the target narrows the focus. Follow-up questions in the same session get answers in the same style.

**Claude Code:** `/code-explainer:explain [args]`. For the bare `/explain`, install it as a personal skill (below).

**Codex:** `$explain [args]`

## Installation

### Claude Code

```bash
claude plugin marketplace add SvetlovA/cc-stuff
claude plugin install code-explainer@SvetlovA-cc-stuff
```

For a bare `/explain` command, copy the skill into your personal skills:

```bash
git clone https://github.com/SvetlovA/cc-stuff.git
mkdir -p ~/.claude/skills
cp -R ./cc-stuff/code-explainer/skills/explain ~/.claude/skills/
```

For local development: `claude --plugin-dir ./cc-stuff/code-explainer`.

### Codex

```bash
codex plugin marketplace add SvetlovA/cc-stuff
codex plugin add code-explainer@SvetlovA-cc-stuff
```

For a standalone install, copy `skills/explain` into `~/.agents/skills/` (personal) or the repository's `.agents/skills/` (project). Restart the session after installing.

## Layout

```
skills/explain/
├── SKILL.md              # resolve the target → gather → write → follow-ups
└── references/
    ├── style.md          # the style guide and diagram templates
    ├── targets.md        # gather commands and section outlines for each mode
    └── example.md        # a finished answer at the expected quality
```

## Requirements

`git`. For pull requests, an authenticated [`gh`](https://cli.github.com/). Without it, the skill offers to explain the local branch instead.
