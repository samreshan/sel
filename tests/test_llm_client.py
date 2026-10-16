import json
import os
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

from sel.llm import GROQ_BASE_URL, LLMError, OpenAICompatClient, client_from_env

SCHEMA = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"],
          "additionalProperties": False}


class FakeServer:
    """An OpenAI-compatible /chat/completions endpoint that replays scripted responses."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                fake.requests.append({"body": body, "auth": self.headers.get("Authorization")})
                status, payload, headers = fake.responses.pop(0)
                data = json.dumps(payload).encode()
                self.send_response(status)
                for k, v in headers.items():
                    self.send_header(k, v)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args):
                pass

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.httpd.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}/v1"

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


def ok(content, finish="stop"):
    return 200, {"choices": [{"message": {"content": content}, "finish_reason": finish}],
                 "usage": {"prompt_tokens": 10, "completion_tokens": 3}}, {}


class OpenAICompatClientTests(unittest.TestCase):
    def serve(self, *responses):
        server = FakeServer(responses)
        self.addCleanup(server.close)
        return server

    def test_strict_json_schema_by_default(self):
        server = self.serve(ok('{"ok": true}'))
        client = OpenAICompatClient(base_url=server.url, model="m", api_key="k")
        self.assertEqual(client.complete_json("sys", "user", SCHEMA, "check"), {"ok": True})
        body = server.requests[0]["body"]
        self.assertEqual(body["response_format"]["type"], "json_schema")
        self.assertTrue(body["response_format"]["json_schema"]["strict"])
        self.assertNotIn("reasoning_effort", body)
        self.assertEqual(server.requests[0]["auth"], "Bearer k")
        self.assertEqual(client.stats.prompt_tokens, 10)

    def test_falls_back_to_json_mode_when_schema_is_refused(self):
        refused = (400, {"error": {"message": "response_format `json_schema` is not supported by this model"}}, {})
        server = self.serve(refused, ok('{"ok": true}'), ok('{"ok": false}'))
        client = OpenAICompatClient(base_url=server.url, model="m")
        self.assertEqual(client.complete_json("sys", "user", SCHEMA, "check"), {"ok": True})
        retry = server.requests[1]["body"]
        self.assertEqual(retry["response_format"], {"type": "json_object"})
        self.assertIn('"required":["ok"]', retry["messages"][0]["content"])
        # Remembered: the next call goes straight to JSON mode.
        self.assertEqual(client.complete_json("sys", "user", SCHEMA, "check"), {"ok": False})
        self.assertEqual(len(server.requests), 3)
        self.assertEqual(server.requests[2]["body"]["response_format"], {"type": "json_object"})

    def test_other_client_errors_are_not_retried(self):
        server = self.serve((401, {"error": {"message": "invalid api key"}}, {}))
        client = OpenAICompatClient(base_url=server.url, model="m")
        with self.assertRaisesRegex(LLMError, "HTTP 401.*invalid api key"):
            client.complete_json("sys", "user", SCHEMA, "check")
        self.assertEqual(len(server.requests), 1)

    def test_rate_limit_waits_for_retry_after(self):
        limited = (429, {"error": {"message": "rate limit"}}, {"Retry-After": "7"})
        server = self.serve(limited, ok('{"ok": true}'))
        waits = []
        client = OpenAICompatClient(base_url=server.url, model="m", sleep=waits.append)
        self.assertEqual(client.complete_json("sys", "user", SCHEMA, "check"), {"ok": True})
        self.assertEqual(waits, [7.0])
        self.assertEqual(client.stats.retries, 1)

    def test_rate_limit_gives_up_after_retries(self):
        limited = (429, {"error": {"message": "rate limit"}}, {"Retry-After": "999"})
        server = self.serve(limited, limited)
        waits = []
        client = OpenAICompatClient(base_url=server.url, model="m", rate_limit_retries=1, sleep=waits.append)
        with self.assertRaisesRegex(LLMError, "HTTP 429"):
            client.complete_json("sys", "user", SCHEMA, "check")
        self.assertEqual(waits, [30.0])  # capped

    def test_truncated_reply_is_reported_not_retried(self):
        server = self.serve(ok('{"ok": tr', finish="length"))
        client = OpenAICompatClient(base_url=server.url, model="m")
        with self.assertRaisesRegex(LLMError, "max_tokens"):
            client.complete_json("sys", "user", SCHEMA, "check")
        self.assertEqual(len(server.requests), 1)

    def test_reasoning_effort_is_sent_when_set(self):
        server = self.serve(ok('{"ok": true}'))
        OpenAICompatClient(base_url=server.url, model="m", reasoning_effort="low").complete_json(
            "sys", "user", SCHEMA, "check")
        self.assertEqual(server.requests[0]["body"]["reasoning_effort"], "low")


class ProviderFromEnvTests(unittest.TestCase):
    def env(self, **values):
        clean = {k: v for k, v in os.environ.items() if not k.startswith("SEL_LLM_") and k != "GROQ_API_KEY"}
        patcher = mock.patch.dict(os.environ, {**clean, **values}, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_groq_defaults(self):
        self.env(SEL_LLM_PROVIDER="groq", GROQ_API_KEY="gsk_test")
        client = client_from_env()
        self.assertEqual(client.base_url, GROQ_BASE_URL)
        self.assertEqual(client.api_key, "gsk_test")
        self.assertEqual(client.model, "openai/gpt-oss-20b")
        self.assertEqual(client.reasoning_effort, "low")
        self.assertTrue(client.name.startswith("groq:"))

    def test_groq_model_and_effort_overrides(self):
        self.env(SEL_LLM_PROVIDER="groq", SEL_LLM_API_KEY="k2", GROQ_API_KEY="k1",
                 SEL_LLM_MODEL="qwen/qwen3-32b", SEL_LLM_EFFORT="default")
        client = client_from_env()
        self.assertEqual((client.api_key, client.model, client.reasoning_effort), ("k2", "qwen/qwen3-32b", "default"))

    def test_groq_non_reasoning_model_sends_no_effort(self):
        self.env(SEL_LLM_PROVIDER="groq", GROQ_API_KEY="k", SEL_LLM_MODEL="llama-3.3-70b-versatile")
        self.assertIsNone(client_from_env().reasoning_effort)

    def test_groq_without_key_is_an_llm_error(self):
        self.env(SEL_LLM_PROVIDER="groq")
        with self.assertRaisesRegex(LLMError, "GROQ_API_KEY"):
            client_from_env()

    def test_local_default_unchanged(self):
        self.env()
        client = client_from_env()
        self.assertEqual((client.base_url, client.model, client.api_key), ("http://127.0.0.1:11434/v1", "qwen2.5:7b", None))

    def test_unknown_provider(self):
        self.env(SEL_LLM_PROVIDER="nope")
        with self.assertRaisesRegex(LLMError, "groq"):
            client_from_env()


if __name__ == "__main__":
    unittest.main()
