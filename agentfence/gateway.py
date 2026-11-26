"""The only callable injected into the Agent. Approval never leaves this module."""
from functools import lru_cache
from pathlib import Path
from threading import Lock

from agentfence.audit import record
from agentfence.contracts import FenceError, VERSION, _ID, validate_request
from agentfence.policy import Policy
from tools.file_tools import _make_dispatch


def build_gateway(*, workspace, policy_path, audit=None):
    """Return handle(request: dict) -> response: dict. Configuration is trusted."""
    startup_error = None
    policy = dispatch = None
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

    def process(tool, arguments, request_id):
        if startup_error is not None:
            return _response(request_id, "BLOCK", startup_error.code, startup_error.message), None
        if policy is None or dispatch is None:
            return _response(request_id, "BLOCK", "CONFIG_ERROR", "Workspace configuration is unavailable."), None
        try:
            allowed = policy.allows(tool, arguments["path"])
        except Exception:
            # No executor has been called. A policy failure must remain a BLOCK.
            return _response(request_id, "BLOCK", "POLICY_ERROR", "Policy check failed; request blocked."), None
        if not allowed:
            return _response(request_id, "BLOCK", "NOT_ALLOWED", "No matching allowlist grant."), None

        change = audit.capture_change(tool, arguments["path"]) if audit is not None else None
        try:
            # The closure owns the executor. Agent never receives it or an ALLOW token.
            if change is None:
                result = dispatch(tool, arguments)
            else:
                result = dispatch(tool, arguments, change=change)
        except FenceError as exc:
            # Expected safety rejections from the trusted file Tool boundary.
            response = _response(request_id, "BLOCK", exc.code, exc.message)
        except (OSError, UnicodeError) as exc:
            code, reason = "EXECUTION_ERROR", "Approved operation failed."
            if isinstance(exc, FileNotFoundError):
                code, reason = "FILE_NOT_FOUND", "Approved target does not exist."
            elif isinstance(exc, FileExistsError):
                code, reason = "ALREADY_EXISTS", "create_file cannot overwrite a target."
            response = _failed(request_id, code, reason)
        except Exception:
            # Execution may already have changed a file; never claim it was not run.
            response = _failed(request_id, "INTERNAL_ERROR", "Approved operation failed.")
        else:
            response = _response(request_id, "ALLOW", "OK", "Approved and executed.",
                                 {"status": "SUCCESS", "result": result})
        return response, change

    def handle(request):
        request_id = None
        if type(request) is dict:
            candidate = request.get("request_id")
            if isinstance(candidate, str) and _ID.fullmatch(candidate):
                request_id = candidate
        with lock:
            tool = path = change = None
            try:
                tool, arguments = validate_request(request)
            except FenceError as exc:
                response = _response(request_id, "BLOCK", exc.code, exc.message)
            except Exception:
                response = _response(request_id, "BLOCK", "INTERNAL_ERROR", "Request could not be processed.")
            else:
                path = arguments["path"]
                response, change = process(tool, arguments, request_id)
            try:
                if audit is None:
                    record(request, response)
                else:
                    audit.record(request, response, tool=tool, path=path, change=change)
            except Exception:
                # Best-effort logging must not hide a completed operation and invite a retry.
                pass
            return response

    return handle


def _response(request_id, decision, code, reason, execution=None):
    return {"version": VERSION, "request_id": request_id, "decision": decision,
            "reason_code": code, "reason": reason, "execution": execution}


def _failed(request_id, code, reason):
    return _response(request_id, "ALLOW", code, reason, {
        "status": "FAILED", "result": None, "error": {"code": code, "message": reason},
    })


_ROOT = Path(__file__).resolve().parents[1]


@lru_cache(maxsize=1)
def _default_gateway():
    return build_gateway(workspace=_ROOT / "workspace",
                         policy_path=_ROOT / "config" / "policy.json")


def handle_tool_call(request: dict) -> dict:
    """Execute a v1 request using the repository workspace and allowlist."""
    return _default_gateway()(request)
