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
        if "\x00" in str(workspace) or "\x00" in str(policy_path):
            raise FenceError("CONFIG_ERROR", "Configuration paths cannot contain NUL.")
        # resolve is a configuration-placement check, never the execution defense.
        if policy_path.resolve().is_relative_to(workspace.resolve()):
            raise FenceError("CONFIG_ERROR", "Policy must be outside the workspace.")
        policy = Policy.load(policy_path)
        dispatch = _make_dispatch(workspace, policy)
    except FenceError as exc:
        startup_error = exc
    except (OSError, TypeError, ValueError):
        startup_error = FenceError("CONFIG_ERROR", "Workspace configuration is unavailable.")
    except Exception:
        startup_error = FenceError("INTERNAL_ERROR", "Gateway could not be initialized.")
    lock = Lock()

    def process(request, request_id):
        try:
            tool, arguments = validate_request(request)
        except FenceError as exc:
            return _response(request_id, "BLOCK", exc.code, exc.message)
        except Exception:
            return _response(request_id, "BLOCK", "INTERNAL_ERROR", "Request could not be processed.")

        if startup_error:
            return _response(request_id, "BLOCK", startup_error.code, startup_error.message)
        try:
            allowed = policy.allows(tool, arguments["path"])
        except Exception:
            # No executor has been called. A policy failure must remain a BLOCK.
            return _response(request_id, "BLOCK", "POLICY_ERROR", "Policy check failed; request blocked.")
        if not allowed:
            return _response(request_id, "BLOCK", "NOT_ALLOWED", "No matching allowlist grant.")

        try:
            # The closure owns the executor. Agent never receives it or an ALLOW token.
            result = dispatch(tool, arguments)
        except FenceError as exc:
            # Expected safety rejections from the trusted file Tool boundary.
            return _response(request_id, "BLOCK", exc.code, exc.message)
        except (OSError, UnicodeError) as exc:
            code, reason = "EXECUTION_ERROR", "Approved operation failed."
            if isinstance(exc, FileNotFoundError):
                code, reason = "NOT_FOUND", "Approved target does not exist."
            elif isinstance(exc, FileExistsError):
                code, reason = "ALREADY_EXISTS", "create_file cannot overwrite a target."
            return _response(request_id, "ALLOW", code, reason, {"status": "ERROR", "result": None})
        except Exception:
            # Execution may already have changed a file; never claim it was not run.
            return _response(request_id, "ALLOW", "INTERNAL_ERROR", "Approved operation failed.",
                             {"status": "ERROR", "result": None})
        return _response(request_id, "ALLOW", "OK", "Approved and executed.",
                         {"status": "SUCCESS", "result": result})

    def handle(request):
        request_id = None
        if type(request) is dict:
            candidate = request.get("request_id")
            if isinstance(candidate, str) and _ID.fullmatch(candidate):
                request_id = candidate
        with lock:
            response = process(request, request_id)
            # No paths, content, prompts, keys, or raw exception strings in audit logs.
            try:
                _LOG.info("request_id=%s decision=%s reason_code=%s", request_id,
                          response["decision"], response["reason_code"])
            except Exception:
                # Best-effort logging must not hide a completed operation and invite a retry.
                pass
            return response

    return handle


def _response(request_id, decision, code, reason, execution=None):
    return {"version": VERSION, "request_id": request_id, "decision": decision,
            "reason_code": code, "reason": reason, "execution": execution}
