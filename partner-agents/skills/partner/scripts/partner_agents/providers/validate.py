"""Checking a provider, model and effort before a tab is opened.

A problem stops the spawn and names the fix; a caution is this code not
recognising something, which is said out loud and then done anyway.
"""
from __future__ import annotations

from ..providers.binaries import find_binary
from ..providers.discovery import scan_clis
from ..providers.models import discover_models
from ..providers.resolve import resolve_provider
from ..util import NL, run_command


# --------------------------------------------------------------------------
# validation

def cli_runs(binary: str) -> bool:
    """Does the binary actually start? Catches broken or half-finished installs."""
    rc, _ = run_command([find_binary(binary) or binary, "--version"], 30)
    return rc == 0


def validate(provider: str, model: str | None, effort: str | None,
             cmd: str | None, force: bool = False) -> dict:
    """Check a partner's configuration before a tab is opened.

    Two severities, and the split is the point. A **problem** is something no
    amount of insisting fixes -- a CLI that is not installed, an effort value
    the API will reject -- and it stops the spawn. A **caution** is this script
    not recognising something, which is not evidence of anything: model names
    move faster than any check here, so an unrecognised model is said out loud
    and then used. Refusing until --force meant a model released last week
    needed a flag to try.
    """
    problems: list[str] = []
    cautions: list[str] = []

    if provider == "custom" and not cmd:
        return {"problems": ["--provider custom needs --cmd "
                             "'<template containing {prompt}>'"],
                "cautions": cautions}

    spec = resolve_provider(provider, cmd)
    binary = spec.get("bin") or ""
    if not binary:
        problems.append(f"no binary to run for '{provider}'. "
                        f"Give one with --cmd '<template>'.")
    elif not find_binary(binary):
        fix = (f"Install it with:  {spec['install']}" if spec.get("install")
               else "Install it, or point --cmd at the right binary.")
        near = [c["name"] for c in scan_clis() if c["installed"]][:8]
        problems.append(f"'{binary}' is not installed or not on PATH. {fix}"
                        + (f"{NL}    Installed here: {', '.join(near)}" if near else ""))
    elif not cli_runs(binary):
        cautions.append(f"`{binary} --version` did not exit cleanly. It may still "
                        f"work, but a broken install shows up as a tab that "
                        f"opens and closes.")

    if effort:
        if effort not in spec["efforts"]:
            valid = ", ".join(sorted(spec["efforts"]))
            extra = ""
            if spec.get("effort") == "flag" and effort == "max":
                extra = " ('max' is this skill's own level; 'high' is the real flag.)"
            problems.append(f"effort '{effort}' is not accepted by {provider}. "
                            f"Use one of: {valid}.{extra}")
        elif spec.get("effort") == "prompt":
            cautions.append(f"{provider} exposes no reasoning-effort flag, so "
                            f"--effort {effort} goes into the briefing as an "
                            f"instruction rather than a setting.")

    if model and not force and binary and find_binary(binary):
        known = {m["id"] for m in discover_models(provider, find_binary(binary),
                                                  budget=12)}
        if known and model not in known:
            sample = ", ".join(sorted(known)[:6])
            cautions.append(
                f"'{model}' is not among the ids this machine names for "
                f"{provider} ({sample}...). Not proof it is wrong -- a new model "
                f"is mentioned nowhere locally until it is used once -- but "
                f"worth checking the spelling.")
        elif not known:
            cautions.append(f"nothing on this machine names a {provider} model, "
                            f"so '{model}' could not be cross-checked.")

    if spec.get("kind") == "probed" and binary:
        pr = spec.get("probe", {})
        got = [pr[k] for k in ("model_flag", "effort_flag") if pr.get(k)]
        auto = "/".join(pr.get("auto", {}))
        derived = ", ".join(got) if got else "no model or effort flag found"
        if auto:
            derived += "; auto: " + auto
        cautions.append(
            f"no hand-verified recipe for '{provider}': its flags were read from "
            f"`{binary} --help` ({derived}). If the tab misbehaves, pass the "
            f"exact command with --cmd instead.")
    return {"problems": problems, "cautions": cautions}
