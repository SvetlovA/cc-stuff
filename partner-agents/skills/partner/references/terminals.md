# Terminal tabs

`spawn` opens each partner in its own terminal tab. The tab runs the provider's normal interactive interface, so the user can read the debate as it happens and type at that agent directly at any moment.

## The runner-script indirection

Quoting a nested agent command through `wt.exe`, `osascript` and `gnome-terminal` is where cross-platform launchers normally break — each has its own quoting rules, and `wt` additionally treats `;` as a command separator.

So the real command is never passed through the terminal at all. `spawn` writes it into a per-partner runner script:

```
.partner/<id>/run.cmd    # Windows
.partner/<id>/run.sh     # macOS / Linux
```

Every terminal then only has to run one plain file path with no arguments. Adding a new terminal means adding one line, and no escaping is involved.

## Selection order

The first available option wins.

**0. Orca** — checked before everything else. When this session is running inside Orca, a detached OS terminal would put the partner outside the workspace the user is actually looking at, so the tab has to be created by Orca itself:

```bash
orca terminal create --worktree path:<repo> --title partner:<id> --command "<runner>" --json
```

Detection is by the environment Orca sets for processes it launches — `ORCA_WORKTREE_ID`, `ORCA_TERMINAL_HANDLE` or `ORCA_TAB_ID`. The binary is taken from `orca` on PATH, falling back to the path in `ORCA_CODEX_LAUNCH_PREFLIGHT`. If any of that is missing, or the call fails, the normal selection below continues — Orca is a preference, not a requirement.

The partner lands as a tab in the current worktree, without stealing focus.

**1. Multiplexers** — checked first, because if the user is already inside one, a real tab costs nothing and stays inside their existing window.

| Condition | Command |
|-----------|---------|
| `$TMUX` set | `tmux new-window -n <title> -c <cwd> "bash run.sh"` |
| `$ZELLIJ` set | `zellij run --name <title> --cwd <cwd> -- bash run.sh` |

**2. Terminals with a control CLI** — work identically on all three platforms.

| Condition | Command |
|-----------|---------|
| `wezterm` on PATH | `wezterm cli spawn --cwd <cwd> -- bash run.sh` |
| `kitty` on PATH and `$KITTY_LISTEN_ON` set | `kitty @ launch --type=tab --tab-title <title> --cwd <cwd> bash run.sh` |

`kitty` requires `allow_remote_control yes` in `kitty.conf`; the `$KITTY_LISTEN_ON` check avoids trying when it is off.

**3. Platform defaults**

| Platform | Preferred | Fallback |
|----------|-----------|----------|
| Windows | `wt.exe -w 0 nt --title <title> -d <cwd> cmd.exe /k run.cmd` | `cmd.exe /c start` (new window) |
| macOS | iTerm2 via `osascript`, when `/Applications/iTerm.app` exists | `Terminal.app` via `osascript do script` |
| Linux | `gnome-terminal --tab` | `konsole --new-tab`, `xfce4-terminal --tab`, `terminator`, `alacritty`, `xterm` |

`wt -w 0` targets the *current* Windows Terminal window, so partners appear as tabs beside the session that spawned them rather than in new windows.

On Linux, only GNOME Terminal, Konsole and Xfce Terminal give real tabs; the rest open separate windows. That is a cosmetic difference — the transcript works the same either way.

## When no terminal is available

Headless servers, SSH sessions without a display, and containers have no terminal to open. `spawn` still registers the partner and prints the exact command to run:

```
p2 (codex/gpt-5-codex/high, auto=edits) registered, but no terminal could be opened.
Open a tab yourself and run:
  bash "/repo/.partner/p2/run.sh"
```

Relay that command to the user rather than reporting the spawn as failed — everything except the tab worked, and the partner starts participating the moment that command runs.

`--no-tab` requests this deliberately, which is also the right choice when driving partners from a script or a CI job.

## Verifying a tab

`python partner.py providers` ends with a line saying where partners will open, so it can be checked before spawning anything:

```
Partners will open in Orca tab.
```

`python partner.py list` shows the launch method that was actually used per partner:

```
  p2       codex    gpt-5-codex   effort=high   auto=edits [running] Orca tab
```

An empty method column means the agent is registered but its runner has not been started.
