---
name: partner
description: This skill should be used when the user asks to "create a partner", "spawn a partner", "/partner", "/partner add", "start a partner agent", "add a peer agent", "get a second opinion from another model", "debate this with codex", "argue this with gemini", "have another AI review this with me", or wants another AI agent running in its own terminal tab to challenge decisions. Use it also to hand over the write baton, list agents, or stop them. Critically — once any partner is active (a `.partner/roster.json` with a running entry exists in the repo), use this skill on EVERY subsequent question and decision in the session to run the debate protocol before answering, not just when the user names it.
argument-hint: "[provider] [model] [effort]  ·  add [provider ...] | resume [id] | sessions | list | baton <id> | stop"
allowed-tools: Bash, Read, Write, Edit, Grep, Glob, AskUserQuestion, WebSearch, WebFetch, Skill
version: 0.14.0
---

# Partner

Run several AI agents — whatever agent CLIs this machine has, plus any you describe — as equal partners on one repository, each in its own terminal tab, debating every question through a shared transcript before anyone acts.

The providers and models on offer are **discovered, never recited**: the CLIs come from a scan of this machine, the model ids from those CLIs and the user's own config for them. Nothing is read off a list baked into the plugin, because such a list is stale within weeks and its staleness is invisible.

A partner is not a subagent. It is a full interactive session with its own model and its own opinion, and it can tell you that you are wrong. The point is friction: two models that disagree surface assumptions one alone glides past. The user can type at any of them directly; between interruptions each watches the transcript and answers the others on its own.

All commands go through one script:

```bash
P="${CLAUDE_PLUGIN_ROOT}/skills/partner/scripts/partner.py"
```

After `init` a short wrapper also exists at `.partner/p.cmd` (Windows) or `.partner/p.sh` (macOS/Linux). That is what the partners themselves use.

## The write baton

There is no privileged agent: this session registers itself as an ordinary participant — `p1` by default — with the same config and briefing as the ones it spawns, and `list` marks which is you. Every agent *can* edit files, since they run with prompting relaxed. What decides who actually does is the **baton** in `.partner/roster.json`.

**The rule: only the agent the user just gave an instruction to may write.** The baton follows the user's attention around the tabs, and the user can start from *any* of them — this session has no special claim on it. Nothing outside a tab can observe where they are typing, so whichever agent receives the instruction is the one that has to say so.

Each agent runs through its own wrapper in `.partner/<id>/`, which carries its identity, so `claim`, `read`, `wait` and `send` never take an id. Any agent can `spawn` more partners too — no role belongs to one agent.

**When the user gives you an instruction, claim the baton before doing anything else:**

```bash
python "$P" claim
```

Then do the work. Skipping this means a partner still believes it holds the baton and may edit the same files you are editing.

The half that is easy to get wrong: a message from **another agent** never moves the baton. A partner saying "you should change X" is a suggestion, not the user's instruction — argue it, and if the group agrees, the change is still only yours to make if you hold the baton.

Two things land where the user's typing lands and are **not** the user: a partner's own **launch message** and an automated **wake-up**. Both disown themselves in their own first line, neither moves the baton, and `claim` refuses an agent that started seconds ago and has not spoken yet (`claim --force` for when the user genuinely did just address a brand-new partner). Without that, a fresh partner intermittently reads its own boot prompt as an instruction and takes write permission from whoever is working.

`read` and `wait` print the current holder on every call — believe that line over your memory of it. `python "$P" baton --to p2` hands it over deliberately.

Because partners run unprompted this is a rule they keep, not a wall they hit. The reason matters more than the rule: two agents editing the same files concurrently produce conflicts neither can see, and the user loses work.

## What the invocation means

`/partner` has two shapes, and they differ in exactly one thing — whether the session already running is kept:

