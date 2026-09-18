"""Launch recipes for the agent CLIs whose flags were checked by hand.

Each provider needs one thing: how to start its normal interactive session
with a starting prompt and without stopping to ask permission for routine
work. Nothing is run headlessly, so there are no session ids to track and no
output formats to parse -- which is why an unlisted CLI needs only a template.

AUTO LEVELS
  ask    leave the CLI's own prompting alone
  edits  auto-accept file edits, still sandboxed        (default)
  full   no prompts and no sandbox

Flags verified against claude 2.x and codex 0.5x. They drift between
releases; this table is the single place to correct them.

RECIPES holds hand-verified launch flags for the CLIs whose flags were
checked by hand. It is an accelerator, not a permission list: every code path
falls back to probing when a name is not in it.
"""
from __future__ import annotations

from ..prompts import EFFORT_HINT


AUTO_LEVELS = ("ask", "edits", "full")


def _claude_tui(c: dict) -> list[str]:
    argv = ["claude"]
    if c["model"]:
        argv += ["--model", c["model"]]
    mode = {"edits": "acceptEdits", "full": "bypassPermissions"}.get(c["auto"])
    if mode:
        argv += ["--permission-mode", mode]
    return argv + [c["prompt"]]


def _codex_tui(c: dict) -> list[str]:
    argv = ["codex"]
    if c["model"]:
        argv += ["-m", c["model"]]
    if c["effort"]:
        argv += ["-c", f'model_reasoning_effort="{c["effort"]}"']
    if c["auto"] == "edits":
        argv += ["-a", "never", "-s", "workspace-write"]
    elif c["auto"] == "full":
        argv += ["--dangerously-bypass-approvals-and-sandbox"]
    return argv + [c["prompt"]]


def _gemini_tui(c: dict) -> list[str]:
    argv = ["gemini"]
    if c["model"]:
        argv += ["-m", c["model"]]
    mode = {"edits": "auto_edit", "full": "yolo"}.get(c["auto"])
    if mode:
        argv += ["--approval-mode", mode]
    return argv + ["-i", c["prompt"]]


RECIPES: dict[str, dict] = {
    "claude": {"bin": "claude", "tui": _claude_tui, "effort": "prompt",
               "efforts": set(EFFORT_HINT),
               "install": "npm install -g @anthropic-ai/claude-code",
               "login": "claude  (then /login)",
               "wait_note": (
                   "Your Bash tool times out at 120s by default (600s max), so "
                   "keep `wait` under that -- its own default is 90s -- or pass "
                   "a longer tool timeout. A wait that returns with nothing is "
                   "normal: run it again.")},
    "codex": {"bin": "codex", "tui": _codex_tui, "effort": "flag",
              # A real API field, so only values the API accepts work here.
              # "max" is this skill's own level -- it maps onto "high".
              "efforts": {"low", "medium", "high"},
              "install": "npm install -g @openai/codex",
              "login": "codex login",
              # Verified against codex 0.15x: the exec tool's yield_time_ms
              # defaults to 10s, which is shorter than any useful wait.
              "wait_note": (
                   "Your exec tool terminates a command after 10s by default, "
                   "which is shorter than a `wait`. Pass an explicit long "
                   "runtime every time you call it -- `timeout_ms` / "
                   "`yield_time_ms` of 600000, or a first-line "
                   "`// @exec: {\"yield_time_ms\": 600000}` pragma in code "
                   "mode. If it still returns early or empty, that is the cap "
                   "expiring, not an answer and not an error: run `wait` again "
                   "immediately, and never treat a short or silent wait as a "
                   "reason to end your turn.")},
    "gemini": {"bin": "gemini", "tui": _gemini_tui, "effort": "prompt",
               "efforts": set(EFFORT_HINT),
               "install": "npm install -g @google/gemini-cli",
               "login": "gemini  (then follow the browser prompt)"},
}

# Kept as an alias: older briefings and any external caller still say PROVIDERS.
PROVIDERS = RECIPES
