# Provider reference

Every provider is described by one thing: how to start its **normal interactive session** with a starting prompt and without stopping to ask permission for routine work.

Nothing is ever run headlessly. There are no session ids to track, no streaming events to parse, no output formats to handle — the agent runs its own CLI as a human would, and talks to the others through the transcript. That is what keeps adding a new CLI cheap.

## Nothing here is a fixed list

There is no blessed set of providers and no bundled model catalog. Both go stale the week they are written, and the user pays: a partner they cannot start, or a model they are never offered because a table in `partner.py` has not been edited since it shipped.

Four sources, in the order they are consulted:

| Source | What it supplies | Where it lives |
|--------|------------------|----------------|
| **Recipe** | launch flags verified by hand | `RECIPES` in `scripts/partner.py` |
| **Your config** | CLIs described once and kept | `~/.claude/partner-providers.json`, `.partner/providers.json` |
| **Probe** | flags read from the CLI's own `--help` | the CLI itself, cached |
| **`--cmd`** | the exact command, ad hoc | the invocation |

A recipe is an *accelerator*, not a permission list. Every path falls through to probing when a name is not in it, so `spawn --provider <anything-on-this-machine>` works.

## Finding the CLIs

```bash
python partner.py providers          # what is here
python partner.py providers --deep   # also probe binaries whose names say nothing
```

The scan looks in PATH **and** in the per-user directories package managers actually install into — `~/.local/bin`, `~/.bun/bin`, `~/.cargo/bin`, `~/go/bin`, npm's prefix, winget's link dir, and one or two levels under `%LOCALAPPDATA%\Programs`. PATH alone is not enough: Codex installs to `%LOCALAPPDATA%\Programs\OpenAI\Codex\bin`, which a login shell picks up and an agent's inherited environment often does not. Anything the scan resolves is remembered in the cache, so a CLI that `shutil.which` cannot see is still nameable afterwards.

**Behaviour decides what gets listed, not the name.** Each candidate is asked for its `--help` and kept only if that help reads like an agent CLI: it mentions a model, it accepts a task in words, and at least three of the operational signals fire (a model flag, agent/chat vocabulary, sessions, an approval mechanism, tools/MCP). Name alone was tried and it failed in both directions — it let in `gpg-agent` and `ssh-agent`, and it would still miss any CLI whose name gives nothing away.

The load-bearing check is **"can it be handed a task in words"**, read from the *usage line* only. A browser driver built for agents has `--model`, sessions and an approve command, and every other signal fires — but it is driven by `open <url>` and `click <sel>`, so there is no way to tell it what the argument is about. `claude [options] [prompt]` and `codex [OPTIONS] [PROMPT]` say the prompt is what the CLI is for.

`--deep` drops the name prefilter and probes every binary in those directories instead. It costs a subprocess each, so it is opt-in and time-bounded.

System directories are skipped. Nobody installs an agent CLI into `System32` or `/usr/sbin`, and scanning them buries the output in `MBR2GPT` and `logagent`.

## Driving a CLI nobody wrote a recipe for

```bash
python partner.py probe --provider <name-or-path>
```

Shows what was read out of its help, and the exact command a spawn would run:

| Read from help | Used for |
|----------------|----------|
| `--model` / `-m` | the model flag |
| `--reasoning-effort`, `--effort`, `--thinking*` | a real effort flag, and the values it lists |
| `--yolo`, `--dangerously-*`, `--permission-mode`, `--approval-mode`, `--yes`, `--auto-edit`, … | the auto levels |
| a prompt positional, or `--prompt`/`--message`/`--task`/`-i` | how the starting prompt is passed |
| a `models` line under a **Commands:** heading | a model-list command |

Probing is conservative on purpose: **a flag that was not found is a flag that is not passed.** A partner launched with no model flag runs on the CLI's own default model, which is a working tab. A partner launched with a guessed flag is a tab that prints a usage error and closes.

There is deliberately **no fallback from `edits` to the `full` flag**. A CLI that only advertises a sandbox-bypass flag gets nothing for `--auto edits`, and the partner stops to ask in its tab — annoying, and visible. Substituting the bypass flag would silently turn "accept edits" into "remove the sandbox" on a CLI nobody verified.

Results are cached against the binary's path, size and mtime, so upgrading the CLI invalidates the entry by itself.

`-p` and `--print` are never used as prompt flags: on several CLIs they mean *run headless and exit*, which opens a tab that finishes before anybody can type in it.

## Built-in recipes

