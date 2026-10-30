import json
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock

from agent.agent import ToolCall, Turn, run_agent
from agent.models.gemini import Gemini, tool_declarations


class AgentTests(unittest.TestCase):
    def test_agent_sends_every_call_to_gateway_and_feeds_block_back(self):
        call = ToolCall("read_file", {"path": "secret.txt"}, "model-call-1")
        model = Mock()
        model.start.return_value = Turn("", (call,))
        model.resume.return_value = Turn("Access blocked.")
        response = {"version": "1.0", "request_id": "x", "decision": "BLOCK", "reason_code": "NOT_ALLOWED",
                    "reason": "No matching allowlist grant.", "execution": None}
        gateway = Mock(return_value=response)
        self.assertEqual(run_agent("read secret", model=model, gateway=gateway), "Access blocked.")
        request = gateway.call_args.args[0]
        self.assertEqual(set(request), {"version", "request_id", "tool", "arguments"})
        self.assertEqual(request["tool"], "read_file")
        self.assertEqual(request["arguments"], {"path": "secret.txt"})
        model.resume.assert_called_once_with([(call, response)])

    def test_gemini_preserves_raw_signature_ids_and_all_parallel_calls(self):
        raw = {"role": "model", "parts": [
            {"functionCall": {"name": "read_file", "args": {"path": "public.txt"}, "id": "id1"},
             "thoughtSignature": "opaque-signature"},
            {"functionCall": {"name": "list_files", "args": {"path": "."}, "id": "id2"}}]}
        payloads = []
        def transport(payload):
            payloads.append(json.loads(json.dumps(payload)))
            return {"candidates": [{"finishReason": "STOP", "content": raw if len(payloads) == 1 else
                                     {"role": "model", "parts": [{"text": "done"}]}}]}
        model = Gemini(api_key="fake-key", model="test-model", transport=transport)
        gateway = Mock(return_value={"decision": "BLOCK", "execution": None})
        self.assertEqual(run_agent("test", model=model, gateway=gateway), "done")
        self.assertEqual(gateway.call_count, 2)
        self.assertEqual(payloads[1]["contents"][1], raw)
        responses = payloads[1]["contents"][2]["parts"]
        self.assertEqual([p["functionResponse"]["id"] for p in responses], ["id1", "id2"])
        self.assertNotIn("fake-key", json.dumps(payloads))
        self.assertEqual(len(tool_declarations()), 5)

    def test_model_errors_do_not_expose_raw_response(self):
        model = Gemini(api_key="fake", model="test", transport=lambda _: {"promptFeedback": "blocked"})
        with self.assertRaisesRegex(RuntimeError, "invalid or blocked"):
            model.start("prompt")

    def test_agent_call_budget(self):
        model = Mock()
        turn = Turn("", (ToolCall("read_file", {"path": "public.txt"}),) * 17)
        model.start.return_value = turn
        gateway = Mock(return_value={})
        with self.assertRaisesRegex(RuntimeError, "tool-call limit"):
            run_agent("test", model=model, gateway=gateway)
        self.assertEqual(gateway.call_count, 16)

    def test_cli_offline_json_and_duplicate_member_block(self):
        root = Path(__file__).resolve().parents[1]
        request = {"version": "1.0", "request_id": "cli-1", "tool": "read_file", "arguments": {"path": "public.txt"}}
        invalid = '{"version":"1.0","version":"1.0","request_id":"x","tool":"delete_file","arguments":{"path":"public.txt"}}'
        result = subprocess.run([sys.executable, str(root / "main.py")],
                                input=(json.dumps(request) + "\n" + invalid + "\n").encode(),
                                capture_output=True, cwd=root, check=True)
        responses = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual(responses[0]["decision"], "ALLOW", responses)
        self.assertEqual(responses[1]["decision"], "BLOCK")
        self.assertEqual(responses[1]["reason_code"], "INVALID_REQUEST")

    def test_cli_excessive_json_depth_is_blocked(self):
        root = Path(__file__).resolve().parents[1]
        nested = "[" * 2000 + "0" + "]" * 2000
        result = subprocess.run([sys.executable, str(root / "main.py")], input=(nested + "\n").encode(),
                                capture_output=True, cwd=root, check=True)
        self.assertEqual(json.loads(result.stdout)["decision"], "BLOCK")


if __name__ == "__main__":
    unittest.main()