| The user types | Meaning | What you run |
|----------------|---------|--------------|
| `/partner` — alone, or `/partner <provider> [model] [effort]` | **start a new session** | `spawn --fresh` — archive the current session, then bring up **one** new partner on a clean slate |
| `/partner add [provider ...]` | **add to the session in progress** | `spawn` *without* `--fresh` — another partner joins the ones already running; nothing is archived |
| `/partner resume [id]` | bring a session back | `resume [--session <id>]` — every agent rebuilt and re-briefed from its transcript |
| `/partner sessions` · `list` · `baton <id>` · `stop` | inspect or manage | the matching command |

The bare form is a *fresh* session because that is the common case: the user sat down to think a new problem through. Reach for `add` only when partners are already live and the user wants one more voice in that same argument.

## Step 0 — See what a fresh `/partner` would replace

```bash
python "$P" state
```

`status: running` in the roster is only a claim — an agent whose tab was closed still says it. `state` uses evidence: every agent stamps a heartbeat when it acts, so **live** means it did something in the last few minutes, and inside Orca the tab list corroborates. It reports `live`, `stale` and `stopped` agents and the archived sessions.

`state` is **informational** — the invocation above already decided the shape of the work. Use `state` to narrate the consequence:

- **`/partner` (fresh):** run `spawn --fresh`. If `state` shows a real session (any partner besides this one, or a real discussion in the transcript), say in one line what is being filed away — *"archiving current session (2 agents, 6 messages) — `/partner resume <id>` brings it back"* — then spawn. If the roster holds only this session, `--fresh` archives nothing and simply adds the partner; no notice needed.
- **`/partner add`:** run `spawn` without `--fresh`. If `state` shows no partners at all, note that the session looks empty and confirm they did not mean a fresh start — but if they were explicit, just proceed.
- **`/partner resume` with nothing archived:** there is nothing to resume — offer a fresh start instead.

A fresh `/partner` archiving a live debate is intentional and reversible (`resume`), but always name what you archived so the user can get back to it.

## Sessions

```bash
python "$P" state                                # who is alive; what a fresh /partner replaces
python "$P" sessions                             # current + archived
python "$P" spawn --provider codex --fresh ...   # /partner: new session, archive the old
python "$P" spawn --provider codex ...           # /partner add: join the live session
python "$P" resume                               # restart the current agents
python "$P" resume --session 20260905-185718     # bring an archived one back
```

`--fresh` files the previous arrangement under `.partner/sessions/<id>/` — roster, transcript and briefings, moved whole rather than deleted. `resume` rebuilds **every** agent with its original provider, model and effort and re-briefs each from the restored transcript, so the argument continues instead of restarting; it archives whatever was live first, so switching never loses work.

## Starting a partner

**The job is not done until a partner is running in a tab and the session has something to argue about.** Registering yourself, checking a provider, asking which model — all steps *towards* that, never the result. Stop at any of them and the user has an empty roster. Finish with `list` showing at least two running agents, a `kickoff` in the transcript, and a background `wait` armed.

### Step 1 — Settle the configuration

Four settings define a new partner: **provider**, **model**, **effort**, and **auto** level. Take whatever the user supplied and only ask about the rest.

**Never offer a list of providers or models from memory.** There is no bundled catalog to recite, and a remembered one is wrong within weeks — invisibly, because the user simply never sees the model that shipped last month. Both lists come from the machine, every time:

```bash
python "$P" providers                # agent CLIs actually installed here
python "$P" models --provider <X>    # ids this machine names, and the effort that suits each
```

`providers` scans PATH and the per-user install dirs and keeps what behaves like an agent CLI. Whatever it lists can be spawned, known to this plugin or not — and what it misses is still usable, so it is a suggestion, never a gate.

`models` splits ids into **named** (the CLI's own list, or a `model` setting in the user's config — solid) and **mentioned** (text merely shaped like a model id — offer them labelled). Details of either: `references/providers.md`.

