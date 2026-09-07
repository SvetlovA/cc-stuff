# partner-agents

Run several AI agents as equal partners on one repo, each in its own terminal tab, and argue with them before you commit to anything.

A partner is a **separate process** with its own provider, model, and context — not a subagent. Claude can run the debate against whichever agent CLIs are on your machine, or any CLI you can describe in one line, and the disagreement is the product: two models that differ surface the assumptions one model alone glides past.

## What it does

- **Spawns partners into real terminal tabs** on Windows, macOS, and Linux — Windows Terminal, iTerm2, Terminal.app, GNOME Terminal, Konsole, WezTerm, kitty, tmux, and zellij are all detected automatically.
- **Native Orca support.** Running inside [Orca](https://orca.computer)? Partners open as Orca tabs in the current worktree, beside the session that spawned them — not in a detached OS window. Detected automatically, with the normal terminal flow as fallback.
- **Finds your CLIs instead of assuming them.** No bundled list of blessed providers. It scans PATH *and* the per-user install directories (`~/.local/bin`, npm's prefix, `%LOCALAPPDATA%\Programs`, cargo, bun, winget…), then keeps only what behaves like an agent CLI — its own `--help` has to mention a model and accept a task in words. `gpg-agent` does not qualify; a coding agent you installed last week does, whether or not this plugin has heard of it.
- **Reads models off your machine, not off a list.** A catalog baked into a plugin is wrong within weeks, and the wrongness is invisible — you just never see the model that shipped last month. Instead: the CLI's own model list where it has one, the `model` settings in your config for it, its help output, its program files. Ids are grouped by how much the source proves, sorted newest-first, and annotated from the *name shape*, so `whatever-pro-7` is described correctly the first time it exists.
- **Any CLI, three ways.** Name a binary directly and its flags are read from `--help`; pass the exact command with `--cmd 'mycli --model {model} --yes {prompt}'`; or describe it once in `~/.claude/partner-providers.json` and use it by name forever after.
- **Tells you before it fails.** Checks the CLI is installed and actually runs, and the effort level is one that provider accepts — each hard problem reported with the command that fixes it, before a tab is opened. Things it merely does not *recognise* are said out loud and then done anyway.
- **Every partner is a full interactive session.** Walk into any tab and type at that agent directly — it answers you, then goes back to debating. Between your interruptions it drives itself.
- **No permission dialogs to babysit.** Partners start with prompting relaxed (`--auto ask | edits | full`), so you are not approving edits in three tabs at once.
- **Joins mid-work with the context intact.** Spawn a partner twenty exchanges into a hard problem and it arrives knowing the branch, the uncommitted diff, and the argument so far — plus whatever briefing you write. Each partner keeps its own, so adding a third never overwrites what the second was told.
- **Asks what to start on, and takes any answer.** Once the partners are up you are asked what to work on — free text, a bug, a doubt, or another skill or slash command to run through the debate. Say nothing and the agents orient themselves instead: they split this repository between them, read it, cite `path:line`, and hand each other one grounded picture of what it is and what to be careful with — editing nothing, converging in two rounds, then waiting for you. Your first real question is not answered from a cold read.
- **Debates every question.** Once a partner is running, the skill puts each question and decision to it, weighs the pushback against the actual code, and reports the argument rather than hiding it.
- **Nothing gets delivered once and forgotten.** A read cursor answers "was this handed over", which is the wrong question when the handover fails — a wait killed mid-print, or an agent that read a message and ended its turn. `wait` asks the transcript instead: anything addressed to an agent and still unanswered comes back marked *re-delivered*, so a dropped message recovers itself rather than waiting for another partner to notice.
- **Keeps partners in the loop, and wakes them when they fall out.** Every agent CLI caps how long one shell command may run, and the caps differ by more than tenfold — which used to kill the blocking `wait` an agent's loop depends on and leave the tab silent for good. Now every partner is briefed on that rule whatever it is running (plus its own CLI's cap where one has been measured — and you can record one for a CLI of your own), `wait` explains itself before it blocks, and any agent nothing is listening for gets a wake-up typed straight into its tab (Orca, tmux, WezTerm, kitty). A message to a stalled partner wakes it instead of vanishing.
- **No lead agent, and it is structural.** The session you are typing in registers itself as `p1` with the same config fields, the same briefing document and its own identity wrapper as every agent it spawns — built by the same code, so the two cannot drift. Any partner can spawn more partners; any partner can take the baton. The only asymmetry is the baton, and it moves.
- **Only the partner you just spoke to can write.** The write baton follows your attention: the moment you type an instruction into a tab, that agent claims it and the others are told to stop editing. A partner asking another partner to change something is a suggestion, not an instruction — it does not move the baton. Concurrent edits produce conflicts nobody can see.
- **Runs as many partners as you want** — `p2`, `p3`, `p4` alongside you, each with its own provider, model and effort, all debating in one shared transcript.
- **Knows who is actually alive.** Agents stamp a heartbeat when they act, so a partner whose tab you closed is reported as stale rather than trusted to be running. `/partner` starts a fresh session and files the running one under `.partner/sessions/` (a no-op if nothing was running yet); `/partner add` puts another voice into the argument already in progress.
- **Sessions you can come back to.** Every new arrangement files the previous one under `.partner/sessions/` — roster, transcript and briefings intact. `resume` rebuilds all its agents with their original models and re-briefs them from the transcript, so the argument continues instead of restarting.
- **Keeps everything in plain markdown.** `.partner/chat.md` is the whole conversation, readable in any editor at any time.

## Install

```bash
cc plugin install https://github.com/SvetlovA/cc-stuff/tree/master/partner-agents
```

Or locally:

```bash
git clone https://github.com/SvetlovA/cc-stuff.git
cc --plugin-dir ./cc-stuff/partner-agents
```

## Use

```
/partner                              # new session: archive the current one, spawn one partner
/partner codex gpt-5-codex high       # same, with the config up front instead of asked
/partner add gemini                   # add another partner to the session in progress
/partner resume [id]                  # rebuild a session from its transcript
/partner list                         # who is running, who holds the baton
/partner check codex gpt-5-codex high # validate without starting anything
/partner baton p2                     # hand editing to p2 deliberately
/partner stop --all
```

You are then asked what to start on — anything goes, including another slash command to run through the debate. Say nothing and the partners spend a round working out what this repository is before waiting for you.

Then just keep working. Every question after that gets debated before it gets answered.

## Providers

| Provider | Effort control | Auto-approval at `--auto edits` |
|----------|---------------|----------------------------------|
| `claude` | prompt hint | `--permission-mode acceptEdits` |
| `codex` | `model_reasoning_effort` (native) | `-a never -s workspace-write` |
| `gemini` | prompt hint | `--approval-mode auto_edit` |
| `custom` | template | your own flag, in the template |
| anything else on your machine | read from its `--help` | read from its `--help` |

Those three rows are hand-verified launch flags, not the permitted set. Any other agent CLI is driven from its own help text:

```bash
partner.py providers            # what is installed here, and how each will be driven
partner.py providers --deep     # also probe binaries whose names give nothing away
partner.py probe --provider mycli   # the flags read from its help, and the command a spawn runs
partner.py spawn --provider mycli --model ... --effort high
```

Probing is conservative: a flag it did not find is a flag it does not pass, so an unknown CLI launches on its own defaults rather than on a guess. `--auto edits` deliberately never falls back to a sandbox-bypass flag.

When the derived command is wrong, give the exact one:

```bash
/partner custom --cmd 'aider --model {model} --yes --message {prompt}'
```

...or keep it, in `~/.claude/partner-providers.json` (or `.partner/providers.json` for one repo):

```json
{
  "aider": {
    "cmd": "aider --model {model} --yes --message {prompt}",
    "efforts": ["low", "medium", "high"],
    "install": "pipx install aider-chat"
  }
}
```

It then appears in `providers` and spawns as `--provider aider` with no template on the command line.

## Picking a model

```
$ partner.py models --provider codex
codex
  gpt-5.6-luna                    effort=high    [general]
                                  no tier signal in the name -- try it and see
                                  from a model setting in your config.toml
  only the shape of a model id, found in text -- some of these are not models:
  gpt-5.6                         effort=high    [general]
  gpt-5.4-mini                    effort=low     [fast]
                                  fast and cheap; concedes too easily to be much of an opponent
  effort is a native flag; accepts: high, low, medium
```

Everything printed was found on this machine, and where it came from is printed with it, because that is the whole caveat: a model list command is evidence, a string in a bundle is a lead.

The tier note is read off the **name**, not from a table of ids — `opus`/`pro`/`max` reason deeper and slower than `flash`/`mini`/`lite`. Shapes stay true for models that do not exist yet, which is why it is phrased that way.

The limit is stated in the output rather than hidden: a model released after your CLI was built is mentioned nowhere locally. When the list looks thin or dated, the skill checks the provider's current lineup on the web and offers those alongside — they spawn with `--model` either way.

## Requirements

- **Python 3.9+** on PATH — the only dependency, and it uses nothing outside the standard library.
- At least one agent CLI installed and authenticated (`claude`, `codex`, `gemini`, or your own).
- A git repository — `.partner/` is anchored at the repo root and auto-excluded via `.git/info/exclude`.

A terminal is *not* required. Without one, partners still run; the skill prints the command to start each in a tab you open yourself.

## One command, one outcome

`spawn` registers you as a participant *and* starts the partner:

```bash
partner.py spawn --provider codex --model gpt-5-codex --effort high   --me-provider claude --me-model claude-opus-5 --me-effort high
```

You become `p1`, the partner `p2`, both with the same config fields and the same briefing. Splitting this into "register, then spawn" left sessions that registered themselves and stopped, so there is one call and it either produces a running partner or reports why not.

## Sessions

```
$ partner.py state
you are p1; baton held by p1
  p1       live    this session
  p2       live    acted 12s ago

suggested: add -- p2 active -- `/partner` archives this session and starts one partner fresh; `/partner add` keeps it and joins

$ partner.py sessions
current   2 agents, 5 messages   Should div() guard against zero?
20260905-185729   2 agents, 1 messages   [p1, p2]
                  no discussion (p1, p2)

$ partner.py resume --session 20260905-185718
resumed session 20260905-185718
  p2 (codex/gpt-5-codex)
```

Sessions are labelled by the first real thing said in them, so the list reads as a list of questions. `resume` with no `--session` restarts the agents of the current one — useful after closing tabs or rebooting.

## Validating before you spawn

```
$ partner.py check --provider codex --effort max
cannot start this partner:
  - effort 'max' is not accepted by codex. Use one of: high, low, medium.
    ('max' is this skill's own level; 'high' is the real flag.)

$ partner.py check --provider codex --model gpt-9-imaginary --effort high
ok: codex / gpt-9-imaginary / effort=high
  worth knowing:
  - 'gpt-9-imaginary' is not among the ids this machine names for codex
    (gpt-5, gpt-5.4, gpt-5.6...). Not proof it is wrong -- a new model is
    mentioned nowhere locally until it is used once -- but worth checking
    the spelling.
```

Two severities, and the split is the point. A **problem** is something no amount of insisting fixes — a CLI that is not installed, an effort value the API will reject — and it stops the spawn. A **caution** is the plugin not recognising something, which is not evidence of anything: it gets said, and the spawn proceeds. Refusing an unknown model until you passed `--force` meant a model released last week needed a flag to try.

## How it works

```
     you ──────────────┬──────────────────┐
      │   type in any tab; whoever you   │
      │   address claims the write baton │
      ▼                                  ▼
    ┌─────────┐   .partner/chat.md    ┌──────────┐
    │   p1    │◄─────────────────────►│    p2    │  codex, own tab
    │  this   │   append-only         │  claude  │
    │ session │   shared transcript   │  gemini  │
    └─────────┘                       └──────────┘
         ▲                                  ▲
         └──────── roster.json ─────────────┘
                  baton: who may write

    peers, not a hierarchy - p1 spawned p2, and that
    is the only difference between them
```

Each tab runs the provider's ordinary interactive interface, started on a briefing that tells it to loop: block on `wait` until somebody addresses it, think, reply, repeat. Nothing runs headlessly and nothing supervises the agents, so there are no session ids to track and no output formats to parse — which is why any CLI that can run shell commands can join with a one-line template.

`p1` — the session the skill was invoked in — can't block in the foreground without cutting off the human who talks to it through that same session. It runs the identical loop with `wait` as a background command instead, re-armed at the end of every turn, so it stays in the debate even while the human is working in a partner's tab.

Two things follow from a tab having exactly one input, and both are handled in text because nothing else can distinguish them. A partner's **launch prompt** and an automated **wake-up** arrive where your typing arrives, so each opens by disowning itself, and `claim` refuses a claim from an agent that started seconds ago and has not spoken yet (`claim --force` when you really did just address a new partner) — otherwise a fresh partner intermittently reads its own boot message as an instruction and takes the write baton from whoever is working.

## Documentation

- [`skills/partner/SKILL.md`](./skills/partner/SKILL.md) — the workflow
- [`references/providers.md`](./skills/partner/references/providers.md) — provider flags and adding your own
- [`references/protocol.md`](./skills/partner/references/protocol.md) — transcript format, state layout, and the Stop hook
- [`references/terminals.md`](./skills/partner/references/terminals.md) — terminal detection per OS, and typing into a tab that went quiet
- [`references/debate.md`](./skills/partner/references/debate.md) — opening a session and the debate loop
- [`references/troubleshooting.md`](./skills/partner/references/troubleshooting.md) — symptoms, causes and fixes

A `Stop` hook (`hooks/hooks.json`) keeps any agent — the session that ran the skill included — from ending a turn while a message addressed to it is still unanswered in the transcript. It is what stops the non-blocking session agent from going silent the moment the human's attention moves to another tab.

## License

[MIT](../LICENSE)
