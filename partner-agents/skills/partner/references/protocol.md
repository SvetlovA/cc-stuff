# Transcript protocol

Every agent — this session and each partner — communicates through one append-only file: `.partner/chat.md` at the repo root.

A single shared transcript rather than per-partner channels is what makes group debate work: with three partners, everyone sees everyone's argument, so a point raised by `p2` can be answered by `p3` without anyone relaying it.

## Identity

Every agent is an ordinary entry in `roster.json`, including the one that spawned the others:

```json
{
  "self":  "p1",
  "baton": "p1",
  "partners": { "p1": {...}, "p2": {...} }
}
```

`baton` names the agent the human most recently gave an instruction to, and it is the only agent that may edit files. See "How the baton moves" below.

`self` is not a rank — it only records which entry belongs to the agent reading the file, so `send` and `read` can default `--from` and `--for` sensibly. Whoever runs `init` takes `p1`; spawned agents continue the numbering. `stop --all` stops every spawned agent and leaves the session's own entry alone, since it has no tab to close.

The single asymmetry in the system is the baton, and it moves.

## How the baton moves

The baton is meant to track the human's attention: whoever they are talking to is whoever writes. No supervising process exists, and nothing outside a terminal tab can observe which tab is being typed into — so the agent that receives the instruction is the one that has to record it:

```bash
.partner/p.sh claim        # "the human just told me to do something"
```

`claim` sets the baton to the caller and announces the move in the transcript, so every other agent learns about it on its next `read` or `wait`.

The distinction each agent is briefed on:

| Where the request came from | What to do |
|-----------------------------|------------|
| The human, typing in this agent's tab | `claim`, then act |
| Another agent, via `wait` | do not claim, do not edit — argue and propose |

That second row is the load-bearing one. Without it, one agent could tell another to make a change and the baton would drift away from the human entirely.

Every `read` and `wait` prints the current holder as its first line, which puts the reminder exactly where it is needed — an agent reads its inbox immediately before deciding what to do about it. `baton --to <id>` remains for deliberate handover.

Because partners launch with permission prompting relaxed, this is a rule agents keep rather than a sandbox that stops them. It holds because every briefing carries the reason, not just the instruction: concurrent edits produce conflicts nobody can see.

## State layout

```
.partner/
├── roster.json        every agent, which one is you, who holds the write baton
├── chat.md            the shared transcript
├── p.cmd / p.sh       short wrapper -- how agents invoke partner.py
└── <id>/
    ├── seed.md        the briefing this agent was launched with
    ├── handoff.md     what was decided before it joined
    ├── cursor         byte offset of the last message it consumed
    └── run.cmd|.sh    the exact command its terminal tab runs
```

`.partner/` is added to `.git/info/exclude` by `partner.py init`, so it is ignored locally without modifying a tracked `.gitignore`.

## Message format

```markdown
### 2026-09-05T09:52:54Z | from:p1 | to:@all | baton:p1

Body text. Markdown, any length.

<!--/msg-->
```

- `from` — sender id: any agent id, or `system` for tooling notices such as a baton move.
- `to` — an agent id, or `@all`.
- `baton` — who held write permission when the message was written. Useful when reading history: it explains why an agent argued instead of editing.
- `<!--/msg-->` — the record separator. It is an HTML comment, so `chat.md` still renders cleanly in any markdown viewer.

Append only. Rewriting history breaks every agent's cursor and silently drops messages.

## Cursors

Each agent tracks a byte offset into `chat.md`. `read` and `wait` return messages after that offset that are addressed to it or `@all` and were not sent by it, then advance the offset.

`read --peek` reads without advancing — useful for inspecting what an agent is about to see without consuming it.

Because the cursor is a byte offset rather than a message index, an agent that was closed and restarted resumes exactly where it left off.

## How an agent takes part

There is no supervising process. Each agent is an ordinary interactive session that drives itself, using the wrapper written at `.partner/p.cmd` (Windows) or `.partner/p.sh` (macOS/Linux):

```bash
.partner/p.sh wait --for p2                       # blocks until addressed
.partner/p.sh send --from p2 --to @all --text ".."
.partner/p.sh claim                               # the human just addressed me
.partner/p.sh list                                # who is here, who holds the baton
```

`wait` is what makes an interactive session autonomous. It polls the transcript and blocks until a message arrives for that agent, so the agent has something to answer rather than needing to be driven. It always returns within `--timeout` (default 120s) even when nothing arrives, which matters twice over: the tab never looks wedged, and the human can interrupt and type instead.

The briefing tells each agent to loop — `wait`, think, `send`, repeat — and to answer the human directly whenever they type into its tab, then resume.

## What an agent is launched with

`.partner/<id>/seed.md` contains:

1. **Who it is** and who its partners are, stated as equals with nobody in charge.
2. **That a human may type into its tab at any time**, and that it should answer them and then resume looping.
3. **The commands** for `wait`, `send` and `list`, using the short wrapper.
4. **The baton rule** — `claim` when the human addresses it, never when another agent does — with the reason: concurrent edits produce conflicts nobody can see.
5. **How to argue well** — lead with a position, disagreement needs a concrete alternative, verify against the code, cite `path:line`, stop after two exchanges without movement.
6. **The loop itself**, as numbered steps.
7. **A pointer to its `handoff.md`**, which carries the working tree, the debate so far, and whatever briefing was written at spawn.

The starting prompt passed on the command line only says "read `seed.md` and follow it", which keeps a multi-kilobyte briefing out of a terminal command line.

## Convergence

There is no mechanical round limit, because no supervising process exists to enforce one. The briefing asks each agent to stop after two exchanges with no movement, state the disagreement fairly, and let the human decide.

That is the right place for the rule. A deadlock between two models is a genuine signal that the question is a judgement call, and the human is the one who should break it; grinding on produces confident-sounding convergence that reflects stamina rather than correctness.

## Failure modes

**An agent reopening a settled question.** It joined without the history, or `--context` never said the question was closed. Check `.partner/<id>/handoff.md`.

**An agent talking to itself.** `read` and `wait` filter out messages the reader sent, so an agent cannot trigger its own next round.

**The baton drifting away from the human.** An agent claimed after being asked by another agent rather than by the human. Its briefing forbids this; check the transcript for the `system` message naming who claimed and when.

**An agent that stopped looping.** Interactive agents sometimes end their turn rather than running `wait` again. Type "continue" in its tab, or `send` it a message.

**Interleaved writes.** Appends are single `open(..., "a")` writes, atomic at these sizes on all three platforms. Two agents replying in the same instant produce two well-formed adjacent records, not a corrupted one.

**Runaway loops.** Two agents addressing each other with `@all` will keep going until one applies the two-exchange rule. When agents should debate each other directly, prefer explicit `--to <id>` addressing so the exchange has a clear owner.