| Provider | Model flag | Effort | Accepts effort | Starting prompt | Install |
|----------|-----------|--------|----------------|-----------------|---------|
| `claude` | `--model` | prompt hint | low, medium, high, max | positional | `npm install -g @anthropic-ai/claude-code` |
| `codex` | `-m` | `-c model_reasoning_effort="..."` | low, medium, high | positional | `npm install -g @openai/codex` |
| `gemini` | `-m` | prompt hint | low, medium, high, max | `-i` | `npm install -g @google/gemini-cli` |

The claude and codex rows were verified against the installed CLIs; the gemini row follows its documented flags but is unverified here.

## Auto levels

A partner sits in its own tab. A permission prompt there is easy to miss and silently stalls the debate, so partners launch with prompting relaxed. `--auto` chooses how far:

| `--auto` | claude | codex | gemini | probed CLI |
|----------|--------|-------|--------|------------|
| `ask` | *(no flag)* | *(no flag)* | *(no flag)* | *(no flag)* |
| `edits` *(default)* | `--permission-mode acceptEdits` | `-a never -s workspace-write` | `--approval-mode auto_edit` | whatever its help offers at that level, or nothing |
| `full` | `--permission-mode bypassPermissions` | `--dangerously-bypass-approvals-and-sandbox` | `--approval-mode yolo` | its bypass flag, if it has one |

`edits` is the working default: file edits go through, but the CLI's sandbox still applies to commands. `full` removes the sandbox too — reasonable inside a container or a throwaway clone, not on a machine you care about.

**This is why the baton is a convention rather than a sandbox.** At `edits` or `full` every partner is technically able to write. The baton is enforced by every agent's briefing telling it to check `list` first, not by a flag. For hard enforcement, spawn with `--auto ask` — the CLI will then stop and ask before it writes anything.

## Choosing a model

```bash
python partner.py models [--provider X] [--deep] [--no-probe]
```

Every id printed was **found on this machine**. Nothing is recited from a list in the script, because such a list is wrong within weeks and its wrongness is invisible — the user simply never sees the model that shipped last month.

Sources, strongest first:

| Rank | Source | Shown as |
|------|--------|----------|
| named | the CLI's own `models` command | `the CLI's own model list` |
| named | a key called `*model*` in the user's config for that CLI | `a model setting in your config.toml` |
| mentioned | the shape of a model id in help text, a config file, or the CLI's program files | `text in your .claude.json`, `the CLI's own bundle` |

The split is printed because it is the whole caveat: a model list command is evidence, a string in a bundle is a lead. "Mentioned" ids are grouped under a heading that says outright that some of them are not models.

### What counts as a model id

One shape, not one pattern per vendor: a lowercase family token followed by segments, at least one of which carries a version number — `gpt-5.1`, `claude-opus-5`, `gemini-3-pro`, `llama3:8b`, `qwen2.5-coder`. Anchoring on the shape rather than on a vendor prefix is what lets a model released after this file was written be found at all.

Then filtered: no segment longer than 20 characters, no run of 8+ hex characters (session ids and UUIDs have exactly the shape of a model id, and config directories are full of them), no known tooling segment (`claude-code`, `gemini-cli`), nothing matching a secret prefix.

**Credential files are never opened.** Everything found is printed into a transcript every partner reads, so a file whose whole purpose is holding a secret is not somewhere to go looking for model names. A secret-shaped token is filtered as a backstop.

### Ordering and the tier note

Newest-looking first, by the version numbers in the id read *in order* — `claude-opus-4-7` has a 7 in it and is older than `claude-opus-5`.

The one-line character note is derived from the **name shape**, not from an id table:

| Shape | Suggested effort | Reads as |
|-------|-----------------|----------|
| `opus`, `ultra`, `max`, `large`, `xl` | high | top of its family: deepest, strongest challenger, slowest |
| `sonnet`, `pro`, `medium`, `turbo`, `plus` | high | the balanced tier: fast enough to argue with in real time |
| `codex`, `coder`, `code` | high | code-tuned: sharper on diffs than on open design questions |
| `think`, `reason`, `r1`, `o1`–`o9` | high | reasoning-tuned: slow, hard to talk out of a position |
| `haiku`, `flash`, `mini`, `lite`, `nano`, `8b` | low | fast and cheap; concedes too easily to be much of an opponent |

Shapes stay true for models that do not exist yet, which is the entire reason it is phrased this way.

### The limit, and what to do about it

A model released after the installed CLI was built is mentioned **nowhere locally** until it is used once. `models` says so in its own output. When the newest id looks dated or the list comes back thin, look up the provider's current lineup on the web and offer those too — they spawn with `--model` whether or not they appear in the scan.

The skill's rule is that the **human chooses**. `models` recommends; it does not decide. A partner the user did not pick is one they will not believe when it disagrees with them.

## Validation

`check` runs the same rules `spawn` does, without starting anything:

```bash
python partner.py check --provider codex --model gpt-5.6 --effort high
```

