"""B-2 allowlist decisions. Reload requires building a new gateway."""
import json
from dataclasses import dataclass

from agentfence.contracts import FenceError, TOOLS, unique_object, validate_path

_PERMISSIONS = {"read": "read_file", "write": "write_file",
                "delete": "delete_file", "list": "list_files"}


@dataclass(frozen=True)
class PolicyDecision:
    decision: str
    reason_code: str
    reason: str


def _permission_policy(data):
    if (set(data) != {"default", "files", "directories"}
            or data["default"] != "BLOCK"):
        raise ValueError("Expected a default BLOCK policy")
    grants, create_directories = set(), set()
    count = 0
    for section, valid in (("files", {"read", "write", "delete"}),
                           ("directories", {"list", "create"})):
        entries = data[section]
        if type(entries) is not dict:
            raise ValueError("Expected path to permissions mapping")
        count += len(entries)
        if count > 1000:
            raise ValueError("Too many policy entries")
        for path, permissions in entries.items():
            validate_path(path, listing=section == "directories")
            if (type(permissions) is not list
                    or any(type(p) is not str or p not in valid for p in permissions)
                    or len(set(permissions)) != len(permissions)):
                raise ValueError("Invalid or duplicate permissions")
            for permission in permissions:
                if permission == "create":
                    create_directories.add(path)
                else:
                    grants.add((_PERMISSIONS[permission], path))
    return grants, create_directories


@dataclass(frozen=True)
class Policy:
    grants: frozenset
    create_directories: frozenset = frozenset()

    @classmethod
    def load(cls, path):
        try:
            with open(path, "r", encoding="utf-8") as stream:
                text = stream.read(262_145)
            if len(text) > 262_144:
                raise ValueError("Policy too large")
            data = json.loads(text, object_pairs_hook=unique_object)
            if type(data) is dict and set(data) == {"default", "files", "directories"}:
                grants, directories = _permission_policy(data)
                return cls(frozenset(grants), frozenset(directories))
            # Compatibility with existing exact tool/path policies.
            if (type(data) is not dict or set(data) != {"version", "allow"}
                    or data["version"] != "1.0" or type(data["allow"]) is not list
                    or len(data["allow"]) > 1000):
                raise ValueError("Invalid policy")
            grants = set()
            for rule in data["allow"]:
                if type(rule) is not dict or set(rule) != {"tool", "path"}:
                    raise ValueError("Invalid rule")
                tool = rule["tool"]
                if not isinstance(tool, str) or tool not in TOOLS:
                    raise ValueError("Unknown tool")
                path_value = validate_path(rule["path"], listing=tool == "list_files")
                if (tool, path_value) in grants:
                    raise ValueError("Duplicate rule")
                grants.add((tool, path_value))
            return cls(frozenset(grants))
        except (OSError, ValueError, TypeError, UnicodeError, RecursionError, FenceError):
            raise FenceError("POLICY_ERROR", "Policy is missing or invalid; all calls are blocked.") from None

    def allows(self, tool, path):
        return self.check_permission(tool, path).decision == "ALLOW"

    def check_permission(self, tool, path):
        """Return a decision without executing tools or reading target files.

        Gateway validates arguments. File backend checks links and races at
        execution time; a policy decision alone is never an execution token.
        """
        if not isinstance(tool, str) or tool not in TOOLS:
            return PolicyDecision("BLOCK", "INVALID_TOOL", "지원하지 않는 Tool입니다.")
        try:
            validate_path(path, listing=tool == "list_files")
        except FenceError:
            return PolicyDecision("BLOCK", "INVALID_PATH", "허용되지 않는 workspace 상대 경로입니다.")
        allowed = (tool, path) in self.grants
        if tool == "create_file":
            parent = path.rsplit("/", 1)[0] if "/" in path else "."
            allowed = allowed or parent in self.create_directories
        if allowed:
            return PolicyDecision("ALLOW", "POLICY_ALLOWED", "정책에서 허용된 요청입니다.")
        return PolicyDecision("BLOCK", "POLICY_DENIED", "해당 작업을 허용하는 규칙이 없습니다.")

    def visible_children(self, directory):
        # Only read/list grants reveal names. Mutation grants do not reveal contents.
        prefix = "" if directory == "." else directory + "/"
        return sorted({path[len(prefix):] for tool, path in self.grants
                       if tool in {"read_file", "list_files"} and path != "."
                       and path.startswith(prefix) and "/" not in path[len(prefix):]})
