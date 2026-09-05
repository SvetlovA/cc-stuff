---
name: partner
description: This skill should be used when the user asks to "create a partner", "spawn a partner", "/partner", "start a partner agent", "add a peer agent", "get a second opinion from another model", "debate this with codex", "argue this with gemini", "have another AI review this with me", or wants a second AI agent running in its own terminal tab to challenge decisions. Use it also to hand over the write baton, list partners, or stop them. Critically — once any partner is active (a `.partner/roster.json` with a running entry exists in the repo), use this skill on EVERY subsequent question and decision in the session to run the debate protocol before answering, not just when the user names it.
argument-hint: "[provider] [model] [effort] | list | baton <id> | stop [id]"
allowed-tools: Bash, Read, Write, Edit, Grep, Glob, AskUserQuestion
version: 0.1.0
---

# Partner

Spawn peer AI agents — Claude, Codex, Gemini, or any CLI — into new terminal tabs, then debate every question with them through a shared markdown transcript before acting.

A partner is not a subagent. It is a separate process with its own model, its own context, and its own opinion, and it can tell you that you are wrong. The point is friction: two models that disagree surface assumptions that one model alone will confidently glide past.

All commands go through one script. Define it once per session:

```bash
P="${CLAUDE_PLUGIN_ROOT}/skills/partner/scripts/partner.py"
```

## The write baton

Partners are equal in capability — every one of them *can* edit files. What decides who actually does is the **baton**, a single value in `.partner/roster.json`.

The baton holder edits files. Everyone else reads, runs read-only commands, and argues. This is enforced, not merely requested: each debate round launches the provider with a read-only sandbox flag (`--permission-mode plan` for Claude, `--sandbox read-only` for Codex) whenever that partner does not hold the baton, and with write access when it does.

There is no privileged agent. This session registers itself in the roster as an ordinary participant — `p1` by default — exactly like the ones it spawns, and `list` marks which one is you. The baton belongs to whoever the user is currently addressing. When they turn to another agent's tab and ask it to implement something, move the baton there first:

```bash
python "$P" baton --to p2     # p2 now edits; you advise
python "$P" baton --to p1     # take it back
```

Move the baton *before* the other side starts working, and say so in the transcript. Two agents editing the same files concurrently produces conflicts that neither can see.

## Starting a partner

### Step 1 — Settle the configuration

First register yourself, so the roster describes every participant accurately rather than guessing at yours:

```bash
python "$P" init --me-provider claude --me-model <this session's model id>
```

This takes `p1` and costs nothing if it has already run. Spawned agents continue from `p2`.

Three settings define a new partner: **provider**, **model**, **effort**. Take whatever the user supplied as arguments and only ask about the rest.

Check what is actually installed before offering options — recommending an uninstalled CLI wastes a round trip:

```bash
python "$P" providers
```

If anything is still unset, ask with `AskUserQuestion`, presenting installed providers first and marking uninstalled ones. Sensible defaults when the user says "just pick": a provider *different from this session's own model*, because an agent running the same model as you tends to agree with you, and mid or high effort.

For a CLI not in the registry, use `--provider custom` with a command template:

```bash
python "$P" spawn --provider custom --cmd 'aider --model {model} --message {msg}' --model gpt-4o
```

`{msg}`, `{model}`, `{effort}` and `{readonly}` are substituted per round; a template without `{msg}` gets the prompt appended as the final argument.

### Step 2 — Brief the partner on what it is joining

A partner can be spawned at **any** point — before work starts, or twenty exchanges into a hard problem. Mid-work is the normal case, and it is where briefing matters most: a partner that does not know what was already settled will reopen it, and confidently.

Each partner gets its own briefing at `.partner/<id>/handoff.md`, so spawning `p2` later never disturbs what `p1` was told. Three things go into what it sees on its first round:

| What | Who provides it |
|------|-----------------|
| Branch, recent commits, uncommitted changes, diffstat | **Automatic** — collected at spawn |
| The debate so far, if one is already underway | **Automatic** — last 20 transcript messages |
| What is being built, what is decided, what is open | **You**, via `--context` |

The automatic half means a hurried spawn still produces a useful partner. The half you write is what stops it re-litigating: state the decisions that are closed and *why*, not a narrative of what was typed.

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

