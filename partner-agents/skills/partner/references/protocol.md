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

Whoever runs `init` takes `p1`; spawned agents continue the numbering. `spawn` calls `init` itself from its `--me-*` arguments, so registering yourself is never a separate step that can be completed on its own. Every entry has the same shape — provider, model, effort, auto level, status — because `init` and `spawn` build them with the same code. The agent that starts the session is a participant, not the thing the others hang off.

### Identity is per-process

Each agent runs through its own wrapper at `.partner/<id>/p.cmd` (or `p.sh`), which exports `PARTNER_ID` before calling the script:

```
@echo off
set PARTNER_ID=p2
"python" "…/partner.py" %*
```

So `claim`, `read`, `wait` and `send` need no id passed to them — the process knows who it is. This matters more than convenience: if identity were read from the shared `roster.json`, every agent would resolve to whoever ran `init`, and a partner running `claim` would take the baton *on that agent's behalf*. `self` survives only as the fallback for the one agent with no wrapper of its own — the session someone typed the skill into.

`stop --all` stops every other agent and leaves the caller's own entry alone, since it has no tab to close.

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
    ├── p.cmd / p.sh   this agent's wrapper -- carries its PARTNER_ID
    ├── lastseen       heartbeat: when this agent last acted
    ├── seed.md        the briefing this agent was launched with
    ├── handoff.md     what was decided before it joined
    ├── cursor         byte offset of the last message it consumed
    └── run.cmd|.sh    the exact command its terminal tab runs
```

Every agent has an `<id>/` directory, including the one that started the session. If one of them were missing a briefing or a wrapper, it would not be a peer.

Past sessions live alongside, each a complete copy of the above:

```
.partner/sessions/20260905-185718/
├── meta.json          id, label, agent configs, message count
├── roster.json        who was in it
├── chat.md            what they said
└── <id>/              each agent's briefing as it stood
```

`.partner/` is added to `.git/info/exclude` by `partner.py init`, so it is ignored locally without modifying a tracked `.gitignore`.

## Liveness

`roster.json` records `status: "running"` because nothing has told it otherwise. Close a tab and the claim survives, so it cannot decide whether to add a partner or start over. Liveness is evidence instead:

- **Heartbeat.** Every agent writes `<id>/lastseen` whenever it runs `wait`, `read`, `send` or `claim`. Acting is what proves it is alive, and `wait` returns every 120s and loops, so a working agent stamps at least every couple of minutes. Default window is 360s.
- **Orca tab list.** Inside Orca, `orca terminal list` reports whether a tab titled `partner:<id>` still exists.

A **fresh heartbeat outranks the tab list**. An agent that ran a command seconds ago is alive regardless of what the tab list says — checking tabs first would call it dead whenever the tab was renamed, launched with `--no-tab`, or started outside Orca. The tab check only downgrades an agent that has *not* checked in recently.

`state` combines all of this with the session history into one answer, and ends with a `recommend` (`add` / `ask` / `new`).

The `recommend` is advisory. What actually decides add-vs-fresh is the **invocation**: a bare `/partner` starts a new session (`spawn --fresh`), `/partner add` joins the live one (`spawn`). `state` is read to *narrate* that — "you are about to archive 2 agents, 6 messages" — and to catch the cases where the user's intent and the roster disagree (a bare `resume` with nothing archived, an `add` with no session running).

It is one call on purpose: roster, liveness and history belong together, and fetching them separately invites deciding on half the picture.

## Sessions

A session is one arrangement of agents plus the transcript they produced. Starting a new one never destroys the old: `archive` moves the live session under `sessions/` whole — files moved, not copied and deleted — and leaves a clean slate. `spawn --fresh` does this as its first act, so a new invocation of the skill cannot silently overwrite the previous argument.

A session with **only this session in it and nothing said beyond system notices** is not archived — there is nothing to come back to, and filing it away would force a needless re-init. So `spawn --fresh` on a cold roster just adds the first partner; the archive step is a no-op.

`meta.json` records each agent's provider, model, effort and auto level, which is what makes rebuilding possible. The `label` is the first non-`system` message in the transcript, so a list of sessions reads as a list of questions rather than timestamps.

`resume` restores a session and relaunches **every** agent from its roster entry — the same `relaunch()` path `spawn` uses, so a rebuilt agent is indistinguishable from a freshly created one. Two details make it continue rather than restart:

- Each agent's `handoff.md` is rewritten from the restored transcript, so it knows what was already settled.
- Each cursor is set to the end of that transcript. Without this an agent would find the entire history sitting in its inbox and try to answer all of it.

`spawn` sets a new partner's cursor the same way, once, right after announcing its arrival: the backlog is already in its `handoff.md`, so its first `wait` should block for the question being put to it, not return the whole transcript.

A `system` message then announces the resume, which is the one new message every agent sees.

Resuming archives whatever was live first, so switching between sessions never loses work. The agent running `resume` adopts the `kind: "session"` entry — a restored roster would otherwise still name the agent that created it, on a machine where that process no longer exists.

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
.partner/p2/p.sh wait                     # blocks until addressed
.partner/p2/p.sh send --to @all --text ".."
.partner/p2/p.sh claim                    # the human just addressed me
.partner/p2/p.sh spawn --provider gemini  # bring in another partner
.partner/p2/p.sh list                     # who is here, who holds the baton
```

