# cc-stuff — Claude Code and Codex Plugin Marketplace

A public collection of plugins for developer workflows. See each plugin for its supported hosts and installation instructions.

## Available Plugins

| Plugin | Description | Version |
|--------|-------------|---------|
| [partner-agents](./partner-agents/) | Run AI agents as equal partners in their own terminal tabs (Orca-aware), debating every decision with you, with an exclusive write baton | 1.1.0 |
| [pr-review-toolkit](./pr-review-toolkit/) | Address supplied review feedback or fetch GitHub PR and Orca editor comments in Claude Code and Codex | 0.7.1 |

## Installing a Plugin

### Claude Code

```bash
claude plugin marketplace add SvetlovA/cc-stuff
claude plugin install partner-agents@SvetlovA-cc-stuff
claude plugin install pr-review-toolkit@SvetlovA-cc-stuff
```

Invoke skills with `/partner-agents:partner` or `/pr-review-toolkit:address-review-comments`.

### Codex

```bash
codex plugin marketplace add SvetlovA/cc-stuff
codex plugin add partner-agents@SvetlovA-cc-stuff
codex plugin add pr-review-toolkit@SvetlovA-cc-stuff
```

Invoke skills with `$partner` or `$address-review-comments`. Codex in the desktop app can also install from the registered marketplace through its Plugins UI. Review and trust the partner plugin's hooks in Codex before using them. See [Codex packaging](https://developers.openai.com/plugins/build/plugins) for marketplace and hook support.

### Local development

```bash
git clone https://github.com/SvetlovA/cc-stuff.git
cd cc-stuff
claude --plugin-dir ./partner-agents --plugin-dir ./pr-review-toolkit
```

For Codex, register the checkout with `codex plugin marketplace add .`, then install the plugins with the same `codex plugin add` commands above. Restart the host session after changing installed plugin files.

Both hosts use the same skills and scripts. Claude reads `.claude-plugin/marketplace.json`; Codex reads `.agents/plugins/marketplace.json`. Each plugin includes a manifest for each host.

## Plugin Overview

### [partner-agents](./partner-agents/)

Runs several AI agents as equal partners, each in its own terminal tab, so decisions get argued before they get made:

- Launches a partner on **any agent CLI on your machine** — found by scanning PATH and the per-user install directories, then driven from its own `--help` if there is no built-in recipe; or name the exact command with a one-line `--cmd` template
- **Offers models it found, not models it remembers** — read from the CLI's own model list, your config for it, its help and its program files, grouped by how much each source proves, and checked against the web when the local answer looks dated
- Opens a real tab on **Windows, macOS, and Linux** (Windows Terminal, iTerm2, Terminal.app, GNOME Terminal, Konsole, WezTerm, kitty, Ghostty, foot, Rio, Alacritty, tmux, zellij, xterm), and prints a manual command when there is no terminal — and a terminal it has never heard of works too — whatever hosts your session names itself in the environment and is driven from flags read out of its own `--help`, with a per-OS default terminal behind that so a spawn always lands somewhere; `terminals` lists what can open a tab and which wins, `--terminal <name>` forces one, and a JSON entry adds or fixes any terminal without touching the plugin
- Every agent is a **full interactive session** — interrupt any tab and type at that partner directly; it answers you, then resumes debating
- Starts partners with **permission prompting relaxed** (`--auto ask|edits|full`) so nobody is answering dialogs in three tabs at once
- **Genuinely symmetric**: the session you start from registers itself as an ordinary partner with the same config, the same briefing and its own identity — begin from any tab, and only that partner writes
- Opens partners as **Orca tabs** in the current worktree when running inside Orca, and falls back to the platform terminal everywhere else
- **Validates before spawning**: CLI installed and runnable, effort level accepted by that provider, model recognised — each failure reported with its fix
- Passes a **handoff briefing** so the partner joins mid-work already knowing what was decided and what is open
- **Asks what to start on once the partners are up** — any text, or another slash command to run through the debate; say nothing and the agents spend one round reading the repository and agreeing on what it is, editing nothing, before waiting for you
- Puts **every question and decision** to the partners, weighs the pushback against the code, and reports the disagreement instead of hiding it
- **No message is delivered once and forgotten** — `wait` checks the transcript for anything addressed to an agent and still unanswered, not just what its cursor has not seen, so a message dropped by a killed wait or an ended turn comes back on its own instead of needing another partner to notice
- **Keeps partners in the loop** — every agent CLI caps how long one command may run, and a cap shorter than a blocking `wait` used to end an agent's loop for good; each partner is now briefed on that rule whatever it runs, with its own CLI's measured cap where there is one, and any agent that stops listening gets a wake-up typed into its tab (Orca, tmux, WezTerm, kitty)
- Keeps a **write baton** that follows your attention: type into a tab and that agent claims it, so only the partner you just spoke to edits files — one partner telling another to change something does not move it
- Runs **as many partners as you want**, all debating in one shared `.partner/chat.md` transcript

- One `spawn` call registers you **and** starts the partner, so the arrangement cannot be left half-built
- **Liveness-aware**: heartbeats tell it whether partners are really running, so a partner whose tab you closed shows as stale rather than trusted to be alive
- **Session history**: `/partner` starts a new session and archives the last one whole (a no-op if nothing was running); `/partner add` joins the debate in progress; `resume` rebuilds every agent with its model and re-briefs it from the transcript, continuing the argument rather than restarting it

**Claude Code:** `/partner-agents:partner [provider] [model] [effort]`

**Codex:** `$partner [provider] [model] [effort]`

Use `add`, `resume`, `list`, `baton`, or `stop` after the skill name to manage a session.

**Requires:** Python 3.9+ and at least one agent CLI installed

---

### [pr-review-toolkit](./pr-review-toolkit/)

Addresses review comments through one shared fixing workflow and two fetching wrappers:

- `address-review-comments` accepts review text, structured arguments, or a comments file without fetching
- `gh-address-review-comments` detects/selects a PR, fetches active inline threads and general comments, then passes them to the shared skill
- `orca-address-review-comments` fetches notes left directly in Orca's editor/diff or Markdown preview, then passes them to the shared skill
- The shared skill summarizes feedback, recommends on-the-fly fixes or planning, applies changes, and reports validation results
- Resolution, replies, Orca note cleanup, commits, and pushing remain manual

**Claude Code:** `/pr-review-toolkit:address-review-comments <comments>`, `/pr-review-toolkit:gh-address-review-comments [pr-number]`, `/pr-review-toolkit:orca-address-review-comments [worktree-selector]`

**Codex:** `$address-review-comments <comments>`, `$gh-address-review-comments [pr-number]`, `$orca-address-review-comments [worktree-selector]`

**Requires:** supplied feedback for the shared skill; authenticated `gh` for the GitHub wrapper; a reachable Orca runtime/CLI for the Orca wrapper. See the [plugin README](./pr-review-toolkit/README.md) for installation in each host.

---

## Contributing

Contributions welcome! To add a plugin:

1. Fork this repo
2. Create a new directory at the repo root: `my-plugin-name/`
3. Follow the [plugin structure](AGENTS.md) — include matching Claude Code and Codex manifests and at least one skill or component
4. Add a `README.md` for your plugin
5. Add entries to both marketplace registries and update the table above
6. Open a PR

See [AGENTS.md](./AGENTS.md) for conventions around skill writing, naming, and path portability.

## License

[MIT](./LICENSE)
