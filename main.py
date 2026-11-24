"""Local JSON CLI and opt-in Gemini mode. Run from the repository root."""
import argparse
import json
import logging
import os
import sys
from pathlib import Path

from agentfence.contracts import unique_object
from agentfence.gateway import build_gateway, handle_tool_call

ROOT = Path(__file__).resolve().parent


def run_prompt(prompt, gateway):
    from agent.agent import run_agent
    from agent.models.gemini import Gemini

    with Gemini(api_key=os.environ.get("GEMINI_API_KEY"),
                model=os.environ.get("GEMINI_MODEL")) as model:
        print(run_agent(prompt, model=model, gateway=gateway))


def main():
    parser = argparse.ArgumentParser(description="AgentFence file-tool gateway")
    parser.add_argument("--workspace", type=Path, default=ROOT / "workspace")
    parser.add_argument("--policy", type=Path, default=ROOT / "config" / "policy.json")
    parser.add_argument("--audit", action="store_true", help="Write metadata-only audit records to stderr")
    parser.add_argument("--json", action="store_true", help="Read JSON requests from stdin")
    parser.add_argument("--gemini", metavar="PROMPT", help="Send prompt and approved tool results to Gemini")
    args = parser.parse_args()
    if args.audit:
        logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stderr)
    if args.workspace == ROOT / "workspace" and args.policy == ROOT / "config" / "policy.json":
        gateway = handle_tool_call
    else:
        gateway = build_gateway(workspace=args.workspace, policy_path=args.policy)

    if args.json and args.gemini is not None:
        parser.error("--json and --gemini cannot be used together")
    try:
        if args.gemini is not None:
            run_prompt(args.gemini, gateway)
            return 0
        if not args.json and sys.stdin.isatty():
            while True:
                prompt = input("AgentFence> ").strip()
                if prompt.casefold() in {"exit", "quit"}:
                    return 0
                if prompt:
                    try:
                        run_prompt(prompt, gateway)
                    except (ValueError, RuntimeError) as exc:
                        print(str(exc), file=sys.stderr)
    except (EOFError, KeyboardInterrupt):
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