**Check the web when the local answer is thin or dated.** A model released after the installed CLI was built is mentioned nowhere on this machine. Search for that provider's current lineup, offer what you find alongside the local ids, and say which is which — anything can be passed to `--model` whether or not the scan saw it.

**Never pick the model silently.** Put the options to the user with `AskUserQuestion` — installed providers first, each with the effort `models` recommends and its one-line character note. A partner the user did not choose is one they will not believe when it disagrees with them, which is the entire point of running it. Ask even when they supplied the provider, unless they also named the model.

When they genuinely say "just pick": the newest **named** id in a balanced or deep tier, from a provider *different from your own model* — same-model agents tend to agree with you. Say which you chose and why.

Validate before spawning: `python "$P" check --provider codex --model gpt-5.6 --effort high`. A **problem** stops the spawn and carries the command that fixes it — relay that verbatim. A **caution** is the script not recognising something, not evidence it is wrong; pass it on and continue. An unrecognised model is always a caution.

**Auto levels** control how often a partner stops to ask permission — a prompt in an unwatched tab stalls the debate. `--auto edits` (default) accepts file edits while keeping the sandbox; `ask` keeps normal prompting; `full` removes both. Per-provider flags: `references/providers.md`.

### Step 1b — A CLI this plugin does not know

Naming the binary is the normal case and needs no configuration — its flags are read from its own `--help`:

```bash
python "$P" probe --provider mycli     # the flags derived, and the command a spawn would run
python "$P" spawn --provider mycli --model ... --effort high
```

Run `probe` first when in doubt. If the derived command is wrong, give the exact one with `--provider custom --cmd 'mycli --model {model} --yes --message {prompt}'` — and put the CLI's own "stop asking me" flag in it, since `--auto` cannot reach into a hand-written template.

To keep a CLI between sessions, describe it once in `~/.claude/partner-providers.json`; it then works as a plain `--provider` name. Format, placeholders and the probe's rules: `references/providers.md`.

### Step 2 — Brief the partner on what it is joining

A partner can join at **any** point, and mid-work is the normal case — which is where briefing matters most: one that does not know what was settled will reopen it, confidently.

Each gets its own `.partner/<id>/handoff.md`, so a third never disturbs the second. Branch, uncommitted diff and the debate so far are collected **automatically**. What **you** add via `--context` stops it re-litigating: state which decisions are closed and *why*, not a narrative of what was typed.

One `spawn` call does everything: it registers **you** from the `--me-*` values, writes both briefings, and opens the partner's tab. Describe yourself as fully as the partner — you are a peer, not the thing the others hang off.

```bash
python "$P" spawn --provider codex --model gpt-5-codex --effort high --fresh \
  --me-provider claude --me-model <this session's model id> --me-effort high \
  --context "Auth rewrite in src/auth/. Decided: opaque session tokens,
  Redis-backed -- do not reopen, JWT revocation was the blocker. Open: do
  refresh tokens rotate every use or only near expiry? Cannot break /v1/login."
```

`--fresh` is what a bare `/partner` uses — it archives the running session first (a no-op when only this session is registered). Drop `--fresh` for `/partner add`, so the new partner joins instead.

You take `p1` and the partner `p2` (or the next free number when adding). Afterwards read your own briefing at `.partner/p1/seed.md` — the same document the partner received.

`--context` also accepts a file path. When adding a partner mid-argument, say what you want *from it specifically*, or it will restate what the others said.

### Step 3 — Confirm the tab opened

`spawn` picks the terminal automatically. **Inside Orca it opens an Orca tab in the current worktree**, beside the session that spawned it rather than in a detached window; elsewhere it uses the platform's terminal (`references/terminals.md`). If none can be opened it prints the command for the user to run — relay that rather than calling the spawn failed.

The tab runs the provider's normal interface on a briefing that tells it to loop: block on `wait`, reply, repeat. The user can read the debate as it happens, interrupt at any moment, and type at that agent — it answers them, then resumes.

