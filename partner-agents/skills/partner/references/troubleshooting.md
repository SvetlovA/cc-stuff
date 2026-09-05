# When things go wrong

**Partners disappeared mid-argument.** A bare `/partner` starts a new session and archives the running one — that is what it is for. Nothing is lost: `sessions` lists it, `resume --session <id>` brings it back with its agents. If the user wanted to keep the debate going, they meant `/partner add`.

**The user expected their old partners back.** They wanted `resume`, not a fresh `/partner`. Nothing is lost — `sessions` lists what was archived, and `resume --session <id>` rebuilds it.

**The current session was not archived on a fresh `/partner`.** Expected when the roster held only this session with no real discussion — there is nothing to archive, so `--fresh` just adds the partner. Once a partner has joined or anything substantive is in `chat.md`, the next fresh `/partner` archives it.

**Only yourself in `list` after spawning.** The spawn never ran. Registering yourself is not the deliverable — go back and run `spawn`.

**Spawn refused with a list of problems.** That is validation, not a crash — each line ends with the command that fixes it.

**Partner never replies.** Check its tab: a permission prompt (raise `--auto`), an error, or simply between `wait` calls.

**Partner opens by answering the whole backlog, then goes quiet.** Its cursor started at zero, so its first `wait` returned the entire transcript instead of blocking for the question. `spawn` sets the cursor to the end of `chat.md` right after the join message to prevent this; if you see it, check `.partner/<id>/cursor` exists and is non-zero, and that the join `system` message is in the transcript.

**Partner stopped looping.** Interactive agents sometimes end their turn instead of running `wait` again. Type "continue" in its tab, or `send` it a message. The briefing now tells them the loop is the whole job and to return to `wait` after every reply.

**Partner agrees with everything.** Usually the same model as yours, or effort too low. Stop it and spawn a different provider.

**Partner edited files it should not have.** It still held the baton, or treated another agent's suggestion as an instruction. Run `claim` when the user is talking to you.

**Debate will not converge.** Expected on judgement calls. Stop after three exchanges and hand the choice to the user with both positions stated fairly.
