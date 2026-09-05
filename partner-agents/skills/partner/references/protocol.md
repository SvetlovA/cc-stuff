# Transcript protocol

Every agent — this session, each partner, and the tooling itself — communicates through one append-only file: `.partner/chat.md` at the repo root.

A single shared transcript rather than per-partner channels is what makes group debate work: with three partners, everyone sees everyone's argument, so a point raised by `p2` can be answered by `p3` without anyone relaying it.

## State layout

```
.partner/
├── roster.json        every partner + the current baton holder
├── chat.md            the shared transcript
└── <id>/
    ├── handoff.md     this partner's briefing, written at spawn time
    ├── cursor         byte offset of the last message this partner consumed
    ├── session        provider session id (only for resume-capable providers)
    ├── run.sh|.cmd    the command its terminal tab runs
    ├── seed.md        protocol briefing (tui mode only)
    └── log            raw provider output, one block per round
```

`.partner/` is added to `.git/info/exclude` by `partner.py init`, so it is ignored locally without modifying a tracked `.gitignore`.

## Identity

Every agent is an ordinary entry in `roster.json`, including the one that spawned the
others. `roster.json` carries three things:

```json
{
  "self":  "p1",
  "baton": "p1",
  "partners": { "p1": {...}, "p2": {...} }
}
```

`self` is not a rank — it only records which entry belongs to the agent reading the
file, so `send` and `read` can default `--from` and `--for` sensibly. The agent that
ran `init` takes `p1`; spawned agents continue the numbering. `stop --all` stops every
spawned agent and leaves the session's own entry alone, since it has no tab to close.

The single asymmetry in the system is the baton, and it moves.

## Message format

```markdown
### 2026-09-05T09:52:54Z | from:p1 | to:@all | baton:p1

Body text. Markdown, any length.

<!--/msg-->
```

- `from` — sender id: any agent id, or `system` for tooling notices such as a baton move.
- `to` — an agent id, or `@all`.
- `baton` — who held write permission when the message was written. Useful when reading history: it explains why a partner argued instead of editing.
- `<!--/msg-->` — the record separator. It is an HTML comment, so `chat.md` still renders cleanly in any markdown viewer.

Append only. Rewriting history breaks every partner's cursor and silently drops messages.

## Cursors

Each partner tracks a byte offset into `chat.md`. `read --for <id>` returns messages after that offset that are addressed to `<id>` or `@all` and were not sent by `<id>` itself, then advances the offset.

`--peek` reads without advancing — useful for inspecting what a partner is about to see without consuming it.

Because the cursor is a byte offset rather than a message index, a partner that was stopped and restarted resumes exactly where it left off.

## Participating from inside a partner tab

A partner in `--mode tui` drives the protocol with the same script. The seed prompt written to `.partner/<id>/seed.md` tells it:

```bash
python <path>/partner.py read --for p1                       # what is new for me
python <path>/partner.py send --from p1 --to @all --text ".." # say something
python <path>/partner.py list                                 # who holds the baton
```

A partner in `--mode loop` never calls these itself — the watcher does it around each provider invocation.

## What a partner receives each round

The watcher assembles a prompt containing:

1. **Role framing** — that it is an equal peer, not an assistant, and that agreeing with everything makes it useless.
2. **Baton state** — who holds it, and whether this partner may edit files this round.
3. **Effort hint** — for providers with no native effort flag.
4. **Handoff context** — `<id>/handoff.md`, on the partner's first round only. Per partner, so a later spawn never overwrites an earlier partner's briefing.
5. **The debate so far** — on the first round, the last 20 transcript messages regardless of provider, because a partner spawned into an argument in progress must see what is already settled. On later rounds, the last 8, and only for providers without session resume.
6. **New messages** — what it is being asked right now.

The reply is appended to the transcript verbatim and mirrored to the tab, so the user can watch the debate as it happens.

## Convergence

After each reply the watcher calls `should_continue_debate(reply, consecutive, max_rounds)` in `partner.py`. It ends the exchange when the partner opens with `AGREED`, or after `--max-rounds` consecutive replies (default 3).

This is the tuning point for how stubborn partners are. A low cap converges quickly but lets whichever agent is more confident win by attrition; a high cap surfaces real disagreement but burns tokens and can deadlock two stubborn models on something that does not matter.

## Failure modes

**Interleaved writes.** Appends are single `open(..., "a")` writes, which are atomic for the sizes involved on all three platforms. Two partners replying in the same instant produce two well-formed adjacent records, not a corrupted one.

**A partner reopening a settled question.** It joined without the history, or `--context` never said the question was closed. Check `.partner/<id>/handoff.md`.

**A partner talking to itself.** `read` filters out messages the reader sent. A partner cannot trigger its own next round.

**Runaway loops.** Two watchers addressing each other with `@all` will keep going until `max_rounds` stops them. When partners should debate each other directly, prefer explicit `--to <id>` addressing so the exchange has a clear owner.
