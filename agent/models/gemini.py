"""Gemini generateContent REST adapter; manual calls only, no SDK auto-execution."""
import json
import re
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from agent.agent import ToolCall, Turn


def tool_declarations():
    declarations = []
    descriptions = {
        "read_file": "Read an allowed UTF-8 file, at most 65536 bytes.",
        "write_file": "Replace an existing allowed file; never create it.",
        "list_files": "List visible allowed children of a directory; use . for workspace root.",
        "create_file": "Create a new allowed file exclusively; never overwrite.",
        "delete_file": "Delete one allowed regular file; never a directory.",
    }
    for name, description in descriptions.items():
        properties = {"path": {"type": "STRING", "description": "Relative workspace path using /"}}
        if name in {"write_file", "create_file"}:
            properties["content"] = {"type": "STRING", "description": "UTF-8 text, at most 65536 bytes"}
        declarations.append({"name": name, "description": description,
                             "parameters": {"type": "OBJECT", "properties": properties,
                                            "required": list(properties)}})
    return declarations


class Gemini:
    def __init__(self, *, api_key, model, transport=None):
        if not api_key or not re.fullmatch(r"[A-Za-z0-9.-]{1,100}", model or ""):
            raise ValueError("Set GEMINI_API_KEY and a valid GEMINI_MODEL")
        self._api_key, self._model = api_key, model
        self._transport = transport or self._post
        self._history = []

    def start(self, prompt):
        self._history = [{"role": "user", "parts": [{"text": prompt}]}]
        return self._next()

    def resume(self, results):
        parts = []
        for call, response in results:
            function_response = {"name": call.name, "response": response}
            if call.call_id is not None:
                function_response["id"] = call.call_id
            parts.append({"functionResponse": function_response})
        self._history.append({"role": "user", "parts": parts})
        return self._next()

    def _next(self):
        data = self._transport({"contents": self._history,
                                "tools": [{"functionDeclarations": tool_declarations()}],
                                "toolConfig": {"functionCallingConfig": {"mode": "AUTO"}}})
        try:
            candidate = data["candidates"][0]
            if candidate.get("finishReason") not in {None, "STOP"}:
                raise ValueError("Unusable completion")
            content = candidate["content"]
            parts = content["parts"]
            calls = []
            text = []
            for part in parts:
                if "functionCall" in part:
                    call = part["functionCall"]
                    calls.append(ToolCall(call["name"], call.get("args", {}), call.get("id")))
                if "text" in part and not part.get("thought", False):
                    text.append(part["text"])
            # Preserve every raw part, including thought signatures and function-call IDs.
            self._history.append(content)
            return Turn("\n".join(text), tuple(calls))
        except (KeyError, IndexError, TypeError, ValueError):
            raise RuntimeError("Gemini returned an invalid or blocked response") from None

    def _post(self, payload):
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self._model}:generateContent"
        request = Request(url, data=json.dumps(payload).encode("utf-8"), method="POST",
                          headers={"Content-Type": "application/json", "x-goog-api-key": self._api_key})
        try:
            with urlopen(request, timeout=30) as response:
                body = response.read(1_048_577)
            if len(body) > 1_048_576:
                raise ValueError("Response too large")
            return json.loads(body)
        except (HTTPError, URLError, OSError, ValueError):
            # No URL, request headers or API key in exception messages.
            raise RuntimeError("Gemini API request failed; check connection, model and credentials") from None
