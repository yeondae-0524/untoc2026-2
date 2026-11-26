"""Metadata-only audit events for gateway requests."""

import logging
import sys
from contextlib import contextmanager
from datetime import datetime, timezone
from uuid import uuid4

from agentfence.contracts import TOOLS
from agentfence.patches import FileChange


_LOG = logging.getLogger("agentfence.audit")
_SYSTEM_ERRORS = frozenset({"POLICY_ERROR", "CONFIG_ERROR", "UNSUPPORTED_PLATFORM", "INTERNAL_ERROR"})
_FILE_CHANGES = frozenset({"write_file", "create_file", "delete_file"})


@contextmanager
def configure_stderr():
    """Enable audit output for the CLI without changing other loggers."""
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(message)s"))
    previous_level, previous_propagation = _LOG.level, _LOG.propagate
    _LOG.addHandler(handler)
    _LOG.setLevel(logging.INFO)
    _LOG.propagate = False
    try:
        yield
    finally:
        _LOG.removeHandler(handler)
        _LOG.setLevel(previous_level)
        _LOG.propagate = previous_propagation
        handler.close()


def _result(response):
    execution = response["execution"]
    if response["decision"] == "ALLOW" and execution and execution["status"] == "SUCCESS":
        return "ALLOW"
    if response["decision"] == "BLOCK" and execution is None:
        return "ERROR" if response["reason_code"] in _SYSTEM_ERRORS else "BLOCK"
    return "ERROR"


def _timestamp(when=None):
    when = when or datetime.now(timezone.utc)
    return when.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def format_event(request, response, *, when=None, run_id=None, details=()):
    """Format one bounded event; details come from the trusted audit recorder."""
    tool = request.get("tool") if type(request) is dict else None
    if type(tool) is not str or tool not in TOOLS:
        tool = "<unknown>"
    request_id = response["request_id"] or "<unknown>"
    result = _result(response)
    fields = [_timestamp(when), f"[{result}]", tool]
    if run_id is not None:
        fields.append(f"run_id={run_id}")
    fields.append(f"request_id={request_id}")
    if result != "ALLOW":
        fields.append(f"reason_code={response['reason_code']}")
    fields.extend(details)
    return " ".join(fields)


def record(request, response):
    if _LOG.isEnabledFor(logging.INFO):
        _LOG.info("%s", format_event(request, response))


class AuditRun:
    def __init__(self, *, store=None):
        self.store = store
        self.run_id = uuid4().hex
        self.requests = 0

    def start(self, mode):
        _LOG.info("%s [START] run_id=%s mode=%s", _timestamp(), self.run_id, mode)

    def finish(self, exit_code):
        _LOG.info("%s [END] run_id=%s exit_code=%s requests=%s",
                  _timestamp(), self.run_id, exit_code, self.requests)

    def capture_change(self, tool, path):
        if self.store is not None and tool in _FILE_CHANGES:
            return FileChange(path)
        return None

    def record(self, request, response, *, tool, path, change):
        self.requests += 1
        details = []
        if self.store is not None and tool in _FILE_CHANGES and path is not None:
            details.append(f"path={path}")
            if change is not None and (summary := change.summarize(self.store)):
                details.append(summary)
        _LOG.info("%s", format_event(request, response, run_id=self.run_id, details=details))
