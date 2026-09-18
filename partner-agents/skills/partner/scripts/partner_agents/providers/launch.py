"""Building the command line that starts a partner's CLI in its tab."""
from __future__ import annotations

import shlex

from ..providers.resolve import resolve_provider


def _custom_tui(template: str, c: dict) -> list[str]:
    """Any CLI, described by a template string.

    Placeholders: {prompt} {model} {effort} {auto}
    e.g. --cmd 'aider --model {model} --yes --message {prompt}'
    A template without {prompt} gets the starting prompt appended last.
    """
    out, saw = [], False
    for part in shlex.split(template):
        if "{prompt}" in part:
            saw = True
        out.append(part.replace("{prompt}", c["prompt"])
                       .replace("{model}", c["model"] or "")
                       .replace("{effort}", c["effort"] or "")
                       .replace("{auto}", c["auto"] or ""))
    out = [x for x in out if x != ""]
    if not saw:
        out.append(c["prompt"])
    return out


def _probed_tui(pr: dict, c: dict) -> list[str]:
    """Drive a CLI nobody wrote a recipe for, from what its --help admitted to.

    Conservative on purpose: a flag that was not found is a flag that is not
    passed. A partner launched with no model flag runs on the CLI's own default
    model, which is a working tab; a partner launched with a guessed flag is a
    tab that prints a usage error and closes.
    """
    argv = [pr["bin"] or pr["name"]]
    if c["model"] and pr.get("model_flag"):
        argv += [pr["model_flag"], c["model"]]
    if c["effort"] and pr.get("effort_flag"):
        argv += [pr["effort_flag"], c["effort"]]
    argv += pr.get("auto", {}).get(c["auto"], [])
    if pr.get("prompt_flag"):
        argv += [pr["prompt_flag"], c["prompt"]]
        return argv
    return argv + [c["prompt"]]


def build_tui(p: dict, prompt: str) -> list[str]:
    c = {"prompt": prompt, "model": p.get("model") or "",
         "effort": p.get("effort") or "", "auto": p.get("auto") or "edits"}
    prov = p["provider"]
    if prov == "custom":
        return _custom_tui(p.get("cmd") or "", c)
    spec = resolve_provider(prov, p.get("cmd"))
    if spec["kind"] == "recipe":
        return spec["tui"](c)
    if spec["kind"] == "config":
        # A CLI the user described in their own providers.json: same template
        # language as --cmd, so there is one substitution path, not two.
        return _custom_tui(spec["cmd"], c)
    if spec.get("found"):
        return _probed_tui(spec["probe"], c)
    raise SystemExit(f"{prov!r} is not installed and has no --cmd template")