Any agent can run any of these, `spawn` included. `.partner/p.sh` (no id) is the shared wrapper, for a human at a shell.

`wait` is what makes an interactive session autonomous. It polls the transcript and blocks until a message arrives for that agent, so the agent has something to answer rather than needing to be driven. It always returns within `--timeout` (default 120s) even when nothing arrives, which matters twice over: the tab never looks wedged, and the human can interrupt and type instead.

The briefing tells each agent to loop — `wait`, think, `send`, repeat — and what it does on each pass turns on whether it holds the baton. The holder runs the debate and makes the change (state a position, `send --to @all --wait`, weigh the replies, act). Everyone else advises — and not only in reply to the holder: any non-holder can open a thread with any other, `send --to p3` as readily as `--to @all`, so three or four agents can work a question out among themselves and hand the holder a joint recommendation. The baton moves with the human's attention; each `wait` and `read` reprints the holder, and an agent switches roles the moment it changes.

The one agent that does **not** block on `wait` is the session someone typed the skill into. It reaches the human through its own Claude Code harness, and a foreground block would stop them talking to it — so its briefing has it run `wait` as a **background** command instead. Claude Code re-invokes it when that returns (a partner spoke, or it timed out), which is what keeps it in the debate even while the human is working in another agent's tab. Without this it would sit idle any time the conversation moved to a tab — participating only when addressed in its own session. It keeps exactly one background `wait` in flight and re-arms it at the end of every turn. That is the only difference between any two agents here, and it comes from how the human reaches them, not from rank.

## What an agent is launched with

`.partner/<id>/seed.md` contains:

1. **Who it is** and who its partners are, stated as equals with nobody in charge.
2. **That a human may address it at any time** (in its tab, or — for the session agent — through its own harness); when they do it holds the baton and runs the debate before acting, and the rest of the time it advises whoever holds it and argues with the other advisors. Then it resumes looping.
3. **The commands** for `wait`, `send` and `list`, using the short wrapper.
4. **The baton rule** — `claim` when the human addresses it, never when another agent does — with the reason: concurrent edits produce conflicts nobody can see.
5. **How to argue well** — lead with a position, disagreement needs a concrete alternative, verify against the code, cite `path:line`, stop after two exchanges without movement.
6. **The loop itself**, as numbered steps.
7. **A pointer to its `handoff.md`**, which carries the working tree, the debate so far, and whatever briefing was written at spawn.

The starting prompt passed on the command line only says "read `seed.md` and follow it", which keeps a multi-kilobyte briefing out of a terminal command line.

## The Stop hook

`hooks/hooks.json` registers a `Stop` hook — `partner.py hook-stop` — that runs each time any agent's turn ends. It makes two checks, in order.

**1. Unanswered messages.** Blocks when a message in `chat.md` is addressed to that agent (by id or `@all`), arrived after the agent last spoke, and has no reply yet: the agent is told to `read` and `send` before it can stop.

**2. Nobody listening.** Blocks when no `wait` is in flight for that agent, whoever it is and whether or not it holds the baton. `wait` holds `<id>/waiting` for as long as it polls, stamped with its own deadline, so the marker answers *"listening right now"* — where `lastseen` would only answer *"ran a command recently"*, which `send` and `read` satisfy just as well. That distinction is the point: an agent that has just replied and is about to stop looks busy by every other measure. The session agent additionally accepts a fresh `lastseen`, since it backgrounds its `wait` and the marker write can race a Stop firing straight after.

