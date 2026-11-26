"""Compare six fixed requests with pathlib and AgentFence in disposable fixtures.

Run from the repository root: python -m experiments.compare_gateway
No user paths, API keys, network calls, or repository workspace files are used.
The deliberately unprotected pathlib executor is only a control for this demo.
"""
import json
import tempfile
from dataclasses import dataclass
from pathlib import Path

from agentfence.gateway import build_gateway


@dataclass(frozen=True)
class Attack:
    name: str
    tool: str
    path: str
    expected_code: str


ATTACKS = (
    Attack("Unauthorized read", "read_file", "secret.txt", "NOT_ALLOWED"),
    Attack("Unauthorized write", "write_file", "public.txt", "NOT_ALLOWED"),
    Attack("Unauthorized delete", "delete_file", "public.txt", "NOT_ALLOWED"),
    Attack("Unauthorized create", "create_file", "unauthorized.txt", "NOT_ALLOWED"),
    Attack("Path Traversal", "read_file", "../outside.txt", "INVALID_PATH"),
    Attack("Absolute path", "read_file", "<absolute-outside>", "INVALID_PATH"),
)


@dataclass(frozen=True)
class Outcome:
    attack_success: bool
    reason_code: str | None


def _snapshot(root):
    return {path.relative_to(root).as_posix(): path.read_bytes()
            for path in root.rglob("*") if path.is_file()}


def run_case(protected: bool, attack: Attack) -> Outcome:
    """Run one fixed case, validating observed effects before reporting a result."""
    if type(protected) is not bool or attack not in ATTACKS:
        raise ValueError("Only the six built-in cases and boolean modes are supported.")
    with tempfile.TemporaryDirectory(prefix="agentfence-comparison-") as directory:
        root = Path(directory)
        workspace = root / "workspace"
        workspace.mkdir()
        public = workspace / "public.txt"
        secret = workspace / "secret.txt"
        outside = root / "outside.txt"
        public.write_text("ORIGINAL", encoding="utf-8")
        secret.write_text("SECRET_TEST", encoding="utf-8")
        outside.write_text("OUTSIDE_TEST", encoding="utf-8")
        policy = root / "policy.json"
        policy.write_text(json.dumps({
            "default": "BLOCK", "files": {"public.txt": ["read"]},
            "directories": {},
        }), encoding="utf-8")
        before = _snapshot(root)
        path = outside.as_posix() if attack.path == "<absolute-outside>" else attack.path
        arguments = {"path": path}
        if attack.tool in {"write_file", "create_file"}:
            arguments["content"] = "CHANGED"
        request = {"version": "1.0", "request_id": "comparison-test",
                   "tool": attack.tool, "arguments": arguments}

        if protected:
            gateway = build_gateway(workspace=workspace, policy_path=policy)
            # An all-blocking or misconfigured gateway must not count as protection.
            probe = gateway({"version": "1.0", "request_id": "positive-control",
                             "tool": "read_file", "arguments": {"path": "public.txt"}})
            if (probe["decision"] != "ALLOW" or probe["reason_code"] != "OK"
                    or probe["execution"] is None
                    or probe["execution"]["status"] != "SUCCESS"
                    or probe["execution"]["result"]["content"] != "ORIGINAL"):
                raise AssertionError(f"Allowed positive control failed: {probe}")
            response = gateway(request)
            if (response["decision"] != "BLOCK"
                    or response["reason_code"] != attack.expected_code
                    or response["execution"] is not None):
                raise AssertionError(f"Unexpected protected response: {response}")
            if _snapshot(root) != before:
                raise AssertionError("Protected request changed the fixture files.")
            if any(marker in json.dumps(response) for marker in ("SECRET_TEST", "OUTSIDE_TEST")):
                raise AssertionError("Protected response disclosed fixture content.")
            return Outcome(False, response["reason_code"])

        # Deliberately no path validation or allowlist: isolated control only.
        # This is not the production file Tool with a security switch turned off.
        target = workspace / request["arguments"]["path"]
        if request["tool"] == "read_file":
            expected = "SECRET_TEST" if attack.path == "secret.txt" else "OUTSIDE_TEST"
            success = target.read_text(encoding="utf-8") == expected
        elif request["tool"] in {"write_file", "create_file"}:
            target.write_text(request["arguments"]["content"], encoding="utf-8")
            success = target.read_text(encoding="utf-8") == "CHANGED"
        elif request["tool"] == "delete_file":
            target.unlink()
            success = not target.exists()
        else:
            raise AssertionError("Unsupported fixed tool.")
        if not success:
            raise AssertionError(f"Control attack did not succeed: {attack.name}")
        return Outcome(True, None)


def main():
    for protected in (False, True):
        print("\nAgentFence:", "ON" if protected else "OFF")
        successes = 0
        for attack in ATTACKS:
            outcome = run_case(protected, attack)
            successes += int(outcome.attack_success)
            print(attack.name, ":", "ATTACK SUCCESS" if outcome.attack_success else "BLOCKED")
        print(f"Attack success rate: {successes}/{len(ATTACKS)} "
              f"({successes / len(ATTACKS) * 100:.0f}%)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
