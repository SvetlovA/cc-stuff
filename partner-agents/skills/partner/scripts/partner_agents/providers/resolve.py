"""Resolving a provider name to one spec, whichever layer it comes from.

A hand-verified recipe, the user's own providers.json, or a probe of the CLI's
--help -- in that order -- all produce the same shape, so nothing downstream
has to know which one it got.
"""
from __future__ import annotations

import shlex

from ..prompts import EFFORT_HINT, GENERIC_WAIT_NOTE
from ..providers.overrides import load_overrides
from ..providers.probe import probe_cli
from ..providers.recipes import RECIPES


def wait_note(provider: str) -> str:
    """The wait guidance for one CLI: its own, or the rule that holds for all."""
    if not provider or provider == "custom":
        return GENERIC_WAIT_NOTE
    try:
        return provider_spec(provider).get("wait_note") or GENERIC_WAIT_NOTE
    except (OSError, ValueError, KeyError, SystemExit):
        return GENERIC_WAIT_NOTE


def resolve_provider(name: str, cmd: str | None = None) -> dict:
    """Everything needed to drive one CLI, from whichever source knows it.

    Recipe, then the user's own config, then the CLI's own help. That last
    fallback is why naming a CLI nobody here has heard of still produces a
    working tab instead of an error.
    """
    if name == "custom":
        return {"kind": "custom", "bin": (shlex.split(cmd or "")[:1] or [""])[0],
                "efforts": set(EFFORT_HINT), "effort": "template",
                "install": "", "cmd": cmd}
    ov = load_overrides().get(name)
    if ov:
        return {"kind": "config", "bin": (shlex.split(ov["cmd"])[:1] or [name])[0],
                "efforts": set(ov.get("efforts") or EFFORT_HINT),
                "effort": "template", "install": ov.get("install", ""),
                "cmd": ov["cmd"], "note": ov.get("note", ""),
                # A cap this plugin has never heard of is describable by the
                # person who has: the note goes into that CLI's briefings.
                "wait_note": ov.get("wait_note", ""),
                "source": ov.get("source", "")}
    if name in RECIPES:
        return {"kind": "recipe", **RECIPES[name]}
    pr = probe_cli(name)
    return {"kind": "probed", "bin": pr["bin"] or name, "probe": pr,
            "efforts": set(pr["efforts"]) if pr["efforts"] else set(EFFORT_HINT),
            "effort": "flag" if pr["effort_flag"] else "prompt",
            "install": "", "found": pr["found"]}


def provider_spec(prov: str) -> dict:
    return resolve_provider(prov)
