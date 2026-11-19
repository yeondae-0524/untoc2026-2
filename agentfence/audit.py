"""Metadata-only audit events for gateway requests."""

import logging
import sys
from datetime import datetime, timezone

from agentfence.contracts import TOOLS


_LOG = logging.getLogger("agentfence.audit")
_SYSTEM_ERRORS = frozenset({"POLICY_ERROR", "CONFIG_ERROR", "UNSUPPORTED_PLATFORM", "INTERNAL_ERROR"})


def configure_stderr():
    """Enable audit output for the CLI without changing other loggers."""
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(message)s"))
    _LOG.addHandler(handler)
    _LOG.setLevel(logging.INFO)
    _LOG.propagate = False


def _result(response):
    execution = response["execution"]
    if response["decision"] == "ALLOW" and execution and execution["status"] == "SUCCESS":
        return "ACCEPTED"
    if response["decision"] == "BLOCK" and execution is None:
        return "ERROR" if response["reason_code"] in _SYSTEM_ERRORS else "REJECTED"
    return "ERROR"


def format_event(request, response, *, when=None):
    """Format one bounded event; never include raw tool names or arguments."""
    when = when or datetime.now(timezone.utc)
    timestamp = when.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    tool = request.get("tool") if type(request) is dict else None
    if type(tool) is not str or tool not in TOOLS:
        tool = "<unknown>"
    request_id = response["request_id"] or "<unknown>"
    result = _result(response)
    event = f"{timestamp} [{result}] {tool} request_id={request_id}"
    if result != "ACCEPTED":
        event += f" reason_code={response['reason_code']}"
    return event


def record(request, response):
    _LOG.info("%s", format_event(request, response))
