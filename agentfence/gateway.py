"""The only callable injected into the Agent. Approval never leaves this module."""
import logging
from pathlib import Path
from threading import Lock

from agentfence.contracts import FenceError, VERSION, _ID, validate_request
from agentfence.policy import Policy
from tools.file_tools import _make_dispatch

_LOG = logging.getLogger("agentfence.audit")


def build_gateway(*, workspace, policy_path):
    """Return handle(request: dict) -> response: dict. Configuration is trusted."""
    startup_error = None
    try:
        workspace = Path(workspace).absolute()
        policy_path = Path(policy_path).absolute()
        # resolve is a configuration-placement check, never the execution defense.
        if policy_path.resolve().is_relative_to(workspace.resolve()):
            raise FenceError("CONFIG_ERROR", "Policy must be outside the workspace.")
        policy = Policy.load(policy_path)
        dispatch = _make_dispatch(workspace, policy)
    except FenceError as exc:
        startup_error = exc
    except OSError:
        startup_error = FenceError("CONFIG_ERROR", "Workspace configuration is unavailable.")
    lock = Lock()

    def handle(request):
        request_id = None
        if type(request) is dict:
            candidate = request.get("request_id")
            if isinstance(candidate, str) and _ID.fullmatch(candidate):
                request_id = candidate
        decision, code, reason, execution = "BLOCK", "INTERNAL_ERROR", "Request could not be processed.", None
        with lock:
            try:
                tool, arguments = validate_request(request)
                if startup_error:
                    raise startup_error
                if not policy.allows(tool, arguments["path"]):
                    raise FenceError("NOT_ALLOWED", "No matching allowlist grant.")
                # The closure owns the executor. Agent never receives it or an ALLOW token.
                result = dispatch(tool, arguments)
                decision, code, reason = "ALLOW", "OK", "Approved and executed."
                execution = {"status": "SUCCESS", "result": result}
            except FenceError as exc:
                code, reason = exc.code, exc.message
            except (OSError, UnicodeError) as exc:
                # Policy allowed it, but execution failed. Never expose raw OS errors/paths.
                decision, code, reason = "ALLOW", "EXECUTION_ERROR", "Approved operation failed."
                execution = {"status": "ERROR", "result": None}
                if isinstance(exc, FileNotFoundError):
                    code, reason = "NOT_FOUND", "Approved target does not exist."
                elif isinstance(exc, FileExistsError):
                    code, reason = "ALREADY_EXISTS", "create_file cannot overwrite a target."
            except Exception:
                # Fail closed on implementation defects without exposing file data.
                decision, code, reason, execution = "BLOCK", "INTERNAL_ERROR", "Request could not be processed.", None
            response = {"version": VERSION, "request_id": request_id, "decision": decision,
                        "reason_code": code, "reason": reason, "execution": execution}
            # No paths, content, prompts, keys, or raw exception strings in audit logs.
            _LOG.info("request_id=%s decision=%s reason_code=%s", request_id, decision, code)
            return response

    return handle
