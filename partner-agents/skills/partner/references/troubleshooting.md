# When things go wrong

**Partners disappeared mid-argument.** `--fresh` was passed while they were live, archiving the session. Nothing is lost: `sessions` lists it, `resume --session <id>` brings it back. Run `state` before spawning to avoid it.

**The user expected their old partners back.** They wanted `resume`, not a fresh session. Nothing is lost — `sessions` lists what was archived, and `resume --session <id>` rebuilds it.

**Only yourself in `list` after spawning.** The spawn never ran. Registering yourself is not the deliverable — go back and run `spawn`.

**Spawn refused with a list of problems.** That is validation, not a crash — each line ends with the command that fixes it.

**Partner never replies.** Check its tab: a permission prompt (raise `--auto`), an error, or simply between `wait` calls.

**Partner stopped looping.** Interactive agents sometimes end their turn instead of running `wait` again. Type "continue" in its tab, or `send` it a message.

**Partner agrees with everything.** Usually the same model as yours, or effort too low. Stop it and spawn a different provider.

**Partner edited files it should not have.** It still held the baton, or treated another agent's suggestion as an instruction. Run `claim` when the user is talking to you.

**Debate will not converge.** Expected on judgement calls. Stop after three exchanges and hand the choice to the user with both positions stated fairly.
