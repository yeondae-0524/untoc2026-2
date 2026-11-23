"""Provider-independent orchestration: an Agent receives only the gateway callable."""
from dataclasses import dataclass
from typing import Protocol
from uuid import uuid4


@dataclass(frozen=True)
class ToolCall:
    name: str
    arguments: dict
    call_id: str | None = None


@dataclass(frozen=True)
class Turn:
    text: str
    calls: tuple[ToolCall, ...] = ()


class Model(Protocol):
    def start(self, prompt: str) -> Turn: ...
    def resume(self, results: list[tuple[ToolCall, dict]]) -> Turn: ...


def run_agent(prompt, *, model: Model, gateway, max_rounds=8):
    """No eval, shell, Python execution, file executor or automatic SDK tools."""
    turn = model.start(prompt)
    count = 0
    for _ in range(max_rounds):
        if not turn.calls:
            return turn.text
        results = []
        for call in turn.calls:
            count += 1
            if count > 16:
                raise RuntimeError("Agent tool-call limit reached")
            response = gateway({"version": "1.0", "request_id": str(uuid4()),
                                "tool": call.name, "arguments": call.arguments})
            results.append((call, response))
        turn = model.resume(results)
    if not turn.calls:
        return turn.text
    raise RuntimeError("Agent round limit reached")
