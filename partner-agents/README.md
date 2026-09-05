# partner-agents

Run several AI agents as equal partners on one repo, each in its own terminal tab, and argue with them before you commit to anything.

A partner is a **separate process** with its own provider, model, and context — not a subagent. Claude can run the debate against Codex, Gemini, or any CLI you can describe in one line, and the disagreement is the product: two models that differ surface the assumptions one model alone glides past.

## What it does

- **Spawns partners into real terminal tabs** on Windows, macOS, and Linux — Windows Terminal, iTerm2, Terminal.app, GNOME Terminal, Konsole, WezTerm, kitty, tmux, and zellij are all detected automatically.
- **Native Orca support.** Running inside [Orca](https://orca.computer)? Partners open as Orca tabs in the current worktree, beside the session that spawned them — not in a detached OS window. Detected automatically, with the normal terminal flow as fallback.
- **Tells you before it fails.** Checks the CLI is installed and actually runs, the effort level is one that provider accepts, and the model is one it knows — each problem reported with the command that fixes it, before a tab is opened.
- **Every partner is a full interactive session.** Walk into any tab and type at that agent directly — it answers you, then goes back to debating. Between your interruptions it drives itself.
- **No permission dialogs to babysit.** Partners start with prompting relaxed (`--auto ask | edits | full`), so you are not approving edits in three tabs at once.
- **Joins mid-work with the context intact.** Spawn a partner twenty exchanges into a hard problem and it arrives knowing the branch, the uncommitted diff, and the argument so far — plus whatever briefing you write. Each partner keeps its own, so adding a third never overwrites what the second was told.
- **Debates every question.** Once a partner is running, the skill puts each question and decision to it, weighs the pushback against the actual code, and reports the argument rather than hiding it.
- **No lead agent, and it is structural.** The session you are typing in registers itself as `p1` with the same config fields, the same briefing document and its own identity wrapper as every agent it spawns — built by the same code, so the two cannot drift. Any partner can spawn more partners; any partner can take the baton. The only asymmetry is the baton, and it moves.
- **Only the partner you just spoke to can write.** The write baton follows your attention: the moment you type an instruction into a tab, that agent claims it and the others are told to stop editing. A partner asking another partner to change something is a suggestion, not an instruction — it does not move the baton. Concurrent edits produce conflicts nobody can see.
- **Runs as many partners as you want** — `p2`, `p3`, `p4` alongside you, each with its own provider, model and effort, all debating in one shared transcript.
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
/partner                              # asks which provider, model, and effort
/partner codex gpt-5-codex high       # or say it up front
/partner list                         # who is running, who holds the baton
/partner check codex gpt-5-codex high # validate without starting anything
/partner baton p2                     # hand editing to p2 deliberately
/partner stop --all
```

Then just keep working. Every question after that gets debated before it gets answered.

## Providers

| Provider | Effort control | Auto-approval at `--auto edits` |
|----------|---------------|----------------------------------|
| `claude` | prompt hint | `--permission-mode acceptEdits` |
| `codex` | `model_reasoning_effort` (native) | `-a never -s workspace-write` |
| `gemini` | prompt hint | `--approval-mode auto_edit` |
| `custom` | template | your own flag, in the template |

Anything else works through a one-line template:

```bash
/partner custom --cmd 'aider --model {model} --yes --message {prompt}'
```

`python partner.py providers` reports which are installed on the current machine.

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
  - effort 'max' is not supported by codex. Use one of: high, low, medium.
    ('max' is this skill's own level; use 'high' for a real flag.)
```

`spawn` runs the same checks and refuses rather than opening a tab that flashes an error and vanishes. Install and effort problems are hard failures; an unrecognised model is soft, since model names change faster than this plugin does — pass `--force` to use one anyway.

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

## Documentation

- [`skills/partner/SKILL.md`](./skills/partner/SKILL.md) — the workflow
- [`references/providers.md`](./skills/partner/references/providers.md) — provider flags and adding your own
- [`references/protocol.md`](./skills/partner/references/protocol.md) — transcript format and state layout
- [`references/terminals.md`](./skills/partner/references/terminals.md) — terminal detection per OS

## License

[MIT](../LICENSE)
