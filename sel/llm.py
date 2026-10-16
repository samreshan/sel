"""Language-model clients that return JSON constrained by a schema.

Sel uses open-weight models only. Two providers, chosen with
``SEL_LLM_PROVIDER``, share one standard-library client for the
``/chat/completions`` protocol:

* ``local`` (default): a server on this machine or your network: Ollama,
  llama.cpp ``llama-server``, LM Studio, vLLM. A call never leaves the
  machine. (``openai`` is accepted as an old name for this provider; it
  means the protocol, not the company.)
* ``groq``: open-weight models hosted by Groq (``GROQ_API_KEY``).
  Transcripts and pack text go to Groq's servers.

Proprietary model services (OpenAI, Anthropic, Google, Azure OpenAI, Cohere)
are refused by host name: see ``PROPRIETARY_HOSTS``.

Two clients are configured separately: ``SEL_LLM_*`` for call analysis and
coaching (accurate, runs in the background) and ``SEL_CHAT_LLM_*`` for work at
conversation speed (the AI customer and the Coach), which inherits anything
unset from ``SEL_LLM_*``.

Servers that refuse a strict ``json_schema`` response format (some
OpenAI-compatible servers, and Groq for most models) get JSON mode with the
schema in the prompt instead; the reply is parsed the same way.
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Protocol

DEFAULT_BASE_URL = "http://127.0.0.1:11434/v1"  # Ollama
DEFAULT_LOCAL_MODEL = "qwen2.5:7b"
GROQ_BASE_URL = "https://api.groq.com/openai/v1"
DEFAULT_GROQ_MODEL = "openai/gpt-oss-20b"  # open weights (Apache-2.0); supports strict json_schema on Groq
MAX_RATE_LIMIT_WAIT_S = 30.0
# Closed, paid model services. Sel's rule is open-weight models everywhere, so these are refused even
# when someone points SEL_LLM_BASE_URL at them.
PROPRIETARY_HOSTS = ("openai.com", "openai.azure.com", "anthropic.com", "googleapis.com", "cohere.ai", "cohere.com")


class LLMError(RuntimeError):
    pass


@dataclass
class LLMStats:
    calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    seconds: float = 0.0
    retries: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {**self.__dict__, "seconds": round(self.seconds, 2)}


class LLMClient(Protocol):
    name: str
    stats: LLMStats

    def complete_json(self, system: str, user: str, schema: dict[str, Any], name: str,
                      max_tokens: int = 2048) -> dict[str, Any]:
        ...


def _parse_json(text: str) -> dict[str, Any]:
    text = text.strip()
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.S)
    if fence:
        text = fence.group(1)
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            raise
        value = json.loads(text[start:end + 1])
    if not isinstance(value, dict):
        raise json.JSONDecodeError("expected a JSON object", text, 0)
    return value


def _schema_rejected(error: urllib.error.HTTPError, detail: str) -> bool:
    """True if a 400 says the server can't do the json_schema response format."""
    if error.code != 400:
        return False
    detail = detail.lower()
    return any(w in detail for w in ("response_format", "json_schema", "structured output", "schema"))


def _retry_after(error: urllib.error.HTTPError, attempt: int) -> float:
    try:
        wait = float(error.headers.get("Retry-After", ""))
    except (TypeError, ValueError):
        wait = 2.0 * 2 ** attempt
    return min(max(wait, 0.0), MAX_RATE_LIMIT_WAIT_S)


@dataclass
class ChatCompletionsClient:
    base_url: str = DEFAULT_BASE_URL
    model: str = DEFAULT_LOCAL_MODEL
    api_key: str | None = None
    timeout: float = 900.0
    temperature: float = 0.0
    reasoning_effort: str | None = None  # sent only when set (Groq's gpt-oss models: low/medium/high)
    label: str = "local"
    rate_limit_retries: int = 3
    stats: LLMStats = field(default_factory=LLMStats)
    sleep: Any = field(default=time.sleep, repr=False)
    # Flips to "json_object" once the server refuses json_schema, so later calls skip the failed attempt.
    response_mode: str = "json_schema"

    @property
    def name(self) -> str:
        return f"{self.label}:{self.model}@{self.base_url}"

    def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        req = urllib.request.Request(
            self.base_url.rstrip("/") + "/chat/completions",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json",
                     **({"Authorization": f"Bearer {self.api_key}"} if self.api_key else {})},
        )
        for attempt in range(self.rate_limit_retries + 1):
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    return json.loads(resp.read())
            except urllib.error.HTTPError as e:
                if e.code == 429 and attempt < self.rate_limit_retries:
                    self.stats.retries += 1
                    self.sleep(_retry_after(e, attempt))
                    continue
                raise
            except urllib.error.URLError as e:
                raise LLMError(f"cannot reach {self.base_url}: {e.reason}") from e
        raise AssertionError("unreachable")

    def _request(self, payload: dict[str, Any], schema: dict[str, Any], name: str) -> dict[str, Any]:
        """POST with the current response mode, falling back to JSON mode if json_schema is refused."""
        while True:
            body = dict(payload)
            messages = list(payload["messages"])
            if self.response_mode == "json_schema":
                body["response_format"] = {"type": "json_schema",
                                           "json_schema": {"name": name, "schema": schema, "strict": True}}
            else:
                messages[0] = {"role": "system", "content": messages[0]["content"]
                               + "\n\nReply with one JSON object that matches this JSON schema:\n"
                               + json.dumps(schema, separators=(",", ":"))}
                body["response_format"] = {"type": "json_object"}
            body["messages"] = messages
            try:
                return self._post(body)
            except urllib.error.HTTPError as e:
                detail = e.read()[:500].decode("utf-8", "replace")
                if self.response_mode == "json_schema" and _schema_rejected(e, detail):
                    self.response_mode = "json_object"
                    continue
                raise LLMError(f"{self.base_url} returned HTTP {e.code}: {detail}") from e

    def complete_json(self, system: str, user: str, schema: dict[str, Any], name: str,
                      max_tokens: int = 2048) -> dict[str, Any]:
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        last_error: Exception | None = None
        for attempt in range(2):
            payload: dict[str, Any] = {
                "model": self.model,
                "messages": messages,
                "temperature": self.temperature,
                "max_tokens": max_tokens,
            }
            if self.reasoning_effort:
                payload["reasoning_effort"] = self.reasoning_effort
            t = time.perf_counter()
            try:
                body = self._request(payload, schema, name)
            finally:
                self.stats.seconds += time.perf_counter() - t
            self.stats.calls += 1
            usage = body.get("usage") or {}
            self.stats.prompt_tokens += usage.get("prompt_tokens", 0)
            self.stats.completion_tokens += usage.get("completion_tokens", 0)
            choice = body["choices"][0]
            text = choice["message"].get("content") or ""
            try:
                return _parse_json(text)
            except json.JSONDecodeError as e:
                if choice.get("finish_reason") == "length":
                    raise LLMError(f"model hit max_tokens on {name}") from e
                last_error = e
                self.stats.retries += 1
                messages = messages + [
                    {"role": "assistant", "content": text},
                    {"role": "user", "content": "That was not valid JSON. Reply with only the JSON object."},
                ]
        raise LLMError(f"model did not return valid JSON for {name}: {last_error}")

    def health(self) -> dict[str, Any]:
        req = urllib.request.Request(self.base_url.rstrip("/") + "/models",
                                     headers={"Authorization": f"Bearer {self.api_key}"} if self.api_key else {})
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                ids = [m.get("id") for m in json.loads(resp.read()).get("data", [])]
            ok = self.model in ids or not ids
            return {"ok": ok, "models": ids, "error": None if ok else f"model {self.model!r} not served"}
        except Exception as e:  # noqa: BLE001 - reported to the user as-is
            return {"ok": False, "models": [], "error": str(e)}


