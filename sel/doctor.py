"""``python -m sel doctor``: which models Sel would use, where they run, and under which licence.

Sel's rule is open-weight models only, everywhere (docs/decisions.md, MODELS.md). This check makes
that easy to verify: it reads the same environment variables the app reads, reports every model with
its licence, says whether data leaves the machine, and fails if anything proprietary is configured.
"""

from __future__ import annotations

import importlib.util
import ipaddress
import os
import urllib.parse
from typing import Any

from .asr import DEFAULT_MODEL as WHISPER_DEFAULT, live_model_name
from .llm import LLMError, client_from_env

# Licences of the models Sel is documented with (MODELS.md). Matched by prefix on the lower-cased
# model name; anything else is reported as "unknown: check the model card".
MODEL_LICENCES: tuple[tuple[str, str], ...] = (
    ("qwen2.5:3b", "Qwen Research License (non-commercial)"),
    ("qwen/qwen2.5-3b", "Qwen Research License (non-commercial)"),
    ("qwen2.5:72b", "Qwen License"),
    ("qwen2.5", "Apache-2.0"),
    ("qwen3", "Apache-2.0"),
    ("qwen/qwen3", "Apache-2.0"),
    ("openai/gpt-oss", "Apache-2.0 (open weights)"),
    ("gpt-oss", "Apache-2.0 (open weights)"),
    ("llama", "Llama Community License (open weights)"),
    ("meta-llama/", "Llama Community License (open weights)"),
    ("mistral", "Apache-2.0"),
    ("gemma", "Gemma Terms of Use (open weights)"),
)

_LOCAL_HOSTS = ("127.0.0.1", "localhost", "::1")


def model_licence(name: str) -> str:
    n = name.lower()
    return next((lic for prefix, lic in MODEL_LICENCES if n.startswith(prefix)), "unknown: check the model card")


def _where(base_url: str) -> str:
    host = (urllib.parse.urlsplit(base_url).hostname or "").lower()
    if host in _LOCAL_HOSTS:
        return "this machine"
    try:
        private = ipaddress.ip_address(host).is_private
    except ValueError:
        private = host.endswith(".local") or "." not in host
    if private:
        return "your network"
    return f"hosted ({host}): transcripts leave the machine"


def check(probe: bool = True) -> dict[str, Any]:
    """Everything the doctor reports; ``ok`` is False when a model would break the open-models rule."""
    problems: list[str] = []
    out: dict[str, Any] = {"provider": os.environ.get("SEL_LLM_PROVIDER", "local").lower()}
    try:
        llm = client_from_env()
        out["llm"] = {
            "model": llm.model,
            "endpoint": llm.base_url,
            "runs_on": _where(llm.base_url),
            "licence": model_licence(llm.model),
        }
        if probe:
            out["llm"]["health"] = llm.health()
    except LLMError as e:
        out["llm"] = {"error": str(e)}
        if "proprietary" in str(e) or "unknown SEL_LLM_PROVIDER" in str(e):
            problems.append(str(e))
    out["speech"] = {
        "uploads": f"Whisper {os.environ.get('SEL_WHISPER_MODEL', WHISPER_DEFAULT)} (MIT) via faster-whisper",
        "live": f"Whisper {live_model_name()} (MIT) via faster-whisper",
        "runs_on": "this machine",
        "installed": importlib.util.find_spec("faster_whisper") is not None,
    }
    out["browser_speech_services"] = "none (browser captions removed; typed lines or Whisper only)"
    out["ok"] = not problems
    out["problems"] = problems
    return out


def render(report: dict[str, Any]) -> str:
    lines = ["Sel doctor: open models only", ""]
    llm = report["llm"]
    if "error" in llm:
        lines.append(f"  Language model   {llm['error']}")
    else:
        health = llm.get("health")
        state = "" if health is None else ("  reachable" if health.get("ok") else f"  NOT reachable: {health.get('error')}")
        lines += [
            f"  Language model   {llm['model']}  [{llm['licence']}]{state}",
            f"                   {report['provider']} provider at {llm['endpoint']} ({llm['runs_on']})",
        ]
    sp = report["speech"]
    lines += [
        f"  Speech (uploads) {sp['uploads']}, {sp['runs_on']}" + ("" if sp["installed"] else "  (not installed)"),
        f"  Speech (live)    {sp['live']}, {sp['runs_on']}",
        f"  Browser speech   {report['browser_speech_services']}",
        "",
        "OK: no proprietary model service is configured." if report["ok"]
        else "FAIL: " + "; ".join(report["problems"]),
    ]
    return "\n".join(lines)
