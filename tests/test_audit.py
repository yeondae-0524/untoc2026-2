import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agentfence.audit import AuditRun
from agentfence.gateway import build_gateway
from agentfence.patches import PatchStore


ROOT = Path(__file__).resolve().parents[1]


class AuditDiffTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.workspace = self.base / "workspace"
        self.workspace.mkdir()
        self.audit_dir = self.base / "audit"
        self.audit_dir.mkdir(mode=0o700)
        self.policy = ROOT / "config" / "policy.json"
        self.target = self.workspace / "notes.txt"
        self.target.write_bytes(b"old private text\n")

    def request(self, content: str | None = None, *, path: str = "notes.txt", tool: str = "write_file"):
        arguments: dict[str, str | None] = {"path": path}
        if tool in {"write_file", "create_file"}:
            arguments["content"] = content
        return {"version": "1.0", "request_id": "case-1", "tool": tool, "arguments": arguments}

    def run_cli(self, requests, *options):
        return subprocess.run(
            [sys.executable, str(ROOT / "main.py"), "--workspace", str(self.workspace), *options],
            input="".join(json.dumps(request) + "\n" for request in requests),
            text=True, capture_output=True, timeout=15,
        )

    def store(self):
        return PatchStore(self.audit_dir, workspace=self.workspace, repository=ROOT)

    def test_cli_captures_approved_file_changes_across_workspace(self):
        plain = self.run_cli([self.request("old private text\n")])
        self.assertEqual(plain.stderr, "")
        self.assertFalse((self.audit_dir / "patches").exists())
        basic_audit = self.run_cli([self.request("old private text\n")], "--audit")
        self.assertIn("[ALLOW] write_file", basic_audit.stderr)
        self.assertNotIn("notes.txt", basic_audit.stderr)
        self.assertFalse((self.audit_dir / "patches").exists())

        (self.workspace / "drafts").mkdir()
        deleted = self.workspace / "drafts" / "old.txt"
        deleted.write_bytes(b"deleted private text\n")
        requests = [
            self.request("new private text"), self.request("new private text"),
            self.request("blocked", path="public.txt"),
            self.request("created private text\n", tool="create_file", path="drafts/new.txt"),
            self.request(tool="delete_file", path="drafts/old.txt"),
            self.request(tool="read_file"), self.request(tool="list_files", path="."),
        ]
        result = self.run_cli(requests, "--audit", "--audit-diff-dir", str(self.audit_dir))
        self.assertEqual(result.returncode, 0, result.stderr)
        replies = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual([reply["decision"] for reply in replies],
                         ["ALLOW", "ALLOW", "BLOCK", "ALLOW", "ALLOW", "ALLOW", "ALLOW"])
        audit = result.stderr
        self.assertNotIn("private text", audit)
        self.assertNotIn("patch_id", result.stdout)
        self.assertIn("change=unchanged", audit)
        self.assertIn("path=drafts/new.txt change=created", audit)
        self.assertIn("path=drafts/old.txt change=deleted", audit)
        self.assertFalse(deleted.exists())
        self.assertIn("[BLOCK] write_file", audit)
        self.assertIn("exit_code=0 requests=7", audit)
        run_ids = re.findall(r"run_id=([0-9a-f]{32})", audit)
        self.assertEqual(len(run_ids), 9)
        self.assertEqual(len(set(run_ids)), 1)
        ids = dict(re.findall(r"change=(\w+) patch_id=([0-9a-f]{32})", audit))
        saved = {path.stem: path.read_bytes() for path in (self.audit_dir / "patches").glob("*.diff")}
        self.assertEqual(set(saved), set(ids.values()))
        self.assertEqual(len(saved), 3)
        self.assertIn(b"-old private text\n+new private text\n\\ No newline at end of file\n", saved[ids["modified"]])
        self.assertIn(b"--- /dev/null\n+++ b/drafts/new.txt\n", saved[ids["created"]])
        self.assertIn(b"+created private text\n", saved[ids["created"]])
        self.assertIn(b"--- a/drafts/old.txt\n+++ /dev/null\n", saved[ids["deleted"]])
        self.assertIn(b"-deleted private text\n", saved[ids["deleted"]])

    def test_diff_directory_alone_records_empty_creation_without_stderr(self):
        (self.workspace / "drafts").mkdir()
        result = self.run_cli([self.request("", tool="create_file", path="drafts/empty.txt")],
                              "--audit-diff-dir", str(self.audit_dir))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        saved, = self.store().directory.glob("*.diff")
        self.assertEqual(saved.read_bytes(),
                         b"diff --git a/drafts/empty.txt b/drafts/empty.txt\nnew file mode 100644\n")

    def test_cli_rejects_patch_directory_inside_workspace(self):
        result = self.run_cli([self.request("unapplied")], "--audit-diff-dir", str(self.workspace))
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertEqual(self.target.read_bytes(), b"old private text\n")

    def test_partial_write_error_records_the_bytes_actually_written(self):
        store = self.store()
        gateway = build_gateway(workspace=self.workspace, policy_path=self.policy,
                                audit=AuditRun(store=store))
        real_write = os.write

        def partial(fd, data):
            if data == b"partially written":
                return real_write(fd, data[:4])
            raise OSError("disk error")

        with (self.assertLogs("agentfence.audit", level="INFO") as logs,
              patch("tools.file_tools.os.write", side_effect=partial)):
            response = gateway(self.request("partially written"))
        self.assertEqual(response["reason_code"], "EXECUTION_ERROR")
        self.assertEqual(self.target.read_bytes(), b"part")
        self.assertIn("[ERROR] write_file", logs.output[0])
        saved, = store.directory.glob("*.diff")
        self.assertIn(b"+part\n", saved.read_bytes())
        self.assertNotIn(b"partially written", saved.read_bytes())

    def test_save_failure_does_not_change_tool_result(self):
        store = self.store()
        gateway = build_gateway(workspace=self.workspace, policy_path=self.policy,
                                audit=AuditRun(store=store))
        with (self.assertLogs("agentfence.audit", level="INFO") as logs,
              patch.object(store, "save", side_effect=OSError("full"))):
            response = gateway(self.request("new"))
        self.assertEqual(response["decision"], "ALLOW")
        self.assertEqual(self.target.read_bytes(), b"new")
        self.assertIn("change=modified diff=save_failed", logs.output[0])
        self.assertNotIn("patch_id=", logs.output[0])

    def test_oversized_and_binary_patches_are_omitted(self):
        store = self.store()
        gateway = build_gateway(workspace=self.workspace, policy_path=self.policy,
                                audit=AuditRun(store=store))
        for before, reason in ((b"a" * 33000, "omitted_size"), (b"\xff", "omitted_format")):
            with self.subTest(reason=reason):
                self.target.write_bytes(before)
                with self.assertLogs("agentfence.audit", level="INFO") as logs:
                    response = gateway(self.request("new"))
                self.assertEqual(response["decision"], "ALLOW")
                self.assertEqual(self.target.read_bytes(), b"new")
                self.assertIn(f"change=modified diff={reason}", logs.output[0])
                self.assertFalse(store.directory.exists())

    def test_store_prunes_expired_and_oldest_diff_files(self):
        store = self.store()
        expired, older, newer = [store.save(b"patch") for _ in range(3)]
        os.utime(store.directory / f"{expired}.diff", (1, 1))
        now = (store.directory / f"{newer}.diff").stat().st_mtime
        os.utime(store.directory / f"{older}.diff", (now - 10, now - 10))
        with patch("agentfence.patches._STORE_BYTES", 10):
            latest = store.save(b"patch")
        self.assertEqual({path.stem for path in store.directory.glob("*.diff")}, {newer, latest})