Two severities, and the split is the point:

| Severity | Meaning | Effect |
|----------|---------|--------|
| **problem** | nothing fixes it but fixing it — CLI not installed, effort value the API rejects | stops the spawn |
| **caution** | this script not recognising something | printed, then the spawn proceeds |

| Checked | How | Severity |
|---------|-----|----------|
| CLI installed | PATH, then the scan's remembered paths | problem, listing what *is* installed |
| CLI actually runs | `<bin> --version` exits 0 | caution |
| Effort supported | against the provider's accepted set | problem, listing the accepted values |
| Effort is real | whether the CLI has a flag for it | caution |
| Model recognised | against what `models` finds locally | caution |
| Flags were guessed | whether the provider came from a probe | caution, naming the flags derived |

**An unrecognised model no longer blocks.** Model names move faster than any check here, and refusing until `--force` meant a model released last week needed a flag to try. `--force` still exists and now only silences the caution.

Effort is the check that earns its keep as a hard failure. `max` is this plugin's own level and works for Claude and Gemini, where effort is a prompt hint — but Codex passes it to a real API field that rejects it. Without the check, that surfaces as a tab that flashes an error and disappears.

## Custom CLIs

Two ways, and neither needs the CLI to be known.

### Ad hoc — one invocation

```bash
python partner.py spawn --provider custom --model gpt-4o --effort high \
  --cmd 'aider --model {model} --yes --message {prompt}'
```

Placeholders:

- `{prompt}` — the starting prompt, which tells the agent to read its briefing. A template without it gets the prompt appended last.
- `{model}` — value of `--model`, or empty.
- `{effort}` — value of `--effort`, or empty.
- `{auto}` — the literal auto level (`ask`/`edits`/`full`), if the CLI can use it.

Empty substitutions are dropped, so `--model {model}` with no `--model` leaves no dangling flag.

**Put the CLI's own non-interactive flag in the template** (`--yes` above). `--auto` maps onto flags a recipe or a probe knows; in a template it can only be passed through as `{auto}`, so if the CLI keeps prompting, the partner will sit there waiting.

### Kept — a CLI used often

Describe it once in `~/.claude/partner-providers.json` (or `.partner/providers.json` for one repo; repo entries win):

```json
{
  "aider": {
    "cmd": "aider --model {model} --yes --message {prompt}",
    "efforts": ["low", "medium", "high"],
    "install": "pipx install aider-chat",
    "note": "terse, diff-focused; good at spotting an over-broad change"
  }
}
```

It then appears in `providers`, validates against its own `efforts`, and spawns as `--provider aider` with no template on the command line. Same substitution code as `--cmd`, so there is one template language, not two.

The file is optional in every sense — it exists so a CLI used often does not have to be retyped.

### What a partner CLI must be able to do

Run shell commands. That is how it calls `wait` and `send`. A CLI that cannot run commands can read the transcript but cannot take part in the debate.

## Adding a recipe

Worth doing only for a CLI whose flags the probe gets wrong. Add a builder and one entry in `scripts/partner.py`:

```python
def _mytool_tui(c: dict) -> list[str]:
    argv = ["mytool"]
    if c["model"]:
        argv += ["--model", c["model"]]
    if c["auto"] in ("edits", "full"):
        argv += ["--no-confirm"]
    return argv + [c["prompt"]]


RECIPES["mytool"] = {
    "bin": "mytool",
    "tui": _mytool_tui,
    "effort": "prompt",           # or "flag" if the CLI has a real one
    "efforts": {"low", "medium", "high", "max"},   # what validation accepts
    "install": "npm install -g mytool",
    "login": "mytool auth",
}
```

`tui` receives `{prompt, model, effort, auto}` and returns an argv list. No model list is needed — that comes from the machine.

## Effort

Only a CLI with a real reasoning-effort flag gets one. Everywhere else `--effort` becomes a line in the briefing, and `check` says so:

| `--effort` | Line added for providers without a flag |
|-----------|------------------------------------------|
| `low` | Answer directly and keep reasoning brief. |
| `medium` | Think things through before answering. |
| `high` | Think hard. Consider at least two alternatives before answering. |
| `max` | Ultrathink. Stress-test your own position before you put it forward. |

An honest approximation, not an equivalent knob. When effort genuinely matters for a comparison, prefer a CLI where it is a real setting — `probe` says which those are.

## Flag drift

These CLIs change flags between releases. When a partner's tab shows a usage error instead of starting, run its command by hand first — `.partner/<id>/run.cmd` (or `run.sh`) holds the exact command that was used, and `probe --provider <name> --refresh` shows what the current help says. Correct a recipe in `partner.py`, or override it for good with a `providers.json` entry; no other file needs to change.
