---
name: partner
description: This skill should be used when the user asks to "create a partner", "spawn a partner", "/partner", "start a partner agent", "add a peer agent", "get a second opinion from another model", "debate this with codex", "argue this with gemini", "have another AI review this with me", or wants another AI agent running in its own terminal tab to challenge decisions. Use it also to hand over the write baton, list agents, or stop them. Critically — once any partner is active (a `.partner/roster.json` with a running entry exists in the repo), use this skill on EVERY subsequent question and decision in the session to run the debate protocol before answering, not just when the user names it.
argument-hint: "[provider] [model] [effort] | list | baton <id> | stop [id]"
allowed-tools: Bash, Read, Write, Edit, Grep, Glob, AskUserQuestion
version: 0.2.0
---

# Partner

Run several AI agents — Claude, Codex, Gemini, or any CLI — as equal partners on one repository, each in its own terminal tab, debating every question through a shared markdown transcript before anyone acts.

A partner is not a subagent. It is a full interactive session with its own model and its own opinion, and it can tell you that you are wrong. The point is friction: two models that disagree surface assumptions one model alone glides past.

Every agent is an ordinary interactive session, so the user can walk into any tab and type at that agent directly. Between those interruptions, each agent watches the transcript and answers the others on its own.

All commands go through one script:

```bash
P="${CLAUDE_PLUGIN_ROOT}/skills/partner/scripts/partner.py"
```

After `init` a short wrapper also exists at `.partner/p.cmd` (Windows) or `.partner/p.sh` (macOS/Linux). That is what the partners themselves use.

## The write baton

There is no privileged agent. This session registers itself in the roster as an ordinary participant — `p1` by default — exactly like the ones it spawns, and `list` marks which one is you.

Every agent *can* edit files: they run with permission prompting relaxed, so the user is not answering dialogs in three tabs at once. What decides who actually edits is the **baton**, a single value in `.partner/roster.json`.

**The rule: only the agent the user just gave an instruction to may write.** The baton follows the user's attention around the tabs. Nothing outside a tab can observe where they are typing, so whichever agent receives the instruction is the one that has to say so.

**When the user gives you an instruction, claim the baton before doing anything else:**

```bash
python "$P" claim
```

Then do the work. Skipping this means a partner still believes it holds the baton and may edit the same files you are editing.

The rule cuts both ways, and the second half is the one that is easy to get wrong:

- An instruction from **the user, in this session** → `claim`, then act.
- A message from **another agent** via `read` → do **not** claim, and do **not** edit. A partner saying "you should change X" is a suggestion, not the user's instruction. Argue it, and if the group agrees, the change is still only yours to make if you hold the baton.

`read` and `wait` print the current holder on every call, because that is exactly when it matters — believe that line over your memory of it, since it may have moved while you were thinking. To hand the baton over deliberately rather than by being addressed:

```bash
python "$P" baton --to p2
```

Because partners run unprompted, this is a rule they keep rather than a wall they hit. The reason is worth more than the rule: two agents editing the same files concurrently produce conflicts neither can see, and the user loses work.

## Starting a partner

### Step 1 — Settle the configuration

First register yourself, so the roster describes every participant accurately rather than guessing at yours:

```bash
python "$P" init --me-provider claude --me-model <this session's model id>
```

This takes `p1` and costs nothing if it has already run. Spawned agents continue from `p2`.

Four settings define a new partner: **provider**, **model**, **effort**, and **auto** level. Take whatever the user supplied and only ask about the rest.

Check what is actually installed before offering options — recommending an uninstalled CLI wastes a round trip:

```bash
python "$P" providers
```

If anything is still unset, ask with `AskUserQuestion`, presenting installed providers first. Sensible defaults when the user says "just pick": a provider *different from this session's own model*, because an agent running the same model as you tends to agree with you, and medium or high effort.

**Auto levels** control how often a partner stops to ask permission. It sits in its own tab, where a prompt is easy to miss and stalls the debate:

| `--auto` | Behaviour | Use when |
|----------|-----------|----------|
| `ask` | the CLI's normal prompting | the user wants to approve everything |
| `edits` *(default)* | auto-accepts file edits, still sandboxed | normal work |
| `full` | no prompts, no sandbox | throwaway repos, containers |

For a CLI not in the registry, use `--provider custom` with a command template:

```bash
python "$P" spawn --provider custom --model gpt-4o \
  --cmd 'aider --model {model} --yes --message {prompt}'
```

`{prompt}`, `{model}`, `{effort}` and `{auto}` are substituted; a template without `{prompt}` gets the starting prompt appended last. Put the CLI's own "stop asking me" flag in the template — `--auto` cannot be applied to a CLI the registry does not know.

### Step 2 — Brief the partner on what it is joining

A partner can be spawned at **any** point — before work starts, or twenty exchanges into a hard problem. Mid-work is the normal case, and it is where briefing matters most: a partner that does not know what was already settled will reopen it, confidently.

Each partner gets its own briefing at `.partner/<id>/handoff.md`, so spawning a third never disturbs what the second was told. Three things go into it:

| What | Who provides it |
|------|-----------------|
| Branch, recent commits, uncommitted changes, diffstat | **Automatic** — collected at spawn |
| The debate so far, if one is underway | **Automatic** — last 20 transcript messages |
| What is being built, what is decided, what is open | **You**, via `--context` |

