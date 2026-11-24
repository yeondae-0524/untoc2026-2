
# agent/agent.py

from dataclasses import dataclass
from typing import Any, Protocol, Tuple, List, Dict
from uuid import uuid4


# --------------------------------------------------
# 1. ToolCall
# --------------------------------------------------

@dataclass(frozen=True, init=False)
class ToolCall:
    """모델이 요청한 도구 호출 정보를 저장한다."""

    name: str
    arguments: Dict[str, Any]
    call_id: Any

    def __init__(
        self,
        name: str,
        arguments: Dict[str, Any],
        call_id: Any = None,
        *,
        id: Any = None
    ):
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "arguments", arguments)
        object.__setattr__(
            self,
            "call_id",
            call_id if call_id is not None else id
        )

    @property
    def id(self):
        """Gemini 어댑터와의 호환성을 위한 ID."""
        return self.call_id


# --------------------------------------------------
# 2. Turn
# --------------------------------------------------

@dataclass(frozen=True, init=False)
class Turn:
    """모델의 한 번의 응답을 저장한다."""

    text: str
    calls: Tuple[ToolCall, ...]

    def __init__(
        self,
        text: str,
        calls: Tuple[ToolCall, ...] = (),
        *,
        tool_calls: Tuple[ToolCall, ...] = ()
    ):
        selected_calls = calls if calls else tool_calls

        object.__setattr__(self, "text", text)
        object.__setattr__(self, "calls", tuple(selected_calls))

    @property
    def tool_calls(self):
        """Gemini 어댑터와의 호환성을 위한 속성."""
        return self.calls


# --------------------------------------------------
# 3. Model Protocol
# --------------------------------------------------

class Model(Protocol):
    """모델이 구현해야 하는 공통 인터페이스."""

    def start(self, prompt: str) -> Turn:
        """새로운 대화를 시작한다."""
        ...

    def resume(
        self,
        results: List[Tuple[ToolCall, Dict[str, Any]]]
    ) -> Turn:
        """도구 실행 결과를 전달하고 대화를 이어간다."""
        ...


# --------------------------------------------------
# 4. Agent 실행 함수
# --------------------------------------------------

def run_agent(
    prompt: str,
    *,
    model: Model,
    gateway,
    max_rounds: int = 10
) -> str:
    """
    모델과 Gateway를 연결하여 Agent를 실행한다.

    실행 순서:
    1. 사용자 프롬프트를 모델에 전달
    2. 모델의 응답 확인
    3. 도구 호출이 있으면 Gateway에 요청
    4. Gateway 실행 결과를 모델에 전달
    5. 최종 텍스트 응답 반환

    최대 실행 제한:
    - 왕복 횟수: 10회
    - 전체 Tool Call: 10회
    """

    # 실행 인자 확인
    if not isinstance(prompt, str):
        raise TypeError("prompt must be a string")

    if not callable(gateway):
        raise TypeError("gateway must be callable")

    if (
        not isinstance(max_rounds, int)
        or isinstance(max_rounds, bool)
        or not 1 <= max_rounds <= 10
    ):
        raise ValueError("max_rounds must be between 1 and 10")

    # 첫 번째 모델 응답
    turn = model.start(prompt)

    total_tool_calls = 0

    # 모델과 도구 사이의 왕복 처리
    for _ in range(max_rounds):

        # 도구 호출이 없으면 최종 응답 반환
        if not turn.calls:
            return turn.text

        results = []

        # 모든 도구 호출을 순차적으로 처리
        for call in turn.calls:

            total_tool_calls += 1

            # 전체 Tool Call 제한
            if total_tool_calls > 10:
                raise RuntimeError(
                    "Agent tool-call limit reached"
                )

            # Gateway에 전달할 공통 JSON 요청 생성
            request = {
                "version": "1.0",
                "request_id": str(uuid4()),
                "tool": call.name,
                "arguments": call.arguments
            }

            # 실제 파일 작업은 Gateway에서만 수행
            response = gateway(request)

            if not isinstance(response, dict):
                raise RuntimeError(
                    "Invalid gateway response"
                )

            # 원본 ToolCall과 실행 결과 저장
            results.append((call, response))

        # 실행 결과를 모델에 전달하고 다음 응답 생성
        turn = model.resume(results)

        if not isinstance(turn, Turn):
            raise RuntimeError(
                "Invalid model response"
            )

    # 마지막 왕복에서 최종 응답이 생성되었는지 확인
    if not turn.calls:
        return turn.text

    raise RuntimeError("Agent round limit reached")
