"""A2: file Tool return-format tests based on the final spec (section 5)."""
import json

import pytest

from agentfence.gateway import build_gateway


@pytest.fixture
def handle(tmp_path):
    root = tmp_path / "workspace"
    root.mkdir()
    (root / "notes.txt").write_text("before", encoding="utf-8")
    policy = tmp_path / "policy.json"
    policy.write_text(json.dumps({"version": "1.0", "allow": [
        {"tool": "write_file", "path": "notes.txt"},
        {"tool": "create_file", "path": "new.txt"},
    ]}), encoding="utf-8")
    gateway = build_gateway(workspace=root, policy_path=policy)

    def call(tool, path, **arguments):
        return gateway({"version": "1.0", "request_id": "a2-test", "tool": tool,
                        "arguments": {"path": path, **arguments}})

    call.root = root
    return call


@pytest.mark.parametrize("tool, path", [("write_file", "notes.txt"), ("create_file", "new.txt")])
@pytest.mark.parametrize("content, expected", [("hello", 5), ("새 메모", 10), ("", 0)])
def test_write_and_create_return_bytes_written(handle, tool, path, content, expected):
    response = handle(tool, path, content=content)

    assert response["decision"] == "ALLOW"
    assert response["execution"]["status"] == "SUCCESS"
    assert response["execution"]["result"] == {"path": path, "bytes_written": expected}
    # bytes_written must match what is actually on disk (UTF-8, no newline translation).
    assert len((handle.root / path).read_bytes()) == expected