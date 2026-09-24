"""Every clock the partners run on, in one place.

Most of these are balanced against each other -- a wait must return before a
CLI's command cap, a heartbeat must land well inside the liveness window -- so
they are easier to reason about side by side than next to the code using them.
"""
from __future__ import annotations


WAIT_POLL = 3.0

# Every agent runs `wait` through its CLI's own shell tool, and every such tool
# caps how long a command may run. A default at or above a cap turns every
# normal wait into a tool error, which is what teaches an agent that the
# command is broken. 90s sits under the caps seen so far (120s and 10s on the
# two CLIs measured) and still stamps a heartbeat well inside LIVE_WINDOW.
# Agents whose cap is shorter are told to raise it or re-run -- see
# GENERIC_WAIT_NOTE.
WAIT_TIMEOUT = 90

NUDGE_WINDOW = 120         # seconds between nudges to the same silent agent

# How long after launch a claim is treated as the agent misreading its own boot
# prompt for a human instruction. Long enough to cover reading the briefing and
# reaching the first `wait`; short enough that a human typing at a new partner
# is rarely caught by it, and `claim --force` covers them when they are.
CLAIM_GRACE = 90

# A message addressed to an agent that it has not answered is re-delivered by
# `wait` once it is this old -- long enough that a reply already in progress is
# never mistaken for a dropped message, short enough that a real drop costs one
# cycle rather than a partner noticing the silence.
REDELIVER_AFTER = 45

REDELIVER_WINDOW = 90      # seconds between re-deliveries of the same backlog

# A polling `wait` refreshes its marker every cycle, so "is anyone listening"
# is answered by a heartbeat rather than by the marker merely existing: a wait
# that was killed stops refreshing, and another can take over within a cycle
# or two instead of waiting out a deadline that will never arrive.
MARKER_STALE = 15

LIVE_WINDOW = 360          # seconds; `wait` returns within WAIT_TIMEOUT and loops

IDLE_PAUSE = 60            # seconds every agent must sit finished before pausing

IDLE_CHECK = 15            # how often a polling `wait` re-asks the question

# Between two empty `wait`s an agent spends one model turn re-arming. A gap no
# longer than this continues its idle streak; a longer one means it was doing
# something, and the streak starts over.
REARM_GAP = 60

# A turn marker older than this is from a turn that never reached Stop -- an
# interrupt -- and stops counting as work in progress.
TURN_STALE = 1800

# A background `wait` needs an interpreter start before it writes its marker. A
# Stop firing right after one was armed looks for that marker this long before
# calling the agent deaf -- rather than trusting a recent `lastseen`, which any
# `send` refreshes and which is how a deaf session agent used to slip through.
ARM_GRACE = 5

WAKER_POLL = 1.0           # how often the waker looks for wake-up requests
WAKER_STALE = 10           # a waker heartbeat older than this is a dead waker
WAKER_RELAUNCH = 60        # at most one attempt per this many seconds to start one
WAKE_REQUEST_TTL = 120     # an older request describes a moment that has passed
WAKER_IDLE_EXIT = 6 * 3600 # nobody has done anything for this long: the waker exits

# Wake-ups typed into a tab whose agent never acts on them are going to a CLI
# that has exited -- killed, crashed, or quit -- and a shell is reading them.
# After this many in a row the tab is left alone and everyone is told to
# `restart` the agent instead.
UNANSWERED_NUDGES = 3
