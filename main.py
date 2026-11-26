"""Local JSON CLI and opt-in Gemini mode. Run from the repository root."""
import argparse
import json
import os
import sys
from pathlib import Path

from agentfence.audit import AuditRun, configure_stderr
from agentfence.contracts import unique_object
from agentfence.gateway import build_gateway
from agentfence.patches import PatchStore

ROOT = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser(description="AgentFence file-tool gateway")
    parser.add_argument("--workspace", type=Path, default=ROOT / "workspace")
    parser.add_argument("--policy", type=Path, default=ROOT / "config" / "policy.json")
    parser.add_argument("--audit", action="store_true", help="Write metadata-only audit records to stderr")
    parser.add_argument("--audit-diff-dir", type=Path, metavar="DIR",
                        help="Save diffs for file changes across the workspace to a private directory")
    parser.add_argument("--gemini", metavar="PROMPT", help="Send prompt and approved tool results to Gemini")
    args = parser.parse_args()
    store = None
    if args.audit_diff_dir is not None:
        try:
            store = PatchStore(args.audit_diff_dir, workspace=args.workspace, repository=ROOT)
        except (OSError, ValueError, RuntimeError):
            parser.error("Use an existing private --audit-diff-dir outside the workspace and repository")
    if not args.audit:
        return _run(args, AuditRun(store=store) if store is not None else None)

    audit = AuditRun(store=store)
    with configure_stderr():
        audit.start("gemini" if args.gemini is not None else "stdin")
        exit_code = 1
        try:
            exit_code = _run(args, audit)
        except KeyboardInterrupt:
            exit_code = 130
            raise
        finally:
            audit.finish(exit_code)
    return exit_code


def _run(args, audit=None):
    gateway = build_gateway(workspace=args.workspace, policy_path=args.policy, audit=audit)
    if args.gemini is not None:
        from agent.agent import run_agent
        from agent.models.gemini import Gemini
        try:
            model = Gemini(api_key=os.environ.get("GEMINI_API_KEY"), model=os.environ.get("GEMINI_MODEL"))
            print(run_agent(args.gemini, model=model, gateway=gateway))
            return 0
        except (ValueError, RuntimeError) as exc:
            print(str(exc), file=sys.stderr)
            return 1
    # JSON lines, bounded input. Invalid JSON follows the same BLOCK response shape.
    while True:
        line = sys.stdin.buffer.readline(524_289)
        if not line:
            break
        if len(line) > 524_288:
            print(json.dumps(gateway(None)))
            return 1
        try:
            request = json.loads(line, object_pairs_hook=unique_object)
        except (ValueError, UnicodeError, RecursionError):
            request = None
        print(json.dumps(gateway(request), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
