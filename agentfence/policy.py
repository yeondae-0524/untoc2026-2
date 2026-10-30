"""Exact path allowlist. Load once; reload requires building a new gateway."""
import json
from dataclasses import dataclass

from agentfence.contracts import FenceError, TOOLS, unique_object, validate_path


@dataclass(frozen=True)
class Policy:
    grants: frozenset

    @classmethod
    def load(cls, path):
        try:
            with open(path, "r", encoding="utf-8") as stream:
                text = stream.read(262_145)
            if len(text) > 262_144:
                raise ValueError("Policy too large")
            data = json.loads(text, object_pairs_hook=unique_object)
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
        except (OSError, ValueError, TypeError, UnicodeError, FenceError):
            raise FenceError("POLICY_ERROR", "Policy is missing or invalid; all calls are blocked.") from None

    def allows(self, tool, path):
        return (tool, path) in self.grants

    def visible_children(self, directory):
        # Only read/list grants reveal names. Mutation grants do not reveal contents.
        prefix = "" if directory == "." else directory + "/"
        return sorted({path[len(prefix):] for tool, path in self.grants
                       if tool in {"read_file", "list_files"} and path != "."
                       and path.startswith(prefix) and "/" not in path[len(prefix):]})
