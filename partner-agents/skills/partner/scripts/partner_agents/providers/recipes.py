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
              "wait_note": (
                   "exec_command may yield a session_id while the process is "
                   "still running. Poll that id with write_stdin until exit; "
                   "yield_time_ms controls when output is returned, not the "
                   "process lifetime. In code mode, if functions.exec yields "
                   "a cell_id, resume it with functions.wait. Keep exactly one "
                   "partner wait running. Re-arm only after it exits and its "
                   "messages are handled; an empty timeout is normal.")},
    "gemini": {"bin": "gemini", "tui": _gemini_tui, "effort": "prompt",
               "efforts": set(EFFORT_HINT),
               "install": "npm install -g @google/gemini-cli",
               "login": "gemini  (then follow the browser prompt)"},
}

# Kept as an alias: older briefings and any external caller still say PROVIDERS.
PROVIDERS = RECIPES
