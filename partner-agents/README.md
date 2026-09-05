# partner-agents

Spawn peer AI agents into new terminal tabs and argue with them before you commit to anything.

A partner is a **separate process** with its own provider, model, and context — not a subagent. Claude can run the debate against Codex, Gemini, or any CLI you can describe in one line, and the disagreement is the product: two models that differ surface the assumptions one model alone glides past.

## What it does

- **Spawns partners into real terminal tabs** on Windows, macOS, and Linux — Windows Terminal, iTerm2, Terminal.app, GNOME Terminal, Konsole, WezTerm, kitty, tmux, and zellij are all detected automatically.
- **Joins mid-work with the context intact.** Spawn a partner twenty exchanges into a hard problem and it arrives knowing the branch, the uncommitted diff, and the argument so far — plus whatever briefing you write. Each partner keeps its own, so adding a third never overwrites what the second was told.
- **Debates every question.** Once a partner is running, the skill puts each question and decision to it, weighs the pushback against the actual code, and reports the argument rather than hiding it.
- **No lead agent.** The session you are typing in registers itself as `p1`, an ordinary peer beside the ones it spawns. The only asymmetry is the write baton, and it moves.
- **Enforces one writer.** All partners *can* edit files; only the one holding the **write baton** does. The baton follows whoever you are talking to, and it is applied as a real sandbox flag per round, not a polite request.
- **Runs as many partners as you want** — `p2`, `p3`, `p4` alongside you, each with its own provider, model and effort, all debating in one shared transcript.
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
/partner baton p2                     # let p2 do the editing
/partner stop --all
```

Then just keep working. Every question after that gets debated before it gets answered.

## Providers

| Provider | Effort control | Read-only enforcement |
|----------|---------------|----------------------|
| `claude` | prompt hint | `--permission-mode plan` |
| `codex` | `model_reasoning_effort` (native) | `--sandbox read-only` |
| `gemini` | prompt hint | default approval mode |
| `custom` | template | your flag, via `{readonly}` |

Anything else works through a one-line template:

```bash
/partner custom --cmd 'aider --model {model} --message {msg}'
```

`python partner.py providers` reports which are installed on the current machine.

## Requirements

- **Python 3.9+** on PATH — the only dependency, and it uses nothing outside the standard library.
- At least one agent CLI installed and authenticated (`claude`, `codex`, `gemini`, or your own).
- A git repository — `.partner/` is anchored at the repo root and auto-excluded via `.git/info/exclude`.

A terminal is *not* required. Without one, partners still run; the skill prints the command to start each in a tab you open yourself.

## How it works

```
        you
         │
    ┌────▼────┐   .partner/chat.md    ┌──────────┐
    │   p1    │◄─────────────────────►│    p2    │  codex, own tab
    │  this   │   append-only         │  watcher │
    │ session │   shared transcript   │   loop   │
    └─────────┘                       └──────────┘
         ▲                                  ▲
         └──────── roster.json ─────────────┘
                  baton: who may write

    peers, not a hierarchy - p1 spawned p2, and that
    is the only difference between them
```

Each partner's tab runs a watcher that polls the transcript, and when something is addressed to it, invokes its provider for one headless round — read-only or writable depending on the baton — and appends the reply. The transcript is the shared memory, which is why a provider with no session-resume support loses nothing.

## Documentation

- [`skills/partner/SKILL.md`](./skills/partner/SKILL.md) — the workflow
- [`references/providers.md`](./skills/partner/references/providers.md) — provider flags and adding your own
- [`references/protocol.md`](./skills/partner/references/protocol.md) — transcript format and state layout
- [`references/terminals.md`](./skills/partner/references/terminals.md) — terminal detection per OS

## License

[MIT](../LICENSE)
