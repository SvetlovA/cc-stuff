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
| Its own launch prompt | do not claim — `spawn` started it; the boot line says so |
| An automated wake-up typed into its tab | do not claim — another agent noticed its silence |

The second row is the load-bearing one: without it, one agent could tell another to make a change and the baton would drift away from the human entirely.

Rows three and four exist because both messages arrive through the same channel as the human's typing and are indistinguishable from it by construction — a terminal tab has one input. Text is therefore the only defence available, and it is applied at both ends: the launch prompt and the wake-up line each open by disowning themselves, and every briefing names them as the two impostors. Because "is this the human?" is a judgement call, and a wrong answer silently takes write permission from whoever is working, shared state carries a guard too: `claim` refuses when the caller started less than `CLAIM_GRACE` (90s) ago and has never spoken in the transcript, and says to use `claim --force` if the human really did just address a brand-new partner. That combination is what turns an intermittent, invisible failure into a refusal with an explanation.

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
    ├── waiting        present only while a `wait` is actually polling
    ├── .nudge         when this agent was last woken (throttle)
    ├── .redeliver     when it was last handed its unanswered backlog
    ├── seed.md        the briefing this agent was launched with
    ├── handoff.md     what was decided before it joined
    ├── cursor         byte offset of the last message handed to it
    ├── floor          byte offset where its responsibility starts
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

- **Heartbeat.** Every agent writes `<id>/lastseen` whenever it runs `wait`, `read`, `send` or `claim`. Acting is what proves it is alive, and `wait` returns within its timeout (90s by default) and loops, so a working agent stamps at least every couple of minutes. Default window is 360s.
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

## Cursors, floors, and re-delivery

Each agent tracks two byte offsets into `chat.md`:

- **`cursor`** — how far it has been *handed* messages. `read` and `wait` return messages after it that are addressed to the agent or `@all` and were not sent by it.
- **`floor`** — where its responsibility starts, written when it joins or is resumed. Everything above the floor is history it was briefed on, not mail it owes an answer to.

`read --peek` reads without advancing the cursor — useful for inspecting what an agent is about to see without consuming it.

Because these are byte offsets rather than message indexes, an agent that was closed and restarted resumes exactly where it left off.

### The cursor moves only after delivery succeeds

`read_new` does not advance the cursor; `commit_cursor` does, after the output has been printed and flushed. The two were one step until a message went missing: a `wait` killed between "cursor advanced" and "output reached the model" consumed the message and lost it, and no later `wait` could return it, because the cursor was already past. That is not a rare race: every CLI caps command runtime, and a cap shorter than the wait makes the kill routine rather than exceptional — the shortest measured here fires after 10s. Delivering a message twice costs a duplicate; delivering it zero times costs the debate a participant.

### `wait` asks the transcript, not just the cursor

The cursor answers "has this been handed over", which stops being the right question the moment a handover fails. Two ways it fails, and both used to be unrecoverable from inside:

- a `wait` killed mid-print, as above;
- an agent that was given a message and ended its turn without replying — the cursor is past it, so nothing will ever show it again.

Either way the message is gone until another agent notices the silence and pings. So `wait` also asks a cursor-free question every poll: *is anything addressed to me, after my own last message, still unanswered?* — `pending_for`, the same check the Stop hook uses, bounded below by the floor. Anything older than `REDELIVER_AFTER` (45s) comes back marked **re-delivered**, and a reply is what clears it.

Three things keep that from becoming noise:

- **New messages win.** Re-delivery only runs when nothing new arrived, so the normal cycle never sees it.
- **The age threshold.** A message being replied to right now is being worked on, not dropped.
- **A throttle.** `<id>/.redeliver` holds off repeats for 90s, so an agent that cannot answer is reminded at a bounded rate instead of spinning through instant returns.

`read` shows the same backlog under "still unanswered, from earlier", with no age threshold — an agent running `read` is asking what it owes.

The floor is what makes this safe for a new partner: without it, an agent that has never spoken would owe a reply to every `@all` message in the transcript and answer the entire backlog on its first `wait` — the failure the cursor reset was introduced to prevent.

