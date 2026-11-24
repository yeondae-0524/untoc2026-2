"""Offline demonstration of the gateway's final decisions and filesystem effects."""
import json
import tempfile
import unittest
from pathlib import Path

from agentfence.gateway import build_gateway


class DemoTests(unittest.TestCase):
    def test_demo_matrix(self):
        with tempfile.TemporaryDirectory(prefix="agentfence-demo-") as directory:
            root = Path(directory)
            workspace = root / "workspace"
            workspace.mkdir()
            (workspace / "public.txt").write_text("public", encoding="utf-8")
            restricted = workspace / "restricted.txt"
            restricted.write_text("fixture", encoding="utf-8")
            policy = root / "policy.json"
            policy.write_text(json.dumps({"version": "1.0", "allow": [
                {"tool": "read_file", "path": "public.txt"},
                {"tool": "list_files", "path": "."},
                *({"tool": tool, "path": "demo.txt"} for tool in
                  ("create_file", "write_file", "delete_file")),
            ]}), encoding="utf-8")
            gateway = build_gateway(workspace=workspace, policy_path=policy)
            cases = [
                ("read_file", "public.txt", {}, "ALLOW"),
                ("list_files", ".", {}, "ALLOW"),
                ("create_file", "demo.txt", {"content": "start"}, "ALLOW"),
                ("write_file", "demo.txt", {"content": "수정"}, "ALLOW"),
                ("delete_file", "demo.txt", {}, "ALLOW"),
                ("read_file", "restricted.txt", {}, "BLOCK"),
                ("write_file", "restricted.txt", {"content": "changed"}, "BLOCK"),
                ("delete_file", "restricted.txt", {}, "BLOCK"),
                ("create_file", "other.txt", {"content": "new"}, "BLOCK"),
            ]
            totals = {"ALLOW": 0, "BLOCK": 0}
            for index, (tool, path, args, expected) in enumerate(cases):
                result = gateway({"version": "1.0", "request_id": f"demo-{index}",
                                  "tool": tool, "arguments": {"path": path, **args}})
                self.assertEqual(result["decision"], expected, result)
                if expected == "ALLOW":
                    self.assertEqual(result["execution"]["status"], "SUCCESS", result)
                else:
                    self.assertIsNone(result["execution"])
                totals[expected] += 1
            self.assertFalse((workspace / "demo.txt").exists())
            self.assertFalse((workspace / "other.txt").exists())
            self.assertEqual(restricted.read_text(encoding="utf-8"), "fixture")
            print(f"Allowed: {totals['ALLOW']}/5; denied: {totals['BLOCK']}/4")
