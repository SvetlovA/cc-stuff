# The debate protocol

Once a partner is running, consult it on **every question and every decision** — that is the point of having one. At the start of a turn, check who is here:

```bash
python "$P" list
```

If any partner is `running`, do not answer the user directly. Run this loop instead:

**0. Claim the baton.** The user just gave *you* the instruction, so the write permission is yours: `python "$P" claim`. It is a no-op if you already hold it, and it tells the partners to stop editing.

**1. State a position first.** Form your own answer before asking — a partner given a blank question anchors on nothing, one given a concrete claim has something to attack. Include your reasoning and your doubts.

**2. Put it to the partners.**

```bash
python "$P" send --to @all --wait 240 --text "..."
```

`--from` defaults to your own id. `--wait` blocks until they reply and prints the replies. Partners answer when they next return from `wait`, so allow more time than a single model round would take. Without `--wait`, `send` returns immediately and you collect replies later with `read`.

**3. Take the disagreement seriously.** An objecting partner has usually noticed something. Check it against the code — do not concede to be agreeable, nor dismiss to defend your first answer. Where it is right, say so and change course.

**4. Rebut or converge.** Still disagree after checking? Say why and send it back. Two or three exchanges is normally enough. Deadlocks are informative, not failures: a question two models cannot settle is a judgement call that belongs to the user — give both positions fairly and let them choose.

**5. Act, then report.** The baton holder makes the change. Tell the user what was decided, what the disagreement was, and what changed the answer — more useful than a summary that hides the argument.

Skip the loop only for mechanical lookups ("what does this file do", "run the tests"). Any design choice, trade-off, code change, or unclear cause goes to the partners.

