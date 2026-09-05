# Provider reference

Every provider is described by one thing: how to start its **normal interactive session** with a starting prompt and without stopping to ask permission for routine work.

Nothing is ever run headlessly. There are no session ids to track, no streaming events to parse, no output formats to handle — the agent runs its own CLI as a human would, and talks to the others through the transcript. That is what keeps adding a new CLI cheap.

The registry lives in `PROVIDERS` in `scripts/partner.py`.

## Built-in providers

| Provider | Model flag | Effort | Starting prompt |
|----------|-----------|--------|-----------------|
| `claude` | `--model` | prompt hint | positional argument |
| `codex` | `-m` | `-c model_reasoning_effort="..."` | positional argument |
| `gemini` | `-m` | prompt hint | `-i` |
| `custom` | `{model}` | `{effort}` | `{prompt}` |

Run `python partner.py providers` to see which are installed on the current machine.

## Auto levels

A partner sits in its own tab. A permission prompt there is easy to miss and silently stalls the debate, so partners launch with prompting relaxed. `--auto` chooses how far:

| `--auto` | claude | codex | gemini |
|----------|--------|-------|--------|
| `ask` | *(no flag)* | *(no flag)* | *(no flag)* |
| `edits` *(default)* | `--permission-mode acceptEdits` | `-a never -s workspace-write` | `--approval-mode auto_edit` |
| `full` | `--permission-mode bypassPermissions` | `--dangerously-bypass-approvals-and-sandbox` | `--approval-mode yolo` |

`edits` is the working default: file edits go through, but the CLI's sandbox still applies to commands. `full` removes the sandbox too — reasonable inside a container or a throwaway clone, not on a machine you care about.

**This is why the baton is a convention rather than a sandbox.** At `edits` or `full` every partner is technically able to write. The baton is enforced by every agent's briefing telling it to check `list` first, not by a flag. If you need hard enforcement more than you need an unattended partner, spawn with `--auto ask` — the CLI will then stop and ask before it writes anything.

The claude and codex flags were verified against the installed CLIs; the gemini row follows its documented flags but is unverified here.

## Effort

Only Codex exposes reasoning effort as a real flag. For Claude and Gemini, `--effort` becomes a line in the briefing:

| `--effort` | Line added for providers without a flag |
|-----------|------------------------------------------|
| `low` | Answer directly and keep reasoning brief. |
| `medium` | Think things through before answering. |
| `high` | Think hard. Consider at least two alternatives before answering. |
| `max` | Ultrathink. Stress-test your own position before you put it forward. |

An honest approximation, not an equivalent knob. When effort genuinely matters for a comparison, prefer Codex, where it is a real setting.

## Custom providers

For a CLI that is not in the registry:

```bash
python partner.py spawn --provider custom --model gpt-4o --effort high \
  --cmd 'aider --model {model} --yes --message {prompt}'
```

Placeholders:

- `{prompt}` — the starting prompt, which tells the agent to read its briefing. If the template omits it, the prompt is appended as the final argument.
- `{model}` — value of `--model`, or empty.
- `{effort}` — value of `--effort`, or empty.
- `{auto}` — the literal auto level (`ask`/`edits`/`full`), if the CLI can use it.

Empty substitutions are dropped, so `--model {model}` with no `--model` leaves no dangling flag.

**Put the CLI's own non-interactive flag in the template** (`--yes` above). `--auto` maps onto flags the registry knows; for a custom CLI it can only be passed through as `{auto}`, so if the CLI keeps prompting, the partner will sit there waiting.

The agent also needs to be able to run shell commands, since that is how it calls `wait` and `send`. A CLI that cannot run commands can read the transcript but cannot take part.

## Adding a provider to the registry

Add a builder and one entry in `scripts/partner.py`:

```python
def _mytool_tui(c: dict) -> list[str]:
    argv = ["mytool"]
    if c["model"]:
        argv += ["--model", c["model"]]
    if c["auto"] in ("edits", "full"):
        argv += ["--no-confirm"]
    return argv + [c["prompt"]]


PROVIDERS["mytool"] = {
    "bin": "mytool",
    "tui": _mytool_tui,
    "effort": "prompt",          # or "flag" if the CLI has a real one
    "models": ["default-model"],
}
```

`tui` receives `{prompt, model, effort, auto}` and returns an argv list. Nothing else is required.

## Flag drift

These CLIs change flags between releases. When a partner's tab shows a usage error instead of starting, run its command by hand first — `.partner/<id>/run.cmd` (or `run.sh`) holds the exact command that was used. The registry is the single place to correct it, and no other file needs to change.