This and the nudge cover different halves of the same problem. Re-delivery recovers a message an agent dropped while it was still looping; the nudge recovers an agent that stopped looping at all. An agent woken by a nudge is told to run `read`, which is exactly where its unanswered backlog is waiting.

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

`wait` is what makes an interactive session autonomous. It polls the transcript and blocks until a message arrives for that agent, so the agent has something to answer rather than needing to be driven. It always returns within `--timeout` (default 90s) even when nothing arrives, which matters twice over: the tab never looks wedged, and the human can interrupt and type instead.

### Command-runtime caps, and why they end debates

Every agent reaches `wait` through its own CLI's shell tool, and every one of those caps how long a command may run. The two measured here differ by more than tenfold: one CLI's exec tool terminates at **10s** by default, another's Bash tool at **120s**. A wait longer than the cap is killed mid-poll, and — before this was handled — returned nothing at all. An agent reading an empty result from the command its entire loop depends on concludes the command is broken and ends its turn, which for a tab agent is permanent: nothing re-invokes it.

Three changes make the loop survive that:

- **`wait` announces itself before blocking**, flushed, so a killed call still prints the line telling the agent that a short return is the cap expiring and to run `wait` again. The output an agent gets from a truncated command is now instructions rather than silence.
- **Every briefing states the rule, whatever the agent is running:** an early or empty `wait` is the tool's cap, so raise the timeout if the tool takes one and re-run it either way. A *specific* cap is an accelerator on top, and it travels with the rest of that CLI's launch facts — `wait_note` in a recipe or in the user's own `providers.json`, returned by `resolve_provider` like `install` or `efforts`. A CLI nobody has measured is still fully briefed by the rule.
- **The default dropped from 120s to 90s**, since a default sitting exactly on a known cap turned every normal wait into a tool error. 90s still stamps a heartbeat well inside the 360s liveness window.

### Waking an agent that stopped listening

A Stop hook only exists inside Claude Code. Any other CLI may or may not have an equivalent, and this plugin cannot require one — so for a tab that ends its turn the recovery comes from outside the agent entirely: type into the tab, exactly as the human would. That works whatever is running in it.

```bash
.partner/p.sh nudge --id p2      # one agent
.partner/p.sh nudge              # everyone with no `wait` in flight
```

`spawn` records how each tab was opened (`tab_kind`, `tab_handle`, `tab_title` on the roster entry) because the terminal that owns a tab is not recoverable afterwards. Orca tabs are typed into with `orca terminal send --terminal <handle> --text … --enter`; tmux, WezTerm and kitty have equivalents. A detached OS window (Windows Terminal, Terminal.app, GNOME Terminal) cannot be typed into, so `nudge` reports that and prints the command that restarts the agent instead.

Where it fires:

- **`send`** checks its recipients and wakes any that are not listening — the message that would otherwise vanish into a transcript nobody reads.
- **`wait`**, on an idle timeout, wakes anyone silent: the cheapest moment to notice, since nothing is waiting on a reply.
- **`read` and `wait`** name silent agents in their output, so an agent about to conclude that a partner has nothing to say learns that nobody was listening instead.

Nudges are throttled through a marker in the *target's* directory (`<id>/.nudge`, 120s), so three agents noticing the same silence produce one wake-up between them. The text says it is automated and not the human, and tells the agent not to claim the baton — see "How the baton moves".

The briefing tells each agent to loop — `wait`, think, `send`, repeat — and what it does on each pass turns on whether it holds the baton. The holder runs the debate and makes the change (state a position, `send --to @all --wait`, weigh the replies, act). Everyone else advises — and not only in reply to the holder: any non-holder can open a thread with any other, `send --to p3` as readily as `--to @all`, so three or four agents can work a question out among themselves and hand the holder a joint recommendation. The baton moves with the human's attention; each `wait` and `read` reprints the holder, and an agent switches roles the moment it changes.