Pass `--context <path>` to reuse a file instead. Run `python "$P" snapshot` to see the repo state that will be captured.

Spawn as many partners as useful — ids are `p1`, `p2`, … unless `--name` is given. When adding one to an argument in progress, say in `--context` what you want *from it specifically*, or it will simply restate what the others already said.

### Step 3 — Confirm the tab opened

`spawn` picks the best available terminal automatically (see `references/terminals.md`). If none can be opened, it prints the exact command for the user to run in a tab they open themselves — relay that command rather than treating the spawn as failed.

Two modes:

- `--mode loop` (default) — the tab runs a watcher that auto-replies whenever a message is addressed to that partner. Best for debate: the partner answers without anyone driving it, and the user watches the argument happen live.
- `--mode tui` — the tab runs the provider's normal interactive interface, seeded with the protocol. Best when the user wants to type at the partner directly.

Both write to the same transcript, so modes can be mixed.

## The debate protocol

Once a partner is running, consult it on **every question and every decision** — that is the point of having one. At the start of a turn, check whether partners exist:

```bash
python "$P" list
```

If any are `running`, do not answer the user directly. Run this loop instead:

**1. State a position first.** Form your own answer before asking. A partner given a blank question anchors on nothing; a partner given a concrete claim has something to attack. Include your reasoning and what you are unsure about.

**2. Put it to the partners.**

```bash
python "$P" send --to @all --wait 180 --text "..."
```

`--from` defaults to your own id, so it can be omitted. `--wait` blocks until they reply and prints the replies; without it, `send` returns immediately and you collect replies later with `read`. Use `--to p2` to ask one agent, `--to @all` for a group debate.

**3. Take the disagreement seriously.** A partner that objects has usually noticed something. Check the claim against the code before accepting or rejecting it — do not concede to be agreeable, and do not dismiss to defend your first answer. Where it is right, say so plainly and change course.

**4. Rebut or converge.** If you still disagree after checking, say why and send it back. Two or three exchanges is normally enough. Real deadlocks are informative, not failures: when partners still disagree after a genuine exchange, the decision is a judgement call and belongs to the user. Present both positions and their strongest arguments, and let the user choose.

**5. Act, then report.** The baton holder makes the change. Tell the user what was decided, what the disagreement was, and what changed their answer — that is more useful than a summary that hides the argument.

Skip the loop only for mechanical lookups where there is nothing to disagree about ("what does this file do", "run the tests"). Anything involving a design choice, a trade-off, a code change, or an unclear cause goes to the partners.

## Reference

```bash
python "$P" providers                       # what is installed, models, effort support
python "$P" list                            # partners + who holds the baton
python "$P" snapshot                        # repo state captured for a new partner
python "$P" spawn --provider X [--model M] [--effort low|medium|high|max]
                  [--name id] [--mode loop|tui] [--context TEXT|PATH]
                  [--cmd 'template']        # with --provider custom
python "$P" send --to @all --text "..." [--wait 180]   # --from defaults to you
python "$P" read [--peek]                   # new messages; --peek keeps the cursor
python "$P" baton [--to p2]                 # show or move write permission
python "$P" stop --id p1 | --all            # stop partners (then close their tabs)
```

Add `--json` to any command for machine-readable output.

State lives in `.partner/` at the repo root — `chat.md` is the full transcript and is readable at any time. `partner.py init` adds `.partner/` to `.git/info/exclude`, so it is ignored locally without touching a tracked `.gitignore`.

## Deeper reference

- `references/providers.md` — per-provider flags, how effort and read-only map onto each CLI, and how to add a new provider.
- `references/protocol.md` — transcript message format, cursor semantics, and how to participate from inside a partner tab.
- `references/terminals.md` — the terminal-tab selection order per OS and what to do when none is available.

## When things go wrong

**Partner never replies.** Look at its tab; the watcher prints every round. `[partner error]` lines carry the provider's stderr. Check the CLI runs standalone and is authenticated.

**A partner agrees with everything.** Usually the same model as yours, or effort set too low. Stop it and spawn a different provider.

**Partner edited files it should not have.** It held the baton. Check `python "$P" list` and move the baton before asking a partner to think rather than act.

**Debate will not converge.** Expected on genuine judgement calls. Stop after three exchanges and hand the choice to the user with both positions stated fairly.
