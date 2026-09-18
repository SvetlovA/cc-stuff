"""chat.md, the one append-only transcript every agent reads and writes.

Also the per-agent bookkeeping around it: the cursor (what has been handed
over), the floor (where an agent's responsibility starts) and the debts (what
is addressed to an agent and still unanswered).
"""
from __future__ import annotations

import re
from pathlib import Path

from .state import baton_of
from .timing import REDELIVER_AFTER
from .util import NL, age_seconds, utcnow


MSG_DELIM = "<!--/msg-->"


def append_msg(sd: Path, sender: str, to: str, body: str, baton: str) -> None:
    chat = sd / "chat.md"
    chat.parent.mkdir(parents=True, exist_ok=True)
    if not chat.exists():
        chat.write_text("# Partner debate transcript" + NL * 2, encoding="utf-8")
    entry = (f"{NL}### {utcnow()} | from:{sender} | to:{to} | baton:{baton}{NL * 2}"
             f"{body.strip()}{NL * 2}{MSG_DELIM}{NL}")
    with chat.open("a", encoding="utf-8") as fh:
        fh.write(entry)


HEADER_RE = re.compile(
    r"^###\s+(?P<ts>\S+)\s*\|\s*from:(?P<from>\S+)\s*\|\s*to:(?P<to>\S+)\s*\|\s*baton:(?P<baton>\S+)\s*$",
    re.MULTILINE,
)


def parse_msgs(text: str) -> list[dict]:
    out = []
    for chunk in text.split(MSG_DELIM):
        m = HEADER_RE.search(chunk)
        if not m:
            continue
        out.append({
            "ts": m.group("ts"), "from": m.group("from"),
            "to": m.group("to"), "baton": m.group("baton"),
            "body": chunk[m.end():].strip(),
        })
    return out


def _offset(sd: Path, who: str, name: str, cap: int) -> int:
    f = sd / who / name
    if not f.exists():
        return 0
    try:
        return min(int(f.read_text(encoding="utf-8").strip() or 0), cap)
    except (OSError, ValueError):
        return 0


def read_new(sd: Path, who: str) -> tuple[list[dict], int]:
    """Messages addressed to `who` (or @all) that `who` has not yet consumed,
    plus the offset that consuming them would move the cursor to.

    The cursor is deliberately *not* moved here. Advancing it before the caller
    has printed anything means a command killed in between -- which is routine,
    since every CLI caps command runtime and some caps are shorter than a wait
    -- consumes the message and loses it: no later `wait` can return it,
    because the cursor is already past it. `commit_cursor` is called after the output is flushed, so
    a killed wait re-delivers instead of swallowing.
    """
    chat = sd / "chat.md"
    if not chat.exists():
        return [], 0
    raw = chat.read_bytes()
    start = _offset(sd, who, "cursor", len(raw))
    fresh = raw[start:].decode("utf-8", errors="replace")
    msgs = [m for m in parse_msgs(fresh)
            if m["from"] != who and m["to"] in (who, "@all", "all")]
    return msgs, len(raw)


def commit_cursor(sd: Path, who: str, end: int) -> None:
    """Mark everything up to `end` as delivered, and never un-mark anything.

    Only ever forward: a `read` in one process and a `wait` in another can each
    hold an offset from a different moment, and letting the older one win would
    rewind the cursor and re-deliver a stretch of transcript that was already
    answered.
    """
    try:
        cur_f = sd / who / "cursor"
        cur_f.parent.mkdir(parents=True, exist_ok=True)
        have = 0
        if cur_f.exists():
            try:
                have = int(cur_f.read_text(encoding="utf-8").strip() or 0)
            except ValueError:
                have = 0
        if end > have:
            cur_f.write_text(str(end), encoding="utf-8")
    except OSError:
        pass