The one agent that does **not** block on `wait` is the session someone typed the skill into. It reaches the human through its own Claude Code harness, and a foreground block would stop them talking to it — so its briefing has it run `wait` as a **background** command instead. Claude Code re-invokes it when that returns (a partner spoke, or it timed out), which is what keeps it in the debate even while the human is working in another agent's tab. Without this it would sit idle any time the conversation moved to a tab — participating only when addressed in its own session. It keeps exactly one background `wait` in flight and re-arms it at the end of every turn. That is the only difference between any two agents here, and it comes from how the human reaches them, not from rank.

## Opening the session

A roster of briefed agents is not a debate. Everyone is blocked on `wait`, the transcript holds nothing but join notices, and nobody has been asked anything — so the session has to be opened deliberately:

```bash
.partner/p.sh kickoff --text "what the human wants worked on"
.partner/p.sh kickoff                    # they have not said yet
```

Both send one message to `@all`, and both are canned text rather than improvised, so every agent receives the same protocol in the same words. An improvised "have a look around" produces a summary nobody asked for and an agent that then stops looping.

**With `--text`**, the human's instruction is framed as the session's opening question, naming the sender as the baton holder: the others verify it against the code and argue, the holder acts.

**Without**, it sends the orientation brief — the agents split the repository between them, read it, ground every claim at `path:line`, and hand each other one joint picture of what the project is and what to be careful with. Its constraints are the point: **nobody edits anything**, not even the baton holder; claims are split rather than duplicated; it converges in at most two rounds and everyone returns to `wait`. The goal is that the human's first real question is not answered from a cold read — not to fill the silence with work nobody asked for.

Skip the orientation when the context is already there: a resumed session, or a `--context` briefing that already says what the work is. Re-reading the repo to restate it costs everyone a round.

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

**A message nobody ever answered.** It was handed over and dropped — a killed `wait`, or a turn that ended without a reply. `wait` re-delivers anything addressed to an agent and unanswered after 45s, and `read` lists it under "still unanswered", so recovery no longer depends on another agent noticing. A message that keeps coming back marked *re-delivered* means the agent is receiving it and not replying; `pending --for <id>` shows what it owes.

**The session agent idle while the human works in a tab.** It had no background `wait` armed, so nothing re-invoked it when a partner addressed `@all`. Its briefing arms one at the end of every turn; if it stopped, the next human turn in the main session re-arms it, and the Stop hook blocks it from ending a turn with an unanswered message in the meantime. Check `.partner/<id>/lastseen` — a fresh stamp means the background `wait` is running.

**The Stop hook never fires.** It needs `bash` and Python on `PATH`, and `hooks.json` is read once at session start — a session already open when the plugin was installed will not have it. Restart. `partner.py hook-stop < /dev/null` from the repo root should print nothing and exit 0.

**Advisors only ever reply to the baton holder.** They should also debate each other — `send --to p3`, not just `--to @all` — and hand the holder a joint view. If the transcript is all spokes to one hub, the briefing's "you advise, and you discuss" step is being skipped; `send` one of them a direct question to seed it.

**The baton drifting away from the human.** An agent claimed after being asked by another agent rather than by the human. Its briefing forbids this; check the transcript for the `system` message naming who claimed and when.

**An agent that stopped looping.** Interactive agents sometimes end their turn rather than running `wait` again — most often because their CLI's command-runtime cap cut a `wait` short (see above), or right after a discussion concluded. `nudge --id <id>` types a wake-up into its tab; `send` does it automatically for a recipient that is not listening.

**A partner that claimed the baton the moment it started.** It read its own launch prompt as the human addressing it. The boot line disowns itself, the briefing names it, and `claim` refuses inside the launch grace window — if it still happened, `baton --to <id>` puts it back and the transcript names who claimed.

**Interleaved writes.** Appends are single `open(..., "a")` writes, atomic at these sizes on all three platforms. Two agents replying in the same instant produce two well-formed adjacent records, not a corrupted one.

**Runaway loops.** Two agents addressing each other with `@all` will keep going until one applies the two-exchange rule. When agents should debate each other directly, prefer explicit `--to <id>` addressing so the exchange has a clear owner.
