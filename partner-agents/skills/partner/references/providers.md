# Provider reference

Every provider is described by one thing: how to run a single headless round — send one prompt, get one reply. Session continuity, streaming and event parsing are deliberately *not* required, because the shared transcript already carries the conversation. That is what keeps adding a new CLI cheap.

The registry lives in `PROVIDERS` near the top of `scripts/partner.py`.

## Built-in providers

| Provider | Model flag | Effort | Read-only (no baton) | Write (baton) | Session resume |
|----------|-----------|--------|----------------------|---------------|----------------|
| `claude` | `--model` | prompt hint | `--permission-mode plan` | `--permission-mode acceptEdits` | yes, via `--resume <id>` |
| `codex`  | `-m`      | `-c model_reasoning_effort="..."` | `--sandbox read-only` | `--sandbox workspace-write` | no — re-seeded from transcript |
| `gemini` | `-m`      | prompt hint | default (no `--approval-mode`) | `--approval-mode yolo` | no — re-seeded from transcript |
| `custom` | `{model}` | `{effort}` | `{readonly}` is `1` | `{readonly}` is `0` | no — re-seeded from transcript |

Run `python partner.py providers` to see which of these are installed on the current machine.

### Effort: flag vs. prompt hint

Only Codex exposes reasoning effort as a real flag. Claude and Gemini have no equivalent, so `--effort` becomes an instruction prepended to the round prompt:

| `--effort` | Prompt hint for providers without a flag |
|-----------|------------------------------------------|
| `low` | Answer directly; keep reasoning brief. |
| `medium` | Think it through before answering. |
| `high` | Think hard. Consider at least two alternatives before answering. |
| `max` | Ultrathink. Stress-test your own position before answering. |

This is an honest approximation, not an equivalent knob. When effort genuinely matters for a comparison, prefer Codex, where it is a real setting.

### Session resume

`resume: "id"` providers get their session id captured from the first round's JSON output and replayed with a resume flag, so the provider keeps its own context.

`resume: "none"` providers receive the last 8 transcript messages inside each round's prompt instead. This costs some tokens but is strictly more robust: it survives provider restarts, session expiry, and CLIs with no resume support at all. If a provider's resume flag ever breaks, switching it to `"none"` is a safe fallback.

## Custom providers

For a CLI that is not in the registry, pass a command template:

```bash
python partner.py spawn --provider custom \
  --cmd 'aider --model {model} --message {msg}' \
  --model gpt-4o --effort high
```

Placeholders, substituted fresh each round:

- `{msg}` — the round prompt. If the template omits it, the prompt is appended as the final argument.
- `{model}` — value of `--model`, or empty.
- `{effort}` — value of `--effort`, or empty.
- `{readonly}` — `1` when the partner does not hold the baton, `0` when it does.

Empty substitutions are dropped, so `--model {model}` with no `--model` does not leave a dangling flag.

The reply is the command's entire stdout. Anything the CLI prints as decoration ends up in the transcript, so prefer a quiet/non-interactive flag if the CLI has one.

**Caveat:** a custom provider cannot enforce the baton unless its template actually uses `{readonly}`. Without that, the baton is a convention the partner is asked to respect rather than a sandbox that stops it. Wire `{readonly}` into a real sandbox flag when the CLI has one.

## Adding a provider to the registry

Add a builder function and one registry entry in `scripts/partner.py`:

```python
def _mytool_round(c: dict) -> list[str]:
    argv = ["mytool", "--non-interactive"]
    if c["model"]:
        argv += ["--model", c["model"]]
    argv += ["--mode", "read" if c["readonly"] else "write"]
    return argv + [c["msg"]]


PROVIDERS["mytool"] = {
    "bin": "mytool",
    "round": _mytool_round,
    "resume": "none",
    "effort": "prompt",
    "models": ["default-model"],
}
```

`round` receives `{msg, model, effort, readonly, session}` and returns an argv list. Nothing else is required.

If the CLI emits JSON containing `session_id`, `conversation_id` or `thread_id` anywhere in its output, set `"resume": "id"` and it will be found and reused automatically.

## Flag drift

These CLIs change flags between releases. When a partner starts failing with `[partner error]`, run its command manually first — the registry is the single place to fix it, and no other file needs to change.
