"""A2: file Tool return-format tests based on the final spec (section 5)."""
import json
import tempfile
import os
import unittest
from pathlib import Path

from agentfence.gateway import build_gateway


class BytesWrittenTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="agentfence-a2-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name) / "workspace"
        self.root.mkdir()
        self.policy = Path(temp.name) / "policy.json"
        self.policy.write_text(json.dumps({"version": "1.0", "allow": [
            {"tool": "write_file", "path": "notes.txt"},
            {"tool": "create_file", "path": "new.txt"},
        ]}), encoding="utf-8")

    def call(self, tool, path, content):
        (self.root / "new.txt").unlink(missing_ok=True)
        (self.root / "notes.txt").write_text("before", encoding="utf-8")
        gateway = build_gateway(workspace=self.root, policy_path=self.policy)
        return gateway({"version": "1.0", "request_id": "a2-test", "tool": tool,
                        "arguments": {"path": path, "content": content}})

    def test_list_labels_an_allowed_symlink_without_following_it(self):
        (self.root / "notes.txt").write_text("before", encoding="utf-8")
        try:
            os.symlink("notes.txt", self.root / "alias.txt")
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"Symlink creation unavailable: {exc}")
        self.policy.write_text(json.dumps({"version": "1.0", "allow": [
            {"tool": "list_files", "path": "."},
            {"tool": "read_file", "path": "alias.txt"},
        ]}), encoding="utf-8")
        gateway = build_gateway(workspace=self.root, policy_path=self.policy)
        response = gateway({"version": "1.0", "request_id": "a2-list", "tool": "list_files",
                            "arguments": {"path": "."}})
        self.assertEqual(response["decision"], "ALLOW", response)
        self.assertEqual(response["execution"]["result"]["entries"],
                         [{"path": "alias.txt", "type": "symlink"}])

    def test_write_and_create_return_bytes_written(self):
        for tool, path in [("write_file", "notes.txt"), ("create_file", "new.txt")]:
            for content, expected in [("hello", 5), ("새 메모", 10), ("", 0)]:
                with self.subTest(tool=tool, content=content):
                    response = self.call(tool, path, content)
                    self.assertEqual(response["decision"], "ALLOW", response)
                    self.assertEqual(response["execution"]["status"], "SUCCESS", response)
                    self.assertEqual(response["execution"]["result"],
                                     {"path": path, "bytes_written": expected})
                    # Must match what is actually on disk (UTF-8, no newline translation).
                    self.assertEqual(len((self.root / path).read_bytes()), expected)


if __name__ == "__main__":
    unittest.main()