"""Language-model clients that return JSON constrained by a schema.

Two backends, chosen with ``SEL_LLM_PROVIDER``:

* ``openai`` (default): any OpenAI-compatible ``/chat/completions`` endpoint.
  This is how Sel talks to *local* models (Ollama, llama.cpp ``llama-server``,
  LM Studio, vLLM) and also to hosted OpenAI-compatible APIs. Uses only the
  standard library.
* ``anthropic``: the Claude API through the official ``anthropic`` SDK
  (``pip install anthropic``), using structured outputs.

Sel's default is a local model, so a call never leaves the machine unless
the user points ``SEL_LLM_BASE_URL`` or the provider at a hosted service.
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Protocol

DEFAULT_BASE_URL = "http://127.0.0.1:11434/v1"  # Ollama
DEFAULT_LOCAL_MODEL = "qwen2.5:7b"
DEFAULT_ANTHROPIC_MODEL = "claude-opus-5-5"


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


@dataclass
class OpenAICompatClient:
    base_url: str = DEFAULT_BASE_URL
    model: str = DEFAULT_LOCAL_MODEL
    api_key: str | None = None
    timeout: float = 900.0
    temperature: float = 0.0
    stats: LLMStats = field(default_factory=LLMStats)

    @property
    def name(self) -> str:
        return f"openai-compatible:{self.model}@{self.base_url}"

    def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        req = urllib.request.Request(
            self.base_url.rstrip("/") + "/chat/completions",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json",
                     **({"Authorization": f"Bearer {self.api_key}"} if self.api_key else {})},
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            raise LLMError(f"{self.base_url} returned HTTP {e.code}: {e.read()[:500]!r}") from e
        except urllib.error.URLError as e:
            raise LLMError(f"cannot reach {self.base_url}: {e.reason}") from e

    def complete_json(self, system: str, user: str, schema: dict[str, Any], name: str,
                      max_tokens: int = 2048) -> dict[str, Any]:
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        last_error: Exception | None = None
        for attempt in range(2):
            t = time.perf_counter()
            body = self._post({
                "model": self.model,
                "messages": messages,
                "temperature": self.temperature,
                "max_tokens": max_tokens,
                "response_format": {"type": "json_schema",
                                    "json_schema": {"name": name, "schema": schema, "strict": True}},
            })
            self.stats.seconds += time.perf_counter() - t
            self.stats.calls += 1
            usage = body.get("usage") or {}
            self.stats.prompt_tokens += usage.get("prompt_tokens", 0)
            self.stats.completion_tokens += usage.get("completion_tokens", 0)
            text = body["choices"][0]["message"].get("content") or ""
            try:
                return _parse_json(text)
            except json.JSONDecodeError as e:
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


@dataclass
class AnthropicClient:
    model: str = DEFAULT_ANTHROPIC_MODEL
    effort: str | None = None
    stats: LLMStats = field(default_factory=LLMStats)

    def __post_init__(self) -> None:
        try:
            import anthropic
        except ImportError as e:
            raise LLMError("SEL_LLM_PROVIDER=anthropic needs the SDK: pip install anthropic") from e
        self._anthropic = anthropic
        self._client = anthropic.Anthropic()

    @property
    def name(self) -> str:
        return f"anthropic:{self.model}"

    def complete_json(self, system: str, user: str, schema: dict[str, Any], name: str,
                      max_tokens: int = 2048) -> dict[str, Any]:
        output_config: dict[str, Any] = {"format": {"type": "json_schema", "schema": schema}}
        if self.effort:
            output_config["effort"] = self.effort
        t = time.perf_counter()
        try:
            response = self._client.messages.create(
                model=self.model,
                # Thinking tokens share this budget; leave headroom beyond the JSON itself.
                max_tokens=max(max_tokens * 4, 16000),
                system=system,
                messages=[{"role": "user", "content": user}],
                output_config=output_config,
            )
        except self._anthropic.APIStatusError as e:
            raise LLMError(f"Claude API error {e.status_code}: {e.message}") from e
        except self._anthropic.APIConnectionError as e:
            raise LLMError(f"cannot reach the Claude API: {e}") from e
        finally:
            self.stats.seconds += time.perf_counter() - t
        self.stats.calls += 1
        self.stats.prompt_tokens += response.usage.input_tokens
        self.stats.completion_tokens += response.usage.output_tokens
        if response.stop_reason == "refusal":
            raise LLMError(f"Claude declined the {name} request")
        if response.stop_reason == "max_tokens":
            raise LLMError(f"Claude hit max_tokens on {name}")
        text = next((b.text for b in response.content if b.type == "text"), "")
        return _parse_json(text)

    def health(self) -> dict[str, Any]:
        return {"ok": True, "models": [self.model], "error": None}


def client_from_env() -> LLMClient:
    provider = os.environ.get("SEL_LLM_PROVIDER", "openai").lower()
    if provider == "anthropic":
        return AnthropicClient(
            model=os.environ.get("SEL_LLM_MODEL", DEFAULT_ANTHROPIC_MODEL),
            effort=os.environ.get("SEL_LLM_EFFORT") or None,
        )
    if provider != "openai":
        raise LLMError(f"unknown SEL_LLM_PROVIDER {provider!r}; use 'openai' or 'anthropic'")
    return OpenAICompatClient(
        base_url=os.environ.get("SEL_LLM_BASE_URL", DEFAULT_BASE_URL),
        model=os.environ.get("SEL_LLM_MODEL", DEFAULT_LOCAL_MODEL),
        api_key=os.environ.get("SEL_LLM_API_KEY") or None,
    )