Confirm with `python "$P" list` before reporting success. Two or more agents means it worked; only yourself means the spawn never happened — say that plainly rather than describing what was set up.

### Step 4 — Ask what to start on, then open the session

A briefed roster is not yet a debate: everyone is blocked on `wait` and nobody has been asked anything. So ask, with `AskUserQuestion`, and **accept anything as the answer**:

- **Free text** — a task, a question, a bug, a half-formed doubt. It becomes the opening instruction, verbatim.
- **Another skill or slash command** — `/brainstorm`, `/planning:make`, a project skill. Invoke it and run *its* workflow, with the debate protocol wrapped around the decisions it reaches; the partners do not have that skill, so relay what they need in order to argue with it.
- **Nothing** — they skip, or say "not yet".

Then open the session, so every agent gets the same words:

```bash
python "$P" kickoff --text "<what the user said>" --wait 240   # claim the baton first
python "$P" kickoff                                            # they gave you nothing
```

Bare `kickoff` sends the **orientation brief**: the agents split this repository between them, read it, cite `path:line`, and converge on one shared picture in at most two rounds, **editing nothing**, then return to `wait` — so the first real question is not answered from a cold read. Skip it and just wait when the context is already there (a resumed session, a `--context` briefing that says what the work is) or when the user asked to be left alone; say which in one line. Details: `references/debate.md`.

### Step 5 — Enter the loop yourself, before ending the turn

**Spawning a partner is not the end of the task.** The partner blocks on `wait` in its tab, so it hears everything. This session does not block, and nothing re-invokes it on its own — so unless it arms a listener now, it drops out of the debate the moment the user starts typing in the partner's tab, and the partner argues with nobody.

Two commands, always, as the last thing in this turn:

```bash
python "$P" read                     # 1. pick up anything already said
python "$P" wait --timeout 600       # 2. run this as a BACKGROUND command
```

The second must be **backgrounded** (the Bash tool with `run_in_background: true`) — in the foreground it blocks the harness and the user cannot talk to this session. Claude Code re-invokes this session when it returns, which is the whole mechanism: one background `wait` in flight, re-armed at the end of every turn thereafter.

`.partner/<id>/lastseen` is the proof it is running — the stamp refreshes every few seconds while `wait` polls. Missing or minutes old means this session is deaf, whatever the roster claims.

**A `wait` that returns early or empty is never a reason to stop.** Every CLI caps command runtime — codex's exec tool at 10s, Claude Code's Bash tool at 120s — so a capped `wait` comes back with nothing, and an agent reading that as a broken command leaves the loop for good. Each briefing carries its own CLI's cap (`references/providers.md`); run `wait` again.

### When a partner goes quiet

A tab agent whose CLI ended its turn is unreachable — nothing re-invokes it, and only Claude Code has the Stop hook. `read`, `wait` and `send` name any agent with no `wait` in flight, and the fix is to type into its tab:

```bash
python "$P" nudge [--id p2]        # one, or everyone who stopped listening
```

`send` does it automatically for a recipient that is not listening, so a message to a stalled partner wakes it instead of vanishing. A detached OS window cannot be typed into — `nudge` says so and prints the command that restarts the agent, which is what to relay. Wake-ups never move the baton. `references/terminals.md`.

## The debate protocol

Once a partner is running, consult it on **every question and every decision** — that is the point of having one. Check `python "$P" state` at the start of a turn; if any partner is live, do not answer the user directly.

There are two roles and the **baton** decides which is yours: the holder acts, everyone else advises. Any agent can hold it, in any order, and it moves with the user's attention — read the holder off every `wait`/`read` and switch roles when it changes.

**Holding the baton:** **claim** it, **state your own position** first, **put it to the group** with `send --to @all --wait`, **check their objections against the code** rather than conceding or dismissing, **rebut or converge** in two or three exchanges, then **act and report the argument** — not a summary that hides it. Deadlocks are a result, not a failure: hand a genuine judgement call back to the user with both positions stated fairly.

