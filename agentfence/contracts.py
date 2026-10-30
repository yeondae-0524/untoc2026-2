"""Version 1.0: strict, deliberately small JSON contract."""
import re

VERSION = "1.0"
MAX_BYTES = 65_536
TOOLS = frozenset({"read_file", "write_file", "list_files", "create_file", "delete_file"})
_NAME = re.compile(r"[A-Za-z0-9_-][A-Za-z0-9_.-]{0,99}\Z")
_ID = re.compile(r"[A-Za-z0-9_-]{1,64}\Z")
_RESERVED = {"config", "secrets", "credentials", "policy.json", "api_key", "api_keys"}
_DEVICES = {"con", "prn", "aux", "nul", "conin$", "conout$"} | {
    f"{prefix}{n}" for prefix in ("com", "lpt") for n in range(10)
}


class FenceError(Exception):
    def __init__(self, code, message):
        self.code = code
        self.message = message
        super().__init__(message)


def validate_path(path, *, listing=False):
    if listing and path == ".":
        return path
    if not isinstance(path, str) or not 1 <= len(path) <= 512:
        raise FenceError("INVALID_PATH", "A relative workspace path is required.")
    parts = path.split("/")
    if len(parts) > 20 or any(not _NAME.fullmatch(p) or p.endswith(".") for p in parts):
        raise FenceError("INVALID_PATH", "Use plain relative paths with / separators.")
    for part in parts:
        name = part.casefold()
        if (name in _RESERVED or name.startswith(("policy.", "credentials.", "api_key."))
                or name.endswith((".key", ".pem", ".p12", ".pfx"))
                or name.split(".")[0] in _DEVICES):
            raise FenceError("PROTECTED_PATH", "Control files and credential names are protected.")
    return path


def validate_request(request):
    if type(request) is not dict or set(request) != {"version", "request_id", "tool", "arguments"}:
        raise FenceError("INVALID_REQUEST", "Expected exactly version, request_id, tool and arguments.")
    if request["version"] != VERSION:
        raise FenceError("INVALID_REQUEST", "Unsupported protocol version.")
    if not isinstance(request["request_id"], str) or not _ID.fullmatch(request["request_id"]):
        raise FenceError("INVALID_REQUEST", "request_id must contain 1-64 letters, digits, _ or -.")
    tool = request["tool"]
    if not isinstance(tool, str) or tool not in TOOLS:
        raise FenceError("UNKNOWN_TOOL", "Tool is not registered.")
    args = request["arguments"]
    expected = {"path", "content"} if tool in {"write_file", "create_file"} else {"path"}
    if type(args) is not dict or set(args) != expected:
        raise FenceError("INVALID_ARGUMENTS", "Tool arguments do not match the contract.")
    path = validate_path(args["path"], listing=tool == "list_files")
    if "content" in args:
        if not isinstance(args["content"], str):
            raise FenceError("INVALID_ARGUMENTS", "content must be UTF-8 text.")
        try:
            size = len(args["content"].encode("utf-8"))
        except UnicodeError:
            raise FenceError("INVALID_ARGUMENTS", "content must be valid UTF-8 text.") from None
        if size > MAX_BYTES:
            raise FenceError("SIZE_LIMIT", "content exceeds 65536 UTF-8 bytes.")
    return tool, {**args, "path": path}


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON member")
        result[key] = value
    return result
