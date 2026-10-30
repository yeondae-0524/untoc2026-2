import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

from agentfence.contracts import FenceError, MAX_BYTES, TOOLS
from agentfence.gateway import build_gateway
from tools import _filesystem as fs
from tools.file_tools import read_file, write_file, list_files, create_file, delete_file


class SecurityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="agentfence-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / "workspace"
        self.root.mkdir()
        (self.root / "docs").mkdir()
        (self.root / "public.txt").write_text("public", encoding="utf-8")
        (self.root / "notes.txt").write_text("before", encoding="utf-8")
        (self.root / "docs" / "item.txt").write_text("inside", encoding="utf-8")
        self.secret = self.base / "secret.txt"
        self.secret.write_text("OUTSIDE_SECRET", encoding="utf-8")
        self.policy_path = self.base / "policy.json"
        self.rules = [("read_file", "public.txt"), ("write_file", "notes.txt"),
                      ("read_file", "notes.txt"), ("list_files", "."), ("list_files", "docs"),
                      ("read_file", "docs/item.txt"), ("write_file", "docs/item.txt"),
                      ("create_file", "docs/new.txt"), ("delete_file", "docs/new.txt")]
        self.handle = self.build()

    def build(self, rules=None):
        self.policy_path.write_text(json.dumps({"version": "1.0", "allow": [
            {"tool": tool, "path": path} for tool, path in (self.rules if rules is None else rules)
        ]}), encoding="utf-8")
        return build_gateway(workspace=self.root, policy_path=self.policy_path)

    def request(self, tool="read_file", path="public.txt", **arguments):
        return {"version": "1.0", "request_id": "test-1", "tool": tool,
                "arguments": {"path": path, **arguments}}

    def call(self, tool="read_file", path="public.txt", **arguments):
        return self.handle(self.request(tool, path, **arguments))

    def assert_block(self, response, code=None):
        self.assertEqual(response["decision"], "BLOCK", response)
        self.assertIsNone(response["execution"])
        if code:
            self.assertEqual(response["reason_code"], code)
        self.assertNotIn("OUTSIDE_SECRET", json.dumps(response))

    def assert_success(self, response):
        self.assertEqual(response["decision"], "ALLOW", response)
        self.assertEqual(response["execution"]["status"], "SUCCESS", response)
        self.assertEqual(response["reason_code"], "OK")
        return response["execution"]["result"]

    def test_five_tools_and_response_contract(self):
        response = self.call()
        self.assertEqual(set(response), {"version", "request_id", "decision", "reason_code", "reason", "execution"})
        self.assertEqual(self.assert_success(response)["content"], "public")
        self.assert_success(self.call("write_file", "notes.txt", content="after 한글"))
        self.assertEqual((self.root / "notes.txt").read_text(encoding="utf-8"), "after 한글")
        self.assert_success(self.call("create_file", "docs/new.txt", content="created"))
        self.assert_success(self.call("list_files", "docs"))
        self.assert_success(self.call("delete_file", "docs/new.txt"))
        self.assertFalse((self.root / "docs/new.txt").exists())

    def test_unknown_tool_and_invalid_envelopes(self):
        cases = [None, [], {}, {**self.request(), "version": "2.0"},
                 {**self.request(), "extra": True}, {**self.request(), "request_id": "bad\nlog"},
                 {**self.request(), "tool": []}, {**self.request(), "arguments": []},
                 {**self.request(), "arguments": {"path": "public.txt", "approved": True}},
                 self.request("write_file", "notes.txt"), self.request("read_file", "public.txt", content="x")]
        for request in cases:
            with self.subTest(request=request):
                self.assert_block(self.handle(request))
        self.assert_block(self.call("run_python"), "UNKNOWN_TOOL")

    def test_path_traversal_windows_aliases_and_control_files(self):
        for path in ["../secret.txt", "/etc/passwd", "C:/secret.txt", "C:secret.txt", "\\\\host\\share\\file",
                     "docs\\item.txt", "docs/../public.txt", "./public.txt", "docs//item.txt",
                     "public.txt:stream", "public.txt.", "public.txt ", "PUBLIC~1.TXT", "NUL", "com1.txt",
                     ".env", ".git/config", "config/policy.json", "policy.json", "docs/POLICY.JSON",
                     "api_key", "credentials.json", "private.pem", "docs/secret.key", "x\x00.txt", "."]:
            with self.subTest(path=path):
                self.assert_block(self.call(path=path))

    def test_default_deny_all_operations_have_no_effect(self):
        (self.root / "secret.txt").write_text("local secret", encoding="utf-8")
        requests = [self.request("read_file", "secret.txt"), self.request("write_file", "public.txt", content="bad"),
                    self.request("list_files", "private"), self.request("create_file", "unauthorized.txt", content="bad"),
                    self.request("delete_file", "public.txt")]
        spy = Mock()
        with patch("agentfence.gateway._make_dispatch", return_value=spy):
            gateway = self.build()
        for request in requests:
            self.assert_block(gateway(request), "NOT_ALLOWED")
        spy.assert_not_called()
        self.assertEqual((self.root / "public.txt").read_text(), "public")
        self.assertFalse((self.root / "unauthorized.txt").exists())

    def test_exact_path_case_and_prefix_do_not_expand_grants(self):
        for path in ["PUBLIC.TXT", "public.txt.bak", "docs/item.txt/child", "docs/item2.txt"]:
            self.assert_block(self.call(path=path), "NOT_ALLOWED")

    def test_list_reveals_only_read_or_list_granted_children(self):
        (self.root / "secret.txt").write_text("secret")
        entries = self.assert_success(self.call("list_files", "."))["entries"]
        self.assertEqual(entries, [{"path": "docs", "type": "directory"},
                                   {"path": "notes.txt", "type": "file"},
                                   {"path": "public.txt", "type": "file"}])
        self.assertEqual(self.assert_success(self.call("list_files", "docs"))["entries"],
                         [{"path": "docs/item.txt", "type": "file"}])

    def test_create_exclusive_and_write_existing_only(self):
        self.handle = self.build(self.rules + [("create_file", "public.txt"), ("write_file", "missing.txt")])
        response = self.call("create_file", "public.txt", content="bad")
        self.assertEqual(response["reason_code"], "ALREADY_EXISTS")
        self.assertEqual(response["execution"], {"status": "ERROR", "result": None})
        self.assertEqual((self.root / "public.txt").read_text(), "public")
        self.assertEqual(self.call("write_file", "missing.txt", content="x")["reason_code"], "NOT_FOUND")
        self.assertFalse((self.root / "missing.txt").exists())

    def test_directory_never_read_written_or_deleted_as_file(self):
        self.handle = self.build([(tool, "docs") for tool in TOOLS if tool != "create_file"])
        for tool in ["read_file", "write_file", "delete_file"]:
            self.assert_block(self.call(tool, "docs", **({"content": "x"} if tool == "write_file" else {})), "UNSAFE_TARGET")
        self.assertTrue((self.root / "docs/item.txt").exists())

    def test_missing_target_and_invalid_utf8_are_execution_errors(self):
        (self.root / "public.txt").unlink()
        self.assertEqual(self.call()["reason_code"], "NOT_FOUND")
        (self.root / "public.txt").write_bytes(b"\xff")
        self.assertEqual(self.call()["execution"], {"status": "ERROR", "result": None})

    def test_size_limits_before_write_and_after_bounded_read(self):
        before = (self.root / "notes.txt").read_bytes()
        for content in ["x" * (MAX_BYTES + 1), "가" * 22_000, "\ud800", 123]:
            self.assert_block(self.call("write_file", "notes.txt", content=content))
            self.assertEqual((self.root / "notes.txt").read_bytes(), before)
        (self.root / "public.txt").write_bytes(b"x" * (MAX_BYTES + 1))
        self.assert_block(self.call(), "SIZE_LIMIT")
        (self.root / "public.txt").write_bytes(b"x" * MAX_BYTES)
        self.assertEqual(self.assert_success(self.call())["bytes"], MAX_BYTES)

    def test_invalid_missing_duplicate_and_protected_policy_fail_closed(self):
        invalid = ["{", '{"version":"1.0","version":"1.0","allow":[]}',
                   '{"version":"1.0","allow":[{"tool":"read_file","path":"policy.json"}]}',
                   '{"version":"1.0","allow":[{"tool":"read_file","path":"*.txt"}]}',
                   '{"version":"1.0","allow":[{"tool":"exec","path":"public.txt"}]}',
                   '{"version":"1.0","allow":[],"default":"allow"}',
                   '{"version":"1.0","allow":[{"tool":"read_file","path":"public.txt"},'
                   '{"tool":"read_file","path":"public.txt"}]}']
        for text in invalid:
            self.policy_path.write_text(text)
            gateway = build_gateway(workspace=self.root, policy_path=self.policy_path)
            self.assert_block(gateway(self.request()), "POLICY_ERROR")
        self.policy_path.unlink()
        self.assert_block(build_gateway(workspace=self.root, policy_path=self.policy_path)(self.request()), "POLICY_ERROR")

    def test_policy_inside_workspace_is_configuration_error(self):
        path = self.root / "anything.json"
        path.write_text(self.policy_path.read_text())
        self.assert_block(build_gateway(workspace=self.root, policy_path=path)(self.request()), "CONFIG_ERROR")

    def test_excessive_json_depth_policy_is_blocked(self):
        self.policy_path.write_text("[" * 2000 + "0" + "]" * 2000)
        self.assert_block(build_gateway(workspace=self.root, policy_path=self.policy_path)(self.request()), "POLICY_ERROR")

    def test_hardlinks_block_all_existing_file_operations_and_listing(self):
        (self.root / "public.txt").unlink()
        os.link(self.secret, self.root / "public.txt")
        self.handle = self.build(self.rules + [("write_file", "public.txt"), ("delete_file", "public.txt")])
        for tool in ["read_file", "write_file", "delete_file"]:
            self.assert_block(self.call(tool, "public.txt", **({"content": "BAD"} if tool == "write_file" else {})), "UNSAFE_TARGET")
        self.assertEqual(self.secret.read_text(), "OUTSIDE_SECRET")
        entries = self.assert_success(self.call("list_files", "."))["entries"]
        self.assertNotIn("public.txt", [entry["path"] for entry in entries])

    def _symlink(self, target, link, directory=False):
        try:
            os.symlink(target, link, target_is_directory=directory)
        except OSError as exc:
            if os.name == "nt" and getattr(exc, "winerror", None) == 1314:
                self.skipTest("Windows file symlink creation requires Developer Mode or privilege")
            raise

    def test_leaf_symlink_and_dangling_symlink_are_never_followed(self):
        (self.root / "public.txt").unlink()
        self._symlink(self.secret, self.root / "public.txt")
        self.assert_block(self.call(), "UNSAFE_TARGET")
        (self.root / "public.txt").unlink()
        self._symlink(self.base / "missing.txt", self.root / "public.txt")
        self.assert_block(self.call(), "UNSAFE_TARGET")

    def test_parent_junction_or_symlink_escape(self):
        outside = self.base / "outside"
        outside.mkdir()
        (outside / "item.txt").write_text("OUTSIDE_SECRET")
        link = self.root / "linked"
        if os.name == "nt":
            completed = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(outside)],
                                       capture_output=True, check=False)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.addCleanup(lambda: os.rmdir(link) if link.exists() else None)
        else:
            self._symlink(outside, link, directory=True)
        self.handle = self.build(self.rules + [("read_file", "linked/item.txt")])
        self.assert_block(self.call(path="linked/item.txt"), "UNSAFE_TARGET")

    def test_swap_to_hardlink_between_policy_check_and_file_open(self):
        self.handle = self.build(self.rules + [("write_file", "public.txt")])
        for tool in ["read_file", "write_file"]:
            (self.root / "public.txt").unlink()
            (self.root / "public.txt").write_text("original")
            swapped = False

            def swap():
                nonlocal swapped
                if not swapped:
                    swapped = True
                    (self.root / "public.txt").unlink()
                    os.link(self.secret, self.root / "public.txt")

            if os.name == "nt":
                original = fs._win_open

                def raced(path, **kwargs):
                    if Path(path).name == "public.txt" and not kwargs.get("directory"):
                        swap()
                    return original(path, **kwargs)

                context = patch.object(fs, "_win_open", side_effect=raced)
            else:
                original = os.open

                def raced(path, *args, **kwargs):
                    if path == "public.txt" and "dir_fd" in kwargs:
                        swap()
                    return original(path, *args, **kwargs)

                context = patch.object(os, "open", side_effect=raced)
            with context:
                self.assert_block(self.call(tool, "public.txt", **({"content": "BAD"} if tool == "write_file" else {})), "UNSAFE_TARGET")
            self.assertTrue(swapped)
            self.assertEqual(self.secret.read_text(), "OUTSIDE_SECRET")

    @unittest.skipUnless(os.name == "nt", "Windows sharing semantics")
    def test_windows_pinned_parent_uses_handle_after_rename(self):
        backend = fs.Workspace(self.root)
        outside = self.base / "outside"
        outside.mkdir()
        (outside / "item.txt").write_text("OUTSIDE_SECRET")
        with backend.directory("docs") as directory:
            try:
                (self.root / "docs").rename(self.root / "moved")
            except OSError:
                pass  # Older Windows may also prevent renames using share modes.
            else:
                link = self.root / "docs"
                completed = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(outside)],
                                           capture_output=True, check=False)
                self.assertEqual(completed.returncode, 0, completed.stderr)
                self.addCleanup(lambda: os.rmdir(link) if link.exists() else None)
            with directory.file("item.txt", "r") as descriptor:
                self.assertEqual(os.read(descriptor, 100), b"inside")

    @unittest.skipUnless(os.name == "posix", "POSIX descriptor semantics")
    def test_posix_pinned_parent_does_not_follow_replacement_link(self):
        backend = fs.Workspace(self.root)
        outside = self.base / "outside"
        outside.mkdir()
        (outside / "item.txt").write_text("OUTSIDE_SECRET")
        with backend.directory("docs") as directory:
            (self.root / "docs").rename(self.root / "moved")
            os.symlink(outside, self.root / "docs", target_is_directory=True)
            with directory.file("item.txt", "r") as descriptor:
                self.assertEqual(os.read(descriptor, 100), b"inside")

    def test_replaced_workspace_root_is_blocked(self):
        self.root.rename(self.base / "old")
        self.root.mkdir()
        (self.root / "public.txt").write_text("replacement")
        self.assert_block(self.call(), "UNSAFE_TARGET")

    def test_direct_raw_calls_cannot_execute_files(self):
        for function in [read_file, write_file, list_files, create_file, delete_file]:
            for forged in ["public.txt", {"approved": True, "path": "public.txt"}, None]:
                with self.assertRaises(FenceError) as caught:
                    function(forged)
                self.assertEqual(caught.exception.code, "INVALID_APPROVAL")

    def test_approval_is_bound_to_one_tool_and_consumed_once(self):
        from agentfence.policy import Policy
        from tools.file_tools import _ApprovedCall, _ISSUER
        call = _ApprovedCall(_ISSUER, "read_file", {"path": "public.txt"},
                             fs.Workspace(self.root), Policy.load(self.policy_path))
        with self.assertRaises(FenceError):
            delete_file(call)
        self.assertEqual(read_file(call)["content"], "public")
        with self.assertRaises(FenceError):
            read_file(call)

    @unittest.skipUnless(os.name == "posix", "POSIX FIFO semantics")
    def test_fifo_is_rejected_without_blocking(self):
        (self.root / "public.txt").unlink()
        os.mkfifo(self.root / "public.txt")
        self.assert_block(self.call(), "UNSAFE_TARGET")

    def test_audit_log_omits_paths_content_and_exception_details(self):
        with self.assertLogs("agentfence.audit", level="INFO") as captured:
            self.call("write_file", "notes.txt", content="DO_NOT_LOG_THIS")
        audit = "\n".join(captured.output)
        self.assertIn("request_id=test-1 decision=ALLOW reason_code=OK", audit)
        self.assertNotIn("notes.txt", audit)
        self.assertNotIn("DO_NOT_LOG_THIS", audit)
        self.assertNotIn(str(self.root), audit)


if __name__ == "__main__":
    unittest.main()
