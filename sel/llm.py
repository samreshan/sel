"""Language-model clients that return JSON constrained by a schema.

Three backends, chosen with ``SEL_LLM_PROVIDER``:

* ``openai`` (default): any OpenAI-compatible ``/chat/completions`` endpoint.
  This is how Sel talks to *local* models (Ollama, llama.cpp ``llama-server``,
  LM Studio, vLLM) and also to hosted OpenAI-compatible APIs. Uses only the
  standard library.
* ``groq``: open-weight models hosted by Groq, through the same client
  (``GROQ_API_KEY``). Transcripts and pack text go to Groq's servers.
* ``anthropic``: the Claude API through the official ``anthropic`` SDK
  (``pip install anthropic``), using structured outputs.

Servers that refuse a strict ``json_schema`` response format (some
OpenAI-compatible servers, and Groq for most models) get JSON mode with the
schema in the prompt instead; the reply is parsed the same way.

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
GROQ_BASE_URL = "https://api.groq.com/openai/v1"
DEFAULT_GROQ_MODEL = "openai/gpt-oss-20b"  # open weights (Apache-2.0); supports strict json_schema on Groq
MAX_RATE_LIMIT_WAIT_S = 30.0


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
class OpenAICompatClient:
    base_url: str = DEFAULT_BASE_URL
    model: str = DEFAULT_LOCAL_MODEL
    api_key: str | None = None
    timeout: float = 900.0
    temperature: float = 0.0
    reasoning_effort: str | None = None  # sent only when set (Groq's gpt-oss models: low/medium/high)
    label: str = "openai-compatible"
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
    if provider == "groq":
        key = os.environ.get("SEL_LLM_API_KEY") or os.environ.get("GROQ_API_KEY")
        if not key:
            raise LLMError("SEL_LLM_PROVIDER=groq needs GROQ_API_KEY (or SEL_LLM_API_KEY)")
        model = os.environ.get("SEL_LLM_MODEL", DEFAULT_GROQ_MODEL)
        effort = os.environ.get("SEL_LLM_EFFORT") or None
        if effort is None and model.startswith("openai/gpt-oss"):
            effort = "low"  # its reasoning tokens share max_tokens; low leaves room for the JSON
        return OpenAICompatClient(
            base_url=os.environ.get("SEL_LLM_BASE_URL", GROQ_BASE_URL),
            model=model,
            api_key=key,
            timeout=120.0,
            reasoning_effort=effort,
            label="groq",
        )
    if provider != "openai":
        raise LLMError(f"unknown SEL_LLM_PROVIDER {provider!r}; use 'openai', 'groq' or 'anthropic'")
    return OpenAICompatClient(
        base_url=os.environ.get("SEL_LLM_BASE_URL", DEFAULT_BASE_URL),
        model=os.environ.get("SEL_LLM_MODEL", DEFAULT_LOCAL_MODEL),
        api_key=os.environ.get("SEL_LLM_API_KEY") or None,
        reasoning_effort=os.environ.get("SEL_LLM_EFFORT") or None,
    )
