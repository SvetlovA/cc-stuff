# When things go wrong

**Partners disappeared mid-argument.** A bare `/partner` starts a new session and archives the running one — that is what it is for. Nothing is lost: `sessions` lists it, `resume --session <id>` brings it back with its agents. If the user wanted to keep the debate going, they meant `/partner add`.

**The user expected their old partners back.** They wanted `resume`, not a fresh `/partner`. Nothing is lost — `sessions` lists what was archived, and `resume --session <id>` rebuilds it.

**The current session was not archived on a fresh `/partner`.** Expected when the roster held only this session with no real discussion — there is nothing to archive, so `--fresh` just adds the partner. Once a partner has joined or anything substantive is in `chat.md`, the next fresh `/partner` archives it.

**Only yourself in `list` after spawning.** The spawn never ran. Registering yourself is not the deliverable — go back and run `spawn`.

**Spawn refused with a list of problems.** That is validation, not a crash — each line ends with the command that fixes it. Only hard problems refuse; anything under "worth knowing" is a caution and the spawn went ahead.

**The CLI the user wants is not in `providers`.** The scan keeps only binaries whose `--help` reads like an agent CLI, and it skips system directories. Try `providers --deep`, which probes by behaviour instead of by name. Failing that the list is not a gate: `spawn --provider <bin>` works on any binary, with flags read from its help — run `probe --provider <bin>` first to see what those will be.

**A partner's tab opens on a usage error and closes.** Its flags were probably derived from `--help` and got something wrong. `probe --provider <name>` prints the exact command a spawn builds; `.partner/<id>/run.cmd` (or `run.sh`) holds the one that was actually used. Correct it with `--provider custom --cmd '<the right command with {prompt}>'`, or keep the fix in `~/.claude/partner-providers.json`.

**A partner keeps stopping to ask permission.** Its CLI advertised no accept-edits flag, so `--auto edits` passed nothing — deliberately, since substituting a sandbox-bypass flag would silently do far more than was asked. `probe` shows which auto levels it found. Either pass the CLI's own flag in a `--cmd` template, or spawn with `--auto full` if that is genuinely wanted.

**`models` lists junk, or nothing.** Ids under "only the shape of a model id" are text that merely looks like a model — that heading is the warning. An empty list means nothing on this machine names a model for that CLI: its default model still works (spawn without `--model`), or look the current lineup up on the web and pass one. A model released after the CLI was built is mentioned nowhere locally.

**A model the user knows exists is flagged as unrecognised.** Expected, and it does not block — the check compares against what this machine happens to name, which is not what exists. Spawn it.

**Partner never replies.** Check its tab: a permission prompt (raise `--auto`), an error, or simply between `wait` calls.

**Partner opens by answering the whole backlog, then goes quiet.** Its cursor started at zero, so its first `wait` returned the entire transcript instead of blocking for the question. `spawn` sets the cursor to the end of `chat.md` right after the join message to prevent this; if you see it, check `.partner/<id>/cursor` exists and is non-zero, and that the join `system` message is in the transcript.

**Partner stopped looping.** Interactive agents sometimes end their turn instead of running `wait` again — most often right after a discussion concludes, or after the baton moves to someone else. Type "continue" in its tab, or `send` it a message. `.partner/<id>/waiting` exists only while a `wait` is actually polling, so its absence is the check. For a Claude tab or the session agent the Stop hook blocks that turn from ending; a codex or gemini tab has no such hook and needs the nudge.

**This session stays silent while the user works in a partner's tab.** Almost always this: it spawned the partner, reported success, and ended the turn without ever entering the loop — so no background `wait` was ever armed and nothing re-invokes it. Check `.partner/<id>/lastseen`: **missing, or minutes old, means this session is deaf**, whatever `roster.json` claims. An empty `.partner/<id>/cursor` confirms it has never even read the transcript. Fix: `read`, then `wait --timeout 600` as a background command, re-armed at the end of every turn after. SKILL step 4 makes that the closing move of `/partner`, and the `Stop` hook blocks the turn from ending while the stamp is stale.

**The Stop hook is not firing.** `hooks.json` loads once at session start — a session open before the plugin was installed will not have it; restart. It also needs `bash` and Python on `PATH`. Test from the repo root: `python <plugin>/skills/partner/scripts/partner.py hook-stop < /dev/null` prints nothing and exits 0 when nothing is owed. A `.partner/<id>/.stop-nag` file that matches the current `chat.md` size means it already nagged and is holding off — expected.

**Partners only ever reply to the baton holder, never each other.** The advisors are meant to debate among themselves and hand the holder a joint view — `send --to p3`, not just `--to @all`. If every message is a spoke to one hub, `send` one advisor a direct question to seed a side thread.

**The baton holder waits forever for consensus.** Two exchanges, then act — unanimity is not the bar. A genuine deadlock goes to the user with both positions; it does not block the change.

**Partner agrees with everything.** Usually the same model as yours, or effort too low. Stop it and spawn a different provider.

**Partner edited files it should not have.** It still held the baton, or treated another agent's suggestion as an instruction. Run `claim` when the user is talking to you.

**Debate will not converge.** Expected on judgement calls. Stop after three exchanges and hand the choice to the user with both positions stated fairly.
