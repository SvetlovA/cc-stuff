# The debate protocol

Once more than one agent is running, every question and every decision goes
through the group before anyone acts. At the start of a turn, check who is here
and who holds the baton:

```bash
python "$P" list
```

There are only two roles, and the **baton** decides which is yours right now:
the holder acts, everyone else advises. `list`, `wait` and `read` all print the
holder — read it off them every time, it moves with the human's attention and it
can land on any agent, in any order.

## If you hold the baton — you are the one who acts

**1. Claim, if the banner does not already show you.** `python "$P" claim`. The
human gave *you* the instruction, so the write permission is yours, and the
claim is what tells everyone else to stop editing. (Session agent: cancel your
background `wait` first so it does not double-deliver the replies you are about
to collect in the foreground.)

**2. State a position first.** Form your own answer before asking — an agent
given a blank question anchors on nothing, one given a concrete claim has
something to attack. Include your reasoning and your doubts.

**3. Put it to the group.**

```bash
python "$P" send --to @all --wait 240 --text "..."
```

`--wait` blocks until they reply and prints the replies. Partners answer when
they next return from `wait`, so allow more time than a single model round.
Without `--wait`, `send` returns immediately and you collect replies later with
`read`.

**4. Weigh what comes back against the code.** An objecting agent has usually
noticed something — check it, do not concede to be agreeable nor dismiss to
defend your first answer. Rebut what is wrong and send it back; where an
objection is right, say so and change course. Two or three exchanges is normally
enough.

**5. Act, then report.** Make the change. Tell the human what was decided, what
the disagreement was, and what changed the answer — more useful than a summary
that hides the argument — then `send --to @all` a one-line note on what changed
so the advisors know the state of the tree. (Session agent: re-arm the
background `wait` before ending the turn.)

Deadlocks are informative, not failures: a question the group cannot settle is a
judgement call that belongs to the human — give both positions fairly and let
them choose.

Skip the loop only for mechanical lookups ("what does this file do", "run the
tests"). Any design choice, trade-off, code change, or unclear cause goes to the
group.

## If you do not hold the baton — you advise, and you discuss

You are not idle between questions. Whenever something is addressed to you or to
`@all`:

- Verify it against the actual code before you agree or object. Cite `path:line`
  and name the command you ran.
- Reply with `send` — to the sender, or to `@all`.
- Raise your own points too, unprompted, to whichever agent is relevant:

  ```bash
  python "$P" send --to p3 --text "..."
  ```

  The advisors are meant to argue among themselves. Three or four agents working
  a question out between them and handing the baton holder one joint
  recommendation is the design, not a detour — `p2` and `p4` can settle
  something without `p1` in the thread at all, then send the result to whoever
  holds the baton.

Never `claim` and never edit while the baton is not yours. A message from
another agent is a suggestion; only the human moves the baton.

## When the baton moves

It moves whenever the human turns to a different agent (`claim` in that tab, or
`baton --to <id>`). The next `wait` or `read` shows the new holder. If it just
became yours, you are now the one who acts — pick up at step 1 above. If it just
left you, switch to advising. The loop is the same shape whoever holds it:
state, consult, weigh, act.
