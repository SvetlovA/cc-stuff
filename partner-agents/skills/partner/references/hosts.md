# Claude Code and Codex integration

## Invocation and tools

Invoke `/partner-agents:partner` in Claude Code or `$partner` in Codex. The same arguments, scripts, transcript, and baton protocol apply. Load another skill with Claude's Skill tool when available; otherwise read its installed `SKILL.md` and follow it in the current session. Ask missing configuration with the host's question tool or a direct question. Honor choices already supplied by the user.

Resolve the script from the active skill catalog entry: `<skill-directory>/scripts/partner.py`. Do not assume a cache directory or that hook environment variables are exported in shell calls. Hook commands receive `CLAUDE_PLUGIN_ROOT` in Claude Code and `PLUGIN_ROOT` plus the compatibility alias in Codex. The shared hook entry point also resolves its own location when neither is set.

## Session identity and hooks

Pass `--me-provider claude` or `--me-provider codex` when starting a session. The runtime detects the host when possible, but an explicit provider wins. `init` and `resume` bind the starting participant to `CLAUDE_CODE_SESSION_ID` or `CODEX_THREAD_ID`; hooks compare their payload's `session_id` to that binding. Another session in the checkout must not adopt the starting participant's tab or open its turn. Old rosters containing `cc_session` still identify their owning Claude session.

The shared `Stop` and `UserPromptSubmit` hooks use the event/input/output schema supported by both hosts. Codex requires the installed hooks to be reviewed and trusted before execution. Install the full plugin, then restart the host session. Hook support depends on the client and configuration; an installed skill alone does not guarantee hooks are active. Follow the loop explicitly when hooks are unavailable.

Treat launcher prompts, automated wake-ups, and hook continuation prompts as automation. They never authorize claiming the baton. A real user instruction does: run `claim` before editing.

## Wait in Claude Code

For the starting session, run one `wait` with Bash `run_in_background: true`. A task notification re-invokes the session after completion. Answer messages, then re-arm. Tab partners can run their wait directly. Never kill processes by a shared name to cancel a wait; use `unwait`.

## Wait in Codex

Use the available execution tool's actual asynchronous protocol. With `exec_command`, start one `wait --timeout 90` using a short `yield_time_ms`. If the tool returns a running `session_id`, poll that same process with `write_stdin` until exit. A yield returns control while the process continues; it is not an execution timeout. If `functions.exec` itself yields a `cell_id`, resume that call with `functions.wait` before starting another call for the same work.

Keep the turn active while polling. A process running after a final answer does not by itself guarantee the host will invoke the model again. Use background completion notifications only when the active host explicitly provides them. Process and cell IDs belong to the tool session, not the partner roster.

Entering a bound Codex session's polled wait declares its work finished and clears the open-turn marker, allowing idle pausing even while the harness turn stays active. Delivering a work message reopens that marker. A user prompt arriving while an existing wait runs keeps the turn open until the next finished-work declaration. After the wait exits, handle its messages and re-arm one wait. If it exits with an empty timeout, continue listening. If it reports a pause, stop as directed; if it says it is sleeping through a pause, keep that existing process and poll it. Cancel the current listener with `unwait` before claiming the baton and collecting replies with `send --wait`.

See [Claude manifests](https://code.claude.com/docs/en/plugins-reference), [Codex plugin packaging](https://developers.openai.com/plugins/build/plugins), and [Codex hooks](https://learn.chatgpt.com/docs/hooks) for installation and hook support.
