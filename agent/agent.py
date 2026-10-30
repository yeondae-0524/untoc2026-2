# agent/agent.py
from typing import Dict, Any, Callable
from agent.models.gemini import GeminiModel

class Agent:
    """
    LLM Agent 제어기.
    연동 기준에 따라 handle() 메서드만을 외부 진입점으로 제공합니다.
    """
    def __init__(self, model: GeminiModel, gateway_handler: Callable[[Dict[str, Any]], Dict[str, Any]], tool_registry: Any):
        """
        Args:
            model: GeminiModel 인스턴스 (Model.start/resume 제공)
            gateway_handler: B팀 게이트웨이 함수 (또는 Mock 게이트웨이)
            tool_registry: A팀 2번 담당자의 Tool Registry 인스턴스
        """
        self.model = model
        self.gateway_handler = gateway_handler
        self.tool_registry = tool_registry
        self._request_counter = 0

    def _generate_request_id(self) -> str:
        """개요서 4-① 규격에 따른 고유 식별자 생성 (req-0001, req-0002 ...)"""
        self._request_counter += 1
        return f"req-{self._request_counter:04d}"

    def handle(self, user_prompt: str) -> str:
        """
        외부에서 호출하는 단일 진입점 함수.
        사용자 프롬프트 처리 -> Model.start -> 게이트웨이 전송 -> Model.resume 순으로 오케스트레이션합니다.
        
        Args:
            user_prompt: 사용자가 입력한 자연어 요청
            
        Returns:
            최종 어시스턴트 응답 문자열
        """
        # 1. Tool Registry에서 등록된 도구 명세 수집
        tool_declarations = self.tool_registry.get_declarations()

        # 2. Model.start 호출을 통한 도구 요청 파싱
        tool_name, tool_args, normal_text = self.model.start(user_prompt, tool_declarations)

        # 도구 호출이 필요 없는 일반 대화인 경우 바로 반환
        if not tool_name:
            return normal_text

        # 3. A팀 -> B팀 공통 JSON v1.0 요청 생성 (개요서 4-① 준수)
        request_payload = {
            "version": "1.0",
            "request_id": self._generate_request_id(),
            "tool": tool_name,
            "arguments": tool_args
        }

        # 4. B팀 게이트웨이 호출 (보안 검사 및 실행 위임)
        gateway_response = self.gateway_handler(request_payload)

        # 5. 게이트웨이 응답 규격(ALLOW/BLOCK, execution.status) 해석 및 피드백 데이터 구성
        decision = gateway_response.get("decision")
        execution = gateway_response.get("execution")
        reason = gateway_response.get("reason", "")

        if decision == "BLOCK":
            # 정책에 의해 차단된 경우
            tool_result_payload = {
                "decision": "BLOCK",
                "status": "BLOCKED",
                "reason": reason
            }
        elif decision == "ALLOW":
            if execution and execution.get("status") == "SUCCESS":
                # 승인 및 실행 성공
                tool_result_payload = {
                    "decision": "ALLOW",
                    "status": "SUCCESS",
                    "output": execution.get("result", {})
                }
            else:
                # 승인은 되었으나 파일 실행 중 오류 발생 (예: 파일 없음)
                error_detail = execution.get("result", {}) if execution else {}
                tool_result_payload = {
                    "decision": "ALLOW",
                    "status": "FAILED",
                    "reason": reason,
                    "error": error_detail
                }
        else:
            tool_result_payload = {
                "decision": "ERROR",
                "status": "ERROR",
                "reason": "게이트웨이 응답 규격 오류"
            }

        # 6. Model.resume 호출로 최종 사용자 안내 문장 반환
        final_response = self.model.resume(tool_name=tool_name, tool_result=tool_result_payload)
        return final_response
