"""Scanning this machine for agent CLIs.

A scan suggests, it does not gate: whatever it finds is offered, and a binary
it missed is still usable by naming it.
"""
from __future__ import annotations

import json
import re
import shlex
import time

from ..providers.binaries import _exe_exts, find_binary, search_dirs
from ..providers.overrides import load_overrides
from ..providers.probe import probe_cli
from ..providers.recipes import RECIPES
from ..state import cache_dir


# Name signals that make a binary worth surfacing in the *fast* scan. These are
# accelerators, not a whitelist: `--deep` classifies by behaviour instead, and
# naming any binary directly bypasses this regex entirely. Product names are in
# here only because they are the cheapest possible hit; a CLI called something
# nobody here predicted is still reachable by the other two routes.
LIKELY_AGENT_RE = re.compile(
    r"(?:^|[-_])(?:ai|llm|gpt|agent|agents|assistant|bot|chat|code|coder|"
    r"copilot|cli|pair)(?:$|[-_])"
    r"|(?:ai|gpt|llm|agent|assistant|coder|copilot|code)$"
    r"|^(?:claude|codex|gemini|aider|cursor|goose|amp|crush|qwen|grok|deepseek|"
    r"mistral|ollama|opencode|droid|cline|continue|kilo|windsurf|zed|openhands|"
    r"plandex|mentat|smol)",
    re.I)

# Names that match the heuristic but are never an agent CLI.
NOT_A_CLI = {"code", "code-insiders", "codesign", "clip", "clipboard",
             "devenv", "devcon", "aiff", "chattr", "codepage"}


def _stem(name: str) -> str:
    low = name.lower()
    for e in _exe_exts():
        if e and low.endswith(e):
            return low[: -len(e)]
    return low


def scan_clis(deep: bool = False, budget: float = 45.0) -> list[dict]:
    """Agent CLIs present on this machine, found rather than assumed.

    The name is only a prefilter; **behaviour decides**. Every candidate is
    asked for its --help, and it is listed only if that help reads like an
    agent CLI -- a model flag, a prompt, a session, a way to stop it asking
    permission. Name alone was tried first and it was useless in both
    directions: it let in gpg-agent and ssh-agent, and it would still have
    missed any CLI whose name gives nothing away.

    `deep` drops the name prefilter and probes every binary in those
    directories instead, which is the version that finds the CLI nobody here
    could have predicted the name of. It costs a subprocess per binary, so it
    is opt-in and bounded by `budget`.
    """
    found: dict[str, dict] = {}
    for name, spec in RECIPES.items():
        path = find_binary(spec["bin"])
        found[name] = {"name": name, "path": path, "installed": bool(path),
                       "how": "recipe", "install": spec["install"]}
    for name, ov in load_overrides().items():
        binary = (shlex.split(ov.get("cmd", ""))[:1] or [name])[0]
        path = find_binary(binary)
        found[name] = {"name": name, "path": path, "installed": bool(path),
                       "how": "your config", "install": ov.get("install", "")}

    exts = _exe_exts()
    seen: set[str] = set()
    deadline = time.time() + budget
    for d in search_dirs():
        try:
            entries = sorted(d.iterdir())
        except OSError:
            continue
        for f in entries:
            if time.time() > deadline:
                break
            if exts != ("",) and not f.name.lower().endswith(exts):
                continue
            stem = _stem(f.name)
            if stem in found or stem in NOT_A_CLI or stem in seen:
                continue
            if "." in stem or len(stem) < 2:
                continue
            if not deep and not LIKELY_AGENT_RE.search(stem):
                continue
            try:
                if not f.is_file():
                    continue
            except OSError:
                continue
            seen.add(stem)
            if probe_cli(stem, str(f), timeout=8).get("agentic"):
                found[stem] = {"name": stem, "path": str(f), "installed": True,
                               "how": "found here", "install": ""}
    try:
        (cache_dir() / "clis.json").write_text(json.dumps(
            {k: v["path"] for k, v in found.items() if v["path"]}),
            encoding="utf-8")
    except OSError:
        pass
    return sorted(found.values(), key=lambda r: (not r["installed"], r["name"]))
