# cc-stuff — Claude Code Plugin Marketplace

A public collection of Claude Code plugins for developer workflows. Install any plugin directly into Claude Code to extend its capabilities.

## Available Plugins

| Plugin | Description | Version |
|--------|-------------|---------|
| [partner-agents](./partner-agents/) | Run AI agents as equal partners in their own terminal tabs (Orca-aware), debating every decision with you, with an exclusive write baton | 0.3.0 |
| [pr-review-toolkit](./pr-review-toolkit/) | Fetch active PR review comments, apply code fixes, and resolve threads automatically | 0.6.1 |

## Installing a Plugin

### Install from GitHub

```bash
cc plugin install https://github.com/SvetlovA/cc-stuff/tree/master/<plugin-name>
```

### Install locally (for development or testing)

```bash
git clone https://github.com/SvetlovA/cc-stuff.git
cc --plugin-dir ./cc-stuff/<plugin-name>
```

## Plugin Overview

### [partner-agents](./partner-agents/)

Runs several AI agents as equal partners, each in its own terminal tab, so decisions get argued before they get made:

- Launches a partner on **any provider** — `claude`, `codex`, `gemini`, or any CLI via a one-line `--cmd` template — with its own model and effort level
- Opens a real tab on **Windows, macOS, and Linux** (Windows Terminal, iTerm2, Terminal.app, GNOME Terminal, Konsole, WezTerm, kitty, tmux, zellij), and prints a manual command when there is no terminal
- Every agent is a **full interactive session** — interrupt any tab and type at that partner directly; it answers you, then resumes debating
- Starts partners with **permission prompting relaxed** (`--auto ask|edits|full`) so nobody is answering dialogs in three tabs at once
- Opens partners as **Orca tabs** in the current worktree when running inside Orca, and falls back to the platform terminal everywhere else
- **Validates before spawning**: CLI installed and runnable, effort level accepted by that provider, model recognised — each failure reported with its fix
- Passes a **handoff briefing** so the partner joins mid-work already knowing what was decided and what is open
- Puts **every question and decision** to the partners, weighs the pushback against the code, and reports the disagreement instead of hiding it
- Keeps a **write baton** that follows your attention: type into a tab and that agent claims it, so only the partner you just spoke to edits files — one partner telling another to change something does not move it
- Runs **as many partners as you want**, all debating in one shared `.partner/chat.md` transcript

**Invoke with:** `/partner [provider] [model] [effort]`, plus `/partner list`, `/partner baton <id>`, `/partner stop`

**Requires:** Python 3.9+ and at least one agent CLI installed

---

### [pr-review-toolkit](./pr-review-toolkit/)

Addresses GitHub PR review comments end-to-end:

- Detects the current PR or lets you pick from a list
- Fetches **all active** inline threads and general comments (skips resolved and already-replied threads; outdated threads are included and labeled)
- Evaluates scope and recommends **address on the fly** (simple, few files) or **create a plan first** (complex, cross-file, architectural)
- On the fly: applies fixes and resolves threads in one pass, then prompts to commit
- Plan mode: delegates to `/planning:make` (optional dependency) to produce a structured plan ending with thread resolution and `git push`

**Invoke with:** `/pr-review-toolkit:address-review-comments [pr-number]`

**Requires:** `gh` CLI authenticated

---

## Contributing

Contributions welcome! To add a plugin:

1. Fork this repo
2. Create a new directory at the repo root: `my-plugin-name/`
3. Follow the [plugin structure](CLAUDE.md) — at minimum a `.claude-plugin/plugin.json` and at least one skill or component
4. Add a `README.md` for your plugin
5. Update the table above
6. Open a PR

See [CLAUDE.md](./CLAUDE.md) for conventions around skill writing, naming, and path portability.

## License

[MIT](./LICENSE)