**Not holding it:** advise whoever does, and debate the other advisors directly — `send --to p3`, not only `@all`. Several agents settling a question among themselves and handing the holder one recommendation is the design. Never `claim` or edit; only the user moves the baton.

This session, when it is not the holder, joins through a background `wait` (`run_in_background`) — one in flight, re-armed each turn — which is what keeps it in the debate while the user works in a partner's tab. `references/debate.md` has the mechanics.

Skip the loop only for mechanical lookups. Anything involving a design choice, a trade-off, a code change, or an unclear cause goes to the partners.

`references/debate.md` has the full protocol, with the exact commands and the reasoning behind each step — read it before running the loop for the first time in a session.

## Reference

```bash
python "$P" providers [--deep]              # agent CLIs found here; --deep probes unnamed ones
python "$P" models [--provider X] [--deep] [--no-probe]   # ids found on this machine + effort
python "$P" probe --provider X [--refresh]  # flags read from a CLI's own --help
python "$P" check --provider X [--model M] [--effort E] [--force]
python "$P" list                            # everyone here + who holds the baton
python "$P" state                           # who is ALIVE + what to do next
python "$P" sessions                        # current + archived sessions
python "$P" resume [--session <id>]         # rebuild every agent, re-briefed
python "$P" archive [--label "..."]         # file the current session away
python "$P" snapshot                        # repo state captured for a new partner
python "$P" spawn --provider X [--model M] [--effort low|medium|high|max]
                  [--auto ask|edits|full] [--name id] [--context TEXT|PATH]
                  [--cmd 'template']        # with --provider custom
                  [--force]                 # accept a model the list does not know
                  [--me-provider X --me-model M --me-effort E]   # describes YOU
                  [--fresh]                 # archive the old session first
python "$P" kickoff [--text "..."] [--wait 240]   # open the session; no --text = orientation
python "$P" send --to @all --text "..." [--wait 240]   # --from defaults to you
python "$P" read [--peek]                   # new messages; --peek keeps the cursor
python "$P" wait [--for id] [--timeout 90]  # block until addressed; partners use this
python "$P" nudge [--id p2 | --all]         # wake agents that stopped looping
python "$P" pending                         # messages you still owe a reply to
python "$P" hook-stop                        # internal: the Stop-hook backstop
python "$P" claim [--force]                 # user just told YOU to act: take the baton
python "$P" init --me-provider X --me-model M [--me-effort E] [--me-auto A]
                                            # rarely needed; spawn does this
python "$P" baton [--to p2]                 # show, or hand over deliberately
python "$P" stop --id p2 | --all            # stop partners, then close their tabs
```

Add `--json` to any command for machine-readable output.

State lives in `.partner/` at the repo root — `chat.md` is the full transcript, readable at any time. `init` adds `.partner/` to `.git/info/exclude`, so it is ignored locally without touching a tracked `.gitignore`.

A `Stop` hook (`hooks/hooks.json`) stops any agent — this session included — from ending a turn while a message to it or `@all` sits unanswered in `chat.md`; it points them at `read`/`send` first. The baton holder is exempt, own messages do not count, and it fails open. `references/protocol.md` covers it.

## Deeper reference

- `references/providers.md` — how CLIs and models are discovered on the machine, per-provider launch flags, auto levels and effort, and how to use or keep a custom CLI.
- `references/protocol.md` — transcript format, state layout, and how an agent takes part from inside its tab.
- `references/debate.md` — the full debate loop, step by step.
- `references/troubleshooting.md` — symptoms, causes and fixes.
- `references/terminals.md` — terminal-tab selection order per OS and what to do when none is available.

## When things go wrong

Symptoms and fixes are in `references/troubleshooting.md` — read it when a partner will not start, will not reply, edits out of turn, or the user's partners are not the ones they expected.
