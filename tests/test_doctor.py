import os
import unittest
from unittest import mock

from sel.cli import main
from sel.doctor import check, model_licence


class DoctorTests(unittest.TestCase):
    def env(self, **values):
        clean = {k: v for k, v in os.environ.items() if not k.startswith("SEL_LLM_") and k != "GROQ_API_KEY"}
        patcher = mock.patch.dict(os.environ, {**clean, **values}, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_default_is_local_and_open(self):
        self.env()
        report = check(probe=False)
        self.assertTrue(report["ok"])
        self.assertEqual(report["llm"]["runs_on"], "this machine")
        self.assertEqual(report["llm"]["licence"], "Apache-2.0")

    def test_lan_server_is_your_network(self):
        self.env(SEL_LLM_BASE_URL="http://192.168.1.20:11434/v1")
        self.assertEqual(check(probe=False)["llm"]["runs_on"], "your network")

    def test_groq_is_reported_as_hosted(self):
        self.env(SEL_LLM_PROVIDER="groq", GROQ_API_KEY="k")
        report = check(probe=False)
        self.assertTrue(report["ok"])
        self.assertIn("leave the machine", report["llm"]["runs_on"])

    def test_proprietary_endpoint_fails(self):
        self.env(SEL_LLM_BASE_URL="https://api.anthropic.com/v1")
        report = check(probe=False)
        self.assertFalse(report["ok"])
        with mock.patch("builtins.print"):
            self.assertEqual(main(["doctor", "--offline"]), 1)

    def test_licences(self):
        self.assertEqual(model_licence("qwen2.5:7b"), "Apache-2.0")
        self.assertIn("non-commercial", model_licence("qwen2.5:3b"))
        self.assertTrue(model_licence("some-new-model").startswith("unknown"))


if __name__ == "__main__":
    unittest.main()
