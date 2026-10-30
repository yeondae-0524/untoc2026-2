"""A-team implementation. Functions consume a one-use, operation-bound approval.

The factory is private integration code owned by B team's gateway. There is no
public execute(tool, raw_path) API and no caller-supplied approved=True switch.
"""
import os

from agentfence.contracts import FenceError, MAX_BYTES
from tools._filesystem import Workspace


class _ApprovedCall:
    __slots__ = ("_issuer", "tool", "arguments", "workspace", "policy", "_used")

    def __init__(self, issuer, tool, arguments, workspace, policy):
        self._issuer, self.tool, self.arguments = issuer, tool, arguments
        self.workspace, self.policy, self._used = workspace, policy, False


_ISSUER = object()


def _consume(call, tool):
    if (type(call) is not _ApprovedCall or call._issuer is not _ISSUER
            or call.tool != tool or call._used):
        raise FenceError("INVALID_APPROVAL", "An unused gateway approval for this tool is required.")
    call._used = True
    return call.arguments


def _parent(path):
    return path.rsplit("/", 1) if "/" in path else (".", path)


def read_file(call):
    args = _consume(call, "read_file")
    parent, name = _parent(args["path"])
    with call.workspace.directory(parent) as directory, directory.file(name, "r") as fd:
        data = bytearray()
        while len(data) <= MAX_BYTES:
            chunk = os.read(fd, min(8192, MAX_BYTES + 1 - len(data)))
            if not chunk:
                break
            data.extend(chunk)
        if len(data) > MAX_BYTES:
            raise FenceError("SIZE_LIMIT", "File exceeds 65536 bytes.")
        return {"path": args["path"], "content": data.decode("utf-8"), "bytes": len(data)}


def _write(call, args, mode):
    parent, name = _parent(args["path"])
    data = args["content"].encode("utf-8")
    with call.workspace.directory(parent) as directory, directory.file(name, mode) as fd:
        # Never O_TRUNC before the opened object's type/link checks.
        os.ftruncate(fd, 0)
        offset = 0
        while offset < len(data):
            written = os.write(fd, data[offset:])
            if written <= 0:
                raise OSError("Short write")
            offset += written
        os.fsync(fd)
    return {"path": args["path"], "bytes": len(data)}


def write_file(call):
    return _write(call, _consume(call, "write_file"), "w")


def create_file(call):
    return _write(call, _consume(call, "create_file"), "x")


def list_files(call):
    args = _consume(call, "list_files")
    entries = []
    with call.workspace.directory(args["path"]) as directory:
        for name in call.policy.visible_children(args["path"]):
            try:
                kind = directory.kind(name)
            except FileNotFoundError:
                continue
            if kind:
                prefix = "" if args["path"] == "." else args["path"] + "/"
                entries.append({"path": prefix + name, "type": kind})
    return {"path": args["path"], "entries": entries}


def delete_file(call):
    args = _consume(call, "delete_file")
    parent, name = _parent(args["path"])
    with call.workspace.directory(parent) as directory:
        directory.delete(name)
    return {"path": args["path"], "deleted": True}


def _make_dispatch(workspace, policy):
    backend = Workspace(workspace)
    functions = {fn.__name__: fn for fn in (read_file, write_file, list_files, create_file, delete_file)}

    def dispatch(tool, arguments):
        # Defense in depth at the integration boundary, before minting an approval.
        if not policy.allows(tool, arguments["path"]):
            raise FenceError("NOT_ALLOWED", "No matching allowlist grant.")
        call = _ApprovedCall(_ISSUER, tool, dict(arguments), backend, policy)
        return functions[tool](call)

    return dispatch