def is_proprietary(base_url: str) -> bool:
    host = (urllib.parse.urlsplit(base_url).hostname or "").lower()
    return any(host == h or host.endswith("." + h) for h in PROPRIETARY_HOSTS)


def _checked(base_url: str) -> str:
    if is_proprietary(base_url):
        raise LLMError(f"{base_url} is a proprietary model service; Sel uses open-weight models only "
                       "(a local server, or SEL_LLM_PROVIDER=groq)")
    return base_url


def _settings(prefix: str, inherit: str | None) -> dict[str, str | None]:
    """``<prefix>_PROVIDER`` and friends; unset ones come from ``<inherit>_*`` while the provider is the same."""
    get = os.environ.get
    provider = (get(f"{prefix}_PROVIDER") or (get(f"{inherit}_PROVIDER") if inherit else None) or "local").lower()
    same = inherit is not None and provider == (get(f"{inherit}_PROVIDER") or "local").lower()
    out: dict[str, str | None] = {"provider": provider}
    for key in ("BASE_URL", "MODEL", "API_KEY", "EFFORT"):
        value = get(f"{prefix}_{key}")
        if value is None and same:
            value = get(f"{inherit}_{key}")
        out[key.lower()] = value
    return out


def client_from_env(prefix: str = "SEL_LLM", inherit: str | None = None, timeout: float | None = None,
                    temperature: float = 0.0, label_suffix: str = "") -> LLMClient:
    """The client configured by ``<prefix>_*`` variables (``SEL_LLM_*`` for call analysis)."""
    s = _settings(prefix, inherit)
    provider = s["provider"]
    if provider == "groq":
        key = s["api_key"] or os.environ.get("GROQ_API_KEY")
        if not key:
            raise LLMError(f"{prefix}_PROVIDER=groq needs GROQ_API_KEY (or {prefix}_API_KEY)")
        model = s["model"] or DEFAULT_GROQ_MODEL
        effort = s["effort"] or None
        if effort is None and model.startswith("openai/gpt-oss"):
            effort = "low"  # its reasoning tokens share max_tokens; low leaves room for the JSON
        return ChatCompletionsClient(
            base_url=_checked(s["base_url"] or GROQ_BASE_URL),
            model=model,
            api_key=key,
            timeout=timeout or 120.0,
            temperature=temperature,
            reasoning_effort=effort,
            label="groq" + label_suffix,
        )
    if provider not in ("local", "openai"):
        raise LLMError(f"unknown {prefix}_PROVIDER {provider!r}; use 'local' or 'groq'")
    return ChatCompletionsClient(
        base_url=_checked(s["base_url"] or DEFAULT_BASE_URL),
        model=s["model"] or DEFAULT_LOCAL_MODEL,
        api_key=s["api_key"] or None,
        timeout=timeout or 900.0,
        temperature=temperature,
        reasoning_effort=s["effort"] or None,
        label="local" + label_suffix,
    )


def chat_client_from_env(temperature: float = 0.0) -> LLMClient:
    """The fast client for conversation-speed work: the AI customer in drills and the Coach.

    Configured with ``SEL_CHAT_LLM_*``; anything unset is taken from ``SEL_LLM_*``
    (same server and model) as long as the provider is the same. Point it at a
    small open-weight model to keep replies within a few seconds.
    """
    return client_from_env("SEL_CHAT_LLM", inherit="SEL_LLM", timeout=60.0, temperature=temperature,
                           label_suffix="-chat")