The automatic half means a hurried spawn still produces a useful partner. The half you write is what stops it re-litigating: state which decisions are closed and *why*, not a narrative of what was typed.

```bash
python "$P" spawn --provider codex --model gpt-5-codex --effort high \
  --context "$(cat <<'EOF'
Working on the auth rewrite in src/auth/.
Decided: opaque session tokens, Redis-backed. Do not reopen — JWT revocation
was the blocker.
Open: whether refresh tokens rotate on every use or only near expiry.
Constraint: cannot break the existing /v1/login contract.
EOF
)"
```

Pass `--context <path>` to reuse a file. Run `python "$P" snapshot` to preview the repo state that will be captured.

Spawn as many partners as useful. When adding one to an argument in progress, say in `--context` what you want *from it specifically*, or it will restate what the others already said.

### Step 3 — Confirm the tab opened

`spawn` picks the best available terminal automatically (see `references/terminals.md`). If none can be opened, it prints the exact command for the user to run in a tab they open themselves — relay that command rather than treating the spawn as failed.

The tab runs the provider's normal interface, started on a briefing that tells it to loop: block on `wait` until someone addresses it, reply, repeat. So the user can read the debate as it happens, interrupt at any moment, and type at that agent directly — it answers them, then returns to the loop.

## The debate protocol

Once a partner is running, consult it on **every question and every decision** — that is the point of having one. At the start of a turn, check who is here:

```bash
python "$P" list
```

If any partner is `running`, do not answer the user directly. Run this loop instead:

**0. Claim the baton.** The user just gave *you* the instruction, so the write permission is yours: `python "$P" claim`. It is a no-op if you already hold it, and it tells the partners to stop editing.

**1. State a position first.** Form your own answer before asking. A partner given a blank question anchors on nothing; one given a concrete claim has something to attack. Include your reasoning and what you are unsure about.

**2. Put it to the partners.**

```bash
python "$P" send --to @all --wait 240 --text "..."
```

`--from` defaults to your own id. `--wait` blocks until they reply and prints the replies. Partners answer when they next return from `wait`, so allow more time than a single model round would take. Without `--wait`, `send` returns immediately and you collect replies later with `read`.

**3. Take the disagreement seriously.** A partner that objects has usually noticed something. Check the claim against the code before accepting or rejecting it — do not concede to be agreeable, and do not dismiss to defend your first answer. Where it is right, say so plainly and change course.

**4. Rebut or converge.** If you still disagree after checking, say why and send it back. Two or three exchanges is normally enough. Real deadlocks are informative, not failures: when partners still disagree after a genuine exchange, the decision is a judgement call and belongs to the user. Present both positions and their strongest arguments, and let them choose.

**5. Act, then report.** The baton holder makes the change. Tell the user what was decided, what the disagreement was, and what changed the answer — more useful than a summary that hides the argument.

Skip the loop only for mechanical lookups where there is nothing to disagree about ("what does this file do", "run the tests"). Anything involving a design choice, a trade-off, a code change, or an unclear cause goes to the partners.

## Reference

```bash
python "$P" providers                       # what is installed, models, effort support
python "$P" list                            # everyone here + who holds the baton
python "$P" snapshot                        # repo state captured for a new partner
python "$P" spawn --provider X [--model M] [--effort low|medium|high|max]
                  [--auto ask|edits|full] [--name id] [--context TEXT|PATH]
                  [--cmd 'template']        # with --provider custom
python "$P" send --to @all --text "..." [--wait 240]   # --from defaults to you
python "$P" read [--peek]                   # new messages; --peek keeps the cursor
python "$P" wait [--for id] [--timeout 120] # block until addressed; partners use this
python "$P" claim                           # user just told YOU to act: take the baton
python "$P" baton [--to p2]                 # show, or hand over deliberately
python "$P" stop --id p2 | --all            # stop partners, then close their tabs
```

Add `--json` to any command for machine-readable output.

State lives in `.partner/` at the repo root — `chat.md` is the full transcript, readable at any time. `init` adds `.partner/` to `.git/info/exclude`, so it is ignored locally without touching a tracked `.gitignore`.

## Deeper reference

- `references/providers.md` — per-provider launch flags, how auto levels and effort map onto each CLI, and how to add a provider.
- `references/protocol.md` — transcript format, state layout, and how an agent takes part from inside its tab.
- `references/terminals.md` — terminal-tab selection order per OS and what to do when none is available.

## When things go wrong

**Partner never replies.** Look at its tab. It may be waiting on a permission prompt (raise `--auto`), stuck on an error, or simply between `wait` calls. If the CLI failed to start, the tab shows why — check that it runs standalone and is authenticated.

**Partner stopped looping.** Interactive agents sometimes end their turn instead of running `wait` again. Type "continue" in its tab, or `send` it a message — it picks up from there.

**Partner agrees with everything.** Usually the same model as yours, or effort set too low. Stop it and spawn a different provider.

**Partner edited files it should not have.** Either it still held the baton from the last time the user addressed it, or it treated another agent's suggestion as an instruction. Check `python "$P" list`; if the user is talking to you, run `claim` so the partners are told to stop.

**Debate will not converge.** Expected on genuine judgement calls. Stop after three exchanges and hand the choice to the user with both positions stated fairly.
