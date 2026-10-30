"""B-2 unit tests; compatible with unittest and pytest."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from agentfence.contracts import FenceError
from agentfence.gateway import build_gateway
from agentfence.policy import Policy


class PolicyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "policy.json"
        self.data = {
            "default": "BLOCK",
            "files": {"public.txt": ["read"], "notes.txt": ["read", "write"],
                      "drafts/old.txt": ["read", "write", "delete"]},
            "directories": {".": ["list"], "drafts": ["list", "create"]},
        }

    def load(self, data=None):
        self.path.write_text(json.dumps(self.data if data is None else data), encoding="utf-8")
        return Policy.load(self.path)

    def test_five_permissions_and_reasons(self):
        policy = self.load()
        for tool, path in [("read_file", "public.txt"), ("write_file", "notes.txt"),
                           ("delete_file", "drafts/old.txt"), ("list_files", "."),
                           ("list_files", "drafts"), ("create_file", "drafts/new.txt")]:
            with self.subTest(tool=tool, path=path):
                result = policy.check_permission(tool, path)
                self.assertEqual((result.decision, result.reason_code), ("ALLOW", "POLICY_ALLOWED"))
                self.assertTrue(result.reason)

    def test_no_implicit_permissions_or_recursive_create(self):
        policy = self.load()
        for tool, path in [("write_file", "public.txt"), ("delete_file", "notes.txt"),
                           ("read_file", "drafts/new.txt"), ("write_file", "drafts/new.txt"),
                           ("delete_file", "drafts/new.txt"), ("create_file", "new.txt"),
                           ("create_file", "drafts/sub/new.txt"), ("list_files", "drafts/sub"),
                           ("read_file", "PUBLIC.TXT"), ("read_file", "public.txt.bak")]:
            with self.subTest(tool=tool, path=path):
                result = policy.check_permission(tool, path)
                self.assertEqual((result.decision, result.reason_code), ("BLOCK", "POLICY_DENIED"))

    def test_root_create_and_empty_policy(self):
        self.data["directories"]["."] = ["create"]
        policy = self.load()
        self.assertTrue(policy.allows("create_file", "new.txt"))
        self.assertFalse(policy.allows("create_file", "other/new.txt"))
        empty = self.load({"default": "BLOCK", "files": {}, "directories": {}})
        self.assertFalse(empty.allows("read_file", "public.txt"))

    def test_invalid_inputs_block_without_exception(self):
        policy = self.load()
        for tool in [None, [], "execute"]:
            self.assertEqual(policy.check_permission(tool, "public.txt").reason_code, "INVALID_TOOL")
        for path in [None, [], "", "../public.txt", "/public.txt", "C:/public.txt",
                     "drafts/../public.txt", "drafts\\new.txt", ".env", "policy.json", "."]:
            with self.subTest(path=path):
                self.assertEqual(policy.check_permission("read_file", path).reason_code, "INVALID_PATH")

    def test_invalid_policy_rejected_as_a_whole(self):
        invalid = [
            {**self.data, "default": "ALLOW"}, {**self.data, "extra": True},
            {"default": "BLOCK", "files": {}},
            {**self.data, "files": []}, {**self.data, "directories": []},
        ]
        for section, path, permissions in [
            ("files", "x.txt", ["create"]), ("directories", "drafts", ["read"]),
            ("files", "x.txt", ["read", "read"]), ("files", "x.txt", "read"),
            ("files", "x.txt", [None]), ("files", "x.txt", [[]]),
            ("files", "../outside.txt", ["read"]), ("files", ".env", ["read"]),
            ("files", ".", ["read"]), ("directories", "policy.json", ["create"]),
        ]:
            invalid.append({**self.data, section: {path: permissions}})
        for data in invalid:
            with self.subTest(data=data), self.assertRaises(FenceError) as caught:
                self.load(data)
            self.assertEqual(caught.exception.code, "POLICY_ERROR")

    def test_missing_malformed_duplicate_and_oversized_policy(self):
        with self.assertRaises(FenceError):
            Policy.load(self.path)
        for raw in ["{", '{"default":"BLOCK","default":"BLOCK","files":{},"directories":{}}',
                    '{"default":"BLOCK","files":{"x.txt":["read"],"x.txt":[]},"directories":{}}',
                    " " * 262145]:
            self.path.write_text(raw, encoding="utf-8")
            with self.assertRaises(FenceError) as caught:
                Policy.load(self.path)
            self.assertEqual(caught.exception.code, "POLICY_ERROR")
        self.path.write_bytes(b"\xff")
        with self.assertRaises(FenceError):
            Policy.load(self.path)

    def test_entry_limit(self):
        self.data["files"] = {f"file{i}.txt": ["read"] for i in range(1001)}
        with self.assertRaises(FenceError):
            self.load()

    def test_listing_and_snapshot_do_not_expand_grants(self):
        policy = self.load()
        self.assertEqual(policy.visible_children("."), ["drafts", "notes.txt", "public.txt"])
        self.assertEqual(policy.visible_children("drafts"), ["old.txt"])
        self.load({"default": "BLOCK", "files": {}, "directories": {}})
        self.assertTrue(policy.allows("read_file", "public.txt"))

    def test_gateway_uses_new_policy_and_does_not_dispatch_blocked_calls(self):
        self.load()
        root = Path(self.temp.name) / "workspace"
        root.mkdir()
        spy = Mock(return_value={"bytes": 1})
        with patch("agentfence.gateway._make_dispatch", return_value=spy):
            handle = build_gateway(workspace=root, policy_path=self.path)
        def request(tool, path):
            args = {"path": path}
            if tool == "create_file":
                args["content"] = "x"
            return {"version": "1.0", "request_id": "policy-test", "tool": tool, "arguments": args}
        self.assertEqual(handle(request("create_file", "drafts/any.txt"))["decision"], "ALLOW")
        spy.reset_mock()
        response = handle(request("read_file", "drafts/any.txt"))
        self.assertEqual(response["decision"], "BLOCK")
        self.assertIsNone(response["execution"])
        spy.assert_not_called()
        self.path.write_text("{", encoding="utf-8")
        with patch("agentfence.gateway._make_dispatch", return_value=spy):
            broken = build_gateway(workspace=root, policy_path=self.path)
        self.assertEqual(broken(request("read_file", "public.txt"))["reason_code"], "POLICY_ERROR")
        spy.assert_not_called()
