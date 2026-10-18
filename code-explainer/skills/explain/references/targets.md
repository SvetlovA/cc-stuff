# Targets: Gather Commands and Outlines

For each mode: how to collect the material, then the outline to write. Use the outline as a default, not a form. Drop a section that has nothing to say.

## Default Branch

Several modes compare against the default branch. Resolve it once:

```bash
git symbolic-ref --short refs/remotes/origin/HEAD 2>/dev/null | sed 's#^origin/##'
# fallback: whichever exists of main, master
git show-ref --verify --quiet refs/heads/main && echo main || echo master
```

## Branch (`pr`, `branch`, a branch name)

```bash
base=$(git merge-base HEAD "origin/$default" 2>/dev/null || git merge-base HEAD "$default")
git log --oneline "$base"..HEAD
git diff --stat "$base"..HEAD
git diff "$base"..HEAD -- <core files>
git status --short          # mention uncommitted work if any
```

For a branch name other than the current one, use `<branch>` in place of `HEAD`, and read its files with `git show <branch>:<path>`. Line links then point to the content on that branch, so say that the branch is not checked out.

Check the repository's plans folder (for example `docs/plans/`) for a plan that matches the branch. It usually explains the intent better than the commits do.

## Pull Request (`#123`, `123`, PR URL)

```bash
gh pr view <n> --json number,title,body,url,baseRefName,headRefName,headRefOid,files,commits
gh pr diff <n>
git rev-parse --abbrev-ref HEAD   # is the PR branch checked out?
```

- If the PR's head branch is checked out and matches `headRefOid`, read the files from disk and use local links.
- Otherwise read files with `gh api repos/{owner}/{repo}/contents/<path>?ref=<headRefOid>`, or use the diff hunks. Link GitHub blob URLs at `headRefOid`.
- Use the PR body for intent, but check every claim it makes against the code.
- If `gh` fails, report the exact error. Offer the local-branch mode instead.

## Working Tree (`changes`, `diff`, `uncommitted`, `staged`)

```bash
git status --short
git diff HEAD --stat        # `staged` → git diff --cached --stat
git diff HEAD
```

Read untracked files directly, since `git diff` does not show them.

## Commits (SHA or range)

```bash
git show --stat <sha>        # single commit
git show <sha>
git log --oneline <a>..<b>   # range
git diff --stat <a>..<b>
```

## Outline: Changes (branch, PR, working tree, commits)

1. `#` title and the core idea: what this change does, in one or two sentences.
2. **Before / after** table, if behaviour changed.
3. **How the parts talk:** a flow diagram of the new or changed path, with a Note.
4. **One section per important part:** a diagram or table, links, the rules that keep it correct.
5. **Settings**, if the change adds or changes configuration: key | value | what it does.
6. **Smaller changes that matter:** change | why | where.
7. **Still open:** what is unverified, has a TODO or is best effort.

## File

Read the whole file. For a long file, first list its outline with `grep -n` on declarations, then read the important members in full. Find the callers with `git grep -n '<SymbolName>'`.

Outline:

1. What the file is for and who uses it (link the callers).
2. A diagram of its main flow, with a Note.
3. Its main members: member | job | link.
4. Rules, limits and edge cases that are not obvious.

## Plan or Spec Document

A plan is a Markdown file under a plans or specs folder, or a file whose headings look like goals, tasks or steps. Read it whole. Then check it against the repository: do the files it names exist, which tasks are checked off, and did a later commit move it to `completed/`?

Outline:

1. **Problem and goal**, in plain words.
2. **Design:** a before / after diagram of the system, with a Note.
3. **Tasks:** # | what | main files | status (done / open / not started, checked against the repository).
4. **Key decisions:** decision | why | rejected alternative.
5. **Risks and open questions** from the plan, plus any found while checking it.

## Directory or Module

```bash
git ls-files <dir> | head -200
git ls-files <dir> | sed 's#/[^/]*$##' | sort | uniq -c | sort -rn | head -30
```

Find the entry points (hosts, `Program`/`main`, controllers, handlers, exported index files), the public interfaces, and the configuration.

Outline:

1. Its role in the system: what comes in and what goes out.
2. A diagram of how its parts connect, with a Note.
3. What goes where: part | job | link.
4. Settings and external dependencies.
5. Where to start reading.

## Whole Project (`project`, `repo`, `.`)

```bash
ls; cat README* AGENTS.md CLAUDE.md 2>/dev/null | head -300
git ls-files | cut -d/ -f1-2 | sort | uniq -c | sort -rn | head -40
ls docs docs/architecture 2>/dev/null
```

Also read the manifests (`package.json`, `*.sln`/`*.csproj`, `go.mod`, `pyproject.toml`, `Cargo.toml`), the deployment files (`docker-compose*.yaml`, CI workflows) and the architecture docs.

Outline:

1. What the project is, in one paragraph.
2. **Architecture diagram:** services or layers and how they talk, with a Note.
3. **Components:** component | role | link.
4. **One or two main flows**, as sequence diagrams with Notes.
5. **How to run, test and deploy:** task | command | where.
6. **Where to start reading:** three to five files, in order.

## Question or Topic

```bash
git grep -n -i '<term>'          # try synonyms and the code's naming
git grep -n '<SymbolName>'       # follow callers and callees
```

Follow the chain from the entry point to the answer. Read the files involved; do not answer from search hits alone.

Outline:

1. **The direct answer** in one or two sentences, with the main link.
2. A diagram that places the answer in the flow, with a Note.
3. Where each piece lives: piece | link.
4. A table of what the name suggests versus what the code does, when the question comes from a mismatch.