def set_floor(sd: Path, who: str) -> None:
    """Record where this agent's responsibility starts.

    Everything before the floor is history it was briefed on rather than mail
    it owes an answer to. Without it, "unanswered" would mean the entire
    transcript for an agent that has not spoken yet, and a new partner would be
    handed the whole backlog to reply to.
    """
    chat = sd / "chat.md"
    try:
        (sd / who).mkdir(parents=True, exist_ok=True)
        (sd / who / "floor").write_text(
            str(chat.stat().st_size if chat.exists() else 0), encoding="utf-8")
    except OSError:
        pass


def tail_msgs(sd: Path, n: int) -> list[dict]:
    chat = sd / "chat.md"
    if not chat.exists():
        return []
    return parse_msgs(chat.read_text(encoding="utf-8", errors="replace"))[-n:]


def render(msgs: list[dict]) -> str:
    return (NL * 2).join(
        f"### {m['ts']} | {m['from']} -> {m['to']} | baton:{m['baton']}{NL * 2}{m['body']}"
        for m in msgs)


def pending_for(sd: Path, roster: dict, who: str) -> list[dict]:
    """Messages `who` still owes a reply to.

    Empty when `who` holds the baton (it drives the change, it is not waiting on
    anyone), when nothing is addressed to it, or when it has already spoken
    since the last message addressed to it. An agent's own messages never count
    -- it cannot owe itself a reply.
    """
    if baton_of(roster) == who:
        return []
    chat = sd / "chat.md"
    if not chat.exists():
        return []
    raw = chat.read_bytes()
    # From this agent's floor, not from the top of the file: what was said
    # before it joined is briefing material, not mail it owes a reply to.
    text = raw[_offset(sd, who, "floor", len(raw)):].decode("utf-8", errors="replace")
    convo = [m for m in parse_msgs(text) if m["from"] != "system"]
    # Per sender, not one global "since I last spoke" line. With three agents
    # talking, a reply to p1 would otherwise clear the debt to p3 as well -- so
    # a message that arrived alongside another and was never answered became
    # invisible to every later check. What answers a sender is a message TO
    # that sender, or to @all.
    answered_at: dict[str, int] = {}
    for i, m in enumerate(convo):
        if m["from"] != who:
            continue
        if m["to"] in ("@all", "all"):
            for s in {x["from"] for x in convo if x["from"] != who}:
                answered_at[s] = i
            answered_at["@all"] = i
        else:
            answered_at[m["to"]] = i
    return [m for i, m in enumerate(convo)
            if m["from"] != who and m["to"] in (who, "@all", "all")
            and i > answered_at.get(m["from"], answered_at.get("@all", -1))]


def dropped_for(sd: Path, roster: dict, who: str,
                min_age: int = REDELIVER_AFTER) -> list[dict]:
    """Messages this agent was given and never answered.

    The cursor answers "has this been handed over", which stops being the right
    question the moment a handover fails: a `wait` killed mid-print, or an agent
    that read a message and then ended its turn, both leave the message consumed
    and unanswerable -- invisible to every later `wait`, recoverable only by
    another agent noticing the silence and pinging.

    So `wait` asks the transcript instead of the cursor. `min_age` keeps the
    normal cycle out of it: a message being replied to right now is not dropped,
    it is being worked on.
    """
    out = []
    for m in pending_for(sd, roster, who):
        age = age_seconds(m.get("ts"))
        if age is None or age >= min_age:
            out.append(m)
    return out


def baton_banner(roster: dict, who: str) -> str:
    """A one-line reminder of write permission, printed with every inbox read.

    The reminder lands at exactly the moment it is needed -- an agent reads its
    messages immediately before deciding what to do about them. Stating it every
    time costs one line and removes any excuse for editing out of turn.
    """
    holder = baton_of(roster)
    if holder == who:
        return "[baton: yours -- you are the one who edits files right now]"
    return (f"[baton: {holder} -- do NOT edit files. Argue and propose instead. "
            f"If the human just told YOU to make a change, run `claim` first.]")