Check 2 covers the two moments an agent decides it is finished:

- **After spawning.** The session registers a partner, reports success, and ends the turn without ever entering the loop. Nothing re-invokes it, and the human — now typing in the partner's tab — has an agent that never speaks. Check 1 cannot catch this: at that moment nothing has been said *to* it yet, and by the time the partner speaks the session is already unreachable.
- **When a discussion closes, or the baton moves.** Losing the baton demotes an agent to advisor; it does not excuse it from listening. `claim` and `baton` both say so in the system message they append, and check 2 enforces it.

A tab agent inside a foreground `wait` is not stopping, so it never reaches the hook while it is behaving. Reaching it at all means it left the loop.

Details that keep it from getting in the way:

- **The baton holder is exempt from check 1.** It drives the change; it is not waiting on anyone. `pending_for` returns nothing for it. Check 2 still applies — driving is not an excuse for going deaf.
- **Own messages never count** — an agent cannot owe itself a reply (`m["from"] != who`).
- **It nags at a bounded rate.** `.partner/<id>/.stop-nag` holds the transcript size at the last message block — an identical size means "already told them". `.stop-nag-live` throttles the listener warning to once every 120s, since that one is not tied to a new message. A genuinely stuck agent is never wedged in an unbreakable loop.
- **It fails open.** No Python, no `.partner/`, or an unregistered/stopped agent — the stop proceeds.
- **Identity** comes from `PARTNER_ID`, which `run.sh`/`run.cmd` export into the tab, so the hook resolves the same agent the wrappers do rather than assuming it is the session.

## Convergence

There is no mechanical round limit, because no supervising process exists to enforce one. The briefing asks each agent to stop after two exchanges with no movement, state the disagreement fairly, and let the human decide.

That is the right place for the rule. A deadlock between two models is a genuine signal that the question is a judgement call, and the human is the one who should break it; grinding on produces confident-sounding convergence that reflects stamina rather than correctness.

## Failure modes

**A new agent nobody addresses.** `spawn` announces arrivals in the transcript (`system` message naming the id), so the others learn to address it. If that message is missing, the spawn did not complete.

**An agent reopening a settled question.** It joined without the history, or `--context` never said the question was closed. Check `.partner/<id>/handoff.md`.

**An agent talking to itself.** `read` and `wait` filter out messages the reader sent (`m["from"] != who`), so an agent cannot trigger its own next round — including the session agent's background `wait`.

**The session agent idle while the human works in a tab.** It had no background `wait` armed, so nothing re-invoked it when a partner addressed `@all`. Its briefing arms one at the end of every turn; if it stopped, the next human turn in the main session re-arms it, and the Stop hook blocks it from ending a turn with an unanswered message in the meantime. Check `.partner/<id>/lastseen` — a fresh stamp means the background `wait` is running.

**The Stop hook never fires.** It needs `bash` and Python on `PATH`, and `hooks.json` is read once at session start — a session already open when the plugin was installed will not have it. Restart. `partner.py hook-stop < /dev/null` from the repo root should print nothing and exit 0.

**Advisors only ever reply to the baton holder.** They should also debate each other — `send --to p3`, not just `--to @all` — and hand the holder a joint view. If the transcript is all spokes to one hub, the briefing's "you advise, and you discuss" step is being skipped; `send` one of them a direct question to seed it.

**The baton drifting away from the human.** An agent claimed after being asked by another agent rather than by the human. Its briefing forbids this; check the transcript for the `system` message naming who claimed and when.

**An agent that stopped looping.** Interactive agents sometimes end their turn rather than running `wait` again. Type "continue" in its tab, or `send` it a message.

**Interleaved writes.** Appends are single `open(..., "a")` writes, atomic at these sizes on all three platforms. Two agents replying in the same instant produce two well-formed adjacent records, not a corrupted one.

**Runaway loops.** Two agents addressing each other with `@all` will keep going until one applies the two-exchange rule. When agents should debate each other directly, prefer explicit `--to <id>` addressing so the exchange has a clear owner.
