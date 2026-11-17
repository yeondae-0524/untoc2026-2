
# agent/agent.py

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional, Tuple


MAX_TOOL_CALLS = 16


@dataclass
class ToolCall:
    """Gemini가 요청한 단일 도구 호출 정보."""
    name: str
    arguments: Dict[str, Any]
    id: Optional[str] = None


@dataclass
class Turn:
    """Gemini 모델의 한 번의 응답."""
    text: str = ""
    tool_calls: Tuple[ToolCall, ...] = field(default_factory=tuple)


def run_agent(
    prompt: str,
    model: Any,
    gateway: Callable[[Dict[str, Any]], Dict[str, Any]]
) -> str:
    """
    사용자 프롬프트를 처리하고 Gateway를 통해 도구를 실행한다.

    처리 순서:
    1. Gemini 모델에 사용자 프롬프트 전달
    2. 모델의 도구 호출 요청 확인
    3. Gateway에 각 도구 호출 전달
    4. 실행 결과를 Gemini에 전달
    5. 최종 자연어 응답 반환

    최대 도구 호출 횟수는 16회이다.
    """

    request_counter = 0
    total_tool_calls = 0

    turn = model.start(prompt)

    while turn.tool_calls:

        tool_results = []

        for call in turn.tool_calls:

            # 도구 호출 횟수 제한
            if total_tool_calls >= MAX_TOOL_CALLS:
                raise RuntimeError(
                    "tool-call limit exceeded (maximum 16 calls)"
                )

            total_tool_calls += 1
            request_counter += 1

            # Gateway 공통 JSON v1.0 요청
            request_payload = {
                "version": "1.0",
                "request_id": f"req-{request_counter:04d}",
                "tool": call.name,
                "arguments": call.arguments
            }

            # Gateway 호출
            gateway_response = gateway(request_payload)

            # 원본 Gateway 응답을 그대로 모델에 전달할 수 있도록 저장
            tool_results.append((call, gateway_response))

        # 모든 도구 호출 결과를 한 번에 Gemini에 전달
        turn = model.resume(tool_results)

    return turn.text


class Agent:
    """
    기존 Agent 클래스와의 호환성을 위한 래퍼.

    실제 실행은 run_agent()에서 처리한다.
    """

    def __init__(
        self,
        model: Any,
        gateway_handler: Callable[[Dict[str, Any]], Dict[str, Any]],
        tool_registry: Any = None
    ):
        self.model = model
        self.gateway_handler = gateway_handler
        self.tool_registry = tool_registry

    def handle(self, user_prompt: str) -> str:
        return run_agent(
            prompt=user_prompt,
            model=self.model,
            gateway=self.gateway_handler
        )
