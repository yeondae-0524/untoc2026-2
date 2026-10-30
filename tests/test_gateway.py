"""Gateway failure boundaries, independent of the OS file Tool implementation."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from agentfence.gateway import build_gateway
from agentfence.policy import Policy


class GatewayTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="agentfence-gateway-")
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.workspace = self.base / "workspace"
        self.workspace.mkdir()
        self.target = self.workspace / "notes.txt"
        self.target.write_text("before", encoding="utf-8")
        self.policy_path = self.base / "policy.json"
        self.policy_path.write_text(json.dumps({"version": "1.0", "allow": [
            {"tool": "write_file", "path": "notes.txt"}
        ]}), encoding="utf-8")
        self.request = {"version": "1.0", "request_id": "gateway-1", "tool": "write_file",
                        "arguments": {"path": "notes.txt", "content": "after"}}

    def build(self, dispatch):
        with patch("agentfence.gateway._make_dispatch", return_value=dispatch):
            return build_gateway(workspace=self.workspace, policy_path=self.policy_path)

    def assert_blocked_without_execution(self, response, dispatch, code):
        self.assertEqual(response["decision"], "BLOCK", response)
        self.assertEqual(response["reason_code"], code)
        self.assertIsNone(response["execution"])
        dispatch.assert_not_called()
        self.assertEqual(self.target.read_text(encoding="utf-8"), "before")

    def test_policy_exceptions_never_reach_executor(self):
        dispatch = Mock()
        handle = self.build(dispatch)
        for error in [OSError("PRIVATE_DETAILS"), UnicodeError("PRIVATE_DETAILS"), RuntimeError("PRIVATE_DETAILS")]:
            with self.subTest(error=type(error).__name__):
                with patch.object(Policy, "allows", side_effect=error):
                    response = handle(self.request)
                self.assert_blocked_without_execution(response, dispatch, "POLICY_ERROR")
                self.assertNotIn("PRIVATE_DETAILS", json.dumps(response))

    def test_invalid_request_never_calls_policy_or_executor(self):
        dispatch = Mock()
        handle = self.build(dispatch)
        with patch.object(Policy, "allows") as check:
            response = handle({**self.request, "arguments": {"path": "notes.txt", "content": 123}})
        check.assert_not_called()
        self.assert_blocked_without_execution(response, dispatch, "INVALID_ARGUMENTS")

    def test_invalid_configuration_returns_blocking_handler(self):
        with patch("agentfence.gateway._make_dispatch") as factory:
            for workspace in [None, "bad\x00path"]:
                with self.subTest(workspace=workspace):
                    handle = build_gateway(workspace=workspace, policy_path=self.policy_path)
                    response = handle(self.request)
                    self.assertEqual(response["reason_code"], "CONFIG_ERROR")
                    self.assertEqual(response["decision"], "BLOCK")
                    self.assertIsNone(response["execution"])
            factory.assert_not_called()

    def test_startup_exception_returns_blocking_handler(self):
        with patch("agentfence.gateway._make_dispatch", side_effect=RuntimeError("PRIVATE_DETAILS")):
            handle = build_gateway(workspace=self.workspace, policy_path=self.policy_path)
        response = handle(self.request)
        self.assertEqual(response["decision"], "BLOCK")
        self.assertEqual(response["reason_code"], "INTERNAL_ERROR")
        self.assertIsNone(response["execution"])
        self.assertNotIn("PRIVATE_DETAILS", json.dumps(response))

    def test_unexpected_failure_after_write_is_an_execution_error(self):
        def write_then_fail(tool, arguments):
            self.target.write_text(arguments["content"], encoding="utf-8")
            raise RuntimeError("PRIVATE_DETAILS")

        dispatch = Mock(side_effect=write_then_fail)
        response = self.build(dispatch)(self.request)
        self.assertEqual(self.target.read_text(encoding="utf-8"), "after")
        self.assertEqual(response["decision"], "ALLOW")
        self.assertEqual(response["reason_code"], "INTERNAL_ERROR")
        self.assertEqual(response["execution"], {"status": "ERROR", "result": None})
        self.assertNotIn("PRIVATE_DETAILS", json.dumps(response))
        dispatch.assert_called_once()

    def test_logging_failure_preserves_result_and_does_not_repeat_execution(self):
        dispatch = Mock(return_value={"path": "notes.txt", "bytes": 5})
        handle = self.build(dispatch)
        with patch("agentfence.gateway._LOG.info", side_effect=OSError("PRIVATE_DETAILS")):
            response = handle(self.request)
        self.assertEqual(response["decision"], "ALLOW")
        self.assertEqual(response["execution"], {"status": "SUCCESS", "result": {"path": "notes.txt", "bytes": 5}})
        dispatch.assert_called_once_with("write_file", self.request["arguments"])

    def test_logging_failure_preserves_block_and_never_executes(self):
        dispatch = Mock()
        handle = self.build(dispatch)
        with patch("agentfence.gateway._LOG.info", side_effect=RuntimeError("PRIVATE_DETAILS")):
            response = handle({**self.request, "arguments": {"path": "unlisted.txt", "content": "after"}})
        self.assert_blocked_without_execution(response, dispatch, "NOT_ALLOWED")


if __name__ == "__main__":
    unittest.main()
